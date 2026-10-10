#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
postrace_writer.py  —  獨立補寫 postrace.json（唔郁 recorder）  [V3 · lxml parser + 防 stale 賽果頁]

8502「今日場地偏差」要讀 recorder 寫嘅 postrace.json。VPS 跑緊舊 recorder 冇寫，
而新 recorder 原本嗰段 `requests + pd.read_html` 喺而家 HKJC 賽果頁解析唔到
（HKJC 用巢狀 <table> + <thead>，pandas 靠欄位名認表唔穩陣、跨版本會爛）。

本程式改用 lxml 按「表頭文字」直接搵個賽果表、按欄位抽名次 / 檔位 / Running Position，
行為穩定、唔受 pandas 版本影響。格式同 8502 today_bias 讀嗰個 postrace.json 一致。

- 只會：GET HKJC 本地賽果頁（公開）＋ 寫 <DATA_DIR>/<date>__<venue>__<no>/postrace.json
- 唔會：掂 recorder 程式、唔會改任何現有 snapshot 檔
- V3 防 stale：HKJC 對「未出賽果」嘅日期會回最近一次賽事嘅賽果頁；
  寫檔前核對頁面「Race Meeting」日期 == 要求日期，對唔上或者讀唔到就唔寫（寧缺勿錯）
- 冪等：已 completed 嘅場會跳過；只處理香港本地場 ST / HV（海外 S1/S4… 自動略過）

用法：
    python3 postrace_writer.py                # 補今日（HKT）
    python3 postrace_writer.py 2026-10-04      # 補指定日期
    python3 postrace_writer.py 2026-10-04 ST   # 補指定日期+馬場
