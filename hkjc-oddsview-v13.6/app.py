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

APP_VERSION = "v18.2 STHV"
APP_NAME = "HKJC å³æ™‚è³ çŽ‡ç›£å¯Ÿ"

st.set_page_config(page_title=f"{APP_NAME} {APP_VERSION}", layout="wide",
                   initial_sidebar_state="collapsed")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  DISK STORAGE (æ°¸ä¹…å„²å­˜ â€” å¯«è½ç¡¬ç¢Ÿï¼Œé‡å•Ÿå””å¤±)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# æ¯å ´ä¸€å€‹è³‡æ–™å¤¾ï¼Œæ¯å€‹æ™‚é–“é»žä¸€å€‹ JSON snapshotï¼ˆæ¯ 30 ç§’ä¸€æ¬¡ï¼‰ã€‚
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
SNAPSHOT_INTERVAL = 30  # ç§’ï¼Œæ¯éš”å¹¾è€å­˜ä¸€å€‹ snapshot

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

def _nearest_snap_idx(snaps, target_ts):
    """å–ºå·²æŽ’åºå˜…snapshot listå…¥é¢ï¼Œæµæ™‚é–“æˆ³æœ€æŽ¥è¿‘target_tså—°å€‹indexã€‚
    ä¿¾REPLAYå¿«æ·è·³é»žchipç”¨ï¼ˆä¾‹å¦‚æ’³ã€Œ-30åˆ†ã€å°±è·³åŽ»æœ€æŽ¥è¿‘å—°å€‹è¨˜éŒ„é»žï¼‰ã€‚"""
    if not snaps or target_ts is None:
        return None
    best_i, best_diff = 0, None
    for i, sp in enumerate(snaps):
        ts = sp.get("ts")
        if ts is None:
            continue
        d = abs(ts - target_ts)
        if best_diff is None or d < best_diff:
            best_diff = d
            best_i = i
    return best_i

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

def post_time_from_snaps(snaps):
    """ç”±ä¸€å † snapshot å–ã€Œå‡ºç¾æ¬¡æ•¸æœ€å¤šã€å—°å€‹ post_timeï¼ˆçœ¾æ•¸ï¼‰ã€‚

    é»žè§£å””æ”žæœ€æ–°ä¸€å€‹ï¼šrecorder æ¯æ¬¡æ‹‰ HKJC æ”žåˆ°å’©å°±å¯«å’©ï¼Œå¶ç„¶æœƒå¯«å…¥ç•°å¸¸å€¼
    ï¼ˆå¯¦æ¸¬ç¬¬1å ´ï¼š19:10 å‡ºç¾ 4171 æ¬¡å…ˆä¿‚æ­£ç¢ºï¼Œä½†æœ€å¾Œä¸€å€‹ snapshot å¯«ä½ 19:00
    åªå¾— 15 æ¬¡ï¼Œä»²æœ‰ 2 æ¬¡ 19:40ï¼‰ã€‚å–çœ¾æ•¸å°±è‡ªå‹•è“‹éŽå‘¢å•²å°‘æ•¸é›œå€¼ã€‚
    LIVEï¼ˆè®€ç¡¬ç¢Ÿï¼‰åŒ REPLAY éƒ½ç”¨å‘¢ä¸€å€‹ functionï¼Œä¿è­‰å…©é‚ŠåŸºæº–ä¸€è‡´ã€‚"""
    counts = {}
    for sp in snaps or []:
        p = sp.get("post_time")
        if p:
            counts[p] = counts.get(p, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)

def post_time_for_race(race_key):
    """æ”žæŸå ´å˜…é–‹è·‘æ™‚é–“ï¼ˆå–çœ¾æ•¸ï¼‰ã€‚å›žå‚³ datetime æˆ– Noneã€‚
    æœ‰ cacheï¼ˆæ¯ 5 åˆ†é˜å…ˆçœŸæ­£æŽƒä¸€æ¬¡ç¡¬ç¢Ÿï¼‰ï¼Œå””æœƒæ¯æ¬¡ refresh éƒ½è®€æˆå ´ã€‚"""
    ck = f"_posttime_{race_key}"
    tk = ck + "_ts"
    now = datetime.now(HKT).timestamp()
    if ck in st.session_state and (now - st.session_state.get(tk, 0)) < 300:
        return st.session_state[ck]
    try:
        snaps = load_snapshots(race_key)
    except Exception:
        snaps = []
    p = post_time_from_snaps(snaps)
    val = datetime.fromtimestamp(p, HKT) if p else None
    st.session_state[ck] = val
    st.session_state[tk] = now
    return val

def _settings_path():
    return os.path.join(DATA_DIR, "_settings.json")

