from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from research.data_contract import apply_gap_policy, infer_granularity_seconds, load_ohlcv_csv
from research.data_loader import DataSpec, find_processed_csv

@dataclass(frozen=True)
class BacktestConfig:
    csv_path: Path
    granularity_seconds: int
    fast_sma: int
    slow_sma: int
    fee_bps: float
    slippage_bps: float
    gap_cooldown_bars: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hello-world backtest (buy&hold vs SMA crossover).")
    parser.add_argument(
        "--csv",
        default=None,
        help="Path to processed CSV (default: newest match in data/processed).",
    )
    parser.add_argument("--symbol", default=None, help="Optional symbol filter (e.g., BTC-USD).")
    parser.add_argument("--granularity-seconds", type=int, default=None, help="Override candle granularity in seconds.")
    parser.add_argument("--fast-sma", type=int, default=20, help="Fast SMA window (default: 20).")
    parser.add_argument("--slow-sma", type=int, default=100, help="Slow SMA window (default: 100).")
    parser.add_argument("--fee-bps", type=float, default=10.0, help="Fee in bps per trade (default: 10).")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage in bps per trade (default: 5).")
    parser.add_argument(
        "--gap-policy",
        choices=["skip", "forward_fill", "segment"],
        default="segment",
        help="Gap handling policy (default: segment).",
    )
    parser.add_argument(
        "--gap-cooldown-bars",
        type=int,
        default=1,
        help="Bars to block position changes after a detected gap (default: 1).",
    )
    return parser.parse_args()


def _find_latest_processed_csv() -> Path:
    return find_processed_csv(DataSpec())


@dataclass(frozen=True)
class BacktestResult:
    name: str
    equity: pd.Series
    returns: pd.Series
    trades: int


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


def backtest_buy_and_hold(df: pd.DataFrame) -> BacktestResult:
    close = df["close"].astype("float64")
    ret = close.pct_change().fillna(0.0)
    equity = (1.0 + ret).cumprod()
    return BacktestResult(name="buy_and_hold", equity=equity, returns=ret, trades=0)


def backtest_sma_crossover(df: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    if cfg.fast_sma <= 0 or cfg.slow_sma <= 0:
        raise ValueError("SMA windows must be > 0")
    if cfg.fast_sma >= cfg.slow_sma:
        raise ValueError("fast_sma must be < slow_sma")

    close = df["close"].astype("float64")
    ret = close.pct_change().fillna(0.0)

    sma_fast = close.rolling(cfg.fast_sma, min_periods=cfg.fast_sma).mean()
    sma_slow = close.rolling(cfg.slow_sma, min_periods=cfg.slow_sma).mean()

    raw_signal = (sma_fast > sma_slow).astype("float64")
    desired_position = raw_signal.shift(1).fillna(0.0)

    gap, _ = compute_gaps(df, granularity_seconds=cfg.granularity_seconds)
    blocked = pd.Series(False, index=df.index)
    if cfg.gap_cooldown_bars < 0:
        raise ValueError("gap_cooldown_bars must be >= 0")
    cooldown = int(cfg.gap_cooldown_bars)
    if cooldown > 0:
        for idx in gap[gap].index:
            blocked.loc[idx : min(idx + cooldown - 1, len(df) - 1)] = True
    else:
        blocked = gap.copy()

    cost_rate = (cfg.fee_bps + cfg.slippage_bps) / 10_000.0

    equity = np.empty(len(df), dtype=np.float64)
    equity[0] = 1.0
    position = 0.0
    trades = 0

    for i in range(1, len(df)):
        target = position if bool(blocked.iat[i]) else float(desired_position.iat[i])
        target = 0.0 if math.isnan(target) else float(np.clip(target, 0.0, 1.0))

        equity_after_cost = float(equity[i - 1])
        if target != position:
            equity_after_cost *= 1.0 - (cost_rate * abs(target - position))
            trades += 1
            position = target

        equity[i] = equity_after_cost * (1.0 + position * float(ret.iat[i]))

    equity_s = pd.Series(equity, index=df.index)
    strat_ret = equity_s.pct_change().fillna(0.0)
    return BacktestResult(name="sma_crossover", equity=equity_s, returns=strat_ret, trades=trades)


def print_summary(result: BacktestResult, *, start: pd.Timestamp, end: pd.Timestamp, periods_per_year: float) -> None:
    total_return = float(result.equity.iloc[-1] - 1.0)
    cagr = _cagr(result.equity, start=start, end=end)
    mdd = _max_drawdown(result.equity)
    sharpe = _sharpe(result.returns, periods_per_year=periods_per_year)
    print(
        f"{result.name}: "
        f"total_return={total_return:.2%} "
        f"cagr={cagr:.2%} "
        f"max_drawdown={mdd:.2%} "
        f"sharpe={sharpe:.2f} "
        f"trades={result.trades}"
    )


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
        fast_sma=int(args.fast_sma),
        slow_sma=int(args.slow_sma),
        fee_bps=float(args.fee_bps),
        slippage_bps=float(args.slippage_bps),
        gap_cooldown_bars=int(args.gap_cooldown_bars),
    )

    segments, gap_ranges = apply_gap_policy(
        df,
        granularity_seconds=cfg.granularity_seconds,
        policy=str(args.gap_policy),
    )
    missing_candles = int(sum(((end - start).total_seconds() / cfg.granularity_seconds) + 1 for start, end in gap_ranges))

    start = df.loc[0, "time"]
    end = df.loc[len(df) - 1, "time"]
    periods_per_year = (365.25 * 24 * 3600) / cfg.granularity_seconds

    print(f"Dataset: {cfg.csv_path}")
    print(f"Rows: {len(df)}  Start: {start}  End: {end}  Granularity: {cfg.granularity_seconds}s")
    print(f"Gap policy: {args.gap_policy}")
    if gap_ranges:
        print(f"Gaps: {len(gap_ranges)}  Missing candles (approx): {missing_candles}")
        for s, e in gap_ranges[:5]:
            print(f"- missing: {s} → {e}")
    else:
        print("Gaps: 0")

    if str(args.gap_policy) == "segment":
        if segments:
            df = max(segments, key=len)
    else:
        df = segments[0] if segments else df

    bh = backtest_buy_and_hold(df)
    sma = backtest_sma_crossover(df, cfg)

    print_summary(bh, start=start, end=end, periods_per_year=periods_per_year)
    print_summary(sma, start=start, end=end, periods_per_year=periods_per_year)


if __name__ == "__main__":
    main()
