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
import math
import tempfile
from pathlib import Path
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor

APP_VERSION = "V19-R2-8501BASE-20260928"
APP_NAME = "HKJC 即時賠率監察"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# ════════════════════════════════════════════════════════════
#  DISK STORAGE (永久儲存 — 寫落硬碟，重啟唔失)
# ════════════════════════════════════════════════════════════
# 每場一個資料夾，每個時間點一個 JSON snapshot（由獨立 Recorder 寫入）。
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
# Snapshot writes belong only to the standalone Recorder.

def _race_dir(race_key):
    # encode | as __ and keep the rest; date dashes stay as-is (reversible)
    safe = race_key.replace("|", "__")
    return os.path.join(DATA_DIR, safe)

















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

# ── 四池熱度 1分鐘佔比升幅 分層門檻（拉桿可調）──
RISE_TIER1 = 0.5   # ⚡ 留意
RISE_TIER2 = 0.8   # 🔥 明顯
RISE_TIER3 = 1.2   # 💥 強烈

# ── 棒型圖 + 每分鐘金額表 統一「實質金額」門檻（另一組拉桿）──
MONEY_TIER1 = 100_000   # ⚡ 留意（$）
MONEY_TIER2 = 200_000   # 🔥 明顯（$）
MONEY_TIER3 = 400_000   # 💥 強烈（$）

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

/* ── 減少每 5 秒更新時嘅閃動（又暗又光）── */
/* 1. 固定背景，refresh 時唔會閃白 */
.stApp, .main, .block-container { background:var(--bg)!important; }
/* 2. 停用 Streamlit 每次 rerun 嘅淡入動畫（就係「又暗又光」主因） */
.stApp [data-testid="stAppViewContainer"] * { animation:none!important; }
.element-container, .stMarkdown { transition:none!important; animation:none!important; }
[data-testid="stAppViewBlockContainer"] { opacity:1!important; }
/* 3. 更新時嘅「running」半透明遮罩，令佢唔會令全頁變暗 */
[data-testid="stStatusWidget"] { display:none!important; }
.stApp > div[data-stale="true"] { opacity:1!important; filter:none!important; }
[data-stale="true"] { opacity:1!important; }
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

/* ── st.container(border=True) 改返跟 .panel 一樣嘅深色風格（右邊訊號彙總用）── */
[data-testid="stVerticalBlockBorderWrapper"] {
  background:var(--card) !important; border:1px solid var(--border) !important;
  border-radius:12px !important; padding:2px 14px 14px !important;
}
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stExpander"] {
  background:transparent; border:1px solid var(--border); border-radius:8px; margin-bottom:4px;
}

/* ── ③四池熱度 + 30分鐘訊號彙總：兩個column stretch去到一樣高 ── */
.st-key-heat_signal_row [data-testid="stHorizontalBlock"] { align-items:stretch; }
.st-key-heat_signal_row [data-testid="column"] > div { height:100%; }
.st-key-heat_signal_row .panel { height:100%; box-sizing:border-box; }
.st-key-heat_signal_row [data-testid="stVerticalBlockBorderWrapper"] { height:100%; box-sizing:border-box; }

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
        "share_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts,share%)]
        "post_time": None,    # datetime in HKT
        "started_at": None,   # when monitoring began
        "signal_log": deque(maxlen=300),  # [{ts,horse,pool,tier,rise}] 30分鐘訊號記錄
        "_last_logged_tier": {},   # horse -> 上次記錄嘅 tier（升級先再記，避免洗版）
        "_last_logged_ts": {},     # horse -> 上次記錄時間（同 tier 相同時隔60秒先再記）
        "_signal_log_synced_ts": 0.0,  # 由硬碟補齊 signal_log 補到邊
    }

# RACES: race_key -> state dict. Switching races no longer wipes data;
# each race accumulates independently and is remembered.

# 開機讀一次硬碟settings（如果之前撳過「儲存設定」），補做預設值。



# ════════════════════════════════════════════════════════════
#  DATA FETCH
# ════════════════════════════════════════════════════════════
def _to_float(x):
    try: return float(x)
    except: return 0.0


def fetch_all_meetings():
    """#8：用 activeMeetings 自動攞晒所有『有賽事』嘅日期+場地（包括海外）。
    Returns list of {date, venue, label, races}. 唔使手動揀日期/場地。"""
    out = []
    try:
        payload = {"operationName": "raceMeetings",
                   "variables": {"date": None, "venueCode": None},
                   "query": TURNOVER_QUERY}
        r = requests.post(API, headers=HEADERS, json=payload, timeout=20)
        j = r.json()
        if isinstance(j, dict) and j.get("errors"):
            return []
        ams = (j.get("data", {}) or {}).get("activeMeetings", []) or []
        for m in ams:
            d = (m.get("date") or "")[:10]
            v = m.get("venueCode")
            if not d or not v:
                continue
            races = m.get("races") or []
            out.append({"date": d, "venue": v, "n_races": len(races),
                        "status": m.get("status"), "races": races})
    except Exception:
        return []
    # 去重 + 按日期排
    seen = set(); uniq = []
    for m in out:
        k = (m["date"], m["venue"])
        if k in seen:
            continue
        seen.add(k); uniq.append(m)
    return sorted(uniq, key=lambda x: (x["date"], x["venue"]))

VENUE_NAMES = {"ST": "沙田", "HV": "跑馬地"}
def venue_label(v):
    return VENUE_NAMES.get(v, v)   # 海外場地就直接顯示 code










# ════════════════════════════════════════════════════════════
#  ENRICH + HISTORY
# ════════════════════════════════════════════════════════════
def minutes_to_post(now_hkt, post_time):
    return (now_hkt - post_time).total_seconds() / 60.0 if post_time else None


def enrich(df, S, as_of_ts=None, record=True):
    """Add derived odds fields using a single time base.
    as_of_ts is the selected replay timestamp; LIVE leaves it None.
    """
    now_hkt = datetime.fromtimestamp(as_of_ts, HKT) if as_of_ts is not None else datetime.now(HKT)
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

        # Store the same timestamp used for this calculation. In REPLAY the
        # selected snapshot timestamp is used; LIVE uses wall-clock time.
        if record and curr > 0:
            S["series"][key].append((now_hkt.timestamp(), curr))

    S["last_odds"] = new_last
    df["開賠"] = open_arr
    df["即場"] = live_arr
    df["變化"] = chg_arr
    df["方向"] = dir_arr
    return df, mtp



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






