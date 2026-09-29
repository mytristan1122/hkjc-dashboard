# V19-R2 Recorder: backward-compatible JSON, five-second target, saved racecards.
import math
import tempfile
import fcntl
import hashlib
import io
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os

import sys

import json

import time

import glob

import traceback

from datetime import datetime, timezone, timedelta

import requests
import pandas as pd

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

RESULTS_URL = "https://racing.hkjc.com/en-us/local/information/archive/localresults"
STYLE_LABELS = ("放頭", "前置", "中置", "後上")


def classify_running_style(value, field_size):
    positions = [int(x) for x in re.findall(r"\d+", str(value or "")) if int(x) > 0]
    try:
        field = max(1, int(field_size or max(positions or [1])))
    except (TypeError, ValueError):
        field = max(1, max(positions or [1]))
    if not positions:
        return "未知"
    early = positions[0]
    lead_cut = max(1, math.ceil(field * 0.25))
    front_cut = max(2, math.ceil(field * 0.50))
    back_cut = max(front_cut + 1, math.ceil(field * 0.75))
    if early <= lead_cut:
        return "放頭"
    if early <= front_cut:
        return "前置"
    if early >= back_cut:
        return "後上"
    return "中置"


def build_postrace_analysis(race_key, race_meta, runs, previous=None):
    previous = previous or {}
    field = max(1, int(race_meta.get("field_size") or len(runs) or 1))
    enriched = []
    for row in runs:
        item = dict(row)
        item["horse_no"] = int(item.get("horse_no") or 0)
        item["field_size"] = field
        item["running_style"] = classify_running_style(item.get("running_position"), field)
        try:
            item["finishing_position"] = int(item["finishing_position"])
        except (TypeError, ValueError, KeyError):
            item["finishing_position"] = None
        enriched.append(item)
    style_history = {str(k): list(v) for k, v in (previous.get("style_history") or {}).items()}
    for item in enriched:
        style = item["running_style"]
        if style in STYLE_LABELS:
            key = str(item.get("horse_id") or item["horse_no"])
            style_history.setdefault(key, []).append(style)
            style_history[key] = style_history[key][-12:]
    style_stats, draw_stats = {}, {}
    for item in enriched:
        pos = item.get("finishing_position")
        if pos is None:
            continue
        style = item["running_style"]
        stat = style_stats.setdefault(style, {"n": 0, "top3": 0})
        stat["n"] += 1
        stat["top3"] += int(pos <= 3)
        draw = item.get("draw")
        if draw:
            stat = draw_stats.setdefault(str(draw), {"n": 0, "top3": 0})
            stat["n"] += 1
            stat["top3"] += int(pos <= 3)
    baseline = min(3.0 / field, 1.0)
    for stats in (style_stats, draw_stats):
        for stat in stats.values():
            rate = stat["top3"] / stat["n"] if stat["n"] else 0.0
            stat.update(top3_rate=rate, lift=rate / baseline if baseline else 1.0,
                        reliable=stat["n"] >= 4)
    reliable = {k: v for k, v in style_stats.items() if v["reliable"]}
    bias = ("利" + max(reliable, key=lambda k: reliable[k]["lift"])) if reliable else "樣本不足"
    valid = [r for r in enriched if r.get("finishing_position") is not None]
    return {"schema_version": 1, "race_key": race_key, "race_meta": race_meta,
            "runs": enriched, "style_history": style_history,
            "style_stats": style_stats, "draw_stats": draw_stats,
            "bias_label": bias, "completed": bool(valid)}

SCAN_SEC = 5  # Fixed requested cadence; legacy HKJC_SCAN_SEC=30 must not override it.

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

