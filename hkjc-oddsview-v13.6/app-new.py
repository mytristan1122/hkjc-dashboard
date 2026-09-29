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
import sqlite3
import glob
import sys
import math
import tempfile
import re
from pathlib import Path
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor

STYLE_LABELS = ("æ”¾é ­", "å‰ç½®", "ä¸­ç½®", "å¾Œä¸Š")

def classify_running_style(value, field_size):
    positions = [int(x) for x in re.findall(r"\d+", str(value or "")) if int(x) > 0]
    try:
        field = max(1, int(field_size or max(positions or [1])))
    except (TypeError, ValueError):
        field = max(1, max(positions or [1]))
    if not positions:
        return "æœªçŸ¥"
    early = positions[0]
    lead_cut = max(1, math.ceil(field * 0.25))
    front_cut = max(2, math.ceil(field * 0.50))
    back_cut = max(front_cut + 1, math.ceil(field * 0.75))
    if early <= lead_cut:
        return "æ”¾é ­"
    if early <= front_cut:
        return "å‰ç½®"
    if early >= back_cut:
        return "å¾Œä¸Š"
    return "ä¸­ç½®"

def style_from_history(style_history, horse_no):
    key = str(horse_no)
    if key not in (style_history or {}):
        try:
            key = str(int(float(horse_no)))
        except (TypeError, ValueError):
            pass
    values = list((style_history or {}).get(key, []))
    values = [x for x in values if x in STYLE_LABELS]
    return max(STYLE_LABELS, key=lambda x: values.count(x)) if values else "æœªçŸ¥"


def historical_style_history(card_rows, before_date, same_day_analysis=None):
    """Load each declared horse's prior running styles from model SQLite data."""
    horse_ids = [str(x.get('horse_id')) for x in card_rows if x.get('horse_id')]
    result = defaultdict(list)
    db_path = MODEL_DIR / 'data' / 'races.sqlite'
    if horse_ids and db_path.exists():
        marks = ','.join('?' for _ in horse_ids)
        try:
            uri = db_path.resolve().as_uri() + '?mode=ro'
            with sqlite3.connect(uri, uri=True, timeout=3) as con:
                rows = con.execute(
                    f'''SELECT r.horse_id, ra.race_date, ra.race_no,
                               r.running_position, ra.field_size
                        FROM runs r JOIN races ra ON ra.race_id=r.race_id
                        WHERE r.horse_id IN ({marks}) AND ra.race_date < ?
                          AND r.running_position IS NOT NULL
                          AND TRIM(r.running_position) != ''
                        ORDER BY ra.race_date, ra.race_no''',
                    [*horse_ids, before_date]).fetchall()
            for horse_id, _date, _race, positions, field in rows:
                style = classify_running_style(positions, field)
                if style in STYLE_LABELS:
                    result[str(horse_id)].append(style)
            for key in list(result):
                result[key] = result[key][-12:]
        except (sqlite3.Error, OSError):
            pass
    for horse, styles in ((same_day_analysis or {}).get('style_history') or {}).items():
        # Same-day data is later than career history and must follow it.
        result[str(horse)].extend(x for x in styles if x in STYLE_LABELS)
        result[str(horse)] = result[str(horse)][-12:]
    return dict(result)

APP_VERSION = "V19-R2.2-PACEBIAS-20260929"
APP_NAME = "HKJC å³æ™‚è³ çŽ‡ç›£å¯Ÿ"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  DISK STORAGE (æ°¸ä¹…å„²å­˜ â€” å¯«è½ç¡¬ç¢Ÿï¼Œé‡å•Ÿå””å¤±)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# æ¯å ´ä¸€å€‹è³‡æ–™å¤¾ï¼Œæ¯å€‹æ™‚é–“é»žä¸€å€‹ JSON snapshotï¼ˆç”±ç¨ç«‹ Recorder å¯«å…¥ï¼‰ã€‚
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
# Snapshot writes belong only to the standalone Recorder.

def _race_dir(race_key):
    # encode | as __ and keep the rest; date dashes stay as-is (reversible)
    safe = race_key.replace("|", "__")
    return os.path.join(DATA_DIR, safe)


def load_postrace_analysis(race_key):
    """Read optional post-race analysis; old Recorder folders remain valid."""
    path = Path(_race_dir(race_key)) / "postrace.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def load_day_postrace(date_str, venue, before_race):
    """Merge completed same-day races so the next race sees latest evidence."""
    merged = {"style_history": {}, "style_stats": {}, "draw_stats": {}, "runs": [],
              "completed_races": [], "bias_label": "æ¨£æœ¬ä¸è¶³"}
    try:
        root = Path(DATA_DIR)
        for folder in root.glob(f"{date_str}__{venue}__*"):
            try:
                race_no = int(folder.name.rsplit("__", 1)[1])
            except (TypeError, ValueError):
                continue
            if race_no >= int(before_race):
                continue
            try:
                data = json.loads((folder / "postrace.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not data.get("completed"):
                continue
            merged["completed_races"].append(race_no)
            for horse, values in (data.get("style_history") or {}).items():
                merged["style_history"].setdefault(str(horse), []).extend(values)
            for style, stats in (data.get("style_stats") or {}).items():
                old = merged["style_stats"].setdefault(style, {"n": 0, "top3": 0})
                old["n"] += int(stats.get("n", 0))
                old["top3"] += int(stats.get("top3", 0))
            for draw, stats in (data.get("draw_stats") or {}).items():
                old = merged["draw_stats"].setdefault(str(draw), {"n": 0, "top3": 0})
                old["n"] += int(stats.get("n", 0))
                old["top3"] += int(stats.get("top3", 0))
            merged["runs"].extend(data.get("runs") or [])
        # A day's baseline is three top-three places per completed race, not
        # three places across the concatenated runner rows.
        field = max(1, len(merged["runs"]))
        expected_top3 = min(1.0, (3.0 * len(merged["completed_races"])) / field)
        for stats in merged["style_stats"].values():
            rate = stats["top3"] / stats["n"] if stats["n"] else 0.0
            stats.update(top3_rate=rate, lift=rate / expected_top3 if expected_top3 else 1.0,
                         reliable=stats["n"] >= 4)
        for stats in merged["draw_stats"].values():
            rate = stats["top3"] / stats["n"] if stats["n"] else 0.0
            stats.update(top3_rate=rate, lift=rate / expected_top3 if expected_top3 else 1.0,
                         reliable=stats["n"] >= 4)
        reliable = {k: v for k, v in merged["style_stats"].items() if v.get("reliable")}
        if reliable:
            best = max(reliable, key=lambda k: reliable[k].get("lift", 1.0))
            merged["bias_label"] = f"åˆ©{best}" if best in STYLE_LABELS else "è§€å¯Ÿä¸­"
    except OSError:
        pass
    return merged

















# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  CONFIG / CONSTANTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
API = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": "https://bet.hkjc.com/",
    "User-Agent": "Mozilla/5.0",
}
HKT = timezone(timedelta(hours=8))   # Hong Kong time

INFO = "#185FA5"     # è½é£› (odds down / money in) â€” blue
DANGER = "#A32D2D"   # å›žé£› (odds up / money out) â€” red
MUTE = "#888888"     # å¹³ç©© (flat) â€” grey

FLAT_THRESHOLD = 2.0       # |%| <= this  -> å¹³ç©© (faint grey)
PLUNGE_PCT = 8.0           # drop >= this % within PLUNGE_WINDOW -> æ’æ°´ alert
PLUNGE_WINDOW = 30         # seconds
AXIS_MINUTES = 14          # countdown axis spans -14min -> 0 (post)

# â”€â”€ æŠ•æ³¨é¡æ€¥å‡åµæ¸¬ï¼ˆè³‡é‡‘æµå‘è¡¨ï¼‰â”€â”€
SURGE_WINDOW = 30          # è¿‘ N ç§’
SURGE_MIN_DROP = 4.0       # è¿‘30ç§’è³ çŽ‡è·Œå¹… >= æ­¤ % -> â–² æ€¥è·Œï¼ˆæœ‰éŒ¢å…¥ï¼‰
SURGE_BIG_DROP = 8.0       # è¿‘30ç§’è³ çŽ‡è·Œå¹… >= æ­¤ % -> ðŸ”¥ å¤§é‡æ¹§å…¥

# â”€â”€ å››æ± ç†±åº¦ 1åˆ†é˜ä½”æ¯”å‡å¹… åˆ†å±¤é–€æª»ï¼ˆæ‹‰æ¡¿å¯èª¿ï¼‰â”€â”€
RISE_TIER1 = 0.5   # âš¡ ç•™æ„
RISE_TIER2 = 0.8   # ðŸ”¥ æ˜Žé¡¯
RISE_TIER3 = 1.2   # ðŸ’¥ å¼·çƒˆ

# â”€â”€ æ£’åž‹åœ– + æ¯åˆ†é˜é‡‘é¡è¡¨ çµ±ä¸€ã€Œå¯¦è³ªé‡‘é¡ã€é–€æª»ï¼ˆå¦ä¸€çµ„æ‹‰æ¡¿ï¼‰â”€â”€
MONEY_TIER1 = 100_000   # âš¡ ç•™æ„ï¼ˆ$ï¼‰
MONEY_TIER2 = 200_000   # ðŸ”¥ æ˜Žé¡¯ï¼ˆ$ï¼‰
MONEY_TIER3 = 400_000   # ðŸ’¥ å¼·çƒˆï¼ˆ$ï¼‰

# â”€â”€ ç¶œåˆè©•åˆ†ï¼ˆ100 åˆ†åˆ¶ï¼‰é…ç½® â”€â”€
# å„é …æ»¿åˆ†ï¼šè³‡é‡‘æ€¥å‡ 40 + è³ çŽ‡æ€¥è·Œ 30 + æ°´ä½æˆç†Ÿ 20 + ç¨ä½ä¸€è‡´ 10 = 100
SCORE_SURGE_MAX = 40       # è³‡é‡‘æ€¥å‡ï¼ˆç›¸å°å…¨å ´çªå‡ºç¨‹åº¦ï¼‰
SCORE_DROP_MAX  = 30       # è³ çŽ‡æ€¥è·Œï¼ˆçµ•å°è·Œå¹…ç´šåˆ¥ï¼‰
SCORE_WATER_MAX = 20       # æ°´ä½æˆç†Ÿï¼ˆè¶ŠæŽ¥è¿‘ 1.21 è¶Šé«˜ï¼‰
SCORE_AGREE_MAX = 10       # ç¨è´ï¼ä½ç½®åŒæ™‚æµå…¥
SCORE_DROP_FULL = 10.0     # è³ çŽ‡è·Œ >= æ­¤ % å¾—æ»¿åˆ†ï¼ˆ30ï¼‰
WATER_IDEAL = 1.21         # ç†è«–æˆç†Ÿæ°´ä½
WATER_LOOSE = 1.45         # æ°´ä½ >= æ­¤å€¼ -> 0 åˆ†ï¼ˆæœªæˆç†Ÿï¼‰

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

# Pool investment (turnover) â€” uses HKJC's EXACT official query verbatim.
# Whitelist matches the string literally, so DO NOT modify this string.
TURNOVER_QUERY = "fragment raceFragment on Race {\n  id\n  no\n  status\n  raceName_en\n  raceName_ch\n  postTime\n  country_en\n  country_ch\n  distance\n  wageringFieldSize\n  go_en\n  go_ch\n  ratingType\n  raceTrack {\n    description_en\n    description_ch\n  }\n  raceCourse {\n    description_en\n    description_ch\n    displayCode\n  }\n  claCode\n  raceClass_en\n  raceClass_ch\n  judgeSigns {\n    value_en\n  }\n}\n\nfragment racingBlockFragment on RaceMeeting {\n  jpEsts: pmPools(\n    oddsTypes: [WIN, PLA, TCE, TRI, FF, QTT, DT, TT, SixUP]\n    filters: [\"jackpot\", \"estimatedDividend\"]\n  ) {\n    leg {\n      number\n      races\n    }\n    oddsType\n    jackpot\n    estimatedDividend\n    mergedPoolId\n  }\n  poolInvs: pmPools(\n    oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n  ) {\n    id\n    leg {\n      races\n    }\n  }\n  penetrometerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  hammerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  changeHistories(filters: [\"top3\"]) {\n    type\n    time\n    raceNo\n    runnerNo\n    horseName_ch\n    horseName_en\n    jockeyName_ch\n    jockeyName_en\n    scratchHorseName_ch\n    scratchHorseName_en\n    handicapWeight\n    scrResvIndicator\n  }\n}\n\nquery raceMeetings($date: String, $venueCode: String) {\n  timeOffset {\n    rc\n  }\n  activeMeetings: raceMeetings {\n    id\n    venueCode\n    date\n    status\n    races {\n      no\n      postTime\n      status\n      wageringFieldSize\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      status\n    }\n  }\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    id\n    status\n    venueCode\n    date\n    totalNumberOfRace\n    currentNumberOfRace\n    dateOfWeek\n    meetingType\n    totalInvestment\n    country {\n      code\n      namech\n      nameen\n      seq\n    }\n    races {\n      ...raceFragment\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n    obSt: pmPools(oddsTypes: [WIN, PLA]) {\n      leg {\n        races\n      }\n      oddsType\n      comingleStatus\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      id\n      leg {\n        number\n        races\n      }\n      status\n      sellStatus\n      oddsType\n      investment\n      mergedPoolId\n      lastUpdateTime\n    }\n    ...racingBlockFragment\n    pmPools(oddsTypes: []) {\n      id\n    }\n    jkcInstNo: foPools(oddsTypes: [JKC], filters: [\"top\"]) {\n      instNo\n    }\n    tncInstNo: foPools(oddsTypes: [TNC], filters: [\"top\"]) {\n      instNo\n    }\n  }\n}"

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  STYLES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');
:root {
  --bg:#0b0e14; --surface:#141925; --card:#161b27; --border:#222b3a;
  --text:#e6edf3; --subtext:#9aa7b8; --muted:#5b6675;
}
html, body, .stApp { background:var(--bg)!important; color:var(--text); font-family:'Inter',sans-serif; }
#MainMenu, footer, header { visibility:hidden; }

/* â”€â”€ æ¸›å°‘æ¯ 5 ç§’æ›´æ–°æ™‚å˜…é–ƒå‹•ï¼ˆåˆæš—åˆå…‰ï¼‰â”€â”€ */
/* 1. å›ºå®šèƒŒæ™¯ï¼Œrefresh æ™‚å””æœƒé–ƒç™½ */
.stApp, .main, .block-container { background:var(--bg)!important; }
/* 2. åœç”¨ Streamlit æ¯æ¬¡ rerun å˜…æ·¡å…¥å‹•ç•«ï¼ˆå°±ä¿‚ã€Œåˆæš—åˆå…‰ã€ä¸»å› ï¼‰ */
.stApp [data-testid="stAppViewContainer"] * { animation:none!important; }
.element-container, .stMarkdown { transition:none!important; animation:none!important; }
[data-testid="stAppViewBlockContainer"] { opacity:1!important; }
/* 3. æ›´æ–°æ™‚å˜…ã€Œrunningã€åŠé€æ˜Žé®ç½©ï¼Œä»¤ä½¢å””æœƒä»¤å…¨é è®Šæš— */
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

/* â”€â”€ st.container(border=True) æ”¹è¿”è·Ÿ .panel ä¸€æ¨£å˜…æ·±è‰²é¢¨æ ¼ï¼ˆå³é‚Šè¨Šè™Ÿå½™ç¸½ç”¨ï¼‰â”€â”€ */
[data-testid="stVerticalBlockBorderWrapper"] {
  background:var(--card) !important; border:1px solid var(--border) !important;
  border-radius:12px !important; padding:2px 14px 14px !important;
}
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stExpander"] {
  background:transparent; border:1px solid var(--border); border-radius:8px; margin-bottom:4px;
}

