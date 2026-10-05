#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
postrace_writer.py  —  獨立補寫 postrace.json（唔郁 recorder）

用途：8502「今日場地偏差」要讀 recorder 寫嘅 postrace.json，但 VPS 跑緊嘅舊
recorder 冇寫。呢支係獨立程式，用 V19-R2 一模一樣嘅邏輯去 HKJC 賽果頁攞名次，
計跑法/偏差，寫入每場資料夾嘅 postrace.json。格式同 8502 today_bias 讀嗰個一致。

- 只會：GET HKJC 賽果頁（公開）＋ 寫 <DATA_DIR>/<date>__<venue>__<no>/postrace.json
- 唔會：掂 recorder 程式、唔會改任何現有 snapshot 檔
- 冪等：已經 completed 嘅場會跳過，唔會重覆打 HKJC

用法：
    python3 postrace_writer.py                # 補今日（HKT）所有已錄場次
    python3 postrace_writer.py 2026-10-04      # 補指定日期
    python3 postrace_writer.py 2026-10-04 ST   # 補指定日期+馬場
"""
import os, re, io, sys, json, glob, math, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
RESULTS_URL = "https://racing.hkjc.com/en-us/local/information/archive/localresults"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://bet.hkjc.com",
    "Referer": "https://bet.hkjc.com/",
    "User-Agent": "Mozilla/5.0",
}
STYLE_LABELS = ("放頭", "前置", "中置", "後上")


# ───── 以下 5 個 helper 由 recorder V19-R2 逐字搬過嚟，確保結果一致 ─────
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


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='postrace-', suffix='.tmp', dir=path.parent)
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
# ─────────────────────────────────────────────────────────────────────


def _already_done(folder):
    pj = os.path.join(folder, "postrace.json")
    if not os.path.exists(pj):
        return False
    try:
        return bool(json.load(open(pj, encoding="utf-8")).get("completed"))
    except (OSError, ValueError):
        return False


def process_day(date_str, venue_filter=None):
    pattern = os.path.join(DATA_DIR, f"{date_str}__*")
    folders = sorted(glob.glob(pattern))
    if not folders:
        print(f"[{date_str}] 冇搵到任何場次資料夾喺 {DATA_DIR}")
        return 0, 0
    wrote = skipped = 0
    for folder in folders:
        base = os.path.basename(folder)
        try:
            d, venue, rno = base.split("__")
            race_no = int(rno)
        except ValueError:
            continue
        if venue_filter and venue != venue_filter:
            continue
        if _already_done(folder):
            skipped += 1
            continue
        race_key = f"{d}|{venue}|{race_no}"
        try:
            runs = fetch_postrace_runs(d, venue, race_no)
        except Exception as e:
            print(f"  {base}: 賽果未出／讀取失敗（{type(e).__name__}）— 跳過，下次再試")
            continue
        meta = {"date": d, "venue": venue, "race_no": race_no,
                "field_size": len(runs)}
        result = build_postrace_analysis(race_key, meta, runs)
        atomic_json(Path(folder) / "postrace.json", result)
        if result.get("completed"):
            wrote += 1
            done_styles = ", ".join(f"{k}:{v['n']}場n" for k, v in result["style_stats"].items())
            print(f"  {base}: ✅ 寫咗 postrace.json（{result['bias_label']}；{done_styles}）")
        else:
            print(f"  {base}: 寫咗但未有名次（completed=false）")
    print(f"[{date_str}] 完成：新寫 {wrote} 場、已有跳過 {skipped} 場")
    return wrote, skipped


def main():
    args = sys.argv[1:]
    date_str = args[0] if len(args) >= 1 else datetime.now(HKT).date().isoformat()
    venue_filter = args[1] if len(args) >= 2 else None
    print(f"postrace_writer：DATA_DIR={DATA_DIR}；日期={date_str}"
          + (f"；馬場={venue_filter}" if venue_filter else ""))
    process_day(date_str, venue_filter)


if __name__ == "__main__":
    main()
