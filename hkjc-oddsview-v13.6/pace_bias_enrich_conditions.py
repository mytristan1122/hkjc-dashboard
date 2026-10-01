#!/usr/bin/env python3
"""Apply 28 verified HKJC race-condition records to existing runner rows.

Source: HKJC official English per-race results, retrieved 2026-10-01.
All 28 source pages passed date, venue, race-number and metadata checks.
This is a fixed-date data patch. It does not request pages from the VPS.
"""
import argparse
import csv
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

VERSION = "VERIFIED_CONDITIONS_20261001"
BASE = "https://racing.hkjc.com/en-us/local/information/localresults"
FIELDS = ("distance", "track", "track_config", "going")
MEETINGS = {"2026-09-16": ("HV", 8), "2026-09-23": ("HV", 9), "2026-09-27": ("ST", 11)}
# date, venue, race_no, distance_metres, track, course_configuration, going
RECORDS = [
    ('2026-09-16', 'HV', 1, '1000', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 2, '1200', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 3, '1800', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 4, '1200', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 5, '1200', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 6, '1650', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 7, '1000', 'TURF', 'B', 'GOOD'),
    ('2026-09-16', 'HV', 8, '1650', 'TURF', 'B', 'GOOD'),
    ('2026-09-23', 'HV', 1, '1650', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 2, '1200', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 3, '1650', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 4, '1200', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 5, '1650', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 6, '1000', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 7, '1200', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 8, '1200', 'TURF', 'C', 'GOOD'),
    ('2026-09-23', 'HV', 9, '1800', 'TURF', 'C', 'GOOD'),
    ('2026-09-27', 'ST', 1, '1200', 'ALL WEATHER TRACK', 'ALL WEATHER TRACK', 'GOOD'),
    ('2026-09-27', 'ST', 2, '1200', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 3, '1400', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 4, '1000', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 5, '1200', 'ALL WEATHER TRACK', 'ALL WEATHER TRACK', 'GOOD'),
    ('2026-09-27', 'ST', 6, '1400', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 7, '1200', 'ALL WEATHER TRACK', 'ALL WEATHER TRACK', 'GOOD'),
    ('2026-09-27', 'ST', 8, '1400', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 9, '1600', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 10, '1200', 'TURF', 'C+3', 'GOOD TO FIRM'),
    ('2026-09-27', 'ST', 11, '1400', 'TURF', 'C+3', 'GOOD TO FIRM'),
]
CONDITIONS = {(d, v, n): dict(zip(FIELDS, values)) for d, v, n, *values in RECORDS}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        rows = list(reader)
    if len(fields) != len(set(fields)) or any(None in r or None in r.values() for r in rows):
        raise ValueError("Malformed CSV: duplicate headers or inconsistent column counts")
    return fields, rows


def race_number(value):
    if not re.fullmatch(r"\d+(?:\.0+)?", value.strip()):
        raise ValueError("Invalid race number: " + repr(value))
    return int(float(value))


def main():
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--running-csv", type=Path, default=Path("hkjc_quant/data/running_positions_raw.csv"))
    args = ap.parse_args()
    print(VERSION, flush=True)
    path = args.running_csv.resolve()
    original_bytes = path.read_bytes()
    fields, rows = read_csv(path)
    required = {"race_date", "venue", "race_no", *FIELDS}
    if not required.issubset(fields):
        raise ValueError("Missing columns: " + ", ".join(sorted(required - set(fields))))
    if len(CONDITIONS) != 28 or any(not value for data in CONDITIONS.values() for value in data.values()):
        raise ValueError("Verified metadata is incomplete; replace this script with the full file")
    counts = {}
    for date, (venue, count) in MEETINGS.items():
        group = [r for r in rows if r["race_date"].strip() == date]
        if {r["venue"].strip().upper() for r in group} != {venue}:
            raise ValueError(date + ": missing runner rows or unexpected venue")
        found = {race_number(r["race_no"]) for r in group}
        if found != set(range(1, count + 1)):
            raise ValueError(date + ": unexpected race numbers " + repr(sorted(found)))
        if {(date, venue, n) for n in found} - set(CONDITIONS):
            raise ValueError(date + ": metadata missing for a race")
        counts[date] = len(group)
    output = []
    changed = 0
    for original in rows:
        row = original.copy()
        date = row["race_date"].strip()
        if date in MEETINGS:
            key = (date, row["venue"].strip().upper(), race_number(row["race_no"]))
            row.update(CONDITIONS[key])
        changed += row != original
        output.append(row)
    if path.read_bytes() != original_bytes:
        raise RuntimeError("CSV changed during processing; no update written")
    if changed:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(path.name + ".before_verified_conditions_" + stamp + ".bak")
        shutil.copy2(path, backup)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(output)
                handle.flush()
                os.fsync(handle.fileno())
            checked_fields, checked_rows = read_csv(Path(temporary))
            if checked_fields != fields or checked_rows != output:
                raise RuntimeError("Output CSV verification failed")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print("BACKUP: " + str(backup), flush=True)
    for date, count in counts.items():
        print("{} {}: {} races, {} runner rows".format(date, MEETINGS[date][0], MEETINGS[date][1], count), flush=True)
    print("SUCCESS: 28 races; {} rows matched; {} rows changed".format(sum(counts.values()), changed), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FAILED: " + str(exc), file=sys.stderr, flush=True)
        sys.exit(1)