def current_stake(S, pool_name, horse):
    """該馬即場累積總投注額（佔比法，= 棒型圖棒高 = 每分鐘表合計）。"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    return hist[-1][1] if hist else None



# ════════════════════════════════════════════════════════════
#  RENDER HELPERS
# ════════════════════════════════════════════════════════════






def _fmt_money(v):
    """Format HKD compactly: $1.23M / $456K / $789."""
    if v is None or v <= 0:
        return "—"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"




def _nice_ceiling(maxv):
    """自動揀靚頂 + 間格（1/2/2.5/5 × 10^n），目標約 5 格。"""
    import math
    if maxv <= 0:
        return 100000, 20000
    raw = maxv / 5.0
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    top = math.ceil(maxv / step) * step
    return int(top), int(step)

def stake_bar_chart_v(df_pool, pool_name, pool_inv, S, sort_by="馬號",
                      m1=100_000, m2=200_000, m3=400_000, mtp=None, as_of_ts=None):
    """直向棒型圖：棒高＝估算投注額（賠率佔比 × 彩池總額）。
    棒色＝最近一個完整分鐘流入（同每分鐘表 -1分格同步）。Y軸金額刻度（自動跟最大）。"""
    if pool_inv is None or pool_inv <= 0:
        st.markdown(
            f'<div class="panel"><div class="panel-title">📊 {pool_name}投注額棒型圖</div>'
            f'<div class="panel-sub">暫無彩池金額</div></div>', unsafe_allow_html=True)
        return
    sub = df_pool[df_pool["即場"] > 0].copy()
    if sub.empty:
        st.markdown(
            f'<div class="panel"><div class="panel-title">📊 {pool_name}投注額棒型圖</div>'
            f'<div class="panel-sub">有彩池金額 {_fmt_money(pool_inv)}，但未有逐匹馬賠率</div></div>',
            unsafe_allow_html=True)
        return
    pool_code = str(df_pool["池"].iloc[0]) if len(df_pool) else pool_name
    inv_live = 1.0 / sub["即場"]
    sub["投注額"] = inv_live / inv_live.sum() * pool_inv   # 即場總投注（佔比法）
    if sort_by == "賠率":
        sub = sub.sort_values("即場")
    else:
        sub = sub.sort_values("馬號", key=lambda s: pd.to_numeric(s, errors="coerce"))
    max_stake = sub["投注額"].max() if len(sub) else 1
    top, step = _nice_ceiling(max_stake)

    # 最近一個完整分鐘窗（同每分鐘表 -1分格一致）
    now_ts = as_of_ts if as_of_ts is not None else datetime.now(HKT).timestamp()
    m_end, m_start = now_ts, now_ts - 60

    # Y軸刻度 HTML（絕對定位喺左邊）
    PLOT_H = 130
    yaxis = ""
    t = 0
    while t <= top:
        y_px = int(t / top * PLOT_H) if top else 0
        if t >= 1_000_000:
            ylbl = f"{t/1_000_000:.1f}M"
        elif t > 0:
            ylbl = f"{int(t/1000)}K"
        else:
            ylbl = "0"
        yaxis += (f'<div style="position:absolute;left:0;right:0;bottom:{y_px}px;'
                  f'border-top:1px solid rgba(40,48,62,0.9);height:0">'
                  f'<span style="position:absolute;left:0;top:-7px;font-size:8px;color:var(--muted);'
                  f'font-family:JetBrains Mono,monospace">{ylbl}</span></div>')
        t += step

    bars = ""
    for _, r in sub.iterrows():
        horse = r["馬號"]
        odds = r["即場"]
        stake = r["投注額"]
        # 棒色：最近一個完整分鐘流入（同每分鐘表同步）
        inflow = latest_minute_gain(S, pool_code, horse, m_end) if S is not None else None
        inflow = inflow or 0.0
        if inflow >= m3:
            bcol = "#c878ff"
        elif inflow >= m2:
            bcol = "#ff8c3c"
        elif inflow >= m1:
            bcol = "#ffd43b"
        else:
            bcol = INFO
        PLOT_H = 130   # 繪圖區高度（px），棒用 px 計，唔用 % （% 會因為父層冇固定高而塌）
        h_px = max(2, int(stake / top * PLOT_H)) if top > 0 else 2
        inflow_lbl = (f'<div style="font-size:8px;color:{bcol};height:12px;text-align:center;white-space:nowrap">'
                      f'{("+"+_fmt_money(inflow)) if inflow>=m1 else ""}</div>')
        bars += (
            f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;min-width:0">'
            f'{inflow_lbl}'
            f'<div style="width:70%;height:{h_px}px;background:{bcol};border-radius:3px 3px 0 0;'
            f'opacity:0.9"></div>'
            f'<div style="font-size:10px;color:var(--subtext);margin-top:3px;font-family:JetBrains Mono,monospace;line-height:1.1;text-align:center">'
            f'{horse}<br><span style="font-size:8px;color:var(--muted)">{odds:g}</span></div>'
            f'</div>'
        )

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">📊 {pool_name}投注額棒型圖</div>'
        f'<div class="panel-sub">棒高＝總投注金額（Y軸自動刻度）· 近1分鐘流入 ⚡{_fmt_money(m1)}黃/🔥{_fmt_money(m2)}橙/💥{_fmt_money(m3)}紫 變色（與金額表同步）</div>'
        f'<div style="position:relative;padding-left:34px">'
        f'<div style="position:absolute;left:0;right:0;bottom:26px;height:130px">{yaxis}</div>'
        f'<div style="display:flex;align-items:flex-end;gap:3px;position:relative">{bars}</div>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="WIN", n_min=9,
                       m1=100_000, m2=200_000, m3=400_000,
                       disk_race_key=None, as_of_ts=None):
    """Per-minute actual stake inflow table (Excel-style).
    Each row a horse, each column a countdown minute (-8..-1), cell = $ that
    flowed into that horse that minute. Uses stake = pool×0.825/odds reversal.
    Only shown when post time known (needs countdown minutes)."""
    pool_inv = win_inv if pool == "WIN" else pla_inv
    title = "獨贏" if pool == "WIN" else "位置"
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df[df["池"] == pool].copy()
    sub = sub[sub["即場"] > 0]
    if sub.empty:
        return
    if mtp is None:
        st.markdown(
            f'<div class="panel"><div class="panel-title">📋 每分鐘落注金額表（{title}）</div>'
            f'<div class="panel-sub">需要開跑時間先計倒數分鐘 — 請喺上方填開跑時間</div></div>',
            unsafe_allow_html=True)
        return

    # ── 時間軸格仔（左＝早，右＝開跑）── v17.5：隔夜/當日 + 固定60/30/20/10 + 逐分鐘。
    # 隔夜/當日嘅邊界用固定嘅 00:00（唔理個別場次開跑時間），解決 #6b：
    # 同一日唔同場開跑時間唔同，但早段（隔夜/當日）理應完全一致。
    post_ts = S["post_time"].timestamp() if S["post_time"] else None
    post_dt_local = S["post_time"] if S["post_time"] else None

    def edge_ts(min_before):
        return post_ts - min_before * 60 if post_ts is not None else None

    # 當日 00:00（固定，唔跟開跑時間浮動）
    midnight_dt = datetime(post_dt_local.year, post_dt_local.month, post_dt_local.day,
                           0, 0, 0, tzinfo=HKT) if post_dt_local else None
    midnight_ts = midnight_dt.timestamp() if midnight_dt else None

    # 逐格定義：(label, is_hour[早段/整點格,唔變色], ts_start, ts_end)
    # 60/30/20/10：呢格代表「由呢個分鐘數開始，去到下一個刻度」嘅一段流入
    #（例如「60」= 開跑前60分鐘 → 開跑前30分鐘 呢段）。10之後逐分鐘去到開跑。
    ladder = [60, 30, 20, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    cols = [
        ("隔夜", True, False, None, midnight_ts),
        ("當日", True, False, midnight_ts, edge_ts(60)),
    ]
    for i in range(len(ladder) - 1):
        start_e, end_e = ladder[i], ladder[i + 1]
        lbl = "開跑" if end_e == 0 else str(start_e)
        is_hour = start_e >= 60   # 60呢格仲係大格,唔變色；30/20/10之後嘅逐分鐘格先變色
        cols.append((lbl, is_hour, False, edge_ts(start_e), edge_ts(end_e)))

    latest_end_ts = (as_of_ts if as_of_ts is not None
                     else datetime.now(HKT).timestamp())
    cols.append(("最新1分", False, True, latest_end_ts - 60, latest_end_ts))

    def stake_bucket(horse, ts_start, ts_end):
        if ts_end is None:
            return None
        if ts_start is None:
            # 由最早記錄到 ts_end 嘅累積
            s_end = stake_at_ts(S, pool, horse, ts_end)
            hist = S["stake_hist"][(pool, str(horse))]
            s_start = hist[0][1] if hist else None
            if s_end is None or s_start is None:
                return None
            return s_end - s_start
        if ts_end == latest_end_ts and ts_start == latest_end_ts - 60:
            return latest_minute_gain(S, pool, horse, latest_end_ts)
        return stake_in_bucket(S, pool, horse, ts_start, ts_end)

    # 「隔夜」「當日」由硬碟計（唔受記憶體deque上限影響，開賣提前幾耐都啱）；
    # 60/30/20/10同逐分鐘就用返記憶體（夠近，唔使拖硬碟）。
    # disk_race_key：REPLAY 揀嗰場嘅 key（同上面下拉選單可能唔同場），
    # 冇傳就用返 S 自己嗰個（LIVE 情況）。
    early_map = {}
    for horse in sub["馬號"]:
        v_mid = stake_at_ts(S, pool, horse, min(midnight_ts, S['as_of_ts'])) if midnight_ts else None
        v_60 = stake_at_ts(S, pool, horse, min(edge_ts(60), S['as_of_ts'])) if edge_ts(60) else None
        early_map[str(horse)] = {"隔夜": v_mid, "當日": v_60 - v_mid if v_mid is not None and v_60 is not None and S['as_of_ts'] >= midnight_ts else None}

    rows_data = []
    for _, r in sub.sort_values("即場").iterrows():
        horse = str(r["馬號"])
        odds = r["即場"]
        eb = early_map.get(horse, {})
        per_col = [eb.get("隔夜"), eb.get("當日")]
        per_col += [stake_bucket(horse, s, e) for (_, _, _, s, e) in cols[2:]]
        rows_data.append((horse, odds, per_col))

    def cellcol(v, is_hour):
        if v is None:
            return "var(--muted)"
        if is_hour:
            return "var(--subtext)"   # 早段/大格唔變色
        if v >= m3: return "#c878ff"
        if v >= m2: return "#ff8c3c"
        if v >= m1: return "#ffd43b"
        return "var(--subtext)"

    # header
    head = '<th style="text-align:left;padding:3px 5px;font-size:9px;color:var(--muted);position:sticky;left:0;background:var(--card)">馬 賠</th>'
    for (lbl, is_hour, is_prev, _, _) in cols:
        col_bg = "background:rgba(30,30,44,0.5);" if is_hour else ""
        sync_head = 'border-left:2px solid rgba(80,170,255,0.55);' if is_prev else ''
        head += (f'<th style="text-align:right;padding:2px 5px;font-size:9px;color:{"#78899a" if is_hour else "var(--muted)"};{col_bg}{sync_head}">'
                 f'{lbl}</th>')
    head += '<th style="text-align:right;padding:3px 5px;font-size:9px;color:#e0a83c">合計</th>'

    body = ""
    for horse, odds, per_col in rows_data:
        # 合計 = 即場總投注（佔比法，同棒型圖棒高一致）
        total = current_stake(S, pool, horse)
        if total is None:
            total = sum(v for v in per_col[:-1] if v) or 0
        cells = ""
        for (lbl, is_hour, is_prev, _, _), v in zip(cols, per_col):
            txt = f'+{_fmt_money(v)}' if (v and v > 0) else ('—' if v is None else _fmt_money(v))
            bg = ''
            if not is_hour and v:
                if v >= m3: bg = 'background:rgba(200,120,255,0.15);'
                elif v >= m2: bg = 'background:rgba(255,140,60,0.15);'
                elif v >= m1: bg = 'background:rgba(255,212,59,0.12);'
            sync_border = 'border-left:2px solid rgba(80,170,255,0.55);' if is_prev else ''
            cells += f'<td style="text-align:right;padding:2px 5px;font-size:10px;color:{cellcol(v,is_hour)};{bg}{sync_border}font-family:JetBrains Mono,monospace">{txt}</td>'
        body += (f'<tr><td style="padding:3px 5px;font-size:11px;color:var(--text);white-space:nowrap;position:sticky;left:0;background:var(--card)">'
                 f'{horse} <span style="font-size:8px;color:var(--subtext)">{odds:g}</span></td>'
                 f'{cells}'
                 f'<td style="text-align:right;padding:3px 5px;font-size:10px;color:#e0a83c;font-weight:600;font-family:JetBrains Mono,monospace">{_fmt_money(total)}</td></tr>')

    html = (
        f'<div class="panel" style="overflow-x:auto">'
        f'<div class="panel-title">📋 落注金額表（{title} · 時間由左到右）</div>'
        f'<div class="panel-sub">隔夜(開賣→00:00) · 當日(00:00→-60分,全場一致) · '
        f'60/30/20/10(每段) · 10分之後逐分鐘 → 開跑 · 最新1分＝畫面時間向前60秒 · '
        f'⚡{_fmt_money(m1)}黃/🔥{_fmt_money(m2)}橙/💥{_fmt_money(m3)}紫（只臨場逐分鐘格變色）· 合計＝總投注（同棒型圖）</div>'
        f'<table style="border-collapse:collapse;width:100%">'
        f'<tr>{head}</tr>{body}</table>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)



def combo_matrix_panel(matrix, pool_title, horses):
    """Render a combination pool (QIN/QPL) as HKJC-style triangular matrix.
    horses: sorted list of int horse numbers present in the race."""
    if not matrix or not horses:
        st.markdown(
            f'<div class="panel"><div class="panel-title">🎲 {pool_title}</div>'
            f'<div class="panel-sub">暫無資料</div></div>', unsafe_allow_html=True)
        return
    hs = sorted(horses)
    # find hottest (lowest odds) for highlight
    min_odds = min(matrix.values()) if matrix else 0
    # header row: horses[1:] as columns
    cols = hs[1:]
    rows_h = hs[:-1]

    def cell(v, hot=False):
        if v is None:
            return '<td style="background:#3a4560;padding:3px"></td>'
        bg = 'background:#c0392b;color:#fff;font-weight:600;' if hot else ''
        return f'<td style="text-align:center;padding:3px;font-size:10px;{bg}">{v:g}</td>'

    # build header
    head = f'<td style="background:#1a2a4a;color:#9aa7b8;padding:3px;text-align:center;font-size:9px">{pool_title}</td>'
    for c in cols:
        head += f'<td style="background:#2a3550;padding:3px;text-align:center;color:#9aa7b8;font-size:10px">{c}</td>'

    body = ""
    for ri, rh in enumerate(rows_h):
        row = f'<td style="background:#3a4560;text-align:center;padding:3px;color:#9aa7b8;font-size:10px">{rh}</td>'
        for c in cols:
            if c <= rh:
                # left of diagonal: blank (or diagonal marker at c == next)
                row += '<td style="background:#3a4560;padding:3px"></td>'
            else:
                pair = (rh, c)
                odds = matrix.get(pair)
                row += cell(odds, hot=(odds is not None and odds == min_odds))
        body += f'<tr>{row}</tr>'

    html = (
        f'<div class="panel" style="overflow-x:auto">'
        f'<div class="panel-title">🎲 {pool_title}</div>'
        f'<div class="panel-sub">紅＝最熱組合（賠率最低 {min_odds:g}）· 交叉格＝該對馬賠率</div>'
        f'<table style="border-collapse:collapse;width:100%">'
        f'<tr>{head}</tr>{body}</table>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S,
                         pool_totals, cold_odds=10.0, rise_thresh=0.5):
    """Four-pool combined heat table with money + 1-min share-rise detection.
    pool_totals: {'WIN':$, 'PLA':$, 'QIN':$, 'QPL':$} (QIN/QPL are estimates).
    rise_thresh: flag a horse if its share rose >= this %pts in the last 60s."""
    win = df[df["池"] == "WIN"].copy()
    if win.empty:
        return
    inv = 1.0 / win["即場"]
    win["W%"] = inv / inv.sum() * 100.0
    win_share = {int(k): v for k, v in zip(win["馬號"], win["W%"])}

    def topN(part, n=3):
        return set(sorted(part, key=lambda h: part[h], reverse=True)[:n]) if part else set()
    top_win = topN(win_share)
    top_pla = topN(pla_part)
    top_qin = topN(qin_part)
    top_qpl = topN(qpl_part)

    wt = pool_totals.get("WIN") or 0
    pt = pool_totals.get("PLA") or 0
    qt = pool_totals.get("QIN") or 0
    qpt = pool_totals.get("QPL") or 0
    t1 = st.session_state.get("rise_t1", RISE_TIER1)
    t2 = st.session_state.get("rise_t2", RISE_TIER2)
    t3 = st.session_state.get("rise_t3", RISE_TIER3)

    rows = []
    for _, r in win.sort_values("即場").iterrows():
        h = int(r["馬號"])
        wo = r["即場"]
        shares = {"WIN": r["W%"], "PLA": pla_part.get(h, 0.0),
                  "QIN": qin_part.get(h, 0.0), "QPL": qpl_part.get(h, 0.0)}
        tops = {"WIN": h in top_win, "PLA": h in top_pla,
                "QIN": h in top_qin, "QPL": h in top_qpl}
        nhot = sum(tops.values())
        suspicious = (wo >= cold_odds) and nhot >= 3

        # 1-min share rise across pools -> tiered
        rises = {p: share_rise(S, p, h, 60) for p in ("WIN", "PLA", "QIN", "QPL")}
        max_rise = max(rises.values()) if rises else 0.0
        if max_rise >= t3:
            tier, tier_col, tier_tag = 3, "#c878ff", "💥強烈"
        elif max_rise >= t2:
            tier, tier_col, tier_tag = 2, "#ff8c3c", "🔥明顯"
        elif max_rise >= t1:
            tier, tier_col, tier_tag = 1, "#ffd43b", "⚡留意"
        else:
            tier, tier_col, tier_tag = 0, "#5b6675", ""

        # pool % cells: all neutral grey (no green top-3)
        def cell(pct):
            return f'<span class="c-num" style="color:#9aa7b8">{pct:.1f}%</span>'

        marker = ""
        if suspicious:
            marker = ' 💥可疑' if tier >= 3 else (' 🔥可疑' if tier >= 1 else ' 可疑')
        elif tier > 0:
            marker = f' {tier_tag}'

        # row background by strongest signal
        rowbg = ''
        if suspicious:
            rowbg = 'background:rgba(239,87,87,0.10);border-radius:4px;'
        elif tier == 3:
            rowbg = 'background:rgba(200,120,255,0.10);border-radius:4px;'
        elif tier == 2:
            rowbg = 'background:rgba(255,140,60,0.10);border-radius:4px;'
        elif tier == 1:
            rowbg = 'background:rgba(255,212,59,0.08);border-radius:4px;'

        nhot_col = "#ff5757" if suspicious else "#5b6675"
        rise_disp = f"+{max_rise:.1f}%" if max_rise >= 0.1 else "—"

        rows.append(
            f'<div class="row" style="{rowbg}">'
            f'<span class="c-no" style="color:var(--text)">{h}'
            f'<span style="font-size:9px;color:{"#ff5757" if suspicious else "#9aa7b8"}"> {wo:g}</span>'
            f'<span style="font-size:9px;color:{tier_col}">{marker}</span></span>'
            f'{cell(shares["WIN"])}{cell(shares["PLA"])}{cell(shares["QIN"])}{cell(shares["QPL"])}'
            f'<span class="c-num" style="color:{tier_col};font-weight:600">{rise_disp}</span>'
            f'<span class="c-num" style="color:{nhot_col};font-weight:600">{nhot}/4</span>'
            f'</div>'
        )

    # money reference line: 直接將⚡/🔥/💥三級門檻（%）換算做各池實際觸發金額（$），
    # 對應真正決定訊號嘅 share_rise() 門檻，唔使用戶自己攞「1%」再心算一次。
    def tier_money(total, pct):
        return _fmt_money(total * pct / 100.0) if total else "—"
    money_ref = (
        f'觸發金額對照（即場彩池 × 門檻%）：<br>'
        f'⚡{t1:g}% 獨贏{tier_money(wt, t1)}/位置{tier_money(pt, t1)}/'
        f'連贏{tier_money(qt, t1)}/位置Q{tier_money(qpt, t1)}<br>'
        f'🔥{t2:g}% 獨贏{tier_money(wt, t2)}/位置{tier_money(pt, t2)}/'
        f'連贏{tier_money(qt, t2)}/位置Q{tier_money(qpt, t2)}<br>'
        f'💥{t3:g}% 獨贏{tier_money(wt, t3)}/位置{tier_money(pt, t3)}/'
        f'連贏{tier_money(qt, t3)}/位置Q{tier_money(qpt, t3)}'
        f'（連贏/位置Q金額為粗估）'
    )

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">🎯 四池綜合熱度</div>'
        f'<div class="panel-sub">按獨贏賠率排序 · 平時乾淨 · '
        f'1分升 ⚡{t1:g}%/🔥{t2:g}%/💥{t3:g}% · 冷馬(≥{cold_odds:g}倍)多池皆熱＝可疑<br>{money_ref}</div>'
        f'<div class="thead">'
        f'<span class="c-no">馬 賠率</span>'
        f'<span class="c-num">獨贏</span><span class="c-num">位置</span>'
        f'<span class="c-num">連贏</span><span class="c-num">位置Q</span>'
        f'<span class="c-num">1分升</span><span class="c-num">皆熱</span></div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ffd43b"></i>⚡留意</span>'
        f'<span><i style="background:#ff8c3c"></i>🔥明顯</span>'
        f'<span><i style="background:#c878ff"></i>💥強烈</span>'
        f'<span><i style="background:#ff5757"></i>冷馬皆熱可疑</span>'
        f'</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def signal_summary_panel(events, minutes=30, as_of_ts=None):
    """30分鐘訊號彙總（右邊新面板）：按馬分組，撳開睇逐行時序細節。
    events: list of {ts,horse,pool,tier,rise}。as_of_ts=None 用而家時間；
    REPLAY 模式會傳返嗰個snapshot嘅ts，等個30分鐘窗跟返翻睇緊嗰一刻。
    用 st.container(border=True) 包住成個panel（連暫無訊號都喺border入面），
    等個box可以自動stretch去到同左邊「四池綜合熱度」一樣高（CSS喺別處控制）。"""
    if as_of_ts is None:
        as_of_ts = datetime.now(HKT).timestamp()
    cutoff = as_of_ts - minutes * 60
    evs_in_window = [e for e in events if cutoff <= e.get("ts", 0) <= as_of_ts]

    with st.container(border=True):
        st.markdown(
            f'<div class="panel-title">🕐 {minutes}分鐘訊號彙總</div>'
            f'<div class="panel-sub">按馬分組 · 撳隻馬展開時序細節 · 過咗{minutes}分鐘自動移除</div>',
            unsafe_allow_html=True)

        if not evs_in_window:
            st.caption("暫無訊號")
            return

        by_horse = defaultdict(list)
        for e in evs_in_window:
            by_horse[e["horse"]].append(e)

        tier_emoji = {1: "⚡", 2: "🔥", 3: "💥"}

        def horse_key(h):
            evs = by_horse[h]
            return (-max(ev["tier"] for ev in evs), -max(ev["ts"] for ev in evs))

        for h in sorted(by_horse.keys(), key=horse_key):
            evs = sorted(by_horse[h], key=lambda e: e["ts"], reverse=True)
            counts = {1: 0, 2: 0, 3: 0}
            for e in evs:
                counts[e["tier"]] = counts.get(e["tier"], 0) + 1
            summary = "　".join(f'{tier_emoji[t]}×{counts[t]}' for t in (3, 2, 1) if counts.get(t))
            with st.expander(f"{h}號　{summary}", expanded=False):
                for e in evs:
                    t_str = datetime.fromtimestamp(e["ts"], HKT).strftime("%H:%M:%S")
                    st.markdown(
                        f'<div style="display:flex;gap:8px;font-size:11px;padding:2px 0">'
                        f'<span style="color:var(--muted);width:56px">{t_str}</span>'
                        f'<span style="color:var(--subtext);width:36px">{e["pool"]}</span>'
                        f'<span style="color:var(--text)">{tier_emoji.get(e["tier"], "")} +{e["rise"]:.1f}%</span>'
                        f'</div>', unsafe_allow_html=True)


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
    cutoff = card["race_date"].min()
    history = history[history["race_date"] < cutoff].copy()
    combined = pd.concat([history, card], ignore_index=True, sort=False)
    featured = build_features(combined)
    live = featured[featured["race_id"] == card["race_id"].iloc[0]].copy()
    live = live.sort_values("horse_no", key=lambda s: pd.to_numeric(s, errors="coerce"))
    x_live = _model_apply_scaler(live, bundle["scaler"], bundle["feature_cols"])
    p_model = _group_softmax(x_live @ np.asarray(bundle["cl_beta"], dtype=float))
    return live[["horse_no", "horse_name"]].assign(p_model=p_model).to_dict("records")


def score_live_model(card_rows, win_odds, rebate_rate=0.0):
    """Return real-time Benter probabilities and value metrics for one race."""
    if not MODEL_READY or not card_rows or not win_odds:
        return pd.DataFrame(), MODEL_IMPORT_ERROR or "未有完整排位／即時獨贏賠率"
    try:
        bundle, _ = load_quant_assets()
        card_json = json.dumps(card_rows, ensure_ascii=False, sort_keys=True, default=str)
        base = pd.DataFrame(build_live_fundamentals(card_json))
        base["horse_key"] = base["horse_no"].apply(lambda x: str(int(float(x))))
        base["win_odds"] = base["horse_key"].map(
            {str(int(float(k))): float(v) for k, v in win_odds.items() if float(v) > 0}
        )
        base = base.dropna(subset=["win_odds"]).reset_index(drop=True)
        if len(base) < 2:
            return pd.DataFrame(), "有效獨贏賠率不足"
        race_idx = np.zeros(len(base), dtype=int)
        p_public = public_probabilities(base["win_odds"].to_numpy(float), race_idx)
        eps = 1e-12
        z = (float(bundle["ss_alpha"]) * np.log(np.clip(base["p_model"], eps, 1.0))
             + float(bundle["ss_beta"]) * np.log(np.clip(p_public, eps, 1.0)))
        p_final = _group_softmax(z)
        n_places = 2 if len(base) < 7 else 3
        if n_places == 2:
            # Two-place races: sum P(first/second) using fitted lambda2.
            lam2 = float(bundle["lambda2"])
            p_place = np.zeros(len(base))
            for i in range(len(base)):
                rest = np.delete(p_final, i) ** lam2
                denom = rest.sum()
                if denom > 0:
                    cond = rest / denom
                    p_place[i] += p_final[i]
                    p_place[np.arange(len(base)) != i] += p_final[i] * cond
        else:
            p_place = place_probs(p_final, float(bundle["lambda2"]), float(bundle["lambda3"]))
        base["p_public"] = p_public
        base["p_final"] = p_final
        base["fair_odds"] = 1.0 / np.clip(p_final, eps, 1.0)
        base["overlay_pct"] = (p_final / np.clip(p_public, eps, 1.0) - 1.0) * 100.0
        base["ev"] = p_final * base["win_odds"] + float(rebate_rate) * (1.0 - p_final)
        base["p_place"] = np.clip(p_place, 0.0, 1.0)
        base["value"] = base["ev"] - 1.0
        return base.sort_values("ev", ascending=False), None
    except Exception as exc:
        return pd.DataFrame(), f"模型計算失敗：{exc}"


def matching_meeting(meetings, date_str, venue):
    return next((m for m in meetings or []
                 if str(m.get('date') or '')[:10] == str(date_str)[:10]
                 and str(m.get('venueCode') or '').upper() == str(venue).upper()), None)


def race_info_matches(info, race_key):
    if not isinstance(info, dict):
        return False
    d, v, r = race_key.split('|')
    return (str(info.get('date') or '')[:10] == d
            and str(info.get('venue') or '').upper() == v.upper()
            and str(info.get('no')) == str(int(r)))


def normalize_card(meeting, race_no):
    if not meeting:
        return None
    date_str = str(meeting.get('date') or '')[:10]
    venue = meeting.get('venueCode')
    rc = next((r for r in meeting.get('races') or []
               if str(r.get('no')) == str(int(race_no))), None)
    if not rc:
        return None
    runners = [r for r in rc.get('runners') or []
               if str(r.get('status') or '').upper() not in ('SCRATCHED', 'SCR', 'WITHDRAWN')]
    info = {'date': date_str, 'venue': venue, 'no': int(race_no),
            'post': rc.get('postTime'), 'name': rc.get('raceName_ch'),
            'dist': rc.get('distance'), 'cls': rc.get('raceClass_ch'),
            'track': (rc.get('raceTrack') or {}).get('description_ch'),
            'course': (rc.get('raceCourse') or {}).get('description_ch'),
            'going': rc.get('go_ch'), 'field': rc.get('wageringFieldSize') or len(runners),
            'status': rc.get('status'), 'card_rows': []}
    for runner in runners:
        no = runner.get('no') or runner.get('saddleClothNo')
        horse = runner.get('horse') or {}
        horse_id = horse.get('id') or horse.get('code') or runner.get('id')
        if no is None or not horse_id:
            continue
        info['card_rows'].append({
            'race_id': f"{date_str.replace('-', '')}_{venue}_{int(race_no)}",
            'race_date': date_str, 'season': 'LIVE', 'venue': venue, 'race_no': int(race_no),
            'horse_no': _to_float(no), 'horse_id': str(horse_id),
            'horse_name': runner.get('name_ch') or runner.get('name_en') or str(no),
            'distance': _to_float(rc.get('distance')), 'going': rc.get('go_ch') or rc.get('go_en') or '',
            'track': info['track'] or '', 'track_config': (rc.get('raceCourse') or {}).get('displayCode') or '',
            'class_level': int(''.join(c for c in str(rc.get('claCode') or rc.get('raceClass_en') or '') if c.isdigit()) or 0),
            'field_size': int(info['field']),
            'jockey_id': (runner.get('jockey') or {}).get('code') or 'UNKNOWN',
            'trainer_id': (runner.get('trainer') or {}).get('code') or 'UNKNOWN',
            'draw': _to_float(runner.get('barrierDrawNumber')),
            'actual_weight': _to_float(runner.get('handicapWeight')),
            'declared_horse_weight': _to_float(runner.get('currentWeight')),
            'runner_status': runner.get('status') or 'Declared',
        })
    return info


def snapshot_from_pools(meeting, race_no, pools, captured_ts, meeting_ts):
    info = normalize_card(meeting, race_no)
    if info is None:
        return None
    key = f"{info['date']}|{info['venue']}|{int(race_no)}"
    snap = {'schema_version': 2, 'ts': captured_ts, 'race_key': key, 'post_time': None,
            'win': {}, 'pla': {}, 'qin': {}, 'qpl': {}, 'pool': {},
            'race_info': info, 'pool_captured_ts': meeting_ts, 'source_updates': {}}
    if info.get('post'):
        try:
            pt = datetime.fromisoformat(info['post'].replace('Z', '+00:00'))
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=HKT)
            snap['post_time'] = pt.timestamp()
        except (ValueError, TypeError):
            pass
    for p in meeting.get('poolInvs') or []:
        if int(race_no) not in [int(n) for n in (p.get('leg') or {}).get('races') or []]:
            continue
        code = p.get('oddsType')
        if code in ('WIN', 'PLA', 'QIN', 'QPL'):
            snap['pool'][code] = _to_float(p.get('investment'))
            snap['source_updates'][code + '_pool'] = p.get('lastUpdateTime')
    for p in pools or []:
        code = p.get('oddsType')
        if code not in ('WIN', 'PLA', 'QIN', 'QPL'):
            continue
        legs = (p.get('leg') or {}).get('races') or []
        if legs and int(race_no) not in [int(n) for n in legs]:
            continue
        snap['source_updates'][code + '_odds'] = p.get('lastUpdateTime')
        for node in p.get('oddsNodes') or []:
            comb = str(node.get('combString') or '').replace('-', ',')
            odds = _to_float(node.get('oddsValue'))
            if not math.isfinite(odds) or odds <= 0:
                continue
            try:
                if code in ('WIN', 'PLA'):
                    comb = str(int(comb))
                else:
                    parts = sorted(int(x) for x in comb.split(','))
                    if len(parts) != 2 or parts[0] == parts[1]:
                        continue
                    comb = ','.join(map(str, parts))
            except (TypeError, ValueError):
                continue
            snap[code.lower()][comb] = odds
    return snap if snap['win'] else None


def valid_snapshot(snap, race_key):
    if not isinstance(snap, dict) or snap.get('race_key') != race_key:
        return False
    try:
        if not math.isfinite(float(snap['ts'])):
            return False
        for code in ('win', 'pla', 'qin', 'qpl'):
            values = snap.get(code) or {}
            if not isinstance(values, dict):
                return False
            for key, value in values.items():
                parts = str(key).split(',')
                if len(parts) != (1 if code in ('win', 'pla') else 2):
                    return False
                if any(int(h) < 1 for h in parts):
                    return False
                if not math.isfinite(float(value)) or float(value) <= 0:
                    return False
        if not isinstance(snap.get('pool') or {}, dict):
            return False
        return all(v is None or (math.isfinite(float(v)) and float(v) >= 0)
                   for v in (snap.get('pool') or {}).values())
    except (KeyError, TypeError, ValueError):
        return False


def pool_shares(snap):
    result = {}
    for code in ('WIN', 'PLA', 'QIN', 'QPL'):
        raw = {}
        for comb, value in (snap.get(code.lower()) or {}).items():
            odds = _to_float(value)
            if not math.isfinite(odds) or odds <= 0:
                continue
            try:
                horses = [int(h) for h in str(comb).replace('-', ',').split(',')]
            except ValueError:
                continue
            for h in horses:
                raw[h] = raw.get(h, 0.0) + 1.0 / odds
        total = sum(raw.values())
        result[code] = {h: x / total * 100 for h, x in raw.items()} if total else {}
    return result


def sample_at(points, ts):
    i = bisect_right(points, (ts, float('inf'))) - 1
    return points[i] if i >= 0 else None


def window_gain(points, end_ts, seconds=60, max_age=45):
    start = sample_at(points, end_ts - seconds)
    end = sample_at(points, end_ts)
    if not start or not end:
        return None
    if end_ts - seconds - start[0] > max_age or end_ts - end[0] > max_age or end[0] <= end_ts - seconds:
        return None
    return end[1] - start[1]


def select_replay_index(snaps, target_ts):
    if not snaps or target_ts is None:
        return None
    times = [float(s['ts']) for s in snaps]
    i = bisect_right(times, target_ts) - 1
    if i < 0 or target_ts - times[i] > 45:
        return None
    return i


def make_history(snaps, end_ts, thresholds):
    state = _blank_state()
    state['as_of_ts'] = end_ts
    state['stake_hist'] = defaultdict(list)
    state['share_hist'] = defaultdict(list)
    state['series'] = defaultdict(list)
    state['signal_log'] = []
    last_tier, last_event = {}, {}
    for snap in snaps:
        ts = float(snap['ts'])
        if ts > end_ts:
            break
        shares = snap.get('_shares')
        if shares is None:
            shares = pool_shares(snap)
            snap['_shares'] = shares
        for code, values in shares.items():
            total = (snap.get('pool') or {}).get(code)
            for h, share in values.items():
                key = (code, str(h))
                state['share_hist'][key].append((ts, share))
                if code in ('WIN', 'PLA'):
                    odds = _to_float(snap[code.lower()].get(str(h)))
                    state['series'][key].append((ts, odds))
                    state['open_odds'].setdefault(key, odds)
                    if total is not None and _to_float(total) > 0:
                        state['stake_hist'][key].append((ts, share / 100 * _to_float(total)))
        # Only the last 31 minutes need event detection; older points remain for tables.
        if ts < end_ts - 1860:
            continue
        for h in shares['WIN']:
            rises = {code: window_gain(state['share_hist'][(code, str(h))], ts) for code in shares}
            rises = {code: rise for code, rise in rises.items() if rise is not None}
            if not rises:
                continue
            cause = max(rises, key=rises.get)
            rise = rises[cause]
            tier = sum(rise >= threshold for threshold in thresholds)
            previous = last_tier.get(h, 0)
            if tier and (tier > previous or ts - last_event.get(h, 0) >= 60):
                state['signal_log'].append({'ts': ts, 'horse': h, 'pool': cause,
                                            'tier': tier, 'rise': round(rise, 2)})
                last_event[h] = ts
            last_tier[h] = tier
    return state


def latest_minute_gain(S, pool_name, horse, end_ts):
    return window_gain(S['stake_hist'][(pool_name, str(horse))], end_ts)


def stake_at_ts(S, pool_name, horse, target_ts):
    if target_ts is None:
        return None
    ts = min(target_ts, S['as_of_ts'])
    item = sample_at(S['stake_hist'][(pool_name, str(horse))], ts)
    return item[1] if item else None


def stake_in_bucket(S, pool_name, horse, ts_start, ts_end):
    if ts_start is None or ts_end is None or ts_start >= S['as_of_ts']:
        return None
    points = S['stake_hist'][(pool_name, str(horse))]
    a = sample_at(points, ts_start)
    b = sample_at(points, min(ts_end, S['as_of_ts']))
    return b[1] - a[1] if a and b else None


def share_rise(S, pool, horse, seconds=60):
    return window_gain(S['share_hist'][(pool, str(horse))], S['as_of_ts'], seconds) or 0.0

@st.cache_data(ttl=300, show_spinner=False)
def current_meetings():
    return fetch_all_meetings()


def fetch_full_meeting(date_str, venue):
    payload = {'operationName': 'raceMeetings', 'variables': {'date': date_str, 'venueCode': venue},
               'query': TURNOVER_QUERY}
    response = requests.post(API, headers=HEADERS, json=payload, timeout=(5, 15))
    response.raise_for_status()
    body = response.json()
    if body.get('errors'):
        raise RuntimeError('HKJC 回傳查詢錯誤')
    meeting = matching_meeting((body.get('data') or {}).get('raceMeetings'), date_str, venue)
    if meeting is None:
        raise RuntimeError(f'HKJC 未回傳相符賽日：{date_str} {venue}')
    return meeting, datetime.now(HKT).timestamp()


def fetch_live_snapshot(race_key):
    d, v, r = race_key.split('|')
    def odds_request():
        payload = {'operationName': 'racing', 'query': RACING_QUERY,
                   'variables': {'date': d, 'venueCode': v, 'raceNo': int(r),
                                 'oddsTypes': ['WIN', 'PLA', 'QIN', 'QPL']}}
        response = requests.post(API, headers=HEADERS, json=payload, timeout=(5, 15))
        response.raise_for_status()
        body = response.json()
        if body.get('errors'):
            raise RuntimeError('HKJC 賠率查詢錯誤')
        meetings = (body.get('data') or {}).get('raceMeetings') or []
        if len(meetings) != 1:
            raise RuntimeError('HKJC 賠率回應賽日數量異常')
        return meetings[0].get('pmPools') or [], datetime.now(HKT).timestamp()
    with ThreadPoolExecutor(max_workers=2) as executor:
        meeting_future = executor.submit(fetch_full_meeting, d, v)
        odds_future = executor.submit(odds_request)
        meeting, meeting_ts = meeting_future.result()
        pools, odds_ts = odds_future.result()
    snap = snapshot_from_pools(meeting, int(r), pools, max(meeting_ts, odds_ts), meeting_ts)
    if snap is None:
        raise RuntimeError('呢場暫時冇有效獨贏賠率')
    snap['odds_captured_ts'] = odds_ts
    return snap


def list_saved_races():
    root = Path(DATA_DIR)
    if not root.is_dir():
        return []
    races = []
    for folder in root.iterdir():
        parts = folder.name.split('__')
        if not folder.is_dir() or len(parts) != 3:
            continue
        try:
            date.fromisoformat(parts[0])
            if int(parts[2]) < 1:
                continue
        except ValueError:
            continue
        if any(p.stem.isdigit() for p in folder.glob('*.json')):
            races.append('|'.join(parts))
    return sorted(races, key=lambda key: (key.split('|')[0], key.split('|')[1], int(key.split('|')[2])))


def load_snapshots(race_key):
    # One directory index per refresh; only changed/new files are parsed again.
    cache = st.session_state.setdefault('_archive_v19_r2', {})
    entry = cache.setdefault(race_key, {'files': {}, 'snaps': [], 'cards': {}})
    changed, seen, errors = False, set(), []
    try:
        paths = list(Path(_race_dir(race_key)).glob('*.json'))
    except OSError as exc:
        st.session_state['_archive_error'] = str(exc)
        return []
    for path in paths:
        if not path.stem.isdigit():
            continue
        name = path.name
        seen.add(name)
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            previous = entry['files'].get(name)
            if previous and previous[0] == signature:
                continue
            value = json.loads(path.read_text(encoding='utf-8'))
            if not valid_snapshot(value, race_key):
                raise ValueError('場次或記錄格式不符')
            value['ts'] = float(value['ts'])
            ref = value.get('race_info_ref')
            if ref:
                # Accept only content hashes; never follow arbitrary stored paths.
                if isinstance(ref, str) and len(ref) == 64 and all(c in '0123456789abcdef' for c in ref):
                    if ref not in entry['cards']:
                        try:
                            card = json.loads((path.parent / '.cards' / (ref + '.card')).read_text(encoding='utf-8'))
                            entry['cards'][ref] = card if race_info_matches(card, race_key) else None
                        except (OSError, ValueError):
                            entry['cards'][ref] = None
                    value['race_info'] = entry['cards'][ref]
                else:
                    value['race_info'] = None
            entry['files'][name] = (signature, value)
            changed = True
        except (OSError, ValueError, TypeError) as exc:
            errors.append(f'{name}: {exc}')
            if name in entry['files']:
                del entry['files'][name]
                changed = True
    for name in list(entry['files']):
        if name not in seen:
            del entry['files'][name]
            changed = True
    if changed:
        unique = {value[1]['ts']: value[1] for value in entry['files'].values()}
        entry['snaps'] = [unique[k] for k in sorted(unique)]
    st.session_state['_archive_error'] = '; '.join(errors[:3])
    # Bound loaded races; selected race remains resident.
    for other in list(cache):
        if len(cache) <= 3:
            break
        if other != race_key:
            del cache[other]
    return entry['snaps']


def settings_path():
    return Path(os.environ.get('HKJC_V19_DATA_DIR', str(Path.home() / 'hkjc_data_v19'))) / '_settings.json'


def load_settings():
    for path in (settings_path(), Path(DATA_DIR) / '_settings.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
    return {}


def save_settings(settings):
    try:
        path = settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='settings-', suffix='.tmp', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def replay_controls(snaps, race_key, post_ts):
    times = [float(s['ts']) for s in snaps]
    state_key = f'replay_time::{race_key}'
    if state_key not in st.session_state:
        st.session_state[state_key] = len(snaps) - 1
    st.session_state[state_key] = max(0, min(int(st.session_state[state_key]), len(snaps) - 1))
    st.caption('REPLAY 時間軸：數字表示開跑前分鐘；缺少該時段記錄會提示。')
    targets = [('隔夜', None), ('60', 60), ('30', 30), ('20', 20), ('10', 10),
               ('9', 9), ('8', 8), ('7', 7), ('6', 6), ('5', 5), ('4', 4), ('3', 3), ('2', 2), ('1', 1), ('開跑', 0)]
    for (label, minutes), col in zip(targets, st.columns(len(targets))):
        with col:
            if st.button(label, key=f'clip::{race_key}::{label}', use_container_width=True):
                if post_ts is None:
                    st.warning('未有開跑時間')
                else:
                    post = datetime.fromtimestamp(post_ts, HKT)
                    target = post.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() if minutes is None else post_ts - minutes * 60
                    index = select_replay_index(snaps, target)
                    if index is None:
                        st.warning('呢段時間冇相近記錄')
                    else:
                        st.session_state[state_key] = index
    if len(snaps) > 1:
        idx = st.slider('時間軸（拉去任何一刻，微調）', 0, len(snaps) - 1, key=state_key)
    else:
        idx = 0
        st.caption('只有一個記錄點，未能移動時間軸。')
    st.caption(f"時間點：{datetime.fromtimestamp(times[idx], HKT):%Y-%m-%d %H:%M:%S}　共 {len(snaps)} 個記錄點")
    return idx

if '_settings_loaded_rebuilt' not in st.session_state:
    for key, value in load_settings().items():
        st.session_state.setdefault(key, value)
    st.session_state['_settings_loaded_rebuilt'] = True

st.markdown(f'<div class="hdr"><div class="hdr-title">🐎 {APP_NAME}</div><span class="live">{APP_VERSION}</span></div>', unsafe_allow_html=True)
_src_col, _sep_col, _mode_col = st.columns([2.4, 0.1, 2])
with _mode_col:
    mode = st.radio('模式', ['● LIVE 即場', '🔁 REPLAY 翻睇'], horizontal=True, key='mode_v19_rebuilt')
replay_mode = 'REPLAY' in mode
with _src_col:
    st.caption('📡 Recorder 歷史記錄' if replay_mode else '📡 直接連線 HKJC · 目標每5秒更新')

c1, c2, c3, c4, c5 = st.columns([2.4, 1, 1, 1.4, 1.4])
if replay_mode:
    saved = list_saved_races()
    if not saved:
        st.info(f'未有 Recorder 歷史記錄：{DATA_DIR}')
        st.stop()
    with c1:
        race_key = st.selectbox('揀場次', saved, index=len(saved)-1, key='selected_replay_race_v19')
    d, course, race_no = race_key.split('|')
    race_no = int(race_no)
    race_date = date.fromisoformat(d)
    with c2:
        st.write(venue_label(course))
    with c3:
        st.write(f'第 {race_no} 場')
else:
    try:
        meetings = current_meetings()
    except Exception as exc:
        meetings = []
        st.warning(f'未取得賽期：{exc}')
    with c1:
        if meetings:
            today = datetime.now(HKT).date().isoformat()
            default = next((i for i, m in enumerate(meetings) if m['date'] >= today), 0)
            mi = st.selectbox('賽事（自動同步馬會）', range(len(meetings)), index=default,
                format_func=lambda i: f"{meetings[i]['date']} · {venue_label(meetings[i]['venue'])} ({meetings[i]['venue']}) · {meetings[i]['n_races']}場")
            meeting = meetings[mi]
            race_date, course = date.fromisoformat(meeting['date']), meeting['venue']
            max_race = int(meeting['n_races'] or 14)
        else:
            race_date = st.date_input('日期', datetime.now(HKT).date())
            course, max_race = 'ST', 14
    with c2:
        if not meetings:
            course = st.selectbox('場地', ['ST', 'HV'])
        else:
            st.write(venue_label(course))
    with c3:
        race_no = st.number_input('場次', 1, max_race, 1)
    race_key = f'{race_date}|{course}|{int(race_no)}'
with c4:
    post_input = st.text_input('開跑時間 (可選)', '', placeholder='HH:MM', key=f'post::{race_key}::{replay_mode}')
with c5:
    reset_clicked = st.button('🔄 重設此場走勢', use_container_width=True)
if reset_clicked:
    st.session_state.pop('_live_buffer::' + race_key, None)
    st.session_state.pop('replay_time::' + race_key, None)
    st.caption('已重設此場畫面；Recorder 歷史保留。')

with st.expander("⚙️ 急升偵測敏感度（分層 · 拉桿微調）"):
    def _clampf(v, lo, hi, fallback):
        """同 _clamp 一樣，但處理浮點數（四池熱度用%）。"""
        try:
            v = float(v)
        except Exception:
            return fallback
        return max(lo, min(hi, v))

    st.markdown('<div style="font-size:11px;color:var(--subtext);margin-bottom:2px">四池熱度（佔比 %）</div>', unsafe_allow_html=True)
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.session_state["rise_t1"] = st.slider(
            "⚡ 留意（%）", 0.2, 2.0,
            _clampf(st.session_state.get("rise_t1", RISE_TIER1), 0.2, 2.0, RISE_TIER1), 0.1)
    with sc2:
        st.session_state["rise_t2"] = st.slider(
            "🔥 明顯（%）", 0.3, 2.5,
            _clampf(st.session_state.get("rise_t2", RISE_TIER2), 0.3, 2.5, RISE_TIER2), 0.1)
    with sc3:
        st.session_state["rise_t3"] = st.slider(
            "💥 強烈（%）", 0.5, 3.0,
            _clampf(st.session_state.get("rise_t3", RISE_TIER3), 0.5, 3.0, RISE_TIER3), 0.1)

    st.markdown('<div style="font-size:11px;color:var(--subtext);margin:8px 0 2px">金額訊號（棒型圖 + 每分鐘金額表）· 千元（輸入數值，撳「儲存設定」先會跨session記住）</div>', unsafe_allow_html=True)

    def _clamp(v, lo, hi, fallback):
        """舊session_state / 舊settings檔可能存住超出新範圍嘅值（例如舊版拉桿
        set過100K，但新⚡上限得50K），直接傳落 number_input 會令 Streamlit 拋
        StreamlitValueAboveMaxError。呢度統一夾返入合法範圍先用。"""
        try:
            v = int(v)
        except Exception:
            return fallback
        return max(lo, min(hi, v))

    mc1, mc2, mc3, mc4 = st.columns([1, 1, 1, 0.9])
    with mc1:
        _mk1 = st.number_input("⚡ 留意（1-50 K）", min_value=1, max_value=50, step=1,
                               value=_clamp(st.session_state.get("money_t1_k", 20), 1, 50, 20))
    with mc2:
        _mk2 = st.number_input("🔥 明顯（51-100 K）", min_value=51, max_value=100, step=1,
                               value=_clamp(st.session_state.get("money_t2_k", 70), 51, 100, 70))
    with mc3:
        _mk3 = st.number_input("💥 強烈（101-400 K）", min_value=101, max_value=400, step=1,
                               value=_clamp(st.session_state.get("money_t3_k", 150), 101, 400, 150))
    with mc4:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("💾 儲存設定", use_container_width=True):
            _ok = save_settings({
                "money_t1_k": _mk1, "money_t2_k": _mk2, "money_t3_k": _mk3,
                "rise_t1": st.session_state.get("rise_t1", RISE_TIER1),
                "rise_t2": st.session_state.get("rise_t2", RISE_TIER2),
                "rise_t3": st.session_state.get("rise_t3", RISE_TIER3),
            })
            st.session_state["_settings_saved_at"] = datetime.now(HKT).strftime("%H:%M:%S")
            st.session_state["_settings_save_ok"] = _ok

    # 呢3個數值即刻生效（唔使撳儲存都會即場用到），儲存淨係影響「下次開app」嘅預設值
    st.session_state["money_t1_k"] = _mk1
    st.session_state["money_t2_k"] = _mk2
    st.session_state["money_t3_k"] = _mk3
    st.session_state["money_t1"] = _mk1 * 1000
    st.session_state["money_t2"] = _mk2 * 1000
    st.session_state["money_t3"] = _mk3 * 1000

    if st.session_state.get("_settings_saved_at"):
        _status = "已儲存" if st.session_state.get("_settings_save_ok") else "⚠️ 儲存失敗（check硬碟權限）"
        st.caption(f"{_status}：{st.session_state['_settings_saved_at']}　· 下次開app會自動讀返呢啲數值")

    st.caption("四池熱度用佔比%；棒型圖+金額表用實質金額（近1分鐘估算落注），兩個金額表完美同步。低＝多提示、高＝少但精。")


thresholds = tuple(st.session_state.get(f'rise_t{i}', default) for i, default in enumerate((RISE_TIER1, RISE_TIER2, RISE_TIER3), 1))
if not thresholds[0] < thresholds[1] < thresholds[2]:
    st.error('敏感度須依次遞增：⚡ < 🔥 < 💥')
    st.stop()
archive = load_snapshots(race_key)
if st.session_state.get('_archive_error'):
    st.warning('已略過無法讀取或場次不符嘅記錄：' + st.session_state['_archive_error'])
# Keep the controls in their original position above the bar charts, but execute
# them now, before every dependent panel is calculated.
upper_panels = st.container()
timeline_slot = st.empty()
prior_idx = min(int(st.session_state.get(f'replay_time::{race_key}', len(archive)-1)), len(archive)-1)
post_ts = archive[prior_idx].get('post_time') if archive else None
if post_input.strip():
    try:
        hh, mm = map(int, post_input.split(':'))
        post_ts = datetime.combine(race_date, datetime.min.time(), tzinfo=HKT).replace(hour=hh, minute=mm).timestamp()
    except ValueError:
        st.error('開跑時間請用 HH:MM')
        st.stop()
if replay_mode:
    if not archive:
        st.info('所選場次冇有效 Recorder 記錄。')
        st.stop()
    with timeline_slot.container():
        replay_idx = replay_controls(archive, race_key, post_ts)
    snap = archive[replay_idx]
    all_snaps = archive[:replay_idx + 1]
    # Metadata must come from the selected time, never a later meeting/API.
    if not post_input.strip():
        post_ts = snap.get('post_time')
else:
    try:
        snap = fetch_live_snapshot(race_key)
    except Exception as exc:
        st.error(f'即時資料更新失敗：{exc}。今次不顯示舊資料作為最新報價。')
        st_autorefresh(interval=5000, key='live_refresh_v19_rebuilt')
        st.stop()
    buffer_key = '_live_buffer::' + race_key
    buffer = st.session_state.setdefault(buffer_key, [])
    buffer.append(snap)
    # Only bridge gaps since the Recorder's latest capture; do not mix duplicate feeds.
    last_recorded = archive[-1]['ts'] if archive else -float('inf')
    buffer[:] = [s for s in buffer if s['ts'] > last_recorded]
    all_snaps = sorted(archive + buffer, key=lambda s: s['ts'])
    if not post_input.strip():
        post_ts = snap.get('post_time')

ACTIVE_RACE_KEY = race_key
ACTIVE_NOW_TS = float(snap['ts'])
ACTIVE_POST_TIME = datetime.fromtimestamp(post_ts, HKT) if post_ts else None
S = make_history(all_snaps, ACTIVE_NOW_TS, thresholds)
S['race_key'], S['post_time'] = race_key, ACTIVE_POST_TIME
S['started_at'] = datetime.fromtimestamp(all_snaps[0]['ts'], HKT)
rows = [{'池': code, '馬號': str(h), '賠率': _to_float(o), '大熱': False}
        for code in ('WIN', 'PLA') for h, o in (snap.get(code.lower()) or {}).items() if _to_float(o) > 0]
if not rows:
    st.info('呢個記錄點冇有效賠率。')
    st.stop()
df, mtp = enrich(pd.DataFrame(rows), S, as_of_ts=ACTIVE_NOW_TS, record=False)
mtp = (ACTIVE_NOW_TS - post_ts) / 60 if post_ts else None
pools = snap.get('pool') or {}
win_inv, pla_inv, this_inv = pools.get('WIN'), pools.get('PLA'), pools
shares = pool_shares(snap)
pla_part, qin_part, qpl_part = shares['PLA'], shares['QIN'], shares['QPL']
qin_matrix = {tuple(map(int, k.split(','))): v for k, v in (snap.get('qin') or {}).items()}
qpl_matrix = {tuple(map(int, k.split(','))): v for k, v in (snap.get('qpl') or {}).items()}
_ri = snap.get('race_info')
if not race_info_matches(_ri, race_key):
    _ri = None
with upper_panels:
    if mtp is not None:
        if mtp < 0:
            countdown = f"距離開跑 {abs(mtp)*60:.0f} 秒" if mtp >= -1 else f"距離開跑 {abs(mtp):.0f} 分鐘"
        else:
            countdown = '已到／超過預定開跑時間'
        st.caption(countdown)
    alerts = []
    for _, row in df[df['池'] == 'WIN'].iterrows():
        pct, _ = recent_speed(S, ('WIN', row['馬號']), PLUNGE_WINDOW)
        if pct >= PLUNGE_PCT:
            alerts.append((row['馬號'], pct, row['即場']))
    for no, pct, odds in sorted(alerts, key=lambda a: a[1], reverse=True)[:3]:
        st.markdown(f'<div class="alert-bar">⚠️ 插水警示　{no} 號於 {PLUNGE_WINDOW} 秒內急跌 {pct:.0f}%，即場 {odds:.1f}</div>', unsafe_allow_html=True)
    st.markdown(f'**{race_date} {venue_label(course)} · 第 {int(race_no)} 場**')
    st.caption(f"{'REPLAY' if replay_mode else 'LIVE'} 資料時間：{datetime.fromtimestamp(ACTIVE_NOW_TS, HKT):%Y-%m-%d %H:%M:%S}"
               + (f"　開跑時間：{ACTIVE_POST_TIME:%H:%M}" if ACTIVE_POST_TIME else ''))
    if _ri:
        st.caption(' · '.join(str(_ri.get(k)) for k in ('name', 'cls', 'dist', 'track', 'going') if _ri.get(k)))
    else:
        st.info('呢筆舊記錄冇保存相符歷史馬名／排位；保留馬號及投注歷史，模型暫不計算。新 Recorder 會保存排位。')

    # ═══ MODEL 即時計算：基本面 + HKJC 即時獨贏賠率 ═══
    st.markdown(
        '<div class="panel-title" style="margin-top:4px">🧠 V19 即時量化模型</div>'
        '<div class="panel-sub">Benter 第二階：歷史基本面勝率 × 馬會即時獨贏市場概率；每 5 秒隨賠率更新</div>',
        unsafe_allow_html=True)
    _rebate_label = st.radio(
        "EV 回扣設定", ["散戶／無回扣", "獨贏合資格回扣 10%"],
        horizontal=True, key="model_rebate_mode",
        help="10% 回扣只適用於符合馬會門檻的輸注；一般小額投注請選無回扣。")
    _rebate_rate = 0.10 if "10%" in _rebate_label else 0.0
    _live_win_odds = {
        str(r["馬號"]): float(r["即場"])
        for _, r in df[df["池"] == "WIN"].iterrows()
        if _to_float(r.get("即場")) > 0
    }
    _model_rows = (_ri or {}).get("card_rows") or []
    _model_df, _model_err = score_live_model(_model_rows, _live_win_odds, _rebate_rate)
    if not _model_df.empty:
        _shown = _model_df[["horse_key", "horse_name", "win_odds", "p_model",
                            "p_public", "p_final", "overlay_pct", "fair_odds",
                            "ev", "value", "p_place"]].copy()
        _shown.columns = ["馬號", "馬名", "即時賠率", "基本面勝率", "市場勝率",
                          "綜合勝率", "直博率", "Fair Odds", "EV", "預期回報", "位置概率"]
        _shown["馬號"] = _shown["馬號"].astype(str)
        for _c in ("基本面勝率", "市場勝率", "綜合勝率", "位置概率"):
            _shown[_c] = _shown[_c].map(lambda x: f"{x:.2%}")
        _shown["直博率"] = _shown["直博率"].map(lambda x: f"{x:+.1f}%")
        _shown["Fair Odds"] = _shown["Fair Odds"].map(lambda x: f"{x:.2f}")
        _shown["即時賠率"] = _shown["即時賠率"].map(lambda x: f"{x:.1f}")
        _shown["EV"] = _shown["EV"].map(lambda x: f"{x:.3f}")
        _shown["預期回報"] = _shown["預期回報"].map(lambda x: f"{x:+.1%}")
        st.dataframe(_shown, hide_index=True, use_container_width=True)
        _value_count = int((_model_df["ev"] > 1.0).sum())
        st.caption(
            f"即時辨識 {_value_count} 匹 EV > 1；直博率 = 綜合勝率 ÷ 市場勝率 − 1。"
            "綜合勝率及位置概率為模型估算，並非保證結果。")
    else:
        st.info(f"模型等待資料：{_model_err or '排位或獨贏賠率尚未齊全'}")



    # Four pool totals and original pair matrices.
    def total_card(label, value):
        return f'<div class="panel" style="flex:1"><div class="panel-sub">{label}</div><b>{_fmt_money(value)}</b></div>'
    st.markdown('<div style="display:flex;gap:8px">' + ''.join(total_card(c, pools.get(c)) for c in ('WIN', 'PLA', 'QIN', 'QPL')) + '</div>', unsafe_allow_html=True)
    race_horses = sorted(int(h) for h in snap.get('win', {}))
    qcol1, qcol2 = st.columns(2)
    with qcol1:
        combo_matrix_panel(qin_matrix, '連贏 QIN', race_horses)
    with qcol2:
        combo_matrix_panel(qpl_matrix, '位置Q QPL', race_horses)
    hcol1, hcol2 = st.columns([1.3, 1])
    with hcol1:
        four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S, pools, cold_odds=10.0)
    with hcol2:
        signal_summary_panel(S['signal_log'], minutes=30, as_of_ts=ACTIVE_NOW_TS)
    if replay_mode:
        st.caption('歷史熱度與訊號按目前敏感度重算；模型使用現有模型及該筆歷史排位重算。')
    bar_sort = st.radio('棒型圖排序', ['順馬號', '順賠率（熱→冷）'], horizontal=True, key='bar_sort')
    sort_key = '賠率' if '賠率' in bar_sort else '馬號'
# The placeholder is visually placed here; its controls were evaluated earlier.
# Upper panels render in the container reserved before this timeline.
_m1, _m2, _m3 = (st.session_state[f'money_t{i}'] for i in (1, 2, 3))
start_clock = datetime.fromtimestamp(ACTIVE_NOW_TS - 60, HKT).strftime('%H:%M:%S')
end_clock = datetime.fromtimestamp(ACTIVE_NOW_TS, HKT).strftime('%H:%M:%S')
st.caption(f'最新1分鐘目標區間：{start_clock} → {end_clock}；逐馬金額為賠率佔比估算，缺少基準時顯示空白。')
baselines = [sample_at(points, ACTIVE_NOW_TS - 60) for points in S['stake_hist'].values()]
baselines = [p[0] for p in baselines if p and ACTIVE_NOW_TS - 60 - p[0] <= 45]
if baselines:
    lo, hi = min(baselines), max(baselines)
    label = datetime.fromtimestamp(lo, HKT).strftime('%H:%M:%S')
    if hi != lo:
        label += ' 至 ' + datetime.fromtimestamp(hi, HKT).strftime('%H:%M:%S')
    st.caption(f'實際可用基準記錄：{label}；終點：{end_clock}。舊30秒記錄可能較目標區間長。')
bcol1, bcol2 = st.columns(2)
with bcol1:
    stake_bar_chart_v(df[df['池'] == 'WIN'], '獨贏', win_inv, S, sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
with bcol2:
    stake_bar_chart_v(df[df['池'] == 'PLA'], '位置', pla_inv, S, sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
for code in ('WIN', 'PLA'):
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool=code, m1=_m1, m2=_m2, m3=_m3, as_of_ts=ACTIVE_NOW_TS)
st.caption(f'{APP_NAME} {APP_VERSION} · 歷史資料由獨立 Recorder 記錄')

if not replay_mode:
    st_autorefresh(interval=5000, key="live_refresh_v19_rebuilt")