TURNOVER_QUERY = "fragment raceFragment on Race {\n  id\n  no\n  status\n  raceName_en\n  raceName_ch\n  postTime\n  country_en\n  country_ch\n  distance\n  wageringFieldSize\n  go_en\n  go_ch\n  ratingType\n  raceTrack {\n    description_en\n    description_ch\n  }\n  raceCourse {\n    description_en\n    description_ch\n    displayCode\n  }\n  claCode\n  raceClass_en\n  raceClass_ch\n  judgeSigns {\n    value_en\n  }\n}\n\nfragment racingBlockFragment on RaceMeeting {\n  jpEsts: pmPools(\n    oddsTypes: [WIN, PLA, TCE, TRI, FF, QTT, DT, TT, SixUP]\n    filters: [\"jackpot\", \"estimatedDividend\"]\n  ) {\n    leg {\n      number\n      races\n    }\n    oddsType\n    jackpot\n    estimatedDividend\n    mergedPoolId\n  }\n  poolInvs: pmPools(\n    oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n  ) {\n    id\n    leg {\n      races\n    }\n  }\n  penetrometerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  hammerReadings(filters: [\"first\"]) {\n    reading\n    readingTime\n  }\n  changeHistories(filters: [\"top3\"]) {\n    type\n    time\n    raceNo\n    runnerNo\n    horseName_ch\n    horseName_en\n    jockeyName_ch\n    jockeyName_en\n    scratchHorseName_ch\n    scratchHorseName_en\n    handicapWeight\n    scrResvIndicator\n  }\n}\n\nquery raceMeetings($date: String, $venueCode: String) {\n  timeOffset {\n    rc\n  }\n  activeMeetings: raceMeetings {\n    id\n    venueCode\n    date\n    status\n    races {\n      no\n      postTime\n      status\n      wageringFieldSize\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      status\n    }\n  }\n  raceMeetings(date: $date, venueCode: $venueCode) {\n    id\n    status\n    venueCode\n    date\n    totalNumberOfRace\n    currentNumberOfRace\n    dateOfWeek\n    meetingType\n    totalInvestment\n    country {\n      code\n      namech\n      nameen\n      seq\n    }\n    races {\n      ...raceFragment\n      runners {\n        id\n        no\n        standbyNo\n        status\n        name_ch\n        name_en\n        horse {\n          id\n          code\n        }\n        color\n        barrierDrawNumber\n        handicapWeight\n        currentWeight\n        currentRating\n        internationalRating\n        gearInfo\n        racingColorFileName\n        allowance\n        trainerPreference\n        last6run\n        saddleClothNo\n        trumpCard\n        priority\n        finalPosition\n        deadHeat\n        winOdds\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n      }\n    }\n    obSt: pmPools(oddsTypes: [WIN, PLA]) {\n      leg {\n        races\n      }\n      oddsType\n      comingleStatus\n    }\n    poolInvs: pmPools(\n      oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]\n    ) {\n      id\n      leg {\n        number\n        races\n      }\n      status\n      sellStatus\n      oddsType\n      investment\n      mergedPoolId\n      lastUpdateTime\n    }\n    ...racingBlockFragment\n    pmPools(oddsTypes: []) {\n      id\n    }\n    jkcInstNo: foPools(oddsTypes: [JKC], filters: [\"top\"]) {\n      instNo\n    }\n    tncInstNo: foPools(oddsTypes: [TNC], filters: [\"top\"]) {\n      instNo\n    }\n  }\n}"

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
    r.raise_for_status()
    return r.json()

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

def matching_meeting(meetings, date_str, venue):
    return next((m for m in meetings or []
                 if str(m.get('date') or '')[:10] == str(date_str)[:10]
                 and str(m.get('venueCode') or '').upper() == str(venue).upper()), None)

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

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='record-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _result_col(table, *names):
    cols = {str(c).strip().lower().rstrip('.'): c for c in table.columns}
    for name in names:
        n = name.lower().rstrip('.')
        for c, norm in cols.items():
            if norm == n or norm.startswith(n):
                return cols[c]
    return None


def _result_int(value):
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) if m else None


