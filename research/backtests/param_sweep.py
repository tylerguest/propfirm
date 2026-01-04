from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.backtests.coinbase_backtest import BacktestConfig, FeeSchedule, backtest_strategy
from research.data_contract import apply_gap_policy, infer_granularity_seconds, load_ohlcv_csv
from research.data_loader import DataSpec, find_processed_csv


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Parameter sweep for SMA crossover.")
    p.add_argument("--csv", default=None, help="Path to processed OHLCV CSV (default: newest match).")
    p.add_argument("--symbol", default="BTC-USD", help="Symbol (default: BTC-USD).")
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
    p.add_argument("--fast", default="10,20,30", help="Fast SMA grid (comma-separated).")
    p.add_argument("--slow", default="80,100,150", help="Slow SMA grid (comma-separated).")
    p.add_argument("--initial-cash", type=float, default=1.0, help="Initial cash in quote currency units (default: 1.0).")
    p.add_argument("--out-dir", default="research/output/sweeps", help="Output directory.")
    p.add_argument("--start", default=None, help="UTC start datetime (ISO8601) to window the sweep.")
    p.add_argument("--end", default=None, help="UTC end datetime (ISO8601) to window the sweep.")
    return p.parse_args()


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _sharpe(returns: pd.Series, *, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 10:
        return float("nan")
    std = float(r.std(ddof=1))
    if std == 0.0:
        return float("nan")
    return float((r.mean() / std) * (periods_per_year**0.5))


def _cagr(equity: pd.Series, *, start: pd.Timestamp, end: pd.Timestamp) -> float:
    years = (end - start).total_seconds() / (365.25 * 24 * 3600)
    if years <= 0:
        return float("nan")
    return float(equity.iloc[-1] ** (1.0 / years) - 1.0)


def main() -> None:
    args = _parse_args()
    if args.csv:
        csv_path = Path(args.csv)
    else:
        csv_path = find_processed_csv(DataSpec(symbol=str(args.symbol).strip()))
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    df = load_ohlcv_csv(csv_path)
    granularity_seconds = int(args.granularity_seconds) if args.granularity_seconds else infer_granularity_seconds(csv_path, df)
    if args.start or args.end:
        df["time"] = pd.to_datetime(df["time"], utc=True)
        start_dt = pd.to_datetime(args.start, utc=True) if args.start else df["time"].min()
        end_dt = pd.to_datetime(args.end, utc=True) if args.end else df["time"].max()
        df = df[(df["time"] >= start_dt) & (df["time"] <= end_dt)].reset_index(drop=True)
        if df.empty:
            raise SystemExit("No candles available in the requested window.")

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

    start = df.loc[0, "time"]
    end = df.loc[len(df) - 1, "time"]
    periods_per_year = (365.25 * 24 * 3600) / granularity_seconds

    fee_schedule = FeeSchedule(maker_fee_bps=float(args.maker_fee_bps), taker_fee_bps=float(args.taker_fee_bps))

    fast_grid = [int(x.strip()) for x in str(args.fast).split(",") if x.strip()]
    slow_grid = [int(x.strip()) for x in str(args.slow).split(",") if x.strip()]

    rows: list[dict[str, str]] = []
    for fast in fast_grid:
        for slow in slow_grid:
            if fast >= slow:
                continue
            cfg = BacktestConfig(
                csv_path=csv_path,
                granularity_seconds=granularity_seconds,
                fee_schedule=fee_schedule,
                execution=str(args.execution),
                slippage_bps=float(args.slippage_bps),
                gap_cooldown_bars=int(args.gap_cooldown_bars),
                fast_sma=int(fast),
                slow_sma=int(slow),
                initial_cash=float(args.initial_cash),
            )
            result = backtest_strategy(df, cfg, strategy_name="sma_crossover")
            total_return = float((result.equity.iloc[-1] / cfg.initial_cash) - 1.0)
            cagr = _cagr(result.equity / cfg.initial_cash, start=start, end=end)
            mdd = _max_drawdown(result.equity)
            sharpe = _sharpe(result.returns, periods_per_year=periods_per_year)
            fees_pct = (result.fees_paid / cfg.initial_cash) if cfg.initial_cash != 0 else float("nan")
            rows.append(
                {
                    "fast_sma": str(fast),
                    "slow_sma": str(slow),
                    "total_return": f"{total_return:.6f}",
                    "cagr": f"{cagr:.6f}",
                    "max_drawdown": f"{mdd:.6f}",
                    "sharpe": f"{sharpe:.6f}",
                    "trades": str(result.trades),
                    "fees_paid_pct": f"{fees_pct:.6f}",
                }
            )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{str(args.symbol).strip()}_sma_crossover_{ts}.csv"

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "fast_sma",
                "slow_sma",
                "total_return",
                "cagr",
                "max_drawdown",
                "sharpe",
                "trades",
                "fees_paid_pct",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote sweep: {out_path}")


if __name__ == "__main__":
    main()