def load_settings():
    """è®€è¿”ä¸Šæ¬¡ã€Œå„²å­˜è¨­å®šã€å¯«ä½Žå˜…æ•æ„Ÿåº¦æ•¸å€¼ã€‚æœ‰å•é¡Œå°±è¿”å›žç©ºdictï¼ˆç”¨è¿”ç¨‹å¼é è¨­å€¼ï¼‰ã€‚"""
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(settings):
    """å°‡æ•æ„Ÿåº¦è¨­å®šå¯«è½ç¡¬ç¢Ÿï¼Œä¸‹æ¬¡é–‹app/restartéƒ½æœƒè‡ªå‹•è®€è¿”ï¼Œå””ä½¿æˆæ—¥èª¿ã€‚"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(_settings_path(), "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
        return True
    except Exception:
        return False

def load_latest_snapshot(race_key):
    """è®€è©²å ´æœ€æ–°ä¸€å€‹ snapshotï¼ˆLIVE è®€ç¡¬ç¢Ÿç”¨ï¼Œå¿«ã€å””ä½¿æ‹‰ HKJCï¼‰ã€‚"""
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

def sync_state_from_disk(race_key, S):
    """è®€ç¡¬ç¢Ÿå·²ç¶“è¨˜éŒ„å’—å˜… snapshotï¼Œè£œè¿”è½ S å˜… series/stake_hist/share_histã€‚

    è§£æ±ºå•é¡Œï¼šLIVE ç•«é¢è½‰åŽ»ç¬¬2å ´ã€å†è½‰è¿”ç¬¬1å ´ï¼Œè¨˜æ†¶é«”å…¥é¢ S è‹¥æžœå› ç‚º
    session ä¸­æ–· / ä¸€æ®µæ™‚é–“å†‡é¡¯ç¤ºè€Œè·Ÿå””åˆ° recorder å˜…è¨˜éŒ„ï¼Œè½æ³¨é‡‘é¡è¡¨
    å°±æœƒé¡¯ç¤ºå””åˆ°ä¹‹å‰å˜…æ•¸ã€‚Recorder ä¸€ç›´èƒŒæ™¯å¯«ç·Šç¡¬ç¢Ÿï¼ˆå””ç†ä½ è€Œå®¶ç‡ç·Šé‚Š
    å ´ï¼‰ï¼Œæ‰€ä»¥å‘¢å€‹ function ä»¤ LIVE åŒ REPLAY ç”¨è¿”åŒä¸€å€‹ã€Œç¡¬ç¢Ÿç‚ºæº–ã€å˜…
    æ•¸æ“šæºï¼šæ¯æ¬¡é¡¯ç¤ºå‘¢å ´ï¼Œéƒ½ç”±ç¡¬ç¢Ÿè£œé½Šè¨˜æ†¶é«”æ¼å’—å˜…æ™‚é–“é»žï¼ˆåªè£œæ–°å˜…ï¼Œ
    å·²ç¶“æœ‰å˜…æ™‚é–“é»žå””æœƒé‡è¦†åŠ ï¼Œæ‰€ä»¥æˆæœ¬å¥½ç´°ï¼‰ã€‚"""
    try:
        snaps = load_snapshots(race_key)
    except Exception:
        snaps = []
    if not snaps:
        return
    last_synced = S.get("_disk_synced_ts", 0.0)
    new_snaps = [sp for sp in snaps if sp.get("ts", 0) > last_synced]
    if not new_snaps:
        return
    max_ts = last_synced
    for sp in new_snaps:
        ts = sp.get("ts")
        if ts is None:
            continue
        max_ts = max(max_ts, ts)
        pinv = sp.get("pool") or {}
        for pool_code, odds_map in (("WIN", sp.get("win")), ("PLA", sp.get("pla"))):
            if not odds_map:
                continue
            try:
                inv_sum = sum(1.0 / float(o) for o in odds_map.values() if float(o) > 0)
            except Exception:
                inv_sum = 0.0
            ptot = pinv.get(pool_code)
            for h, o in odds_map.items():
                try:
                    o = float(o)
                except Exception:
                    continue
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
    S["_disk_synced_ts"] = max_ts

    # â”€â”€ ä¿®æ­£æ™‚é–“é †åº â”€â”€
    # syncè£œæ­·å²å—°é™£ï¼Œç¡¬ç¢ŸèˆŠè¨˜éŒ„å¯èƒ½åŠ å–ºè¨˜æ†¶é«”æ–°è¨˜éŒ„ä¹‹å¾Œï¼Œä»¤æ¬¡åºå””å†
    # éžå¢žã€‚è½æ³¨é‡‘é¡è¡¨é€æ ¼è¨ˆç®—é æ™‚é–“é †åºæµé‚Šç•Œï¼Œæ¬¡åºäº‚å’—å°±æœƒè¨ˆéŒ¯ã€‚
    # å‘¢åº¦çµ±ä¸€æŒ‰æ™‚é–“æˆ³(ts)é‡æ–°æŽ’è¿”ï¼šå””åˆªã€å””åŠ ä»»ä½•ä¸€å€‹è¨˜éŒ„é»žï¼Œæ·¨ä¿‚sortã€‚
    for store in (S["series"], S["stake_hist"]):
        for key in list(store.keys()):
            dq = store[key]
            if len(dq) < 2:
                continue
            sorted_pts = sorted(dq, key=lambda p: p[0])
            dq.clear()
            dq.extend(sorted_pts)
    for key in list(S["share_hist"].keys()):
        dq = S["share_hist"][key]
        if len(dq) < 2:
            continue
        sorted_pts = sorted(dq, key=lambda p: p[0])
        dq.clear()
        dq.extend(sorted_pts)

def _signal_log_path(race_key):
    return os.path.join(_race_dir(race_key), "signals.jsonl")

def append_signal_log(race_key, event):
    """å³åˆ»å¯«ä¸€è¡Œè½ç¡¬ç¢Ÿï¼ˆæ¯æ¬¡è¨Šè™Ÿä¸€è§¸ç™¼å°±å¯«ï¼Œå””ä½¿ç­‰30ç§’snapshotï¼‰ã€‚"""
    try:
        rd = _race_dir(race_key)
        os.makedirs(rd, exist_ok=True)
        with open(_signal_log_path(race_key), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass

def load_signal_log(race_key, since_ts=0.0):
    """è®€è¿”å‘¢å ´æ‰€æœ‰ï¼ˆæˆ–æŒ‡å®šæ™‚é–“ä¹‹å¾Œï¼‰å˜…è¨Šè™Ÿè¨˜éŒ„ã€‚"""
    events = []
    try:
        path = _signal_log_path(race_key)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if ev.get("ts", 0) > since_ts:
                        events.append(ev)
    except Exception:
        pass
    return events

def sync_signal_log_from_disk(race_key, S):
    """è½‰å ´æ¬¡/é‡é–‹ä¹‹å¾Œï¼Œç”±ç¡¬ç¢Ÿè£œè¿”è¨˜æ†¶é«”æ¼å’—å˜…è¨Šè™Ÿè¨˜éŒ„ã€‚"""
    last_ts = S.get("_signal_log_synced_ts", 0.0)
    new_events = load_signal_log(race_key, last_ts)
    if not new_events:
        return
    for ev in new_events:
        S["signal_log"].append(ev)
        S["_signal_log_synced_ts"] = max(S.get("_signal_log_synced_ts", 0.0), ev.get("ts", 0.0))

def backfill_signals_from_disk(race_key):
    """REPLAYæ€åˆ°ä¸€å ´ä¹‹å‰å®Œå…¨æœª live ç‡éŽï¼ˆå³ä¿‚ signals.jsonl ä»²æœªå­˜åœ¨ï¼‰å˜…å ´ï¼Œ
    å°±ç”¨ç¡¬ç¢Ÿå®Œæ•´ snapshot æ­·å²ï¼Œäº‹å¾Œè£œè·‘ä¸€æ¬¡âš¡ðŸ”¥ðŸ’¥åµæ¸¬é‚è¼¯ï¼ˆWIN/PLA/QIN/QPL
    å››å€‹æ± ï¼‰ï¼Œå¯«è¿”è½ signals.jsonlã€‚å‘¢å€‹ä¿‚ä¸€æ¬¡æ€§é‹ç®—ï¼Œè¨ˆå®Œå°±cacheå–ºç¡¬ç¢Ÿï¼Œ
    ä¸‹æ¬¡å†æ€è¿”å‘¢å ´ï¼ˆLIVEæˆ–REPLAYï¼‰éƒ½å””ä½¿é‡è¨ˆã€‚"""
    try:
        snaps = load_snapshots(race_key)
    except Exception:
        snaps = []
    # å°±ç®—å†‡snapshotéƒ½è¦å»ºç«‹è¿”å€‹ï¼ˆç©ºï¼‰fileï¼Œç­‰ä½¢åšã€Œå·²ç¶“è™•ç†éŽã€å˜…æ¨™è¨˜ï¼Œ
    # å””æœƒä¸‹æ¬¡åˆå†åšŸä¸€æ¬¡ã€‚
    if not snaps:
        try:
            os.makedirs(_race_dir(race_key), exist_ok=True)
            with open(_signal_log_path(race_key), "w", encoding="utf-8") as f:
                pass
        except Exception:
            pass
        return

    share_hist = defaultdict(list)   # (pool,horse) -> [(ts, share%)]
    last_tier = {}
    last_ts_logged = {}
    events = []
    t1, t2, t3 = RISE_TIER1, RISE_TIER2, RISE_TIER3   # ç”¨è¿”ç³»çµ±é è¨­é–€æª»ï¼ˆè£œæ­·å²ï¼Œå””è·Ÿuserè€Œå®¶è‡ªè¨‚å˜…ï¼‰

    for sp in snaps:
        ts = sp.get("ts")
        if ts is None:
            continue

        for pool_code, snap_key in (("WIN", "win"), ("PLA", "pla")):
            odds_map = sp.get(snap_key) or {}
            if not odds_map:
                continue
            try:
                inv_sum = sum(1.0 / float(o) for o in odds_map.values() if float(o) > 0)
            except Exception:
                inv_sum = 0.0
            if inv_sum <= 0:
                continue
            for h, o in odds_map.items():
                try:
                    o = float(o)
                except Exception:
                    continue
                if o <= 0:
                    continue
                share = (1.0 / o) / inv_sum * 100.0
                share_hist[(pool_code, str(h))].append((ts, share))

        for pool_code, snap_key in (("QIN", "qin"), ("QPL", "qpl")):
            raw = sp.get(snap_key) or {}
            if not raw:
                continue
            matrix = {}
            for k, o in raw.items():
                try:
                    a_str, b_str = k.split(",")
                    a, b = int(a_str), int(b_str)
                    o = float(o)
                except Exception:
                    continue
                if o <= 0:
                    continue
                matrix[(a, b)] = o
            part = combo_participation(matrix)
            for h, pct in part.items():
                share_hist[(pool_code, str(h))].append((ts, pct))

        all_horses = set(h for (_p, h) in share_hist.keys())
        for h in all_horses:
            rises = {}
            for pool_code in ("WIN", "PLA", "QIN", "QPL"):
                hist = share_hist.get((pool_code, h))
                if not hist or len(hist) < 2:
                    rises[pool_code] = 0.0
                    continue
                last_pt_ts, last_pt_v = hist[-1]
                cutoff = last_pt_ts - 60
                base_v = None
                for hts, hv in hist:
                    if hts <= cutoff:
                        base_v = hv
                if base_v is None:
                    base_v = hist[0][1]
                rises[pool_code] = last_pt_v - base_v
            max_rise = max(rises.values()) if rises else 0.0
            if max_rise >= t3:
                tier = 3
            elif max_rise >= t2:
                tier = 2
            elif max_rise >= t1:
                tier = 1
            else:
                tier = 0
            prev_tier = last_tier.get(h, 0)
            should_log = False
            if tier > 0:
                if tier > prev_tier or (ts - last_ts_logged.get(h, 0)) >= 60:
                    should_log = True
            if should_log:
                cause_pool = max(rises, key=rises.get)
                events.append({"ts": ts, "horse": h, "pool": cause_pool,
                               "tier": tier, "rise": round(max_rise, 2)})
                last_tier[h] = tier
                last_ts_logged[h] = ts
            elif tier == 0:
                last_tier[h] = 0

    try:
        os.makedirs(_race_dir(race_key), exist_ok=True)
        with open(_signal_log_path(race_key), "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass

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

st_autorefresh(interval=5000, key="auto_refresh")

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
if "RACES" not in st.session_state:
    st.session_state.RACES = {}

# é–‹æ©Ÿè®€ä¸€æ¬¡ç¡¬ç¢Ÿsettingsï¼ˆå¦‚æžœä¹‹å‰æ’³éŽã€Œå„²å­˜è¨­å®šã€ï¼‰ï¼Œè£œåšé è¨­å€¼ã€‚
if "_settings_loaded" not in st.session_state:
    for _k, _v in (load_settings() or {}).items():
        st.session_state.setdefault(_k, _v)
    st.session_state["_settings_loaded"] = True

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

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  DATA FETCH
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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

def fetch_race_info(date_str, venue, race_no):
    """#9ï¼šæ”žæŸå ´è³½äº‹è³‡æ–™ï¼ˆç­æ¬¡/è·é›¢/è·‘é“/åç¨±/é–‹è·‘æ™‚é–“ï¼‰ï¼Œç”¨åšŸåš headerã€‚"""
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
    One call covers the whole meeting (all races) â€” includes postTime (é–‹è·‘æ™‚é–“)."""
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
    """Fetch QIN (é€£è´) + QPL (ä½ç½®Q) odds. Same verbatim query, only the
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
            rows.append({"æ± ": pool_type,
                         "é¦¬è™Ÿ": n.get("combString"),
                         "è³ çŽ‡": odds,
                         "å¤§ç†±": bool(n.get("hotFavourite"))})
    return pd.DataFrame(rows)

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  ENRICH + HISTORY
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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

        # Always store (wall_clock_epoch, odds). Display-X is derived at render
        # time from current mode, so switching to countdown never corrupts old points.
        if curr > 0:
            S["series"][key].append((now_hkt.timestamp(), curr))

    S["last_odds"] = new_last
    df["é–‹è³ "] = open_arr
    df["å³å ´"] = live_arr
    df["è®ŠåŒ–"] = chg_arr
    df["æ–¹å‘"] = dir_arr
    return df, mtp

def record_into_state(pools, S):
    """Lightweight background recorder â€” appends odds to a race's series & open_odds.
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

def record_stakes(df_pool, pool_name, pool_inv, S):
    """Record each horse's current stake ($ = live-share x pool_inv) into stake_hist,
    timestamped by wall-clock epoch seconds. Only when pool_inv is known."""
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df_pool[df_pool["å³å ´"] > 0].copy()
    if sub.empty:
        return
    inv_live = 1.0 / sub["å³å ´"]
    shares = inv_live / inv_live.sum()
    ts = datetime.now(HKT).timestamp()
    for (_, r), sh in zip(sub.iterrows(), shares):
        key = (pool_name, r["é¦¬è™Ÿ"])
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
    """è©²é¦¬å–º target_tsï¼ˆæˆ–ä¹‹å‰æœ€æŽ¥è¿‘ï¼‰å˜…ç´¯ç©æŠ•æ³¨é¡ï¼ˆä½”æ¯”æ³•ï¼‰ã€‚å†‡å°± Noneã€‚"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    if not hist:
        return None
    best = None
    for ts, v in hist:
        if ts <= target_ts:
            best = v
    return best if best is not None else hist[0][1]

def stake_in_bucket(S, pool_name, horse, ts_start, ts_end):
    """è©²é¦¬å–º [ts_start, ts_end] å‘¢æ®µæµå…¥å˜…é‡‘é¡ = end ç´¯ç© âˆ’ start ç´¯ç©ã€‚
    ä½”æ¯”æ³•ï¼ŒåŒæ£’åž‹åœ–åŒä¸€æŠŠå°ºã€‚"""
    s_end = stake_at_ts(S, pool_name, horse, ts_end)
    s_start = stake_at_ts(S, pool_name, horse, ts_start)
    if s_end is None or s_start is None:
        return None
    return s_end - s_start

def current_stake(S, pool_name, horse):
    """è©²é¦¬å³å ´ç´¯ç©ç¸½æŠ•æ³¨é¡ï¼ˆä½”æ¯”æ³•ï¼Œ= æ£’åž‹åœ–æ£’é«˜ = æ¯åˆ†é˜è¡¨åˆè¨ˆï¼‰ã€‚"""
    hist = S["stake_hist"][(pool_name, str(horse))]
    return hist[-1][1] if hist else None

def stake_at_ts_disk(snaps, pool_name, horse, target_ts):
    """ç”±ç¡¬ç¢Ÿ snapshotï¼ˆå·²æŒ‰æ™‚é–“æŽ’åºï¼Œå†‡ä¸Šé™ï¼‰é‡å»ºè©²é¦¬å–º target_ts å—°åˆ»å˜…
    ç´¯ç©æŠ•æ³¨é¡ã€‚åŒ stake_at_ts() é‚è¼¯ä¸€æ¨£ï¼ˆä½”æ¯”æ³•ï¼‰ï¼Œåˆ†åˆ¥ä¿‚å‘¢å€‹å””å—è¨˜æ†¶é«”
    deque å˜… maxlen é™åˆ¶ â€”â€” é–‹è³£æå‰å¹¾å¤šå€‹é˜ã€ç”šè‡³è·¨æ—¥éƒ½è¨ˆå¾—åˆ°ã€‚"""
    if target_ts is None:
        return None
    best = None
    for sp in snaps:
        ts = sp.get("ts")
        if ts is None or ts > target_ts:
            continue
        odds_map = sp.get("win") if pool_name == "WIN" else sp.get("pla")
        if not odds_map:
            continue
        o = odds_map.get(str(horse))
        if o is None:
            continue
        try:
            o = float(o)
        except Exception:
            continue
        if o <= 0:
            continue
        try:
            inv_sum = sum(1.0 / float(v) for v in odds_map.values() if float(v) > 0)
        except Exception:
            inv_sum = 0.0
        ptot = (sp.get("pool") or {}).get(pool_name)
        if not ptot or inv_sum <= 0:
            continue
        best = (1.0 / o) / inv_sum * ptot
    return best

