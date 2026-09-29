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

STYLE_LABELS = ("\u653e\u982d", "\u524d\u7f6e", "\u4e2d\u7f6e", "\u5f8c\u4e0a")

def classify_running_style(value, field_size):
    positions = [int(x) for x in re.findall(r"\d+", str(value or "")) if int(x) > 0]
    try:
        field = max(1, int(field_size or max(positions or [1])))
    except (TypeError, ValueError):
        field = max(1, max(positions or [1]))
    if not positions:
        return "\u672a\u77e5"
    early = positions[0]
    lead_cut = max(1, math.ceil(field * 0.25))
    front_cut = max(2, math.ceil(field * 0.50))
    back_cut = max(front_cut + 1, math.ceil(field * 0.75))
    if early <= lead_cut:
        return "\u653e\u982d"
    if early <= front_cut:
        return "\u524d\u7f6e"
    if early >= back_cut:
        return "\u5f8c\u4e0a"
    return "\u4e2d\u7f6e"

def style_from_history(style_history, horse_no):
    key = str(horse_no)
    if key not in (style_history or {}):
        try:
            key = str(int(float(horse_no)))
        except (TypeError, ValueError):
            pass
    values = list((style_history or {}).get(key, []))
    values = [x for x in values if x in STYLE_LABELS]
    return max(STYLE_LABELS, key=lambda x: values.count(x)) if values else "\u672a\u77e5"


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
APP_NAME = "HKJC \u5373\u6642\u8ce0\u7387\u76e3\u5bdf"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  DISK STORAGE (\u6c38\u4e45\u5132\u5b58 \u2014 \u5beb\u843d\u786c\u789f\uff0c\u91cd\u555f\u5514\u5931)
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
# \u6bcf\u5834\u4e00\u500b\u8cc7\u6599\u593e\uff0c\u6bcf\u500b\u6642\u9593\u9ede\u4e00\u500b JSON snapshot\uff08\u7531\u7368\u7acb Recorder \u5beb\u5165\uff09\u3002
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
              "completed_races": [], "bias_label": "\u6a23\u672c\u4e0d\u8db3"}
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
            merged["bias_label"] = f"\u5229{best}" if best in STYLE_LABELS else "\u89c0\u5bdf\u4e2d"
    except OSError:
        pass
    return merged

















# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  CONFIG / CONSTANTS
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
API = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": "https://bet.hkjc.com/",
    "User-Agent": "Mozilla/5.0",
}
HKT = timezone(timedelta(hours=8))   # Hong Kong time

INFO = "#185FA5"     # \u843d\u98db (odds down / money in) \u2014 blue
DANGER = "#A32D2D"   # \u56de\u98db (odds up / money out) \u2014 red
MUTE = "#888888"     # \u5e73\u7a69 (flat) \u2014 grey

FLAT_THRESHOLD = 2.0       # |%| <= this  -> \u5e73\u7a69 (faint grey)
PLUNGE_PCT = 8.0           # drop >= this % within PLUNGE_WINDOW -> \u63d2\u6c34 alert
PLUNGE_WINDOW = 30         # seconds
AXIS_MINUTES = 14          # countdown axis spans -14min -> 0 (post)

# \u2500\u2500 \u6295\u6ce8\u984d\u6025\u5347\u5075\u6e2c\uff08\u8cc7\u91d1\u6d41\u5411\u8868\uff09\u2500\u2500
SURGE_WINDOW = 30          # \u8fd1 N \u79d2
SURGE_MIN_DROP = 4.0       # \u8fd130\u79d2\u8ce0\u7387\u8dcc\u5e45 >= \u6b64 % -> \u25b2 \u6025\u8dcc\uff08\u6709\u9322\u5165\uff09
SURGE_BIG_DROP = 8.0       # \u8fd130\u79d2\u8ce0\u7387\u8dcc\u5e45 >= \u6b64 % -> \U0001f525 \u5927\u91cf\u6e67\u5165

# \u2500\u2500 \u56db\u6c60\u71b1\u5ea6 1\u5206\u9418\u4f54\u6bd4\u5347\u5e45 \u5206\u5c64\u9580\u6abb\uff08\u62c9\u687f\u53ef\u8abf\uff09\u2500\u2500
RISE_TIER1 = 0.5   # \u26a1 \u7559\u610f
RISE_TIER2 = 0.8   # \U0001f525 \u660e\u986f
RISE_TIER3 = 1.2   # \U0001f4a5 \u5f37\u70c8

# \u2500\u2500 \u68d2\u578b\u5716 + \u6bcf\u5206\u9418\u91d1\u984d\u8868 \u7d71\u4e00\u300c\u5be6\u8cea\u91d1\u984d\u300d\u9580\u6abb\uff08\u53e6\u4e00\u7d44\u62c9\u687f\uff09\u2500\u2500
MONEY_TIER1 = 100_000   # \u26a1 \u7559\u610f\uff08$\uff09
MONEY_TIER2 = 200_000   # \U0001f525 \u660e\u986f\uff08$\uff09
MONEY_TIER3 = 400_000   # \U0001f4a5 \u5f37\u70c8\uff08$\uff09

# \u2500\u2500 \u7d9c\u5408\u8a55\u5206\uff08100 \u5206\u5236\uff09\u914d\u7f6e \u2500\u2500
# \u5404\u9805\u6eff\u5206\uff1a\u8cc7\u91d1\u6025\u5347 40 + \u8ce0\u7387\u6025\u8dcc 30 + \u6c34\u4f4d\u6210\u719f 20 + \u7368\u4f4d\u4e00\u81f4 10 = 100
SCORE_SURGE_MAX = 40       # \u8cc7\u91d1\u6025\u5347\uff08\u76f8\u5c0d\u5168\u5834\u7a81\u51fa\u7a0b\u5ea6\uff09
SCORE_DROP_MAX  = 30       # \u8ce0\u7387\u6025\u8dcc\uff08\u7d55\u5c0d\u8dcc\u5e45\u7d1a\u5225\uff09
SCORE_WATER_MAX = 20       # \u6c34\u4f4d\u6210\u719f\uff08\u8d8a\u63a5\u8fd1 1.21 \u8d8a\u9ad8\uff09
SCORE_AGREE_MAX = 10       # \u7368\u8d0f\uff0f\u4f4d\u7f6e\u540c\u6642\u6d41\u5165
SCORE_DROP_FULL = 10.0     # \u8ce0\u7387\u8dcc >= \u6b64 % \u5f97\u6eff\u5206\uff0830\uff09
WATER_IDEAL = 1.21         # \u7406\u8ad6\u6210\u719f\u6c34\u4f4d
WATER_LOOSE = 1.45         # \u6c34\u4f4d >= \u6b64\u503c -> 0 \u5206\uff08\u672a\u6210\u719f\uff09

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

# Pool investment (turnover) \u2014 uses HKJC's EXACT official query verbatim.
# Whitelist matches the string literally, so DO NOT modify this string.
TURNOVER_QUERY = "fragment raceFragment on Race {\n  id\n  no\n  status\n  raceName_en\n  raceName_ch\n  postTime\n  country_en\n  country_ch\n  distance\n  wageringFieldSize\n  go_en\n  go_ch\n  ratingType\n  raceTrack {\n    description_en\n    description_ch\n  }\n  raceCourse {\n    description_en\n    description_ch\n    displayCode\n  }\n  claCode\n  raceClass_en\n  raceClass_ch\n  judgeSigns {\n    value_en\n  }\n}\n\nfragment racingBlockFragment on RaceMeeting {\n  jpEsts: pmPools(\n    oddsTypes: [WIN, PLA, TCE, TRI, FF, QTT, DT, TT, SixUP]\n    filters: [\"jackpot\", \"estimatedDividend\"]\n  ) {\n    leg {\n      number\n      races\n    }\n    oddsType\n    jackpot\n    estimatedDividend\n    mergedPoolId\n  }\n  poolInvs: pmPools(\n    oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n  ) {\n    id\n    leg {\n      races\n    }\n  }\n  penetrometerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  hammerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  changeHistories(filters: [\"top3\"]) {\n    type\n    time\n    raceNo\n    runnerNo\n    horseName_ch\n    horseName_en\n    jockeyName_ch\n    jockeyName_en\n    scratchHorseName_ch\n    scratchHorseName_en\n    handicapWeight\n    scrResvIndicator\n  }\n}\n\nquery raceMeetings($date: String, $venueCode: String) {\n  timeOffset {\n    rc\n  }\n  activeMeetings: raceMeetings {\n    id\n    venueCode\n    date\n    status\n    races {\n      no\n      postTime\n      status\n      wageringFieldSize\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      status\n    }\n  }\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    id\n    status\n    venueCode\n    date\n    totalNumberOfRace\n    currentNumberOfRace\n    dateOfWeek\n    meetingType\n    totalInvestment\n    country {\n      code\n      namech\n      nameen\n      seq\n    }\n    races {\n      ...raceFragment\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n    obSt: pmPools(oddsTypes: [WIN, PLA]) {\n      leg {\n        races\n      }\n      oddsType\n      comingleStatus\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      id\n      leg {\n        number\n        races\n      }\n      status\n      sellStatus\n      oddsType\n      investment\n      mergedPoolId\n      lastUpdateTime\n    }\n    ...racingBlockFragment\n    pmPools(oddsTypes: []) {\n      id\n    }\n    jkcInstNo: foPools(oddsTypes: [JKC], filters: [\"top\"]) {\n      instNo\n    }\n    tncInstNo: foPools(oddsTypes: [TNC], filters: [\"top\"]) {\n      instNo\n    }\n  }\n}"

# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  STYLES
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap');
:root {
  --bg:#0b0e14; --surface:#141925; --card:#161b27; --border:#222b3a;
  --text:#e6edf3; --subtext:#9aa7b8; --muted:#5b6675;
}
html, body, .stApp { background:var(--bg)!important; color:var(--text); font-family:'Inter',sans-serif; }
#MainMenu, footer, header { visibility:hidden; }

/* \u2500\u2500 \u6e1b\u5c11\u6bcf 5 \u79d2\u66f4\u65b0\u6642\u5605\u9583\u52d5\uff08\u53c8\u6697\u53c8\u5149\uff09\u2500\u2500 */
/* 1. \u56fa\u5b9a\u80cc\u666f\uff0crefresh \u6642\u5514\u6703\u9583\u767d */
.stApp, .main, .block-container { background:var(--bg)!important; }
/* 2. \u505c\u7528 Streamlit \u6bcf\u6b21 rerun \u5605\u6de1\u5165\u52d5\u756b\uff08\u5c31\u4fc2\u300c\u53c8\u6697\u53c8\u5149\u300d\u4e3b\u56e0\uff09 */
.stApp [data-testid="stAppViewContainer"] * { animation:none!important; }
.element-container, .stMarkdown { transition:none!important; animation:none!important; }
[data-testid="stAppViewBlockContainer"] { opacity:1!important; }
/* 3. \u66f4\u65b0\u6642\u5605\u300crunning\u300d\u534a\u900f\u660e\u906e\u7f69\uff0c\u4ee4\u4f62\u5514\u6703\u4ee4\u5168\u9801\u8b8a\u6697 */
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

/* \u2500\u2500 st.container(border=True) \u6539\u8fd4\u8ddf .panel \u4e00\u6a23\u5605\u6df1\u8272\u98a8\u683c\uff08\u53f3\u908a\u8a0a\u865f\u5f59\u7e3d\u7528\uff09\u2500\u2500 */
[data-testid="stVerticalBlockBorderWrapper"] {
  background:var(--card) !important; border:1px solid var(--border) !important;
  border-radius:12px !important; padding:2px 14px 14px !important;
}
[data-testid="stVerticalBlockBorderWrapper"] [data-testid="stExpander"] {
  background:transparent; border:1px solid var(--border); border-radius:8px; margin-bottom:4px;
}

/* \u2500\u2500 \u2462\u56db\u6c60\u71b1\u5ea6 + 30\u5206\u9418\u8a0a\u865f\u5f59\u7e3d\uff1a\u5169\u500bcolumn stretch\u53bb\u5230\u4e00\u6a23\u9ad8 \u2500\u2500 */
.st-key-heat_signal_row [data-testid="stHorizontalBlock"] { align-items:stretch; }
.st-key-heat_signal_row [data-testid="column"] > div { height:100%; }
.st-key-heat_signal_row .panel { height:100%; box-sizing:border-box; }
.st-key-heat_signal_row [data-testid="stVerticalBlockBorderWrapper"] { height:100%; box-sizing:border-box; }

/* \u2500\u2500 \u624b\u6a5f\u512a\u5316\uff08\u7a84\u87a2\u5e55\uff09\u2500\u2500 */
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
  /* \u8b93\u5de6\u53f3\u5169\u6b04\uff08\u7368\u8d0f/\u4f4d\u7f6e\uff09\u55ba\u624b\u6a5f\u76f4\u63a5\u4e0a\u4e0b\u6392 */
  [data-testid="column"] { width:100% !important; flex:1 1 100% !important;
    min-width:100% !important; }
}
</style>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
""", unsafe_allow_html=True)



# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  SESSION STATE (multi-race: each race keeps its own history)
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
def _blank_state():
    return {
        "race_key": None,
        "open_odds": {},      # (pool, horse) -> first odds seen
        "last_odds": {},      # (pool, horse) -> previous odds
        "series": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(x_min, odds)]
        "stake_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts_epoch, stake$)]
        "ever_surged": {},    # (pool,horse) -> peak drop% ever seen (for \U0001f525 memory)
        "share_hist": defaultdict(lambda: deque(maxlen=400)),  # (pool,horse)->[(ts,share%)]
        "post_time": None,    # datetime in HKT
        "started_at": None,   # when monitoring began
        "signal_log": deque(maxlen=300),  # [{ts,horse,pool,tier,rise}] 30\u5206\u9418\u8a0a\u865f\u8a18\u9304
        "_last_logged_tier": {},   # horse -> \u4e0a\u6b21\u8a18\u9304\u5605 tier\uff08\u5347\u7d1a\u5148\u518d\u8a18\uff0c\u907f\u514d\u6d17\u7248\uff09
        "_last_logged_ts": {},     # horse -> \u4e0a\u6b21\u8a18\u9304\u6642\u9593\uff08\u540c tier \u76f8\u540c\u6642\u969460\u79d2\u5148\u518d\u8a18\uff09
        "_signal_log_synced_ts": 0.0,  # \u7531\u786c\u789f\u88dc\u9f4a signal_log \u88dc\u5230\u908a
    }

# RACES: race_key -> state dict. Switching races no longer wipes data;
# each race accumulates independently and is remembered.

# \u958b\u6a5f\u8b80\u4e00\u6b21\u786c\u789fsettings\uff08\u5982\u679c\u4e4b\u524d\u64b3\u904e\u300c\u5132\u5b58\u8a2d\u5b9a\u300d\uff09\uff0c\u88dc\u505a\u9810\u8a2d\u503c\u3002



# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  DATA FETCH
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
def _to_float(x):
    try: return float(x)
    except: return 0.0


def fetch_all_meetings():
    """#8\uff1a\u7528 activeMeetings \u81ea\u52d5\u651e\u6652\u6240\u6709\u300e\u6709\u8cfd\u4e8b\u300f\u5605\u65e5\u671f+\u5834\u5730\uff08\u5305\u62ec\u6d77\u5916\uff09\u3002
    Returns list of {date, venue, label, races}. \u5514\u4f7f\u624b\u52d5\u63c0\u65e5\u671f/\u5834\u5730\u3002"""
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
    # \u53bb\u91cd + \u6309\u65e5\u671f\u6392
    seen = set(); uniq = []
    for m in out:
        k = (m["date"], m["venue"])
        if k in seen:
            continue
        seen.add(k); uniq.append(m)
    return sorted(uniq, key=lambda x: (x["date"], x["venue"]))

VENUE_NAMES = {"ST": "\u6c99\u7530", "HV": "\u8dd1\u99ac\u5730"}
def venue_label(v):
    return VENUE_NAMES.get(v, v)   # \u6d77\u5916\u5834\u5730\u5c31\u76f4\u63a5\u986f\u793a code










# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  ENRICH + HISTORY
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
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
        key = (r["\u6c60"], r["\u99ac\u865f"])
        curr = r["\u8ce0\u7387"]
        new_last[key] = curr

        # open odds (locked once)
        if key not in S["open_odds"] and curr > 0:
            S["open_odds"][key] = curr
        opn = S["open_odds"].get(key, curr)

        # % change vs open \u2014 sign follows odds movement:
        #   negative = odds dropped (\u843d\u98db), positive = odds rose (\u56de\u98db)
        if opn and opn > 0:
            pct = (curr - opn) / opn * 100.0
        else:
            pct = 0.0

        if abs(pct) <= FLAT_THRESHOLD:
            d = "flat"
        elif pct < 0:
            d = "down"   # odds dropped => \u843d\u98db
        else:
            d = "up"     # odds rose => \u56de\u98db

        open_arr.append(opn); live_arr.append(curr)
        chg_arr.append(pct); dir_arr.append(d)

        # Store the same timestamp used for this calculation. In REPLAY the
        # selected snapshot timestamp is used; LIVE uses wall-clock time.
        if record and curr > 0:
            S["series"][key].append((now_hkt.timestamp(), curr))

    S["last_odds"] = new_last
    df["\u958b\u8ce0"] = open_arr
    df["\u5373\u5834"] = live_arr
    df["\u8b8a\u5316"] = chg_arr
    df["\u65b9\u5411"] = dir_arr
    return df, mtp



def recent_speed(S, key, seconds=30):
    """Decay-weighted recent % move magnitude over last N sec \u2014 for ranking & plunge."""
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
    """\u8a72\u99ac\u5373\u5834\u7d2f\u7a4d\u7e3d\u6295\u6ce8\u984d\uff08\u4f54\u6bd4\u6cd5\uff0c= \u68d2\u578b\u5716\u68d2\u9ad8 = \u6bcf\u5206\u9418\u8868\u5408\u8a08\uff09\u3002"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    return hist[-1][1] if hist else None



# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
#  RENDER HELPERS
# \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550






def _fmt_money(v):
    """Only missing/nonfinite values are blank; zero and negative estimates are data."""
    if v is None:
        return "\u2014"
    value = float(v)
    if not math.isfinite(value):
        return "\u2014"
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.0f}K"
    return f"{sign}${amount:.0f}"





def _nice_ceiling(maxv):
    """\u81ea\u52d5\u63c0\u975a\u9802 + \u9593\u683c\uff081/2/2.5/5 \u00d7 10^n\uff09\uff0c\u76ee\u6a19\u7d04 5 \u683c\u3002"""
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

