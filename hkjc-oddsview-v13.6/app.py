
import streamlit as st
import requests
import time as _time
import pandas as pd
import numpy as np
from datetime import datetime, date, timezone, timedelta
from collections import defaultdict, deque
from streamlit_autorefresh import st_autorefresh

APP_VERSION = "v13.6 STHV"
APP_NAME = "HKJC 即時賠率監察"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# ════════════════════════════════════════════════════════════
#  CONFIG / CONSTANTS
# ════════════════════════════════════════════════════════════
API = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": "https://bet.hkjc.com/",
    "User-Agent": "Mozilla/5.0",
}
HKT = timezone(timedelta(hours=8))   # Hong Kong time

INFO = "#185FA5"     # 落飛 (odds down / money in) — blue
DANGER = "#A32D2D"   # 回飛 (odds up / money out) — red
MUTE = "#888888"     # 平穩 (flat) — grey

FLAT_THRESHOLD = 2.0       # |%| <= this  -> 平穩 (faint grey)
PLUNGE_PCT = 8.0           # drop >= this % within PLUNGE_WINDOW -> 插水 alert
PLUNGE_WINDOW = 30         # seconds
AXIS_MINUTES = 14          # countdown axis spans -14min -> 0 (post)

# ── 投注額急升偵測（資金流向表）──
SURGE_WINDOW = 30          # 近 N 秒
SURGE_MIN_DROP = 4.0       # 近30秒賠率跌幅 >= 此 % -> ▲ 急跌（有錢入）
SURGE_BIG_DROP = 8.0       # 近30秒賠率跌幅 >= 此 % -> 🔥 大量湧入

# ── 綜合評分（100 分制）配置 ──
# 各項滿分：資金急升 40 + 賠率急跌 30 + 水位成熟 20 + 獨位一致 10 = 100
SCORE_SURGE_MAX = 40       # 資金急升（相對全場突出程度）
SCORE_DROP_MAX  = 30       # 賠率急跌（絕對跌幅級別）
SCORE_WATER_MAX = 20       # 水位成熟（越接近 1.21 越高）
SCORE_AGREE_MAX = 10       # 獨贏／位置同時流入
SCORE_DROP_FULL = 10.0     # 賠率跌 >= 此 % 得滿分（30）
WATER_IDEAL = 1.21         # 理論成熟水位
WATER_LOOSE = 1.45         # 水位 >= 此值 -> 0 分（未成熟）

RACING_QUERY = """
query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      status
      sellStatus
      oddsType
      lastUpdateTime
      guarantee
      minTicketCost
      name_en
      name_ch
      leg {
        number
        races
      }
      cWinSelections {
        composite
        name_ch
        name_en
        starters
      }
      oddsNodes {
        combString
        oddsValue
        hotFavourite
        oddsDropValue
        bankerOdds {
          combString
          oddsValue
        }
      }
    }
  }
}
"""

# Pool investment (turnover) — uses HKJC's EXACT official query verbatim.
# Whitelist matches the string literally, so DO NOT modify this string.
TURNOVER_QUERY = "fragment raceFragment on Race {\n  id\n  no\n  status\n  raceName_en\n  raceName_ch\n  postTime\n  country_en\n  country_ch\n  distance\n  wageringFieldSize\n  go_en\n  go_ch\n  ratingType\n  raceTrack {\n    description_en\n    description_ch\n  }\n  raceCourse {\n    description_en\n    description_ch\n    displayCode\n  }\n  claCode\n  raceClass_en\n  raceClass_ch\n  judgeSigns {\n    value_en\n  }\n}\n\nfragment racingBlockFragment on RaceMeeting {\n  jpEsts: pmPools(\n    oddsTypes: [WIN, PLA, TCE, TRI, FF, QTT, DT, TT, SixUP]\n    filters: [\"jackpot\", \"estimatedDividend\"]\n  ) {\n    leg {\n      number\n      races\n    }\n    oddsType\n    jackpot\n    estimatedDividend\n    mergedPoolId\n  }\n  poolInvs: pmPools(\n    oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n  ) {\n    id\n    leg {\n      races\n    }\n  }\n  penetrometerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  hammerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  changeHistories(filters: [\"top3\"]) {\n    type\n    time\n    raceNo\n    runnerNo\n    horseName_ch\n    horseName_en\n    jockeyName_ch\n    jockeyName_en\n    scratchHorseName_ch\n    scratchHorseName_en\n    handicapWeight\n    scrResvIndicator\n  }\n}\n\nquery raceMeetings($date: String, $venueCode: String) {\n  timeOffset {\n    rc\n  }\n  activeMeetings: raceMeetings {\n    id\n    venueCode\n    date\n    status\n    races {\n      no\n      postTime\n      status\n      wageringFieldSize\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      status\n    }\n  }\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    id\n    status\n    venueCode\n    date\n    totalNumberOfRace\n    currentNumberOfRace\n    dateOfWeek\n    meetingType\n    totalInvestment\n    country {\n      code\n      namech\n      nameen\n      seq\n    }\n    races {\n      ...raceFragment\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n    obSt: pmPools(oddsTypes: [WIN, PLA]) {\n      leg {\n        races\n      }\n      oddsType\n      comingleStatus\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      id\n      leg {\n        number\n        races\n      }\n      status\n      sellStatus\n      oddsType\n      investment\n      mergedPoolId\n      lastUpdateTime\n    }\n    ...racingBlockFragment\n    pmPools(oddsTypes: []) {\n      id\n    }\n    jkcInstNo: foPools(oddsTypes: [JKC], filters: [\"top\"]) {\n      instNo\n    }\n    tncInstNo: foPools(oddsTypes: [TNC], filters: [\"top\"]) {\n      instNo\n    }\n  }\n}"

# ════════════════════════════════════════════════════════════
#  STYLES
# ════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');
:root {
  --bg:#0b0e14; --surface:#141925; --card:#161b27; --border:#222b3a;
  --text:#e6edf3; --subtext:#9aa7b8; --muted:#5b6675;
}
html, body, .stApp { background:var(--bg)!important; color:var(--text); font-family:'Inter',sans-serif; }
#MainMenu, footer, header { visibility:hidden; }
.block-container { padding:0.8rem 1.6rem 2rem!important; max-width:100%!important; }
.hdr { display:flex; align-items:center; justify-content:space-between; padding:10px 16px;
  background:var(--surface); border:1px solid var(--border); border-radius:10px; margin-bottom:10px; }
.hdr-title { font-size:16px; font-weight:600; color:var(--text); }
.live { font-family:'JetBrains Mono',monospace; font-size:11px; color:#ff5757;
  background:rgba(255,87,87,0.12); border:1px solid rgba(255,87,87,0.35);
  padding:3px 10px; border-radius:20px; }
.live-dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:#ff5757;
  margin-right:5px; animation:pulse 1.4s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
