from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class JournalPaths:
    templates_dir: Path
    local_dir: Path

    @property
    def template_trades(self) -> Path:
        return self.templates_dir / "trades.csv"

    @property
    def template_fills(self) -> Path:
        return self.templates_dir / "fills.csv"

    @property
    def template_ledger(self) -> Path:
        return self.templates_dir / "ledger.csv"

    @property
    def local_trades(self) -> Path:
        return self.local_dir / "trades.csv"

    @property
    def local_fills(self) -> Path:
        return self.local_dir / "fills.csv"

    @property
    def local_ledger(self) -> Path:
        return self.local_dir / "ledger.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Manual trade journal helper (init/validate).")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Create journal/local CSVs from templates (does not overwrite).")
    init.add_argument("--force", action=argparse.BooleanOptionalAction, default=False, help="Overwrite existing files.")

    sub.add_parser("validate", help="Validate journal/local CSVs (columns + timestamps).")
    return p.parse_args()


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            return [c.strip() for c in row]
    raise RuntimeError(f"Empty CSV (no header): {path}")


def _copy_template(src: Path, dst: Path, *, force: bool) -> None:
    if dst.exists() and not force:
        print(f"Exists (skipping): {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    print(f"Wrote: {dst}")


def _read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit(f"Empty CSV (no header): {path}")
        rows = [row for row in reader]
        return [c.strip() for c in reader.fieldnames], rows


def _parse_iso8601_utc(value: str) -> datetime:
    v = value.strip()
    if not v:
        raise ValueError("empty timestamp")
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        raise ValueError("timestamp missing timezone (expected UTC)")
    return dt


def init_journal(paths: JournalPaths, *, force: bool) -> None:
    if not paths.template_trades.exists():
        raise SystemExit(f"Missing template: {paths.template_trades}")
    if not paths.template_fills.exists():
        raise SystemExit(f"Missing template: {paths.template_fills}")
    if not paths.template_ledger.exists():
        raise SystemExit(f"Missing template: {paths.template_ledger}")

    _copy_template(paths.template_trades, paths.local_trades, force=force)
    _copy_template(paths.template_fills, paths.local_fills, force=force)
    _copy_template(paths.template_ledger, paths.local_ledger, force=force)


def _validate_timestamps(rows: list[dict[str, str]], *, columns: list[str], file: Path) -> None:
    for col in columns:
        bad_values: list[str] = []
        for row in rows:
            raw = (row.get(col) or "").strip()
            if raw == "":
                continue
            try:
                _parse_iso8601_utc(raw)
            except Exception:
                bad_values.append(raw)
                if len(bad_values) >= 5:
                    break
        if bad_values:
            raise SystemExit(f"{file}: invalid timestamps in {col}: {bad_values}")


def validate_journal(paths: JournalPaths) -> None:
    if not paths.local_trades.exists() or not paths.local_fills.exists() or not paths.local_ledger.exists():
        raise SystemExit("Journal not initialized. Run: python3 journal/tools/journal_cli.py init")

    template_trades_cols = _read_header(paths.template_trades)
    template_fills_cols = _read_header(paths.template_fills)
    template_ledger_cols = _read_header(paths.template_ledger)

    trades_cols, trades_rows = _read_csv_rows(paths.local_trades)
    fills_cols, fills_rows = _read_csv_rows(paths.local_fills)
    ledger_cols, ledger_rows = _read_csv_rows(paths.local_ledger)

    missing_trades = sorted(set(template_trades_cols) - set(trades_cols))
    missing_fills = sorted(set(template_fills_cols) - set(fills_cols))
    missing_ledger = sorted(set(template_ledger_cols) - set(ledger_cols))

    if missing_trades:
        raise SystemExit(f"{paths.local_trades}: missing columns: {missing_trades}")
    if missing_fills:
        raise SystemExit(f"{paths.local_fills}: missing columns: {missing_fills}")
    if missing_ledger:
        raise SystemExit(f"{paths.local_ledger}: missing columns: {missing_ledger}")

    _validate_timestamps(
        trades_rows,
        columns=["created_at_utc", "updated_at_utc", "entry_time_utc", "exit_time_utc"],
        file=paths.local_trades,
    )
    _validate_timestamps(fills_rows, columns=["time_utc"], file=paths.local_fills)
    _validate_timestamps(ledger_rows, columns=["time_utc"], file=paths.local_ledger)

    now = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    print("Validation: OK")
    print(f"- trades rows: {len(trades_rows)}")
    print(f"- fills rows:  {len(fills_rows)}")
    print(f"- ledger rows: {len(ledger_rows)}")
    print(f"- checked at:  {now}")


def main() -> None:
    args = _parse_args()
    paths = JournalPaths(templates_dir=Path("journal/templates"), local_dir=Path("journal/local"))

    if args.cmd == "init":
        init_journal(paths, force=bool(args.force))
        return
    if args.cmd == "validate":
        validate_journal(paths)
        return
    raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
