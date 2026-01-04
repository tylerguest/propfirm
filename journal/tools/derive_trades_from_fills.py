from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class Fill:
    fill_id: str
    time_utc: datetime
    symbol: str
    strategy: str
    side: str
    price: float
    qty_base: float
    qty_quote: float
    fee_quote: float
    liquidity: str
    order_id: str
    trade_id: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Derive trade episodes from journal/local/fills.csv.")
    p.add_argument("--fills", default="journal/local/fills.csv", help="Input fills CSV.")
    p.add_argument("--trades-out", default="journal/local/trades.csv", help="Output trades CSV (overwritten).")
    p.add_argument(
        "--rewrite-fills-with-trade-ids",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rewrite fills file with derived trade_id filled in (default: true).",
    )
    p.add_argument(
        "--episode-gap-hours",
        type=float,
        default=24.0,
        help="If time between fills exceeds this, start a new trade episode (default: 24h).",
    )
    return p.parse_args()


def _parse_iso8601(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _read_fills(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise SystemExit(f"Fills file not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit(f"Empty fills file: {path}")
        rows = [row for row in reader]
        return list(reader.fieldnames), rows


def _to_float(value: str) -> float:
    v = (value or "").strip()
    if v == "":
        return 0.0
    return float(v)


def _normalize_side(side: str) -> str:
    s = (side or "").strip().lower()
    if s in {"buy", "sell"}:
        return s
    return s


def parse_fill(row: dict[str, str]) -> Fill:
    return Fill(
        fill_id=(row.get("fill_id") or "").strip(),
        time_utc=_parse_iso8601(row.get("time_utc") or ""),
        symbol=(row.get("symbol") or "").strip(),
        strategy=(row.get("strategy") or "").strip(),
        side=_normalize_side(row.get("side") or ""),
        price=_to_float(row.get("price") or ""),
        qty_base=_to_float(row.get("qty_base") or ""),
        qty_quote=_to_float(row.get("qty_quote") or ""),
        fee_quote=_to_float(row.get("fee_quote") or ""),
        liquidity=(row.get("liquidity") or "").strip(),
        order_id=(row.get("order_id") or "").strip(),
        trade_id=(row.get("trade_id") or "").strip(),
    )


def derive_trade_episodes(
    fills: list[Fill],
    *,
    episode_gap_hours: float,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    # Returns:
    # - fill_id -> derived trade_id mapping
    # - trades summary rows (CSV dicts)
    fills_sorted = sorted(fills, key=lambda f: (f.symbol, f.strategy, f.time_utc, f.fill_id))

    fill_to_trade: dict[str, str] = {}
    trades_rows: list[dict[str, str]] = []

    current_symbol: str | None = None
    current_strategy: str | None = None
    episode_index = 0
    last_time: datetime | None = None

    pos_qty = 0.0
    cash_flow = 0.0
    entry_qty = 0.0
    entry_notional = 0.0
    exit_qty = 0.0
    exit_notional = 0.0
    entry_fees = 0.0
    exit_fees = 0.0
    first_time: datetime | None = None
    last_fill_time: datetime | None = None
    direction: str | None = None

    def close_episode() -> None:
        nonlocal pos_qty, cash_flow, entry_qty, entry_notional, exit_qty, exit_notional
        nonlocal entry_fees, exit_fees, first_time, last_fill_time, direction

        if (
            current_symbol is None
            or current_strategy is None
            or first_time is None
            or last_fill_time is None
            or episode_index == 0
        ):
            return

        safe_symbol = current_symbol.replace("/", "-")
        safe_strategy = current_strategy.replace(" ", "_") if current_strategy else "unknown"
        trade_id = f"{safe_symbol}-{safe_strategy}-{first_time.date().isoformat()}-{episode_index:03d}"
        realized_pnl = cash_flow if abs(pos_qty) < 1e-12 else ""

        entry_avg = (entry_notional / entry_qty) if entry_qty > 0 else ""
        exit_avg = (exit_notional / exit_qty) if exit_qty > 0 else ""

        holding_hours = (last_fill_time - first_time).total_seconds() / 3600.0

        trades_rows.append(
            {
                "trade_id": trade_id,
                "created_at_utc": first_time.isoformat().replace("+00:00", "Z"),
                "updated_at_utc": last_fill_time.isoformat().replace("+00:00", "Z"),
                "venue": "coinbase",
                "account": "",
                "symbol": current_symbol,
                "product_type": "spot",
                "timeframe": "",
                "strategy": current_strategy or "",
                "setup": "",
                "thesis": "",
                "direction": direction or "",
                "expected_liquidity": "",
                "planned_entry_type": "",
                "planned_entry_price": "",
                "planned_stop_price": "",
                "planned_take_profit_price": "",
                "planned_size_quote": "",
                "planned_risk_quote": "",
                "planned_r_multiple_target": "",
                "entry_time_utc": first_time.isoformat().replace("+00:00", "Z"),
                "entry_avg_price": f"{entry_avg:.10f}".rstrip("0").rstrip(".") if entry_avg != "" else "",
                "entry_qty_base": f"{entry_qty:.10f}".rstrip("0").rstrip(".") if entry_qty else "",
                "entry_qty_quote": f"{entry_notional:.10f}".rstrip("0").rstrip(".") if entry_notional else "",
                "entry_fee_quote": f"{entry_fees:.10f}".rstrip("0").rstrip(".") if entry_fees else "",
                "exit_time_utc": last_fill_time.isoformat().replace("+00:00", "Z") if exit_qty > 0 else "",
                "exit_avg_price": f"{exit_avg:.10f}".rstrip("0").rstrip(".") if exit_avg != "" else "",
                "exit_qty_base": f"{exit_qty:.10f}".rstrip("0").rstrip(".") if exit_qty else "",
                "exit_qty_quote": f"{exit_notional:.10f}".rstrip("0").rstrip(".") if exit_notional else "",
                "exit_fee_quote": f"{exit_fees:.10f}".rstrip("0").rstrip(".") if exit_fees else "",
                "realized_pnl_quote": (
                    f"{float(realized_pnl):.10f}".rstrip("0").rstrip(".") if realized_pnl != "" else ""
                ),
                "realized_pnl_pct": "",
                "realized_r_multiple": "",
                "max_favorable_excursion_pct": "",
                "max_adverse_excursion_pct": "",
                "holding_time_hours": f"{holding_hours:.2f}",
                "rules_followed": "",
                "rule_violations": "",
                "tags": "",
                "notes": "",
                "post_mortem": "",
            }
        )

        # Reset episode state
        pos_qty = 0.0
        cash_flow = 0.0
        entry_qty = 0.0
        entry_notional = 0.0
        exit_qty = 0.0
        exit_notional = 0.0
        entry_fees = 0.0
        exit_fees = 0.0
        first_time = None
        last_fill_time = None
        direction = None

    gap_seconds = float(episode_gap_hours) * 3600.0

    for f in fills_sorted:
        if not f.symbol:
            continue

        symbol_changed = current_symbol is not None and f.symbol != current_symbol
        strategy_changed = current_strategy is not None and f.strategy != current_strategy
        time_gap = last_time is not None and (f.time_utc - last_time).total_seconds() > gap_seconds

        if current_symbol is None:
            current_symbol = f.symbol
            current_strategy = f.strategy
            episode_index = 1
        elif symbol_changed or strategy_changed or time_gap:
            close_episode()
            current_symbol = f.symbol
            current_strategy = f.strategy
            episode_index = 1

        # Start first timestamp for this episode
        if first_time is None:
            first_time = f.time_utc

        last_fill_time = f.time_utc
        last_time = f.time_utc

        # Determine direction from first fill
        if direction is None:
            direction = "long" if f.side == "buy" else "short"

        # Apply cash flows (quote currency), including fees.
        if f.side == "buy":
            pos_qty += f.qty_base
            cash_flow -= f.qty_quote
            cash_flow -= f.fee_quote
            entry_qty += f.qty_base
            entry_notional += f.qty_quote
            entry_fees += f.fee_quote
        elif f.side == "sell":
            pos_qty -= f.qty_base
            cash_flow += f.qty_quote
            cash_flow -= f.fee_quote
            exit_qty += f.qty_base
            exit_notional += f.qty_quote
            exit_fees += f.fee_quote

        safe_symbol = current_symbol.replace("/", "-")
        safe_strategy = current_strategy.replace(" ", "_") if current_strategy else "unknown"
        trade_id = f"{safe_symbol}-{safe_strategy}-{first_time.date().isoformat()}-{episode_index:03d}"
        fill_to_trade[f.fill_id] = trade_id

        # Close trade when flat again (spot-style trade episode)
        if abs(pos_qty) < 1e-12 and (entry_qty > 0 or exit_qty > 0):
            close_episode()
            episode_index += 1

    close_episode()
    return fill_to_trade, trades_rows


def _write_csv(path: Path, *, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> None:
    args = _parse_args()
    fills_path = Path(args.fills)
    trades_out = Path(args.trades_out)

    fills_cols, fills_rows = _read_fills(fills_path)
    fills = [parse_fill(r) for r in fills_rows if (r.get("fill_id") or "").strip()]

    fill_to_trade, trades_rows = derive_trade_episodes(fills, episode_gap_hours=float(args.episode_gap_hours))

    if args.rewrite_fills_with_trade_ids and "trade_id" in fills_cols:
        updated_rows: list[dict[str, str]] = []
        for row in fills_rows:
            fid = (row.get("fill_id") or "").strip()
            if fid and (row.get("trade_id") or "").strip() == "" and fid in fill_to_trade:
                row = dict(row)
                row["trade_id"] = fill_to_trade[fid]
            updated_rows.append(row)
        _write_csv(fills_path, fieldnames=fills_cols, rows=updated_rows)

    trades_template = Path("journal/templates/trades.csv")
    if not trades_template.exists():
        raise SystemExit(f"Missing template: {trades_template}")
    with trades_template.open("r", encoding="utf-8", newline="") as f:
        trades_fieldnames = next(csv.reader(f))

    _write_csv(trades_out, fieldnames=trades_fieldnames, rows=trades_rows)
    print(f"Wrote derived trades: {trades_out} ({len(trades_rows)} row(s))")


if __name__ == "__main__":
    main()
