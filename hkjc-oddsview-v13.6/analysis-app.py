"""
HKJC 賽馬日分析 — 8502 (V1)
================================================================================
獨立服務，完全唔郁 8501。慢刷 60 秒 / 按「更新」。
重用 hkjc_quant 模組（features/model/exotics/staking），唔重寫模型。
評分流程 = score_racecard.py；加 歷史跑法 / 距離・場地適配 / 1/4 凱利 / Dutching。
規格見 project: claude/8502-技術規格.md
"""
from __future__ import annotations
import os, sys, io, json, glob
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st

APP_VERSION = "8502-V1-20261002"
HKT = timezone(timedelta(hours=8))

# ── 路徑：hkjc_quant（模型+數據+模組）──
APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = Path(os.environ.get("HKJC_MODEL_DIR", APP_DIR / "hkjc_quant"))
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
STYLE_CSV_CANDIDATES = [
    APP_DIR / "horse_running_style_summary.csv",                       # 同 app-new.py 同層（你放嗰度）
    APP_DIR / "running_style_analysis" / "horse_running_style_summary.csv",
    MODEL_DIR / "running_style_analysis" / "horse_running_style_summary.csv",
    MODEL_DIR / "horse_running_style_summary.csv",
]
PACE_CSV_CANDIDATES = [
    APP_DIR / "pace_per_start.csv",
    MODEL_DIR / "pace_per_start.csv",
]
if str(MODEL_DIR) not in sys.path:
    sys.path.insert(0, str(MODEL_DIR))

# ── 可調參數（規格 §6/§11）──
DIST_BAND, FIT_MARGIN, FIT_MIN_RUNS = 100, 0.08, 3
EV_THRESHOLD, KELLY_FRACTION = 1.05, 0.25
AMT_MIN, AMT_MAX = 100, 10000
REBATE_WIN = 0.10
STYLE_COLS = [("put_front_pct", "放頭"), ("prominent_pct", "前置"),
              ("midfield_pct", "中置"), ("rear_pct", "後置")]

st.set_page_config(page_title=f"HKJC 賽馬日分析 {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# ── HKJC GraphQL（逐字不可改）──
API = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
           "Origin": "https://bet.hkjc.com", "Referer": "https://bet.hkjc.com/",
           "User-Agent": "Mozilla/5.0"}
TURNOVER_QUERY = 'fragment raceFragment on Race {\n  id\n  no\n  status\n  raceName_en\n  raceName_ch\n  postTime\n  country_en\n  country_ch\n  distance\n  wageringFieldSize\n  go_en\n  go_ch\n  ratingType\n  raceTrack {\n    description_en\n    description_ch\n  }\n  raceCourse {\n    description_en\n    description_ch\n    displayCode\n  }\n  claCode\n  raceClass_en\n  raceClass_ch\n  judgeSigns {\n    value_en\n  }\n}\n\nfragment racingBlockFragment on RaceMeeting {\n  jpEsts: pmPools(\n    oddsTypes: [WIN, PLA, TCE, TRI, FF, QTT, DT, TT, SixUP]\n    filters: ["jackpot", "estimatedDividend"]\n  ) {\n    leg {\n      number\n      races\n    }\n    oddsType\n    jackpot\n    estimatedDividend\n    mergedPoolId\n  }\n  poolInvs: pmPools(\n    oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n  ) {\n    id\n    leg {\n      races\n    }\n  }\n  penetrometerReadings(filters: ["first"]) {\n    reading\n    readingTime\n  }\n  hammerReadings(filters: ["first"]) {\n    reading\n    readingTime\n  }\n  changeHistories(filters: ["top3"]) {\n    type\n    time\n    raceNo\n    runnerNo\n    horseName_ch\n    horseName_en\n    jockeyName_ch\n    jockeyName_en\n    scratchHorseName_ch\n    scratchHorseName_en\n    handicapWeight\n    scrResvIndicator\n  }\n}\n\nquery raceMeetings($date: String, $venueCode: String) {\n  timeOffset {\n    rc\n  }\n  activeMeetings: raceMeetings {\n    id\n    venueCode\n    date\n    status\n    races {\n      no\n      postTime\n      status\n      wageringFieldSize\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      status\n    }\n  }\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    id\n    status\n    venueCode\n    date\n    totalNumberOfRace\n    currentNumberOfRace\n    dateOfWeek\n    meetingType\n    totalInvestment\n    country {\n      code\n      namech\n      nameen\n      seq\n    }\n    races {\n      ...raceFragment\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n    obSt: pmPools(oddsTypes: [WIN, PLA]) {\n      leg {\n        races\n      }\n      oddsType\n      comingleStatus\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      id\n      leg {\n        number\n        races\n      }\n      status\n      sellStatus\n      oddsType\n      investment\n      mergedPoolId\n      lastUpdateTime\n    }\n    ...racingBlockFragment\n    pmPools(oddsTypes: []) {\n      id\n    }\n    jkcInstNo: foPools(oddsTypes: [JKC], filters: ["top"]) {\n      instNo\n    }\n    tncInstNo: foPools(oddsTypes: [TNC], filters: ["top"]) {\n      instNo\n    }\n  }\n}'