"""
import os, re, sys, json, glob, math, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
import lxml.html   # 若未裝：  .venv/bin/pip install lxml

HKT = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("HKJC_DATA_DIR", os.path.join(os.path.expanduser("~"), "hkjc_data"))
LOCAL_VENUES = ("ST", "HV")           # 只有香港本地場先有「場地偏差」意義
RESULTS_URL = "https://racing.hkjc.com/en-us/local/information/localresults"
RESULTS_HEADERS = {                   # 簡單瀏覽器 header（唔好用 GraphQL 嗰套 application/json）
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
STYLE_LABELS = ("放頭", "前置", "中置", "後上")


def _int(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else None


def classify_running_style(value, field_size):   # 同 recorder V19-R2 一致
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


def fetch_postrace_runs(date_str, venue, race_no):
    """用 lxml 解析 HKJC 本地賽果頁：按表頭文字搵個賽果表，再逐行抽
       名次 / 馬號 / Running Position / 檔位。回傳 list[dict]。"""
    params = {'racedate': str(date_str).replace('-', '/'), 'racecourse': venue, 'RaceNo': int(race_no)}
    r = requests.get(RESULTS_URL, params=params, headers=RESULTS_HEADERS, timeout=25)
    r.raise_for_status()
    doc = lxml.html.fromstring(r.text)

    # V3：核對頁面賽事日期（防 HKJC 回舊賽果）
    page_txt = re.sub(r'\s+', ' ', doc.text_content())
    want = datetime.strptime(str(date_str), '%Y-%m-%d').strftime('%d/%m/%Y')
    m = re.search(r'Race\s*Meeting\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})', page_txt, re.I)
    if not m:
        raise RuntimeError('賽果頁讀唔到賽事日期（Race Meeting），為安全唔寫')
    got = datetime.strptime(m.group(1), '%d/%m/%Y').strftime('%d/%m/%Y')
    if got != want:
        raise RuntimeError(f'賽果頁日期 {got} ≠ 要求 {want}（HKJC 回咗舊賽果／賽果未出）')

    target = None
    for tb in doc.xpath('//table'):
        head = re.sub(r'\s+', '', ' '.join(tb.xpath('.//tr[1]//text()'))).lower()
        if 'horseno' in head and 'jockey' in head and 'runningposition' in head:
            target = tb
            break
    if target is None:
        raise RuntimeError('賽果頁未找到賽果表（可能賽果未出）')

    hcells = target.xpath('.//thead//td | .//thead//th') or target.xpath('.//tr[1]/td | .//tr[1]/th')
    headers = [re.sub(r'\s+', '', (c.text_content() or '')).lower() for c in hcells]

    def col(*names):
        for n in names:                              # 先精確
            for i, hh in enumerate(headers):
                if hh == n:
                    return i
        for n in names:                              # 後前綴
            for i, hh in enumerate(headers):
                if hh.startswith(n):
                    return i
        return None

    i_pla = col('pla')
    i_no = col('horseno')
    i_horse = col('horse')
    i_dr = col('dr')
    i_run = col('runningposition', 'running')

    body = target.xpath('.//tbody/tr') or target.xpath('.//tr[position()>1]')
    runs = []
    for tr in body:
        tds = tr.xpath('./td')
        if len(tds) < 6:
            continue

        def txt(i):
            return re.sub(r'\s+', ' ', tds[i].text_content()).strip() if (i is not None and i < len(tds)) else ''

        no = _int(txt(i_no))
        if no is None:
            continue
        run_nums = re.findall(r'\d+', tds[i_run].text_content()) if (i_run is not None and i_run < len(tds)) else []
        hid = None
        if i_horse is not None and i_horse < len(tds):
            hrefs = tds[i_horse].xpath('.//a/@href')
            m = re.search(r'horseid=([A-Za-z0-9_]+)', hrefs[0]) if hrefs else None
            hid = m.group(1) if m else None
        runs.append({'horse_no': no, 'horse_name': txt(i_horse) or str(no), 'horse_id': hid,
                     'finishing_position': _int(txt(i_pla)),
                     'running_position': ' '.join(run_nums), 'draw': _int(txt(i_dr))})
    if not runs:
        raise RuntimeError('賽果表冇參賽馬資料')
    return runs


def build_postrace_analysis(race_key, race_meta, runs, previous=None):   # 同 recorder V19-R2 一致
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


def _already_done(folder):
    pj = os.path.join(folder, "postrace.json")
    if not os.path.exists(pj):
        return False
    try:
        return bool(json.load(open(pj, encoding="utf-8")).get("completed"))
    except (OSError, ValueError):
        return False


def process_day(date_str, venue_filter=None):
    folders = sorted(glob.glob(os.path.join(DATA_DIR, f"{date_str}__*")))
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
        if venue not in LOCAL_VENUES:          # 海外（S1/S4…）冇場地偏差意義，略過
            continue
        if _already_done(folder):
            skipped += 1
            continue
        race_key = f"{d}|{venue}|{race_no}"
        try:
            runs = fetch_postrace_runs(d, venue, race_no)
        except Exception as e:
            print(f"  {base}: 賽果未出／讀取失敗（{type(e).__name__}: {e}）— 跳過，下次再試")
            continue
        meta = {"date": d, "venue": venue, "race_no": race_no, "field_size": len(runs)}
        result = build_postrace_analysis(race_key, meta, runs)
        atomic_json(Path(folder) / "postrace.json", result)
        if result.get("completed"):
            wrote += 1
            styles = "、".join(f"{k}{v['n']}場" for k, v in result["style_stats"].items())
            print(f"  {base}: ✅ 寫咗 postrace.json（{result['bias_label']}；{styles}）")
        else:
            print(f"  {base}: 寫咗但未有名次（completed=false）")
    print(f"[{date_str}] 完成：新寫 {wrote} 場、已有跳過 {skipped} 場")
    return wrote, skipped


def main():
    args = sys.argv[1:]
    date_str = args[0] if len(args) >= 1 else datetime.now(HKT).date().isoformat()
    venue_filter = args[1] if len(args) >= 2 else None
    print(f"postrace_writer V2：DATA_DIR={DATA_DIR}；日期={date_str}"
          + (f"；馬場={venue_filter}" if venue_filter else ""))
    process_day(date_str, venue_filter)


if __name__ == "__main__":
    main()