def fetch_postrace_runs(date_str, venue, race_no):
    """Fetch all runners and HKJC Running Position after a race finishes."""
    params = {'racedate': str(date_str).replace('-', '/'), 'racecourse': venue,
              'RaceNo': int(race_no)}
    response = requests.get(RESULTS_URL, params=params, headers=HEADERS, timeout=25)
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))
    table = None
    for candidate in tables:
        cols = {str(c).strip().lower().rstrip('.') for c in candidate.columns}
        if len({'pla', 'horse no', 'horse', 'jockey'} & cols) >= 3:
            table = candidate.copy()
            break
    if table is None or table.empty:
        raise RuntimeError('賽果頁未找到完整賽果表')
    table_html = next((m.group(0) for m in re.finditer(r"<table\b.*?</table>", response.text, re.I | re.S)
                       if "horse no" in m.group(0).lower() and "jockey" in m.group(0).lower()), "")
    row_ids = []
    if table_html:
        for tr in re.findall(r"<tr\b.*?</tr>", table_html, re.I | re.S):
            m = re.search(r"horseid=([A-Za-z0-9_]+)", tr, re.I)
            if m:
                row_ids.append(m.group(1))
    c_no = _result_col(table, 'horse no')
    c_pla = _result_col(table, 'pla')
    c_horse = _result_col(table, 'horse')
    c_run = _result_col(table, 'running position', 'running')
    c_draw = _result_col(table, 'dr', 'draw')
    runs = []
    for i, (_, row) in enumerate(table.iterrows()):
        no = _result_int(row.get(c_no))
        if no is None:
            continue
        runs.append({'horse_no': no, 'horse_name': str(row.get(c_horse) or no),
                     'horse_id': row_ids[i] if len(row_ids) == len(table) and i < len(row_ids) else None,
                     'finishing_position': _result_int(row.get(c_pla)),
                     'running_position': str(row.get(c_run) or ''),
                     'draw': _result_int(row.get(c_draw))})
    if not runs:
        raise RuntimeError('賽果頁沒有參賽馬資料')
    return runs


def save_postrace_analysis(race_key, meeting, race_no, previous=None):
    date_str = str(meeting.get('date') or '')[:10]
    venue = meeting.get('venueCode') or race_key.split('|')[1]
    runs = fetch_postrace_runs(date_str, venue, race_no)
    race = next((r for r in meeting.get('races') or [] if int(r.get('no')) == int(race_no)), {})
    meta = {'date': date_str, 'venue': venue, 'race_no': int(race_no),
            'field_size': int(race.get('wageringFieldSize') or len(runs)),
            'distance': race.get('distance'), 'going': race.get('go_ch'),
            'track': (race.get('raceTrack') or {}).get('description_ch'),
            'course': (race.get('raceCourse') or {}).get('displayCode')}
    result = build_postrace_analysis(race_key, meta, runs, previous=previous)
    atomic_json(Path(DATA_DIR) / race_key.replace('|', '__') / 'postrace.json', result)
    return result


