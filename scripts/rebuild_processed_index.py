from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


FILENAME_RE = re.compile(
    r"^(?P<product_id>.+)_(?P<granularity>(\d+s|\d+[mhd]))_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})\.csv$"
)

LABEL_TO_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "1d": 86400,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild data/processed/index.csv from existing CSV files.")
    parser.add_argument("--processed-dir", default="data/processed", help="Processed data directory (default: data/processed).")
    parser.add_argument("--output", default="data/processed/index.csv", help="Index output path (default: data/processed/index.csv).")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    processed_dir = Path(args.processed_dir)
    if not processed_dir.exists():
        raise SystemExit(f"Processed dir not found: {processed_dir}")

    rows: list[dict[str, str]] = []
    for path in sorted(processed_dir.glob("*.csv")):
        if path.name == Path(args.output).name:
            continue
        match = FILENAME_RE.match(path.name)
        if not match:
            print(f"Skipping (unrecognized filename): {path.name}")
            continue
        granularity = match.group("granularity")
        if granularity.endswith("s"):
            granularity_seconds = granularity[:-1]
        else:
            granularity_seconds = str(LABEL_TO_SECONDS.get(granularity, ""))
            if not granularity_seconds:
                print(f"Skipping (unknown granularity label): {path.name}")
                continue
        rows.append(
            {
                "product_id": match.group("product_id"),
                "granularity_seconds": granularity_seconds,
                "dataset_start": match.group("start"),
                "dataset_end": match.group("end"),
                "processed_path": str(path.as_posix()),
            }
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "product_id",
                "granularity_seconds",
                "dataset_start",
                "dataset_end",
                "processed_path",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote {len(rows)} row(s) to {out_path}")


if __name__ == "__main__":
    main()
