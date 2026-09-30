#!/usr/bin/env python3
"""Audit running-position CSV coverage against the model race/runner index.

Read-only: writes audit reports only; never edits either input CSV.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), [dict(row) for row in reader]


def norm_date(value: str) -> str:
    value = (value or "").strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def key(row: dict[str, str]) -> tuple[str, str, int, str]:
    date = norm_date(row.get("race_date", ""))
    venue = (row.get("venue", "") or "").strip().upper()
    race_no = int(float(row.get("race_no", "")))
    horse_id = (row.get("horse_id", "") or "").strip()
    if not date or not venue or not horse_id:
        raise ValueError(f"Missing race_date, venue or horse_id: {row}")
    return date, venue, race_no, horse_id


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index-csv", type=Path, default=Path("hkjc_quant/data/runs_clean.csv"))
    ap.add_argument("--positions-csv", type=Path, default=Path("hkjc_quant/data/running_positions_raw.csv"))
    ap.add_argument("--report-prefix", type=Path, default=Path("hkjc_quant/data/running_positions_audit"))
    args = ap.parse_args()
    for path in (args.index_csv, args.positions_csv):
        if not path.is_file():
            sys.exit(f"Input file not found: {path}")

    index_fields, index_rows = read_rows(args.index_csv)
    pos_fields, pos_rows = read_rows(args.positions_csv)
    for label, fields, required in (
        ("Model index", index_fields, {"race_date", "venue", "race_no", "horse_id"}),
        ("Position data", pos_fields, {"race_date", "venue", "race_no", "horse_id"}),
    ):
        missing = required - set(fields)
        if missing:
            sys.exit(f"{label} missing required columns: {', '.join(sorted(missing))}")

    expected_by_race: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    expected_keys: set[tuple[str, str, int, str]] = set()
    for row in index_rows:
        k = key(row)
        expected_by_race[k[:3]].add(k[3])
        expected_keys.add(k)

    raw_keys: list[tuple[str, str, int, str]] = []
    actual_by_race: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    raw_counts: Counter[tuple[str, str, int, str]] = Counter()
    for row in pos_rows:
        k = key(row)
        raw_keys.append(k)
        actual_by_race[k[:3]].add(k[3])
        raw_counts[k] += 1

    dup_keys = {k: n for k, n in raw_counts.items() if n > 1}
    race_rows: list[dict[str, object]] = []
    races = sorted(set(expected_by_race) | set(actual_by_race))
    for rk in races:
        expected = expected_by_race.get(rk, set())
        actual = actual_by_race.get(rk, set())
        missing = sorted(expected - actual)
        extra = sorted(actual - expected) if expected else []
        duplicate_count = sum(n - 1 for k, n in dup_keys.items() if k[:3] == rk)
        if not expected:
            status = "no_model_index"
        elif missing or extra or duplicate_count:
            status = "review"
        else:
            status = "matched"
        race_rows.append({
            "race_date": rk[0], "venue": rk[1], "race_no": rk[2],
            "expected_runners": len(expected), "position_unique_runners": len(actual),
            "missing_runner_count": len(missing), "missing_horse_ids": ";".join(missing),
            "extra_runner_count": len(extra), "extra_horse_ids": ";".join(extra),
            "duplicate_rows": duplicate_count, "status": status,
        })

    dup_rows = [{"race_date": k[0], "venue": k[1], "race_no": k[2],
                 "horse_id": k[3], "row_count": n}
                for k, n in sorted(dup_keys.items())]

    # Report missing values in the running-position fields, including identity
    # fields. Empty JSON arrays are valid and are not classified as missing.
    check_fields = ["race_date", "venue", "race_no", "horse_id", "horse_no",
                    "horse_name", "finish_position", "call_positions_json",
                    "call_details_json", "finish_time_sec", "source_url"]
    gaps = []
    for field in check_fields:
        if field not in pos_fields:
            gaps.append({"field": field, "missing_rows": len(pos_rows),
                         "total_rows": len(pos_rows), "note": "column_absent"})
        else:
            missing = sum(1 for row in pos_rows if not (row.get(field) or "").strip())
            gaps.append({"field": field, "missing_rows": missing,
                         "total_rows": len(pos_rows), "note": ""})

    prefix = args.report_prefix
    write_csv(prefix.with_name(prefix.name + "_by_race.csv"),
              ["race_date", "venue", "race_no", "expected_runners", "position_unique_runners",
               "missing_runner_count", "missing_horse_ids", "extra_runner_count", "extra_horse_ids",
               "duplicate_rows", "status"], race_rows)
    write_csv(prefix.with_name(prefix.name + "_duplicates.csv"),
              ["race_date", "venue", "race_no", "horse_id", "row_count"], dup_rows)
    write_csv(prefix.with_name(prefix.name + "_field_gaps.csv"),
              ["field", "missing_rows", "total_rows", "note"], gaps)

    reviewed = sum(1 for r in race_rows if r["status"] == "review")
    no_index = sum(1 for r in race_rows if r["status"] == "no_model_index")
    matched = sum(1 for r in race_rows if r["status"] == "matched")
    missing_ids = sum(int(r["missing_runner_count"]) for r in race_rows)
    extra_ids = sum(int(r["extra_runner_count"]) for r in race_rows)
    print(f"Model index: {len(index_rows)} rows, {len(expected_keys)} unique runners, {len(expected_by_race)} races")
    print(f"Position file: {len(pos_rows)} rows, {len(set(raw_keys))} unique runners, {len(actual_by_race)} races")
    print(f"Race results: matched={matched}, review={reviewed}, no_model_index={no_index}")
    print(f"Runner comparison: missing={missing_ids}, extra={extra_ids}, duplicate_rows={sum(n - 1 for n in dup_keys.values())}")
    print(f"Reports: {prefix}_by_race.csv ; {prefix}_duplicates.csv ; {prefix}_field_gaps.csv")
    print("Input CSV files were not changed.")


if __name__ == "__main__":
    main()
