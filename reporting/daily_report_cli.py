from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path


REPORT_COLUMNS = [
    "date_utc",
    "start_equity",
    "end_equity",
    "net_pnl",
    "fees_paid",
    "positions_held",
    "trades_count",
    "max_exposure",
    "max_drawdown",
    "rules_followed",
    "rule_violations",
    "notes",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append a daily report entry.")
    p.add_argument("--out", default="reporting/daily_report_log.csv", help="Output CSV path.")
    p.add_argument("--date-utc", default=None, help="UTC date (YYYY-MM-DD). Default: today.")
    p.add_argument("--start-equity", required=True, help="Start equity (quote currency).")
    p.add_argument("--end-equity", required=True, help="End equity (quote currency).")
    p.add_argument("--net-pnl", required=True, help="Net PnL (quote currency).")
    p.add_argument("--fees-paid", default="", help="Fees paid (quote currency).")
    p.add_argument("--positions-held", default="", help="Comma-separated symbols held.")
    p.add_argument("--trades-count", default="", help="Number of trades.")
    p.add_argument("--max-exposure", default="", help="Max exposure (quote currency).")
    p.add_argument("--max-drawdown", default="", help="Max drawdown (pct or quote).")
    p.add_argument("--rules-followed", default="", help="yes/no.")
    p.add_argument("--rule-violations", default="", help="Short description.")
    p.add_argument("--notes", default="", help="Freeform notes.")
    return p.parse_args()


def _ensure_header(path: Path) -> None:
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        if header is None:
            raise SystemExit(f"Report file exists but is empty: {path}")
        if [c.strip() for c in header] != REPORT_COLUMNS:
            raise SystemExit(f"Report header mismatch in {path}. Expected: {REPORT_COLUMNS}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(REPORT_COLUMNS)


def main() -> None:
    args = _parse_args()
    out = Path(args.out)
    _ensure_header(out)

    date_utc = args.date_utc
    if not date_utc:
        date_utc = datetime.now(tz=UTC).date().isoformat()

    row = {
        "date_utc": date_utc,
        "start_equity": str(args.start_equity).strip(),
        "end_equity": str(args.end_equity).strip(),
        "net_pnl": str(args.net_pnl).strip(),
        "fees_paid": str(args.fees_paid).strip(),
        "positions_held": str(args.positions_held).strip(),
        "trades_count": str(args.trades_count).strip(),
        "max_exposure": str(args.max_exposure).strip(),
        "max_drawdown": str(args.max_drawdown).strip(),
        "rules_followed": str(args.rules_followed).strip(),
        "rule_violations": str(args.rule_violations).strip(),
        "notes": str(args.notes).strip(),
    }

    with out.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        writer.writerow(row)

    print(f"Appended daily report entry to {out}")


if __name__ == "__main__":
    main()