VENUE_NAMES = {"ST": "沙田", "HV": "跑馬地"}
def venue_label(v): return VENUE_NAMES.get(v, v)
def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return np.nan


# 間歇性 DNS（info.cld.hkjc.com 時好時壞）→ 用 Session + 連線重試捱過。
_SESSION = requests.Session()
try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    _SESSION.mount("https://", HTTPAdapter(max_retries=Retry(
        total=5, connect=5, read=3, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504])))
except Exception:
    pass


def _gql(variables):
    last = None
    for _ in range(3):   # 額外手動重試（兜底 DNS NameResolutionError）
        try:
            r = _SESSION.post(API, headers=HEADERS, json={
                "operationName": "raceMeetings", "variables": variables,
                "query": TURNOVER_QUERY}, timeout=25)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            last = e
            import time as _t
            _t.sleep(1.5)
    raise last


@st.cache_data(ttl=300, show_spinner=False)
def list_meetings():
    """所有有賽事嘅 日期+場地（含海外）。"""
    try:
        j = _gql({"date": None, "venueCode": None})
    except Exception as e:
        return [], str(e)
    if j.get("errors"):
        return [], str(j["errors"])
    ams = (j.get("data") or {}).get("activeMeetings") or []
    seen, out = set(), []
    for m in ams:
        d, v = (m.get("date") or "")[:10], m.get("venueCode")
        if d and v and (d, v) not in seen:
            seen.add((d, v)); out.append({"date": d, "venue": v, "races": m.get("races") or []})
    return sorted(out, key=lambda x: (x["date"], x["venue"])), None


def _norm_track(desc):
    s = str(desc or "").upper()
    return "AWT" if "ALL WEATHER" in s or "AWT" in s else "TURF"


@st.cache_data(ttl=55, show_spinner=False)
def fetch_cards(date_str, venue):
    """攞指定賽馬日所有場 → 砌成 card DataFrame（runs_clean schema + win_odds）+ 每場資料 + WIN池。"""
    j = _gql({"date": date_str, "venueCode": venue})
    if j.get("errors"):
        raise RuntimeError(str(j["errors"]))
    mtgs = (j.get("data") or {}).get("raceMeetings") or []
    if not mtgs:
        return pd.DataFrame(), {}, {}
    mt = mtgs[0]
    races_meta, win_pool = {}, {}
    for p in mt.get("poolInvs") or []:
        if p.get("oddsType") == "WIN":
            for rno in (p.get("leg") or {}).get("races") or []:
                win_pool[int(rno)] = _f(p.get("investment"))
    rows = []
    for rc in mt.get("races") or []:
        rno = int(rc.get("no"))
        races_meta[rno] = {
            "no": rno, "post": rc.get("postTime"), "name": rc.get("raceName_ch"),
            "dist": _f(rc.get("distance")), "going": rc.get("go_en"),
            "track": _norm_track((rc.get("raceTrack") or {}).get("description_en")),
            "course": (rc.get("raceCourse") or {}).get("displayCode"),
            "cls": rc.get("raceClass_ch"),
            "field": rc.get("wageringFieldSize"), "status": rc.get("status")}
        cla = "".join(c for c in str(rc.get("claCode") or rc.get("raceClass_en") or "") if c.isdigit())
        field = int(rc.get("wageringFieldSize") or 0)
        for rn in rc.get("runners") or []:
            if str(rn.get("status") or "").upper() in ("SCRATCHED", "SCR", "WITHDRAWN", "STANDBY"):
                continue
            no = rn.get("no") or rn.get("saddleClothNo")
            horse = rn.get("horse") or {}
            hid = horse.get("id") or horse.get("code") or rn.get("id")
            if no is None or not hid:
                continue
            rows.append({
                "race_id": f"{date_str.replace('-', '')}_{venue}_{rno}",
                "race_date": date_str, "season": "LIVE", "venue": venue, "race_no": rno,
                "distance": _f(rc.get("distance")), "going": (rc.get("go_en") or "").upper(),
                "track": _norm_track((rc.get("raceTrack") or {}).get("description_en")),
                "track_config": (rc.get("raceCourse") or {}).get("displayCode") or "",
                "class_level": int(cla or 0), "field_size": field or len(rc.get("runners") or []),
                "horse_no": int(no), "horse_id": str(hid),
                "horse_name": rn.get("name_ch") or rn.get("name_en") or str(no),
                "jockey_id": (rn.get("jockey") or {}).get("code") or "UNKNOWN",
                "trainer_id": (rn.get("trainer") or {}).get("code") or "UNKNOWN",
                "draw": _f(rn.get("barrierDrawNumber")),
                "actual_weight": _f(rn.get("handicapWeight")),
                "declared_horse_weight": _f(rn.get("currentWeight")),
                "win_odds": _f(rn.get("winOdds")),
                "finishing_position": np.nan, "finish_time_sec": np.nan, "lbw": np.nan})
    return pd.DataFrame(rows), races_meta, win_pool


