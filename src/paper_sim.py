from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from research.backtests.engine import run_target_position
from research.data_contract import apply_gap_policy, infer_granularity_seconds, load_ohlcv_csv
from research.data_loader import DataSpec, find_processed_csv
from research.strategies.registry import get_strategy
from research.strategies.sma_crossover import SmaCrossoverConfig


@dataclass(frozen=True)
class FeeSchedule:
    maker_fee_bps: float
    taker_fee_bps: float

    def fee_rate(self, *, execution: str) -> float:
        if execution == "maker":
            return self.maker_fee_bps / 10_000.0
        if execution == "taker":
            return self.taker_fee_bps / 10_000.0
        raise ValueError("execution must be 'maker' or 'taker'")


FILL_COLUMNS = [
    "trade_id",
    "fill_id",
    "time_utc",
    "venue",
    "account",
    "symbol",
    "side",
    "order_type",
    "liquidity",
    "price",
    "qty_base",
    "qty_quote",
    "fee_quote",
    "fee_rate_bps",
    "order_id",
    "trade_type",
    "sequence_timestamp",
    "size_in_quote",
    "retail_portfolio_id",
    "notes",
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Paper/sim run using historical data (writes fills to journal).")
    p.add_argument("--csv", default=None, help="Path to processed OHLCV CSV (default: newest match).")
    p.add_argument("--symbol", default=None, help="Optional symbol filter (e.g., BTC-USD).")
    p.add_argument(
        "--strategy",
        default="sma_crossover",
        help="Strategy name (default: sma_crossover).",
    )
    p.add_argument("--granularity-seconds", type=int, default=None, help="Override candle granularity in seconds.")
    p.add_argument("--execution", choices=["maker", "taker"], default="taker", help="Assumed execution type (default: taker).")
    p.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage model in bps (default: 5).")
    p.add_argument("--gap-cooldown-bars", type=int, default=1, help="Bars to block trades after a detected gap (default: 1).")
    p.add_argument(
        "--gap-policy",
        choices=["skip", "forward_fill", "segment"],
        default="segment",
        help="Gap handling policy (default: segment).",
    )
    p.add_argument("--maker-fee-bps", type=float, default=2.5, help="Maker fee in bps (default: 2.5).")
    p.add_argument("--taker-fee-bps", type=float, default=6.5, help="Taker fee in bps (default: 6.5).")
    p.add_argument("--fast-sma", type=int, default=20, help="Fast SMA window (default: 20).")
    p.add_argument("--slow-sma", type=int, default=100, help="Slow SMA window (default: 100).")
    p.add_argument("--initial-cash", type=float, default=1.0, help="Initial cash in quote currency units (default: 1.0).")
    p.add_argument("--out-fills", default="journal/local/fills.csv", help="Fills CSV output path.")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False, help="Compute but do not write.")
    return p.parse_args()


def _ensure_header(path: Path) -> None:
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        if header is None:
            raise SystemExit(f"Fills file exists but is empty: {path}")
        if [c.strip() for c in header] != FILL_COLUMNS:
            raise SystemExit(f"Fills header mismatch in {path}. Expected: {FILL_COLUMNS}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(FILL_COLUMNS)


def main() -> None:
    args = _parse_args()
    if args.csv:
        csv_path = Path(args.csv)
    else:
        spec = DataSpec(symbol=str(args.symbol).strip() if args.symbol else None)
        csv_path = find_processed_csv(spec)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    df = load_ohlcv_csv(csv_path)
    granularity_seconds = int(args.granularity_seconds) if args.granularity_seconds else infer_granularity_seconds(csv_path, df)

    segments, _ = apply_gap_policy(
        df,
        granularity_seconds=granularity_seconds,
        policy=str(args.gap_policy),
    )
    if str(args.gap_policy) == "segment":
        if segments:
            df = max(segments, key=len)
    else:
        df = segments[0] if segments else df

    strategy = get_strategy(str(args.strategy))
    if strategy.name == "sma_crossover":
        strategy_cfg = SmaCrossoverConfig(fast_window=int(args.fast_sma), slow_window=int(args.slow_sma))
    else:
        strategy_cfg = strategy.config_type()

    target = strategy.generate_target_position(df, strategy_cfg)

    fees = FeeSchedule(maker_fee_bps=float(args.maker_fee_bps), taker_fee_bps=float(args.taker_fee_bps))
    fee_rate = fees.fee_rate(execution=str(args.execution))

    result = run_target_position(
        df,
        target_position=target,
        initial_cash=float(args.initial_cash),
        fee_rate=fee_rate,
        slippage_bps=float(args.slippage_bps),
        granularity_seconds=granularity_seconds,
        gap_cooldown_bars=int(args.gap_cooldown_bars),
        strategy_name=strategy.name,
    )

    if args.dry_run:
        print(f"Dry run: {len(result.fills)} fill(s) for strategy {strategy.name}")
        return

    out_path = Path(args.out_fills)
    _ensure_header(out_path)

    with out_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FILL_COLUMNS)
        for fill in result.fills:
            time_utc = pd.Timestamp(fill.time_utc).isoformat().replace("+00:00", "Z")
            fill_id = f"{strategy.name}:{time_utc}:{fill.side}"
            writer.writerow(
                {
                    "trade_id": "",
                    "fill_id": fill_id,
                    "time_utc": time_utc,
                    "venue": "paper",
                    "account": "paper",
                    "symbol": str(args.symbol).strip() if args.symbol else "",
                    "side": fill.side,
                    "order_type": "market",
                    "liquidity": str(args.execution),
                    "price": f"{fill.price:.10f}".rstrip("0").rstrip("."),
                    "qty_base": f"{fill.qty_base:.10f}".rstrip("0").rstrip("."),
                    "qty_quote": f"{fill.qty_quote:.10f}".rstrip("0").rstrip("."),
                    "fee_quote": f"{fill.fee_quote:.10f}".rstrip("0").rstrip("."),
                    "fee_rate_bps": f"{fee_rate * 10_000:.4f}".rstrip("0").rstrip("."),
                    "order_id": "paper",
                    "trade_type": "",
                    "sequence_timestamp": "",
                    "size_in_quote": "",
                    "retail_portfolio_id": "",
                    "notes": "paper_sim",
                }
            )

    print(f"Wrote {len(result.fills)} fill(s) to {out_path}")


if __name__ == "__main__":
    main()
