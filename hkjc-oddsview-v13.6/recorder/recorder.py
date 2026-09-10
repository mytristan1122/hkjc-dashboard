# -*- coding: utf-8 -*-
"""
HKJC 背景記錄引擎 (recorder.py)
──────────────────────────────────────────────────────────
獨立程式，唔靠 Streamlit，24 小時喺 VPS 跑。
專責：掃描賽馬日 + 開賣狀態 → 一開賣自動記錄所有場次 →
      每 30 秒寫 snapshot 落 /root/hkjc_data（同 dashboard 同格式）。

dashboard 只負責顯示 + 翻睇，記錄全部交俾呢個程式。

用法（VPS）：
    python3 recorder.py            # 前台跑（測試用）
    （正式用 systemd service，24 小時背景跑）

環境變數：
    HKJC_DATA_DIR   數據目錄（預設 ~/hkjc_data，同 dashboard 一致）
    HKJC_SCAN_SEC   掃描間隔秒（預設 30）
"""
import os
import sys
import json
import time
import glob
import traceback
from datetime import datetime, timezone, timedelta

import requests

# ══════════════════════════════════════════════════════════
#  CONFIG（同 dashboard 一致）
# ══════════════════════════════════════════════════════════
API = "https://info.cld.hkjc.com/graphql/base/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": "https://bet.hkjc.com/",
    "User-Agent": "Mozilla/5.0",
}
HKT = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
SCAN_SEC = int(os.environ.get("HKJC_SCAN_SEC", "30"))   # 每隔幾耐掃描+記錄一次

# ── 賠率 query（逐字，同 dashboard）──
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

# ── turnover + 賽期 query（逐字官方，含 activeMeetings 列出所有賽馬日）──
TURNOVER_QUERY = "fragment raceFragment on Race {\n  id\n  no\n  status\n  raceName_en\n  raceName_ch\n  postTime\n  country_en\n  country_ch\n  distance\n  wageringFieldSize\n  go_en\n  go_ch\n  ratingType\n  raceTrack {\n    description_en\n    description_ch\n  }\n  raceCourse {\n    description_en\n    description_ch\n    displayCode\n  }\n  claCode\n  raceClass_en\n  raceClass_ch\n  judgeSigns {\n    value_en\n  }\n}\n\nfragment racingBlockFragment on RaceMeeting {\n  jpEsts: pmPools(\n    oddsTypes: [WIN, PLA, TCE, TRI, FF, QTT, DT, TT, SixUP]\n    filters: [\"jackpot\", \"estimatedDividend\"]\n  ) {\n    leg {\n      number\n      races\n    }\n    oddsType\n    jackpot\n    estimatedDividend\n    mergedPoolId\n  }\n  poolInvs: pmPools(\n    oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n  ) {\n    id\n    leg {\n      races\n    }\n  }\n  penetrometerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  hammerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  changeHistories(filters: [\"top3\"]) {\n    type\n    time\n    raceNo\n    runnerNo\n    horseName_ch\n    horseName_en\n    jockeyName_ch\n    jockeyName_en\n    scratchHorseName_ch\n    scratchHorseName_en\n    handicapWeight\n    scrResvIndicator\n  }\n}\n\nquery raceMeetings($date: String, $venueCode: String) {\n  timeOffset {\n    rc\n  }\n  activeMeetings: raceMeetings {\n    id\n    venueCode\n    date\n    status\n    races {\n      no\n      postTime\n      status\n      wageringFieldSize\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      status\n    }\n  }\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    id\n    status\n    venueCode\n    date\n    totalNumberOfRace\n    currentNumberOfRace\n    dateOfWeek\n    meetingType\n    totalInvestment\n    country {\n      code\n      namech\n      nameen\n      seq\n    }\n    races {\n      ...raceFragment\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n    obSt: pmPools(oddsTypes: [WIN, PLA]) {\n      leg {\n        races\n      }\n      oddsType\n      comingleStatus\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      id\n      leg {\n        number\n        races\n      }\n      status\n      sellStatus\n      oddsType\n      investment\n      mergedPoolId\n      lastUpdateTime\n    }\n    ...racingBlockFragment\n    pmPools(oddsTypes: []) {\n      id\n    }\n    jkcInstNo: foPools(oddsTypes: [JKC], filters: [\"top\"]) {\n      instNo\n    }\n    tncInstNo: foPools(oddsTypes: [TNC], filters: [\"top\"]) {\n      instNo\n    }\n  }\n}"


