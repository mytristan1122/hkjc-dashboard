#!/usr/bin/env python3
"""Fill distance/track/course/going for existing running-position rows.

Reads each race's official HKJC results page, validates all requested races
before writing, and keeps the running-position data separate from model data.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://racing.hkjc.com/zh-hk/local/information/localresults"
MEETINGS = {
    "2026-09-16": ("HV", 8),
    "2026-09-23": ("HV", 9),
    "2026-09-27": ("ST", 11),
}
GOING_MAP = (
    ("å¥½åœ°è‡³å¿«åœ°", "GOOD TO FIRM"),
    ("å¥½åœ°è‡³é»åœ°", "GOOD TO YIELDING"),
    ("å¥½åœ°è‡³è»Ÿåœ°", "GOOD TO SOFT"),
    ("é»è»Ÿåœ°", "GOOD TO SOFT"),
    ("å¥½/å¿«", "GOOD TO FIRM"),
    ("å¥½åœ°", "GOOD"),
    ("æ¨™æº–åœ°", "STANDARD"),
    ("å…¨å¤©å€™æ¨™æº–åœ°", "STANDARD"),
    ("é»åœ°", "YIELDING"),
    ("è»Ÿåœ°", "SOFT"),
    ("å¿«åœ°", "FAST"),
    ("æ¿•å¿«", "WET FAST"),
    ("æ¿•æ…¢", "WET SLOW"),
)


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())

    def text(self):
        return re.sub(r"\s+", " ", " ".join(self.parts)).replace("\xa0", " ")


def fetch(url: str, attempts: int = 4) -> str:
    for attempt in range(attempts):
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; HKJC-Condition-Backfill/1.0)",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.7",
        })
        try:
            with urlopen(req, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, "replace")
        except HTTPError as exc:
            if exc.code in (403, 429):
                raise RuntimeError(f"HKJC returned HTTP {exc.code}; stopped") from exc
            if exc.code < 500 or attempt == attempts - 1:
                raise
        except (URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(f"HKJC request failed: {exc}") from exc
        time.sleep(min(2 ** attempt, 20))
    raise RuntimeError("HKJC request failed")


def parse_conditions(html: str, date: str, venue: str, race_no: int) -> dict[str, str]:
    parser = VisibleText()
    parser.feed(html)
    text = parser.text()
    # Results pages show e.g. "ç¬¬äº”ç­ - 1000ç±³" / "Class 5 - 1,000M".
    dm = re.search(r"(?<!\d)(\d[\d,]{2,})\s*(?:ç±³|M)(?![A-Za-z])", text, re.I)
    if not dm:
        raise ValueError(f"{date} {venue} R{race_no}: distance missing in official race result")
    distance = dm.group(1).replace(",", "")

    # Read the actual course field only. The rest of a result page can mention
    # another surface in stewards' comments, which must not change this race.
    track_match = re.search(
        r"(?:è³½é“|Course)\s*[:ï¼š]?\s*((?:è‰åœ°|å…¨å¤©å€™è·‘é“|TURF|ALL\s+WEATHER(?:\s+TRACK)?|AWT)(?:\s*[-â€“â€”]\s*[\"â€œ]?[A-F](?:\+\d+)?[\"â€]?\s*(?:è³½é“|Course))?)",
        text, re.I,
    )
    if not track_match:
        raise ValueError(f"{date} {venue} R{race_no}: official course field missing")
    track_field = track_match.group(1)
    awt = re.search(r"å…¨å¤©å€™|ALL\s+WEATHER|\bAWT\b", track_field, re.I)
    turf = re.search(r"è‰åœ°|\bTURF\b", track_field, re.I)
    if awt:
        track, config = "ALL WEATHER TRACK", "ALL WEATHER TRACK"
    elif turf:
        track = "TURF"
        cm = re.search(r"[-â€“â€”]\s*[\"â€œ]?([A-F](?:\+\d+)?)[\"â€]?\s*(?:è³½é“|Course)", track_field, re.I)
        if not cm:
            raise ValueError(f"{date} {venue} R{race_no}: turf course configuration missing")
        config = cm.group(1).upper()
    else:
        raise ValueError(f"{date} {venue} R{race_no}: track type missing")

    # Restrict going extraction to the official going field, avoiding incidental text.
    gm = re.search(r"(?:å ´åœ°ç‹€æ³|Going)\s*[:ï¼š]?\s*([^|]{1,24})", text, re.I)
    going_text = gm.group(1).strip() if gm else ""
    going = ""
    for label, normalized in GOING_MAP:
        if label.lower() in going_text.lower():
            going = normalized
            break
    if not going:
        for label, normalized in (("GOOD TO FIRM", "GOOD TO FIRM"), ("GOOD TO YIELDING", "GOOD TO YIELDING"), ("GOOD TO SOFT", "GOOD TO SOFT"), ("STANDARD", "STANDARD"), ("YIELDING", "YIELDING"), ("HEAVY", "HEAVY"), ("SOFT", "SOFT"), ("GOOD", "GOOD"), ("FAST", "FAST")):
            if re.search(r"\b" + re.escape(label) + r"\b", going_text, re.I):
                going = normalized
                break
    if not going:
        raise ValueError(f"{date} {venue} R{race_no}: going condition missing")
    return {"distance": distance, "track": track, "track_config": config, "going": going}


def official_url(date: str, venue: str, race_no: int) -> str:
    d = datetime.strptime(date, "%Y-%m-%d").strftime("%Y/%m/%d")
    return BASE + "?" + urlencode({"RaceNo": race_no, "Racecourse": venue, "racedate": d})


def main():
    ap = argparse.ArgumentParser(description="Add official HKJC race conditions to existing running-position rows.")
    ap.add_argument("--running-csv", type=Path, default=Path("hkjc_quant/data/running_positions_raw.csv"))
    ap.add_argument("--sleep", type=float, default=1.0, help="Seconds between official race-result requests")
    args = ap.parse_args()
    if not args.running_csv.is_file():
        sys.exit(f"Running-position CSV not found: {args.running_csv}")

    with args.running_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames or [], list(reader)
    required = {"race_date", "venue", "race_no", "distance", "track", "track_config", "going"}
    missing = required - set(fields)
    if missing:
        sys.exit("CSV missing required columns: " + ", ".join(sorted(missing)))

    # Validate the expected dates, venues and race counts before making network calls.
    meeting_rows: dict[str, list[dict[str, str]]] = {}
    for date, (venue, expected_races) in MEETINGS.items():
        selected = [r for r in rows if r["race_date"].strip() == date]
        if not selected:
            sys.exit(f"No existing running-position rows for {date}; CSV not changed")
        venues = {r["venue"].strip().upper() for r in selected}
        if venues != {venue}:
            sys.exit(f"Unexpected venue for {date}: {sorted(venues)}; CSV not changed")
        race_numbers = {int(float(r["race_no"])) for r in selected}
        if race_numbers != set(range(1, expected_races + 1)):
            sys.exit(f"{date}: expected races 1-{expected_races}, found {sorted(race_numbers)}; CSV not changed")
        meeting_rows[date] = selected

    conditions: dict[tuple[str, int], dict[str, str]] = {}
    total_requests = sum(races for _, races in MEETINGS.values())
    request_no = 0
    # Fetch every race separately; this uses the official result page's explicit fields.
    for date, (venue, race_count) in MEETINGS.items():
        for race_no in range(1, race_count + 1):
            url = official_url(date, venue, race_no)
            data = parse_conditions(fetch(url), date, venue, race_no)
            conditions[(date, race_no)] = data
            request_no += 1
            print(f"[{request_no}/{total_requests}] {date} {venue} R{race_no}: {data}", flush=True)
            if request_no < total_requests:
                time.sleep(max(0, args.sleep))

    # Ensure every existing runner in these meetings maps to a parsed race.
    updates = 0
    for row in rows:
        date = row["race_date"].strip()
        if date not in MEETINGS:
            continue
        key = (date, int(float(row["race_no"])))
        if key not in conditions:
            sys.exit(f"Missing parsed conditions for {key}; CSV not changed")
        row.update(conditions[key])
        updates += 1

    backup = args.running_csv.with_suffix(args.running_csv.suffix + ".before_conditions.bak")
    if not backup.exists():
        shutil.copy2(args.running_csv, backup)
    tmp = args.running_csv.with_suffix(args.running_csv.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, args.running_csv)

    print(f"SUCCESS: updated {updates} runner rows; backup: {backup}", flush=True)
    for date, (venue, races) in MEETINGS.items():
        count = sum(1 for r in rows if r["race_date"].strip() == date)
        print(f"{date} {venue}: {races} races, {count} runners", flush=True)


if __name__ == "__main__":
    main()
