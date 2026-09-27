
import streamlit as st
import requests
import time as _time
import pandas as pd
import numpy as np
from datetime import datetime, date, timezone, timedelta
from collections import defaultdict, deque
from streamlit_autorefresh import st_autorefresh
import os
import json
import glob
import sys
from pathlib import Path

APP_VERSION = "v19.1 ROLLING60 APP ONLY"
APP_NAME = "HKJC 即時賠率監察 · NEW"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# ════════════════════════════════════════════════════════════
#  HKJC QUANT MODEL (V19 only — old app.py remains untouched)
# ════════════════════════════════════════════════════════════
# Default layout:
#   app-new.py
#   hkjc_quant/{features.py, model.py, exotics.py, staking.py, ...}
# Override with HKJC_MODEL_DIR when deployed elsewhere.
APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = Path(os.environ.get("HKJC_MODEL_DIR", APP_DIR / "hkjc_quant"))
MODEL_READY = False
MODEL_IMPORT_ERROR = None
try:
    if str(MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(MODEL_DIR))
    from features import build_features
    from model import public_probabilities
    from exotics import place_probs
    MODEL_READY = True
except Exception as _model_import_exc:
    MODEL_IMPORT_ERROR = str(_model_import_exc)


@st.cache_resource(show_spinner=False)
def load_quant_assets():
    """Load the portable model bundle and historical form once per process."""
    if not MODEL_READY:
        raise RuntimeError(MODEL_IMPORT_ERROR or "模型模組未能載入")
    model_path = MODEL_DIR / "models" / "latest_portable.json"
    history_path = MODEL_DIR / "data" / "runs_clean.csv"
    with model_path.open("r", encoding="utf-8") as fh:
        bundle = json.load(fh)
    history = pd.read_csv(history_path, parse_dates=["race_date"])
    return bundle, history


def _model_apply_scaler(df_features, scaler, feature_cols):
    """Apply the exact training-time medians/means/stds and column order."""
    base_cols = [c for c in feature_cols if not c.endswith("_isna")]
    x = df_features[base_cols].copy()
    flags = pd.DataFrame(index=x.index)
    for flag_col in scaler.get("flag_cols", []):
        source_col = flag_col[:-5] if flag_col.endswith("_isna") else flag_col
        flags[flag_col] = x[source_col].isna().astype(int)
    median = pd.Series(scaler["median"], dtype=float)
    mean = pd.Series(scaler["mean"], dtype=float)
    std = pd.Series(scaler["std"], dtype=float)
    x = x.fillna(median)
    x = (x - mean) / std
    x = pd.concat([x, flags], axis=1)
    for col in feature_cols:
        if col not in x.columns:
            x[col] = 0.0
    return x[feature_cols].to_numpy(dtype=np.float64)


def _group_softmax(values):
    values = np.asarray(values, dtype=np.float64)
    values = values - np.nanmax(values)
    exp_v = np.exp(np.clip(values, -700, 700))
    total = exp_v.sum()
    return exp_v / total if total > 0 else np.full(len(values), 1.0 / len(values))


@st.cache_data(ttl=3600, show_spinner=False)
def build_live_fundamentals(card_json):
    """Build pre-race features once; live odds are added separately every refresh."""
    bundle, history = load_quant_assets()
    card = pd.DataFrame(json.loads(card_json))
    card["race_date"] = pd.to_datetime(card["race_date"])
    for col in ("finishing_position", "finish_time_sec", "lbw"):
        card[col] = np.nan
    combined = pd.concat([history, card], ignore_index=True, sort=False)
    featured = build_features(combined)
    live = featured[featured["race_id"] == card["race_id"].iloc[0]].copy()
    live = live.sort_values("horse_no", key=lambda s: pd.to_numeric(s, errors="coerce"))