@keyframes surgeflash { 0%,100%{background:rgba(239,68,68,0.18)} 50%{background:rgba(239,68,68,0.04)} }
.surge-gate { animation:surgeflash 1.1s infinite; border-radius:6px; }
.stake-barwrap { flex:1; min-width:0; height:9px; background:rgba(255,255,255,0.06); border-radius:4px; overflow:hidden; display:flex; align-items:center; }
.stake-bar { display:block; height:100%; border-radius:4px; }
.alert-bar { background:rgba(163,45,45,0.15); border:1px solid rgba(163,45,45,0.4);
  border-radius:8px; padding:8px 14px; margin-bottom:10px; font-size:13px; color:#ff8585; }
.panel { background:var(--card); border:1px solid var(--border); border-radius:12px;
  padding:12px 14px; margin-bottom:10px; }
.panel-title { font-size:13px; font-weight:600; color:var(--text); margin-bottom:2px; }
.panel-sub { font-size:11px; color:var(--subtext); margin-bottom:8px; }
.legend { display:flex; flex-wrap:wrap; gap:14px; margin-top:8px;
  font-family:'JetBrains Mono',monospace; font-size:10px; color:var(--subtext); }
.legend i { display:inline-block; width:13px; height:2px; vertical-align:middle; margin-right:3px; }
.row { display:flex; gap:8px; align-items:center; padding:2.5px 0; border-bottom:0.5px solid var(--border); }
.row:hover { background:rgba(255,255,255,0.03); }
.c-no { width:30px; flex:none; font-size:12px; font-weight:600; }
.c-spark { flex:1; min-width:0; }
.c-num { width:42px; flex:none; text-align:right; font-family:'JetBrains Mono',monospace; font-size:11px; }
.c-chg { width:48px; flex:none; text-align:right; font-family:'JetBrains Mono',monospace; font-size:11px; }
.c-score { flex:1; min-width:0; text-align:right; font-family:'JetBrains Mono',monospace; }
.thead { display:flex; gap:8px; padding-bottom:5px; border-bottom:0.5px solid var(--border);
  font-family:'JetBrains Mono',monospace; font-size:10px; color:var(--muted); }
.axis { display:flex; gap:8px; margin-top:2px; padding-top:5px; border-top:0.5px solid var(--border); }
.axis-inner { flex:1; display:flex; justify-content:space-between; }
.axis-inner span { font-family:'JetBrains Mono',monospace; font-size:9px; color:var(--muted); }
.rank-row { padding:5px 0; border-bottom:0.5px solid var(--border); }
.rank-head { display:flex; justify-content:space-between; margin-bottom:3px; }
.rank-bar { height:5px; background:rgba(255,255,255,0.06); border-radius:3px; overflow:hidden; }
.rank-fill { height:100%; }
.flame { color:#ff5757; font-size:10px; margin-left:2px; }
.div-row { display:flex; align-items:center; gap:10px; padding:6px 0;
  border-bottom:0.5px solid var(--border); flex-wrap:wrap; font-size:12px; }
.pill { font-size:11px; padding:2px 8px; border-radius:6px; }
.pill-win { background:rgba(24,95,165,0.18); color:#5ea0e0; }
.pill-mute { background:rgba(255,255,255,0.06); color:var(--subtext); }
.empty { text-align:center; padding:3rem; color:var(--subtext); }

/* ── 手機優化（窄螢幕）── */
@media (max-width: 640px) {
  .block-container { padding-left:0.5rem !important; padding-right:0.5rem !important;
    padding-top:1rem !important; }
  .panel { padding:10px !important; }
  .c-no { width:26px; font-size:11px; }
  .c-num { width:38px; font-size:10px; }
  .c-chg { width:44px; font-size:10px; }
  .c-score { font-size:10px; }
  .panel-title { font-size:13px; }
  .panel-sub { font-size:10px; }
  .row { padding:4px 0; }
  /* 讓左右兩欄（獨贏/位置）喺手機直接上下排 */
  [data-testid="column"] { width:100% !important; flex:1 1 100% !important;
    min-width:100% !important; }
}
</style>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
""", unsafe_allow_html=True)

st_autorefresh(interval=5000, key="auto_refresh")

# ════════════════════════════════════════════════════════════
#  SESSION STATE (multi-race: each race keeps its own history)
# ════════════════════════════════════════════════════════════
def _blank_state():
    return {
        "race_key": None,
        "open_odds": {},      # (pool, horse) -> first odds seen
        "last_odds": {},      # (pool, horse) -> previous odds
        "series": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(x_min, odds)]
        "stake_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts_epoch, stake$)]
        "ever_surged": {},    # (pool,horse) -> peak drop% ever seen (for 🔥 memory)
        "post_time": None,    # datetime in HKT
        "started_at": None,   # when monitoring began
    }

# RACES: race_key -> state dict. Switching races no longer wipes data;
# each race accumulates independently and is remembered.
if "RACES" not in st.session_state:
    st.session_state.RACES = {}

def get_state(race_key):
    if race_key not in st.session_state.RACES:
        s = _blank_state()
        s["race_key"] = race_key
        s["started_at"] = datetime.now(HKT)
        st.session_state.RACES[race_key] = s
    return st.session_state.RACES[race_key]

def reset_state(race_key):
    """Reset only the given race's accumulated history."""
    s = _blank_state()
    s["race_key"] = race_key
    s["started_at"] = datetime.now(HKT)
    st.session_state.RACES[race_key] = s
    return s

# ════════════════════════════════════════════════════════════
#  DATA FETCH
# ════════════════════════════════════════════════════════════
def _to_float(x):
    try: return float(x)
    except: return 0.0

def fetch_race(date_str, course, race_no):
    """Fetch odds using the EXACT original whitelisted query (verbatim)."""
    payload = {"operationName": "racing",
               "variables": {"date": date_str, "venueCode": course,
                             "raceNo": race_no, "oddsTypes": ["WIN", "PLA"]},
               "query": RACING_QUERY}
    r = requests.post(API, headers=HEADERS, json=payload, timeout=20)
    j = r.json()
    meetings = j.get("data", {}).get("raceMeetings", []) or []
    for m in meetings:
        if m.get("pmPools"):
            return m["pmPools"]
    return []

def fetch_turnover(date_str, course):
    """Return {race_no: {'WIN': float, 'PLA': float, 'post': str}, 'total': float}.
    Uses HKJC's EXACT official query verbatim so it passes the whitelist.
    One call covers the whole meeting (all races) — includes postTime (開跑時間)."""
    out = {}
    payload = {"operationName": "raceMeetings",
               "variables": {"date": date_str, "venueCode": course},
               "query": TURNOVER_QUERY}
    r = requests.post(API, headers=HEADERS, json=payload, timeout=20)
    j = r.json()
    if isinstance(j, dict) and j.get("errors"):
        return {}
    meetings = (j.get("data", {}) or {}).get("raceMeetings", []) or []
    for m in meetings:
        out["total"] = _to_float(m.get("totalInvestment"))
        # postTime per race (races[].no / races[].postTime)
        for rc in (m.get("races") or []):
            rno = rc.get("no")
            pt = rc.get("postTime")
            if rno is not None and pt:
                out.setdefault(int(rno), {})["post"] = pt
        for p in (m.get("poolInvs") or []):
            otype = p.get("oddsType")
            if otype not in ("WIN", "PLA"):
                continue
            inv = _to_float(p.get("investment"))
            races = (p.get("leg") or {}).get("races") or []
            for rno in races:
                out.setdefault(int(rno), {})[otype] = inv
    return out

def parse_post_time(pt_str):
    """HKJC postTime is ISO-ish, e.g. '2026-06-10T14:46:00+08:00'."""
    if not pt_str:
        return None
    try:
        s = pt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=HKT)
        return dt.astimezone(HKT)
    except Exception:
        return None

def pools_to_df(pools):
    rows = []
    for p in pools:
        pool_type = p.get("oddsType")
        for n in p.get("oddsNodes", []):
            rows.append({"池": pool_type,
                         "馬號": n.get("combString"),
                         "賠率": _to_float(n.get("oddsValue")),
                         "大熱": bool(n.get("hotFavourite"))})
    return pd.DataFrame(rows)

# ════════════════════════════════════════════════════════════
#  ENRICH + HISTORY
# ════════════════════════════════════════════════════════════
def minutes_to_post(now_hkt, post_time):
    """Negative minutes before post; 0 at post. None if unknown."""
    if post_time is None:
        return None
    delta = (post_time - now_hkt).total_seconds() / 60.0
    return -max(0.0, delta) if delta >= 0 else delta  # keep sign; negative = before post

def enrich(df, S):
    now_hkt = datetime.now(HKT)
    mtp = minutes_to_post(now_hkt, S["post_time"])

    open_arr, live_arr, chg_arr, dir_arr = [], [], [], []
    new_last = {}
    for _, r in df.iterrows():
        key = (r["池"], r["馬號"])
        curr = r["賠率"]
        new_last[key] = curr

        # open odds (locked once)
        if key not in S["open_odds"] and curr > 0:
            S["open_odds"][key] = curr
        opn = S["open_odds"].get(key, curr)

        # % change vs open — sign follows odds movement:
        #   negative = odds dropped (落飛), positive = odds rose (回飛)
        if opn and opn > 0:
            pct = (curr - opn) / opn * 100.0
        else:
            pct = 0.0

        if abs(pct) <= FLAT_THRESHOLD:
            d = "flat"
        elif pct < 0:
            d = "down"   # odds dropped => 落飛
        else:
            d = "up"     # odds rose => 回飛

        open_arr.append(opn); live_arr.append(curr)
        chg_arr.append(pct); dir_arr.append(d)

        # Always store (wall_clock_epoch, odds). Display-X is derived at render
        # time from current mode, so switching to countdown never corrupts old points.
        if curr > 0:
            S["series"][key].append((now_hkt.timestamp(), curr))

    S["last_odds"] = new_last
    df["開賠"] = open_arr
    df["即場"] = live_arr
    df["變化"] = chg_arr
    df["方向"] = dir_arr
    return df, mtp

def record_into_state(pools, S):
    """Lightweight background recorder — appends odds to a race's series & open_odds.
    Used for the non-displayed races in the sliding window."""
    if not pools:
        return
    now_hkt = datetime.now(HKT)
    for p in pools:
        pool_type = p.get("oddsType")
        for n in p.get("oddsNodes", []):
            horse = n.get("combString")
            curr = _to_float(n.get("oddsValue"))
            if curr <= 0:
                continue
            key = (pool_type, horse)
            if key not in S["open_odds"]:
                S["open_odds"][key] = curr
            S["series"][key].append((now_hkt.timestamp(), curr))

def recent_speed(S, key, seconds=30):
    """Decay-weighted recent % move magnitude over last N sec — for ranking & plunge."""
    series = S["series"][key]
    if len(series) < 2:
        return 0.0, 0.0
    last_ts, last_v = series[-1]   # series stores (epoch_seconds, odds)
    cutoff = last_ts - seconds     # epoch seconds
    base_v = None
    for ts, v in series:
        if ts <= cutoff:
            base_v = v
    if base_v is None:
        base_v = series[0][1]
    if base_v <= 0:
        return 0.0, 0.0
    pct = (base_v - last_v) / base_v * 100.0  # positive = dropped
    return pct, abs(pct)

def record_stakes(df_pool, pool_name, pool_inv, S):
    """Record each horse's current stake ($ = live-share x pool_inv) into stake_hist,
    timestamped by wall-clock epoch seconds. Only when pool_inv is known."""
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df_pool[df_pool["即場"] > 0].copy()
    if sub.empty:
        return
    inv_live = 1.0 / sub["即場"]
    shares = inv_live / inv_live.sum()
    ts = datetime.now(HKT).timestamp()
    for (_, r), sh in zip(sub.iterrows(), shares):
        key = (pool_name, r["馬號"])
        S["stake_hist"][key].append((ts, sh * pool_inv))

def recent_stake_gain(S, pool_name, horse, seconds=30):
    """Return $ increase in this horse's stake over the last N seconds."""
    hist = S["stake_hist"][(pool_name, horse)]
    if len(hist) < 2:
        return 0.0
    last_ts, last_v = hist[-1]
    cutoff = last_ts - seconds
    base_v = None
    for ts, v in hist:
        if ts <= cutoff:
            base_v = v
    if base_v is None:
        base_v = hist[0][1]
    return last_v - base_v   # positive = stake grew

def stake_change_since_open(S, pool_name, horse):
    """Method B: $ change in this horse's stake since first recorded point.
    Uses stake_hist so each timepoint used its own real pool total (no mixing)."""
    hist = S["stake_hist"][(pool_name, horse)]
    if len(hist) < 2:
        return 0.0
    return hist[-1][1] - hist[0][1]   # live stake - first-seen stake

# ════════════════════════════════════════════════════════════
#  RENDER HELPERS
# ════════════════════════════════════════════════════════════
def spark_svg(series, color, faint, countdown=True):
    """Inline SVG sparkline using viewBox 0-100 so it always fills its column.
    countdown=True: x axis fixed -14min -> 0 (post), with countdown gridlines.
    countdown=False: x axis auto-ranges to data extent (elapsed time)."""
    if not series:
        return '<svg viewBox="0 0 100 22" preserveAspectRatio="none" width="100%" height="22"></svg>'

    if countdown:
        x_min, x_max = -AXIS_MINUTES, 0.0
        gridlines = (-10, -6, -2)
    else:
        xs = [x for x, _ in series]
        x_min, x_max = min(xs), max(xs)
        if x_max - x_min < 0.01:
            x_max = x_min + 1.0   # avoid zero span before data accumulates
        gridlines = ()

    ys = [v for _, v in series]
    ymin, ymax = min(ys), max(ys)
    rng = (ymax - ymin) or 1.0
    span = (x_max - x_min) or 1.0

    def px(x):
        xx = max(x_min, min(x_max, x))
        return (xx - x_min) / span * 100.0

    def py(v):
        return 22 - (v - ymin) / rng * (22 - 4) - 2

    pts = " ".join(f"{px(x):.2f},{py(v):.1f}" for x, v in series)
    lx, lv = series[-1]
    dot = "" if faint else f'<circle cx="{px(lx):.2f}" cy="{py(lv):.1f}" r="1.8" fill="{color}" vector-effect="non-scaling-stroke"/>'
    grid = "".join(
        f'<line x1="{px(g):.2f}" y1="0" x2="{px(g):.2f}" y2="22" stroke="rgba(128,128,128,0.13)" stroke-width="0.4"/>'
        for g in gridlines
    )
    dash = ' stroke-dasharray="2,1.5"' if faint else ""
    sw = 0.9 if faint else 1.4
    op = 0.45 if faint else 1.0
    return (f'<svg viewBox="0 0 100 22" preserveAspectRatio="none" width="100%" height="22" style="display:block;">'
            f'{grid}<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'opacity="{op}"{dash} vector-effect="non-scaling-stroke"/>{dot}</svg>')

def dir_color(d):
    return {"down": INFO, "up": DANGER, "flat": MUTE}[d]

def series_to_x(series, S, countdown):
    """Convert stored (epoch, odds) points to (display_x_min, odds).
    countdown=True: x = minutes-to-post (negative before post, 0 at post).
    countdown=False: x = minutes since first recorded point (elapsed)."""
    if not series:
        return []
    post = S.get("post_time")
    if countdown and post is not None:
        post_ts = post.timestamp()
        return [((ts - post_ts) / 60.0, v) for ts, v in series]  # negative before post
    # elapsed: relative to first point
    t0 = series[0][0]
    return [((ts - t0) / 60.0, v) for ts, v in series]

def trend_panel(df_pool, S, pool_title, countdown=True):
    rows_html = []
    sub = df_pool.sort_values("馬號", key=lambda s: pd.to_numeric(s, errors="coerce"))
    for _, r in sub.iterrows():
        key = (r["池"], r["馬號"])
        d = r["方向"]
        col = dir_color(d)
        faint = (d == "flat")
        name_col = "var(--subtext)" if faint else "var(--text)"
        chg = r["變化"]
        sign = "+" if chg > 0 else ""
        chg_col = MUTE if faint else col
        hot = '<span class="flame">🔥</span>' if r.get("大熱") else ""
        series = series_to_x(list(S["series"][key]), S, countdown)
        spark = spark_svg(series, col, faint, countdown=countdown)
        rows_html.append(
            f'<div class="row">'
            f'<span class="c-no" style="color:{name_col}">{r["馬號"]}{hot}</span>'
            f'<span class="c-spark">{spark}</span>'
            f'<span class="c-num" style="color:var(--subtext)">{r["開賠"]:.1f}</span>'
            f'<span class="c-num" style="color:{name_col}">{r["即場"]:.1f}</span>'
            f'<span class="c-chg" style="color:{chg_col}">{sign}{chg:.0f}%</span>'
            f'</div>'
        )

    if countdown:
        axis_labels = ["-14分", "-10分", "-6分", "-2分", "開跑"]
        sub_label = "全場・對齊開跑倒數軸"
        head_label = "走勢（-14分 → 開跑）"
    else:
        axis_labels = ["開機", "", "經過時間", "", "現在"]
        sub_label = "全場・開機後經過時間"
        head_label = "走勢（開機 → 現在）"
    axis = "".join(f"<span>{t}</span>" for t in axis_labels)
    html = (
        f'<div class="panel">'
        f'<div class="panel-title">{pool_title}</div>'
        f'<div class="panel-sub">{sub_label}</div>'
        f'<div class="thead">'
        f'<span class="c-no">馬號</span>'
        f'<span class="c-spark">{head_label}</span>'
        f'<span class="c-num">開賠</span>'
        f'<span class="c-num">即場</span>'
        f'<span class="c-chg">變化</span>'
        f'</div>'
        f'{"".join(rows_html)}'
        f'<div class="axis"><span class="c-no"></span>'
        f'<span class="axis-inner">{axis}</span>'
        f'<span class="c-num"></span><span class="c-num"></span><span class="c-chg"></span></div>'
        f'<div class="legend">'
        f'<span><i style="background:{INFO}"></i>落飛（有錢入）</span>'
        f'<span><i style="background:{DANGER}"></i>回飛（資金離場）</span>'
        f'<span><i style="background:{MUTE}"></i>平穩</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def rank_panel(df_pool, S, want_down, title, sub):
    """want_down=True -> 落飛榜 (blue, odds dropped, pct<0);
       want_down=False -> 回飛榜 (red, odds rose, pct>0)."""
    color = INFO if want_down else DANGER
    items = []
    for _, r in df_pool.iterrows():
        pct = r["變化"]  # negative = 落飛, positive = 回飛
        if want_down and pct < -FLAT_THRESHOLD:
            items.append((r["馬號"], abs(pct)))   # store magnitude
        elif (not want_down) and pct > FLAT_THRESHOLD:
            items.append((r["馬號"], abs(pct)))
    items.sort(key=lambda t: t[1], reverse=True)
    items = items[:6]
    maxv = items[0][1] if items else 1.0

    rows = []
    for no, v in items:
        w = max(4, v / maxv * 100)
        sign = "-" if want_down else "+"
        rows.append(
            f'<div class="rank-row"><div class="rank-head">'
            f'<span style="font-size:12px;font-weight:600">{no} 號</span>'
            f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;color:{color}">{sign}{v:.0f}%</span>'
            f'</div><div class="rank-bar"><div class="rank-fill" style="width:{w:.0f}%;background:{color}"></div></div></div>'
        )
    if not rows:
        rows = ['<div style="font-size:11px;color:var(--muted);padding:8px 0">暫無</div>']
    icon = "📉" if want_down else "📈"
    html = (f'<div class="panel">'
            f'<div class="panel-title">{icon} {title}</div>'
            f'<div class="panel-sub">{sub}</div>{"".join(rows)}</div>')
    st.markdown(html, unsafe_allow_html=True)

def divergence_panel(df):
    """Win moving but Place not (or vice versa)."""
    win = df[df["池"] == "WIN"].set_index("馬號")
    pla = df[df["池"] == "PLA"].set_index("馬號")
    rows = []
    for no in win.index:
        if no not in pla.index:
            continue
        wc = win.loc[no, "變化"]
        pc = pla.loc[no, "變化"]
        w_move = abs(wc) > FLAT_THRESHOLD
        p_move = abs(pc) > FLAT_THRESHOLD
        if w_move and not p_move:
            note = "只得獨贏有錢 — 博贏"
            wlabel = f'獨贏 {wc:+.0f}%'
            rows.append((no, wlabel, "位置 無變", note))
        elif p_move and not w_move:
            note = "只得位置有錢 — 博位置／each-way"
            plabel = f'位置 {pc:+.0f}%'
            rows.append((no, "獨贏 無變", plabel, note))

    body = []
    for no, wl, pl, note in rows[:8]:
        body.append(
            f'<div class="div-row">'
            f'<span style="width:38px;font-weight:600">{no} 號</span>'
            f'<span class="pill pill-win">{wl}</span>'
            f'<span class="pill pill-mute">{pl}</span>'
            f'<span style="color:var(--subtext)">{note}</span>'
            f'</div>'
        )
    if not body:
        body = ['<div style="font-size:11px;color:var(--muted);padding:8px 0">暫無背馳</div>']
    html = (f'<div class="panel"><div class="panel-title">獨贏 / 位置 背馳</div>'
            f'<div class="panel-sub">只得一個池有錢、另一個唔郁</div>{"".join(body)}</div>')
    st.markdown(html, unsafe_allow_html=True)

def _fmt_money(v):
    """Format HKD compactly: $1.23M / $456K / $789."""
    if v is None or v <= 0:
        return "—"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"

def compute_scores(sub, S, pool_name, overround, other_pool_drops):
    """Compute a 0-100 composite score per horse from independent signals.
    Returns dict: 馬號 -> {surge, drop, water, agree, total}.
    - surge (40): recent drop relative to field (how much it stands out now)
    - drop  (30): absolute recent drop % level
    - water (20): market maturity (overround near WATER_IDEAL)
    - agree (10): both WIN & PLA dropping (cross-pool confirmation)
    """
    scores = {}
    drops = sub["近30跌"].tolist()
    max_drop = max(drops) if drops else 0.0
    # water score is same for whole pool (depends on overround)
    if overround <= WATER_IDEAL:
        water = SCORE_WATER_MAX
    elif overround >= WATER_LOOSE:
        water = 0.0
    else:
        # linear between ideal (full) and loose (zero)
        water = SCORE_WATER_MAX * (WATER_LOOSE - overround) / (WATER_LOOSE - WATER_IDEAL)

    for _, r in sub.iterrows():
        horse = r["馬號"]
        d = r["近30跌"]
        # surge (40): how this horse's drop compares to the strongest in field
        surge = SCORE_SURGE_MAX * (d / max_drop) if max_drop > 0.5 and d > 0 else 0.0
        # drop (30): absolute level, capped at SCORE_DROP_FULL %
        drop = SCORE_DROP_MAX * min(1.0, d / SCORE_DROP_FULL) if d > 0 else 0.0
        # agree (10): other pool for same horse also dropping (>= SURGE_MIN_DROP)
        other_d = other_pool_drops.get(horse, 0.0)
        agree = SCORE_AGREE_MAX if (d >= SURGE_MIN_DROP and other_d >= SURGE_MIN_DROP) else 0.0
        total = surge + drop + water + agree
        scores[horse] = {"surge": surge, "drop": drop, "water": water,
                         "agree": agree, "total": total}
    return scores

def moneyflow_panel(df_pool, pool_name, pool_inv=None, S=None, mtp=None, other_pool_drops=None):
    """Method A money flow + visual surge detection.
    Adds: stake bar, ▲/🔥 surge highlight, surge-first sorting, 近30秒流入$.
    'Surge' is detected purely by $ inflow in the last 30s (Method A, no time gate)."""
    sub = df_pool[(df_pool["即場"] > 0) & (df_pool["開賠"] > 0)].copy()
    if sub.empty:
        st.markdown(
            f'<div class="panel"><div class="panel-title">💰 {pool_name}資金流向</div>'
            f'<div class="panel-sub">暫無資料</div></div>', unsafe_allow_html=True)
        return

    # pool_name is the display name (獨贏/位置); the series is keyed by the
    # pool CODE (WIN/PLA) held in the 池 column. Use the code for lookups.
    pool_code = str(df_pool["池"].iloc[0]) if len(df_pool) else pool_name

    inv_live = 1.0 / sub["即場"]
    inv_open = 1.0 / sub["開賠"]
    sub["即場佔比"] = inv_live / inv_live.sum() * 100.0
    sub["開賠佔比"] = inv_open / inv_open.sum() * 100.0

    # Market overround = Σ(1/odds). For WIN (1 winner) the fair baseline ~1.0.
    # For PLACE there are multiple winning positions (usually 3, or 2 for small
    # fields), so Σ(1/odds) naturally sums to ~n_place. Normalise by n_place so
    # both pools compare against the same ~1.2 maturity baseline.
    raw_overround = float(inv_live.sum())
    if pool_code == "PLA":
        n_runners = len(sub)
        n_place = 2 if n_runners < 7 else 3   # HKJC: <7 runners -> 2 places
        overround = raw_overround / n_place
    else:
        overround = raw_overround
    implied_takeout = max(0.0, (1.0 - 1.0 / overround) * 100.0) if overround > 0 else 0.0

    have_money = pool_inv is not None and pool_inv > 0
    if have_money:
        # 佔比法 (self-normalising) — proven most accurate as it absorbs the
        # market's real takeout via Σ-normalisation (see derivation doc).
        sub["投注額"] = sub["即場佔比"] / 100.0 * pool_inv
        # Method B: true absolute stake change since first recorded point.
        sub["投注變化"] = [stake_change_since_open(S, pool_code, no) if S is not None else 0.0
                          for no in sub["馬號"]]
    else:
        # no pool money -> fall back to relative share change for direction only
        sub["投注變化"] = sub["即場佔比"] - sub["開賠佔比"]

    # ── recent odds-drop surge (近30秒賠率跌幅 %) — always-available signal ──
    #   >= SURGE_BIG_DROP -> 🔥 大量湧入（level 2）
    #   >= SURGE_MIN_DROP -> ▲ 急跌（level 1）
    drops, surges = [], []
    for _, r in sub.iterrows():
        if S is not None:
            drop_pct, _ = recent_speed(S, (pool_code, r["馬號"]), SURGE_WINDOW)  # +ve = dropped
        else:
            drop_pct = 0.0
        drops.append(drop_pct)
        if drop_pct >= SURGE_BIG_DROP:
            surges.append(2)
        elif drop_pct >= SURGE_MIN_DROP:
            surges.append(1)
        else:
            surges.append(0)
    sub["近30跌"] = drops
    sub["surge"] = surges

    # ── 綜合評分（100 分制）──
    other_drops = other_pool_drops or {}
    score_map = compute_scores(sub, S, pool_name, overround, other_drops)
    sub["s_surge"] = [score_map[h]["surge"] for h in sub["馬號"]]
    sub["s_drop"] = [score_map[h]["drop"] for h in sub["馬號"]]
    sub["s_water"] = [score_map[h]["water"] for h in sub["馬號"]]
    sub["s_agree"] = [score_map[h]["agree"] for h in sub["馬號"]]
    sub["s_total"] = [score_map[h]["total"] for h in sub["馬號"]]

    max_stake = sub["投注額"].max() if have_money else 0

    # ── sort: by total score (highest confluence first) ──
    sub = sub.sort_values(["s_total", "近30跌"], ascending=[False, False])

    rows = []
    for _, r in sub.iterrows():
        chg = r["投注變化"]   # Method B: dollars (or % fallback if no money)
        # direction threshold: $ mode uses a small $ floor; % mode uses 0.3
        thresh = 1000.0 if have_money else 0.3
        col = INFO if chg > thresh else (DANGER if chg < -thresh else MUTE)
        surge = int(r["surge"])
        faint = (surge == 0 and abs(chg) <= thresh)   # idle -> dim
        name_col = "var(--muted)" if faint else "var(--text)"

        # surge marker
        if surge == 2:
            marker = '<span style="color:#ff5757">🔥</span>'
        elif surge == 1:
            marker = '<span style="color:#ff5757">▲</span>'
        else:
            marker = ""

        # stake bar (width relative to biggest stake)
        if have_money and max_stake > 0:
            w = max(2, r["投注額"] / max_stake * 100)
            bar_col = "#ff5757" if surge == 2 else ("#e0a83c" if surge == 1 else INFO)
            bar = (f'<span class="stake-barwrap"><span class="stake-bar" '
                   f'style="width:{w:.0f}%;background:{bar_col};opacity:{0.45 if faint else 0.9}"></span></span>')
        else:
            bar = f'<span class="c-spark" style="color:var(--subtext);font-size:11px">{r["即場佔比"]:.1f}%</span>'

        # recent drop cell — shows odds drop % in last 30s (always available)
        drop = r["近30跌"]
        if drop >= SURGE_BIG_DROP:
            gain_cell = f'<span class="c-num" style="color:#ff5757;font-weight:700">🔥↓{drop:.1f}%</span>'
        elif drop >= SURGE_MIN_DROP:
            gain_cell = f'<span class="c-num" style="color:#e0a83c;font-weight:600">▲↓{drop:.1f}%</span>'
        elif drop > 0.5:
            gain_cell = f'<span class="c-num" style="color:var(--subtext)">↓{drop:.1f}%</span>'
        else:
            gain_cell = '<span class="c-num" style="color:var(--muted)">—</span>'

        money_cell = (f'<span class="c-num" style="color:#e0a83c;font-weight:600">{_fmt_money(r["投注額"])}</span>'
                      if have_money else "")

        # 流向 cell — Method B shows $ change vs open; fallback shows %
        if have_money:
            flow_sign = "+" if chg > 0 else ("-" if chg < 0 else "")
            flow_cell = f'<span class="c-chg" style="color:{col}">{flow_sign}{_fmt_money(abs(chg))}</span>'
        else:
            flow_sign = "+" if chg > 0.3 else ""
            flow_cell = f'<span class="c-chg" style="color:{col}">{flow_sign}{chg:.1f}%</span>'

        # ── 分數欄 ──
        total = r["s_total"]
        # total colour: high=red hot, mid=gold, low=grey
        if total >= 70:
            tcol, tweight = "#ff5757", "700"
        elif total >= 40:
            tcol, tweight = "#e0a83c", "600"
        else:
            tcol, tweight = "var(--muted)", "400"
        breakdown = (f'<span style="font-size:9px;color:var(--muted)">'
                     f'升{r["s_surge"]:.0f}/跌{r["s_drop"]:.0f}/水{r["s_water"]:.0f}/合{r["s_agree"]:.0f}</span>')
        score_cell = (f'<span class="c-score">{breakdown} '
                      f'<b style="color:{tcol};font-weight:{tweight};font-size:13px">{total:.0f}</b></span>')

        row_cls = "row surge-gate" if surge == 2 else "row"
        rows.append(
            f'<div class="{row_cls}">'
            f'<span class="c-no" style="color:{name_col}">{r["馬號"]}{marker}</span>'
            f'{money_cell}'
            f'{gain_cell}'
            f'{flow_cell}'
            f'{score_cell}'
            f'</div>'
        )

    # water maturity light: green<1.25, yellow 1.25-1.35, red>1.35
    if overround < 1.25:
        wlight, wtext = "#22c55e", "賠率成熟·可信"
    elif overround <= 1.35:
        wlight, wtext = "#e0a83c", "接近成熟"
    else:
        wlight, wtext = "#ff5757", "未成熟·審慎"
    water_badge = (f'<span style="color:{wlight}">●</span> 水位 {overround:.2f}（{wtext}）')

    accuracy_note = "獨贏反推準確" if pool_name == "獨贏" else "位置為近似（多位分享）"
    if have_money:
        sub_label = (f'彩池總額 {_fmt_money(pool_inv)}　·　{water_badge}　·　{accuracy_note}<br>'
                     f'分數＝升(40)+跌(30)+水(20)+合(10)　·　'
                     f'🔥近30秒賠率跌≥{SURGE_BIG_DROP:.0f}%　▲≥{SURGE_MIN_DROP:.0f}%')
        head = ('<span class="c-no">馬號</span>'
                '<span class="c-num">投注額</span>'
                '<span class="c-num">近30跌</span>'
                '<span class="c-chg">流向$</span>'
                '<span class="c-score">分項 / 總分</span>')
    else:
        sub_label = (f'用賠率反推佔比（彩池金額未取得）　·　{water_badge}<br>'
                     f'分數＝升(40)+跌(30)+水(20)+合(10)')
        head = ('<span class="c-no">馬號</span>'
                '<span class="c-num">即場佔比</span>'
                '<span class="c-num">近30跌</span>'
                '<span class="c-score">分項 / 總分</span>')

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">💰 {pool_name}資金流向</div>'
        f'<div class="panel-sub">{sub_label}</div>'
        f'<div class="thead">{head}</div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ff5757"></i>🔥賠率大跌 / ▲急跌（近30秒）</span>'
        f'<span><i style="background:{INFO}"></i>資金流入</span>'
        f'<span><i style="background:{MUTE}"></i>靜止</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def stake_bar_chart(df_pool, pool_name, pool_inv, S):
    """Horizontal bar chart: each horse's stake ($ = live-share × pool_inv).
    Bars sorted by stake (largest first). Hot money (recent odds drop) tints the
    bar and shows the inflow $; horses that EVER surged keep a small 🔥 memo."""
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df_pool[df_pool["即場"] > 0].copy()
    if sub.empty:
        return
    pool_code = str(df_pool["池"].iloc[0]) if len(df_pool) else pool_name

    inv_live = 1.0 / sub["即場"]
    sub["即場佔比"] = inv_live / inv_live.sum() * 100.0
    sub["投注額"] = sub["即場佔比"] / 100.0 * pool_inv

    # recent drop% + recent $ inflow (30s) per horse
    drops, inflows = [], []
    for _, r in sub.iterrows():
        dp, _ = recent_speed(S, (pool_code, r["馬號"]), SURGE_WINDOW) if S is not None else (0.0, 0.0)
        drops.append(dp)
        inflows.append(recent_stake_gain(S, pool_code, r["馬號"], SURGE_WINDOW) if S is not None else 0.0)
        # remember peak drop ever (🔥 memory)
        if S is not None and dp > 0:
            key = (pool_code, r["馬號"])
            prev = S["ever_surged"].get(key, 0.0)
            if dp > prev:
                S["ever_surged"][key] = dp
    sub["近30跌"] = drops
    sub["近30入"] = inflows

    sub = sub.sort_values("投注額", ascending=False)
    max_stake = sub["投注額"].max()

    rows = []
    for _, r in sub.iterrows():
        horse = r["馬號"]
        stake = r["投注額"]
        drop = r["近30跌"]
        inflow = r["近30入"]
        w = max(2, stake / max_stake * 100) if max_stake > 0 else 2

        hot_now = drop >= SURGE_MIN_DROP
        big_now = drop >= SURGE_BIG_DROP
        ever = S["ever_surged"].get((pool_code, horse), 0.0) if S is not None else 0.0

        bar_col = "#ff5757" if big_now else ("#e0a83c" if hot_now else INFO)
        # overlay label: show inflow $ when hot, else stake
        if hot_now and inflow > 0:
            overlay = f'🔥+{_fmt_money(inflow)}' if big_now else f'▲+{_fmt_money(inflow)}'
            overlay_col = "#fff"
        else:
            overlay = ""
        # ever-surged memo (small flame kept even after it cools)
        memo = ''
        if ever >= SURGE_BIG_DROP and not big_now:
            memo = f'<span style="color:#ff5757;font-size:9px" title="曾大跌{ever:.0f}%">🔥</span>'
        elif ever >= SURGE_MIN_DROP and not hot_now:
            memo = f'<span style="color:#e0a83c;font-size:9px" title="曾急跌{ever:.0f}%">▲</span>'

        rows.append(
            f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0">'
            f'<span style="width:34px;flex:none;font-size:12px;font-weight:600;'
            f'color:var(--text);font-family:JetBrains Mono,monospace">{horse}{memo}</span>'
            f'<span style="flex:1;min-width:0;height:16px;background:rgba(255,255,255,0.05);'
            f'border-radius:4px;overflow:hidden;display:flex;align-items:center;position:relative">'
            f'<span style="display:block;height:100%;width:{w:.1f}%;background:{bar_col};'
            f'opacity:0.85;border-radius:4px"></span>'
            f'<span style="position:absolute;left:6px;font-size:9px;color:#fff;'
            f'font-weight:600;text-shadow:0 0 3px rgba(0,0,0,0.8)">{overlay}</span>'
            f'</span>'
            f'<span style="width:56px;flex:none;text-align:right;font-size:11px;'
            f'color:#e0a83c;font-weight:600;font-family:JetBrains Mono,monospace">'
            f'{_fmt_money(stake)}</span>'
            f'</div>'
        )

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">📊 {pool_name}投注額棒型圖</div>'
        f'<div class="panel-sub">棒長＝投注額（大到小排）· 熱錢流入時棒變色並標流入金額 · '
        f'🔥/▲＝曾經爆過（記錄）</div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ff5757"></i>🔥大量湧入</span>'
        f'<span><i style="background:#e0a83c"></i>▲熱錢流入</span>'
        f'<span><i style="background:{INFO}"></i>正常</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════
#  HEADER + CONTROLS
# ════════════════════════════════════════════════════════════
st.markdown(
    f'<div class="hdr"><div class="hdr-title">🐎 {APP_NAME}</div>'
    f'<span class="live"><span class="live-dot"></span>實時 · 5秒</span></div>',
    unsafe_allow_html=True)

c1, c2, c3, c4, c5 = st.columns([1.8, 1.2, 1, 1.4, 1.4])
with c1:
    race_date = st.date_input("日期", date.today())
with c2:
    course = st.selectbox("場地", ["ST", "HV"],
                          format_func=lambda x: "沙田 ST" if x == "ST" else "跑馬地 HV")
with c3:
    race_no = st.number_input("場次", 1, 14, 1)
with c4:
    post_input = st.text_input("開跑時間 (可選)", value="", placeholder="例 14:46",
                               help="留空即用「開機後經過時間」做軸。若想對齊開跑倒數，可手動填 HH:MM。")
with c5:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    reset_clicked = st.button("🔄 重設此場走勢", use_container_width=True)

# Get this race's own state (creates it if first visit; keeps existing on return)
race_key = f"{race_date}|{course}|{race_no}"
if reset_clicked:
    S = reset_state(race_key)
else:
    S = get_state(race_key)

# Manual override (optional). If filled, it wins over auto post time.
manual_post = None
if post_input.strip():
    try:
        hh, mm = post_input.strip().split(":")
        manual_post = datetime.now(HKT).replace(hour=int(hh), minute=int(mm),
                                                second=0, microsecond=0)
    except Exception:
        manual_post = None


# ════════════════════════════════════════════════════════════
#  FETCH + PROCESS
# ════════════════════════════════════════════════════════════
# Sliding window: current race + next 2 (capped at race 14)
window = [n for n in range(int(race_no), int(race_no) + 3) if 1 <= n <= 14]

# Fetch + record the *background* races first (not the current one).
# Small spacing between calls avoids hammering HKJC.
for bg_no in window:
    if bg_no == int(race_no):
        continue
    bg_key = f"{race_date}|{course}|{bg_no}"
    bg_state = get_state(bg_key)
    try:
        bg_pools = fetch_race(str(race_date), course, bg_no)
        record_into_state(bg_pools, bg_state)
    except Exception:
        pass
    _time.sleep(0.15)

# Now fetch the current (displayed) race
try:
    pools = fetch_race(str(race_date), course, int(race_no))
except Exception as e:
    st.error(f"⚠️ 連線失敗：{e}")
    pools = []

# Pool turnover (彩池金額) — cached, refreshed at most every 20s so it never
# slows the 5s odds cycle. If it fails, money flow falls back to percentage.
if "turnover_cache" not in st.session_state:
    st.session_state.turnover_cache = {}
    st.session_state.turnover_cache_ts = None
    st.session_state.turnover_cache_key = None
_tkey = f"{race_date}|{course}"
_tnow = datetime.now(HKT)
_need = (st.session_state.turnover_cache_key != _tkey
         or st.session_state.turnover_cache_ts is None
         or (_tnow - st.session_state.turnover_cache_ts).total_seconds() >= 20)
if _need:
    try:
        st.session_state.turnover_cache = fetch_turnover(str(race_date), course)
    except Exception:
        st.session_state.turnover_cache = {}
    st.session_state.turnover_cache_ts = _tnow
    st.session_state.turnover_cache_key = _tkey
turnover_map = st.session_state.turnover_cache or {}
this_inv = turnover_map.get(int(race_no), {})
win_inv = this_inv.get("WIN")
pla_inv = this_inv.get("PLA")

# Post time: AUTO DISABLED (實時開跑時間唔穩定). Manual override only.
# If manual is empty -> post_time None -> elapsed-time axis (no double-line risk).
S["post_time"] = manual_post

df = pools_to_df(pools)

if df.empty:
    st.markdown(
        '<div class="panel empty"><div style="font-size:2rem">🏁</div>'
        '<div style="font-size:1rem;margin-top:6px">暫時未有即時賠率</div>'
        '<div style="font-size:12px;color:var(--muted);margin-top:4px">'
        '可能彩池未開、賽事已完、或暫時無法取得</div></div>',
        unsafe_allow_html=True)
    with st.expander("🔧 診斷 — 查看 API 原始回應"):
        try:
            dbg_payload = {"operationName": "racing",
                           "variables": {"date": str(race_date), "venueCode": course,
                                         "raceNo": int(race_no), "oddsTypes": ["WIN", "PLA"]},
                           "query": RACING_QUERY}
            dbg = requests.post(API, headers=HEADERS, json=dbg_payload, timeout=20)
            st.write(f"HTTP status: {dbg.status_code}")
            st.json(dbg.json())
        except Exception as e:
            st.write(f"Request error: {e}")
else:
    df, mtp = enrich(df, S)

    # Record current stake ($) per horse for surge detection (only when pool known)
    record_stakes(df[df["池"] == "WIN"], "WIN", win_inv, S)
    record_stakes(df[df["池"] == "PLA"], "PLA", pla_inv, S)

    # ── post-time / countdown status line ──
    if S["post_time"] is not None and mtp is not None:
        src = "手動" if manual_post is not None else "自動抓取"
        if mtp < 0:
            secs_left = abs(mtp) * 60
            if secs_left <= 60:
                cd = f"距離開跑 {secs_left:.0f} 秒　🔥入閘窗"
            else:
                cd = f"距離開跑 {abs(mtp):.0f} 分鐘"
        else:
            cd = "已開跑 / 封盤"
        pt_label = S["post_time"].strftime("%H:%M")
        st.markdown(
            f'<div style="font-family:JetBrains Mono,monospace;font-size:11px;color:var(--subtext);'
            f'margin-bottom:8px">開跑時間 {pt_label}（{src}）· {cd} · 實時跟蹤中</div>',
            unsafe_allow_html=True)
    else:
        elapsed = ""
        if S["started_at"]:
            mins = (datetime.now(HKT) - S["started_at"]).total_seconds() / 60.0
            elapsed = f" · 已監察 {mins:.0f} 分鐘"
        st.markdown(
            f'<div style="font-family:JetBrains Mono,monospace;font-size:11px;color:var(--subtext);'
            f'margin-bottom:8px">時間軸：開機後經過時間（左＝開機，右＝現在）{elapsed} · '
            f'如需對齊開跑倒數，可在上方填開跑時間</div>',
            unsafe_allow_html=True)

    # ── sliding-window / multi-race tracking indicator ──
    win_label = "、".join(f"R{n}" for n in window)
    tracked = [k for k, s in st.session_state.RACES.items() if len(s["series"]) > 0]
    parts_list = []
    for k in tracked:
        parts = k.split("|")
        try:
            rno = int(parts[2])
        except Exception:
            continue
        label = f"R{rno}"
        if k == race_key:
            label = f"<b style='color:var(--text)'>{label}</b>"
        parts_list.append((rno, label))
    parts_list.sort(key=lambda t: t[0])
    tracked_str = "、".join(lbl for _, lbl in parts_list) if parts_list else "—"
    st.markdown(
        f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:var(--muted);'
        f'margin-bottom:8px">背景視窗（現正記錄）：{win_label} · '
        f'已累積數據場次：{tracked_str} · 切換場次唔會清走數據</div>',
        unsafe_allow_html=True)

    # ── plunge alerts (recent fast drops) ──
    alerts = []
    win_df_full = df[df["池"] == "WIN"]
    for _, r in win_df_full.iterrows():
        key = (r["池"], r["馬號"])
        pct, mag = recent_speed(S, key, PLUNGE_WINDOW)
        if pct >= PLUNGE_PCT:
            alerts.append((r["馬號"], pct, r["即場"]))
    alerts.sort(key=lambda t: t[1], reverse=True)
    for no, pct, odds in alerts[:3]:
        st.markdown(
            f'<div class="alert-bar">⚠️ '
            f'<b>插水警示</b>　{no} 號於 {PLUNGE_WINDOW} 秒內急跌 {pct:.0f}%，即場 {odds:.1f}</div>',
            unsafe_allow_html=True)

    # ── per-race pool turnover banner ──
    race_total = (win_inv or 0) + (pla_inv or 0)
    if win_inv or pla_inv:
        def _tcard(label, val, accent):
            return (f'<div style="flex:1;background:var(--card);border:1px solid var(--border);'
                    f'border-radius:10px;padding:8px 14px;position:relative;overflow:hidden">'
                    f'<div style="position:absolute;top:0;left:0;right:0;height:2px;background:{accent}"></div>'
                    f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:var(--subtext);'
                    f'letter-spacing:0.08em">{label}</div>'
                    f'<div style="font-size:20px;font-weight:600;color:var(--text);margin-top:2px">'
                    f'{_fmt_money(val)}</div></div>')
        st.markdown(
            f'<div style="display:flex;gap:10px;margin-bottom:10px">'
            f'{_tcard("獨贏池 WIN", win_inv, INFO)}'
            f'{_tcard("位置池 PLA", pla_inv, "#3b82f6")}'
            f'{_tcard("本場合計 WIN+PLA", race_total, "#e0a83c")}'
            f'</div>',
            unsafe_allow_html=True)

    # ── main: win & place trend side by side ──
    use_countdown = S["post_time"] is not None
    col_w, col_p = st.columns(2)
    with col_w:
        trend_panel(df[df["池"] == "WIN"], S, "獨贏賠率走勢", countdown=use_countdown)
    with col_p:
        trend_panel(df[df["池"] == "PLA"], S, "位置賠率走勢", countdown=use_countdown)

    # ── ranking boards ──
    win_df = df[df["池"] == "WIN"]
    pla_df = df[df["池"] == "PLA"]
    rcol1, rcol2 = st.columns(2)
    with rcol1:
        rank_panel(win_df, S, True, "獨贏落飛榜 · 前六", "獨贏由開賠跌得最多（有錢入）")
    with rcol2:
        rank_panel(pla_df, S, True, "位置落飛榜 · 前六", "位置由開賠跌得最多（有錢入）")

    # ── divergence ──
    divergence_panel(df)

    # ── money flow (signal = 近30秒賠率跌幅 + 100分綜合評分) ──
    # Precompute each pool's recent drops so scoring can check cross-pool agreement.
    def _pool_drops(pool):
        out = {}
        for _, rr in df[df["池"] == pool].iterrows():
            dp, _ = recent_speed(S, (pool, rr["馬號"]), SURGE_WINDOW)
            out[rr["馬號"]] = dp
        return out
    win_drops = _pool_drops("WIN")
    pla_drops = _pool_drops("PLA")

    mcol1, mcol2 = st.columns(2)
    with mcol1:
        moneyflow_panel(df[df["池"] == "WIN"], "獨贏", pool_inv=win_inv, S=S, mtp=mtp,
                        other_pool_drops=pla_drops)
    with mcol2:
        moneyflow_panel(df[df["池"] == "PLA"], "位置", pool_inv=pla_inv, S=S, mtp=mtp,
                        other_pool_drops=win_drops)

    # ── stake bar charts (獨贏 / 位置) — visual overview of pool stakes ──
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        stake_bar_chart(df[df["池"] == "WIN"], "獨贏", win_inv, S)
    with bcol2:
        stake_bar_chart(df[df["池"] == "PLA"], "位置", pla_inv, S)

    # ── footer ──
    now_str = datetime.now(HKT).strftime("%H:%M:%S")
    st.markdown(
        f'<div style="text-align:center;margin-top:1rem;padding:8px;border-top:1px solid var(--border);'
        f'font-family:JetBrains Mono,monospace;font-size:10px;color:var(--muted)">'
        f'{APP_NAME} {APP_VERSION} · 每 5 秒自動更新 · {now_str} HKT</div>',
        unsafe_allow_html=True)
