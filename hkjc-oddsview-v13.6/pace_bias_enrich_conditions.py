#!/usr/bin/env python3
"""Fill race conditions on already-scraped running-position rows for 3 meetings."""
import argparse
import csv
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from pace_bias_backfill import BASE, TreeParser, fetch, txt

MEETINGS = ["2026-09-16", "2026-09-23", "2026-09-27"]
GOINGS = ["GOOD TO FIRM", "GOOD TO YIELDING", "GOOD TO SOFT", "WET FAST",
          "WET SLOW", "STANDARD", "YIELDING", "HEAVY", "SOFT", "GOOD", "FAST"]


def race_number(value):
    return str(int(float(value)))


def extract_conditions(race_node):
    text = txt(race_node)
    result = {"distance": "", "track": "", "track_config": "", "going": ""}

    m = re.search(r"(?<!\d)(\d{3,4})\s*M\b", text, re.I)
    if m:
        result["distance"] = m.group(1)

    if re.search(r"\bAWT\b|ALL\s+WEATHER\s+TRACK", text, re.I):
        result["track"] = "ALL WEATHER TRACK"
        result["track_config"] = "ALL WEATHER TRACK"
        marker = re.search(r"\bAWT\b|ALL\s+WEATHER\s+TRACK", text, re.I)
    else:
        turf = re.search(r"\bTURF\b", text, re.I)
        if turf:
            result["track"] = "TURF"
            marker = turf
            course = re.search(r"[\"“]?([A-F](?:\+\d+)?)['\"”]?\s+COURSE", text, re.I)
            if course:
                result["track_config"] = course.group(1).upper()
        else:
            marker = None

    tail = text[marker.end():] if marker else text
    for going in GOINGS:
        if re.search(r"\b" + re.escape(going) + r"\b", tail, re.I):
            result["going"] = going
            break

    if not all(result.values()):
        missing = [k for k, v in result.items() if not v]
        raise ValueError(f"could not parse {', '.join(missing)} from race header: {text[:180]}")
    return result


def get_meeting_conditions(date):
    d = datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y")
    url = BASE + "?" + urlencode({"All": "True", "RaceDate": d})
    html = fetch(url)
    parser = TreeParser()
    parser.feed(html)
    races = {}
    for node in parser.root.all("div"):
        race_id = node.attrs.get("id", "")
        if re.fullmatch(r"Race\d+", race_id):
            race_no = int(race_id[4:])
            races[race_no] = extract_conditions(node)
    if not races:
        raise ValueError(f"no race headers parsed for {date}; no CSV changes made")
    return url, races


def main():
    ap = argparse.ArgumentParser(description="Enrich the three extra 2026-09 running-position dates with HKJC race conditions.")
    ap.add_argument("--running-csv", type=Path, default=Path("hkjc_quant/data/running_positions_raw.csv"))
    ap.add_argument("--sleep", type=float, default=1.5)
    args = ap.parse_args()
    if not args.running_csv.exists():
        sys.exit(f"Running-position CSV not found: {args.running_csv}")

    with args.running_csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    required = {"race_date", "venue", "race_no", "distance", "track", "track_config", "going"}
    if not required.issubset(fields):
        sys.exit("Running-position CSV is missing required columns: " + ", ".join(sorted(required - set(fields))))

    # Parse all three meetings before writing anything, so a fetch/parse error leaves the CSV untouched.
    meeting_data = {}
    for i, date in enumerate(MEETINGS):
        url, races = get_meeting_conditions(date)
        meeting_data[date] = (url, races)
        print(f"[{i + 1}/{len(MEETINGS)}] {date}: parsed {len(races)} race headers", flush=True)
        if i + 1 < len(MEETINGS):
            time.sleep(max(0, args.sleep))

    updates = 0
    for row in rows:
        date = row["race_date"].strip()
        if date not in meeting_data:
            continue
        _, races = meeting_data[date]
        race_no = int(race_number(row["race_no"]))
        if race_no not in races:
            raise ValueError(f"race {race_no} missing from HKJC page for {date}; no CSV changes made")
        conditions = races[race_no]
        for key, value in conditions.items():
            row[key] = value
        updates += 1

    for date in MEETINGS:
        count = sum(1 for row in rows if row["race_date"].strip() == date)
        if count == 0:
            sys.exit(f"No existing running-position rows for {date}; no CSV changes made")

    backup = args.running_csv.with_suffix(args.running_csv.suffix + ".before_conditions.bak")
    if not backup.exists():
        shutil.copy2(args.running_csv, backup)
    tmp = args.running_csv.with_suffix(args.running_csv.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, args.running_csv)

    for date in MEETINGS:
        url, races = meeting_data[date]
        row_count = sum(1 for row in rows if row["race_date"].strip() == date)
        print(f"{date}: updated {row_count} runners across {len(races)} races; source {url}", flush=True)
    print(f"Updated rows: {updates}; backup: {backup}", flush=True)


if __name__ == "__main__":
    main()