/* â”€â”€ â‘¢å››æ± ç†±åº¦ + 30åˆ†é˜è¨Šè™Ÿå½™ç¸½ï¼šå…©å€‹column stretchåŽ»åˆ°ä¸€æ¨£é«˜ â”€â”€ */
.st-key-heat_signal_row [data-testid="stHorizontalBlock"] { align-items:stretch; }
.st-key-heat_signal_row [data-testid="column"] > div { height:100%; }
.st-key-heat_signal_row .panel { height:100%; box-sizing:border-box; }
.st-key-heat_signal_row [data-testid="stVerticalBlockBorderWrapper"] { height:100%; box-sizing:border-box; }

/* â”€â”€ æ‰‹æ©Ÿå„ªåŒ–ï¼ˆçª„èž¢å¹•ï¼‰â”€â”€ */
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
  /* è®“å·¦å³å…©æ¬„ï¼ˆç¨è´/ä½ç½®ï¼‰å–ºæ‰‹æ©Ÿç›´æŽ¥ä¸Šä¸‹æŽ’ */
  [data-testid="column"] { width:100% !important; flex:1 1 100% !important;
    min-width:100% !important; }
}
</style>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
""", unsafe_allow_html=True)



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  SESSION STATE (multi-race: each race keeps its own history)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
def _blank_state():
    return {
        "race_key": None,
        "open_odds": {},      # (pool, horse) -> first odds seen
        "last_odds": {},      # (pool, horse) -> previous odds
        "series": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(x_min, odds)]
        "stake_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts_epoch, stake$)]
        "ever_surged": {},    # (pool,horse) -> peak drop% ever seen (for ðŸ”¥ memory)
        "share_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts,share%)]
        "post_time": None,    # datetime in HKT
        "started_at": None,   # when monitoring began
        "signal_log": deque(maxlen=300),  # [{ts,horse,pool,tier,rise}] 30åˆ†é˜è¨Šè™Ÿè¨˜éŒ„
        "_last_logged_tier": {},   # horse -> ä¸Šæ¬¡è¨˜éŒ„å˜… tierï¼ˆå‡ç´šå…ˆå†è¨˜ï¼Œé¿å…æ´—ç‰ˆï¼‰
        "_last_logged_ts": {},     # horse -> ä¸Šæ¬¡è¨˜éŒ„æ™‚é–“ï¼ˆåŒ tier ç›¸åŒæ™‚éš”60ç§’å…ˆå†è¨˜ï¼‰
        "_signal_log_synced_ts": 0.0,  # ç”±ç¡¬ç¢Ÿè£œé½Š signal_log è£œåˆ°é‚Š
    }

# RACES: race_key -> state dict. Switching races no longer wipes data;
# each race accumulates independently and is remembered.

# é–‹æ©Ÿè®€ä¸€æ¬¡ç¡¬ç¢Ÿsettingsï¼ˆå¦‚æžœä¹‹å‰æ’³éŽã€Œå„²å­˜è¨­å®šã€ï¼‰ï¼Œè£œåšé è¨­å€¼ã€‚



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  DATA FETCH
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
def _to_float(x):
    try: return float(x)
    except: return 0.0


def fetch_all_meetings():
    """#8ï¼šç”¨ activeMeetings è‡ªå‹•æ”žæ™’æ‰€æœ‰ã€Žæœ‰è³½äº‹ã€å˜…æ—¥æœŸ+å ´åœ°ï¼ˆåŒ…æ‹¬æµ·å¤–ï¼‰ã€‚
    Returns list of {date, venue, label, races}. å””ä½¿æ‰‹å‹•æ€æ—¥æœŸ/å ´åœ°ã€‚"""
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
    # åŽ»é‡ + æŒ‰æ—¥æœŸæŽ’
    seen = set(); uniq = []
    for m in out:
        k = (m["date"], m["venue"])
        if k in seen:
            continue
        seen.add(k); uniq.append(m)
    return sorted(uniq, key=lambda x: (x["date"], x["venue"]))

VENUE_NAMES = {"ST": "æ²™ç”°", "HV": "è·‘é¦¬åœ°"}
def venue_label(v):
    return VENUE_NAMES.get(v, v)   # æµ·å¤–å ´åœ°å°±ç›´æŽ¥é¡¯ç¤º code










# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  ENRICH + HISTORY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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
        key = (r["æ± "], r["é¦¬è™Ÿ"])
        curr = r["è³ çŽ‡"]
        new_last[key] = curr

        # open odds (locked once)
        if key not in S["open_odds"] and curr > 0:
            S["open_odds"][key] = curr
        opn = S["open_odds"].get(key, curr)

        # % change vs open â€” sign follows odds movement:
        #   negative = odds dropped (è½é£›), positive = odds rose (å›žé£›)
        if opn and opn > 0:
            pct = (curr - opn) / opn * 100.0
        else:
            pct = 0.0

        if abs(pct) <= FLAT_THRESHOLD:
            d = "flat"
        elif pct < 0:
            d = "down"   # odds dropped => è½é£›
        else:
            d = "up"     # odds rose => å›žé£›

        open_arr.append(opn); live_arr.append(curr)
        chg_arr.append(pct); dir_arr.append(d)

        # Store the same timestamp used for this calculation. In REPLAY the
        # selected snapshot timestamp is used; LIVE uses wall-clock time.
        if record and curr > 0:
            S["series"][key].append((now_hkt.timestamp(), curr))

    S["last_odds"] = new_last
    df["é–‹è³ "] = open_arr
    df["å³å ´"] = live_arr
    df["è®ŠåŒ–"] = chg_arr
    df["æ–¹å‘"] = dir_arr
    return df, mtp



def recent_speed(S, key, seconds=30):
    """Decay-weighted recent % move magnitude over last N sec â€” for ranking & plunge."""
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
    """è©²é¦¬å³å ´ç´¯ç©ç¸½æŠ•æ³¨é¡ï¼ˆä½”æ¯”æ³•ï¼Œ= æ£’åž‹åœ–æ£’é«˜ = æ¯åˆ†é˜è¡¨åˆè¨ˆï¼‰ã€‚"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    return hist[-1][1] if hist else None



# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  RENDER HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•






def _fmt_money(v):
    """Only missing/nonfinite values are blank; zero and negative estimates are data."""
    if v is None:
        return "â€”"
    value = float(v)
    if not math.isfinite(value):
        return "â€”"
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.0f}K"
    return f"{sign}${amount:.0f}"





def _nice_ceiling(maxv):
    """è‡ªå‹•æ€éšé ‚ + é–“æ ¼ï¼ˆ1/2/2.5/5 Ã— 10^nï¼‰ï¼Œç›®æ¨™ç´„ 5 æ ¼ã€‚"""
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

def stake_bar_chart_v(df_pool, pool_name, pool_inv, S, sort_by="é¦¬è™Ÿ",
                      m1=100_000, m2=200_000, m3=400_000, mtp=None, as_of_ts=None):
    """ç›´å‘æ£’åž‹åœ–ï¼šæ£’é«˜ï¼ä¼°ç®—æŠ•æ³¨é¡ï¼ˆè³ çŽ‡ä½”æ¯” Ã— å½©æ± ç¸½é¡ï¼‰ã€‚
    æ£’è‰²ï¼æœ€è¿‘ä¸€å€‹å®Œæ•´åˆ†é˜æµå…¥ï¼ˆåŒæ¯åˆ†é˜è¡¨ -1åˆ†æ ¼åŒæ­¥ï¼‰ã€‚Yè»¸é‡‘é¡åˆ»åº¦ï¼ˆè‡ªå‹•è·Ÿæœ€å¤§ï¼‰ã€‚"""
    if pool_inv is None or pool_inv <= 0:
        st.markdown(
            f'<div class="panel"><div class="panel-title">ðŸ“Š {pool_name}æŠ•æ³¨é¡æ£’åž‹åœ–</div>'
            f'<div class="panel-sub">æš«ç„¡å½©æ± é‡‘é¡</div></div>', unsafe_allow_html=True)
        return
    sub = df_pool[df_pool["å³å ´"] > 0].copy()
    if sub.empty:
        st.markdown(
            f'<div class="panel"><div class="panel-title">ðŸ“Š {pool_name}æŠ•æ³¨é¡æ£’åž‹åœ–</div>'
            f'<div class="panel-sub">æœ‰å½©æ± é‡‘é¡ {_fmt_money(pool_inv)}ï¼Œä½†æœªæœ‰é€åŒ¹é¦¬è³ çŽ‡</div></div>',
            unsafe_allow_html=True)
        return
    pool_code = str(df_pool["æ± "].iloc[0]) if len(df_pool) else pool_name
    inv_live = 1.0 / sub["å³å ´"]
    sub["æŠ•æ³¨é¡"] = inv_live / inv_live.sum() * pool_inv   # å³å ´ç¸½æŠ•æ³¨ï¼ˆä½”æ¯”æ³•ï¼‰
    if sort_by == "è³ çŽ‡":
        sub = sub.sort_values("å³å ´")
    else:
        sub = sub.sort_values("é¦¬è™Ÿ", key=lambda s: pd.to_numeric(s, errors="coerce"))
    max_stake = sub["æŠ•æ³¨é¡"].max() if len(sub) else 1
    top, step = _nice_ceiling(max_stake)

    # æœ€è¿‘ä¸€å€‹å®Œæ•´åˆ†é˜çª—ï¼ˆåŒæ¯åˆ†é˜è¡¨ -1åˆ†æ ¼ä¸€è‡´ï¼‰
    now_ts = as_of_ts if as_of_ts is not None else datetime.now(HKT).timestamp()
    m_end, m_start = now_ts, now_ts - 60

    # Yè»¸åˆ»åº¦ HTMLï¼ˆçµ•å°å®šä½å–ºå·¦é‚Šï¼‰
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
        horse = r["é¦¬è™Ÿ"]
        odds = r["å³å ´"]
        stake = r["æŠ•æ³¨é¡"]
        # æ£’è‰²ï¼šæœ€è¿‘ä¸€å€‹å®Œæ•´åˆ†é˜æµå…¥ï¼ˆåŒæ¯åˆ†é˜è¡¨åŒæ­¥ï¼‰
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
        PLOT_H = 130   # ç¹ªåœ–å€é«˜åº¦ï¼ˆpxï¼‰ï¼Œæ£’ç”¨ px è¨ˆï¼Œå””ç”¨ % ï¼ˆ% æœƒå› ç‚ºçˆ¶å±¤å†‡å›ºå®šé«˜è€Œå¡Œï¼‰
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
        f'<div class="panel-title">ðŸ“Š {pool_name}æŠ•æ³¨é¡æ£’åž‹åœ–</div>'
        f'<div class="panel-sub">æ£’é«˜ï¼ç¸½æŠ•æ³¨é‡‘é¡ï¼ˆYè»¸è‡ªå‹•åˆ»åº¦ï¼‰Â· è¿‘1åˆ†é˜æµå…¥ âš¡{_fmt_money(m1)}é»ƒ/ðŸ”¥{_fmt_money(m2)}æ©™/ðŸ’¥{_fmt_money(m3)}ç´« è®Šè‰²ï¼ˆèˆ‡é‡‘é¡è¡¨åŒæ­¥ï¼‰</div>'
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
    flowed into that horse that minute. Uses stake = poolÃ—0.825/odds reversal.
    Only shown when post time known (needs countdown minutes)."""
    pool_inv = win_inv if pool == "WIN" else pla_inv
    title = "ç¨è´" if pool == "WIN" else "ä½ç½®"
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df[df["æ± "] == pool].copy()
    sub = sub[sub["å³å ´"] > 0]
    if sub.empty:
        return
    if mtp is None:
        st.markdown(
            f'<div class="panel"><div class="panel-title">ðŸ“‹ æ¯åˆ†é˜è½æ³¨é‡‘é¡è¡¨ï¼ˆ{title}ï¼‰</div>'
            f'<div class="panel-sub">éœ€è¦é–‹è·‘æ™‚é–“å…ˆè¨ˆå€’æ•¸åˆ†é˜ â€” è«‹å–ºä¸Šæ–¹å¡«é–‹è·‘æ™‚é–“</div></div>',
            unsafe_allow_html=True)
        return

    # â”€â”€ æ™‚é–“è»¸æ ¼ä»”ï¼ˆå·¦ï¼æ—©ï¼Œå³ï¼é–‹è·‘ï¼‰â”€â”€ v17.5ï¼šéš”å¤œ/ç•¶æ—¥ + å›ºå®š60/30/20/10 + é€åˆ†é˜ã€‚
    # éš”å¤œ/ç•¶æ—¥å˜…é‚Šç•Œç”¨å›ºå®šå˜… 00:00ï¼ˆå””ç†å€‹åˆ¥å ´æ¬¡é–‹è·‘æ™‚é–“ï¼‰ï¼Œè§£æ±º #6bï¼š
    # åŒä¸€æ—¥å””åŒå ´é–‹è·‘æ™‚é–“å””åŒï¼Œä½†æ—©æ®µï¼ˆéš”å¤œ/ç•¶æ—¥ï¼‰ç†æ‡‰å®Œå…¨ä¸€è‡´ã€‚
    post_ts = S["post_time"].timestamp() if S["post_time"] else None
    post_dt_local = S["post_time"] if S["post_time"] else None

    def edge_ts(min_before):
        return post_ts - min_before * 60 if post_ts is not None else None

    # ç•¶æ—¥ 00:00ï¼ˆå›ºå®šï¼Œå””è·Ÿé–‹è·‘æ™‚é–“æµ®å‹•ï¼‰
    midnight_dt = datetime(post_dt_local.year, post_dt_local.month, post_dt_local.day,
                           0, 0, 0, tzinfo=HKT) if post_dt_local else None
    midnight_ts = midnight_dt.timestamp() if midnight_dt else None

    # é€æ ¼å®šç¾©ï¼š(label, is_hour[æ—©æ®µ/æ•´é»žæ ¼,å””è®Šè‰²], ts_start, ts_end)
    # 60/30/20/10ï¼šå‘¢æ ¼ä»£è¡¨ã€Œç”±å‘¢å€‹åˆ†é˜æ•¸é–‹å§‹ï¼ŒåŽ»åˆ°ä¸‹ä¸€å€‹åˆ»åº¦ã€å˜…ä¸€æ®µæµå…¥
    #ï¼ˆä¾‹å¦‚ã€Œ60ã€= é–‹è·‘å‰60åˆ†é˜ â†’ é–‹è·‘å‰30åˆ†é˜ å‘¢æ®µï¼‰ã€‚10ä¹‹å¾Œé€åˆ†é˜åŽ»åˆ°é–‹è·‘ã€‚
    ladder = [60, 30, 20, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    cols = [
        ("éš”å¤œ", True, False, None, midnight_ts),
        ("ç•¶æ—¥", True, False, midnight_ts, edge_ts(60)),
    ]
    for i in range(len(ladder) - 1):
        start_e, end_e = ladder[i], ladder[i + 1]
        lbl = "é–‹è·‘" if end_e == 0 else str(start_e)
        is_hour = start_e >= 60   # 60å‘¢æ ¼ä»²ä¿‚å¤§æ ¼,å””è®Šè‰²ï¼›30/20/10ä¹‹å¾Œå˜…é€åˆ†é˜æ ¼å…ˆè®Šè‰²
        cols.append((lbl, is_hour, False, edge_ts(start_e), edge_ts(end_e)))

    latest_end_ts = (as_of_ts if as_of_ts is not None
                     else datetime.now(HKT).timestamp())
    cols.append(("æœ€æ–°1åˆ†", False, True, latest_end_ts - 60, latest_end_ts))

    def stake_bucket(horse, ts_start, ts_end):
        if ts_end is None:
            return None
        if ts_start is None:
            # ç”±æœ€æ—©è¨˜éŒ„åˆ° ts_end å˜…ç´¯ç©
            s_end = stake_at_ts(S, pool, horse, ts_end)
            hist = S["stake_hist"][(pool, str(horse))]
            s_start = hist[0][1] if hist else None
            if s_end is None or s_start is None:
                return None
            return s_end - s_start
        if ts_end == latest_end_ts and ts_start == latest_end_ts - 60:
            return latest_minute_gain(S, pool, horse, latest_end_ts)
        return stake_in_bucket(S, pool, horse, ts_start, ts_end)

    # ã€Œéš”å¤œã€ã€Œç•¶æ—¥ã€ç”±ç¡¬ç¢Ÿè¨ˆï¼ˆå””å—è¨˜æ†¶é«”dequeä¸Šé™å½±éŸ¿ï¼Œé–‹è³£æå‰å¹¾è€éƒ½å•±ï¼‰ï¼›
    # 60/30/20/10åŒé€åˆ†é˜å°±ç”¨è¿”è¨˜æ†¶é«”ï¼ˆå¤ è¿‘ï¼Œå””ä½¿æ‹–ç¡¬ç¢Ÿï¼‰ã€‚
    # disk_race_keyï¼šREPLAY æ€å—°å ´å˜… keyï¼ˆåŒä¸Šé¢ä¸‹æ‹‰é¸å–®å¯èƒ½å””åŒå ´ï¼‰ï¼Œ
    # å†‡å‚³å°±ç”¨è¿” S è‡ªå·±å—°å€‹ï¼ˆLIVE æƒ…æ³ï¼‰ã€‚
    early_map = {}
    for horse in sub["é¦¬è™Ÿ"]:
        v_mid = stake_at_ts(S, pool, horse, min(midnight_ts, S['as_of_ts'])) if midnight_ts else None
        v_60 = stake_at_ts(S, pool, horse, min(edge_ts(60), S['as_of_ts'])) if edge_ts(60) else None
        early_map[str(horse)] = {"éš”å¤œ": v_mid, "ç•¶æ—¥": v_60 - v_mid if v_mid is not None and v_60 is not None and S['as_of_ts'] >= midnight_ts else None}

    rows_data = []
    for _, r in sub.sort_values("å³å ´").iterrows():
        horse = str(r["é¦¬è™Ÿ"])
        odds = r["å³å ´"]
        eb = early_map.get(horse, {})
        per_col = [eb.get("éš”å¤œ"), eb.get("ç•¶æ—¥")]
        per_col += [stake_bucket(horse, s, e) for (_, _, _, s, e) in cols[2:]]
        rows_data.append((horse, odds, per_col))

    def cellcol(v, is_hour):
        if v is None:
            return "var(--muted)"
        if is_hour:
            return "var(--subtext)"   # æ—©æ®µ/å¤§æ ¼å””è®Šè‰²
        if v >= m3: return "#c878ff"
        if v >= m2: return "#ff8c3c"
        if v >= m1: return "#ffd43b"
        return "var(--subtext)"

    # header
    head = '<th style="text-align:left;padding:3px 5px;font-size:9px;color:var(--muted);position:sticky;left:0;background:var(--card)">é¦¬ è³ </th>'
    for (lbl, is_hour, is_prev, _, _) in cols:
        col_bg = "background:rgba(30,30,44,0.5);" if is_hour else ""
        sync_head = 'border-left:2px solid rgba(80,170,255,0.55);' if is_prev else ''
        head += (f'<th style="text-align:right;padding:2px 5px;font-size:9px;color:{"#78899a" if is_hour else "var(--muted)"};{col_bg}{sync_head}">'
                 f'{lbl}</th>')
    head += '<th style="text-align:right;padding:3px 5px;font-size:9px;color:#e0a83c">åˆè¨ˆ</th>'

    body = ""
    for horse, odds, per_col in rows_data:
        # åˆè¨ˆ = å³å ´ç¸½æŠ•æ³¨ï¼ˆä½”æ¯”æ³•ï¼ŒåŒæ£’åž‹åœ–æ£’é«˜ä¸€è‡´ï¼‰
        total = current_stake(S, pool, horse)
        if total is None:
            total = sum(v for v in per_col[:-1] if v) or 0
        cells = ""
        for (lbl, is_hour, is_prev, _, _), v in zip(cols, per_col):
            txt = f'+{_fmt_money(v)}' if (v and v > 0) else ('â€”' if v is None else _fmt_money(v))
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
        f'<div class="panel-title">ðŸ“‹ è½æ³¨é‡‘é¡è¡¨ï¼ˆ{title} Â· æ™‚é–“ç”±å·¦åˆ°å³ï¼‰</div>'
        f'<div class="panel-sub">éš”å¤œ(é–‹è³£â†’00:00) Â· ç•¶æ—¥(00:00â†’-60åˆ†,å…¨å ´ä¸€è‡´) Â· '
        f'60/30/20/10(æ¯æ®µ) Â· 10åˆ†ä¹‹å¾Œé€åˆ†é˜ â†’ é–‹è·‘ Â· æœ€æ–°1åˆ†ï¼ç•«é¢æ™‚é–“å‘å‰60ç§’ Â· '
        f'âš¡{_fmt_money(m1)}é»ƒ/ðŸ”¥{_fmt_money(m2)}æ©™/ðŸ’¥{_fmt_money(m3)}ç´«ï¼ˆåªè‡¨å ´é€åˆ†é˜æ ¼è®Šè‰²ï¼‰Â· åˆè¨ˆï¼ç¸½æŠ•æ³¨ï¼ˆåŒæ£’åž‹åœ–ï¼‰</div>'
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
            f'<div class="panel"><div class="panel-title">ðŸŽ² {pool_title}</div>'
            f'<div class="panel-sub">æš«ç„¡è³‡æ–™</div></div>', unsafe_allow_html=True)
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
        f'<div class="panel-title">ðŸŽ² {pool_title}</div>'
        f'<div class="panel-sub">ç´…ï¼æœ€ç†±çµ„åˆï¼ˆè³ çŽ‡æœ€ä½Ž {min_odds:g}ï¼‰Â· äº¤å‰æ ¼ï¼è©²å°é¦¬è³ çŽ‡</div>'
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
    win = df[df["æ± "] == "WIN"].copy()
    if win.empty:
        return
    inv = 1.0 / win["å³å ´"]
    win["W%"] = inv / inv.sum() * 100.0
    win_share = {int(k): v for k, v in zip(win["é¦¬è™Ÿ"], win["W%"])}

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
    for _, r in win.sort_values("å³å ´").iterrows():
        h = int(r["é¦¬è™Ÿ"])
        wo = r["å³å ´"]
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
            tier, tier_col, tier_tag = 3, "#c878ff", "ðŸ’¥å¼·çƒˆ"
        elif max_rise >= t2:
            tier, tier_col, tier_tag = 2, "#ff8c3c", "ðŸ”¥æ˜Žé¡¯"
        elif max_rise >= t1:
            tier, tier_col, tier_tag = 1, "#ffd43b", "âš¡ç•™æ„"
        else:
            tier, tier_col, tier_tag = 0, "#5b6675", ""

        # pool % cells: all neutral grey (no green top-3)
        def cell(pct):
            return f'<span class="c-num" style="color:#9aa7b8">{pct:.1f}%</span>'

        marker = ""
        if suspicious:
            marker = ' ðŸ’¥å¯ç–‘' if tier >= 3 else (' ðŸ”¥å¯ç–‘' if tier >= 1 else ' å¯ç–‘')
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
        rise_disp = f"+{max_rise:.1f}%" if max_rise >= 0.1 else "â€”"

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

    # money reference line: ç›´æŽ¥å°‡âš¡/ðŸ”¥/ðŸ’¥ä¸‰ç´šé–€æª»ï¼ˆ%ï¼‰æ›ç®—åšå„æ± å¯¦éš›è§¸ç™¼é‡‘é¡ï¼ˆ$ï¼‰ï¼Œ
    # å°æ‡‰çœŸæ­£æ±ºå®šè¨Šè™Ÿå˜… share_rise() é–€æª»ï¼Œå””ä½¿ç”¨æˆ¶è‡ªå·±æ”žã€Œ1%ã€å†å¿ƒç®—ä¸€æ¬¡ã€‚
    def tier_money(total, pct):
        return _fmt_money(total * pct / 100.0) if total else "â€”"
    money_ref = (
        f'è§¸ç™¼é‡‘é¡å°ç…§ï¼ˆå³å ´å½©æ±  Ã— é–€æª»%ï¼‰ï¼š<br>'
        f'âš¡{t1:g}% ç¨è´{tier_money(wt, t1)}/ä½ç½®{tier_money(pt, t1)}/'
        f'é€£è´{tier_money(qt, t1)}/ä½ç½®Q{tier_money(qpt, t1)}<br>'
        f'ðŸ”¥{t2:g}% ç¨è´{tier_money(wt, t2)}/ä½ç½®{tier_money(pt, t2)}/'
        f'é€£è´{tier_money(qt, t2)}/ä½ç½®Q{tier_money(qpt, t2)}<br>'
        f'ðŸ’¥{t3:g}% ç¨è´{tier_money(wt, t3)}/ä½ç½®{tier_money(pt, t3)}/'
        f'é€£è´{tier_money(qt, t3)}/ä½ç½®Q{tier_money(qpt, t3)}'
        f'ï¼ˆé€£è´/ä½ç½®Qé‡‘é¡ç‚ºç²—ä¼°ï¼‰'
    )

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">ðŸŽ¯ å››æ± ç¶œåˆç†±åº¦</div>'
        f'<div class="panel-sub">æŒ‰ç¨è´è³ çŽ‡æŽ’åº Â· å¹³æ™‚ä¹¾æ·¨ Â· '
        f'1åˆ†å‡ âš¡{t1:g}%/ðŸ”¥{t2:g}%/ðŸ’¥{t3:g}% Â· å†·é¦¬(â‰¥{cold_odds:g}å€)å¤šæ± çš†ç†±ï¼å¯ç–‘<br>{money_ref}</div>'
        f'<div class="thead">'
        f'<span class="c-no">é¦¬ è³ çŽ‡</span>'
        f'<span class="c-num">ç¨è´</span><span class="c-num">ä½ç½®</span>'
        f'<span class="c-num">é€£è´</span><span class="c-num">ä½ç½®Q</span>'
        f'<span class="c-num">1åˆ†å‡</span><span class="c-num">çš†ç†±</span></div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ffd43b"></i>âš¡ç•™æ„</span>'
        f'<span><i style="background:#ff8c3c"></i>ðŸ”¥æ˜Žé¡¯</span>'
        f'<span><i style="background:#c878ff"></i>ðŸ’¥å¼·çƒˆ</span>'
        f'<span><i style="background:#ff5757"></i>å†·é¦¬çš†ç†±å¯ç–‘</span>'
        f'</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def signal_summary_panel(events, minutes=30, as_of_ts=None):
    """30åˆ†é˜è¨Šè™Ÿå½™ç¸½ï¼ˆå³é‚Šæ–°é¢æ¿ï¼‰ï¼šæŒ‰é¦¬åˆ†çµ„ï¼Œæ’³é–‹ç‡é€è¡Œæ™‚åºç´°ç¯€ã€‚
    events: list of {ts,horse,pool,tier,rise}ã€‚as_of_ts=None ç”¨è€Œå®¶æ™‚é–“ï¼›
    REPLAY æ¨¡å¼æœƒå‚³è¿”å—°å€‹snapshotå˜…tsï¼Œç­‰å€‹30åˆ†é˜çª—è·Ÿè¿”ç¿»ç‡ç·Šå—°ä¸€åˆ»ã€‚
    ç”¨ st.container(border=True) åŒ…ä½æˆå€‹panelï¼ˆé€£æš«ç„¡è¨Šè™Ÿéƒ½å–ºborderå…¥é¢ï¼‰ï¼Œ
    ç­‰å€‹boxå¯ä»¥è‡ªå‹•stretchåŽ»åˆ°åŒå·¦é‚Šã€Œå››æ± ç¶œåˆç†±åº¦ã€ä¸€æ¨£é«˜ï¼ˆCSSå–ºåˆ¥è™•æŽ§åˆ¶ï¼‰ã€‚"""
    if as_of_ts is None:
        as_of_ts = datetime.now(HKT).timestamp()
    cutoff = as_of_ts - minutes * 60
    evs_in_window = [e for e in events if cutoff <= e.get("ts", 0) <= as_of_ts]

    with st.container(border=True):
        st.markdown(
            f'<div class="panel-title">ðŸ• {minutes}åˆ†é˜è¨Šè™Ÿå½™ç¸½</div>'
            f'<div class="panel-sub">æŒ‰é¦¬åˆ†çµ„ Â· æ’³éš»é¦¬å±•é–‹æ™‚åºç´°ç¯€ Â· éŽå’—{minutes}åˆ†é˜è‡ªå‹•ç§»é™¤</div>',
            unsafe_allow_html=True)

        if not evs_in_window:
            st.caption("æš«ç„¡è¨Šè™Ÿ")
            return

        by_horse = defaultdict(list)
        for e in evs_in_window:
            by_horse[e["horse"]].append(e)

        tier_emoji = {1: "âš¡", 2: "ðŸ”¥", 3: "ðŸ’¥"}

        def horse_key(h):
            evs = by_horse[h]
            return (-max(ev["tier"] for ev in evs), -max(ev["ts"] for ev in evs))

        for h in sorted(by_horse.keys(), key=horse_key):
            evs = sorted(by_horse[h], key=lambda e: e["ts"], reverse=True)
            counts = {1: 0, 2: 0, 3: 0}
            for e in evs:
                counts[e["tier"]] = counts.get(e["tier"], 0) + 1
            summary = "ã€€".join(f'{tier_emoji[t]}Ã—{counts[t]}' for t in (3, 2, 1) if counts.get(t))
            with st.expander(f"{h}è™Ÿã€€{summary}", expanded=False):
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
        raise RuntimeError(MODEL_IMPORT_ERROR or "æ¨¡åž‹æ¨¡çµ„æœªèƒ½è¼‰å…¥")
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


def score_live_model(card_rows, win_odds, rebate_rate=0.0, style_history=None, bias_info=None):
    """Return model metrics plus transparent running-style/track-bias adjustments."""
    if not MODEL_READY or not card_rows or not win_odds:
        return pd.DataFrame(), MODEL_IMPORT_ERROR or "æœªæœ‰å®Œæ•´æŽ’ä½ï¼å³æ™‚ç¨è´è³ çŽ‡"
    try:
        bundle, _ = load_quant_assets()
        card_json = json.dumps(card_rows, ensure_ascii=False, sort_keys=True, default=str)
        base = pd.DataFrame(build_live_fundamentals(card_json))
        base["horse_key"] = base["horse_no"].apply(lambda x: str(int(float(x))))
        horse_ids = {str(int(float(x.get("horse_no")))): str(x.get("horse_id") or "")
                     for x in card_rows if x.get("horse_no") is not None}
        base["horse_id"] = base["horse_key"].map(horse_ids)
        base["win_odds"] = base["horse_key"].map(
            {str(int(float(k))): float(v) for k, v in win_odds.items() if float(v) > 0}
        )
        base = base.dropna(subset=["win_odds"]).reset_index(drop=True)
        if len(base) < 2:
            return pd.DataFrame(), "æœ‰æ•ˆç¨è´è³ çŽ‡ä¸è¶³"
        race_idx = np.zeros(len(base), dtype=int)
        p_public = public_probabilities(base["win_odds"].to_numpy(float), race_idx)
        eps = 1e-12
        z = (float(bundle["ss_alpha"]) * np.log(np.clip(base["p_model"], eps, 1.0))
             + float(bundle["ss_beta"]) * np.log(np.clip(p_public, eps, 1.0)))
        p_final = _group_softmax(z)
        # These are deliberately small, visible adjustments. They only use
        # completed same-day evidence and historical style, never future results.
        style_weight = {"æ”¾é ­": 0.08, "å‰ç½®": 0.04, "ä¸­ç½®": -0.02, "å¾Œä¸Š": -0.06}
        style_names = []
        style_adj = []
        bias_adj = []
        draw_adj = []
        bias_info = bias_info or {}
        style_stats = bias_info.get("style_stats") or {}
        draw_stats = bias_info.get("draw_stats") or {}
        for _, row in base.iterrows():
            style = style_from_history(style_history or {}, row.get("horse_id") or row["horse_no"])
            style_names.append(style)
            sa = style_weight.get(style, 0.0) if style != "æœªçŸ¥" else 0.0
            stat = style_stats.get(style) or {}
            if stat.get("reliable"):
                sa += max(-0.06, min(0.06, (float(stat.get("lift", 1.0)) - 1.0) * 0.05))
            try:
                draw_value = next(x.get("draw") for x in card_rows
                                  if str(x.get("horse_no")) == str(row["horse_no"]))
                draw_key = str(int(float(draw_value)))
            except (StopIteration, TypeError, ValueError):
                draw_key = ""
            dstat = draw_stats.get(draw_key) or {}
            da = max(-0.04, min(0.04, (float(dstat.get("lift", 1.0)) - 1.0) * 0.04)) if dstat.get("reliable") else 0.0
            style_adj.append(sa)
            draw_adj.append(da)
            bias_adj.append(sa + da)
        adjustment = np.asarray(bias_adj, dtype=float)
        p_final = _group_softmax(np.log(np.clip(p_final, eps, 1.0)) + adjustment)
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
        base["historical_style"] = style_names
        base["style_adjustment_pct"] = np.asarray(style_adj) * 100.0
        base["draw_adjustment_pct"] = np.asarray(draw_adj) * 100.0
        base["bias_adjustment_pct"] = np.asarray(bias_adj) * 100.0
        base["fair_odds"] = 1.0 / np.clip(p_final, eps, 1.0)
        base["overlay_pct"] = (p_final / np.clip(p_public, eps, 1.0) - 1.0) * 100.0
        base["ev"] = p_final * base["win_odds"] + float(rebate_rate) * (1.0 - p_final)
        base["p_place"] = np.clip(p_place, 0.0, 1.0)
        base["value"] = base["ev"] - 1.0
        return base.sort_values("ev", ascending=False), None
    except Exception as exc:
        return pd.DataFrame(), f"æ¨¡åž‹è¨ˆç®—å¤±æ•—ï¼š{exc}"


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


def normalize_snapshot(snap, race_key):
    """Canonical horse IDs: old HKJC '01' and newer '1' are the same runner.
    Work on a copy; original Recorder JSON remains unchanged.
    """
    if not valid_snapshot(snap, race_key):
        raise ValueError('å ´æ¬¡æˆ–è¨˜éŒ„æ ¼å¼ä¸ç¬¦')
    result = dict(snap)
    result.pop('_shares', None)
    result['ts'] = float(snap['ts'])
    for code in ('win', 'pla', 'qin', 'qpl'):
        normalized = {}
        for key, value in (snap.get(code) or {}).items():
            numbers = [int(h) for h in str(key).split(',')]
            canonical = ','.join(str(h) for h in sorted(numbers))
            odds = float(value)
            if canonical in normalized and normalized[canonical] != odds:
                raise ValueError('åŒä¸€é¦¬è™Ÿæœ‰è¡çªè³ çŽ‡ï¼š' + canonical)
            normalized[canonical] = odds
        result[code] = normalized
    result['pool'] = {k: float(v) if v is not None else None
                      for k, v in (snap.get('pool') or {}).items()}
    return result


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
        raise RuntimeError('HKJC å›žå‚³æŸ¥è©¢éŒ¯èª¤')
    meeting = matching_meeting((body.get('data') or {}).get('raceMeetings'), date_str, venue)
    if meeting is None:
        raise RuntimeError(f'HKJC æœªå›žå‚³ç›¸ç¬¦è³½æ—¥ï¼š{date_str} {venue}')
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
            raise RuntimeError('HKJC è³ çŽ‡æŸ¥è©¢éŒ¯èª¤')
        meetings = (body.get('data') or {}).get('raceMeetings') or []
        if len(meetings) != 1:
            raise RuntimeError('HKJC è³ çŽ‡å›žæ‡‰è³½æ—¥æ•¸é‡ç•°å¸¸')
        return meetings[0].get('pmPools') or [], datetime.now(HKT).timestamp()
    with ThreadPoolExecutor(max_workers=2) as executor:
        meeting_future = executor.submit(fetch_full_meeting, d, v)
        odds_future = executor.submit(odds_request)
        meeting, meeting_ts = meeting_future.result()
        pools, odds_ts = odds_future.result()
    snap = snapshot_from_pools(meeting, int(r), pools, max(meeting_ts, odds_ts), meeting_ts)
    if snap is None:
        raise RuntimeError('å‘¢å ´æš«æ™‚å†‡æœ‰æ•ˆç¨è´è³ çŽ‡')
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
    cache = st.session_state.setdefault('_archive_v19_r21', {})
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
            value = normalize_snapshot(value, race_key)
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
    st.caption('REPLAY æ™‚é–“è»¸ï¼šæ•¸å­—è¡¨ç¤ºé–‹è·‘å‰åˆ†é˜ï¼›ç¼ºå°‘è©²æ™‚æ®µè¨˜éŒ„æœƒæç¤ºã€‚')
    targets = [('éš”å¤œ', None), ('60', 60), ('30', 30), ('20', 20), ('10', 10),
               ('9', 9), ('8', 8), ('7', 7), ('6', 6), ('5', 5), ('4', 4), ('3', 3), ('2', 2), ('1', 1), ('é–‹è·‘', 0)]
    for (label, minutes), col in zip(targets, st.columns(len(targets))):
        with col:
            if st.button(label, key=f'clip::{race_key}::{label}', use_container_width=True):
                if post_ts is None:
                    st.warning('æœªæœ‰é–‹è·‘æ™‚é–“')
                else:
                    post = datetime.fromtimestamp(post_ts, HKT)
                    target = post.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() if minutes is None else post_ts - minutes * 60
                    index = select_replay_index(snaps, target)
                    if index is None:
                        st.warning('å‘¢æ®µæ™‚é–“å†‡ç›¸è¿‘è¨˜éŒ„')
                    else:
                        st.session_state[state_key] = index
    if len(snaps) > 1:
        idx = st.slider('æ™‚é–“è»¸ï¼ˆæ‹‰åŽ»ä»»ä½•ä¸€åˆ»ï¼Œå¾®èª¿ï¼‰', 0, len(snaps) - 1, key=state_key)
    else:
        idx = 0
        st.caption('åªæœ‰ä¸€å€‹è¨˜éŒ„é»žï¼Œæœªèƒ½ç§»å‹•æ™‚é–“è»¸ã€‚')
    st.caption(f"æ™‚é–“é»žï¼š{datetime.fromtimestamp(times[idx], HKT):%Y-%m-%d %H:%M:%S}ã€€å…± {len(snaps)} å€‹è¨˜éŒ„é»ž")
    return idx

if '_settings_loaded_rebuilt' not in st.session_state:
    for key, value in load_settings().items():
        st.session_state.setdefault(key, value)
    st.session_state['_settings_loaded_rebuilt'] = True

st.markdown(f'<div class="hdr"><div class="hdr-title">ðŸŽ {APP_NAME}</div><span class="live">{APP_VERSION}</span></div>', unsafe_allow_html=True)
_src_col, _sep_col, _mode_col = st.columns([2.4, 0.1, 2])
with _mode_col:
    mode = st.radio('æ¨¡å¼', ['â— LIVE å³å ´', 'ðŸ” REPLAY ç¿»ç‡'], horizontal=True, key='mode_v19_rebuilt')
replay_mode = 'REPLAY' in mode
with _src_col:
    st.caption('ðŸ“¡ Recorder æ­·å²è¨˜éŒ„' if replay_mode else 'ðŸ“¡ ç›´æŽ¥é€£ç·š HKJC Â· ç›®æ¨™æ¯5ç§’æ›´æ–°')

controls_slot = st.container()
settings_slot = st.container()
replay_picker_slot = st.empty()
with controls_slot:
    c1, c2, c3, c4, c5 = st.columns([2.4, 1, 1, 1.4, 1.4])
if replay_mode:
    saved = list_saved_races()
    if not saved:
        st.info(f'æœªæœ‰ Recorder æ­·å²è¨˜éŒ„ï¼š{DATA_DIR}')
        st.stop()
    with replay_picker_slot.container():
        race_key = st.selectbox('æ€å ´æ¬¡', saved, index=len(saved)-1, key='selected_replay_race_v19')
    d, course, race_no = race_key.split('|')
    race_no = int(race_no)
    race_date = date.fromisoformat(d)
    with c1:
        st.caption('REPLAY è³½äº‹')
        st.write(f'{race_date} Â· {venue_label(course)} ({course})')
    with c2:
        st.write(venue_label(course))
    with c3:
        st.write(f'ç¬¬ {race_no} å ´')
else:
    try:
        meetings = current_meetings()
    except Exception as exc:
        meetings = []
        st.warning(f'æœªå–å¾—è³½æœŸï¼š{exc}')
    with c1:
        if meetings:
            today = datetime.now(HKT).date().isoformat()
            default = next((i for i, m in enumerate(meetings) if m['date'] >= today), 0)
            mi = st.selectbox('è³½äº‹ï¼ˆè‡ªå‹•åŒæ­¥é¦¬æœƒï¼‰', range(len(meetings)), index=default,
                format_func=lambda i: f"{meetings[i]['date']} Â· {venue_label(meetings[i]['venue'])} ({meetings[i]['venue']}) Â· {meetings[i]['n_races']}å ´")
            meeting = meetings[mi]
            race_date, course = date.fromisoformat(meeting['date']), meeting['venue']
            max_race = int(meeting['n_races'] or 14)
        else:
            race_date = st.date_input('æ—¥æœŸ', datetime.now(HKT).date())
            course, max_race = 'ST', 14
    with c2:
        if not meetings:
            course = st.selectbox('å ´åœ°', ['ST', 'HV'])
        else:
            st.write(venue_label(course))
    with c3:
        race_no = st.number_input('å ´æ¬¡', 1, max_race, 1)
    race_key = f'{race_date}|{course}|{int(race_no)}'
with c4:
    post_input = st.text_input('é–‹è·‘æ™‚é–“ (å¯é¸)', '', placeholder='HH:MM', key=f'post::{race_key}::{replay_mode}')
with c5:
    reset_clicked = st.button('ðŸ”„ é‡è¨­æ­¤å ´èµ°å‹¢', use_container_width=True)
if reset_clicked:
    st.session_state.pop('_live_buffer::' + race_key, None)
    st.session_state.pop('replay_time::' + race_key, None)
    st.caption('å·²é‡è¨­æ­¤å ´ç•«é¢ï¼›Recorder æ­·å²ä¿ç•™ã€‚')

with settings_slot:
    with st.expander("âš™ï¸ æ€¥å‡åµæ¸¬æ•æ„Ÿåº¦ï¼ˆåˆ†å±¤ Â· æ‹‰æ¡¿å¾®èª¿ï¼‰"):
        def _clampf(v, lo, hi, fallback):
            """åŒ _clamp ä¸€æ¨£ï¼Œä½†è™•ç†æµ®é»žæ•¸ï¼ˆå››æ± ç†±åº¦ç”¨%ï¼‰ã€‚"""
            try:
                v = float(v)
            except Exception:
                return fallback
            return max(lo, min(hi, v))

        st.markdown('<div style="font-size:11px;color:var(--subtext);margin-bottom:2px">å››æ± ç†±åº¦ï¼ˆä½”æ¯” %ï¼‰</div>', unsafe_allow_html=True)
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            st.session_state["rise_t1"] = st.slider(
                "âš¡ ç•™æ„ï¼ˆ%ï¼‰", 0.2, 2.0,
                _clampf(st.session_state.get("rise_t1", RISE_TIER1), 0.2, 2.0, RISE_TIER1), 0.1)
        with sc2:
            st.session_state["rise_t2"] = st.slider(
                "ðŸ”¥ æ˜Žé¡¯ï¼ˆ%ï¼‰", 0.3, 2.5,
                _clampf(st.session_state.get("rise_t2", RISE_TIER2), 0.3, 2.5, RISE_TIER2), 0.1)
        with sc3:
            st.session_state["rise_t3"] = st.slider(
                "ðŸ’¥ å¼·çƒˆï¼ˆ%ï¼‰", 0.5, 3.0,
                _clampf(st.session_state.get("rise_t3", RISE_TIER3), 0.5, 3.0, RISE_TIER3), 0.1)

        st.markdown('<div style="font-size:11px;color:var(--subtext);margin:8px 0 2px">é‡‘é¡è¨Šè™Ÿï¼ˆæ£’åž‹åœ– + æ¯åˆ†é˜é‡‘é¡è¡¨ï¼‰Â· åƒå…ƒï¼ˆè¼¸å…¥æ•¸å€¼ï¼Œæ’³ã€Œå„²å­˜è¨­å®šã€å…ˆæœƒè·¨sessionè¨˜ä½ï¼‰</div>', unsafe_allow_html=True)

        def _clamp(v, lo, hi, fallback):
            """èˆŠsession_state / èˆŠsettingsæª”å¯èƒ½å­˜ä½è¶…å‡ºæ–°ç¯„åœå˜…å€¼ï¼ˆä¾‹å¦‚èˆŠç‰ˆæ‹‰æ¡¿
            setéŽ100Kï¼Œä½†æ–°âš¡ä¸Šé™å¾—50Kï¼‰ï¼Œç›´æŽ¥å‚³è½ number_input æœƒä»¤ Streamlit æ‹‹
            StreamlitValueAboveMaxErrorã€‚å‘¢åº¦çµ±ä¸€å¤¾è¿”å…¥åˆæ³•ç¯„åœå…ˆç”¨ã€‚"""
            try:
                v = int(v)
            except Exception:
                return fallback
            return max(lo, min(hi, v))

        mc1, mc2, mc3, mc4 = st.columns([1, 1, 1, 0.9])
        with mc1:
            _mk1 = st.number_input("âš¡ ç•™æ„ï¼ˆ1-50 Kï¼‰", min_value=1, max_value=50, step=1,
                                   value=_clamp(st.session_state.get("money_t1_k", 20), 1, 50, 20))
        with mc2:
            _mk2 = st.number_input("ðŸ”¥ æ˜Žé¡¯ï¼ˆ51-100 Kï¼‰", min_value=51, max_value=100, step=1,
                                   value=_clamp(st.session_state.get("money_t2_k", 70), 51, 100, 70))
        with mc3:
            _mk3 = st.number_input("ðŸ’¥ å¼·çƒˆï¼ˆ101-400 Kï¼‰", min_value=101, max_value=400, step=1,
                                   value=_clamp(st.session_state.get("money_t3_k", 150), 101, 400, 150))
        with mc4:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("ðŸ’¾ å„²å­˜è¨­å®š", use_container_width=True):
                _ok = save_settings({
                    "money_t1_k": _mk1, "money_t2_k": _mk2, "money_t3_k": _mk3,
                    "rise_t1": st.session_state.get("rise_t1", RISE_TIER1),
                    "rise_t2": st.session_state.get("rise_t2", RISE_TIER2),
                    "rise_t3": st.session_state.get("rise_t3", RISE_TIER3),
                })
                st.session_state["_settings_saved_at"] = datetime.now(HKT).strftime("%H:%M:%S")
                st.session_state["_settings_save_ok"] = _ok

        # å‘¢3å€‹æ•¸å€¼å³åˆ»ç”Ÿæ•ˆï¼ˆå””ä½¿æ’³å„²å­˜éƒ½æœƒå³å ´ç”¨åˆ°ï¼‰ï¼Œå„²å­˜æ·¨ä¿‚å½±éŸ¿ã€Œä¸‹æ¬¡é–‹appã€å˜…é è¨­å€¼
        st.session_state["money_t1_k"] = _mk1
        st.session_state["money_t2_k"] = _mk2
        st.session_state["money_t3_k"] = _mk3
        st.session_state["money_t1"] = _mk1 * 1000
        st.session_state["money_t2"] = _mk2 * 1000
        st.session_state["money_t3"] = _mk3 * 1000

        if st.session_state.get("_settings_saved_at"):
            _status = "å·²å„²å­˜" if st.session_state.get("_settings_save_ok") else "âš ï¸ å„²å­˜å¤±æ•—ï¼ˆcheckç¡¬ç¢Ÿæ¬Šé™ï¼‰"
            st.caption(f"{_status}ï¼š{st.session_state['_settings_saved_at']}ã€€Â· ä¸‹æ¬¡é–‹appæœƒè‡ªå‹•è®€è¿”å‘¢å•²æ•¸å€¼")

        st.caption("å››æ± ç†±åº¦ç”¨ä½”æ¯”%ï¼›æ£’åž‹åœ–+é‡‘é¡è¡¨ç”¨å¯¦è³ªé‡‘é¡ï¼ˆè¿‘1åˆ†é˜ä¼°ç®—è½æ³¨ï¼‰ï¼Œå…©å€‹é‡‘é¡è¡¨å®Œç¾ŽåŒæ­¥ã€‚ä½Žï¼å¤šæç¤ºã€é«˜ï¼å°‘ä½†ç²¾ã€‚")


thresholds = tuple(st.session_state.get(f'rise_t{i}', default) for i, default in enumerate((RISE_TIER1, RISE_TIER2, RISE_TIER3), 1))
if not thresholds[0] < thresholds[1] < thresholds[2]:
    st.error('æ•æ„Ÿåº¦é ˆä¾æ¬¡éžå¢žï¼šâš¡ < ðŸ”¥ < ðŸ’¥')
    st.stop()
archive = load_snapshots(race_key)
if st.session_state.get('_archive_error'):
    st.warning('å·²ç•¥éŽç„¡æ³•è®€å–æˆ–å ´æ¬¡ä¸ç¬¦å˜…è¨˜éŒ„ï¼š' + st.session_state['_archive_error'])
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
        st.error('é–‹è·‘æ™‚é–“è«‹ç”¨ HH:MM')
        st.stop()
if replay_mode:
    if not archive:
        st.info('æ‰€é¸å ´æ¬¡å†‡æœ‰æ•ˆ Recorder è¨˜éŒ„ã€‚')
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
        st.error(f'å³æ™‚è³‡æ–™æ›´æ–°å¤±æ•—ï¼š{exc}ã€‚ä»Šæ¬¡ä¸é¡¯ç¤ºèˆŠè³‡æ–™ä½œç‚ºæœ€æ–°å ±åƒ¹ã€‚')
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
rows = [{'æ± ': code, 'é¦¬è™Ÿ': str(h), 'è³ çŽ‡': _to_float(o), 'å¤§ç†±': False}
        for code in ('WIN', 'PLA') for h, o in (snap.get(code.lower()) or {}).items() if _to_float(o) > 0]
if not rows:
    st.info('å‘¢å€‹è¨˜éŒ„é»žå†‡æœ‰æ•ˆè³ çŽ‡ã€‚')
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
_day_analysis = load_day_postrace(race_date.isoformat(), venue, int(race_no))
_current_postrace = load_postrace_analysis(race_key)
_declared_rows = (_ri or {}).get('card_rows') or []
_history_styles = historical_style_history(_declared_rows, race_date.isoformat(), _day_analysis)
with upper_panels:
    if mtp is not None:
        if mtp < 0:
            countdown = f"è·é›¢é–‹è·‘ {abs(mtp)*60:.0f} ç§’" if mtp >= -1 else f"è·é›¢é–‹è·‘ {abs(mtp):.0f} åˆ†é˜"
        else:
            countdown = 'å·²åˆ°ï¼è¶…éŽé å®šé–‹è·‘æ™‚é–“'
        st.caption(countdown)
    alerts = []
    for _, row in df[df['æ± '] == 'WIN'].iterrows():
        pct, _ = recent_speed(S, ('WIN', row['é¦¬è™Ÿ']), PLUNGE_WINDOW)
        if pct >= PLUNGE_PCT:
            alerts.append((row['é¦¬è™Ÿ'], pct, row['å³å ´']))
    for no, pct, odds in sorted(alerts, key=lambda a: a[1], reverse=True)[:3]:
        st.markdown(f'<div class="alert-bar">âš ï¸ æ’æ°´è­¦ç¤ºã€€{no} è™Ÿæ–¼ {PLUNGE_WINDOW} ç§’å…§æ€¥è·Œ {pct:.0f}%ï¼Œå³å ´ {odds:.1f}</div>', unsafe_allow_html=True)
    st.markdown(f'**{race_date} {venue_label(course)} Â· ç¬¬ {int(race_no)} å ´**')
    st.caption(f"{'REPLAY' if replay_mode else 'LIVE'} è³‡æ–™æ™‚é–“ï¼š{datetime.fromtimestamp(ACTIVE_NOW_TS, HKT):%Y-%m-%d %H:%M:%S}"
               + (f"ã€€é–‹è·‘æ™‚é–“ï¼š{ACTIVE_POST_TIME:%H:%M}" if ACTIVE_POST_TIME else ''))
    if _ri:
        st.caption(' Â· '.join(str(_ri.get(k)) for k in ('name', 'cls', 'dist', 'track', 'going') if _ri.get(k)))
    else:
        st.info('å‘¢ç­†èˆŠè¨˜éŒ„å†‡ä¿å­˜ç›¸ç¬¦æ­·å²é¦¬åï¼æŽ’ä½ï¼›ä¿ç•™é¦¬è™ŸåŠæŠ•æ³¨æ­·å²ï¼Œæ¨¡åž‹æš«ä¸è¨ˆç®—ã€‚æ–° Recorder æœƒä¿å­˜æŽ’ä½ã€‚')

    with st.expander('ðŸ‡ è·‘æ³•ï¼åå·®ï¼ˆå·²å®Œæˆå ´æ¬¡ï¼‰', expanded=False):
        _display_analysis = _current_postrace or _day_analysis
        if _display_analysis.get('completed') or _day_analysis.get('completed_races'):
            if _current_postrace:
                st.caption(f"æœ¬å ´è³½å¾Œçµæžœï¼šå·²è¨˜éŒ„å…¨éƒ¨ {len(_current_postrace.get('runs') or [])} åŒ¹é¦¬ï¼›ä¸‹ä¸€å ´æ¨¡åž‹æœƒè®€å–æ­¤çµæžœã€‚")
            else:
                st.caption('å·²å®Œæˆå ´æ¬¡ï¼š' + ', '.join('ç¬¬%då ´' % n for n in sorted(_day_analysis['completed_races']))
                           + 'ï¼›ä»Šå ´æ¨¡åž‹åªè®€å–ä¹‹å‰å·²å®Œæˆçš„å ´æ¬¡ã€‚')
            st.markdown('**ç•¶æ—¥å ´åœ°åå·®ï¼š** ' + str(_display_analysis.get('bias_label') or 'æ¨£æœ¬ä¸è¶³'))
            _style_rows = []
            _card_for_style = ((_ri or {}).get('card_rows') or [])
            if _current_postrace and _current_postrace.get('runs'):
                _card_for_style = _current_postrace.get('runs')
            for _row in _card_for_style:
                _h = _row.get('horse_no')
                _style_rows.append({'é¦¬è™Ÿ': str(int(float(_h))), 'é¦¬å': _row.get('horse_name') or str(_h),
                                    'æ­·å²è·‘æ³•': style_from_history(_history_styles, _row.get('horse_id') or _h),
                                    'ä»Šå ´é æ¸¬è·‘æ³•ï¼å·²è³½å¯¦éš›è·‘æ³•': _row.get('running_style') or style_from_history(_history_styles, _row.get('horse_id') or _h),
                                    'æª”ä½': _row.get('draw') or 'â€”'})
            if _style_rows:
                st.dataframe(pd.DataFrame(_style_rows), hide_index=True, use_container_width=True)
            _bias_rows = []
            for _style, _stat in (_display_analysis.get('style_stats') or {}).items():
                _bias_rows.append({'è·‘æ³•': _style, 'æ¨£æœ¬': _stat.get('n', 0),
                                   'å…¥ä¸‰ç”²çŽ‡': f"{float(_stat.get('top3_rate', 0)):.1%}",
                                   'Lift': f"{float(_stat.get('lift', 1)):.2f}",
                                   'ç‹€æ…‹': 'å¯åƒè€ƒ' if _stat.get('reliable') else 'æ¨£æœ¬ä¸è¶³'})
            if _bias_rows:
                st.dataframe(pd.DataFrame(_bias_rows), hide_index=True, use_container_width=True)
        else:
            st.info('ç•¶æ—¥å°šæœªæœ‰å·²å®Œæˆå ´æ¬¡åˆ†æžï¼›ç¬¬1å ´æœƒå…ˆé¡¯ç¤ºæ­·å²è³‡æ–™å¯ç”¨ç¨‹åº¦ï¼Œå®Œæˆå¾Œç”±Recorderæ›´æ–°ã€‚')

    # â•â•â• MODEL å³æ™‚è¨ˆç®—ï¼šåŸºæœ¬é¢ + HKJC å³æ™‚ç¨è´è³ çŽ‡ â•â•â•
    st.markdown(
        '<div class="panel-title" style="margin-top:4px">ðŸ§  V19 å³æ™‚é‡åŒ–æ¨¡åž‹</div>'
        '<div class="panel-sub">Benter ç¬¬äºŒéšŽï¼šæ­·å²åŸºæœ¬é¢å‹çŽ‡ Ã— é¦¬æœƒå³æ™‚ç¨è´å¸‚å ´æ¦‚çŽ‡ï¼›æ¯ 5 ç§’éš¨è³ çŽ‡æ›´æ–°</div>',
        unsafe_allow_html=True)
    _rebate_label = st.radio(
        "EV å›žæ‰£è¨­å®š", ["æ•£æˆ¶ï¼ç„¡å›žæ‰£", "ç¨è´åˆè³‡æ ¼å›žæ‰£ 10%"],
        horizontal=True, key="model_rebate_mode",
        help="10% å›žæ‰£åªé©ç”¨æ–¼ç¬¦åˆé¦¬æœƒé–€æª»çš„è¼¸æ³¨ï¼›ä¸€èˆ¬å°é¡æŠ•æ³¨è«‹é¸ç„¡å›žæ‰£ã€‚")
    _rebate_rate = 0.10 if "10%" in _rebate_label else 0.0
    _live_win_odds = {
        str(r["é¦¬è™Ÿ"]): float(r["å³å ´"])
        for _, r in df[df["æ± "] == "WIN"].iterrows()
        if _to_float(r.get("å³å ´")) > 0
    }
    _model_rows = (_ri or {}).get("card_rows") or []
    _model_df, _model_err = score_live_model(
        _model_rows, _live_win_odds, _rebate_rate,
        style_history=_history_styles, bias_info=_day_analysis)
    if not _model_df.empty:
        _shown = _model_df[["horse_key", "horse_name", "historical_style", "win_odds", "p_model",
                            "p_public", "p_final", "overlay_pct", "fair_odds",
                            "ev", "value", "p_place", "style_adjustment_pct",
                            "draw_adjustment_pct", "bias_adjustment_pct"]].copy()
        _shown.columns = ["é¦¬è™Ÿ", "é¦¬å", "æ­·å²è·‘æ³•", "å³æ™‚è³ çŽ‡", "åŸºæœ¬é¢å‹çŽ‡", "å¸‚å ´å‹çŽ‡",
                          "ç¶œåˆå‹çŽ‡", "ç›´åšçŽ‡", "Fair Odds", "EV", "é æœŸå›žå ±", "ä½ç½®æ¦‚çŽ‡",
                          "è·‘æ³•èª¿æ•´", "æª”ä½èª¿æ•´", "åå·®åˆè¨ˆ"]
        _shown["é¦¬è™Ÿ"] = _shown["é¦¬è™Ÿ"].astype(str)
        for _c in ("åŸºæœ¬é¢å‹çŽ‡", "å¸‚å ´å‹çŽ‡", "ç¶œåˆå‹çŽ‡", "ä½ç½®æ¦‚çŽ‡"):
            _shown[_c] = _shown[_c].map(lambda x: f"{x:.2%}")
        _shown["ç›´åšçŽ‡"] = _shown["ç›´åšçŽ‡"].map(lambda x: f"{x:+.1f}%")
        _shown["Fair Odds"] = _shown["Fair Odds"].map(lambda x: f"{x:.2f}")
        _shown["å³æ™‚è³ çŽ‡"] = _shown["å³æ™‚è³ çŽ‡"].map(lambda x: f"{x:.1f}")
        _shown["EV"] = _shown["EV"].map(lambda x: f"{x:.3f}")
        _shown["é æœŸå›žå ±"] = _shown["é æœŸå›žå ±"].map(lambda x: f"{x:+.1%}")
        for _c in ("è·‘æ³•èª¿æ•´", "æª”ä½èª¿æ•´", "åå·®åˆè¨ˆ"):
            _shown[_c] = _shown[_c].map(lambda x: f"{x:+.1f}%")
        st.dataframe(_shown, hide_index=True, use_container_width=True)
        _value_count = int((_model_df["ev"] > 1.0).sum())
        st.caption(
            f"å³æ™‚è¾¨è­˜ {_value_count} åŒ¹ EV > 1ï¼›ç›´åšçŽ‡ = ç¶œåˆå‹çŽ‡ Ã· å¸‚å ´å‹çŽ‡ âˆ’ 1ã€‚"
            "ç¶œåˆå‹çŽ‡åŠä½ç½®æ¦‚çŽ‡ç‚ºæ¨¡åž‹ä¼°ç®—ï¼Œä¸¦éžä¿è­‰çµæžœã€‚")
    else:
        st.info(f"æ¨¡åž‹ç­‰å¾…è³‡æ–™ï¼š{_model_err or 'æŽ’ä½æˆ–ç¨è´è³ çŽ‡å°šæœªé½Šå…¨'}")



    # Four pool totals and original pair matrices.
    def total_card(label, value):
        return f'<div class="panel" style="flex:1"><div class="panel-sub">{label}</div><b>{_fmt_money(value)}</b></div>'
    st.markdown('<div style="display:flex;gap:8px">' + ''.join(total_card(c, pools.get(c)) for c in ('WIN', 'PLA', 'QIN', 'QPL')) + '</div>', unsafe_allow_html=True)
    race_horses = sorted(int(h) for h in snap.get('win', {}))
    qcol1, qcol2 = st.columns(2)
    with qcol1:
        combo_matrix_panel(qin_matrix, 'é€£è´ QIN', race_horses)
    with qcol2:
        combo_matrix_panel(qpl_matrix, 'ä½ç½®Q QPL', race_horses)
    hcol1, hcol2 = st.columns([1.3, 1])
    with hcol1:
        four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S, pools, cold_odds=10.0)
    with hcol2:
        signal_summary_panel(S['signal_log'], minutes=30, as_of_ts=ACTIVE_NOW_TS)
    if replay_mode:
        st.caption('æ­·å²ç†±åº¦èˆ‡è¨Šè™ŸæŒ‰ç›®å‰æ•æ„Ÿåº¦é‡ç®—ï¼›æ¨¡åž‹ä½¿ç”¨ç¾æœ‰æ¨¡åž‹åŠè©²ç­†æ­·å²æŽ’ä½é‡ç®—ã€‚')
    bar_sort = st.radio('æ£’åž‹åœ–æŽ’åº', ['é †é¦¬è™Ÿ', 'é †è³ çŽ‡ï¼ˆç†±â†’å†·ï¼‰'], horizontal=True, key='bar_sort')
    sort_key = 'è³ çŽ‡' if 'è³ çŽ‡' in bar_sort else 'é¦¬è™Ÿ'
# The placeholder is visually placed here; its controls were evaluated earlier.
# Upper panels render in the container reserved before this timeline.
_m1, _m2, _m3 = (st.session_state[f'money_t{i}'] for i in (1, 2, 3))
start_clock = datetime.fromtimestamp(ACTIVE_NOW_TS - 60, HKT).strftime('%H:%M:%S')
end_clock = datetime.fromtimestamp(ACTIVE_NOW_TS, HKT).strftime('%H:%M:%S')
st.caption(f'æœ€æ–°1åˆ†é˜ç›®æ¨™å€é–“ï¼š{start_clock} â†’ {end_clock}ï¼›é€é¦¬é‡‘é¡ç‚ºè³ çŽ‡ä½”æ¯”ä¼°ç®—ï¼Œç¼ºå°‘åŸºæº–æ™‚é¡¯ç¤ºç©ºç™½ã€‚')
baselines = [sample_at(points, ACTIVE_NOW_TS - 60) for points in S['stake_hist'].values()]
baselines = [p[0] for p in baselines if p and ACTIVE_NOW_TS - 60 - p[0] <= 45]
if baselines:
    lo, hi = min(baselines), max(baselines)
    label = datetime.fromtimestamp(lo, HKT).strftime('%H:%M:%S')
    if hi != lo:
        label += ' è‡³ ' + datetime.fromtimestamp(hi, HKT).strftime('%H:%M:%S')
    st.caption(f'å¯¦éš›å¯ç”¨åŸºæº–è¨˜éŒ„ï¼š{label}ï¼›çµ‚é»žï¼š{end_clock}ã€‚èˆŠ30ç§’è¨˜éŒ„å¯èƒ½è¼ƒç›®æ¨™å€é–“é•·ã€‚')
bcol1, bcol2 = st.columns(2)
with bcol1:
    stake_bar_chart_v(df[df['æ± '] == 'WIN'], 'ç¨è´', win_inv, S, sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
with bcol2:
    stake_bar_chart_v(df[df['æ± '] == 'PLA'], 'ä½ç½®', pla_inv, S, sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
for code in ('WIN', 'PLA'):
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool=code, m1=_m1, m2=_m2, m3=_m3, as_of_ts=ACTIVE_NOW_TS)
st.caption(f'{APP_NAME} {APP_VERSION} Â· æ­·å²è³‡æ–™ç”±ç¨ç«‹ Recorder è¨˜éŒ„')

if not replay_mode:
    st_autorefresh(interval=5000, key="live_refresh_v19_rebuilt")
