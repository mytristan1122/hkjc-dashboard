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

APP_VERSION = "8502-V1.3-UX-20261003"
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

RACING_QUERY = '\nquery racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {\n      id\n      status\n      sellStatus\n      oddsType\n      lastUpdateTime\n      guarantee\n      minTicketCost\n      name_en\n      name_ch\n      leg {\n        number\n        races\n      }\n      cWinSelections {\n        composite\n        name_ch\n        name_en\n        starters\n      }\n      oddsNodes {\n        combString\n        oddsValue\n        hotFavourite\n        oddsDropValue\n        bankerOdds {\n          combString\n          oddsValue\n        }\n      }\n    }\n  }\n}\n'

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


def _fetch_live_odds(date_str, venue):
    """即場 WIN 賠率由 oddsNodes 攞（同 8501 一樣來源；HKJC 唔填 runner.winOdds）。
    回傳 {race_no: {horse_no: win_odds}}。"""
    try:
        r = _SESSION.post(API, headers=HEADERS, json={
            "operationName": "racing", "query": RACING_QUERY,
            "variables": {"date": date_str, "venueCode": venue,
                          "oddsTypes": ["WIN"], "raceNo": None}}, timeout=25)
        r.raise_for_status()
        j = r.json()
    except Exception:
        return {}
    if j.get("errors"):
        return {}
    out = {}
    for mt in (j.get("data") or {}).get("raceMeetings") or []:
        for p in mt.get("pmPools") or []:
            if p.get("oddsType") != "WIN":
                continue
            races = (p.get("leg") or {}).get("races") or []
            for node in p.get("oddsNodes") or []:
                comb = str(node.get("combString") or "")
                od = _f(node.get("oddsValue"))
                if comb.isdigit() and od == od and od > 0:
                    hno = int(comb)
                    for rno in races:
                        out.setdefault(int(rno), {})[hno] = od
    return out


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
    odds_map = _fetch_live_odds(date_str, venue)   # 即場賠率由 oddsNodes 攞
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
                "win_odds": _f((odds_map.get(rno) or {}).get(int(no), rn.get("winOdds"))),
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
    bundle, hist, _, _, _ = load_assets()
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




# ════════════════════════ 組裝畀 template 嘅數據 ════════════════════════
PACE_NUM = {"放頭": 1, "前置": 2, "中置": 3, "後置": 4, "後上": 4}
FIT_CODE = {"佳": "good", "一般": "mid", "不利": "bad"}


def _rnd(x, n=4):
    try:
        v = float(x)
        return None if v != v else round(v, n)
    except (TypeError, ValueError):
        return None


def build_meeting(date_str, venue):
    cards, meta, win_pool = fetch_cards(date_str, venue)
    if cards.empty:
        return None
    scored = score_day(cards.to_json(), model_src + str(bundle.get("trained_at")))
    _brows, blabel, bdone = today_bias(date_str, venue)
    races = []
    for rno in sorted(scored["race_no"].unique()):
        g = scored[scored["race_no"] == rno]
        m = meta.get(int(rno), {})
        dist = m.get("dist"); going = (m.get("going") or "").upper()
        plist, pbase = pace_lift(pace, dist)
        dlist, dbase = draw_lift(hist, dist)
        horses = []
        for _, r in g.iterrows():
            dl, gl = fitness_labels(hist, r["horse_id"], dist, going)
            sname, _spct = dominant_style(style, r["horse_id"])
            od = r["win_odds"]; od = float(od) if (od == od and od > 0) else None
            draw_v = int(r["draw"]) if r["draw"] == r["draw"] else None
            horses.append({
                "no": int(r["horse_no"]), "nm": r["horse_name"], "draw": draw_v,
                "pace": PACE_NUM.get(sname, 5),
                "dist": FIT_CODE.get(dl, "unknown"), "going": FIT_CODE.get(gl, "unknown"),
                "odds": _rnd(od, 2), "pm": _rnd(r["p_model"]), "pk": _rnd(r["p_public"]),
                "pf": _rnd(r["p_final"]), "pl": _rnd(r["p_place"]),
                "ev": (_rnd(r["ev"], 3) if od is not None else None)})
        chips = [c for c in [m.get("cls"),
                             (f'場地：{m.get("going")}' if m.get("going") else None),
                             (f'跑道 {m.get("course")}' if m.get("course") else None),
                             f'{len(horses)} 匹出賽'] if c]
        dm = f"{int(dist)}M" if (dist == dist and dist) else "?M"
        bias_sub = (f'已完成：{", ".join("第%d場" % n for n in bdone)}'
                    if bdone else "今日尚未有已完成場次；頭幾場完成後逐場更新。")
        races.append({
            "no": int(rno), "title": f"第 {int(rno)} 場 · {dm}", "chips": chips,
            "bias": blabel if bdone else "樣本不足", "bias_sub": bias_sub,
            "dist": int(dist) if (dist == dist and dist) else None,
            "pbase": round(pbase, 4), "dbase": round(dbase, 4),
            "pace": [[n, round(rt, 4), round(lf, 3)] for n, rt, lf in plist],
            "draw": [[n, round(rt, 4), round(lf, 3)] for n, rt, lf in dlist],
            "horses": horses})
    return {"label": f"{date_str} · {venue_label(venue)}",
            "venue": f"{venue_label(venue)} {venue}", "races": races}