# ── 模型 + 歷史 載入（cache_resource：一個 process 一次）──
@st.cache_resource(show_spinner=True)
def load_assets():
    import pickle
    pj, pk = MODEL_DIR / "models" / "latest_portable.json", MODEL_DIR / "models" / "latest.pkl"
    if pj.exists():
        bundle = json.load(open(pj, encoding="utf-8"))
        for k in ("median", "mean", "std"):
            bundle["scaler"][k] = pd.Series(bundle["scaler"][k], dtype=float)
        bundle["cl_beta"] = np.asarray(bundle["cl_beta"], float)
        src = "latest_portable.json"
    else:
        bundle = pickle.load(open(pk, "rb")); src = "latest.pkl"
    hist = pd.read_csv(MODEL_DIR / "data" / "runs_clean.csv", parse_dates=["race_date"])
    style = pd.DataFrame()
    for p in STYLE_CSV_CANDIDATES:
        if Path(p).exists():
            style = pd.read_csv(p); break
    pace = pd.DataFrame()
    for p in PACE_CSV_CANDIDATES:
        if Path(p).exists():
            pace = pd.read_csv(p)
            pace["finish_position"] = pd.to_numeric(pace["finish_position"], errors="coerce")
            break
    return bundle, hist, style, pace, src


# ── 評分引擎 ──
def _gsm(v):
    v = np.asarray(v, float); v = v - np.nanmax(v)
    e = np.exp(np.clip(v, -700, 700)); return e / e.sum()

def _apply_scaler(df, scaler, cols):
    base = [c for c in cols if not c.endswith("_isna")]
    X = df[base].copy(); flags = pd.DataFrame(index=X.index)
    for c in scaler["flag_cols"]:
        flags[c] = X[c.replace("_isna", "")].isna().astype(int)
    X = X.fillna(scaler["median"]); X = (X - scaler["mean"]) / scaler["std"]
    return pd.concat([X, flags], axis=1)[cols].values.astype(np.float64)

@st.cache_data(ttl=55, show_spinner=False)
def score_day(cards_json, _bundle_id):
    """對整個賽馬日所有場評分。cards_json：fetch_cards 出嚟嘅 DataFrame.to_json。"""
    from features import build_features
    from model import public_probabilities
    from exotics import place_probs
    from staking import expected_value, breakeven_odds
    bundle, hist, _, _ = load_assets()
    card = pd.read_json(io.StringIO(cards_json))
    card["race_date"] = pd.to_datetime(card["race_date"])
    rids = set(card["race_id"].unique())
    combined = pd.concat([hist[~hist["race_id"].isin(rids)], card], ignore_index=True, sort=False)
    feat = build_features(combined)
    frames = []
    for rid in card["race_id"].unique():
        g = feat[feat["race_id"] == rid].copy()
        X = _apply_scaler(g, bundle["scaler"], bundle["feature_cols"])
        f = _gsm(X @ bundle["cl_beta"])
        odds = g["win_odds"].values.astype(float)
        has = np.isfinite(odds).any() and (np.nan_to_num(odds) > 0).any()
        if has:
            pi = public_probabilities(np.where(odds > 0, odds, np.nan), np.zeros(len(g), int))
            eps = 1e-9
            pf = _gsm(bundle["ss_alpha"] * np.log(np.clip(f, eps, 1)) +
                      bundle["ss_beta"] * np.log(np.clip(pi, eps, 1)))
            ev = np.array([expected_value(p, o, 0.0, 0.0) if np.isfinite(o) and o > 0 else np.nan
                           for p, o in zip(pf, odds)])
        else:
            pi = np.full(len(g), np.nan); pf = f; ev = np.full(len(g), np.nan)
        pl = place_probs(pf, float(bundle["lambda2"]), float(bundle["lambda3"]))
        g["p_model"], g["p_public"], g["p_final"], g["p_place"], g["ev"] = f, pi, pf, pl, ev
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def fitness_labels(hist, horse_id, distance, going):
    h = hist[hist["horse_id"] == horse_id]
    def lab(sub, base):
        if len(sub) < FIT_MIN_RUNS: return "不詳"
        rate = (sub["finishing_position"] <= 3).mean()
        b = (base["finishing_position"] <= 3).mean() if len(base) else 0.25
        return "佳" if rate >= b + FIT_MARGIN else ("不利" if rate <= b - FIT_MARGIN else "一般")
    try:
        d = float(distance)
        ds, bd = h[(h["distance"] - d).abs() <= DIST_BAND], hist[(hist["distance"] - d).abs() <= DIST_BAND]
    except (TypeError, ValueError):
        ds = bd = h.iloc[0:0]
    gs, bg = h[h["going"].astype(str) == str(going)], hist[hist["going"].astype(str) == str(going)]
    return lab(ds, bd), lab(gs, bg)


