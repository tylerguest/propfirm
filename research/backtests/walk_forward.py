from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from research.backtests.coinbase_backtest import (
    BacktestConfig,
    FeeSchedule,
    backtest_buy_and_hold,
    backtest_strategy,
)
from research.data_contract import apply_gap_policy, infer_granularity_seconds, load_ohlcv_csv
from research.data_loader import DataSpec, find_processed_csv
from research.strategies.registry import list_strategies


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward backtest runner (rolling train/test splits).")
    p.add_argument("--csv", default=None, help="Path to processed OHLCV CSV (default: newest match).")
    p.add_argument("--symbol", default=None, help="Optional symbol filter (e.g., BTC-USD).")
    p.add_argument("--granularity-seconds", type=int, default=None, help="Override candle granularity in seconds.")
    p.add_argument("--train-years", type=int, default=2, help="Train window in years (default: 2).")
    p.add_argument("--test-years", type=int, default=1, help="Test window in years (default: 1).")
    p.add_argument("--step-years", type=int, default=1, help="Step size in years (default: 1).")
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
    p.add_argument("--save-artifacts", action=argparse.BooleanOptionalAction, default=True, help="Save outputs.")
    p.add_argument("--output-dir", default="research/output/walk_forward", help="Output directory.")
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
    return float((r.mean() / std) * math.sqrt(periods_per_year))


def _cagr(equity: pd.Series, *, start: pd.Timestamp, end: pd.Timestamp) -> float:
    years = (end - start).total_seconds() / (365.25 * 24 * 3600)
    if years <= 0:
        return float("nan")
    return float(equity.iloc[-1] ** (1.0 / years) - 1.0)


def _metrics_row(
    *,
    split_id: str,
    phase: str,
    name: str,
    equity: pd.Series,
    returns: pd.Series,
    trades: int,
    fees_paid: float,
    initial_cash: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
    periods_per_year: float,
) -> dict[str, str]:
    total_return = float((equity.iloc[-1] / initial_cash) - 1.0)
    cagr = _cagr(equity / initial_cash, start=start, end=end)
    mdd = _max_drawdown(equity)
    sharpe = _sharpe(returns, periods_per_year=periods_per_year)
    fees_pct = (fees_paid / initial_cash) if initial_cash != 0 else float("nan")
    return {
        "split_id": split_id,
        "phase": phase,
        "strategy": name,
        "start": pd.Timestamp(start).isoformat().replace("+00:00", "Z"),
        "end": pd.Timestamp(end).isoformat().replace("+00:00", "Z"),
        "total_return": f"{total_return:.6f}",
        "cagr": f"{cagr:.6f}",
        "max_drawdown": f"{mdd:.6f}",
        "sharpe": f"{sharpe:.6f}",
        "trades": str(trades),
        "fees_paid_pct": f"{fees_pct:.6f}",
    }


