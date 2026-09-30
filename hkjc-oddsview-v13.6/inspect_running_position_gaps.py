#!/usr/bin/env python3
"""List missing finish-position/time values in the running-position CSV.

Read-only: writes two diagnostic CSV reports and never edits source files.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--positions-csv", type=Path,
                    default=Path("hkjc_quant/data/running_positions_raw.csv"))
    ap.add_argument("--report-prefix", type=Path,
                    default=Path("hkjc_quant/data/running_positions_result_gaps"))
    args = ap.parse_args()
    if not args.positions_csv.is_file():
        sys.exit(f"Position CSV not found: {args.positions_csv}")

    with args.positions_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        required = {"race_date", "venue", "race_no", "horse_id", "horse_no",
                    "horse_name", "finish_position", "finish_time_sec",
                    "call_positions_json", "call_details_json", "source_url"}
        if missing := required - fields:
            sys.exit("Position CSV missing columns: " + ", ".join(sorted(missing)))
        source_rows = list(reader)

    detail_rows: list[dict[str, str]] = []
    by_race: dict[tuple[str, str, str], dict[str, int]] = defaultdict(
        lambda: {"total_rows": 0, "missing_finish_position": 0,
                 "missing_finish_time": 0, "missing_both": 0,
                 "missing_call_positions": 0, "missing_call_details": 0})
    missing_position_rows = missing_time_rows = missing_both_rows = 0
    for row in source_rows:
        date = (row.get("race_date") or "").strip()
        venue = (row.get("venue") or "").strip()
        race_no = (row.get("race_no") or "").strip()
        k = (date, venue, race_no)
        stats = by_race[k]
        stats["total_rows"] += 1
        missing_pos = not (row.get("finish_position") or "").strip()
        missing_time = not (row.get("finish_time_sec") or "").strip()
        missing_calls = not (row.get("call_positions_json") or "").strip()
        missing_details = not (row.get("call_details_json") or "").strip()
        stats["missing_finish_position"] += int(missing_pos)
        stats["missing_finish_time"] += int(missing_time)
        stats["missing_both"] += int(missing_pos and missing_time)
        stats["missing_call_positions"] += int(missing_calls)
        stats["missing_call_details"] += int(missing_details)
        if not (missing_pos or missing_time or missing_calls or missing_details):
            continue
        missing_position_rows += int(missing_pos)
        missing_time_rows += int(missing_time)
        missing_both_rows += int(missing_pos and missing_time)
        detail_rows.append({
            "race_date": date, "venue": venue, "race_no": race_no,
            "horse_no": (row.get("horse_no") or "").strip(),
            "horse_id": (row.get("horse_id") or "").strip(),
            "horse_name": (row.get("horse_name") or "").strip(),
            "finish_position": (row.get("finish_position") or "").strip(),
            "finish_time_sec": (row.get("finish_time_sec") or "").strip(),
            "missing_finish_position": str(missing_pos),
            "missing_finish_time": str(missing_time),
            "call_positions_json": (row.get("call_positions_json") or "").strip(),
            "call_details_json": (row.get("call_details_json") or "").strip(),
            "source_url": (row.get("source_url") or "").strip(),
        })

    prefix = args.report_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    detail_fields = ["race_date", "venue", "race_no", "horse_no", "horse_id",
                     "horse_name", "finish_position", "finish_time_sec",
                     "missing_finish_position", "missing_finish_time",
                     "call_positions_json", "call_details_json", "source_url"]
    with prefix.with_name(prefix.name + "_rows.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        writer.writerows(detail_rows)

    race_fields = ["race_date", "venue", "race_no", "total_rows",
                   "missing_finish_position", "missing_finish_time", "missing_both",
                   "missing_call_positions", "missing_call_details"]
    race_rows = [{"race_date": k[0], "venue": k[1], "race_no": k[2], **v}
                 for k, v in sorted(by_race.items())
                 if any(v[name] for name in race_fields[4:])]
    with prefix.with_name(prefix.name + "_by_race.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=race_fields)
        writer.writeheader()
        writer.writerows(race_rows)

    meetings = sorted({r["race_date"] for r in detail_rows})
    print(f"Position rows checked: {len(source_rows)}")
    print(f"Rows with any result/sectional gap: {len(detail_rows)}")
    print(f"Blank finish_position: {missing_position_rows}; blank finish_time_sec: {missing_time_rows}; blank both: {missing_both_rows}")
    print(f"Affected races: {len(race_rows)}; affected meeting dates: {len(meetings)}")
    print("Affected dates: " + (", ".join(meetings) if meetings else "none"))
    print(f"Reports: {prefix}_rows.csv ; {prefix}_by_race.csv")
    print("Original position CSV was not changed.")


if __name__ == "__main__":
    main()