def compute_early_buckets_from_disk(race_key, pool_name, horses, midnight_ts, edge60_ts,
                                    ttl=60, as_of_ts=None):
    """ã€Œéš”å¤œã€ã€Œç•¶æ—¥ã€ç›´æŽ¥ç”±ç¡¬ç¢Ÿ snapshot è¨ˆï¼Œè§£æ±ºé–‹è³£æå‰è¶…éŽ24å°æ™‚ã€è¨˜æ†¶é«”
    deque è£å””æ™’å˜…å•é¡Œã€‚ç”¨ session_state cache ä½çµæžœï¼Œæ¯ ttl ç§’ï¼ˆé è¨­60ï¼‰å…ˆ
    çœŸæ­£é‡æ–°æŽƒä¸€æ¬¡ç¡¬ç¢Ÿï¼›ä¸­é–“å˜…5ç§’refreshå°±ç›´æŽ¥æ”žè¿”cacheå˜…æ•¸ï¼Œå””æœƒæ‹–æ…¢ç•«é¢ï¼Œ
    äº¦å””æœƒæ‹–ç¡¬ç¢ŸIOã€‚

    as_of_tsï¼šåªè¨ˆåˆ°å‘¢ä¸€åˆ»ç‚ºæ­¢ï¼ˆREPLAY ç”¨ï¼Œç­‰ã€Œéš”å¤œ/ç•¶æ—¥ã€åŒå…¶ä»–æ¬„ä¸€é½Š
    åœå–ºæ™‚é–“è»¸æ€å’—å—°ä¸€åˆ»ï¼›LIVE å‚³ None å°±ç”¨æ™’æ‰€æœ‰è¨˜éŒ„ï¼‰ã€‚"""
    cache_key = f"_early_buckets_{race_key}_{pool_name}_{int(as_of_ts) if as_of_ts else 'live'}"
    ts_key = cache_key + "_ts"
    now = datetime.now(HKT).timestamp()
    last = st.session_state.get(ts_key, 0.0)
    if cache_key in st.session_state and (now - last) < ttl:
        return st.session_state[cache_key]
    result = {}
    if midnight_ts is not None and edge60_ts is not None:
        try:
            snaps = load_snapshots(race_key)
        except Exception:
            snaps = []
        if as_of_ts is not None:
            snaps = [sp for sp in snaps if (sp.get("ts") or 0) <= as_of_ts]
        if snaps:
            for h in horses:
                h = str(h)
                # å½©æ± æ‰“å¾žé–‹è³£å°±ä¿‚ç”± $0 é–‹å§‹ç´¯ç©ï¼Œæ‰€ä»¥ stake_at_ts_disk() ç›´æŽ¥
                # æ”žè¿”å˜…ä¿‚ã€Œçµ•å°å€¼ã€ï¼Œå””ä½¿å†æ¸›èµ°ã€Œç¬¬ä¸€å€‹snapshotã€åšåŸºæº–
                # ï¼ˆæ¸›å’—åè€Œæœƒæ¼èµ°é–‹è³£åˆ°ç¬¬ä¸€å€‹snapshotå‘¢ä¸€å°æ®µï¼Œä»¤åˆè¨ˆå°å””ä¸Šï¼‰ã€‚
                v_mid = stake_at_ts_disk(snaps, pool_name, h, midnight_ts)
                v_60 = stake_at_ts_disk(snaps, pool_name, h, edge60_ts)
                overnight = v_mid if v_mid is not None else None
                today = (v_60 - v_mid) if (v_60 is not None and v_mid is not None) else None
                result[h] = {"éš”å¤œ": overnight, "ç•¶æ—¥": today}
    st.session_state[cache_key] = result
    st.session_state[ts_key] = now
    return result

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  RENDER HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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
    sub = df_pool.sort_values("é¦¬è™Ÿ", key=lambda s: pd.to_numeric(s, errors="coerce"))
    for _, r in sub.iterrows():
        key = (r["æ± "], r["é¦¬è™Ÿ"])
        d = r["æ–¹å‘"]
        col = dir_color(d)
        faint = (d == "flat")
        name_col = "var(--subtext)" if faint else "var(--text)"
        chg = r["è®ŠåŒ–"]
        sign = "+" if chg > 0 else ""
        chg_col = MUTE if faint else col
        hot = '<span class="flame">ðŸ”¥</span>' if r.get("å¤§ç†±") else ""
        series = series_to_x(list(S["series"][key]), S, countdown)
        spark = spark_svg(series, col, faint, countdown=countdown)
        rows_html.append(
            f'<div class="row">'
            f'<span class="c-no" style="color:{name_col}">{r["é¦¬è™Ÿ"]}{hot}</span>'
            f'<span class="c-spark">{spark}</span>'
            f'<span class="c-num" style="color:var(--subtext)">{r["é–‹è³ "]:.1f}</span>'
            f'<span class="c-num" style="color:{name_col}">{r["å³å ´"]:.1f}</span>'
            f'<span class="c-chg" style="color:{chg_col}">{sign}{chg:.0f}%</span>'
            f'</div>'
        )

    if countdown:
        axis_labels = ["-14åˆ†", "-10åˆ†", "-6åˆ†", "-2åˆ†", "é–‹è·‘"]
        sub_label = "å…¨å ´ãƒ»å°é½Šé–‹è·‘å€’æ•¸è»¸"
        head_label = "èµ°å‹¢ï¼ˆ-14åˆ† â†’ é–‹è·‘ï¼‰"
    else:
        axis_labels = ["é–‹æ©Ÿ", "", "ç¶“éŽæ™‚é–“", "", "ç¾åœ¨"]
        sub_label = "å…¨å ´ãƒ»é–‹æ©Ÿå¾Œç¶“éŽæ™‚é–“"
        head_label = "èµ°å‹¢ï¼ˆé–‹æ©Ÿ â†’ ç¾åœ¨ï¼‰"
    axis = "".join(f"<span>{t}</span>" for t in axis_labels)
    html = (
        f'<div class="panel">'
        f'<div class="panel-title">{pool_title}</div>'
        f'<div class="panel-sub">{sub_label}</div>'
        f'<div class="thead">'
        f'<span class="c-no">é¦¬è™Ÿ</span>'
        f'<span class="c-spark">{head_label}</span>'
        f'<span class="c-num">é–‹è³ </span>'
        f'<span class="c-num">å³å ´</span>'
        f'<span class="c-chg">è®ŠåŒ–</span>'
        f'</div>'
        f'{"".join(rows_html)}'
        f'<div class="axis"><span class="c-no"></span>'
        f'<span class="axis-inner">{axis}</span>'
        f'<span class="c-num"></span><span class="c-num"></span><span class="c-chg"></span></div>'
        f'<div class="legend">'
        f'<span><i style="background:{INFO}"></i>è½é£›ï¼ˆæœ‰éŒ¢å…¥ï¼‰</span>'
        f'<span><i style="background:{DANGER}"></i>å›žé£›ï¼ˆè³‡é‡‘é›¢å ´ï¼‰</span>'
        f'<span><i style="background:{MUTE}"></i>å¹³ç©©</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def rank_panel(df_pool, S, want_down, title, sub):
    """want_down=True -> è½é£›æ¦œ (blue, odds dropped, pct<0);
       want_down=False -> å›žé£›æ¦œ (red, odds rose, pct>0)."""
    color = INFO if want_down else DANGER
    items = []
    for _, r in df_pool.iterrows():
        pct = r["è®ŠåŒ–"]  # negative = è½é£›, positive = å›žé£›
        if want_down and pct < -FLAT_THRESHOLD:
            items.append((r["é¦¬è™Ÿ"], abs(pct)))   # store magnitude
        elif (not want_down) and pct > FLAT_THRESHOLD:
            items.append((r["é¦¬è™Ÿ"], abs(pct)))
    items.sort(key=lambda t: t[1], reverse=True)
    items = items[:6]
    maxv = items[0][1] if items else 1.0

    rows = []
    for no, v in items:
        w = max(4, v / maxv * 100)
        sign = "-" if want_down else "+"
        rows.append(
            f'<div class="rank-row"><div class="rank-head">'
            f'<span style="font-size:12px;font-weight:600">{no} è™Ÿ</span>'
            f'<span style="font-family:JetBrains Mono,monospace;font-size:10px;color:{color}">{sign}{v:.0f}%</span>'
            f'</div><div class="rank-bar"><div class="rank-fill" style="width:{w:.0f}%;background:{color}"></div></div></div>'
        )
    if not rows:
        rows = ['<div style="font-size:11px;color:var(--muted);padding:8px 0">æš«ç„¡</div>']
    icon = "ðŸ“‰" if want_down else "ðŸ“ˆ"
    html = (f'<div class="panel">'
            f'<div class="panel-title">{icon} {title}</div>'
            f'<div class="panel-sub">{sub}</div>{"".join(rows)}</div>')
    st.markdown(html, unsafe_allow_html=True)

def divergence_panel(df):
    """Win moving but Place not (or vice versa)."""
    win = df[df["æ± "] == "WIN"].set_index("é¦¬è™Ÿ")
    pla = df[df["æ± "] == "PLA"].set_index("é¦¬è™Ÿ")
    rows = []
    for no in win.index:
        if no not in pla.index:
            continue
        wc = win.loc[no, "è®ŠåŒ–"]
        pc = pla.loc[no, "è®ŠåŒ–"]
        w_move = abs(wc) > FLAT_THRESHOLD
        p_move = abs(pc) > FLAT_THRESHOLD
        if w_move and not p_move:
            note = "åªå¾—ç¨è´æœ‰éŒ¢ â€” åšè´"
            wlabel = f'ç¨è´ {wc:+.0f}%'
            rows.append((no, wlabel, "ä½ç½® ç„¡è®Š", note))
        elif p_move and not w_move:
            note = "åªå¾—ä½ç½®æœ‰éŒ¢ â€” åšä½ç½®ï¼each-way"
            plabel = f'ä½ç½® {pc:+.0f}%'
            rows.append((no, "ç¨è´ ç„¡è®Š", plabel, note))

    body = []
    for no, wl, pl, note in rows[:8]:
        body.append(
            f'<div class="div-row">'
            f'<span style="width:38px;font-weight:600">{no} è™Ÿ</span>'
            f'<span class="pill pill-win">{wl}</span>'
            f'<span class="pill pill-mute">{pl}</span>'
            f'<span style="color:var(--subtext)">{note}</span>'
            f'</div>'
        )
    if not body:
        body = ['<div style="font-size:11px;color:var(--muted);padding:8px 0">æš«ç„¡èƒŒé¦³</div>']
    html = (f'<div class="panel"><div class="panel-title">ç¨è´ / ä½ç½® èƒŒé¦³</div>'
            f'<div class="panel-sub">åªå¾—ä¸€å€‹æ± æœ‰éŒ¢ã€å¦ä¸€å€‹å””éƒ</div>{"".join(body)}</div>')
    st.markdown(html, unsafe_allow_html=True)

def _fmt_money(v):
    """Format HKD compactly: $1.23M / $456K / $789."""
    if v is None or v <= 0:
        return "â€”"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"

def compute_scores(sub, S, pool_name, overround, other_pool_drops):
    """Compute a 0-100 composite score per horse from independent signals.
    Returns dict: é¦¬è™Ÿ -> {surge, drop, water, agree, total}.
    - surge (40): recent drop relative to field (how much it stands out now)
    - drop  (30): absolute recent drop % level
    - water (20): market maturity (overround near WATER_IDEAL)
    - agree (10): both WIN & PLA dropping (cross-pool confirmation)
    """
    scores = {}
    drops = sub["è¿‘30è·Œ"].tolist()
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
        horse = r["é¦¬è™Ÿ"]
        d = r["è¿‘30è·Œ"]
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
    Adds: stake bar, â–²/ðŸ”¥ surge highlight, surge-first sorting, è¿‘30ç§’æµå…¥$.
    'Surge' is detected purely by $ inflow in the last 30s (Method A, no time gate)."""
    sub = df_pool[df_pool["å³å ´"] > 0].copy()
    if sub.empty:
        st.markdown(
            f'<div class="panel"><div class="panel-title">ðŸ’° {pool_name}è³‡é‡‘æµå‘</div>'
            f'<div class="panel-sub">æš«ç„¡è³‡æ–™</div></div>', unsafe_allow_html=True)
        return
    # open-odds fallback: if é–‹è³  somehow 0/missing, use live so the horse still shows
    sub["é–‹è³ "] = sub.apply(lambda r: r["é–‹è³ "] if r["é–‹è³ "] > 0 else r["å³å ´"], axis=1)

    # pool_name is the display name (ç¨è´/ä½ç½®); the series is keyed by the
    # pool CODE (WIN/PLA) held in the æ±  column. Use the code for lookups.
    pool_code = str(df_pool["æ± "].iloc[0]) if len(df_pool) else pool_name

    inv_live = 1.0 / sub["å³å ´"]
    inv_open = 1.0 / sub["é–‹è³ "]
    sub["å³å ´ä½”æ¯”"] = inv_live / inv_live.sum() * 100.0
    sub["é–‹è³ ä½”æ¯”"] = inv_open / inv_open.sum() * 100.0

    # Market overround = Î£(1/odds). For WIN (1 winner) the fair baseline ~1.0.
    # For PLACE there are multiple winning positions (usually 3, or 2 for small
    # fields), so Î£(1/odds) naturally sums to ~n_place. Normalise by n_place so
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
        # ä½”æ¯”æ³• (self-normalising) â€” proven most accurate as it absorbs the
        # market's real takeout via Î£-normalisation (see derivation doc).
        sub["æŠ•æ³¨é¡"] = sub["å³å ´ä½”æ¯”"] / 100.0 * pool_inv
        # Method B: true absolute stake change since first recorded point.
        sub["æŠ•æ³¨è®ŠåŒ–"] = [stake_change_since_open(S, pool_code, no) if S is not None else 0.0
                          for no in sub["é¦¬è™Ÿ"]]
    else:
        # no pool money -> fall back to relative share change for direction only
        sub["æŠ•æ³¨è®ŠåŒ–"] = sub["å³å ´ä½”æ¯”"] - sub["é–‹è³ ä½”æ¯”"]

    # â”€â”€ recent odds-drop surge (è¿‘30ç§’è³ çŽ‡è·Œå¹… %) â€” always-available signal â”€â”€
    #   >= SURGE_BIG_DROP -> ðŸ”¥ å¤§é‡æ¹§å…¥ï¼ˆlevel 2ï¼‰
    #   >= SURGE_MIN_DROP -> â–² æ€¥è·Œï¼ˆlevel 1ï¼‰
    drops, surges = [], []
    for _, r in sub.iterrows():
        if S is not None:
            drop_pct, _ = recent_speed(S, (pool_code, r["é¦¬è™Ÿ"]), SURGE_WINDOW)  # +ve = dropped
        else:
            drop_pct = 0.0
        drops.append(drop_pct)
        if drop_pct >= SURGE_BIG_DROP:
            surges.append(2)
        elif drop_pct >= SURGE_MIN_DROP:
            surges.append(1)
        else:
            surges.append(0)
    sub["è¿‘30è·Œ"] = drops
    sub["surge"] = surges

    # â”€â”€ ç¶œåˆè©•åˆ†ï¼ˆ100 åˆ†åˆ¶ï¼‰â”€â”€
    other_drops = other_pool_drops or {}
    score_map = compute_scores(sub, S, pool_name, overround, other_drops)
    sub["s_surge"] = [score_map[h]["surge"] for h in sub["é¦¬è™Ÿ"]]
    sub["s_drop"] = [score_map[h]["drop"] for h in sub["é¦¬è™Ÿ"]]
    sub["s_water"] = [score_map[h]["water"] for h in sub["é¦¬è™Ÿ"]]
    sub["s_agree"] = [score_map[h]["agree"] for h in sub["é¦¬è™Ÿ"]]
    sub["s_total"] = [score_map[h]["total"] for h in sub["é¦¬è™Ÿ"]]

    max_stake = sub["æŠ•æ³¨é¡"].max() if have_money else 0

    # â”€â”€ sort: by total score (highest confluence first) â”€â”€
    sub = sub.sort_values(["s_total", "è¿‘30è·Œ"], ascending=[False, False])

    rows = []
    for _, r in sub.iterrows():
        chg = r["æŠ•æ³¨è®ŠåŒ–"]   # Method B: dollars (or % fallback if no money)
        # direction threshold: $ mode uses a small $ floor; % mode uses 0.3
        thresh = 1000.0 if have_money else 0.3
        col = INFO if chg > thresh else (DANGER if chg < -thresh else MUTE)
        surge = int(r["surge"])
        faint = (surge == 0 and abs(chg) <= thresh)   # idle -> dim
        name_col = "var(--muted)" if faint else "var(--text)"

        # surge marker
        if surge == 2:
            marker = '<span style="color:#ff5757">ðŸ”¥</span>'
        elif surge == 1:
            marker = '<span style="color:#ff5757">â–²</span>'
        else:
            marker = ""

        # stake bar (width relative to biggest stake)
        if have_money and max_stake > 0:
            w = max(2, r["æŠ•æ³¨é¡"] / max_stake * 100)
            bar_col = "#ff5757" if surge == 2 else ("#e0a83c" if surge == 1 else INFO)
            bar = (f'<span class="stake-barwrap"><span class="stake-bar" '
                   f'style="width:{w:.0f}%;background:{bar_col};opacity:{0.45 if faint else 0.9}"></span></span>')
        else:
            bar = f'<span class="c-spark" style="color:var(--subtext);font-size:11px">{r["å³å ´ä½”æ¯”"]:.1f}%</span>'

        # recent drop cell â€” shows odds drop % in last 30s (always available)
        drop = r["è¿‘30è·Œ"]
        if drop >= SURGE_BIG_DROP:
            gain_cell = f'<span class="c-num" style="color:#ff5757;font-weight:700">ðŸ”¥â†“{drop:.1f}%</span>'
        elif drop >= SURGE_MIN_DROP:
            gain_cell = f'<span class="c-num" style="color:#e0a83c;font-weight:600">â–²â†“{drop:.1f}%</span>'
        elif drop > 0.5:
            gain_cell = f'<span class="c-num" style="color:var(--subtext)">â†“{drop:.1f}%</span>'
        else:
            gain_cell = '<span class="c-num" style="color:var(--muted)">â€”</span>'

        money_cell = (f'<span class="c-num" style="color:#e0a83c;font-weight:600">{_fmt_money(r["æŠ•æ³¨é¡"])}</span>'
                      if have_money else "")

        # æµå‘ cell â€” Method B shows $ change vs open; fallback shows %
        if have_money:
            flow_sign = "+" if chg > 0 else ("-" if chg < 0 else "")
            flow_cell = f'<span class="c-chg" style="color:{col}">{flow_sign}{_fmt_money(abs(chg))}</span>'
        else:
            flow_sign = "+" if chg > 0.3 else ""
            flow_cell = f'<span class="c-chg" style="color:{col}">{flow_sign}{chg:.1f}%</span>'

        # â”€â”€ åˆ†æ•¸æ¬„ â”€â”€
        total = r["s_total"]
        # total colour: high=red hot, mid=gold, low=grey
        if total >= 70:
            tcol, tweight = "#ff5757", "700"
        elif total >= 40:
            tcol, tweight = "#e0a83c", "600"
        else:
            tcol, tweight = "var(--muted)", "400"
        breakdown = (f'<span style="font-size:9px;color:var(--muted)">'
                     f'å‡{r["s_surge"]:.0f}/è·Œ{r["s_drop"]:.0f}/æ°´{r["s_water"]:.0f}/åˆ{r["s_agree"]:.0f}</span>')
        score_cell = (f'<span class="c-score">{breakdown} '
                      f'<b style="color:{tcol};font-weight:{tweight};font-size:13px">{total:.0f}</b></span>')

        row_cls = "row surge-gate" if surge == 2 else "row"
        rows.append(
            f'<div class="{row_cls}">'
            f'<span class="c-no" style="color:{name_col}">{r["é¦¬è™Ÿ"]}{marker}</span>'
            f'{money_cell}'
            f'{gain_cell}'
            f'{flow_cell}'
            f'{score_cell}'
            f'</div>'
        )

    # water maturity light: green<1.25, yellow 1.25-1.35, red>1.35
    if overround < 1.25:
        wlight, wtext = "#22c55e", "è³ çŽ‡æˆç†ŸÂ·å¯ä¿¡"
    elif overround <= 1.35:
        wlight, wtext = "#e0a83c", "æŽ¥è¿‘æˆç†Ÿ"
    else:
        wlight, wtext = "#ff5757", "æœªæˆç†ŸÂ·å¯©æ…Ž"
    water_badge = (f'<span style="color:{wlight}">â—</span> æ°´ä½ {overround:.2f}ï¼ˆ{wtext}ï¼‰')

    accuracy_note = "ç¨è´åæŽ¨æº–ç¢º" if pool_name == "ç¨è´" else "ä½ç½®ç‚ºè¿‘ä¼¼ï¼ˆå¤šä½åˆ†äº«ï¼‰"
    if have_money:
        sub_label = (f'å½©æ± ç¸½é¡ {_fmt_money(pool_inv)}ã€€Â·ã€€{water_badge}ã€€Â·ã€€{accuracy_note}<br>'
                     f'åˆ†æ•¸ï¼å‡(40)+è·Œ(30)+æ°´(20)+åˆ(10)ã€€Â·ã€€'
                     f'ðŸ”¥è¿‘30ç§’è³ çŽ‡è·Œâ‰¥{SURGE_BIG_DROP:.0f}%ã€€â–²â‰¥{SURGE_MIN_DROP:.0f}%')
        head = ('<span class="c-no">é¦¬è™Ÿ</span>'
                '<span class="c-num">æŠ•æ³¨é¡</span>'
                '<span class="c-num">è¿‘30è·Œ</span>'
                '<span class="c-chg">æµå‘$</span>'
                '<span class="c-score">åˆ†é … / ç¸½åˆ†</span>')
    else:
        sub_label = (f'ç”¨è³ çŽ‡åæŽ¨ä½”æ¯”ï¼ˆå½©æ± é‡‘é¡æœªå–å¾—ï¼‰ã€€Â·ã€€{water_badge}<br>'
                     f'åˆ†æ•¸ï¼å‡(40)+è·Œ(30)+æ°´(20)+åˆ(10)')
        head = ('<span class="c-no">é¦¬è™Ÿ</span>'
                '<span class="c-num">å³å ´ä½”æ¯”</span>'
                '<span class="c-num">è¿‘30è·Œ</span>'
                '<span class="c-score">åˆ†é … / ç¸½åˆ†</span>')

    html = (
        f'<div class="panel">'
        f'<div class="panel-title">ðŸ’° {pool_name}è³‡é‡‘æµå‘</div>'
        f'<div class="panel-sub">{sub_label}</div>'
        f'<div class="thead">{head}</div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ff5757"></i>ðŸ”¥è³ çŽ‡å¤§è·Œ / â–²æ€¥è·Œï¼ˆè¿‘30ç§’ï¼‰</span>'
        f'<span><i style="background:{INFO}"></i>è³‡é‡‘æµå…¥</span>'
        f'<span><i style="background:{MUTE}"></i>éœæ­¢</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def stake_bar_chart(df_pool, pool_name, pool_inv, S):
    """Horizontal bar chart: each horse's stake ($ = live-share Ã— pool_inv).
    Bars sorted by stake (largest first). Hot money (recent odds drop) tints the
    bar and shows the inflow $; horses that EVER surged keep a small ðŸ”¥ memo."""
    if pool_inv is None or pool_inv <= 0:
        return
    sub = df_pool[df_pool["å³å ´"] > 0].copy()
    if sub.empty:
        return
    pool_code = str(df_pool["æ± "].iloc[0]) if len(df_pool) else pool_name

    inv_live = 1.0 / sub["å³å ´"]
    sub["å³å ´ä½”æ¯”"] = inv_live / inv_live.sum() * 100.0
    sub["æŠ•æ³¨é¡"] = sub["å³å ´ä½”æ¯”"] / 100.0 * pool_inv

    # recent drop% + recent $ inflow (30s) per horse
    drops, inflows = [], []
    for _, r in sub.iterrows():
        dp, _ = recent_speed(S, (pool_code, r["é¦¬è™Ÿ"]), SURGE_WINDOW) if S is not None else (0.0, 0.0)
        drops.append(dp)
        inflows.append(recent_stake_gain(S, pool_code, r["é¦¬è™Ÿ"], SURGE_WINDOW) if S is not None else 0.0)
        # remember peak drop ever (ðŸ”¥ memory)
        if S is not None and dp > 0:
            key = (pool_code, r["é¦¬è™Ÿ"])
            prev = S["ever_surged"].get(key, 0.0)
            if dp > prev:
                S["ever_surged"][key] = dp
    sub["è¿‘30è·Œ"] = drops
    sub["è¿‘30å…¥"] = inflows

    sub = sub.sort_values("æŠ•æ³¨é¡", ascending=False)
    max_stake = sub["æŠ•æ³¨é¡"].max()

    rows = []
    for _, r in sub.iterrows():
        horse = r["é¦¬è™Ÿ"]
        stake = r["æŠ•æ³¨é¡"]
        drop = r["è¿‘30è·Œ"]
        inflow = r["è¿‘30å…¥"]
        w = max(2, stake / max_stake * 100) if max_stake > 0 else 2

        hot_now = drop >= SURGE_MIN_DROP
        big_now = drop >= SURGE_BIG_DROP
        ever = S["ever_surged"].get((pool_code, horse), 0.0) if S is not None else 0.0

        bar_col = "#ff5757" if big_now else ("#e0a83c" if hot_now else INFO)
        # overlay label: show inflow $ when hot, else stake
        if hot_now and inflow > 0:
            overlay = f'ðŸ”¥+{_fmt_money(inflow)}' if big_now else f'â–²+{_fmt_money(inflow)}'
            overlay_col = "#fff"
        else:
            overlay = ""
        # ever-surged memo (small flame kept even after it cools)
        memo = ''
        if ever >= SURGE_BIG_DROP and not big_now:
            memo = f'<span style="color:#ff5757;font-size:9px" title="æ›¾å¤§è·Œ{ever:.0f}%">ðŸ”¥</span>'
        elif ever >= SURGE_MIN_DROP and not hot_now:
            memo = f'<span style="color:#e0a83c;font-size:9px" title="æ›¾æ€¥è·Œ{ever:.0f}%">â–²</span>'

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
        f'<div class="panel-title">ðŸ“Š {pool_name}æŠ•æ³¨é¡æ£’åž‹åœ–</div>'
        f'<div class="panel-sub">æ£’é•·ï¼æŠ•æ³¨é¡ï¼ˆå¤§åˆ°å°æŽ’ï¼‰Â· ç†±éŒ¢æµå…¥æ™‚æ£’è®Šè‰²ä¸¦æ¨™æµå…¥é‡‘é¡ Â· '
        f'ðŸ”¥/â–²ï¼æ›¾ç¶“çˆ†éŽï¼ˆè¨˜éŒ„ï¼‰</div>'
        f'{"".join(rows)}'
        f'<div class="legend">'
        f'<span><i style="background:#ff5757"></i>ðŸ”¥å¤§é‡æ¹§å…¥</span>'
        f'<span><i style="background:#e0a83c"></i>â–²ç†±éŒ¢æµå…¥</span>'
        f'<span><i style="background:{INFO}"></i>æ­£å¸¸</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

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
                      m1=100_000, m2=200_000, m3=400_000, mtp=None):
    """ç›´å‘æ£’åž‹åœ–ï¼šæ£’é«˜ï¼ç¸½æŠ•æ³¨é¡ï¼ˆä½”æ¯”æ³•ï¼Œ= æ¯åˆ†é˜è¡¨åˆè¨ˆï¼‰ã€‚
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
    now_ts = datetime.now(HKT).timestamp()
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
        return stake_in_bucket(S, pool, horse, ts_start, ts_end)

    # ã€Œéš”å¤œã€ã€Œç•¶æ—¥ã€ç”±ç¡¬ç¢Ÿè¨ˆï¼ˆå””å—è¨˜æ†¶é«”dequeä¸Šé™å½±éŸ¿ï¼Œé–‹è³£æå‰å¹¾è€éƒ½å•±ï¼‰ï¼›
    # 60/30/20/10åŒé€åˆ†é˜å°±ç”¨è¿”è¨˜æ†¶é«”ï¼ˆå¤ è¿‘ï¼Œå””ä½¿æ‹–ç¡¬ç¢Ÿï¼‰ã€‚
    # disk_race_keyï¼šREPLAY æ€å—°å ´å˜… keyï¼ˆåŒä¸Šé¢ä¸‹æ‹‰é¸å–®å¯èƒ½å””åŒå ´ï¼‰ï¼Œ
    # å†‡å‚³å°±ç”¨è¿” S è‡ªå·±å—°å€‹ï¼ˆLIVE æƒ…æ³ï¼‰ã€‚
    race_key_now = disk_race_key or S.get("race_key")
    early_map = (compute_early_buckets_from_disk(
        race_key_now, pool, list(sub["é¦¬è™Ÿ"]), midnight_ts, edge_ts(60),
        as_of_ts=as_of_ts)
        if race_key_now else {})

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
        head += (f'<th style="text-align:right;padding:2px 5px;font-size:9px;color:{"#78899a" if is_hour else "var(--muted)"};{col_bg}">'
                 f'{lbl}</th>')
    head += '<th style="text-align:right;padding:3px 5px;font-size:9px;color:#e0a83c">åˆè¨ˆ</th>'

    body = ""
    for horse, odds, per_col in rows_data:
        # åˆè¨ˆ = å³å ´ç¸½æŠ•æ³¨ï¼ˆä½”æ¯”æ³•ï¼ŒåŒæ£’åž‹åœ–æ£’é«˜ä¸€è‡´ï¼‰
        total = current_stake(S, pool, horse)
        if total is None:
            total = sum(v for v in per_col if v) or 0
        cells = ""
        for (lbl, is_hour, is_prev, _, _), v in zip(cols, per_col):
            txt = f'+{_fmt_money(v)}' if (v and v > 0) else ('â€”' if not v else _fmt_money(v))
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
        f'<div class="panel-title">ðŸ“‹ è½æ³¨é‡‘é¡è¡¨ï¼ˆ{title} Â· æ™‚é–“ç”±å·¦åˆ°å³ï¼‰</div>'
        f'<div class="panel-sub">éš”å¤œ(é–‹è³£â†’00:00) Â· ç•¶æ—¥(00:00â†’-60åˆ†,å…¨å ´ä¸€è‡´) Â· '
        f'60/30/20/10(æ¯æ®µ) Â· 10åˆ†ä¹‹å¾Œé€åˆ†é˜ â†’ é–‹è·‘ Â· '
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

    # record share history (for 1-min rise) â€” all four pools
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

        # â”€â”€ è¨˜éŒ„è½ 30åˆ†é˜è¨Šè™Ÿlogï¼ˆå‡ç´šå³è¨˜ï¼›åŒç´šæ¯60ç§’å…ˆå†è¨˜ä¸€æ¬¡ï¼Œå””æœƒ5ç§’refreshå°±æ´—ç‰ˆï¼‰â”€â”€
        last_tier_map = S.setdefault("_last_logged_tier", {})
        last_ts_map = S.setdefault("_last_logged_ts", {})
        prev_tier = last_tier_map.get(h, 0)
        now_ts_sig = datetime.now(HKT).timestamp()
        should_log = False
        if tier > 0:
            if tier > prev_tier or (now_ts_sig - last_ts_map.get(h, 0)) >= 60:
                should_log = True
        if should_log:
            cause_pool = max(rises, key=rises.get)
            event = {"ts": now_ts_sig, "horse": h, "pool": cause_pool,
                     "tier": tier, "rise": round(max_rise, 2)}
            S["signal_log"].append(event)
            race_key_now = S.get("race_key")
            if race_key_now:
                try:
                    append_signal_log(race_key_now, event)
                except Exception:
                    pass
            last_tier_map[h] = tier
            last_ts_map[h] = now_ts_sig
        elif tier == 0:
            last_tier_map[h] = 0

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

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  HEADER + CONTROLS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
st.markdown(
    f'<div class="hdr"><div class="hdr-title">ðŸŽ {APP_NAME}</div>'
    f'<span class="live"><span class="live-dot"></span>å¯¦æ™‚ Â· 5ç§’</span></div>',
    unsafe_allow_html=True)

# â”€â”€ è³‡æ–™ä¾†æº + æ¨¡å¼ï¼šåˆä½µä¸€åˆ—ï¼Œç½®é ‚ï¼ˆæ±ºå®šæˆå€‹ç•«é¢é»žé‹ä½œå˜…æœ€é ‚å±¤é–‹é—œï¼‰â”€â”€
st.markdown('<div style="font-size:12px;color:var(--subtext);margin:4px 0 2px">ðŸ“¡ è³‡æ–™ä¾†æºã€€ã€€ã€€â±ï¸ æ¨¡å¼</div>',
            unsafe_allow_html=True)
_src_col, _sep_col, _mode_col = st.columns([2.4, 0.1, 2])
with _src_col:
    data_src = st.radio("è³‡æ–™ä¾†æº", ["ðŸ’¾ é›²ç«¯è¨˜éŒ„ï¼ˆè®€ç¡¬ç¢ŸÂ·å¿«ï¼‰", "ðŸŒ ç›´æŽ¥é€£ç·šï¼ˆæ‹‰HKJCï¼‰"],
                        horizontal=True, label_visibility="collapsed", key="data_src")
    use_disk = "é›²ç«¯" in data_src
with _mode_col:
    mode = st.radio("æ¨¡å¼", ["â— LIVE å³å ´", "ðŸ” REPLAY ç¿»ç‡"], horizontal=True,
                    label_visibility="collapsed", key="mode_toggle")
replay_mode = "REPLAY" in mode
replay_snaps = None
replay_idx = None

# â”€â”€ #8ï¼šè‡ªå‹•åŒæ­¥é¦¬æœƒè³½æœŸï¼ˆåˆ—å‡ºæ‰€æœ‰æœ‰è³½äº‹å˜…æ—¥æœŸ+å ´åœ°ï¼ŒåŒ…æ‹¬æµ·å¤–ï¼‰â”€â”€
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
        opts = [f"{m['date']} Â· {venue_label(m['venue'])} ({m['venue']}) Â· {m['n_races']}å ´"
                for m in _meetings]
        # default: æœ€æŽ¥è¿‘ä»Šæ—¥å˜…è³½äº‹
        _today = date.today().isoformat()
        _def = 0
        for i, m in enumerate(_meetings):
            if m["date"] >= _today:
                _def = i
                break
        pick_idx = st.selectbox("è³½äº‹ï¼ˆè‡ªå‹•åŒæ­¥é¦¬æœƒï¼‰", range(len(opts)),
                                format_func=lambda i: opts[i], index=_def)
        _sel = _meetings[pick_idx]
        race_date = datetime.strptime(_sel["date"], "%Y-%m-%d").date()
        course = _sel["venue"]
        _max_race = max(1, _sel["n_races"] or 14)
    else:
        st.warning("æš«æ™‚æ”žå””åˆ°é¦¬æœƒè³½æœŸï¼Œç”¨æ‰‹å‹•æ€")
        race_date = st.date_input("æ—¥æœŸ", date.today())
        course = "ST"
        _max_race = 14
with c2:
    if not _meetings:
        course = st.selectbox("å ´åœ°", ["ST", "HV"],
                              format_func=lambda x: f"{venue_label(x)} {x}")
    else:
        st.markdown(f'<div style="font-size:9px;color:var(--subtext);margin-top:6px">å ´åœ°</div>'
                    f'<div style="font-size:14px;color:var(--text);font-weight:600">'
                    f'{venue_label(course)} {course}</div>', unsafe_allow_html=True)
with c3:
    race_no = st.number_input("å ´æ¬¡", 1, int(_max_race), 1)
with c4:
    post_input = st.text_input("é–‹è·‘æ™‚é–“ (å¯é¸)", value="", placeholder="è‡ªå‹•/å¯è¦†è“‹",
                               help="è®€ç¡¬ç¢Ÿæ¨¡å¼æœƒè‡ªå‹•æ”žé–‹è·‘æ™‚é–“ã€‚æƒ³æ‰‹å‹•è¦†è“‹å…ˆå¡« HH:MMã€‚")
with c5:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    reset_clicked = st.button("ðŸ”„ é‡è¨­æ­¤å ´èµ°å‹¢", use_container_width=True)

# æ•æ„Ÿåº¦è¨­å®šï¼ˆå¯æ‘ºç–Šï¼Œå””é˜»ä¸»ç•«é¢ï¼‰
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

    st.caption("å››æ± ç†±åº¦ç”¨ä½”æ¯”%ï¼›æ£’åž‹åœ–+é‡‘é¡è¡¨ç”¨å¯¦è³ªé‡‘é¡ï¼ˆè¿‘1åˆ†é˜å¯¦è³ªè½æ³¨ï¼‰ï¼Œå…©å€‹é‡‘é¡è¡¨å®Œç¾ŽåŒæ­¥ã€‚ä½Žï¼å¤šæç¤ºã€é«˜ï¼å°‘ä½†ç²¾ã€‚")

if replay_mode:
    saved = list_saved_races()
    if not saved:
        st.info("æš«æ™‚æœªæœ‰å·²å„²å­˜å˜…å ´æ¬¡è¨˜éŒ„ã€‚é–‹ä½ä¸€å ´ï¼ˆæœ‰å½©æ± æ•¸æ“šï¼‰å¹¾åˆ†é˜ï¼Œä½¢æœƒæ¯ 30 ç§’è‡ªå‹•è¨˜ä½Žï¼Œä¹‹å¾Œå°±å¯ä»¥å–ºå‘¢åº¦æ€è¿”ç¿»ç‡ã€‚")
    else:
        pick = st.selectbox("æ€å ´æ¬¡", saved, index=len(saved) - 1)
        st.session_state["_replay_pick"] = pick
        replay_snaps = load_snapshots(pick)
        if replay_snaps:
            n = len(replay_snaps)
            # æ™‚é–“è»¸æ»‘æ¡¿æœ¬èº«ï¼ˆé€£åŒå¿«æ·è·³é»žï¼‰æ¬å’—åŽ»è½é¢ã€Œæ£’åž‹åœ–ã€ä¸Šé¢å…ˆçœŸæ­£
            # renderï¼›å‘¢åº¦æ·¨ä¿‚è®€/åˆå§‹åŒ–è¿”å€‹å€¼ï¼Œç­‰æˆé è¨ˆç®—(df/mtp/stake_histç­‰)
            # ç”¨å¾—åˆ°ã€‚ç”¨ session_state key ä»¤å€‹å€¼å¯ä»¥ã€Œæ—©è®€ã€é²ç•«ã€ã€‚
            if "replay_idx_slider" not in st.session_state:
                st.session_state["replay_idx_slider"] = n - 1
            st.session_state["replay_idx_slider"] = min(st.session_state["replay_idx_slider"], n - 1)
            replay_idx = st.session_state["replay_idx_slider"]
        else:
            st.info("å‘¢å ´å†‡è¨˜éŒ„é»ž")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  ACTIVE CONTEXT â€” LIVE åŒ REPLAY å„æœ‰è‡ªå·±ä¸€å¥—ï¼Œå””å…±ç”¨ã€å””äº’ç›¸æ±¡æŸ“
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# æ¦‚å¿µï¼šæˆå€‹ dashboard åªä¿‚ä¸€å¥—é¡¯ç¤ºé‚è¼¯ï¼Œç”±å‘¢ä¸‰å€‹è®Šæ•¸æ±ºå®šç‡å’©ï¼š
#   ACTIVE_RACE_KEY  = è€Œå®¶ç‡ç·Šé‚Šä¸€å ´
#   ACTIVE_POST_TIME = å—°å ´å˜…é–‹è·‘æ™‚é–“
#   ACTIVE_NOW_TS    = ç‡ç·Šé‚Šä¸€åˆ»ï¼ˆLIVEï¼è€Œå®¶ï¼›REPLAYï¼æ™‚é–“è»¸æ€å—°åˆ»ï¼‰
# è½é¢æ‰€æœ‰ panelï¼ˆæ£’åž‹åœ–ï¼è½æ³¨è¡¨ï¼å››æ± ç†±åº¦ï¼è¨Šè™Ÿï¼‰ä¸€å¾‹ç”¨å‘¢ä¸‰å€‹ï¼Œ
# å””å†å„è‡ªè®€ S["race_key"] / S["post_time"]ï¼Œäº¦å””å†åˆ† LIVE / REPLAY å¯«å…©å¥—ã€‚
LIVE_RACE_KEY = f"{race_date}|{course}|{race_no}"

if replay_mode and replay_snaps:
    # REPLAY ç”¨è‡ªå·±ä¸€å¥— stateï¼ˆkey åŠ å‰ç¶´ï¼‰ï¼Œè¡Œè½åŽ»é»žæ”¹éƒ½å””æœƒå½±éŸ¿ LIVE å—°å ´è¨˜æ†¶
    ACTIVE_RACE_KEY = st.session_state.get("_replay_pick") or LIVE_RACE_KEY
    S = get_state("REPLAY::" + ACTIVE_RACE_KEY)
    S["race_key"] = ACTIVE_RACE_KEY
    # é–‹è·‘æ™‚é–“ç”± REPLAY å—°å ´è‡ªå·±å˜… snapshot å–çœ¾æ•¸ï¼ˆè¦‹ post_time_from_snaps
    # å˜…è¨»è§£ï¼šå””å¯ä»¥æ”žæœ€å¾Œä¸€å€‹ï¼Œrecorder å¶ç„¶æœƒå¯«å…¥ç•°å¸¸å€¼ï¼‰ã€‚
    _rpt = post_time_from_snaps(replay_snaps)
    ACTIVE_POST_TIME = datetime.fromtimestamp(_rpt, HKT) if _rpt else None
    ACTIVE_NOW_TS = (replay_snaps[replay_idx].get("ts")
                     if replay_idx is not None and replay_idx < len(replay_snaps) else None)
else:
    ACTIVE_RACE_KEY = LIVE_RACE_KEY
    if reset_clicked:
        S = reset_state(LIVE_RACE_KEY)
    else:
        S = get_state(LIVE_RACE_KEY)
    ACTIVE_POST_TIME = None      # ä¸‹é¢ç”± manual_post / ç¡¬ç¢Ÿ snapshot æ±ºå®š
    ACTIVE_NOW_TS = None         # None ï¼ ç”¨ã€Œè€Œå®¶ã€

# ç‚ºå’—å…¼å®¹èˆŠ codeï¼Œrace_key ç¹¼çºŒæŒ‡ä½ã€Œè€Œå®¶ç‡ç·Šå—°å ´ã€
race_key = ACTIVE_RACE_KEY

# è½‰å ´æ¬¡ / é‡æ–°æ€è¿”å‘¢å ´ä¹‹å¾Œï¼Œç”±ç¡¬ç¢Ÿè£œè¿”è¨˜æ†¶é«”æ¼å’—å˜…æ­·å²ï¼ˆREPLAY è‡ªå·±æœƒ
# ç”± snapshot å®Œæ•´é‡å»ºï¼Œå””ä½¿å‘¢æ­¥ï¼›åªåœ¨ LIVE å…ˆåšï¼‰ã€‚
if not replay_mode:
    try:
        sync_state_from_disk(race_key, S)
    except Exception:
        pass
    try:
        sync_signal_log_from_disk(race_key, S)
    except Exception:
        pass

# Manual override (optional). If filled, it wins over auto post time.
manual_post = None
if post_input.strip():
    try:
        hh, mm = post_input.strip().split(":")
        manual_post = datetime(race_date.year, race_date.month, race_date.day,
                               int(hh), int(mm), 0, tzinfo=HKT)
    except Exception:
        manual_post = None


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  FETCH + PROCESS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Sliding window: current race + next 2 (capped at race 14)
window = [n for n in range(int(race_no), int(race_no) + 3) if 1 <= n <= 14]

# Fetch + record the *background* races first (not the current one).
# Small spacing between calls avoids hammering HKJC.
# è®€ç¡¬ç¢Ÿæ¨¡å¼å””ä½¿æ‹‰ HKJCï¼ˆrecorder å·²ç¶“èƒŒæ™¯è¨˜ç·Šï¼‰
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
    pools = []      # ä¸‹é¢ç”¨ disk snapshot é‡å»º
else:
    try:
        pools = fetch_race(str(race_date), course, int(race_no))
    except Exception as e:
        st.error(f"âš ï¸ é€£ç·šå¤±æ•—ï¼š{e}")
        pools = []

# Pool turnover (å½©æ± é‡‘é¡) â€” cached, refreshed at most every 20s so it never
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

# Fetch QIN (é€£è´) + QPL (ä½ç½®Q) odds for this race (only oddsTypes var changes,
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

# â”€â”€ Post timeï¼šLIVE åŒ REPLAY éƒ½è‡ªå‹•æ”žï¼Œæ‰‹å‹•è¼¸å…¥ä»»ä½•æ™‚å€™éƒ½æœ€å¤§ â”€â”€
# REPLAYï¼šå–º ACTIVE CONTEXT ç”±å—°å ´ snapshot å–çœ¾æ•¸æ”žå¥½ã€‚
# LIVE è®€ç¡¬ç¢Ÿï¼šç”±å—°å ´æ‰€æœ‰ snapshot å–çœ¾æ•¸ï¼ˆå””å†æ”žæœ€æ–°ä¸€å€‹ï¼Œé¿å…é›œå€¼ï¼‰ã€‚
# LIVE ç›´æŽ¥é€£ç·šï¼šç”± turnover API å˜… postTime æ”žï¼ˆä¹‹å‰ AUTO DISABLEDï¼Œ
#   è€Œå®¶é–‹è¿”ï¼›å¦‚æžœè‡ªå‹•æ”žåˆ°å˜…å€¼å””å•±ï¼Œæ‰‹å‹•å¡«å°±æœƒè“‹éŽä½¢ï¼‰ã€‚
_auto_post = None
if replay_mode:
    _auto_post = ACTIVE_POST_TIME
else:
    if use_disk:
        _auto_post = post_time_for_race(ACTIVE_RACE_KEY)
    else:
        _pt_raw = (turnover_map.get(int(race_no)) or {}).get("post")
        _auto_post = parse_post_time(_pt_raw) if _pt_raw else None
        # ç›´æŽ¥é€£ç·šæ”žå””åˆ°å°±é€€è¿”åŽ»ç¡¬ç¢Ÿè¨˜éŒ„ï¼ˆrecorder èƒŒæ™¯ä¸€ç›´è¨˜ç·Šï¼‰
        if _auto_post is None:
            _auto_post = post_time_for_race(ACTIVE_RACE_KEY)

S["post_time"] = manual_post or _auto_post
ACTIVE_POST_TIME = S["post_time"]

df = pools_to_df(pools)

# â”€â”€ è®€ç¡¬ç¢Ÿ LIVEï¼šç”¨æœ€æ–° snapshot é‡å»ºï¼ˆå””æ‹‰ HKJCï¼Œå¿«ã€è‡ªå‹•é–‹è·‘æ™‚é–“ï¼‰â”€â”€
if use_disk and not replay_mode:
    _snap = load_latest_snapshot(race_key)
    if _snap:
        rows_d = []
        for h, o in (_snap.get("win") or {}).items():
            rows_d.append({"æ± ": "WIN", "é¦¬è™Ÿ": h, "è³ çŽ‡": float(o), "å¤§ç†±": False})
        for h, o in (_snap.get("pla") or {}).items():
            rows_d.append({"æ± ": "PLA", "é¦¬è™Ÿ": h, "è³ çŽ‡": float(o), "å¤§ç†±": False})
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
        # post_time å·²ç¶“å–ºä¸Šé¢çµ±ä¸€è™•ç†ï¼ˆå–çœ¾æ•¸ï¼‰ï¼Œå‘¢åº¦å””å†è¦†å¯«
    else:
        st.info("ðŸ’¾ é›²ç«¯è¨˜éŒ„æ¨¡å¼ï¼šå‘¢å ´æš«æ™‚æœªæœ‰è¨˜éŒ„ï¼ˆrecorder é–‹è³£å¾Œæœƒè‡ªå‹•è¨˜ï¼‰ã€‚æƒ³å³åˆ»ç‡å¯æ€ã€ŒðŸŒ ç›´æŽ¥é€£ç·šã€ã€‚")


# â”€â”€ REPLAY è¦†è“‹ï¼šç”¨æ€å’—å˜… snapshot é‡å»ºæ•¸æ“šï¼ˆå””ç”¨å³å ´ï¼‰â”€â”€
if replay_mode and replay_snaps and replay_idx is not None:
    snap = replay_snaps[replay_idx]
    rows_r = []
    for h, o in (snap.get("win") or {}).items():
        rows_r.append({"æ± ": "WIN", "é¦¬è™Ÿ": h, "è³ çŽ‡": float(o), "å¤§ç†±": False})
    for h, o in (snap.get("pla") or {}).items():
        rows_r.append({"æ± ": "PLA", "é¦¬è™Ÿ": h, "è³ çŽ‡": float(o), "å¤§ç†±": False})
    df = pd.DataFrame(rows_r)
    _p = snap.get("pool") or {}
    win_inv = _p.get("WIN"); pla_inv = _p.get("PLA")
    this_inv = {"WIN": _p.get("WIN"), "PLA": _p.get("PLA"),
                "QIN": _p.get("QIN"), "QPL": _p.get("QPL")}
    qin_matrix = {tuple(int(x) for x in k.split(",")): v for k, v in (snap.get("qin") or {}).items()}
    qpl_matrix = {tuple(int(x) for x in k.split(",")): v for k, v in (snap.get("qpl") or {}).items()}
    qin_part = combo_participation(qin_matrix)
    qpl_part = combo_participation(qpl_matrix)
    # post_time å·²ç¶“å–º ACTIVE CONTEXT ç”±å‘¢å ´è‡ªå·±å˜… snapshot è¨­å®šå¥½ï¼Œå””ä½¿å†è¦†å¯«

if df.empty:
    st.markdown(
        '<div class="panel empty"><div style="font-size:2rem">ðŸ</div>'
        '<div style="font-size:1rem;margin-top:6px">æš«æ™‚æœªæœ‰å³æ™‚è³ çŽ‡</div>'
        '<div style="font-size:12px;color:var(--muted);margin-top:4px">'
        'å¯èƒ½å½©æ± æœªé–‹ã€è³½äº‹å·²å®Œã€æˆ–æš«æ™‚ç„¡æ³•å–å¾—</div></div>',
        unsafe_allow_html=True)
    with st.expander("ðŸ”§ è¨ºæ–· â€” æŸ¥çœ‹ API åŽŸå§‹å›žæ‡‰"):
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
        # mtpï¼šç”¨ ACTIVE_POST_TIMEï¼ˆå–çœ¾æ•¸å—°å€‹ï¼‰ï¼Œå””å¯ä»¥ç”¨å€‹åˆ¥ snapshot è‡ªå·±
        # å—°å€‹ post_time â€”â€” å—°å€‹å¯èƒ½ä¿‚ recorder å¯«è½å˜…é›œå€¼ã€‚
        if ACTIVE_POST_TIME is not None:
            mtp = (snap_now["ts"] - ACTIVE_POST_TIME.timestamp()) / 60.0
        else:
            mtp = None
    else:
        # Record current stake ($) per horse for surge detection (only when pool known)
        record_stakes(df[df["æ± "] == "WIN"], "WIN", win_inv, S)
        record_stakes(df[df["æ± "] == "PLA"], "PLA", pla_inv, S)

        # â”€â”€ å¯«ç¡¬ç¢Ÿ snapshotï¼ˆæ¯ 30 ç§’ä¸€æ¬¡ï¼‰â”€â”€
        _snap_key = f"_last_snap_{race_key}"
        _now_epoch = datetime.now(HKT).timestamp()
        _last_snap = st.session_state.get(_snap_key, 0)
        if _now_epoch - _last_snap >= SNAPSHOT_INTERVAL:
            try:
                snap = {
                    "ts": _now_epoch,
                    "race_key": race_key,
                    "post_time": S["post_time"].timestamp() if S["post_time"] else None,
                    "win": {str(r["é¦¬è™Ÿ"]): r["å³å ´"] for _, r in df[df["æ± "] == "WIN"].iterrows()},
                    "pla": {str(r["é¦¬è™Ÿ"]): r["å³å ´"] for _, r in df[df["æ± "] == "PLA"].iterrows()},
                    "pool": {"WIN": win_inv, "PLA": pla_inv,
                             "QIN": this_inv.get("QIN"), "QPL": this_inv.get("QPL")},
                    "qin": {f"{a},{b}": o for (a, b), o in qin_matrix.items()},
                    "qpl": {f"{a},{b}": o for (a, b), o in qpl_matrix.items()},
                }
                save_snapshot(race_key, snap)
                st.session_state[_snap_key] = _now_epoch
            except Exception:
                pass

    # â”€â”€ post-time / countdown status line â”€â”€
    if S["post_time"] is not None and mtp is not None:
        src = "æ‰‹å‹•" if manual_post is not None else "è‡ªå‹•æŠ“å–"
        if mtp < 0:
            secs_left = abs(mtp) * 60
            if secs_left <= 60:
                cd = f"è·é›¢é–‹è·‘ {secs_left:.0f} ç§’ã€€ðŸ”¥å…¥é–˜çª—"
            else:
                cd = f"è·é›¢é–‹è·‘ {abs(mtp):.0f} åˆ†é˜"
        else:
            cd = "å·²é–‹è·‘ / å°ç›¤"
        pt_label = S["post_time"].strftime("%H:%M")
        st.markdown(
            f'<div style="font-family:JetBrains Mono,monospace;font-size:11px;color:var(--subtext);'
            f'margin-bottom:8px">é–‹è·‘æ™‚é–“ {pt_label}ï¼ˆ{src}ï¼‰Â· {cd} Â· å¯¦æ™‚è·Ÿè¹¤ä¸­</div>',
            unsafe_allow_html=True)
    else:
        elapsed = ""
        if S["started_at"]:
            mins = (datetime.now(HKT) - S["started_at"]).total_seconds() / 60.0
            elapsed = f" Â· å·²ç›£å¯Ÿ {mins:.0f} åˆ†é˜"
        st.markdown(
            f'<div style="font-family:JetBrains Mono,monospace;font-size:11px;color:var(--subtext);'
            f'margin-bottom:8px">æ™‚é–“è»¸ï¼šé–‹æ©Ÿå¾Œç¶“éŽæ™‚é–“ï¼ˆå·¦ï¼é–‹æ©Ÿï¼Œå³ï¼ç¾åœ¨ï¼‰{elapsed} Â· '
            f'å¦‚éœ€å°é½Šé–‹è·‘å€’æ•¸ï¼Œå¯åœ¨ä¸Šæ–¹å¡«é–‹è·‘æ™‚é–“</div>',
            unsafe_allow_html=True)

    # â”€â”€ sliding-window / multi-race tracking indicator â”€â”€
    win_label = "ã€".join(f"R{n}" for n in window)
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
    tracked_str = "ã€".join(lbl for _, lbl in parts_list) if parts_list else "â€”"
    st.markdown(
        f'<div style="font-family:JetBrains Mono,monospace;font-size:10px;color:var(--muted);'
        f'margin-bottom:8px">èƒŒæ™¯è¦–çª—ï¼ˆç¾æ­£è¨˜éŒ„ï¼‰ï¼š{win_label} Â· '
        f'å·²ç´¯ç©æ•¸æ“šå ´æ¬¡ï¼š{tracked_str} Â· åˆ‡æ›å ´æ¬¡å””æœƒæ¸…èµ°æ•¸æ“š</div>',
        unsafe_allow_html=True)

    # â”€â”€ plunge alerts (recent fast drops) â”€â”€
    alerts = []
    win_df_full = df[df["æ± "] == "WIN"]
    for _, r in win_df_full.iterrows():
        key = (r["æ± "], r["é¦¬è™Ÿ"])
        pct, mag = recent_speed(S, key, PLUNGE_WINDOW)
        if pct >= PLUNGE_PCT:
            alerts.append((r["é¦¬è™Ÿ"], pct, r["å³å ´"]))
    alerts.sort(key=lambda t: t[1], reverse=True)
    for no, pct, odds in alerts[:3]:
        st.markdown(
            f'<div class="alert-bar">âš ï¸ '
            f'<b>æ’æ°´è­¦ç¤º</b>ã€€{no} è™Ÿæ–¼ {PLUNGE_WINDOW} ç§’å…§æ€¥è·Œ {pct:.0f}%ï¼Œå³å ´ {odds:.1f}</div>',
            unsafe_allow_html=True)

    # â•â•â• #9 è³½äº‹è³‡æ–™ headerï¼ˆè·Ÿé¦¬æœƒæ ¼å¼ï¼‰â•â•â•
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
            _pt, _ri.get("cls"), f'{_ri.get("dist")}ç±³' if _ri.get("dist") else None,
            _ri.get("track"), _ri.get("course"),
            f'å ´åœ°{_ri.get("going")}' if _ri.get("going") else None,
            f'{_ri.get("field")}åŒ¹' if _ri.get("field") else None,
        ] if b]
        _rname = _ri.get("name") or ""
        _name_html = (f'<div style="font-size:11px;color:var(--muted);margin-top:2px">{_rname}</div>'
                      if _rname else "")
        st.markdown(
            f'<div style="background:var(--card);border:1px solid var(--border);border-radius:10px;'
            f'padding:8px 14px;margin-bottom:10px">'
            f'<span style="font-size:13px;font-weight:600;color:var(--text)">'
            f'{_ri.get("date","")} {venue_label(_ri.get("venue",""))} Â· ç¬¬ {_ri["no"]} å ´</span>'
            f'<span style="font-size:11px;color:var(--subtext);margin-left:10px">'
            f'{" Â· ".join(_bits)}</span>'
            f'{_name_html}'
            f'</div>', unsafe_allow_html=True)

    # â•â•â• â‘  å››å½©æ± æŠ•æ³¨é¡ â•â•â•
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
            f'{_tcard("ç¨è´ WIN", win_inv, INFO)}'
            f'{_tcard("ä½ç½® PLA", pla_inv, "#3b82f6")}'
            f'{_tcard("é€£è´ QIN", this_inv.get("QIN"), "#e0a83c")}'
            f'{_tcard("ä½ç½®Q QPL", this_inv.get("QPL"), "#b48c3c")}'
            f'</div>',
            unsafe_allow_html=True)

    # â•â•â• â‘¡ é€£è´ / ä½ç½®Q è³ çŽ‡çŸ©é™£ â•â•â•
    race_horses = sorted(int(x) for x in df[df["æ± "] == "WIN"]["é¦¬è™Ÿ"].tolist())
    if qin_matrix or qpl_matrix:
        qcol1, qcol2 = st.columns(2)
        with qcol1:
            combo_matrix_panel(qin_matrix, "é€£è´ QIN", race_horses)
        with qcol2:
            combo_matrix_panel(qpl_matrix, "ä½ç½®Q QPL", race_horses)

    # PLA per-horse participation (share% within place pool)
    _pla = df[df["æ± "] == "PLA"].copy()
    if not _pla.empty:
        _inv = 1.0 / _pla["å³å ´"]
        pla_part = {int(h): v for h, v in zip(_pla["é¦¬è™Ÿ"], _inv / _inv.sum() * 100.0)}
    else:
        pla_part = {}

    # â•â•â• â‘¢ å››æ± ç¶œåˆç†±åº¦ï¼ˆåˆ†å±¤ âš¡ðŸ”¥ðŸ’¥ï¼‰+ 30åˆ†é˜è¨Šè™Ÿå½™ç¸½ â•â•â•
    pool_totals = {"WIN": win_inv, "PLA": pla_inv,
                   "QIN": this_inv.get("QIN"), "QPL": this_inv.get("QPL")}
    rise_thresh = st.session_state.get("rise_thresh", 0.5)
    with st.container(key="heat_signal_row"):
        hcol1, hcol2 = st.columns([1.3, 1])
        with hcol1:
            four_pool_heat_panel(df, pla_part, qin_part, qpl_part, S,
                                 pool_totals, cold_odds=10.0, rise_thresh=rise_thresh)
        with hcol2:
            # çµ±ä¸€ç”¨ ACTIVE_RACE_KEYï¼šREPLAY è®€è¿”è‡ªå·±å—°å ´å˜… signals.jsonlï¼Œ
            # å””æœƒå†å¯«ï¼è®€éŒ¯ä¸Šé¢é¸å–®å—°å ´ã€‚
            if replay_mode and replay_snaps and replay_idx is not None:
                try:
                    if ACTIVE_RACE_KEY and not os.path.isfile(_signal_log_path(ACTIVE_RACE_KEY)):
                        backfill_signals_from_disk(ACTIVE_RACE_KEY)
                except Exception:
                    pass
                _sig_events = load_signal_log(ACTIVE_RACE_KEY, 0.0)
            else:
                _sig_events = list(S["signal_log"])
            signal_summary_panel(_sig_events, minutes=30, as_of_ts=ACTIVE_NOW_TS)

    # â•â•â• â‘£ æŠ•æ³¨é¡æ£’åž‹åœ–ï¼ˆç›´å‘ï¼‰â•â•â•
    _m1 = st.session_state.get("money_t1", MONEY_TIER1)
    _m2 = st.session_state.get("money_t2", MONEY_TIER2)
    _m3 = st.session_state.get("money_t3", MONEY_TIER3)
    bar_sort = st.radio("æ£’åž‹åœ–æŽ’åº", ["é †é¦¬è™Ÿ", "é †è³ çŽ‡ï¼ˆç†±â†’å†·ï¼‰"], horizontal=True,
                        label_visibility="collapsed", key="bar_sort")
    sort_key = "è³ çŽ‡" if "è³ çŽ‡" in bar_sort else "é¦¬è™Ÿ"

    # â”€â”€ REPLAY æ™‚é–“è»¸ï¼šå¿«æ·è·³é»ž + æ»‘æ¡¿å¾®èª¿ï¼ˆæ“ºå–ºæŽ’åºä¹‹å¾Œã€æ£’åž‹åœ–ä¹‹å‰ï¼‰â”€â”€
    if replay_mode and replay_snaps:
        _n_snaps = len(replay_snaps)
        st.markdown('<div style="font-size:11px;color:var(--subtext);margin:4px 0 4px">â±ï¸ REPLAY æ™‚é–“è»¸ Â· å¿«æ·è·³åˆ°ï¼ˆæ’³é‚Šå€‹æ¬„ä½ï¼è·³åŽ»å—°æ®µçµæŸï¼Œè©²æ¬„å°±é¡¯ç¤ºå®Œæ•´é‡‘é¡ï¼‰</div>',
                    unsafe_allow_html=True)
        # 15ç²’chipåŒè½æ³¨è¡¨15å€‹æ¬„ä½ä¸€ä¸€å°æ‡‰ã€‚æ¯ç²’è·³åŽ»ã€Œè©²æ™‚æ®µçµæŸã€å—°ä¸€åˆ»ï¼š
        #   éš”å¤œ â†’ ç•¶æ—¥00:00 ï¼ ç•¶æ—¥ â†’ é–‹è·‘å‰60åˆ† ï¼ 60 â†’ é–‹è·‘å‰30åˆ† ï¼ â€¦
        #   2 â†’ é–‹è·‘å‰1åˆ† ï¼ é–‹è·‘ â†’ æœ€å¾Œä¸€å€‹è¨˜éŒ„é»ž
        _chip_defs = [
            ("éš”å¤œ", "midnight"), ("ç•¶æ—¥", 60), ("60", 30), ("30", 20), ("20", 10),
            ("10", 9), ("9", 8), ("8", 7), ("7", 6), ("6", 5),
            ("5", 4), ("4", 3), ("3", 2), ("2", 1), ("é–‹è·‘", "post"),
        ]
        _chip_cols = st.columns(len(_chip_defs))
        for (_clbl, _cval), _ccol in zip(_chip_defs, _chip_cols):
            with _ccol:
                if st.button(_clbl, key=f"chip_{_clbl}", use_container_width=True):
                    _target_idx = None
                    # ç”¨ ACTIVE_POST_TIMEï¼ˆï¼REPLAY æ€å—°å ´è‡ªå·±å˜…é–‹è·‘æ™‚é–“ï¼‰ï¼Œ
                    # å””å†è®€ S["post_time"]ï¼ˆå—°å€‹å¯èƒ½ä¿‚ä¸Šé¢é¸å–®å¦ä¸€å ´ï¼‰ã€‚
                    _pt = ACTIVE_POST_TIME
                    if _cval == "post":
                        # è·³åŽ»æœ€æŽ¥è¿‘ã€Œé–‹è·‘æ™‚é–“ã€å—°å€‹è¨˜éŒ„é»žï¼Œå””å¯ä»¥ç›²è·³æœ€å¾Œä¸€å€‹
                        # â€”â€” recorder æœ‰æ™‚é–‹è·‘å¾Œä»²æœƒç¹¼çºŒéŒ„ï¼ˆå¯¦æ¸¬ç¬¬1å ´æœ€å¾Œä¸€å€‹
                        # è¨˜éŒ„é»žä¿‚ 23:54ï¼Œå³é–‹è·‘å¾Œæˆ 5 å€‹é˜ï¼‰ã€‚
                        if _pt is not None:
                            _target_idx = _nearest_snap_idx(replay_snaps, _pt.timestamp())
                        else:
                            _target_idx = _n_snaps - 1
                    elif _pt is not None:
                        if _cval == "midnight":
                            _mts = datetime(_pt.year, _pt.month, _pt.day, 0, 0, 0,
                                            tzinfo=HKT).timestamp()
                            _target_idx = _nearest_snap_idx(replay_snaps, _mts)
                        else:
                            _tts = _pt.timestamp() - _cval * 60
                            _target_idx = _nearest_snap_idx(replay_snaps, _tts)
                    if _target_idx is not None:
                        st.session_state["replay_idx_slider"] = _target_idx
                        st.rerun()
                    else:
                        st.warning("å‘¢å ´å†‡é–‹è·‘æ™‚é–“è¨˜éŒ„ï¼Œè·³å””åˆ°ï¼›å¯ä»¥ç”¨è½é¢å€‹æ»‘æ¡¿å¾®èª¿ã€‚")

        def _replay_lbl(i):
            s = replay_snaps[i]
            # ç”¨ ACTIVE_POST_TIMEï¼ˆå–çœ¾æ•¸ï¼‰ï¼Œå””ç”¨ snapshot è‡ªå·±å—°å€‹ post_time
            # â€”â€” ç¬¬1å ´å…¥é¢æœ‰ 15 å€‹å¯«ä½ 19:00ã€2 å€‹å¯«ä½ 19:40ï¼Œç”¨ä½¢å“‹æœƒä»¤æ¨™ç±¤
            # æ‹‰åŽ»å””åŒé»žå°±è·³åšŸè·³åŽ»ï¼ˆå¯¦æ¸¬é¡¯ç¤ºæˆã€Œé–‹è·‘å‰70åˆ†ã€ï¼‰ã€‚
            if ACTIVE_POST_TIME is not None:
                _mtp = (s["ts"] - ACTIVE_POST_TIME.timestamp()) / 60.0
                return f"é–‹è·‘å‰ {abs(_mtp):.0f} åˆ†" if _mtp < 0 else f"é–‹è·‘å¾Œ {_mtp:.0f} åˆ†"
            return datetime.fromtimestamp(s["ts"], HKT).strftime("%H:%M:%S")

        replay_idx = st.slider("æ™‚é–“è»¸ï¼ˆæ‹‰åŽ»ä»»ä½•ä¸€åˆ»ï¼Œå¾®èª¿ï¼‰", 0, _n_snaps - 1, key="replay_idx_slider")
        st.caption(f"æ™‚é–“é»žï¼š{_replay_lbl(replay_idx)}ã€€ï¼ˆå…± {_n_snaps} å€‹è¨˜éŒ„é»žï¼Œæ¯ 30 ç§’ä¸€å€‹ï¼‰")

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        stake_bar_chart_v(df[df["æ± "] == "WIN"], "ç¨è´", win_inv, S,
                          sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp)
    with bcol2:
        stake_bar_chart_v(df[df["æ± "] == "PLA"], "ä½ç½®", pla_inv, S,
                          sort_by=sort_key, m1=_m1, m2=_m2, m3=_m3, mtp=mtp)

    # â•â•â• â‘¤ è½æ³¨é‡‘é¡è¡¨ï¼ˆç¨è´ / ä½ç½®ï¼‰â•â•â•
    # çµ±ä¸€ç”¨ ACTIVE è®Šæ•¸ï¼šç‡é‚Šå ´ï¼ˆACTIVE_RACE_KEYï¼‰ã€ç‡é‚Šä¸€åˆ»ï¼ˆACTIVE_NOW_TSï¼‰ã€‚
    # LIVE åŒ REPLAY è¡ŒåŒä¸€æ¢ codeï¼Œå””å†åˆ†é–‹å…©å¥—ã€‚
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="WIN", m1=_m1, m2=_m2, m3=_m3,
                       disk_race_key=ACTIVE_RACE_KEY, as_of_ts=ACTIVE_NOW_TS)
    minute_stake_table(df, S, win_inv, pla_inv, mtp, pool="PLA", m1=_m1, m2=_m2, m3=_m3,
                       disk_race_key=ACTIVE_RACE_KEY, as_of_ts=ACTIVE_NOW_TS)

    # â”€â”€ footer â”€â”€
    now_str = datetime.now(HKT).strftime("%H:%M:%S")
    st.markdown(
        f'<div style="text-align:center;margin-top:1rem;padding:8px;border-top:1px solid var(--border);'
        f'font-family:JetBrains Mono,monospace;font-size:10px;color:var(--muted)">'
        f'{APP_NAME} {APP_VERSION} Â· æ¯ 5 ç§’è‡ªå‹•æ›´æ–° Â· {now_str} HKT</div>',
        unsafe_allow_html=True)
