from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class LedgerEntry:
    time_utc: str
    venue: str
    account: str
    type: str
    amount: str
    currency: str
    transaction_id: str
    reference: str
    notes: str


LEDGER_COLUMNS = [
    "time_utc",
    "venue",
    "account",
    "type",
    "amount",
    "currency",
    "transaction_id",
    "reference",
    "notes",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Append cashflow entries (deposits/withdrawals) to journal/local/ledger.csv.")
    sub = p.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="Append a single ledger entry.")
    add.add_argument("--out", default="journal/local/ledger.csv", help="Ledger CSV path (default: journal/local/ledger.csv).")
    add.add_argument("--time-utc", default=None, help="UTC time (ISO8601). Default: now.")
    add.add_argument("--venue", default="coinbase", help="Venue (default: coinbase).")
    add.add_argument("--account", default="", help="Optional account label.")
    add.add_argument(
        "--type",
        required=True,
        choices=["deposit", "withdrawal", "transfer", "fee", "adjustment"],
        help="Ledger entry type.",
    )
    add.add_argument("--amount", required=True, help="Signed amount (e.g., 94.00 or -10.00).")
    add.add_argument("--currency", required=True, help="Currency code (e.g., USD, USDC, BTC).")
    add.add_argument("--transaction-id", default="", help="Optional transaction id.")
    add.add_argument("--reference", default="", help="Optional reference (e.g., 'initial funding').")
    add.add_argument("--notes", default="", help="Optional notes.")

    return p.parse_args()


def _now_utc_iso() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def _ensure_header(path: Path) -> None:
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        if header is None:
            raise SystemExit(f"Ledger file exists but is empty: {path}")
        if [c.strip() for c in header] != LEDGER_COLUMNS:
            raise SystemExit(f"Ledger header mismatch in {path}. Expected: {LEDGER_COLUMNS}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(LEDGER_COLUMNS)


def add_entry(path: Path, entry: LedgerEntry) -> None:
    _ensure_header(path)
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        w.writerow(entry.__dict__)


def main() -> None:
    args = _parse_args()

    if args.cmd == "add":
        time_utc = args.time_utc.strip() if args.time_utc else _now_utc_iso()
        entry = LedgerEntry(
            time_utc=time_utc,
            venue=str(args.venue).strip(),
            account=str(args.account).strip(),
            type=str(args.type).strip(),
            amount=str(args.amount).strip(),
            currency=str(args.currency).strip().upper(),
            transaction_id=str(args.transaction_id).strip(),
            reference=str(args.reference).strip(),
            notes=str(args.notes).strip(),
        )
        out = Path(args.out)
        add_entry(out, entry)
        print(f"Appended to {out}: {entry.type} {entry.amount} {entry.currency} at {entry.time_utc}")
        return

    raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()