def atomic_snapshot(race_key, snapshot):
    directory = Path(DATA_DIR) / race_key.replace('|', '__')
    stored = dict(snapshot)
    info = stored.pop('race_info', None)
    if info is not None:
        encoded = json.dumps(info, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
        digest = hashlib.sha256(encoded).hexdigest()
        card_path = directory / '.cards' / (digest + '.card')
        if not card_path.exists():
            atomic_json(card_path, info)
        stored['race_info_ref'] = digest
    atomic_json(directory / f"{int(snapshot['ts'])}.json", stored)


def fetch_odds_checked(date_str, venue, race_no):
    body = gql(RACING_QUERY, {'date': date_str, 'venueCode': venue, 'raceNo': race_no,
                              'oddsTypes': ['WIN', 'PLA', 'QIN', 'QPL']}, 'racing')
    if body.get('errors'):
        raise RuntimeError(f"賠率API錯誤 r{race_no}: {body['errors']}")
    meetings = (body.get('data') or {}).get('raceMeetings') or []
    if len(meetings) != 1:
        raise RuntimeError(f'賠率API回應異常 r{race_no}')
    return meetings[0].get('pmPools') or []


def record_one(meeting, race_no, meeting_ts):
    pools = fetch_odds_checked(str(meeting['date'])[:10], meeting['venueCode'], race_no)
    captured = datetime.now(HKT).timestamp()
    snap = snapshot_from_pools(meeting, race_no, pools, captured, meeting_ts)
    if snap is None:
        return False
    snap['odds_captured_ts'] = captured
    atomic_snapshot(snap['race_key'], snap)
    return True


def record_meeting(meeting, executor, closing_done, results_done, results_last_try):
    meeting_ts = datetime.now(HKT).timestamp()
    selling, metadata = {}, {}
    for rc in meeting.get('races') or []:
        metadata[int(rc['no'])] = rc
    for p in meeting.get('poolInvs') or []:
        if p.get('oddsType') != 'WIN':
            continue
        for no in (p.get('leg') or {}).get('races') or []:
            selling[int(no)] = p.get('sellStatus') == 'START_SELL'
    jobs = []
    for no, is_selling in selling.items():
        key = f"{str(meeting['date'])[:10]}|{meeting['venueCode']}|{no}"
        # Capture a final sample after betting closes, without polling it forever.
        if not is_selling and key not in closing_done:
            continue
        if not is_selling and closing_done.get(key) and not results_done.get(key):
            continue
        if not is_selling and closing_done.get(key) and results_done.get(key):
            continue
        jobs.append((no, key, is_selling, executor.submit(record_one, meeting, no, meeting_ts)))
    recorded = 0
    for no, key, selling_now, future in jobs:
        try:
            if future.result():
                recorded += 1
                closing_done[key] = not selling_now
                # Once betting has closed, keep retrying the official result
                # page at most once per minute until all runners are available.
                if not selling_now and not results_done.get(key):
                    now = time.time()
                    if now - results_last_try.get(key, 0.0) >= 60:
                        results_last_try[key] = now
                        try:
                            previous = None
                            result = save_postrace_analysis(key, meeting, no, previous=previous)
                            if result.get('completed'):
                                results_done[key] = True
                                log(f'{key} 賽後跑法／場地偏差已保存（{len(result.get("runs") or [])}匹）')
                        except Exception as result_exc:
                            log(f'{key} 賽後分析稍後重試：{result_exc}')
        except Exception as exc:
            log(f'{key} 記錄失敗：{exc}')
    # Retry result pages independently of the final betting snapshot.
    now = time.time()
    for rc in meeting.get('races') or []:
        no = int(rc.get('no'))
        key = f"{str(meeting['date'])[:10]}|{meeting['venueCode']}|{no}"
        if not closing_done.get(key) or results_done.get(key):
            continue
        if now - results_last_try.get(key, 0.0) < 60:
            continue
        results_last_try[key] = now
        try:
            result = save_postrace_analysis(key, meeting, no)
            if result.get('completed'):
                results_done[key] = True
                log(f'{key} 賽後跑法／場地偏差已保存（{len(result.get("runs") or [])}匹）')
        except Exception as exc:
            log(f'{key} 賽後分析稍後重試：{exc}')
    return recorded


def main():
    log(f'V19-R2 Recorder 啟動；目錄={DATA_DIR}；目標間隔={SCAN_SEC}秒')
    os.makedirs(DATA_DIR, exist_ok=True)
    # flock prevents accidentally starting two copies of this recorder.
    lock = open(os.path.join(DATA_DIR, '.recorder-v19.lock'), 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('另一個新版 Recorder 已在運行；停止重複啟動。')
    closing_done = {}
    results_done = {}
    results_last_try = {}
    active = []
    last_discovery = 0.0
    with ThreadPoolExecutor(max_workers=4) as executor:
        while True:
            started = time.monotonic()
            try:
                if started - last_discovery >= 60 or not active:
                    active = find_active_meetings()
                    last_discovery = started
                count = 0
                for am in active:
                    date_str, venue = am['date'], am['venueCode']
                    body = gql(TURNOVER_QUERY, {'date': date_str, 'venueCode': venue}, 'raceMeetings')
                    if body.get('errors'):
                        log(f'{date_str} {venue}：賽日API錯誤')
                        continue
                    meeting = matching_meeting((body.get('data') or {}).get('raceMeetings'), date_str, venue)
                    if meeting is None:
                        log(f'{date_str} {venue}：API無相符日期／馬場，略過')
                        continue
                    count += record_meeting(meeting, executor, closing_done, results_done, results_last_try)
                elapsed = time.monotonic() - started
                log(f'寫入{count}場；本輪{elapsed:.2f}秒；目標{SCAN_SEC}秒' + ('（本輪超時）' if elapsed > SCAN_SEC else ''))
            except Exception:
                log(traceback.format_exc())
            time.sleep(max(0.1, SCAN_SEC - (time.monotonic() - started)))


if __name__ == '__main__':
    main()