def dominant_style(style, horse_id):
    if style.empty: return "不詳", None
    row = style[style["horse_id"] == horse_id]
    if row.empty: return "不詳", None
    row = row.iloc[0]; best, pct = "不詳", -1.0
    for col, nm in STYLE_COLS:
        v = _f(row.get(col)) or 0
        if v > pct: best, pct = nm, v
    return best, (round(pct * 100) if pct >= 0 else None)


def kelly_stakes(sub, bankroll, win_pool_total):
    """單場：各 EV≥門檻 馬 1/4 凱利；同場合計封頂 = 單場本金。回傳 {horse_no: stake}."""
    from staking import kelly_stake
    cand = sub[(sub["ev"] >= EV_THRESHOLD) & (sub["win_odds"] > 0)]
    raw = {}
    pool = win_pool_total if win_pool_total and win_pool_total > 0 else 5_000_000
    for _, r in cand.iterrows():
        hp = pool * 0.825 / r["win_odds"]
        rebate = REBATE_WIN if False else 0.0   # 小本金注碼唔夠 $10k，無回扣
        k = kelly_stake(float(r["p_final"]), float(bankroll), float(pool), float(hp),
                        rebate_rate=rebate, fraction=KELLY_FRACTION)
        raw[int(r["horse_no"])] = max(0.0, float(k["stake"]))
    tot = sum(raw.values())
    if tot > bankroll and tot > 0:                      # 同場封頂
        raw = {k: v * bankroll / tot for k, v in raw.items()}
    return {k: int(round(v)) for k, v in raw.items()}


def dutching(odds_list):
    odds = [float(o) for o in odds_list if float(o) > 0]
    if len(odds) < 2: return None
    inv = sum(1.0 / o for o in odds)
    return {"pct": [(1.0 / o) / inv for o in odds], "mult": 1.0 / inv, "breakeven": inv}


