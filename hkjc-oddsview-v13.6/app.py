
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

APP_VERSION = "v17.0 STHV"
APP_NAME = "HKJC 即時賠率監察"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# ════════════════════════════════════════════════════════════
#  DISK STORAGE (永久儲存 — 寫落硬碟，重啟唔失)
# ════════════════════════════════════════════════════════════
# 每場一個資料夾，每個時間點一個 JSON snapshot（每 30 秒一次）。
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
SNAPSHOT_INTERVAL = 30  # 秒，每隔幾耐存一個 snapshot

def _race_dir(race_key):
    # encode | as __ and keep the rest; date dashes stay as-is (reversible)
    safe = race_key.replace("|", "__")
    return os.path.join(DATA_DIR, safe)

def save_snapshot(race_key, snapshot):
    """Write one timepoint snapshot to disk as JSON. snapshot is a dict."""
    try:
        rd = _race_dir(race_key)
        os.makedirs(rd, exist_ok=True)
        ts = snapshot.get("ts", datetime.now().timestamp())
        fn = os.path.join(rd, f"{int(ts)}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        return True
    except Exception:
        return False

def list_saved_races():
    """Return list of race_key strings that have saved snapshots on disk."""
    try:
        if not os.path.isdir(DATA_DIR):
            return []
        out = []
        for d in os.listdir(DATA_DIR):
            full = os.path.join(DATA_DIR, d)
            if os.path.isdir(full) and glob.glob(os.path.join(full, "*.json")):
                out.append(d.replace("__", "|"))
        return sorted(out)
    except Exception:
        return []

def load_snapshots(race_key):
    """Load all snapshots for a race, sorted by ts. Returns list of dicts."""
    try:
        rd = _race_dir(race_key)
        files = sorted(glob.glob(os.path.join(rd, "*.json")), key=lambda p: int(os.path.basename(p)[:-5]))
        snaps = []
        for fn in files:
            try:
                with open(fn, encoding="utf-8") as f:
                    snaps.append(json.load(f))
            except Exception:
                continue
        return snaps
    except Exception:
        return []

def load_latest_snapshot(race_key):
    """讀該場最新一個 snapshot（LIVE 讀硬碟用，快、唔使拉 HKJC）。"""
    try:
        rd = _race_dir(race_key)
        files = glob.glob(os.path.join(rd, "*.json"))
        if not files:
            return None
        latest = max(files, key=lambda p: int(os.path.basename(p)[:-5]))
        with open(latest, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

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
        "share_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts,share%)]
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

def fetch_race_info(date_str, venue, race_no):
    """#9：攞某場賽事資料（班次/距離/跑道/名稱/開跑時間），用嚟做 header。"""
    try:
        payload = {"operationName": "raceMeetings",
                   "variables": {"date": date_str, "venueCode": venue},
                   "query": TURNOVER_QUERY}
        r = requests.post(API, headers=HEADERS, json=payload, timeout=20)
        j = r.json()
        if isinstance(j, dict) and j.get("errors"):
            return None
        meetings = (j.get("data", {}) or {}).get("raceMeetings", []) or []
        if not meetings:
            return None
        m = meetings[0]
        info = {"venue": m.get("venueCode"), "date": (m.get("date") or "")[:10],
                "dow": m.get("dateOfWeek"), "total": m.get("totalNumberOfRace")}
        for rc in (m.get("races") or []):
            if rc.get("no") == int(race_no):
                track = (rc.get("raceTrack") or {}).get("description_ch")
                course_d = (rc.get("raceCourse") or {}).get("description_ch")
                info.update({
                    "no": rc.get("no"),
                    "name": rc.get("raceName_ch"),
                    "post": rc.get("postTime"),
                    "dist": rc.get("distance"),
                    "cls": rc.get("raceClass_ch"),
                    "track": track, "course": course_d,
                    "going": rc.get("go_ch"),
                    "field": rc.get("wageringFieldSize"),
                })
                break
        return info
    except Exception:
        return None

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
            if otype not in ("WIN", "PLA", "QIN", "QPL"):
                continue
            inv = _to_float(p.get("investment"))
            races = (p.get("leg") or {}).get("races") or []
            for rno in races:
                out.setdefault(int(rno), {})[otype] = inv
    return out

def fetch_combo(date_str, course, race_no):
    """Fetch QIN (連贏) + QPL (位置Q) odds. Same verbatim query, only the
    oddsTypes VARIABLE changes (query string unchanged -> no whitelist risk).
    Returns the raw pmPools list for combination pools."""
    payload = {"operationName": "racing",
               "variables": {"date": date_str, "venueCode": course,
                             "raceNo": race_no, "oddsTypes": ["QIN", "QPL"]},
               "query": RACING_QUERY}
    try:
        r = requests.post(API, headers=HEADERS, json=payload, timeout=20)
        j = r.json()
        if isinstance(j, dict) and j.get("errors"):
            return []
        meetings = j.get("data", {}).get("raceMeetings", []) or []
        for m in meetings:
            if m.get("pmPools"):
                return m["pmPools"]
    except Exception:
        return []
    return []

def combo_to_matrix(pools, pool_type):
    """Parse a combination pool (QIN/QPL) into {(a,b): odds} with a<b (ints),
    plus a per-horse participation sum {horse: total 1/odds across its pairs}.
    combString for pairs looks like '3,7' or '3-7'."""
    matrix = {}
    for p in pools:
        if p.get("oddsType") != pool_type:
            continue
        for n in p.get("oddsNodes", []):
            comb = n.get("combString") or ""
            odds = _to_float(n.get("oddsValue"))
            if odds <= 0:
                continue
            parts = comb.replace("-", ",").split(",")
            if len(parts) != 2:
                continue
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if a > b:
                a, b = b, a
            matrix[(a, b)] = odds
    return matrix

def combo_participation(matrix):
    """Per-horse participation in a combo pool = sum of 1/odds over all pairs
    containing that horse. Higher = more money concentrated on that horse's
    combinations. Returns {horse:int -> share% within pool}."""
    raw = {}
    for (a, b), odds in matrix.items():
        if odds <= 0:
            continue
        inv = 1.0 / odds
        raw[a] = raw.get(a, 0.0) + inv
        raw[b] = raw.get(b, 0.0) + inv
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {h: v / total * 100.0 for h, v in raw.items()}

def record_share(S, pool, horse, share_pct):
    """Record a horse's pool share% at current time for rise detection."""
    S["share_hist"][(pool, str(horse))].append((datetime.now(HKT).timestamp(), share_pct))

def share_rise(S, pool, horse, seconds=60):
    """% points the horse's share rose over the last N seconds (default 1 min)."""
    hist = S["share_hist"][(pool, str(horse))]
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
    return last_v - base_v   # positive = share grew

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
            odds = _to_float(n.get("oddsValue"))
            # Skip scratched / withdrawn runners: HKJC returns them with a
            # non-numeric or 0 oddsValue (e.g. "SCR", "---", "0"). A runner
            # still in the race always has a positive win/place odds once the
            # pool is selling, so odds <= 0 means it should not be shown.
            if odds <= 0:
                continue
            rows.append({"池": pool_type,
                         "馬號": n.get("combString"),
                         "賠率": odds,
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

def stake_at_ts(S, pool_name, horse, target_ts):
    """該馬喺 target_ts（或之前最接近）嘅累積投注額（佔比法）。冇就 None。"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    if not hist:
        return None
    best = None
    for ts, v in hist:
        if ts <= target_ts:
            best = v
    return best if best is not None else hist[0][1]

def stake_in_bucket(S, pool_name, horse, ts_start, ts_end):
    """該馬喺 [ts_start, ts_end] 呢段流入嘅金額 = end 累積 − start 累積。
    佔比法，同棒型圖同一把尺。"""
    s_end = stake_at_ts(S, pool_name, horse, ts_end)
    s_start = stake_at_ts(S, pool_name, horse, ts_start)
    if s_end is None or s_start is None:
        return None
    return s_end - s_start

def current_stake(S, pool_name, horse):
    """該馬即場累積總投注額（佔比法，= 棒型圖棒高 = 每分鐘表合計）。"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    return hist[-1][1] if hist else None

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
    sub = df_pool[df_pool["即場"] > 0].copy()
    if sub.empty:
        st.markdown(
            f'<div class="panel"><div class="panel-title">💰 {pool_name}資金流向</div>'
            f'<div class="panel-sub">暫無資料</div></div>', unsafe_allow_html=True)
        return
    # open-odds fallback: if 開賠 somehow 0/missing, use live so the horse still shows
    sub["開賠"] = sub.apply(lambda r: r["開賠"] if r["開賠"] > 0 else r["即場"], axis=1)

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
                      m1=100_000, m2=200_000, m3=400_000, mtp=None):
    """直向棒型圖：棒高＝總投注額（佔比法，= 每分鐘表合計）。
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
    now_ts = datetime.now(HKT).timestamp()
    m_end, m_start = now_ts, now_ts - 60

    # Y軸刻度 HTML（絕對定位喺左邊）
    yaxis = ""
    t = 0
    while t <= top:
        frac = t / top if top else 0
        if t >= 1_000_000:
            ylbl = f"{t/1_000_000:.1f}M"
        elif t > 0:
            ylbl = f"{int(t/1000)}K"
        else:
            ylbl = "0"
        yaxis += (f'<div style="position:absolute;left:0;right:0;bottom:{frac*100:.1f}%;'
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
        inflow = stake_in_bucket(S, pool_code, horse, m_start, m_end) if S is not None else None
        inflow = inflow or 0.0
        if inflow >= m3:
            bcol = "#c878ff"
        elif inflow >= m2:
            bcol = "#ff8c3c"
        elif inflow >= m1:
            bcol = "#ffd43b"
        else:
            bcol = INFO
        h_pct = max(1.5, stake / top * 100) if top > 0 else 1.5
        inflow_lbl = (f'<div style="font-size:8px;color:{bcol};height:12px;text-align:center;white-space:nowrap">'
                      f'{("+"+_fmt_money(inflow)) if inflow>=m1 else ""}</div>')
        bars += (
            f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;min-width:0">'
            f'{inflow_lbl}'
            f'<div style="width:70%;height:{h_pct:.1f}%;background:{bcol};border-radius:3px 3px 0 0;'
            f'min-height:2px;opacity:0.9"></div>'
            f'<div style="font-size:10px;color:var(--subtext);margin-top:3px;font-family:JetBrains Mono,monospace;line-height:1.1;text-align:center">'
            f'{horse}<br><span style="font-size:8px;color:var(--muted)">{odds:g}</span></div>'
            f'</div>'
        )

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">📊 {pool_name}投注額棒型圖</div>'
        f'<div class="panel-sub">棒高＝總投注金額（Y軸自動刻度）· 近1分鐘流入 ⚡{_fmt_money(m1)}黃/🔥{_fmt_money(m2)}橙/💥{_fmt_money(m3)}紫 變色（與金額表同步）</div>'
        f'<div style="display:flex;gap:6px">'
        f'<div style="position:relative;width:150px;height:160px;flex:1;padding-left:30px">'
        f'<div style="position:absolute;left:30px;right:0;top:0;bottom:20px">{yaxis}</div>'
        f'<div style="display:flex;align-items:flex-end;gap:3px;height:100%;position:relative">{bars}</div>'
        f'</div></div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="WIN", n_min=9,
                       m1=100_000, m2=200_000, m3=400_000):
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

    # Build non-linear timeline buckets (left=早段 -> right=開跑).
    post_ts = S["post_time"].timestamp() if S["post_time"] else None
    now_ts = datetime.now(HKT).timestamp()

    # 每格定義：(label, is_countdown, minutes_before_post_for_END_edge)
    # 由早到遲（左到右）。臨場逐分鐘、中段中疏、早段每2鐘（用實際時間）。
    # bucket i 覆蓋 [edge[i-1], edge[i]] 段（累積差）。
    # edges 用「開跑前幾多分鐘」表示（越大越早）。
    minute_edges = [60, 30, 15, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]  # 中段+臨場（分鐘）
    # 早段：由開賣到 -60分，每 120 分鐘一格（實際時間顯示）
    early_edges = []
    if post_ts is not None:
        # 由 -60分 再往早，每 120 分一格，去到「有記錄嘅最早時間」
        earliest = S["stake_hist"] and min(
            (h[0][0] for h in S["stake_hist"].values() if h), default=now_ts)
        earliest_min_before = (post_ts - earliest) / 60.0 if earliest else 60
        e = 180
        while e <= earliest_min_before + 120 and e <= 24 * 60:
            early_edges.append(e)
            e += 120
        early_edges = sorted(set(early_edges), reverse=True)  # 大到細（早到遲）

    # 完整 edge 序列（早 -> 遲）：early(大) ... 60,30,...,0
    all_edges = early_edges + minute_edges  # e.g. [ ...300,180, 60,30,15,10,9..0 ]

    def edge_ts(min_before):
        return post_ts - min_before * 60 if post_ts is not None else None

    def label_for(min_before, is_first_early):
        if min_before <= 0:
            return ("開跑", False, False)
        if min_before in (60, 30, 15, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1):
            return (f"-{min_before}分", False, False)
        # 早段：用實際時間
        ts = edge_ts(min_before)
        dt = datetime.fromtimestamp(ts, HKT)
        hhmm = dt.strftime("%H:%M")
        # 「昨」= 唔同開跑日
        post_dt = datetime.fromtimestamp(post_ts, HKT)
        is_prev = dt.date() < post_dt.date()
        return (hhmm, True, is_prev)

    # 方案C（#6）：早段（開賣→60分前）預設摺埋一格；剔「展開早段」先細分。
    show_early = st.session_state.get(f"early_open_{pool}", False)

    # columns = each bucket between consecutive edges (start=older, end=newer)
    cols = []   # list of (label, is_hour, is_prev, ts_start, ts_end)
    if show_early:
        # 展開：早段逐格（實際時間）+ 臨場
        prev_edge = None
        for idx, mb in enumerate(all_edges):
            e_end = edge_ts(mb)
            e_start = None if prev_edge is None else edge_ts(prev_edge)
            lbl, is_hour, is_prev = label_for(mb, idx == 0)
            cols.append((lbl, is_hour, is_prev, e_start, e_end))
            prev_edge = mb
    else:
        # 摺埋：早段一格（由最早 → -60分），之後臨場逐格
        cols.append(("開賣→60分前", True, False, None, edge_ts(60)))
        prev_edge = 60
        for mb in minute_edges:
            if mb >= 60:
                continue
            e_end = edge_ts(mb)
            e_start = edge_ts(prev_edge)
            lbl, is_hour, is_prev = label_for(mb, False)
            cols.append((lbl, is_hour, is_prev, e_start, e_end))
            prev_edge = mb

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
        return stake_in_bucket(S, pool, horse, ts_start, ts_end)

    rows_data = []
    for _, r in sub.sort_values("即場").iterrows():
        horse = str(r["馬號"])
        odds = r["即場"]
        per_col = [stake_bucket(horse, s, e) for (_, _, _, s, e) in cols]
        rows_data.append((horse, odds, per_col))

    def cellcol(v, is_hour):
        if v is None:
            return "var(--muted)"
        if is_hour:
            return "var(--subtext)"   # 早段唔變色
        if v >= m3: return "#c878ff"
        if v >= m2: return "#ff8c3c"
        if v >= m1: return "#ffd43b"
        return "var(--subtext)"

    # header
    head = '<th style="text-align:left;padding:3px 5px;font-size:9px;color:var(--muted);position:sticky;left:0;background:var(--card)">馬 賠</th>'
    for (lbl, is_hour, is_prev, _, _) in cols:
        prev_tag = '<div style="font-size:7px;color:#78899a;line-height:1">昨</div>' if is_prev else ''
        col_bg = "background:rgba(30,30,44,0.5);" if is_hour else ""
        head += (f'<th style="text-align:right;padding:2px 5px;font-size:9px;color:{"#78899a" if is_hour else "var(--muted)"};{col_bg}">'
                 f'{prev_tag}{lbl}</th>')
    head += '<th style="text-align:right;padding:3px 5px;font-size:9px;color:#e0a83c">合計</th>'

    body = ""
    for horse, odds, per_col in rows_data:
        # 合計 = 即場總投注（佔比法，同棒型圖棒高一致）
        total = current_stake(S, pool, horse)
        if total is None:
            total = sum(v for v in per_col if v) or 0
        cells = ""
        for (lbl, is_hour, is_prev, _, _), v in zip(cols, per_col):
            txt = f'+{_fmt_money(v)}' if (v and v > 0) else ('—' if not v else _fmt_money(v))
            bg = ''
            if not is_hour and v:
                if v >= m3: bg = 'background:rgba(200,120,255,0.15);'
                elif v >= m2: bg = 'background:rgba(255,140,60,0.15);'
                elif v >= m1: bg = 'background:rgba(255,212,59,0.12);'
            cells += f'<td style="text-align:right;padding:2px 5px;font-size:10px;color:{cellcol(v,is_hour)};{bg}font-family:JetBrains Mono,monospace">{txt}</td>'
        body += (f'<tr><td style="padding:3px 5px;font-size:11px;color:var(--text);white-space:nowrap;position:sticky;left:0;background:var(--card)">'
                 f'{horse} <span style="font-size:8px;color:var(--subtext)">{odds:g}</span></td>'
                 f'{cells}'
                 f'<td style="text-align:right;padding:3px 5px;font-size:10px;color:#e0a83c;font-weight:600;font-family:JetBrains Mono,monospace">{_fmt_money(total)}</td></tr>')

    html = (
        f'<div class="panel" style="overflow-x:auto">'
        f'<div class="panel-title">📋 落注金額表（{title} · 時間由左到右）</div>'
        f'<div class="panel-sub">每格＝嗰段流入 · 早段(左·實際時間+昨) → 開跑(最右) · '
        f'臨場逐分鐘 ⚡{_fmt_money(m1)}黃/🔥{_fmt_money(m2)}橙/💥{_fmt_money(m3)}紫（只臨場格變色）· 合計＝總投注（同棒型圖）</div>'
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

    # record share history (for 1-min rise) — all four pools
    for h, v in win_share.items():
        record_share(S, "WIN", h, v)
    for h, v in pla_part.items():
        record_share(S, "PLA", h, v)
    for h, v in qin_part.items():
        record_share(S, "QIN", h, v)
    for h, v in qpl_part.items():
        record_share(S, "QPL", h, v)

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

    # money reference line: what 1% of each pool is worth
    def one_pct(v):
        return _fmt_money(v / 100.0) if v else "—"
    money_ref = (f'獨贏1%≈{one_pct(wt)} · 位置1%≈{one_pct(pt)} · '
                 f'連贏1%≈{one_pct(qt)} · 位置Q1%≈{one_pct(qpt)}（連贏/位置Q為粗估）')

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

# ════════════════════════════════════════════════════════════
#  HEADER + CONTROLS
# ════════════════════════════════════════════════════════════
st.markdown(
    f'<div class="hdr"><div class="hdr-title">🐎 {APP_NAME}</div>'
    f'<span class="live"><span class="live-dot"></span>實時 · 5秒</span></div>',
    unsafe_allow_html=True)

# ── #8：自動同步馬會賽期（列出所有有賽事嘅日期+場地，包括海外）──
if "meetings_cache" not in st.session_state:
    st.session_state.meetings_cache = []
    st.session_state.meetings_cache_ts = None
_mnow = datetime.now(HKT)
if (st.session_state.meetings_cache_ts is None
        or (_mnow - st.session_state.meetings_cache_ts).total_seconds() >= 300):
    try:
        st.session_state.meetings_cache = fetch_all_meetings()
    except Exception:
        st.session_state.meetings_cache = []
    st.session_state.meetings_cache_ts = _mnow
_meetings = st.session_state.meetings_cache or []

c1, c2, c3, c4, c5 = st.columns([2.4, 1, 1, 1.4, 1.4])
with c1:
    if _meetings:
        opts = [f"{m['date']} · {venue_label(m['venue'])} ({m['venue']}) · {m['n_races']}場"
                for m in _meetings]
        # default: 最接近今日嘅賽事
        _today = date.today().isoformat()
        _def = 0
        for i, m in enumerate(_meetings):
            if m["date"] >= _today:
                _def = i
                break
        pick_idx = st.selectbox("賽事（自動同步馬會）", range(len(opts)),
                                format_func=lambda i: opts[i], index=_def)
        _sel = _meetings[pick_idx]
        race_date = datetime.strptime(_sel["date"], "%Y-%m-%d").date()
        course = _sel["venue"]
        _max_race = max(1, _sel["n_races"] or 14)
    else:
        st.warning("暫時攞唔到馬會賽期，用手動揀")
        race_date = st.date_input("日期", date.today())
        course = "ST"
        _max_race = 14
with c2:
    if not _meetings:
        course = st.selectbox("場地", ["ST", "HV"],
                              format_func=lambda x: f"{venue_label(x)} {x}")
    else:
        st.markdown(f'<div style="font-size:9px;color:var(--subtext);margin-top:6px">場地</div>'
                    f'<div style="font-size:14px;color:var(--text);font-weight:600">'
                    f'{venue_label(course)} {course}</div>', unsafe_allow_html=True)
with c3:
    race_no = st.number_input("場次", 1, int(_max_race), 1)
with c4:
    post_input = st.text_input("開跑時間 (可選)", value="", placeholder="自動/可覆蓋",
                               help="讀硬碟模式會自動攞開跑時間。想手動覆蓋先填 HH:MM。")
with c5:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    reset_clicked = st.button("🔄 重設此場走勢", use_container_width=True)

# 敏感度設定（可摺疊，唔阻主畫面）
with st.expander("⚙️ 急升偵測敏感度（分層 · 拉桿微調）"):
    st.markdown('<div style="font-size:11px;color:var(--subtext);margin-bottom:2px">四池熱度（佔比 %）</div>', unsafe_allow_html=True)
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.session_state["rise_t1"] = st.slider(
            "⚡ 留意（%）", 0.2, 2.0, st.session_state.get("rise_t1", RISE_TIER1), 0.1)
    with sc2:
        st.session_state["rise_t2"] = st.slider(
            "🔥 明顯（%）", 0.3, 2.5, st.session_state.get("rise_t2", RISE_TIER2), 0.1)
    with sc3:
        st.session_state["rise_t3"] = st.slider(
            "💥 強烈（%）", 0.5, 3.0, st.session_state.get("rise_t3", RISE_TIER3), 0.1)
    st.markdown('<div style="font-size:11px;color:var(--subtext);margin:8px 0 2px">金額訊號（棒型圖 + 每分鐘金額表）· 千元</div>', unsafe_allow_html=True)
    mc1, mc2, mc3 = st.columns(3)
    with mc1:
        _mk1 = st.slider("⚡ 留意（$K）", 20, 500,
                         st.session_state.get("money_t1_k", MONEY_TIER1 // 1000), 10)
        st.session_state["money_t1_k"] = _mk1
        st.session_state["money_t1"] = _mk1 * 1000
    with mc2:
        _mk2 = st.slider("🔥 明顯（$K）", 50, 800,
                         st.session_state.get("money_t2_k", MONEY_TIER2 // 1000), 10)
        st.session_state["money_t2_k"] = _mk2
        st.session_state["money_t2"] = _mk2 * 1000
    with mc3:
        _mk3 = st.slider("💥 強烈（$K）", 100, 1500,
                         st.session_state.get("money_t3_k", MONEY_TIER3 // 1000), 50)
        st.session_state["money_t3_k"] = _mk3
        st.session_state["money_t3"] = _mk3 * 1000
    st.caption("四池熱度用佔比%；棒型圖+金額表用實質金額（近1分鐘實質落注），兩個金額表完美同步。低＝多提示、高＝少但精。")

# ── 📡 資料來源：讀硬碟（recorder 記錄·快·自動開跑時間）定 直接連線（拉 HKJC）──
st.markdown('<div style="font-size:12px;color:var(--subtext);margin:4px 0 2px">📡 資料來源</div>',
            unsafe_allow_html=True)
data_src = st.radio("資料來源", ["💾 雲端記錄（讀硬碟·快）", "🌐 直接連線（拉HKJC）"],
                    horizontal=True, label_visibility="collapsed", key="data_src")
use_disk = "雲端" in data_src

# ── ⏱️ 翻睇控制（LIVE / REPLAY）— 直接顯示，唔收埋 ──
replay_mode = False
replay_snaps = None
replay_idx = None
st.markdown('<div style="font-size:12px;color:var(--subtext);margin:4px 0 2px">⏱️ 模式</div>',
            unsafe_allow_html=True)
mode = st.radio("模式", ["● LIVE 即場", "🔁 REPLAY 翻睇"], horizontal=True,
                label_visibility="collapsed", key="mode_toggle")
replay_mode = "REPLAY" in mode
if replay_mode:
    saved = list_saved_races()
    if not saved:
        st.info("暫時未有已儲存嘅場次記錄。開住一場（有彩池數據）幾分鐘，佢會每 30 秒自動記低，之後就可以喺呢度揀返翻睇。")
    else:
        rc1, rc2 = st.columns([1, 2])
        with rc1:
            pick = st.selectbox("揀場次", saved, index=len(saved) - 1)
        replay_snaps = load_snapshots(pick)
        if replay_snaps:
            n = len(replay_snaps)
            def _lbl(i):
                s = replay_snaps[i]
                pt = s.get("post_time")
                if pt:
                    mtp = (s["ts"] - pt) / 60.0
                    return f"開跑前 {abs(mtp):.0f} 分" if mtp < 0 else "開跑後"
                return datetime.fromtimestamp(s["ts"], HKT).strftime("%H:%M:%S")
            with rc2:
                replay_idx = st.slider("時間軸（拉去任何一刻）", 0, n - 1, n - 1)
            st.caption(f"時間點：{_lbl(replay_idx)}　（共 {n} 個記錄點，每 30 秒一個）")
        else:
            st.info("呢場冇記錄點")

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
        manual_post = datetime(race_date.year, race_date.month, race_date.day,
                               int(hh), int(mm), 0, tzinfo=HKT)
    except Exception:
        manual_post = None


# ════════════════════════════════════════════════════════════
#  FETCH + PROCESS
# ════════════════════════════════════════════════════════════
# Sliding window: current race + next 2 (capped at race 14)
window = [n for n in range(int(race_no), int(race_no) + 3) if 1 <= n <= 14]

# Fetch + record the *background* races first (not the current one).
# Small spacing between calls avoids hammering HKJC.
# 讀硬碟模式唔使拉 HKJC（recorder 已經背景記緊）
if not use_disk:
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
if use_disk:
    pools = []      # 下面用 disk snapshot 重建
else:
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
if _need and not use_disk:
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

# Fetch QIN (連贏) + QPL (位置Q) odds for this race (only oddsTypes var changes,
# query string unchanged -> whitelist-safe). Kept separate from WIN/PLA flow.
if use_disk:
    combo_pools = []
else:
    try:
        combo_pools = fetch_combo(str(race_date), course, int(race_no))
    except Exception:
        combo_pools = []
qin_matrix = combo_to_matrix(combo_pools, "QIN")
qpl_matrix = combo_to_matrix(combo_pools, "QPL")
qin_part = combo_participation(qin_matrix)
qpl_part = combo_participation(qpl_matrix)

# Post time: AUTO DISABLED (實時開跑時間唔穩定). Manual override only.
# If manual is empty -> post_time None -> elapsed-time axis (no double-line risk).
S["post_time"] = manual_post

df = pools_to_df(pools)

# ── 讀硬碟 LIVE：用最新 snapshot 重建（唔拉 HKJC，快、自動開跑時間）──
if use_disk and not replay_mode:
    _snap = load_latest_snapshot(race_key)
    if _snap:
        rows_d = []
        for h, o in (_snap.get("win") or {}).items():
            rows_d.append({"池": "WIN", "馬號": h, "賠率": float(o), "大熱": False})
        for h, o in (_snap.get("pla") or {}).items():
            rows_d.append({"池": "PLA", "馬號": h, "賠率": float(o), "大熱": False})
        df = pd.DataFrame(rows_d)
        _pp = _snap.get("pool") or {}
        win_inv = _pp.get("WIN"); pla_inv = _pp.get("PLA")
        this_inv = {"WIN": _pp.get("WIN"), "PLA": _pp.get("PLA"),
                    "QIN": _pp.get("QIN"), "QPL": _pp.get("QPL")}
        qin_matrix = {tuple(int(x) for x in k.split(",")): v
                      for k, v in (_snap.get("qin") or {}).items()}
        qpl_matrix = {tuple(int(x) for x in k.split(",")): v
                      for k, v in (_snap.get("qpl") or {}).items()}
        qin_part = combo_participation(qin_matrix)
        qpl_part = combo_participation(qpl_matrix)
        if _snap.get("post_time"):
            S["post_time"] = datetime.fromtimestamp(_snap["post_time"], HKT)  # 自動開跑時間
    else:
        st.info("💾 雲端記錄模式：呢場暫時未有記錄（recorder 開賣後會自動記）。想即刻睇可揀「🌐 直接連線」。")


# ── REPLAY 覆蓋：用揀咗嘅 snapshot 重建數據（唔用即場）──
if replay_mode and replay_snaps and replay_idx is not None:
    snap = replay_snaps[replay_idx]
    rows_r = []
    for h, o in (snap.get("win") or {}).items():
        rows_r.append({"池": "WIN", "馬號": h, "賠率": float(o), "大熱": False})
    for h, o in (snap.get("pla") or {}).items():
        rows_r.append({"池": "PLA", "馬號": h, "賠率": float(o), "大熱": False})
    df = pd.DataFrame(rows_r)
    _p = snap.get("pool") or {}
    win_inv = _p.get("WIN"); pla_inv = _p.get("PLA")
    this_inv = {"WIN": _p.get("WIN"), "PLA": _p.get("PLA"),
                "QIN": _p.get("QIN"), "QPL": _p.get("QPL")}
    qin_matrix = {tuple(int(x) for x in k.split(",")): v for k, v in (snap.get("qin") or {}).items()}
    qpl_matrix = {tuple(int(x) for x in k.split(",")): v for k, v in (snap.get("qpl") or {}).items()}
    qin_part = combo_participation(qin_matrix)
    qpl_part = combo_participation(qpl_matrix)
    if snap.get("post_time"):
        S["post_time"] = datetime.fromtimestamp(snap["post_time"], HKT)

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

    if replay_mode and replay_snaps and replay_idx is not None:
        # REPLAY: rebuild series & stake_hist from snapshots up to replay_idx,
        # so surge / 1-min-rise / per-minute table reflect that historical moment.
        snap_now = replay_snaps[replay_idx]
        S["series"] = defaultdict(lambda: deque(maxlen=400))
        S["stake_hist"] = defaultdict(lambda: deque(maxlen=400))
        S["share_hist"] = defaultdict(lambda: deque(maxlen=400))
        S["open_odds"] = {}
        for si in range(replay_idx + 1):
            sp = replay_snaps[si]
            ts = sp["ts"]
            pinv = sp.get("pool") or {}
            for pool_code, odds_map in (("WIN", sp.get("win")), ("PLA", sp.get("pla"))):
                if not odds_map:
                    continue
                inv_sum = sum(1.0 / float(o) for o in odds_map.values() if float(o) > 0)
                ptot = pinv.get(pool_code)
                for h, o in odds_map.items():
                    o = float(o)
                    if o <= 0:
                        continue
                    key = (pool_code, h)
                    S["series"][key].append((ts, o))
                    if key not in S["open_odds"]:
                        S["open_odds"][key] = o
                    if ptot and inv_sum > 0:
                        share = (1.0 / o) / inv_sum
                        S["stake_hist"][key].append((ts, share * ptot))
                        S["share_hist"][(pool_code, str(h))].append((ts, share * 100.0))
        # mtp from snapshot time vs post
        if snap_now.get("post_time"):
            mtp = (snap_now["ts"] - snap_now["post_time"]) / 60.0
        else:
            mtp = None
    else:
        # Record current stake ($) per horse for surge detection (only when pool known)
        record_stakes(df[df["池"] == "WIN"], "WIN", win_inv, S)
        record_stakes(df[df["池"] == "PLA"], "PLA", pla_inv, S)

        # ── 寫硬碟 snapshot（每 30 秒一次）──
        _snap_key = f"_last_snap_{race_key}"
        _now_epoch = datetime.now(HKT).timestamp()
        _last_snap = st.session_state.get(_snap_key, 0)
        if _now_epoch - _last_snap >= SNAPSHOT_INTERVAL:
            try:
                snap = {
                    "ts": _now_epoch,
                    "race_key": race_key,
                    "post_time": S["post_time"].timestamp() if S["post_time"] else None,
                    "win": {str(r["馬號"]): r["即場"] for _, r in df[df["池"] == "WIN"].iterrows()},
                    "pla": {str(r["馬號"]): r["即場"] for _, r in df[df["池"] == "PLA"].iterrows()},
                    "pool": {"WIN": win_inv, "PLA": pla_inv,
                             "QIN": this_inv.get("QIN"), "QPL": this_inv.get("QPL")},
                    "qin": {f"{a},{b}": o for (a, b), o in qin_matrix.items()},
                    "qpl": {f"{a},{b}": o for (a, b), o in qpl_matrix.items()},
                }
                save_snapshot(race_key, snap)
                st.session_state[_snap_key] = _now_epoch
            except Exception:
                pass

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

    # ═══ #9 賽事資料 header（跟馬會格式）═══
    _rinfo_key = f"rinfo_{race_date}_{course}_{race_no}"
    if _rinfo_key not in st.session_state:
        try:
            st.session_state[_rinfo_key] = fetch_race_info(str(race_date), course, int(race_no))
        except Exception:
            st.session_state[_rinfo_key] = None
    _ri = st.session_state.get(_rinfo_key)
    if _ri and _ri.get("no"):
        _pt = ""
        if _ri.get("post"):
            try:
                _pdt = datetime.fromisoformat(_ri["post"].replace("Z", "+00:00")).astimezone(HKT)
                _pt = _pdt.strftime("%H:%M")
            except Exception:
                _pt = ""
        _bits = [b for b in [
            _pt, _ri.get("cls"), f'{_ri.get("dist")}米' if _ri.get("dist") else None,
            _ri.get("track"), _ri.get("course"),
            f'場地{_ri.get("going")}' if _ri.get("going") else None,
            f'{_ri.get("field")}匹' if _ri.get("field") else None,
        ] if b]
        _rname = _ri.get("name") or ""
        _name_html = (f'<div style="font-size:11px;color:var(--muted);margin-top:2px">{_rname}</div>'
                      if _rname else "")
        st.markdown(
            f'<div style="background:var(--card);border:1px solid var(--border);border-radius:10px;'
            f'padding:8px 14px;margin-bottom:10px">'
            f'<span style="font-size:13px;font-weight:600;color:var(--text)">'
            f'{_ri.get("date","")} {venue_label(_ri.get("venue",""))} · 第 {_ri["no"]} 場</span>'
            f'<span style="font-size:11px;color:var(--subtext);margin-left:10px">'
            f'{" · ".join(_bits)}</span>'
            f'{_name_html}'
            f'</div>', unsafe_allow_html=True)

    # ═══ ① 四彩池投注額 ═══
    if win_inv or pla_inv or this_inv.get("QIN") or this_inv.get("QPL"):
        def _tcard(label, val, accent):
            return (f'<div style="flex:1;background:var(--card);border:1px solid var(--border);'
                    f'border-radius:10px;padding:8px 12px;position:relative;overflow:hidden">'
                    f'<div style="position:absolute;top:0;left:0;right:0;height:2px;background:{accent}"></div>'
                    f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:var(--subtext);'
                    f'letter-spacing:0.06em">{label}</div>'
                    f'<div style="font-size:18px;font-weight:600;color:var(--text);margin-top:2px">'
                    f'{_fmt_money(val)}</div></div>')
        st.markdown(
            f'<div style="display:flex;gap:8px;margin-bottom:10px">'
            f'{_tcard("獨贏 WIN", win_inv, INFO)}'
            f'{_tcard("位置 PLA", pla_inv, "#3b82f6")}'
            f'{_tcard("連贏 QIN", this_inv.get("QIN"), "#e0a83c")}'
            f'{_tcard("位置Q QPL", this_inv.get("QPL"), "#b48c3c")}'
            f'</div>',
            unsafe_allow_html=True)

    # ═══ ② 連贏 / 位置Q 賠率矩陣 ═══
    race_horses = sorted(int(x) for x in df[df["池"] == "WIN"]["馬號"].tolist())
    if qin_matrix or qpl_matrix:
        qcol1, qcol2 = st.columns(2)
        with qcol1:
            combo_matrix_panel(qin_matrix, "連贏 QIN", race_horses)
        with qcol2:
            combo_matrix_panel(qpl_matrix, "位置Q QPL", race_horses)

    # PLA per-horse participation (share% within place pool)
    _pla = df[df["池"] == "PLA"].copy()
    if not _pla.empty:
        _inv = 1.0 / _pla["即場"]
        pla_part = {int(h): v for h, v in zip(_pla["馬號"], _inv / _inv.sum() * 100.0)}
    else:
        pla_part = {}

    # ═══ ③ 四池綜合熱度（分層 ⚡🔥💥）═══
    pool_totals = {"WIN": win_inv, "PLA": pla_inv,
                   "QIN": this_inv.get("QIN"), "QPL": this_inv.get("QPL")}
    rise_thresh = st.session_state.get("rise_thresh", 0.5)
    four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S,
                         pool_totals, cold_odds=10.0, rise_thresh=rise_thresh)

    # ═══ ④ 投注額棒型圖（直向）═══
    _m1 = st.session_state.get("money_t1", MONEY_TIER1)
    _m2 = st.session_state.get("money_t2", MONEY_TIER2)
    _m3 = st.session_state.get("money_t3", MONEY_TIER3)
    bar_sort = st.radio("棒型圖排序", ["順馬號", "順賠率（熱→冷）"], horizontal=True,
                        label_visibility="collapsed", key="bar_sort")
    sort_key = "賠率" if "賠率" in bar_sort else "馬號"
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        stake_bar_chart_v(df[df["池"] == "WIN"], "獨贏", win_inv, S,
                          sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp)
    with bcol2:
        stake_bar_chart_v(df[df["池"] == "PLA"], "位置", pla_inv, S,
                          sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp)

    # ═══ ⑤ 每分鐘落注金額表（獨贏 / 位置）═══
    ec1, ec2 = st.columns(2)
    with ec1:
        st.session_state["early_open_WIN"] = st.checkbox(
            "獨贏：展開早段細分", value=st.session_state.get("early_open_WIN", False))
    with ec2:
        st.session_state["early_open_PLA"] = st.checkbox(
            "位置：展開早段細分", value=st.session_state.get("early_open_PLA", False))
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="WIN", m1=_m1, m2=_m2, m3=_m3)
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="PLA", m1=_m1, m2=_m2, m3=_m3)

    # ── footer ──
    now_str = datetime.now(HKT).strftime("%H:%M:%S")
    st.markdown(
        f'<div style="text-align:center;margin-top:1rem;padding:8px;border-top:1px solid var(--border);'
        f'font-family:JetBrains Mono,monospace;font-size:10px;color:var(--muted)">'
        f'{APP_NAME} {APP_VERSION} · 每 5 秒自動更新 · {now_str} HKT</div>',
        unsafe_allow_html=True)