def build_data(meetings):
    out, errs = [], []
    # 只分析本地賽（沙田 ST / 跑馬地 HV）：模型只有本地歷史，海外場（S1/S2…）分析唔到，
    # 而且 list_meetings 按 (date,venue) 排，海外場會排喺沙田前面，舊有 meetings[:4]
    # 會把沙田切走。改成先揀本地場，沙田/跑馬地一定入到；冇本地場先退而顯示海外，避免空白。
    _local = [m for m in meetings if m.get("venue") in ("ST", "HV")]
    _pool = _local if _local else meetings
    for mtg in _pool[:4]:   # 最多 4 個本地賽馬日，控制成本
        try:
            md = build_meeting(mtg["date"], mtg["venue"])
            if md and md["races"]:
                out.append(md)
        except Exception as e:
            errs.append(f'{mtg["date"]} {mtg["venue"]}: {e}')
    return out, errs


# ════════════════════════ Template（= 你定稿 artifact，數據驅動）════════════════════════
TEMPLATE_HTML = r"""
<style>
:root{--bg:#0b0e14;--surface:#141925;--card:#161b27;--border:#222b3a;--text:#e6edf3;--subtext:#9aa7b8;
--muted:#5b6675;--accent:#50aaff;--good:#2ecb77;--warn:#ff8c3c;--hot:#c878ff;--gold:#e0a83c;
--mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
--sans:'Inter',system-ui,-apple-system,'PingFang HK','Microsoft JhengHei',sans-serif;color-scheme:dark;}
*{box-sizing:border-box} html,body{margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.45;
padding:6px 4px 24px;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.wrap{max-width:1250px;margin:0 auto}
.hdr{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;
padding:11px 15px;background:var(--surface);border:1px solid var(--border);border-radius:10px}
.hdr-l{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hdr-title{font-size:16px;font-weight:600}
.badge{font-family:var(--mono);font-size:11px;color:var(--accent);background:rgba(80,170,255,.12);
border:1px solid rgba(80,170,255,.35);padding:3px 9px;border-radius:20px}
.upd{font-family:var(--mono);font-size:11px;color:var(--muted)}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.ctl{display:flex;flex-direction:column;gap:3px;background:var(--card);border:1px solid var(--border);
border-radius:9px;padding:7px 11px;min-width:0}
.ctl label{font-size:9px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.ctl .v{font-size:13px;font-weight:600;color:var(--text);white-space:nowrap}
.vsel{background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:var(--sans);
font-size:13px;font-weight:600;border-radius:6px;padding:3px 6px;cursor:pointer;outline:none}
.binput{background:var(--surface);border:1px solid var(--accent);border-radius:6px;color:var(--text);
font-family:var(--mono);font-size:13px;font-weight:600;padding:3px 8px;width:92px;outline:none}
.binput:focus{box-shadow:0 0 0 2px rgba(80,170,255,.25)}
.pick{width:15px;height:15px;accent-color:var(--accent);cursor:pointer;vertical-align:middle}
#dutchout .drow{display:flex;justify-content:space-between;gap:12px;font-size:12px;padding:4px 2px;border-bottom:1px solid rgba(34,43,58,.45)}
#dutchout .dsum{margin-top:10px;font-size:12px;line-height:1.75}
.racehdr{margin-top:12px;padding:11px 14px;background:var(--card);border:1px solid var(--border);border-radius:10px}
.racehdr .line1{font-size:15px;font-weight:600}
.racehdr .line2{font-size:12px;color:var(--subtext);margin-top:3px;display:flex;gap:7px;flex-wrap:wrap}
.chip{background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:1px 7px;font-size:11px}
.factors{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px;margin-top:11px}
.panel{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:11px 13px;min-width:0}
.panel-title{font-size:12px;font-weight:600;color:var(--text);margin-bottom:7px}
.panel-sub{font-size:10px;color:var(--muted);margin-top:6px}
.frow{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;padding:2px 0}
.frow .k{color:var(--subtext)} .frow .lift{font-family:var(--mono);font-weight:600}
.lift.up{color:var(--good)} .lift.dn{color:#ff6b6b} .lift.mid{color:var(--subtext)}
.bias{font-size:13px;font-weight:600;color:var(--gold)} .n{font-family:var(--mono);font-size:10px;color:var(--muted)}
.tblwrap{margin-top:12px;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:11px 13px}
.tbltop{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:9px}
.tbltitle{font-size:13px;font-weight:600} .sorthint{font-size:11px;color:var(--muted)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:1240px}
thead th{position:sticky;top:0;background:var(--card);text-align:right;font-size:9px;font-weight:600;
letter-spacing:.03em;color:var(--muted);padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none}
thead th:hover{color:var(--subtext)} thead th.active{color:var(--accent)}
thead th .ind{color:var(--accent);font-size:9px;font-family:var(--mono)} thead th.l{text-align:left}
tbody td{text-align:right;font-size:12px;padding:6px 8px;border-bottom:1px solid rgba(34,43,58,.55);font-family:var(--mono);white-space:nowrap}
tbody td.l{text-align:left;font-family:var(--sans)}
tbody tr:hover td{background:rgba(80,170,255,.12)}
tbody tr.ev-pos td{background:rgba(46,203,119,.07)} tbody tr.ev-pos:hover td{background:rgba(46,203,119,.15)}
.no{color:var(--text);font-weight:600} .nm{color:var(--text)}
.draw{display:inline-block;min-width:20px;text-align:center;background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:0 5px;font-size:11px}
.draw.inside{color:var(--good);border-color:rgba(46,203,119,.4)} .draw.outside{color:#ff8c3c;border-color:rgba(255,140,60,.35)}
.pace{display:inline-block;border-radius:5px;padding:1px 7px;font-size:11px;font-weight:600;font-family:var(--sans)}
.pace.p1{color:#ffd43b;background:rgba(255,212,59,.12)} .pace.p2{color:#9ae6b4;background:rgba(46,203,119,.12)}
.pace.p3{color:#9aa7b8;background:rgba(154,167,184,.12)} .pace.p4{color:#8ab4ff;background:rgba(80,170,255,.12)}
.pace.p5{color:var(--muted);background:rgba(91,102,117,.12)}
.fit{font-family:var(--sans);font-size:11px} .fit.good{color:var(--good)} .fit.mid{color:var(--subtext)} .fit.bad{color:#ff6b6b}
.ev{font-weight:700} .ev.pos{color:var(--good)} .ev.neg{color:var(--muted)}
.ret.pos{color:var(--good)} .ret.neg{color:#8a94a3}
.stake{color:var(--gold);font-weight:600;font-family:var(--mono)} .payout{color:var(--subtext);font-family:var(--mono)}
.foot{margin-top:14px;font-size:11px;color:var(--muted);line-height:1.6} .foot b{color:var(--subtext)}
.legend{display:flex;gap:12px;flex-wrap:wrap;margin-top:7px;font-size:10px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:4px} .dot{width:8px;height:8px;border-radius:2px;display:inline-block}
</style>
<div class="wrap">
  <div class="hdr">
    <div class="hdr-l"><span style="font-size:18px">🐎</span>
      <span class="hdr-title">HKJC 賽馬日分析</span>
      <span class="badge">8502-V1</span>
      <span class="upd" id="modelline"></span></div>
    <div class="hdr-r"><span class="upd" id="upd"></span></div>
  </div>
  <div class="controls">
    <div class="ctl"><label>賽馬日</label><select id="selMeet" class="vsel" onchange="onMeet()"></select></div>
    <div class="ctl"><label>場地</label><span class="v" id="venueLbl"></span></div>
    <div class="ctl"><label>場次</label><select id="selRace" class="vsel" onchange="onRace()"></select></div>
    <div class="ctl"><label>EV 回扣</label><span class="v">獨贏 10%</span></div>
    <div class="ctl"><label>單場本金 (HK$100–10000)</label><input id="bankroll" class="binput" type="number" value="1000" min="100" max="10000" step="100"></div>
    <div class="ctl"><label>刷新</label><span class="v">每 60 秒</span></div>
    <div class="ctl"><label>資料來源</label><span class="v">直連 HKJC</span></div>
  </div>
  <div class="racehdr" id="racehdr"></div>
  <div class="factors" id="factors"></div>
  <div class="tblwrap">
    <div class="tbltop"><div class="tbltitle">📊 每匹馬分析 · 綜合勝率 × 即場賠率 → EV</div>
      <div class="sorthint">撳任何欄位標題排序（再撳一次反序 ▲▼）</div></div>
    <div class="scroll"><table><thead><tr id="hdr">
      <th class="l" style="width:26px;cursor:default;text-align:center">選</th>
      <th class="l" data-k="no" data-t="num" data-d="asc" onclick="sortCol(this)">馬號<span class="ind"></span></th>
      <th class="l" data-k="nm" data-t="txt" data-d="asc" onclick="sortCol(this)">馬名<span class="ind"></span></th>
      <th data-k="draw" data-t="num" data-d="asc" onclick="sortCol(this)">檔位<span class="ind"></span></th>
      <th class="l" data-k="pace" data-t="num" data-d="asc" onclick="sortCol(this)">歷史跑法<span class="ind"></span></th>
      <th class="l" data-k="dist" data-t="rank" data-d="desc" onclick="sortCol(this)">距離<span class="ind"></span></th>
      <th class="l" data-k="going" data-t="rank" data-d="desc" onclick="sortCol(this)">場地<span class="ind"></span></th>
      <th data-k="odds" data-t="num" data-d="asc" onclick="sortCol(this)">即場賠率<span class="ind"></span></th>
      <th data-k="pm" data-t="num" data-d="desc" onclick="sortCol(this)">基本面勝率<span class="ind"></span></th>
      <th data-k="pk" data-t="num" data-d="desc" onclick="sortCol(this)">市場勝率<span class="ind"></span></th>
      <th data-k="pf" data-t="num" data-d="desc" onclick="sortCol(this)">綜合勝率<span class="ind"></span></th>
      <th data-k="pl" data-t="num" data-d="desc" onclick="sortCol(this)">位置概率<span class="ind"></span></th>
      <th data-k="fair" data-t="num" data-d="asc" onclick="sortCol(this)">Fair<span class="ind"></span></th>
      <th id="thEV" data-k="ev" data-t="num" data-d="desc" onclick="sortCol(this)">EV<span class="ind"></span></th>
      <th data-k="ret" data-t="num" data-d="desc" onclick="sortCol(this)">預期回報<span class="ind"></span></th>
      <th data-k="stake" data-t="num" data-d="desc" onclick="sortCol(this)">建議注碼<span class="ind"></span></th>
      <th data-k="payout" data-t="num" data-d="desc" onclick="sortCol(this)">若中派彩<span class="ind"></span></th>
      <th data-k="netwin" data-t="num" data-d="desc" onclick="sortCol(this)">派彩減本金<span class="ind"></span></th>
    </tr></thead><tbody id="tb"></tbody></table></div>
    <div class="legend">
      <span><span class="dot" style="background:rgba(46,203,119,.5)"></span>EV &gt; 1.0（有價值，綠底）</span>
      <span><span class="pace p1" style="padding:0 5px">放頭</span><span class="pace p2" style="padding:0 5px">前置</span><span class="pace p3" style="padding:0 5px">中置</span><span class="pace p4" style="padding:0 5px">後置</span></span>
    </div>
  </div>
  <div class="tblwrap">
    <div class="tbltop"><div class="tbltitle">🎯 Dutching 大細注 · 中任何一匹派彩都一樣</div>
      <div class="sorthint">喺上表左邊「選」格剔要覆蓋嘅馬　·　總注 HK$ <input id="dtotal" class="binput" style="width:86px" type="number" value="1000" min="100" max="10000" step="100"> <span style="color:var(--muted)">($100–10000)</span></div></div>
    <div id="dutchout"></div>
  </div>
  <div class="foot">
    <b>點睇：</b>綜合勝率＝基本面勝率（跑法／檔位／距離／場地 等因子）與 市場勝率（即場賠率反推）結合。
    EV＝綜合勝率 × 即場賠率；EV &gt; 1 代表有潛在價值。預期回報＝EV − 1。<br>
    <b>建議注碼：</b>1/4 凱利（按「本金」計），只對 EV &gt; 1 嘅馬顯示。<b>若中派彩＝</b>注碼 × 賠率；<b>派彩減本金＝</b>中咗淨賺。<br>
    <b>提醒：</b>綜合勝率/EV 係模型估算，<b>唔等於保證結果</b>；模型 edge 統計上未顯著。賠率「—」代表該場未開賣。
  </div>
</div>
<script>
const D = window.__DATA__ || {meetings:[]};
const paceName={1:"放頭",2:"前置",3:"中置",4:"後置",5:"不詳"};
const fitName={good:["佳","good"],mid:["一般","mid"],bad:["不利","bad"],unknown:["不詳","mid"]};
const rankMap={good:3,mid:2,bad:1,unknown:0};
const AMT_MIN=100,AMT_MAX=10000;
let rows=[],picked=new Set(),curEl=null,mi=0,ri=0;
function SS(k,v){try{if(v===undefined)return sessionStorage.getItem(k);sessionStorage.setItem(k,v);}catch(e){return null;}}
function bankroll(){const v=parseFloat((document.getElementById('bankroll')||{}).value);return (isFinite(v)&&v>0)?Math.min(AMT_MAX,v):0;}
function clampBox(el){let v=parseFloat(el.value);if(!isFinite(v)){el.value=AMT_MIN;return;}el.value=Math.min(AMT_MAX,Math.max(AMT_MIN,Math.round(v)));}
function kfrac(r){if(r.odds==null||r.odds<=1)return 0;const e=r.pf*r.odds-1;return e<=0?0:(e/(r.odds-1))*0.25;}
function stakeOf(r){return (r.odds==null||r.ev==null)?0:Math.round(bankroll()*kfrac(r));}
function pct(x){return x==null?'—':(x*100).toFixed(1)+'%';}
function drawCls(d){return d<=4?"inside":(d>=10?"outside":"");}
function render(list){
  window.lastList=list;
  document.getElementById('tb').innerHTML=list.map(r=>{
    const evN=r.ev==null,pos=!evN&&r.ev>=1.0,s=stakeOf(r);
    const fd=fitName[r.dist]||["—","mid"],fg=fitName[r.going]||["—","mid"];
    const od=r.odds,odS=od==null?'—':od.toFixed(1),dash='<span style="color:var(--muted)">—</span>';
    return '<tr class="'+(pos?'ev-pos':'')+'">'
      +'<td class="l" style="text-align:center"><input type="checkbox" class="pick" '+(picked.has(r.no)?'checked':'')+(od==null?' disabled':'')+' onchange="togglePick('+r.no+',this.checked)"></td>'
      +'<td class="l no">'+r.no+'</td><td class="l nm">'+r.nm+'</td>'
      +'<td><span class="draw '+(r.draw!=null?drawCls(r.draw):'')+'">'+(r.draw!=null?r.draw:'—')+'</span></td>'
      +'<td class="l"><span class="pace p'+r.pace+'">'+paceName[r.pace]+'</span></td>'
      +'<td class="l"><span class="fit '+fd[1]+'">'+fd[0]+'</span></td>'
      +'<td class="l"><span class="fit '+fg[1]+'">'+fg[0]+'</span></td>'
      +'<td>'+odS+'</td><td>'+pct(r.pm)+'</td><td>'+pct(r.pk)+'</td><td>'+pct(r.pf)+'</td>'
      +'<td style="color:var(--accent)">'+pct(r.pl)+'</td>'
      +'<td>'+(r.pf>0?(1/r.pf).toFixed(2):'—')+'</td>'
      +'<td class="ev '+(pos?'pos':'neg')+'">'+(evN?'—':r.ev.toFixed(2))+'</td>'
      +'<td class="ret '+(pos?'pos':'neg')+'">'+(evN?'—':((r.ev>=1?'+':'')+((r.ev-1)*100).toFixed(0)+'%'))+'</td>'
      +'<td class="stake">'+(s>0?'$'+s:dash)+'</td>'
      +'<td class="payout">'+(s>0?'$'+Math.round(s*od):dash)+'</td>'
      +'<td class="ret '+(s>0?'pos':'')+'">'+(s>0?'+$'+Math.round(s*(od-1)):dash)+'</td></tr>';
  }).join('');
}
const acc={no:r=>r.no,nm:r=>r.nm,draw:r=>r.draw==null?-1:r.draw,pace:r=>r.pace,
  dist:r=>rankMap[r.dist],going:r=>rankMap[r.going],odds:r=>r.odds==null?1e9:r.odds,
  pm:r=>r.pm,pk:r=>r.pk==null?-1:r.pk,pf:r=>r.pf,pl:r=>r.pl,fair:r=>r.pf>0?1/r.pf:1e9,
  ev:r=>r.ev==null?-1:r.ev,ret:r=>r.ev==null?-1:r.ev,stake:r=>stakeOf(r),
  payout:r=>stakeOf(r)*(r.odds||0),netwin:r=>stakeOf(r)*((r.odds||0)-1)};
function sortCol(th){
  const k=th.dataset.k,t=th.dataset.t;
  let dir=(curEl===th)?(th.dataset.cur==='asc'?'desc':'asc'):th.dataset.d;
  th.dataset.cur=dir;curEl=th;SS('sk',k);SS('sd',dir);
  const f=acc[k];
  const l=[...rows].sort((a,b)=>{const x=f(a),y=f(b);
    if(t==='txt')return dir==='asc'?String(x).localeCompare(String(y),'zh-Hant'):String(y).localeCompare(String(x),'zh-Hant');
    return dir==='asc'?x-y:y-x;});
  render(l);
  document.querySelectorAll('#hdr th').forEach(h=>{h.classList.remove('active');const i=h.querySelector('.ind');if(i)i.textContent='';});
  th.classList.add('active');th.querySelector('.ind').textContent=dir==='asc'?' ▲':' ▼';
}
function togglePick(no,on){on?picked.add(no):picked.delete(no);renderDutch();}
function renderDutch(){
  const out=document.getElementById('dutchout');
  const sel=rows.filter(r=>picked.has(r.no)&&r.odds!=null).sort((a,b)=>a.odds-b.odds);
  if(sel.length<2){out.innerHTML='<div style="color:var(--muted);font-size:12px">喺上表剔選 2 匹或以上（有賠率）嘅馬，就會計大細注。</div>';return;}
  const T=Math.min(AMT_MAX,Math.max(0,parseFloat(document.getElementById('dtotal').value)||0));
  const invsum=sel.reduce((a,r)=>a+1/r.odds,0),mult=1/invsum,K=T*mult,cover=sel.reduce((a,r)=>a+r.pf,0),val=cover>invsum;
  const rh=sel.map(r=>{const st=T*(1/r.odds)/invsum;
    return '<div class="drow"><span>'+r.no+' '+r.nm+' <span style="color:var(--muted)">@'+r.odds.toFixed(1)+'</span></span><span><b style="color:var(--gold)">$'+Math.round(st)+'</b> <span style="color:var(--muted)">('+(T?((st/T*100).toFixed(1)):0)+'%)</span></span></div>';}).join('');
  out.innerHTML=rh+'<div class="dsum">保證派彩（中任何一匹）：<b style="color:var(--good)">$'+Math.round(K).toLocaleString()+'</b>　淨賺 <b style="color:var(--good)">+$'+Math.round(K-T).toLocaleString()+'</b>（+'+((mult-1)*100).toFixed(1)+'%）<br>打和門檻：呢 '+sel.length+' 匹合計真實勝率需 &gt; <b>'+(invsum*100).toFixed(1)+'%</b>　｜　模型綜合勝率合計 = <b style="color:'+(val?'var(--good)':'#ff6b6b')+'">'+(cover*100).toFixed(1)+'%</b> → '+(val?'模型覺得值博 ✓':'模型覺得唔值博 ✗')+'<br><span style="color:var(--muted)">⚠️ 若 '+sel.length+' 匹全部跑唔出 → 全輸 $'+Math.round(T).toLocaleString()+'。Dutching 唔變出 edge；模型 edge 未統計顯著。</span></div>';
}
function lhtml(arr){return (arr&&arr.length)?arr.map(a=>{const n=a[0],rate=a[1],lift=a[2];
  const c=lift>=1.08?'up':(lift<=0.92?'dn':'mid'),ar=lift>=1.08?' ↑':(lift<=0.92?' ↓':'');
  return '<div class="frow"><span class="k">'+n+'</span><span class="lift '+c+'">'+(rate*100).toFixed(0)+'%'+ar+'</span></div>';}).join(''):'<div class="panel-sub">樣本不足</div>';}
function applyRace(){
  const mt=D.meetings[mi]||{races:[]},rc=mt.races[ri]||{horses:[],chips:[],pace:[],draw:[]};
  rows=rc.horses||[];
  picked=new Set(rows.filter(r=>r.ev!=null&&r.ev>=1.05&&r.odds!=null).map(r=>r.no));
  const nval=rows.filter(r=>r.ev!=null&&r.ev>=1.05).length;
  document.getElementById('racehdr').innerHTML='<div class="line1">'+(rc.title||'')+'<span style="color:var(--good);font-size:12px;margin-left:10px">值博 '+nval+' 匹</span></div><div class="line2">'+(rc.chips||[]).map(c=>'<span class="chip">'+c+'</span>').join('')+'</div>';
  const dm=rc.dist?rc.dist+'M':'?M';
  document.getElementById('factors').innerHTML=
    '<div class="panel"><div class="panel-title">🏇 今日場地偏差</div><div class="bias">'+(rc.bias||'樣本不足')+'</div><div class="panel-sub">'+(rc.bias_sub||'')+'</div></div>'+
    '<div class="panel"><div class="panel-title">跑法 × 入三甲率 <span class="n">（'+dm+'±100 · 基準 '+Math.round((rc.pbase||.25)*100)+'%）</span></div>'+lhtml(rc.pace)+'</div>'+
    '<div class="panel"><div class="panel-title">檔位 × 入三甲率 <span class="n">（'+dm+'±100 · 基準 '+Math.round((rc.dbase||.25)*100)+'%）</span></div>'+lhtml(rc.draw)+'</div>';
  let th=document.getElementById('thEV');const sk=SS('sk'),sd=SS('sd');
  if(sk){const t2=document.querySelector('#hdr th[data-k="'+sk+'"]');if(t2){th=t2;if(sd)th.dataset.d=sd;}}
  curEl=null;sortCol(th);renderDutch();
}
function fillRaces(){
  const mt=D.meetings[mi]||{races:[]};
  document.getElementById('selRace').innerHTML=mt.races.map((r,i)=>'<option value="'+i+'">第 '+r.no+' 場</option>').join('');
  if(ri>=mt.races.length)ri=0;
  document.getElementById('selRace').value=ri;
  document.getElementById('venueLbl').textContent=mt.venue||'';
}
function onMeet(){mi=+document.getElementById('selMeet').value;ri=0;SS('mi',mi);SS('ri',ri);fillRaces();applyRace();}
function onRace(){ri=+document.getElementById('selRace').value;SS('ri',ri);applyRace();}
(function(){
  document.getElementById('modelline').textContent='模型 '+(D.src||'')+' · 訓練 '+(D.nrace||'')+' 場 · 歷史 '+(D.nhist||'')+' 匹次';
  document.getElementById('upd').textContent='更新 '+(D.updated||'')+' · 直連 HKJC';
  if(!D.meetings||!D.meetings.length){document.getElementById('racehdr').innerHTML='<div class="line1">暫時冇賽馬日資料</div>';return;}
  document.getElementById('selMeet').innerHTML=D.meetings.map((m,i)=>'<option value="'+i+'">'+m.label+'</option>').join('');
  mi=Math.min(parseInt(SS('mi')||'0',10)||0,D.meetings.length-1);if(mi<0)mi=0;
  document.getElementById('selMeet').value=mi;fillRaces();
  ri=Math.min(parseInt(SS('ri')||'0',10)||0,((D.meetings[mi]||{races:[]}).races.length||1)-1);if(ri<0)ri=0;
  document.getElementById('selRace').value=ri;
  const bk=SS('bk');if(bk)document.getElementById('bankroll').value=bk;
  const dt=SS('dt');if(dt)document.getElementById('dtotal').value=dt;
  applyRace();
  document.getElementById('bankroll').addEventListener('input',()=>render(window.lastList||rows));
  document.getElementById('bankroll').addEventListener('change',e=>{clampBox(e.target);SS('bk',e.target.value);render(window.lastList||rows);});
  document.getElementById('dtotal').addEventListener('input',renderDutch);
  document.getElementById('dtotal').addEventListener('change',e=>{clampBox(e.target);SS('dt',e.target.value);renderDutch();});
})();
</script>
"""