def _build_splits(
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    train_years: int,
    test_years: int,
    step_years: int,
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    splits: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    while True:
        train_end = cursor + pd.DateOffset(years=train_years)
        test_end = train_end + pd.DateOffset(years=test_years)
        if test_end > end:
            break
        splits.append((cursor, train_end, test_end))
        cursor = cursor + pd.DateOffset(years=step_years)
    return splits


def _run_slice(df: pd.DataFrame, cfg: BacktestConfig) -> dict[str, object]:
    results: dict[str, object] = {}
    results["buy_and_hold"] = backtest_buy_and_hold(df, cfg)
    for name in list_strategies():
        results[name] = backtest_strategy(df, cfg, strategy_name=name)
    return results


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

    cfg = BacktestConfig(
        csv_path=csv_path,
        granularity_seconds=granularity_seconds,
        fee_schedule=FeeSchedule(maker_fee_bps=float(args.maker_fee_bps), taker_fee_bps=float(args.taker_fee_bps)),
        execution=str(args.execution),
        slippage_bps=float(args.slippage_bps),
        gap_cooldown_bars=int(args.gap_cooldown_bars),
        fast_sma=int(args.fast_sma),
        slow_sma=int(args.slow_sma),
        initial_cash=float(args.initial_cash),
    )

    segments, gap_ranges = apply_gap_policy(
        df,
        granularity_seconds=cfg.granularity_seconds,
        policy=str(args.gap_policy),
    )
    if str(args.gap_policy) == "segment":
        if segments:
            df = max(segments, key=len)
    else:
        df = segments[0] if segments else df

    start = df["time"].min()
    end = df["time"].max()
    splits = _build_splits(
        start=start,
        end=end,
        train_years=int(args.train_years),
        test_years=int(args.test_years),
        step_years=int(args.step_years),
    )
    if not splits:
        required_years = int(args.train_years) + int(args.test_years)
        available_years = (end - start).total_seconds() / (365.25 * 24 * 3600)
        raise SystemExit(
            "No valid walk-forward splits for the selected date range. "
            f"Range={start.isoformat()} → {end.isoformat()} "
            f"({available_years:.2f}y) required≥{required_years}y. "
            f"gap_policy={args.gap_policy}"
        )

    periods_per_year = (365.25 * 24 * 3600) / cfg.granularity_seconds
    metrics_rows: list[dict[str, str]] = []

    test_slices: list[pd.DataFrame] = []
    for i, (train_start, train_end, test_end) in enumerate(splits, start=1):
        split_id = f"split_{i}"
        train_df = df[(df["time"] >= train_start) & (df["time"] < train_end)].reset_index(drop=True)
        test_df = df[(df["time"] >= train_end) & (df["time"] <= test_end)].reset_index(drop=True)
        if train_df.empty or test_df.empty:
            continue

        train_results = _run_slice(train_df, cfg)
        test_results = _run_slice(test_df, cfg)

        for name, result in train_results.items():
            metrics_rows.append(
                _metrics_row(
                    split_id=split_id,
                    phase="train",
                    name=name,
                    equity=result.equity,
                    returns=result.returns,
                    trades=result.trades,
                    fees_paid=result.fees_paid,
                    initial_cash=cfg.initial_cash,
                    start=train_df.loc[0, "time"],
                    end=train_df.loc[len(train_df) - 1, "time"],
                    periods_per_year=periods_per_year,
                )
            )
        for name, result in test_results.items():
            metrics_rows.append(
                _metrics_row(
                    split_id=split_id,
                    phase="test",
                    name=name,
                    equity=result.equity,
                    returns=result.returns,
                    trades=result.trades,
                    fees_paid=result.fees_paid,
                    initial_cash=cfg.initial_cash,
                    start=test_df.loc[0, "time"],
                    end=test_df.loc[len(test_df) - 1, "time"],
                    periods_per_year=periods_per_year,
                )
            )

        test_slices.append(test_df)

    # Combined test chaining across all test slices
    for name in ["buy_and_hold"] + list_strategies():
        equity_series: list[pd.Series] = []
        current_cash = cfg.initial_cash
        fees_paid = 0.0
        trades = 0
        for test_df in test_slices:
            cfg_slice = replace(cfg, initial_cash=current_cash)
            if name == "buy_and_hold":
                result = backtest_buy_and_hold(test_df, cfg_slice)
            else:
                result = backtest_strategy(test_df, cfg_slice, strategy_name=name)
            current_cash = float(result.equity.iloc[-1])
            fees_paid += result.fees_paid
            trades += result.trades
            equity_series.append(pd.Series(result.equity.values, index=test_df["time"]))

        if equity_series:
            combined_equity = pd.concat(equity_series)
            combined_returns = combined_equity.pct_change().fillna(0.0)
            metrics_rows.append(
                _metrics_row(
                    split_id="combined",
                    phase="test",
                    name=name,
                    equity=combined_equity,
                    returns=combined_returns,
                    trades=trades,
                    fees_paid=fees_paid,
                    initial_cash=cfg.initial_cash,
                    start=combined_equity.index.min(),
                    end=combined_equity.index.max(),
                    periods_per_year=periods_per_year,
                )
            )

    if args.save_artifacts:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(tz=UTC)
        ts = created_at.strftime("%Y%m%dT%H%M%SZ")
        symbol = str(args.symbol).strip() if args.symbol else "latest"
        start_date = pd.Timestamp(start).date().isoformat()
        end_date = pd.Timestamp(end).date().isoformat()
        run_id = f"{symbol}_{cfg.granularity_seconds}s_{start_date}_{end_date}_wf_{ts}"
        run_dir = out_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "config.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "symbol": symbol,
                    "csv_path": str(cfg.csv_path),
                    "granularity_seconds": cfg.granularity_seconds,
                    "data_start": pd.Timestamp(start).isoformat().replace("+00:00", "Z"),
                    "data_end": pd.Timestamp(end).isoformat().replace("+00:00", "Z"),
                    "gap_policy": str(args.gap_policy),
                    "execution": cfg.execution,
                    "maker_fee_bps": cfg.fee_schedule.maker_fee_bps,
                    "taker_fee_bps": cfg.fee_schedule.taker_fee_bps,
                    "slippage_bps": cfg.slippage_bps,
                    "gap_cooldown_bars": cfg.gap_cooldown_bars,
                    "fast_sma": cfg.fast_sma,
                    "slow_sma": cfg.slow_sma,
                    "initial_cash": cfg.initial_cash,
                    "train_years": args.train_years,
                    "test_years": args.test_years,
                    "step_years": args.step_years,
                    "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        with (run_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "split_id",
                    "phase",
                    "strategy",
                    "start",
                    "end",
                    "total_return",
                    "cagr",
                    "max_drawdown",
                    "sharpe",
                    "trades",
                    "fees_paid_pct",
                ],
            )
            writer.writeheader()
            for row in metrics_rows:
                writer.writerow(row)
        print(f"Wrote walk-forward artifacts: {run_dir}")


if __name__ == "__main__":
    main()