# ── 今日場地偏差（讀 recorder 寫嘅 postrace.json）──
def today_bias(date_str, venue):
    agg = {}; done = []
    for d in glob.glob(os.path.join(DATA_DIR, f"{date_str}__{venue}__*")):
        pj = os.path.join(d, "postrace.json")
        if not os.path.exists(pj): continue
        try:
            data = json.load(open(pj, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not data.get("completed"): continue
        done.append(data.get("race_meta", {}).get("race_no"))
        for style, s in (data.get("style_stats") or {}).items():
            a = agg.setdefault(style, {"n": 0, "top3": 0})
            a["n"] += int(s.get("n", 0)); a["top3"] += int(s.get("top3", 0))
    rows = []
    for style, a in agg.items():
        if a["n"] == 0: continue
        rate = a["top3"] / a["n"]; lift = rate / 0.25 if 0.25 else 1
        rows.append({"style": style, "n": a["n"], "rate": rate, "lift": lift,
                     "reliable": a["n"] >= 4})
    rows.sort(key=lambda x: -x["lift"])
    reliable = [r for r in rows if r["reliable"]]
    bias = ("利「" + reliable[0]["style"] + "」") if reliable else "樣本不足"
    return rows, bias, sorted([x for x in done if x])


def draw_lift(hist, dist, band=DIST_BAND):
    """檔位 × 入三甲率（此距離帶，全季歷史）。回傳 ([(label,rate,lift)], base)。"""
    try:
        d = float(dist)
    except (TypeError, ValueError):
        return [], 0.25
    h = hist[(hist["distance"] - d).abs() <= band].copy()
    if h.empty:
        return [], 0.25
    h["t3"] = (h["finishing_position"] <= 3).astype(int)
    base = h["t3"].mean() or 0.25
    out = []
    for name, lo, hi in [("內檔 1–4", 1, 4), ("中檔 5–9", 5, 9), ("外檔 10+", 10, 99)]:
        sub = h[(h["draw"] >= lo) & (h["draw"] <= hi)]
        if len(sub) >= 10:
            r = sub["t3"].mean(); out.append((name, r, r / base if base else 1))
    return out, base


def pace_lift(pace, dist, band=DIST_BAND):
    """跑法 × 入三甲率（此距離帶，全季歷史）。"""
    if pace.empty:
        return [], 0.25
    try:
        d = float(dist)
    except (TypeError, ValueError):
        return [], 0.25
    t = pace[(pace["distance"] - d).abs() <= band].copy()
    t = t.dropna(subset=["finish_position"])
    if t.empty:
        return [], 0.25
    t["t3"] = (t["finish_position"] <= 3).astype(int)
    base = t["t3"].mean() or 0.25
    out = []
    for s in ["放頭", "前置", "中置", "後置"]:
        sub = t[t["run_style"] == s]
        if len(sub) >= 10:
            r = sub["t3"].mean(); out.append((s, r, r / base if base else 1))
    return out, base


def _lift_rows_html(rows):
    h = ""
    for name, rate, lift in rows:
        cls = "lup" if lift >= 1.08 else ("ldn" if lift <= 0.92 else "lmid")
        arr = " ↑" if lift >= 1.08 else (" ↓" if lift <= 0.92 else "")
        h += (f'<div class="frow"><span class="fk">{name}</span>'
              f'<span class="{cls}">{rate*100:.0f}%{arr}</span></div>')
    return h or '<div class="psub">樣本不足</div>'


# ════════════════════════ CSS ════════════════════════
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');
:root{--bg:#0b0e14;--surface:#141925;--card:#161b27;--border:#222b3a;--text:#e6edf3;
--subtext:#9aa7b8;--muted:#5b6675;--accent:#50aaff;--good:#2ecb77;--gold:#e0a83c;
--mono:'JetBrains Mono',ui-monospace,monospace;}
html,body,.stApp{background:var(--bg)!important;color:var(--text);
font-family:'Inter',system-ui,-apple-system,'PingFang HK','Microsoft JhengHei',sans-serif;}
#MainMenu,footer,header,[data-testid="stToolbar"]{visibility:hidden;}
.block-container{padding:0.6rem 1.3rem 2rem!important;max-width:100%!important;}
[data-testid="stVerticalBlock"]{gap:0.45rem!important;}
[data-testid="stElementContainer"]{margin:0!important;}
/* ── Streamlit 原生 widget 壓深壓細（貼近 template chip）── */
[data-testid="stWidgetLabel"] p,[data-testid="stWidgetLabel"] label{font-size:9px!important;
letter-spacing:.05em;text-transform:uppercase;color:var(--muted)!important;margin-bottom:2px!important;font-weight:600;}
div[data-baseweb="select"]>div{background:var(--card)!important;border-color:var(--border)!important;
min-height:32px!important;font-size:13px!important;color:var(--text)!important;border-radius:8px!important;}
div[data-baseweb="select"] *{color:var(--text)!important;}
[data-testid="stNumberInput"] input,[data-testid="stTextInput"] input{background:var(--card)!important;
color:var(--text)!important;font-family:var(--mono)!important;font-weight:600;font-size:13px!important;}
[data-testid="stNumberInput"] div[data-baseweb="input"],
[data-testid="stNumberInputContainer"]{background:var(--card)!important;border-color:var(--border)!important;border-radius:8px!important;}
[data-testid="stNumberInput"] button{background:var(--surface)!important;border-color:var(--border)!important;}
.stButton>button{background:var(--good)!important;color:#06220f!important;border:0!important;
border-radius:8px!important;font-weight:600!important;font-size:12px!important;padding:6px 12px!important;margin-top:16px!important;}
div[data-baseweb="popover"] *,[data-baseweb="menu"] *{background:var(--card)!important;color:var(--text)!important;}
[data-baseweb="tag"]{background:rgba(80,170,255,.18)!important;}
/* ── header ── */
.hdr{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 15px;background:var(--surface);
border:1px solid var(--border);border-radius:10px;}
.hdr b{font-size:16px;} .badge{font-family:var(--mono);font-size:11px;color:var(--accent);
background:rgba(80,170,255,.12);border:1px solid rgba(80,170,255,.35);padding:3px 9px;border-radius:20px;}
/* ── 賽事資料 header ── */
.rhdr{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin-top:10px;}
.rhdr .l1{font-size:15px;font-weight:600;}
.rhdr .l1 .nv{color:var(--good);font-size:12px;margin-left:8px;}
.rhdr .l2{margin-top:5px;display:flex;gap:7px;flex-wrap:wrap;}
.rchip{background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:1px 7px;font-size:11px;color:var(--subtext);}
/* ── 因子三格 ── */
.factors{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px;margin-top:9px;}
.panel{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:11px 13px;}
.ptitle{font-size:12px;font-weight:600;margin-bottom:7px;}
.psub{font-size:10px;color:var(--muted);margin-top:5px;}
.bias{font-size:14px;font-weight:600;color:var(--gold);}
.frow{display:flex;justify-content:space-between;font-size:12px;padding:2px 0;}
.fk{color:var(--subtext);} .lup{color:var(--good);font-weight:600;font-family:var(--mono);}
.ldn{color:#ff6b6b;font-weight:600;font-family:var(--mono);} .lmid{color:var(--subtext);font-family:var(--mono);}
.nn{font-family:var(--mono);font-size:10px;color:var(--muted);font-weight:400;}
/* ── 分析表 ── */
table.an{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums;}
table.an th{position:sticky;top:0;background:var(--card);text-align:right;font-size:9px;color:var(--muted);
padding:6px 7px;border-bottom:1px solid var(--border);white-space:nowrap;}
table.an th.l,table.an td.l{text-align:left;}
table.an td{text-align:right;padding:6px 7px;border-bottom:1px solid rgba(34,43,58,.5);
font-family:var(--mono);white-space:nowrap;}
table.an tbody tr:hover td{background:rgba(80,170,255,.12);}
table.an tr.val td{background:rgba(46,203,119,.08);}
table.an tr.val:hover td{background:rgba(46,203,119,.15);}
.p1{color:#ffd43b;background:rgba(255,212,59,.12);} .p2{color:#9ae6b4;background:rgba(46,203,119,.12);}
.p3{color:#9aa7b8;background:rgba(154,167,184,.12);} .p4{color:#8ab4ff;background:rgba(80,170,255,.12);}
.chip{border-radius:5px;padding:1px 6px;font-family:'Inter',sans-serif;font-weight:600;}
.fg{color:var(--good);} .fm{color:var(--subtext);} .fb{color:#ff6b6b;}
.evpos{color:var(--good);font-weight:700;} .evneg{color:var(--muted);}
.stake{color:var(--gold);font-weight:600;} .pl{color:var(--accent);}
</style>""", unsafe_allow_html=True)


# ════════════════════════ 主程式 ════════════════════════
from streamlit_autorefresh import st_autorefresh  # noqa: E402

st.markdown(f'<div class="hdr"><b>🐎 HKJC 賽馬日分析</b>'
            f'<span class="badge">{APP_VERSION}</span>'
            f'<span class="psub">識別工具 · 非保證賺錢系統（模型 edge 未統計顯著）</span></div>',
            unsafe_allow_html=True)

bundle, hist, style, pace, model_src = load_assets()

meetings, merr = list_meetings()
if merr or not meetings:
    st.warning(f"暫時攞唔到賽期（{merr or '今日可能冇賽事'}）。稍後再試或撳更新。")
    st.stop()

# ── 控制列 ──
c1, c2, c3, c4, c5 = st.columns([2, 1.4, 1.2, 1.4, 1])
labels = [f"{m['date']} · {venue_label(m['venue'])}" for m in meetings]
idx = c1.selectbox("賽馬日", range(len(meetings)), format_func=lambda i: labels[i],
                   index=len(meetings) - 1, key="mtg")
sel = meetings[idx]
bankroll = c2.number_input("單場本金 HK$", min_value=AMT_MIN, max_value=AMT_MAX, value=1000, step=100)
refresh = c3.selectbox("刷新", ["每 60 秒", "手動"], index=0)
if c4.button("↻ 立即更新"):
    fetch_cards.clear(); score_day.clear()
mode = c5.selectbox("顯示", ["全日", "單場"], index=0)
if refresh == "每 60 秒":
    st_autorefresh(interval=60000, key="auto60")

# ── 攞 + 評分 ──
try:
    cards, meta, win_pool = fetch_cards(sel["date"], sel["venue"])
except Exception as e:
    msg = str(e)
    if "resolve" in msg or "NameResolution" in msg or "Max retries" in msg:
        st.warning("HKJC 連線一時唔通（間歇性 DNS），已自動重試仍未通。撳「↻ 立即更新」再試，或稍等下一次刷新。")
    else:
        st.error(f"攞排位/賠率失敗：{e}")
    st.stop()
if cards.empty:
    st.info("呢個賽馬日暫時冇排位資料。"); st.stop()
scored = score_day(cards.to_json(), model_src + str(bundle.get("trained_at")))

st.caption(f"模型：{model_src} · 訓練 {bundle.get('n_races','?')} 場 · "
           f"資料時間 {datetime.now(HKT):%H:%M:%S} · 歷史 {len(hist):,} 匹次")

# 今日偏差
brows, blabel, bdone = today_bias(sel["date"], sel["venue"])

race_nos = sorted(scored["race_no"].unique())
if mode == "單場":
    rsel = st.selectbox("場次", race_nos, format_func=lambda n: f"第 {n} 場")
    race_nos = [rsel]

sort_opt = st.selectbox("排序", ["EV（高→低）", "綜合勝率", "馬號", "即場賠率（低→高）"], index=0)
sort_map = {"EV（高→低）": ("ev", False), "綜合勝率": ("p_final", False),
            "馬號": ("horse_no", True), "即場賠率（低→高）": ("win_odds", True)}
sort_col, sort_asc = sort_map[sort_opt]

PACE_CLS = {"放頭": "p1", "前置": "p2", "中置": "p3", "後置": "p4", "後上": "p4"}
FIT_CLS = {"佳": "fg", "一般": "fm", "不利": "fb", "不詳": "fm"}

for rno in race_nos:
    g = scored[scored["race_no"] == rno].copy()
    m = meta.get(int(rno), {})
    dist, going = m.get("dist"), (m.get("going") or "").upper()
    # 逐匹 標籤
    recs = []
    for _, r in g.iterrows():
        dl, gl = fitness_labels(hist, r["horse_id"], dist, going)
        sname, spct = dominant_style(style, r["horse_id"])
        recs.append({**r.to_dict(), "dist_fit": dl, "going_fit": gl,
                     "pace": sname, "pace_pct": spct})
    gg = pd.DataFrame(recs).sort_values(sort_col, ascending=sort_asc, na_position="last")
    stakes = kelly_stakes(gg, bankroll, win_pool.get(int(rno)))

    nval = int((gg["ev"] >= EV_THRESHOLD).sum()) if gg["ev"].notna().any() else 0
    distm = f"{int(dist)}M" if dist == dist else "?M"
    chips = "".join(f'<span class="rchip">{c}</span>' for c in [
        m.get("cls"), f'場地：{m.get("going")}' if m.get("going") else None,
        f'跑道 {m.get("course")}' if m.get("course") else None, f'{len(gg)} 匹出賽'] if c)
    st.markdown(f'<div class="rhdr"><div class="l1">第 {int(rno)} 場 · {distm}'
                f'<span class="nv">值博 {nval} 匹</span></div><div class="l2">{chips}</div></div>',
                unsafe_allow_html=True)
    if mode == "單場":
        plist, pbase = pace_lift(pace, dist)
        dlist, dbase = draw_lift(hist, dist)
        bias_html = ((f'<div class="bias">{blabel}</div>'
                      f'<div class="psub">已完成：{", ".join("第%d場" % n for n in bdone)}</div>')
                     if bdone else '<div class="psub">今日尚未有已完成場次；頭幾場完成後逐場更新。</div>')
        st.markdown(
            '<div class="factors">'
            f'<div class="panel"><div class="ptitle">🏇 今日場地偏差</div>{bias_html}</div>'
            f'<div class="panel"><div class="ptitle">跑法 × 入三甲率 '
            f'<span class="nn">（{distm}±{DIST_BAND} · 基準 {pbase*100:.0f}%）</span></div>{_lift_rows_html(plist)}</div>'
            f'<div class="panel"><div class="ptitle">檔位 × 入三甲率 '
            f'<span class="nn">（{distm}±{DIST_BAND} · 基準 {dbase*100:.0f}%）</span></div>{_lift_rows_html(dlist)}</div>'
            '</div>', unsafe_allow_html=True)

    head = ('<tr><th class="l">馬號</th><th class="l">馬名</th><th>檔</th><th class="l">歷史跑法</th>'
            '<th class="l">距離</th><th class="l">場地</th><th>即場賠率</th><th>基本面</th><th>市場</th>'
            '<th>綜合</th><th>位置</th><th>Fair</th><th>EV</th><th>預期回報</th>'
            '<th>建議注碼</th><th>若中派彩</th><th>派彩減本金</th></tr>')
    body = ""
    for _, r in gg.iterrows():
        pos = (r["ev"] == r["ev"]) and r["ev"] >= EV_THRESHOLD
        stk = stakes.get(int(r["horse_no"]), 0)
        pcls = PACE_CLS.get(r["pace"], "p3")
        ppct = "" if r["pace_pct"] != r["pace_pct"] or r["pace_pct"] is None else f"{int(r['pace_pct'])}"
        od = r["win_odds"]
        ev_s = "—" if r["ev"] != r["ev"] else f'{r["ev"]:.2f}'
        ret_s = "—" if r["ev"] != r["ev"] else f'{(r["ev"]-1)*100:+.0f}%'
        def pc(x): return "—" if x != x else f"{x*100:.1f}%"
        fair = "—" if r["p_final"] <= 0 else f'{1/r["p_final"]:.2f}'
        pay = f'${round(stk*od)}' if stk > 0 and od == od else "—"
        net = f'+${round(stk*(od-1))}' if stk > 0 and od == od else "—"
        body += (f'<tr class="{"val" if pos else ""}">'
                 f'<td class="l" style="font-weight:600">{int(r["horse_no"])}</td>'
                 f'<td class="l">{r["horse_name"]}</td>'
                 f'<td>{int(r["draw"]) if r["draw"]==r["draw"] else "—"}</td>'
                 f'<td class="l"><span class="chip {pcls}">{r["pace"]}{ppct}</span></td>'
                 f'<td class="l"><span class="{FIT_CLS.get(r["dist_fit"],"fm")}">{r["dist_fit"]}</span></td>'
                 f'<td class="l"><span class="{FIT_CLS.get(r["going_fit"],"fm")}">{r["going_fit"]}</span></td>'
                 f'<td>{"—" if od!=od else f"{od:.1f}"}</td>'
                 f'<td>{pc(r["p_model"])}</td><td>{pc(r["p_public"])}</td>'
                 f'<td>{pc(r["p_final"])}</td><td class="pl">{pc(r["p_place"])}</td><td>{fair}</td>'
                 f'<td class="{"evpos" if pos else "evneg"}">{ev_s}</td>'
                 f'<td class="{"evpos" if pos else "evneg"}">{ret_s}</td>'
                 f'<td class="stake">{("$"+str(stk)) if stk>0 else "—"}</td>'
                 f'<td>{pay}</td><td class="{"fg" if stk>0 else ""}">{net}</td></tr>')
    st.markdown(f'<div style="overflow-x:auto"><table class="an"><thead>{head}</thead>'
                f'<tbody>{body}</tbody></table></div>', unsafe_allow_html=True)

    # Dutching（單場模式先出，避免全日太長）
    if mode == "單場":
        st.markdown('<div class="ptitle" style="margin-top:12px">🎯 Dutching 大細注（中任何一匹派彩一樣）</div>',
                    unsafe_allow_html=True)
        opts = {f'{int(r["horse_no"])} {r["horse_name"]} @{r["win_odds"]:.1f}': int(r["horse_no"])
                for _, r in gg.iterrows() if r["win_odds"] == r["win_odds"] and r["win_odds"] > 0}
        default = [k for k, v in opts.items() if (gg.set_index("horse_no").loc[v, "ev"] >= EV_THRESHOLD)] \
            if gg["ev"].notna().any() else []
        picks = st.multiselect("剔要覆蓋嘅馬", list(opts.keys()), default=default, key=f"dut{rno}")
        T = st.number_input("總注 HK$", min_value=AMT_MIN, max_value=AMT_MAX, value=1000, step=100, key=f"dt{rno}")
        pnos = [opts[p] for p in picks]
        sub = gg[gg["horse_no"].isin(pnos)]
        d = dutching(sub["win_odds"].tolist())
        if d:
            K = T * d["mult"]; cover = sub["p_final"].sum(); val = cover > d["breakeven"]
            lines = "".join(
                f'<div style="display:flex;justify-content:space-between;font-size:12px;padding:3px 0;'
                f'border-bottom:1px solid rgba(34,43,58,.4)"><span>{int(r["horse_no"])} {r["horse_name"]} '
                f'<span style="color:var(--muted)">@{r["win_odds"]:.1f}</span></span>'
                f'<span class="stake">${round(T*p)}</span></div>'
                for (_, r), p in zip(sub.iterrows(), d["pct"]))
            st.markdown(lines + f'<div style="font-size:12px;margin-top:8px;line-height:1.7">'
                        f'保證派彩 <b style="color:var(--good)">${round(K):,}</b>　淨賺 '
                        f'<b style="color:var(--good)">+${round(K-T):,}</b>（+{(d["mult"]-1)*100:.1f}%）<br>'
                        f'打和門檻 <b>{d["breakeven"]*100:.1f}%</b>　｜　模型綜合合計 '
                        f'<b style="color:{"var(--good)" if val else "#ff6b6b"}">{cover*100:.1f}%</b> → '
                        f'{"值博 ✓" if val else "唔值博 ✗"}<br>'
                        f'<span style="color:var(--muted)">⚠️ 覆蓋馬全跑唔出 → 全輸 ${round(T):,}。'
                        f'Dutching 唔變出 edge；模型 edge 未顯著。</span></div>', unsafe_allow_html=True)
        else:
            st.caption("剔 2 匹或以上先計到大細注。")

# 因子摘要（今日偏差）
with st.expander("🏇 今日場地偏差（已完成場次逐場更新）", expanded=(mode == "單場")):
    if bdone:
        st.caption(f"已完成：{', '.join('第%d場' % n for n in bdone)}　偏差：{blabel}")
        if brows:
            st.table(pd.DataFrame([{"跑法": r["style"], "樣本": r["n"],
                                    "入三甲率": f'{r["rate"]:.1%}', "Lift": f'{r["lift"]:.2f}',
                                    "狀態": "可參考" if r["reliable"] else "樣本不足"} for r in brows]))
    else:
        st.info("今日尚未有已完成場次（需 recorder 已裝 lxml 並寫到賽果）。頭幾場完成後逐場更新。")

st.caption(f"HKJC 賽馬日分析 {APP_VERSION} · 獨立服務，不影響 8501 即場監察 · "
           f"綜合勝率/EV 為模型估算，非保證結果")