def stake_bar_chart_v(df_pool, pool_name, pool_inv, S, sort_by="\u99ac\u865f",
                      m1=100_000, m2=200_000, m3=400_000, mtp=None, as_of_ts=None):
    """\u76f4\u5411\u68d2\u578b\u5716\uff1a\u68d2\u9ad8\uff1d\u4f30\u7b97\u6295\u6ce8\u984d\uff08\u8ce0\u7387\u4f54\u6bd4 \u00d7 \u5f69\u6c60\u7e3d\u984d\uff09\u3002
    \u68d2\u8272\uff1d\u6700\u8fd1\u4e00\u500b\u5b8c\u6574\u5206\u9418\u6d41\u5165\uff08\u540c\u6bcf\u5206\u9418\u8868 -1\u5206\u683c\u540c\u6b65\uff09\u3002Y\u8ef8\u91d1\u984d\u523b\u5ea6\uff08\u81ea\u52d5\u8ddf\u6700\u5927\uff09\u3002"""
    if pool_inv is None or pool_inv <= 0:
        st.markdown(
            f'<div class="panel"><div class="panel-title">\U0001f4ca {pool_name}\u6295\u6ce8\u984d\u68d2\u578b\u5716</div>'
            f'<div class="panel-sub">\u66ab\u7121\u5f69\u6c60\u91d1\u984d</div></div>', unsafe_allow_html=True)
        return
    sub = df_pool[df_pool["\u5373\u5834"] > 0].copy()
    if sub.empty:
        st.markdown(
            f'<div class="panel"><div class="panel-title">\U0001f4ca {pool_name}\u6295\u6ce8\u984d\u68d2\u578b\u5716</div>'
            f'<div class="panel-sub">\u6709\u5f69\u6c60\u91d1\u984d {_fmt_money(pool_inv)}\uff0c\u4f46\u672a\u6709\u9010\u5339\u99ac\u8ce0\u7387</div></div>',
            unsafe_allow_html=True)
        return
    pool_code = str(df_pool["\u6c60"].iloc[0]) if len(df_pool) else pool_name
    inv_live = 1.0 / sub["\u5373\u5834"]
    sub["\u6295\u6ce8\u984d"] = inv_live / inv_live.sum() * pool_inv   # \u5373\u5834\u7e3d\u6295\u6ce8\uff08\u4f54\u6bd4\u6cd5\uff09
    if sort_by == "\u8ce0\u7387":
        sub = sub.sort_values("\u5373\u5834")
    else:
        sub = sub.sort_values("\u99ac\u865f", key=lambda s: pd.to_numeric(s, errors="coerce"))
    max_stake = sub["\u6295\u6ce8\u984d"].max() if len(sub) else 1
    top, step = _nice_ceiling(max_stake)

    # \u6700\u8fd1\u4e00\u500b\u5b8c\u6574\u5206\u9418\u7a97\uff08\u540c\u6bcf\u5206\u9418\u8868 -1\u5206\u683c\u4e00\u81f4\uff09
    now_ts = as_of_ts if as_of_ts is not None else datetime.now(HKT).timestamp()
    m_end, m_start = now_ts, now_ts - 60

    # Y\u8ef8\u523b\u5ea6 HTML\uff08\u7d55\u5c0d\u5b9a\u4f4d\u55ba\u5de6\u908a\uff09
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
        horse = r["\u99ac\u865f"]
        odds = r["\u5373\u5834"]
        stake = r["\u6295\u6ce8\u984d"]
        # \u68d2\u8272\uff1a\u6700\u8fd1\u4e00\u500b\u5b8c\u6574\u5206\u9418\u6d41\u5165\uff08\u540c\u6bcf\u5206\u9418\u8868\u540c\u6b65\uff09
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
        PLOT_H = 130   # \u7e6a\u5716\u5340\u9ad8\u5ea6\uff08px\uff09\uff0c\u68d2\u7528 px \u8a08\uff0c\u5514\u7528 % \uff08% \u6703\u56e0\u70ba\u7236\u5c64\u5187\u56fa\u5b9a\u9ad8\u800c\u584c\uff09
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
        f'<div class="panel-title">\U0001f4ca {pool_name}\u6295\u6ce8\u984d\u68d2\u578b\u5716</div>'
        f'<div class="panel-sub">\u68d2\u9ad8\uff1d\u7e3d\u6295\u6ce8\u91d1\u984d\uff08Y\u8ef8\u81ea\u52d5\u523b\u5ea6\uff09\u00b7 \u8fd11\u5206\u9418\u6d41\u5165 \u26a1{_fmt_money(m1)}\u9ec3/\U0001f525{_fmt_money(m2)}\u6a59/\U0001f4a5{_fmt_money(m3)}\u7d2b \u8b8a\u8272\uff08\u8207\u91d1\u984d\u8868\u540c\u6b65\uff09</div>'
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
    flowed into that horse that minute. Uses stake = pool\u00d70.825/odds reversal.
    Only shown when post time known (needs countdown minutes)."""
    pool_inv = win_inv if pool == "WIN" else pla_inv
    title = "\u7368\u8d0f" if pool == "WIN" else "\u4f4d\u7f6e"
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df[df["\u6c60"] == pool].copy()
    sub = sub[sub["\u5373\u5834"] > 0]
    if sub.empty:
        return
    if mtp is None:
        st.markdown(
            f'<div class="panel"><div class="panel-title">\U0001f4cb \u6bcf\u5206\u9418\u843d\u6ce8\u91d1\u984d\u8868\uff08{title}\uff09</div>'
            f'<div class="panel-sub">\u9700\u8981\u958b\u8dd1\u6642\u9593\u5148\u8a08\u5012\u6578\u5206\u9418 \u2014 \u8acb\u55ba\u4e0a\u65b9\u586b\u958b\u8dd1\u6642\u9593</div></div>',
            unsafe_allow_html=True)
        return

    # \u2500\u2500 \u6642\u9593\u8ef8\u683c\u4ed4\uff08\u5de6\uff1d\u65e9\uff0c\u53f3\uff1d\u958b\u8dd1\uff09\u2500\u2500 v17.5\uff1a\u9694\u591c/\u7576\u65e5 + \u56fa\u5b9a60/30/20/10 + \u9010\u5206\u9418\u3002
    # \u9694\u591c/\u7576\u65e5\u5605\u908a\u754c\u7528\u56fa\u5b9a\u5605 00:00\uff08\u5514\u7406\u500b\u5225\u5834\u6b21\u958b\u8dd1\u6642\u9593\uff09\uff0c\u89e3\u6c7a #6b\uff1a
    # \u540c\u4e00\u65e5\u5514\u540c\u5834\u958b\u8dd1\u6642\u9593\u5514\u540c\uff0c\u4f46\u65e9\u6bb5\uff08\u9694\u591c/\u7576\u65e5\uff09\u7406\u61c9\u5b8c\u5168\u4e00\u81f4\u3002
    post_ts = S["post_time"].timestamp() if S["post_time"] else None
    post_dt_local = S["post_time"] if S["post_time"] else None

    def edge_ts(min_before):
        return post_ts - min_before * 60 if post_ts is not None else None

    # \u7576\u65e5 00:00\uff08\u56fa\u5b9a\uff0c\u5514\u8ddf\u958b\u8dd1\u6642\u9593\u6d6e\u52d5\uff09
    midnight_dt = datetime(post_dt_local.year, post_dt_local.month, post_dt_local.day,
                           0, 0, 0, tzinfo=HKT) if post_dt_local else None
    midnight_ts = midnight_dt.timestamp() if midnight_dt else None

    # \u9010\u683c\u5b9a\u7fa9\uff1a(label, is_hour[\u65e9\u6bb5/\u6574\u9ede\u683c,\u5514\u8b8a\u8272], ts_start, ts_end)
    # 60/30/20/10\uff1a\u5462\u683c\u4ee3\u8868\u300c\u7531\u5462\u500b\u5206\u9418\u6578\u958b\u59cb\uff0c\u53bb\u5230\u4e0b\u4e00\u500b\u523b\u5ea6\u300d\u5605\u4e00\u6bb5\u6d41\u5165
    #\uff08\u4f8b\u5982\u300c60\u300d= \u958b\u8dd1\u524d60\u5206\u9418 \u2192 \u958b\u8dd1\u524d30\u5206\u9418 \u5462\u6bb5\uff09\u300210\u4e4b\u5f8c\u9010\u5206\u9418\u53bb\u5230\u958b\u8dd1\u3002
    ladder = [60, 30, 20, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
    cols = [
        ("\u9694\u591c", True, False, None, midnight_ts),
        ("\u7576\u65e5", True, False, midnight_ts, edge_ts(60)),
    ]
    for i in range(len(ladder) - 1):
        start_e, end_e = ladder[i], ladder[i + 1]
        lbl = "\u958b\u8dd1" if end_e == 0 else str(start_e)
        is_hour = start_e >= 60   # 60\u5462\u683c\u4ef2\u4fc2\u5927\u683c,\u5514\u8b8a\u8272\uff1b30/20/10\u4e4b\u5f8c\u5605\u9010\u5206\u9418\u683c\u5148\u8b8a\u8272
        cols.append((lbl, is_hour, False, edge_ts(start_e), edge_ts(end_e)))

    latest_end_ts = (as_of_ts if as_of_ts is not None
                     else datetime.now(HKT).timestamp())
    cols.append(("\u6700\u65b01\u5206", False, True, latest_end_ts - 60, latest_end_ts))

    def stake_bucket(horse, ts_start, ts_end):
        if ts_end is None:
            return None
        if ts_start is None:
            # \u7531\u6700\u65e9\u8a18\u9304\u5230 ts_end \u5605\u7d2f\u7a4d
            s_end = stake_at_ts(S, pool, horse, ts_end)
            hist = S["stake_hist"][(pool, str(horse))]
            s_start = hist[0][1] if hist else None
            if s_end is None or s_start is None:
                return None
            return s_end - s_start
        if ts_end == latest_end_ts and ts_start == latest_end_ts - 60:
            return latest_minute_gain(S, pool, horse, latest_end_ts)
        return stake_in_bucket(S, pool, horse, ts_start, ts_end)

    # \u300c\u9694\u591c\u300d\u300c\u7576\u65e5\u300d\u7531\u786c\u789f\u8a08\uff08\u5514\u53d7\u8a18\u61b6\u9ad4deque\u4e0a\u9650\u5f71\u97ff\uff0c\u958b\u8ce3\u63d0\u524d\u5e7e\u8010\u90fd\u5571\uff09\uff1b
    # 60/30/20/10\u540c\u9010\u5206\u9418\u5c31\u7528\u8fd4\u8a18\u61b6\u9ad4\uff08\u5920\u8fd1\uff0c\u5514\u4f7f\u62d6\u786c\u789f\uff09\u3002
    # disk_race_key\uff1aREPLAY \u63c0\u55f0\u5834\u5605 key\uff08\u540c\u4e0a\u9762\u4e0b\u62c9\u9078\u55ae\u53ef\u80fd\u5514\u540c\u5834\uff09\uff0c
    # \u5187\u50b3\u5c31\u7528\u8fd4 S \u81ea\u5df1\u55f0\u500b\uff08LIVE \u60c5\u6cc1\uff09\u3002
    early_map = {}
    for horse in sub["\u99ac\u865f"]:
        v_mid = stake_at_ts(S, pool, horse, min(midnight_ts, S['as_of_ts'])) if midnight_ts else None
        v_60 = stake_at_ts(S, pool, horse, min(edge_ts(60), S['as_of_ts'])) if edge_ts(60) else None
        early_map[str(horse)] = {"\u9694\u591c": v_mid, "\u7576\u65e5": v_60 - v_mid if v_mid is not None and v_60 is not None and S['as_of_ts'] >= midnight_ts else None}

    rows_data = []
    for _, r in sub.sort_values("\u5373\u5834").iterrows():
        horse = str(r["\u99ac\u865f"])
        odds = r["\u5373\u5834"]
        eb = early_map.get(horse, {})
        per_col = [eb.get("\u9694\u591c"), eb.get("\u7576\u65e5")]
        per_col += [stake_bucket(horse, s, e) for (_, _, _, s, e) in cols[2:]]
        rows_data.append((horse, odds, per_col))

    def cellcol(v, is_hour):
        if v is None:
            return "var(--muted)"
        if is_hour:
            return "var(--subtext)"   # \u65e9\u6bb5/\u5927\u683c\u5514\u8b8a\u8272
        if v >= m3: return "#c878ff"
        if v >= m2: return "#ff8c3c"
        if v >= m1: return "#ffd43b"
        return "var(--subtext)"

    # header
    head = '<th style="text-align:left;padding:3px 5px;font-size:9px;color:var(--muted);position:sticky;left:0;background:var(--card)">\u99ac \u8ce0</th>'
    for (lbl, is_hour, is_prev, _, _) in cols:
        col_bg = "background:rgba(30,30,44,0.5);" if is_hour else ""
        sync_head = 'border-left:2px solid rgba(80,170,255,0.55);' if is_prev else ''
        head += (f'<th style="text-align:right;padding:2px 5px;font-size:9px;color:{"#78899a" if is_hour else "var(--muted)"};{col_bg}{sync_head}">'
                 f'{lbl}</th>')
    head += '<th style="text-align:right;padding:3px 5px;font-size:9px;color:#e0a83c">\u5408\u8a08</th>'

    body = ""
    for horse, odds, per_col in rows_data:
        # \u5408\u8a08 = \u5373\u5834\u7e3d\u6295\u6ce8\uff08\u4f54\u6bd4\u6cd5\uff0c\u540c\u68d2\u578b\u5716\u68d2\u9ad8\u4e00\u81f4\uff09
        total = current_stake(S, pool, horse)
        if total is None:
            total = sum(v for v in per_col[:-1] if v) or 0
        cells = ""
        for (lbl, is_hour, is_prev, _, _), v in zip(cols, per_col):
            txt = f'+{_fmt_money(v)}' if (v and v > 0) else ('\u2014' if v is None else _fmt_money(v))
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
        f'<div class="panel-title">\U0001f4cb \u843d\u6ce8\u91d1\u984d\u8868\uff08{title} \u00b7 \u6642\u9593\u7531\u5de6\u5230\u53f3\uff09</div>'
        f'<div class="panel-sub">\u9694\u591c(\u958b\u8ce3\u219200:00) \u00b7 \u7576\u65e5(00:00\u2192-60\u5206,\u5168\u5834\u4e00\u81f4) \u00b7 '
        f'60/30/20/10(\u6bcf\u6bb5) \u00b7 10\u5206\u4e4b\u5f8c\u9010\u5206\u9418 \u2192 \u958b\u8dd1 \u00b7 \u6700\u65b01\u5206\uff1d\u756b\u9762\u6642\u9593\u5411\u524d60\u79d2 \u00b7 '
        f'\u26a1{_fmt_money(m1)}\u9ec3/\U0001f525{_fmt_money(m2)}\u6a59/\U0001f4a5{_fmt_money(m3)}\u7d2b\uff08\u53ea\u81e8\u5834\u9010\u5206\u9418\u683c\u8b8a\u8272\uff09\u00b7 \u5408\u8a08\uff1d\u7e3d\u6295\u6ce8\uff08\u540c\u68d2\u578b\u5716\uff09</div>'
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
            f'<div class="panel"><div class="panel-title">\U0001f3b2 {pool_title}</div>'
            f'<div class="panel-sub">\u66ab\u7121\u8cc7\u6599</div></div>', unsafe_allow_html=True)
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
        f'<div class="panel-title">\U0001f3b2 {pool_title}</div>'
        f'<div class="panel-sub">\u7d05\uff1d\u6700\u71b1\u7d44\u5408\uff08\u8ce0\u7387\u6700\u4f4e {min_odds:g}\uff09\u00b7 \u4ea4\u53c9\u683c\uff1d\u8a72\u5c0d\u99ac\u8ce0\u7387</div>'
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
    win = df[df["\u6c60"] == "WIN"].copy()
    if win.empty:
        return
    inv = 1.0 / win["\u5373\u5834"]
    win["W%"] = inv / inv.sum() * 100.0
    win_share = {int(k): v for k, v in zip(win["\u99ac\u865f"], win["W%"])}

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
    for _, r in win.sort_values("\u5373\u5834").iterrows():
        h = int(r["\u99ac\u865f"])
        wo = r["\u5373\u5834"]
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
            tier, tier_col, tier_tag = 3, "#c878ff", "\U0001f4a5\u5f37\u70c8"
        elif max_rise >= t2:
            tier, tier_col, tier_tag = 2, "#ff8c3c", "\U0001f525\u660e\u986f"
        elif max_rise >= t1:
            tier, tier_col, tier_tag = 1, "#ffd43b", "\u26a1\u7559\u610f"
        else:
            tier, tier_col, tier_tag = 0, "#5b6675", ""

        # pool % cells: all neutral grey (no green top-3)
        def cell(pct):
            return f'<span class="c-num" style="color:#9aa7b8">{pct:.1f}%</span>'

        marker = ""
        if suspicious:
            marker = ' \U0001f4a5\u53ef\u7591' if tier >= 3 else (' \U0001f525\u53ef\u7591' if tier >= 1 else ' \u53ef\u7591')
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
        rise_disp = f"+{max_rise:.1f}%" if max_rise >= 0.1 else "\u2014"

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

    # money reference line: \u76f4\u63a5\u5c07\u26a1/\U0001f525/\U0001f4a5\u4e09\u7d1a\u9580\u6abb\uff08%\uff09\u63db\u7b97\u505a\u5404\u6c60\u5be6\u969b\u89f8\u767c\u91d1\u984d\uff08$\uff09\uff0c
    # \u5c0d\u61c9\u771f\u6b63\u6c7a\u5b9a\u8a0a\u865f\u5605 share_rise() \u9580\u6abb\uff0c\u5514\u4f7f\u7528\u6236\u81ea\u5df1\u651e\u300c1%\u300d\u518d\u5fc3\u7b97\u4e00\u6b21\u3002
    def tier_money(total, pct):
        return _fmt_money(total * pct / 100.0) if total else "\u2014"
    money_ref = (
        f'\u89f8\u767c\u91d1\u984d\u5c0d\u7167\uff08\u5373\u5834\u5f69\u6c60 \u00d7 \u9580\u6abb%\uff09\uff1a<br>'
        f'\u26a1{t1:g}% \u7368\u8d0f{tier_money(wt, t1)}/\u4f4d\u7f6e{tier_money(pt, t1)}/'
        f'\u9023\u8d0f{tier_money(qt, t1)}/\u4f4d\u7f6eQ{tier_money(qpt, t1)}<br>'
        f'\U0001f525{t2:g}% \u7368\u8d0f{tier_money(wt, t2)}/\u4f4d\u7f6e{tier_money(pt, t2)}/'
        f'\u9023\u8d0f{tier_money(qt, t2)}/\u4f4d\u7f6eQ{tier_money(qpt, t2)}<br>'
        f'\U0001f4a5{t3:g}% \u7368\u8d0f{tier_money(wt, t3)}/\u4f4d\u7f6e{tier_money(pt, t3)}/'
        f'\u9023\u8d0f{tier_money(qt, t3)}/\u4f4d\u7f6eQ{tier_money(qpt, t3)}'
        f'\uff08\u9023\u8d0f/\u4f4d\u7f6eQ\u91d1\u984d\u70ba\u7c97\u4f30\uff09'
    )

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">\U0001f3af \u56db\u6c60\u7d9c\u5408\u71b1\u5ea6</div>'
        f'<div class="panel-sub">\u6309\u7368\u8d0f\u8ce0\u7387\u6392\u5e8f \u00b7 \u5e73\u6642\u4e7e\u6de8 \u00b7 '
        f'1\u5206\u5347 \u26a1{t1:g}%/\U0001f525{t2:g}%/\U0001f4a5{t3:g}% \u00b7 \u51b7\u99ac(\u2265{cold_odds:g}\u500d)\u591a\u6c60\u7686\u71b1\uff1d\u53ef\u7591<br>{money_ref}</div>'
        f'<div class="thead">'
        f'<span class="c-no">\u99ac \u8ce0\u7387</span>'
        f'<span class="c-num">\u7368\u8d0f</span><span class="c-num">\u4f4d\u7f6e</span>'
        f'<span class="c-num">\u9023\u8d0f</span><span class="c-num">\u4f4d\u7f6eQ</span>'
        f'<span class="c-num">1\u5206\u5347</span><span class="c-num">\u7686\u71b1</span></div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ffd43b"></i>\u26a1\u7559\u610f</span>'
        f'<span><i style="background:#ff8c3c"></i>\U0001f525\u660e\u986f</span>'
        f'<span><i style="background:#c878ff"></i>\U0001f4a5\u5f37\u70c8</span>'
        f'<span><i style="background:#ff5757"></i>\u51b7\u99ac\u7686\u71b1\u53ef\u7591</span>'
        f'</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def signal_summary_panel(events, minutes=30, as_of_ts=None):
    """30\u5206\u9418\u8a0a\u865f\u5f59\u7e3d\uff08\u53f3\u908a\u65b0\u9762\u677f\uff09\uff1a\u6309\u99ac\u5206\u7d44\uff0c\u64b3\u958b\u7747\u9010\u884c\u6642\u5e8f\u7d30\u7bc0\u3002
    events: list of {ts,horse,pool,tier,rise}\u3002as_of_ts=None \u7528\u800c\u5bb6\u6642\u9593\uff1b
    REPLAY \u6a21\u5f0f\u6703\u50b3\u8fd4\u55f0\u500bsnapshot\u5605ts\uff0c\u7b49\u500b30\u5206\u9418\u7a97\u8ddf\u8fd4\u7ffb\u7747\u7dca\u55f0\u4e00\u523b\u3002
    \u7528 st.container(border=True) \u5305\u4f4f\u6210\u500bpanel\uff08\u9023\u66ab\u7121\u8a0a\u865f\u90fd\u55baborder\u5165\u9762\uff09\uff0c
    \u7b49\u500bbox\u53ef\u4ee5\u81ea\u52d5stretch\u53bb\u5230\u540c\u5de6\u908a\u300c\u56db\u6c60\u7d9c\u5408\u71b1\u5ea6\u300d\u4e00\u6a23\u9ad8\uff08CSS\u55ba\u5225\u8655\u63a7\u5236\uff09\u3002"""
    if as_of_ts is None:
        as_of_ts = datetime.now(HKT).timestamp()
    cutoff = as_of_ts - minutes * 60
    evs_in_window = [e for e in events if cutoff <= e.get("ts", 0) <= as_of_ts]

    with st.container(border=True):
        st.markdown(
            f'<div class="panel-title">\U0001f550 {minutes}\u5206\u9418\u8a0a\u865f\u5f59\u7e3d</div>'
            f'<div class="panel-sub">\u6309\u99ac\u5206\u7d44 \u00b7 \u64b3\u96bb\u99ac\u5c55\u958b\u6642\u5e8f\u7d30\u7bc0 \u00b7 \u904e\u5497{minutes}\u5206\u9418\u81ea\u52d5\u79fb\u9664</div>',
            unsafe_allow_html=True)

        if not evs_in_window:
            st.caption("\u66ab\u7121\u8a0a\u865f")
            return

        by_horse = defaultdict(list)
        for e in evs_in_window:
            by_horse[e["horse"]].append(e)

        tier_emoji = {1: "\u26a1", 2: "\U0001f525", 3: "\U0001f4a5"}

        def horse_key(h):
            evs = by_horse[h]
            return (-max(ev["tier"] for ev in evs), -max(ev["ts"] for ev in evs))

        for h in sorted(by_horse.keys(), key=horse_key):
            evs = sorted(by_horse[h], key=lambda e: e["ts"], reverse=True)
            counts = {1: 0, 2: 0, 3: 0}
            for e in evs:
                counts[e["tier"]] = counts.get(e["tier"], 0) + 1
            summary = "\u3000".join(f'{tier_emoji[t]}\u00d7{counts[t]}' for t in (3, 2, 1) if counts.get(t))
            with st.expander(f"{h}\u865f\u3000{summary}", expanded=False):
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
        raise RuntimeError(MODEL_IMPORT_ERROR or "\u6a21\u578b\u6a21\u7d44\u672a\u80fd\u8f09\u5165")
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
        return pd.DataFrame(), MODEL_IMPORT_ERROR or "\u672a\u6709\u5b8c\u6574\u6392\u4f4d\uff0f\u5373\u6642\u7368\u8d0f\u8ce0\u7387"
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
            return pd.DataFrame(), "\u6709\u6548\u7368\u8d0f\u8ce0\u7387\u4e0d\u8db3"
        race_idx = np.zeros(len(base), dtype=int)
        p_public = public_probabilities(base["win_odds"].to_numpy(float), race_idx)
        eps = 1e-12
        z = (float(bundle["ss_alpha"]) * np.log(np.clip(base["p_model"], eps, 1.0))
             + float(bundle["ss_beta"]) * np.log(np.clip(p_public, eps, 1.0)))
        p_final = _group_softmax(z)
        # These are deliberately small, visible adjustments. They only use
        # completed same-day evidence and historical style, never future results.
        style_weight = {"\u653e\u982d": 0.08, "\u524d\u7f6e": 0.04, "\u4e2d\u7f6e": -0.02, "\u5f8c\u4e0a": -0.06}
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
            sa = style_weight.get(style, 0.0) if style != "\u672a\u77e5" else 0.0
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
        return pd.DataFrame(), f"\u6a21\u578b\u8a08\u7b97\u5931\u6557\uff1a{exc}"


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
        raise ValueError('\u5834\u6b21\u6216\u8a18\u9304\u683c\u5f0f\u4e0d\u7b26')
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
                raise ValueError('\u540c\u4e00\u99ac\u865f\u6709\u885d\u7a81\u8ce0\u7387\uff1a' + canonical)
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
        raise RuntimeError('HKJC \u56de\u50b3\u67e5\u8a62\u932f\u8aa4')
    meeting = matching_meeting((body.get('data') or {}).get('raceMeetings'), date_str, venue)
    if meeting is None:
        raise RuntimeError(f'HKJC \u672a\u56de\u50b3\u76f8\u7b26\u8cfd\u65e5\uff1a{date_str} {venue}')
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
            raise RuntimeError('HKJC \u8ce0\u7387\u67e5\u8a62\u932f\u8aa4')
        meetings = (body.get('data') or {}).get('raceMeetings') or []
        if len(meetings) != 1:
            raise RuntimeError('HKJC \u8ce0\u7387\u56de\u61c9\u8cfd\u65e5\u6578\u91cf\u7570\u5e38')
        return meetings[0].get('pmPools') or [], datetime.now(HKT).timestamp()
    with ThreadPoolExecutor(max_workers=2) as executor:
        meeting_future = executor.submit(fetch_full_meeting, d, v)
        odds_future = executor.submit(odds_request)
        meeting, meeting_ts = meeting_future.result()
        pools, odds_ts = odds_future.result()
    snap = snapshot_from_pools(meeting, int(r), pools, max(meeting_ts, odds_ts), meeting_ts)
    if snap is None:
        raise RuntimeError('\u5462\u5834\u66ab\u6642\u5187\u6709\u6548\u7368\u8d0f\u8ce0\u7387')
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
    st.caption('REPLAY \u6642\u9593\u8ef8\uff1a\u6578\u5b57\u8868\u793a\u958b\u8dd1\u524d\u5206\u9418\uff1b\u7f3a\u5c11\u8a72\u6642\u6bb5\u8a18\u9304\u6703\u63d0\u793a\u3002')
    targets = [('\u9694\u591c', None), ('60', 60), ('30', 30), ('20', 20), ('10', 10),
               ('9', 9), ('8', 8), ('7', 7), ('6', 6), ('5', 5), ('4', 4), ('3', 3), ('2', 2), ('1', 1), ('\u958b\u8dd1', 0)]
    for (label, minutes), col in zip(targets, st.columns(len(targets))):
        with col:
            if st.button(label, key=f'clip::{race_key}::{label}', use_container_width=True):
                if post_ts is None:
                    st.warning('\u672a\u6709\u958b\u8dd1\u6642\u9593')
                else:
                    post = datetime.fromtimestamp(post_ts, HKT)
                    target = post.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() if minutes is None else post_ts - minutes * 60
                    index = select_replay_index(snaps, target)
                    if index is None:
                        st.warning('\u5462\u6bb5\u6642\u9593\u5187\u76f8\u8fd1\u8a18\u9304')
                    else:
                        st.session_state[state_key] = index
    if len(snaps) > 1:
        idx = st.slider('\u6642\u9593\u8ef8\uff08\u62c9\u53bb\u4efb\u4f55\u4e00\u523b\uff0c\u5fae\u8abf\uff09', 0, len(snaps) - 1, key=state_key)
    else:
        idx = 0
        st.caption('\u53ea\u6709\u4e00\u500b\u8a18\u9304\u9ede\uff0c\u672a\u80fd\u79fb\u52d5\u6642\u9593\u8ef8\u3002')
    st.caption(f"\u6642\u9593\u9ede\uff1a{datetime.fromtimestamp(times[idx], HKT):%Y-%m-%d %H:%M:%S}\u3000\u5171 {len(snaps)} \u500b\u8a18\u9304\u9ede")
    return idx

if '_settings_loaded_rebuilt' not in st.session_state:
    for key, value in load_settings().items():
        st.session_state.setdefault(key, value)
    st.session_state['_settings_loaded_rebuilt'] = True

st.markdown(f'<div class="hdr"><div class="hdr-title">\U0001f40e {APP_NAME}</div><span class="live">{APP_VERSION}</span></div>', unsafe_allow_html=True)
_src_col, _sep_col, _mode_col = st.columns([2.4, 0.1, 2])
with _mode_col:
    mode = st.radio('\u6a21\u5f0f', ['\u25cf LIVE \u5373\u5834', '\U0001f501 REPLAY \u7ffb\u7747'], horizontal=True, key='mode_v19_rebuilt')
replay_mode = 'REPLAY' in mode
with _src_col:
    st.caption('\U0001f4e1 Recorder \u6b77\u53f2\u8a18\u9304' if replay_mode else '\U0001f4e1 \u76f4\u63a5\u9023\u7dda HKJC \u00b7 \u76ee\u6a19\u6bcf5\u79d2\u66f4\u65b0')

controls_slot = st.container()
settings_slot = st.container()
replay_picker_slot = st.empty()
with controls_slot:
    c1, c2, c3, c4, c5 = st.columns([2.4, 1, 1, 1.4, 1.4])
if replay_mode:
    saved = list_saved_races()
    if not saved:
        st.info(f'\u672a\u6709 Recorder \u6b77\u53f2\u8a18\u9304\uff1a{DATA_DIR}')
        st.stop()
    with replay_picker_slot.container():
        race_key = st.selectbox('\u63c0\u5834\u6b21', saved, index=len(saved)-1, key='selected_replay_race_v19')
    d, course, race_no = race_key.split('|')
    race_no = int(race_no)
    race_date = date.fromisoformat(d)
    with c1:
        st.caption('REPLAY \u8cfd\u4e8b')
        st.write(f'{race_date} \u00b7 {venue_label(course)} ({course})')
    with c2:
        st.write(venue_label(course))
    with c3:
        st.write(f'\u7b2c {race_no} \u5834')
else:
    try:
        meetings = current_meetings()
    except Exception as exc:
        meetings = []
        st.warning(f'\u672a\u53d6\u5f97\u8cfd\u671f\uff1a{exc}')
    with c1:
        if meetings:
            today = datetime.now(HKT).date().isoformat()
            default = next((i for i, m in enumerate(meetings) if m['date'] >= today), 0)
            mi = st.selectbox('\u8cfd\u4e8b\uff08\u81ea\u52d5\u540c\u6b65\u99ac\u6703\uff09', range(len(meetings)), index=default,
                format_func=lambda i: f"{meetings[i]['date']} \u00b7 {venue_label(meetings[i]['venue'])} ({meetings[i]['venue']}) \u00b7 {meetings[i]['n_races']}\u5834")
            meeting = meetings[mi]
            race_date, course = date.fromisoformat(meeting['date']), meeting['venue']
            max_race = int(meeting['n_races'] or 14)
        else:
            race_date = st.date_input('\u65e5\u671f', datetime.now(HKT).date())
            course, max_race = 'ST', 14
    with c2:
        if not meetings:
            course = st.selectbox('\u5834\u5730', ['ST', 'HV'])
        else:
            st.write(venue_label(course))
    with c3:
        race_no = st.number_input('\u5834\u6b21', 1, max_race, 1)
    race_key = f'{race_date}|{course}|{int(race_no)}'
with c4:
    post_input = st.text_input('\u958b\u8dd1\u6642\u9593 (\u53ef\u9078)', '', placeholder='HH:MM', key=f'post::{race_key}::{replay_mode}')
with c5:
    reset_clicked = st.button('\U0001f504 \u91cd\u8a2d\u6b64\u5834\u8d70\u52e2', use_container_width=True)
if reset_clicked:
    st.session_state.pop('_live_buffer::' + race_key, None)
    st.session_state.pop('replay_time::' + race_key, None)
    st.caption('\u5df2\u91cd\u8a2d\u6b64\u5834\u756b\u9762\uff1bRecorder \u6b77\u53f2\u4fdd\u7559\u3002')

with settings_slot:
    with st.expander("\u2699\ufe0f \u6025\u5347\u5075\u6e2c\u654f\u611f\u5ea6\uff08\u5206\u5c64 \u00b7 \u62c9\u687f\u5fae\u8abf\uff09"):
        def _clampf(v, lo, hi, fallback):
            """\u540c _clamp \u4e00\u6a23\uff0c\u4f46\u8655\u7406\u6d6e\u9ede\u6578\uff08\u56db\u6c60\u71b1\u5ea6\u7528%\uff09\u3002"""
            try:
                v = float(v)
            except Exception:
                return fallback
            return max(lo, min(hi, v))

        st.markdown('<div style="font-size:11px;color:var(--subtext);margin-bottom:2px">\u56db\u6c60\u71b1\u5ea6\uff08\u4f54\u6bd4 %\uff09</div>', unsafe_allow_html=True)
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            st.session_state["rise_t1"] = st.slider(
                "\u26a1 \u7559\u610f\uff08%\uff09", 0.2, 2.0,
                _clampf(st.session_state.get("rise_t1", RISE_TIER1), 0.2, 2.0, RISE_TIER1), 0.1)
        with sc2:
            st.session_state["rise_t2"] = st.slider(
                "\U0001f525 \u660e\u986f\uff08%\uff09", 0.3, 2.5,
                _clampf(st.session_state.get("rise_t2", RISE_TIER2), 0.3, 2.5, RISE_TIER2), 0.1)
        with sc3:
            st.session_state["rise_t3"] = st.slider(
                "\U0001f4a5 \u5f37\u70c8\uff08%\uff09", 0.5, 3.0,
                _clampf(st.session_state.get("rise_t3", RISE_TIER3), 0.5, 3.0, RISE_TIER3), 0.1)

        st.markdown('<div style="font-size:11px;color:var(--subtext);margin:8px 0 2px">\u91d1\u984d\u8a0a\u865f\uff08\u68d2\u578b\u5716 + \u6bcf\u5206\u9418\u91d1\u984d\u8868\uff09\u00b7 \u5343\u5143\uff08\u8f38\u5165\u6578\u503c\uff0c\u64b3\u300c\u5132\u5b58\u8a2d\u5b9a\u300d\u5148\u6703\u8de8session\u8a18\u4f4f\uff09</div>', unsafe_allow_html=True)

        def _clamp(v, lo, hi, fallback):
            """\u820asession_state / \u820asettings\u6a94\u53ef\u80fd\u5b58\u4f4f\u8d85\u51fa\u65b0\u7bc4\u570d\u5605\u503c\uff08\u4f8b\u5982\u820a\u7248\u62c9\u687f
            set\u904e100K\uff0c\u4f46\u65b0\u26a1\u4e0a\u9650\u5f9750K\uff09\uff0c\u76f4\u63a5\u50b3\u843d number_input \u6703\u4ee4 Streamlit \u62cb
            StreamlitValueAboveMaxError\u3002\u5462\u5ea6\u7d71\u4e00\u593e\u8fd4\u5165\u5408\u6cd5\u7bc4\u570d\u5148\u7528\u3002"""
            try:
                v = int(v)
            except Exception:
                return fallback
            return max(lo, min(hi, v))

        mc1, mc2, mc3, mc4 = st.columns([1, 1, 1, 0.9])
        with mc1:
            _mk1 = st.number_input("\u26a1 \u7559\u610f\uff081-50 K\uff09", min_value=1, max_value=50, step=1,
                                   value=_clamp(st.session_state.get("money_t1_k", 20), 1, 50, 20))
        with mc2:
            _mk2 = st.number_input("\U0001f525 \u660e\u986f\uff0851-100 K\uff09", min_value=51, max_value=100, step=1,
                                   value=_clamp(st.session_state.get("money_t2_k", 70), 51, 100, 70))
        with mc3:
            _mk3 = st.number_input("\U0001f4a5 \u5f37\u70c8\uff08101-400 K\uff09", min_value=101, max_value=400, step=1,
                                   value=_clamp(st.session_state.get("money_t3_k", 150), 101, 400, 150))
        with mc4:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("\U0001f4be \u5132\u5b58\u8a2d\u5b9a", use_container_width=True):
                _ok = save_settings({
                    "money_t1_k": _mk1, "money_t2_k": _mk2, "money_t3_k": _mk3,
                    "rise_t1": st.session_state.get("rise_t1", RISE_TIER1),
                    "rise_t2": st.session_state.get("rise_t2", RISE_TIER2),
                    "rise_t3": st.session_state.get("rise_t3", RISE_TIER3),
                })
                st.session_state["_settings_saved_at"] = datetime.now(HKT).strftime("%H:%M:%S")
                st.session_state["_settings_save_ok"] = _ok

        # \u54623\u500b\u6578\u503c\u5373\u523b\u751f\u6548\uff08\u5514\u4f7f\u64b3\u5132\u5b58\u90fd\u6703\u5373\u5834\u7528\u5230\uff09\uff0c\u5132\u5b58\u6de8\u4fc2\u5f71\u97ff\u300c\u4e0b\u6b21\u958bapp\u300d\u5605\u9810\u8a2d\u503c
        st.session_state["money_t1_k"] = _mk1
        st.session_state["money_t2_k"] = _mk2
        st.session_state["money_t3_k"] = _mk3
        st.session_state["money_t1"] = _mk1 * 1000
        st.session_state["money_t2"] = _mk2 * 1000
        st.session_state["money_t3"] = _mk3 * 1000

        if st.session_state.get("_settings_saved_at"):
            _status = "\u5df2\u5132\u5b58" if st.session_state.get("_settings_save_ok") else "\u26a0\ufe0f \u5132\u5b58\u5931\u6557\uff08check\u786c\u789f\u6b0a\u9650\uff09"
            st.caption(f"{_status}\uff1a{st.session_state['_settings_saved_at']}\u3000\u00b7 \u4e0b\u6b21\u958bapp\u6703\u81ea\u52d5\u8b80\u8fd4\u5462\u5572\u6578\u503c")

        st.caption("\u56db\u6c60\u71b1\u5ea6\u7528\u4f54\u6bd4%\uff1b\u68d2\u578b\u5716+\u91d1\u984d\u8868\u7528\u5be6\u8cea\u91d1\u984d\uff08\u8fd11\u5206\u9418\u4f30\u7b97\u843d\u6ce8\uff09\uff0c\u5169\u500b\u91d1\u984d\u8868\u5b8c\u7f8e\u540c\u6b65\u3002\u4f4e\uff1d\u591a\u63d0\u793a\u3001\u9ad8\uff1d\u5c11\u4f46\u7cbe\u3002")


thresholds = tuple(st.session_state.get(f'rise_t{i}', default) for i, default in enumerate((RISE_TIER1, RISE_TIER2, RISE_TIER3), 1))
if not thresholds[0] < thresholds[1] < thresholds[2]:
    st.error('\u654f\u611f\u5ea6\u9808\u4f9d\u6b21\u905e\u589e\uff1a\u26a1 < \U0001f525 < \U0001f4a5')
    st.stop()
archive = load_snapshots(race_key)
if st.session_state.get('_archive_error'):
    st.warning('\u5df2\u7565\u904e\u7121\u6cd5\u8b80\u53d6\u6216\u5834\u6b21\u4e0d\u7b26\u5605\u8a18\u9304\uff1a' + st.session_state['_archive_error'])
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
        st.error('\u958b\u8dd1\u6642\u9593\u8acb\u7528 HH:MM')
        st.stop()
if replay_mode:
    if not archive:
        st.info('\u6240\u9078\u5834\u6b21\u5187\u6709\u6548 Recorder \u8a18\u9304\u3002')
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
        st.error(f'\u5373\u6642\u8cc7\u6599\u66f4\u65b0\u5931\u6557\uff1a{exc}\u3002\u4eca\u6b21\u4e0d\u986f\u793a\u820a\u8cc7\u6599\u4f5c\u70ba\u6700\u65b0\u5831\u50f9\u3002')
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
rows = [{'\u6c60': code, '\u99ac\u865f': str(h), '\u8ce0\u7387': _to_float(o), '\u5927\u71b1': False}
        for code in ('WIN', 'PLA') for h, o in (snap.get(code.lower()) or {}).items() if _to_float(o) > 0]
if not rows:
    st.info('\u5462\u500b\u8a18\u9304\u9ede\u5187\u6709\u6548\u8ce0\u7387\u3002')
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
            countdown = f"\u8ddd\u96e2\u958b\u8dd1 {abs(mtp)*60:.0f} \u79d2" if mtp >= -1 else f"\u8ddd\u96e2\u958b\u8dd1 {abs(mtp):.0f} \u5206\u9418"
        else:
            countdown = '\u5df2\u5230\uff0f\u8d85\u904e\u9810\u5b9a\u958b\u8dd1\u6642\u9593'
        st.caption(countdown)
    alerts = []
    for _, row in df[df['\u6c60'] == 'WIN'].iterrows():
        pct, _ = recent_speed(S, ('WIN', row['\u99ac\u865f']), PLUNGE_WINDOW)
        if pct >= PLUNGE_PCT:
            alerts.append((row['\u99ac\u865f'], pct, row['\u5373\u5834']))
    for no, pct, odds in sorted(alerts, key=lambda a: a[1], reverse=True)[:3]:
        st.markdown(f'<div class="alert-bar">\u26a0\ufe0f \u63d2\u6c34\u8b66\u793a\u3000{no} \u865f\u65bc {PLUNGE_WINDOW} \u79d2\u5167\u6025\u8dcc {pct:.0f}%\uff0c\u5373\u5834 {odds:.1f}</div>', unsafe_allow_html=True)
    st.markdown(f'**{race_date} {venue_label(course)} \u00b7 \u7b2c {int(race_no)} \u5834**')
    st.caption(f"{'REPLAY' if replay_mode else 'LIVE'} \u8cc7\u6599\u6642\u9593\uff1a{datetime.fromtimestamp(ACTIVE_NOW_TS, HKT):%Y-%m-%d %H:%M:%S}"
               + (f"\u3000\u958b\u8dd1\u6642\u9593\uff1a{ACTIVE_POST_TIME:%H:%M}" if ACTIVE_POST_TIME else ''))
    if _ri:
        st.caption(' \u00b7 '.join(str(_ri.get(k)) for k in ('name', 'cls', 'dist', 'track', 'going') if _ri.get(k)))
    else:
        st.info('\u5462\u7b46\u820a\u8a18\u9304\u5187\u4fdd\u5b58\u76f8\u7b26\u6b77\u53f2\u99ac\u540d\uff0f\u6392\u4f4d\uff1b\u4fdd\u7559\u99ac\u865f\u53ca\u6295\u6ce8\u6b77\u53f2\uff0c\u6a21\u578b\u66ab\u4e0d\u8a08\u7b97\u3002\u65b0 Recorder \u6703\u4fdd\u5b58\u6392\u4f4d\u3002')

    with st.expander('\U0001f3c7 \u8dd1\u6cd5\uff0f\u504f\u5dee\uff08\u5df2\u5b8c\u6210\u5834\u6b21\uff09', expanded=False):
        _display_analysis = _current_postrace or _day_analysis
        if _display_analysis.get('completed') or _day_analysis.get('completed_races'):
            if _current_postrace:
                st.caption(f"\u672c\u5834\u8cfd\u5f8c\u7d50\u679c\uff1a\u5df2\u8a18\u9304\u5168\u90e8 {len(_current_postrace.get('runs') or [])} \u5339\u99ac\uff1b\u4e0b\u4e00\u5834\u6a21\u578b\u6703\u8b80\u53d6\u6b64\u7d50\u679c\u3002")
            else:
                st.caption('\u5df2\u5b8c\u6210\u5834\u6b21\uff1a' + ', '.join('\u7b2c%d\u5834' % n for n in sorted(_day_analysis['completed_races']))
                           + '\uff1b\u4eca\u5834\u6a21\u578b\u53ea\u8b80\u53d6\u4e4b\u524d\u5df2\u5b8c\u6210\u7684\u5834\u6b21\u3002')
            st.markdown('**\u7576\u65e5\u5834\u5730\u504f\u5dee\uff1a** ' + str(_display_analysis.get('bias_label') or '\u6a23\u672c\u4e0d\u8db3'))
            _style_rows = []
            _card_for_style = ((_ri or {}).get('card_rows') or [])
            if _current_postrace and _current_postrace.get('runs'):
                _card_for_style = _current_postrace.get('runs')
            for _row in _card_for_style:
                _h = _row.get('horse_no')
                _style_rows.append({'\u99ac\u865f': str(int(float(_h))), '\u99ac\u540d': _row.get('horse_name') or str(_h),
                                    '\u6b77\u53f2\u8dd1\u6cd5': style_from_history(_history_styles, _row.get('horse_id') or _h),
                                    '\u4eca\u5834\u9810\u6e2c\u8dd1\u6cd5\uff0f\u5df2\u8cfd\u5be6\u969b\u8dd1\u6cd5': _row.get('running_style') or style_from_history(_history_styles, _row.get('horse_id') or _h),
                                    '\u6a94\u4f4d': _row.get('draw') or '\u2014'})
            if _style_rows:
                st.dataframe(pd.DataFrame(_style_rows), hide_index=True, use_container_width=True)
            _bias_rows = []
            for _style, _stat in (_display_analysis.get('style_stats') or {}).items():
                _bias_rows.append({'\u8dd1\u6cd5': _style, '\u6a23\u672c': _stat.get('n', 0),
                                   '\u5165\u4e09\u7532\u7387': f"{float(_stat.get('top3_rate', 0)):.1%}",
                                   'Lift': f"{float(_stat.get('lift', 1)):.2f}",
                                   '\u72c0\u614b': '\u53ef\u53c3\u8003' if _stat.get('reliable') else '\u6a23\u672c\u4e0d\u8db3'})
            if _bias_rows:
                st.dataframe(pd.DataFrame(_bias_rows), hide_index=True, use_container_width=True)
        else:
            st.info('\u7576\u65e5\u5c1a\u672a\u6709\u5df2\u5b8c\u6210\u5834\u6b21\u5206\u6790\uff1b\u7b2c1\u5834\u6703\u5148\u986f\u793a\u6b77\u53f2\u8cc7\u6599\u53ef\u7528\u7a0b\u5ea6\uff0c\u5b8c\u6210\u5f8c\u7531Recorder\u66f4\u65b0\u3002')

    # \u2550\u2550\u2550 MODEL \u5373\u6642\u8a08\u7b97\uff1a\u57fa\u672c\u9762 + HKJC \u5373\u6642\u7368\u8d0f\u8ce0\u7387 \u2550\u2550\u2550
    st.markdown(
        '<div class="panel-title" style="margin-top:4px">\U0001f9e0 V19 \u5373\u6642\u91cf\u5316\u6a21\u578b</div>'
        '<div class="panel-sub">Benter \u7b2c\u4e8c\u968e\uff1a\u6b77\u53f2\u57fa\u672c\u9762\u52dd\u7387 \u00d7 \u99ac\u6703\u5373\u6642\u7368\u8d0f\u5e02\u5834\u6982\u7387\uff1b\u6bcf 5 \u79d2\u96a8\u8ce0\u7387\u66f4\u65b0</div>',
        unsafe_allow_html=True)
    _rebate_label = st.radio(
        "EV \u56de\u6263\u8a2d\u5b9a", ["\u6563\u6236\uff0f\u7121\u56de\u6263", "\u7368\u8d0f\u5408\u8cc7\u683c\u56de\u6263 10%"],
        horizontal=True, key="model_rebate_mode",
        help="10% \u56de\u6263\u53ea\u9069\u7528\u65bc\u7b26\u5408\u99ac\u6703\u9580\u6abb\u7684\u8f38\u6ce8\uff1b\u4e00\u822c\u5c0f\u984d\u6295\u6ce8\u8acb\u9078\u7121\u56de\u6263\u3002")
    _rebate_rate = 0.10 if "10%" in _rebate_label else 0.0
    _live_win_odds = {
        str(r["\u99ac\u865f"]): float(r["\u5373\u5834"])
        for _, r in df[df["\u6c60"] == "WIN"].iterrows()
        if _to_float(r.get("\u5373\u5834")) > 0
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
        _shown.columns = ["\u99ac\u865f", "\u99ac\u540d", "\u6b77\u53f2\u8dd1\u6cd5", "\u5373\u6642\u8ce0\u7387", "\u57fa\u672c\u9762\u52dd\u7387", "\u5e02\u5834\u52dd\u7387",
                          "\u7d9c\u5408\u52dd\u7387", "\u76f4\u535a\u7387", "Fair Odds", "EV", "\u9810\u671f\u56de\u5831", "\u4f4d\u7f6e\u6982\u7387",
                          "\u8dd1\u6cd5\u8abf\u6574", "\u6a94\u4f4d\u8abf\u6574", "\u504f\u5dee\u5408\u8a08"]
        _shown["\u99ac\u865f"] = _shown["\u99ac\u865f"].astype(str)
        for _c in ("\u57fa\u672c\u9762\u52dd\u7387", "\u5e02\u5834\u52dd\u7387", "\u7d9c\u5408\u52dd\u7387", "\u4f4d\u7f6e\u6982\u7387"):
            _shown[_c] = _shown[_c].map(lambda x: f"{x:.2%}")
        _shown["\u76f4\u535a\u7387"] = _shown["\u76f4\u535a\u7387"].map(lambda x: f"{x:+.1f}%")
        _shown["Fair Odds"] = _shown["Fair Odds"].map(lambda x: f"{x:.2f}")
        _shown["\u5373\u6642\u8ce0\u7387"] = _shown["\u5373\u6642\u8ce0\u7387"].map(lambda x: f"{x:.1f}")
        _shown["EV"] = _shown["EV"].map(lambda x: f"{x:.3f}")
        _shown["\u9810\u671f\u56de\u5831"] = _shown["\u9810\u671f\u56de\u5831"].map(lambda x: f"{x:+.1%}")
        for _c in ("\u8dd1\u6cd5\u8abf\u6574", "\u6a94\u4f4d\u8abf\u6574", "\u504f\u5dee\u5408\u8a08"):
            _shown[_c] = _shown[_c].map(lambda x: f"{x:+.1f}%")
        st.dataframe(_shown, hide_index=True, use_container_width=True)
        _value_count = int((_model_df["ev"] > 1.0).sum())
        st.caption(
            f"\u5373\u6642\u8fa8\u8b58 {_value_count} \u5339 EV > 1\uff1b\u76f4\u535a\u7387 = \u7d9c\u5408\u52dd\u7387 \u00f7 \u5e02\u5834\u52dd\u7387 \u2212 1\u3002"
            "\u7d9c\u5408\u52dd\u7387\u53ca\u4f4d\u7f6e\u6982\u7387\u70ba\u6a21\u578b\u4f30\u7b97\uff0c\u4e26\u975e\u4fdd\u8b49\u7d50\u679c\u3002")
    else:
        st.info(f"\u6a21\u578b\u7b49\u5f85\u8cc7\u6599\uff1a{_model_err or '\u6392\u4f4d\u6216\u7368\u8d0f\u8ce0\u7387\u5c1a\u672a\u9f4a\u5168'}")



    # Four pool totals and original pair matrices.
    def total_card(label, value):
        return f'<div class="panel" style="flex:1"><div class="panel-sub">{label}</div><b>{_fmt_money(value)}</b></div>'
    st.markdown('<div style="display:flex;gap:8px">' + ''.join(total_card(c, pools.get(c)) for c in ('WIN', 'PLA', 'QIN', 'QPL')) + '</div>', unsafe_allow_html=True)
    race_horses = sorted(int(h) for h in snap.get('win', {}))
    qcol1, qcol2 = st.columns(2)
    with qcol1:
        combo_matrix_panel(qin_matrix, '\u9023\u8d0f QIN', race_horses)
    with qcol2:
        combo_matrix_panel(qpl_matrix, '\u4f4d\u7f6eQ QPL', race_horses)
    hcol1, hcol2 = st.columns([1.3, 1])
    with hcol1:
        four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S, pools, cold_odds=10.0)
    with hcol2:
        signal_summary_panel(S['signal_log'], minutes=30, as_of_ts=ACTIVE_NOW_TS)
    if replay_mode:
        st.caption('\u6b77\u53f2\u71b1\u5ea6\u8207\u8a0a\u865f\u6309\u76ee\u524d\u654f\u611f\u5ea6\u91cd\u7b97\uff1b\u6a21\u578b\u4f7f\u7528\u73fe\u6709\u6a21\u578b\u53ca\u8a72\u7b46\u6b77\u53f2\u6392\u4f4d\u91cd\u7b97\u3002')
    bar_sort = st.radio('\u68d2\u578b\u5716\u6392\u5e8f', ['\u9806\u99ac\u865f', '\u9806\u8ce0\u7387\uff08\u71b1\u2192\u51b7\uff09'], horizontal=True, key='bar_sort')
    sort_key = '\u8ce0\u7387' if '\u8ce0\u7387' in bar_sort else '\u99ac\u865f'
# The placeholder is visually placed here; its controls were evaluated earlier.
# Upper panels render in the container reserved before this timeline.
_m1, _m2, _m3 = (st.session_state[f'money_t{i}'] for i in (1, 2, 3))
start_clock = datetime.fromtimestamp(ACTIVE_NOW_TS - 60, HKT).strftime('%H:%M:%S')
end_clock = datetime.fromtimestamp(ACTIVE_NOW_TS, HKT).strftime('%H:%M:%S')
st.caption(f'\u6700\u65b01\u5206\u9418\u76ee\u6a19\u5340\u9593\uff1a{start_clock} \u2192 {end_clock}\uff1b\u9010\u99ac\u91d1\u984d\u70ba\u8ce0\u7387\u4f54\u6bd4\u4f30\u7b97\uff0c\u7f3a\u5c11\u57fa\u6e96\u6642\u986f\u793a\u7a7a\u767d\u3002')
baselines = [sample_at(points, ACTIVE_NOW_TS - 60) for points in S['stake_hist'].values()]
baselines = [p[0] for p in baselines if p and ACTIVE_NOW_TS - 60 - p[0] <= 45]
if baselines:
    lo, hi = min(baselines), max(baselines)
    label = datetime.fromtimestamp(lo, HKT).strftime('%H:%M:%S')
    if hi != lo:
        label += ' \u81f3 ' + datetime.fromtimestamp(hi, HKT).strftime('%H:%M:%S')
    st.caption(f'\u5be6\u969b\u53ef\u7528\u57fa\u6e96\u8a18\u9304\uff1a{label}\uff1b\u7d42\u9ede\uff1a{end_clock}\u3002\u820a30\u79d2\u8a18\u9304\u53ef\u80fd\u8f03\u76ee\u6a19\u5340\u9593\u9577\u3002')
bcol1, bcol2 = st.columns(2)
with bcol1:
    stake_bar_chart_v(df[df['\u6c60'] == 'WIN'], '\u7368\u8d0f', win_inv, S, sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
with bcol2:
    stake_bar_chart_v(df[df['\u6c60'] == 'PLA'], '\u4f4d\u7f6e', pla_inv, S, sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp, as_of_ts=ACTIVE_NOW_TS)
for code in ('WIN', 'PLA'):
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool=code, m1=_m1, m2=_m2, m3=_m3, as_of_ts=ACTIVE_NOW_TS)
st.caption(f'{APP_NAME} {APP_VERSION} \u00b7 \u6b77\u53f2\u8cc7\u6599\u7531\u7368\u7acb Recorder \u8a18\u9304')

if not replay_mode:
    st_autorefresh(interval=5000, key="live_refresh_v19_rebuilt")