# ══════════════════════════════════════════════════════════
#  小工具
# ══════════════════════════════════════════════════════════
def log(msg):
    ts = datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def gql(query, variables, op_name):
    payload = {"operationName": op_name, "variables": variables, "query": query}
    r = requests.post(API, headers=HEADERS, json=payload, timeout=25)
    return r.json()

# ── storage（同 dashboard 一致：/root/hkjc_data/<date__venue__race>/<ts>.json）──
def _race_dir(race_key):
    safe = race_key.replace("|", "__")
    return os.path.join(DATA_DIR, safe)

def save_snapshot(race_key, snapshot):
    try:
        rd = _race_dir(race_key)
        os.makedirs(rd, exist_ok=True)
        ts = int(snapshot.get("ts", datetime.now(HKT).timestamp()))
        with open(os.path.join(rd, f"{ts}.json"), "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"save_snapshot error: {e}")
        return False


# ══════════════════════════════════════════════════════════
#  馬會數據
# ══════════════════════════════════════════════════════════
def find_active_meetings():
    """用 activeMeetings 搵所有『有賽事』嘅賽馬日+場地。
    Returns list of dicts: {date, venueCode, status}."""
    try:
        j = gql(TURNOVER_QUERY, {"date": None, "venueCode": None}, "raceMeetings")
        if isinstance(j, dict) and j.get("errors"):
            log(f"activeMeetings query errors: {j['errors']}")
            return []
        ams = (j.get("data", {}) or {}).get("activeMeetings", []) or []
        out = []
        for m in ams:
            d = m.get("date")
            v = m.get("venueCode")
            if d and v:
                out.append({"date": d[:10], "venueCode": v, "status": m.get("status"),
                            "races": m.get("races") or []})
        return out
    except Exception as e:
        log(f"find_active_meetings error: {e}")
        return []

def fetch_meeting_full(date_str, venue):
    """一 call 攞成個賽馬日：每場 postTime/status、每池 investment/sellStatus。"""
    try:
        j = gql(TURNOVER_QUERY, {"date": date_str, "venueCode": venue}, "raceMeetings")
        if isinstance(j, dict) and j.get("errors"):
            return None
        meetings = (j.get("data", {}) or {}).get("raceMeetings", []) or []
        return meetings[0] if meetings else None
    except Exception as e:
        log(f"fetch_meeting_full error: {e}")
        return None

def fetch_odds(date_str, venue, race_no, odds_types):
    """攞某場某啲池嘅賠率 oddsNodes。"""
    try:
        j = gql(RACING_QUERY, {"date": date_str, "venueCode": venue,
                               "raceNo": race_no, "oddsTypes": odds_types}, "racing")
        if isinstance(j, dict) and j.get("errors"):
            return []
        meetings = (j.get("data", {}) or {}).get("raceMeetings", []) or []
        for m in meetings:
            if m.get("pmPools"):
                return m["pmPools"]
    except Exception as e:
        log(f"fetch_odds error r{race_no}: {e}")
    return []


# ══════════════════════════════════════════════════════════
#  組裝 snapshot（同 dashboard 格式一致）
# ══════════════════════════════════════════════════════════
def build_snapshots_for_meeting(meeting):
    """對一個賽馬日，將所有『開賣中』嘅場次各自組一個 snapshot 並寫硬碟。
    Returns 記錄咗嘅場數。"""
    if not meeting:
        return 0
    date_str = (meeting.get("date") or "")[:10]
    venue = meeting.get("venueCode")
    if not date_str or not venue:
        return 0

    # per-race postTime + status
    race_meta = {}
    for rc in (meeting.get("races") or []):
        no = rc.get("no")
        if no is not None:
            race_meta[int(no)] = {"post": rc.get("postTime"), "status": rc.get("status")}

    # per-race pool investment + sellStatus (WIN/PLA/QIN/QPL)
    pool_inv = {}      # race_no -> {WIN:.., PLA:.., QIN:.., QPL:..}
    pool_selling = {}  # race_no -> True/False (WIN pool selling)
    for p in (meeting.get("poolInvs") or []):
        otype = p.get("oddsType")
        if otype not in ("WIN", "PLA", "QIN", "QPL"):
            continue
        inv = _to_float(p.get("investment"))
        sell = p.get("sellStatus")
        races = (p.get("leg") or {}).get("races") or []
        for rno in races:
            rno = int(rno)
            pool_inv.setdefault(rno, {})[otype] = inv
            if otype == "WIN":
                pool_selling[rno] = (sell == "START_SELL")

    now_ts = datetime.now(HKT).timestamp()
    recorded = 0

    for rno, selling in pool_selling.items():
        if not selling:
            continue  # 淨係記開賣中嘅場
        # 攞 WIN/PLA 賠率
        win_pla = fetch_odds(date_str, venue, rno, ["WIN", "PLA"])
        win_odds, pla_odds = {}, {}
        for pool in win_pla:
            ot = pool.get("oddsType")
            for n in pool.get("oddsNodes", []):
                h = n.get("combString")
                o = _to_float(n.get("oddsValue"))
                if o <= 0 or not h:
                    continue
                if ot == "WIN":
                    win_odds[str(h)] = o
                elif ot == "PLA":
                    pla_odds[str(h)] = o
        if not win_odds:
            continue  # 未有賠率就跳過（開賣初期）
        # 攞 QIN/QPL 組合
        combo = fetch_odds(date_str, venue, rno, ["QIN", "QPL"])
        qin, qpl = {}, {}
        for pool in combo:
            ot = pool.get("oddsType")
            for n in pool.get("oddsNodes", []):
                comb = (n.get("combString") or "").replace("-", ",")
                o = _to_float(n.get("oddsValue"))
                if o <= 0 or "," not in comb:
                    continue
                parts = comb.split(",")
                if len(parts) != 2:
                    continue
                try:
                    a, b = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                if a > b:
                    a, b = b, a
                key = f"{a},{b}"
                if ot == "QIN":
                    qin[key] = o
                elif ot == "QPL":
                    qpl[key] = o

        meta = race_meta.get(rno, {})
        post_iso = meta.get("post")
        post_ts = None
        if post_iso:
            try:
                dt = datetime.fromisoformat(post_iso.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=HKT)
                post_ts = dt.astimezone(HKT).timestamp()
            except Exception:
                post_ts = None

        pinv = pool_inv.get(rno, {})
        race_key = f"{date_str}|{venue}|{rno}"
        snap = {
            "ts": now_ts,
            "race_key": race_key,
            "post_time": post_ts,
            "win": win_odds,
            "pla": pla_odds,
            "pool": {"WIN": pinv.get("WIN"), "PLA": pinv.get("PLA"),
                     "QIN": pinv.get("QIN"), "QPL": pinv.get("QPL")},
            "qin": qin,
            "qpl": qpl,
        }
        save_snapshot(race_key, snap)
        recorded += 1

    return recorded


# ══════════════════════════════════════════════════════════
#  主迴圈
# ══════════════════════════════════════════════════════════
def main():
    log("=" * 50)
    log("HKJC 背景記錄引擎啟動")
    log(f"數據目錄：{DATA_DIR}")
    log(f"掃描間隔：{SCAN_SEC} 秒")
    log("=" * 50)
    os.makedirs(DATA_DIR, exist_ok=True)

    while True:
        cycle_start = time.time()
        try:
            meetings = find_active_meetings()
            if not meetings:
                log("冇 active meetings（唔係賽馬日或未開賣）")
            else:
                total_recorded = 0
                seen = set()
                for am in meetings:
                    key = (am["date"], am["venueCode"])
                    if key in seen:
                        continue
                    seen.add(key)
                    full = fetch_meeting_full(am["date"], am["venueCode"])
                    n = build_snapshots_for_meeting(full)
                    if n:
                        log(f"{am['date']} {am['venueCode']}：記錄咗 {n} 場（開賣中）")
                        total_recorded += n
                if total_recorded == 0:
                    log("有賽馬日但暫時冇場開賣（或未有賠率）")
        except Exception as e:
            log(f"主迴圈錯誤：{e}")
            log(traceback.format_exc())

        # 掃描間隔（扣返今次用咗嘅時間）
        elapsed = time.time() - cycle_start
        sleep_s = max(1, SCAN_SEC - elapsed)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
