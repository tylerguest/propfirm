from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from reporting.daily_report_cli import REPORT_COLUMNS


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate a daily report entry from journal fills/trades.")
    p.add_argument("--runs-root", default="research/output", help="Backtest output root (contains runs/).")
    p.add_argument("--fills", default="journal/local/fills.csv", help="Journal fills CSV path.")
    p.add_argument("--trades", default="journal/local/trades.csv", help="Journal trades CSV path.")
    p.add_argument("--out", default="reporting/daily_report_log.csv", help="Output CSV log path.")
    p.add_argument("--md-out", default="reporting/daily_report.md", help="Markdown summary output path.")
    p.add_argument("--run-id", action="append", required=True, help="Run ID (repeat for multiple).")
    return p.parse_args()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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


def _extract_run_id(order_id: str) -> str:
    if ":" in order_id:
        return order_id.split(":", 1)[0]
    return order_id


def _report_for_run(*, run_id: str, runs_root: Path, fills_df: pd.DataFrame, trades_df: pd.DataFrame) -> dict[str, str]:
    run_dir = runs_root / "runs" / run_id
    config_path = run_dir / "config.json"
    cfg = {}
    if config_path.exists():
        cfg = pd.read_json(config_path, typ="series").to_dict()

    fills = fills_df.copy()
    if "order_id" not in fills.columns:
        fills["order_id"] = ""
    fills["order_id"] = fills["order_id"].astype(str)
    fills["run_id"] = fills["order_id"].apply(_extract_run_id)
    fills = fills[fills["run_id"] == run_id]

    if "trade_id" in fills.columns:
        trade_ids = set(str(tid) for tid in fills["trade_id"].dropna().tolist())
    else:
        trade_ids = set()
    trades = trades_df.copy()
    trades["trade_id"] = trades.get("trade_id", "").astype(str)
    if trade_ids:
        trades = trades[trades["trade_id"].isin(trade_ids)]
    else:
        trades = trades.iloc[0:0]

    realized = pd.to_numeric(trades.get("realized_pnl_quote", 0), errors="coerce")
    entry_qty = pd.to_numeric(trades.get("entry_qty_quote", 0), errors="coerce")
    exit_qty = pd.to_numeric(trades.get("exit_qty_quote", 0), errors="coerce")
    entry_fee = pd.to_numeric(trades.get("entry_fee_quote", 0), errors="coerce")
    exit_fee = pd.to_numeric(trades.get("exit_fee_quote", 0), errors="coerce")
    fallback = exit_qty - entry_qty - entry_fee - exit_fee
    net_pnl = realized.fillna(fallback).fillna(0.0).sum()
    fees_paid = pd.to_numeric(fills.get("fee_quote", 0), errors="coerce").fillna(0.0).sum()
    trades_count = len(trades.index)

    start_equity = ""
    end_equity = ""
    if isinstance(cfg, dict) and "initial_cash" in cfg:
        try:
            start_equity = float(cfg["initial_cash"])
            end_equity = start_equity + float(net_pnl)
        except Exception:
            start_equity = ""
            end_equity = ""

    data_end = ""
    if isinstance(cfg, dict) and "data_end" in cfg:
        data_end = str(cfg["data_end"])
    date_utc = data_end.split("T")[0] if "T" in data_end else datetime.now(tz=UTC).date().isoformat()

    return {
        "date_utc": date_utc,
        "start_equity": f"{start_equity:.6f}" if isinstance(start_equity, float) else "",
        "end_equity": f"{end_equity:.6f}" if isinstance(end_equity, float) else "",
        "net_pnl": f"{float(net_pnl):.6f}".rstrip("0").rstrip("."),
        "fees_paid": f"{float(fees_paid):.6f}".rstrip("0").rstrip("."),
        "positions_held": "",
        "trades_count": str(trades_count),
        "max_exposure": "",
        "max_drawdown": "",
        "rules_followed": "",
        "rule_violations": "",
        "notes": f"auto backtest report run_id={run_id}",
    }


def _write_md(path: Path, rows: list[dict[str, str]]) -> None:
    lines = ["# Daily Report (Auto)", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row['date_utc']} — {row['notes']}",
                f"- net_pnl: {row['net_pnl']}",
                f"- fees_paid: {row['fees_paid']}",
                f"- trades_count: {row['trades_count']}",
                f"- start_equity: {row['start_equity']}",
                f"- end_equity: {row['end_equity']}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    runs_root = Path(args.runs_root)
    fills_df = _read_csv(Path(args.fills))
    trades_df = _read_csv(Path(args.trades))

    if fills_df.empty:
        raise SystemExit("No fills found. Run a backtest with journal append enabled first.")

    rows: list[dict[str, str]] = []
    for run_id in args.run_id:
        rows.append(_report_for_run(run_id=run_id, runs_root=runs_root, fills_df=fills_df, trades_df=trades_df))

    out = Path(args.out)
    _ensure_header(out)
    with out.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS)
        for row in rows:
            writer.writerow(row)

    _write_md(Path(args.md_out), rows)
    print(f"Wrote daily report entries: {out}")


if __name__ == "__main__":
    main()