# ════════════════════════ 主程式 ════════════════════════
import streamlit.components.v1 as components  # noqa: E402
from streamlit_autorefresh import st_autorefresh  # noqa: E402

st.markdown("""<style>#MainMenu,footer,header,[data-testid="stToolbar"]{visibility:hidden;}
.stApp{background:#0b0e14!important;} .block-container{padding:0!important;max-width:100%!important;}
[data-testid="stElementContainer"]{margin:0!important;}
/* 防止每 60 秒刷新時成版變暗（stale overlay）*/
[data-testid="stStatusWidget"]{display:none!important;}
.stApp [data-testid="stAppViewContainer"] *{animation:none!important;}
.element-container,.stMarkdown{transition:none!important;animation:none!important;}
[data-testid="stAppViewBlockContainer"]{opacity:1!important;}
.stApp>div[data-stale="true"]{opacity:1!important;filter:none!important;}
[data-stale="true"]{opacity:1!important;filter:none!important;}
iframe{opacity:1!important;}</style>""", unsafe_allow_html=True)

st_autorefresh(interval=60000, key="a60")

bundle, hist, style, pace, model_src = load_assets()
meetings, merr = list_meetings()
if merr or not meetings:
    st.warning(f"HKJC 連線一時唔通或今日冇賽事（{merr or '無賽期'}）。稍等下一次刷新或重載頁面。")
    st.stop()

data, derrs = build_data(meetings)
if not data:
    st.warning("HKJC 連線一時唔通（間歇性 DNS），已自動重試仍未攞到排位。稍等下一次刷新。"
               + (f"　（{derrs[0]}）" if derrs else ""))
    st.stop()

payload = {"updated": datetime.now(HKT).strftime("%H:%M:%S"), "src": model_src,
           "nrace": bundle.get("n_races"), "nhist": len(hist), "meetings": data}
html = TEMPLATE_HTML.replace("window.__DATA__ || {meetings:[]}",
                             "window.__DATA__ || " + json.dumps(payload, ensure_ascii=False))
components.html(html, height=1050, scrolling=True)