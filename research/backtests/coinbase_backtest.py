from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


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


@dataclass(frozen=True)
class BacktestConfig:
    csv_path: Path
    granularity_seconds: int
    fee_schedule: FeeSchedule
    execution: str
    slippage_bps: float
    gap_cooldown_bars: int
    fast_sma: int
    slow_sma: int
    initial_cash: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coinbase backtest (fee-aware execution model).")
    parser.add_argument("--csv", default=None, help="Path to processed OHLCV CSV (default: newest match in data/processed).")
    parser.add_argument("--granularity-seconds", type=int, default=None, help="Override candle granularity in seconds.")

    parser.add_argument("--execution", choices=["maker", "taker"], default="taker", help="Assumed execution type (default: taker).")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage model in bps (default: 5).")
    parser.add_argument("--gap-cooldown-bars", type=int, default=1, help="Bars to block trades after a detected gap (default: 1).")

    # Coinbase VIP 4 spot fees (user-provided): 0.025% maker | 0.065% taker
    parser.add_argument("--maker-fee-bps", type=float, default=2.5, help="Maker fee in bps (default: 2.5).")
    parser.add_argument("--taker-fee-bps", type=float, default=6.5, help="Taker fee in bps (default: 6.5).")

    parser.add_argument("--fast-sma", type=int, default=20, help="Fast SMA window (default: 20).")
    parser.add_argument("--slow-sma", type=int, default=100, help="Slow SMA window (default: 100).")

    parser.add_argument("--initial-cash", type=float, default=1.0, help="Initial cash in quote currency units (default: 1.0).")
    return parser.parse_args()


def _find_latest_processed_csv() -> Path:
    processed_dir = Path("data/processed")
    candidates = sorted(processed_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("No CSVs found in data/processed. Run `python3 research/fetch_history.py` first.")
    return candidates[0]


def load_ohlcv_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise SystemExit(f"CSV missing required columns: {sorted(missing)}")

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype("float64")

    return df


def _infer_granularity_seconds(path: Path, df: pd.DataFrame) -> int:
    m = re.search(r"_(\d+)s_", path.name)
    if m:
        return int(m.group(1))

    diffs = df["time"].diff().dropna().dt.total_seconds()
    if diffs.empty:
        raise ValueError("cannot infer granularity (no diffs)")
    mode = diffs.mode()
    if mode.empty:
        raise ValueError("cannot infer granularity (no mode)")
    return int(mode.iloc[0])


def compute_gaps(df: pd.DataFrame, *, granularity_seconds: int) -> tuple[pd.Series, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    freq = pd.Timedelta(seconds=granularity_seconds)
    diffs = df["time"].diff()
    gap = diffs > freq

    gap_ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for idx in gap[gap].index.tolist():
        prev_ts = df.loc[idx - 1, "time"]
        ts = df.loc[idx, "time"]
        missing_start = prev_ts + freq
        missing_end = ts - freq
        gap_ranges.append((missing_start, missing_end))

    return gap.fillna(False), gap_ranges


@dataclass(frozen=True)
class BacktestResult:
    name: str
    equity: pd.Series
    returns: pd.Series
    trades: int
    fees_paid: float


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


def _blocked_trade_mask(gap: pd.Series, *, cooldown_bars: int) -> pd.Series:
    if cooldown_bars < 0:
        raise ValueError("gap_cooldown_bars must be >= 0")
    blocked = pd.Series(False, index=gap.index)
    if cooldown_bars == 0:
        return blocked
    for idx in gap[gap].index:
        blocked.loc[idx : min(idx + cooldown_bars - 1, len(blocked) - 1)] = True
    return blocked


def backtest_buy_and_hold(df: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    fee_rate = cfg.fee_schedule.fee_rate(execution=cfg.execution)
    s = cfg.slippage_bps / 10_000.0

    cash = float(cfg.initial_cash)
    shares = 0.0
    fees_paid = 0.0
    trades = 0

    entry_price = float(df.loc[0, "open"]) * (1.0 + s)
    if entry_price <= 0:
        raise ValueError("invalid entry price")
    shares = cash / (entry_price * (1.0 + fee_rate))
    fee_paid = shares * entry_price * fee_rate
    fees_paid += fee_paid
    cash = 0.0
    trades += 1

    equity = (cash + shares * df["close"].astype("float64")).reset_index(drop=True)
    returns = equity.pct_change().fillna(0.0)
    return BacktestResult(name="buy_and_hold", equity=equity, returns=returns, trades=trades, fees_paid=fees_paid)


def backtest_sma_crossover(df: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    if cfg.fast_sma <= 0 or cfg.slow_sma <= 0:
        raise ValueError("SMA windows must be > 0")
    if cfg.fast_sma >= cfg.slow_sma:
        raise ValueError("fast_sma must be < slow_sma")
    if cfg.initial_cash <= 0:
        raise ValueError("initial_cash must be > 0")

    fee_rate = cfg.fee_schedule.fee_rate(execution=cfg.execution)
    s = cfg.slippage_bps / 10_000.0

    close = df["close"].astype("float64")
    sma_fast = close.rolling(cfg.fast_sma, min_periods=cfg.fast_sma).mean()
    sma_slow = close.rolling(cfg.slow_sma, min_periods=cfg.slow_sma).mean()
    desired = (sma_fast > sma_slow).astype("int8").shift(1).fillna(0).astype("int8")

    gap, _ = compute_gaps(df, granularity_seconds=cfg.granularity_seconds)
    blocked = _blocked_trade_mask(gap, cooldown_bars=cfg.gap_cooldown_bars)

    cash = float(cfg.initial_cash)
    shares = 0.0
    fees_paid = 0.0
    trades = 0

    equity = np.empty(len(df), dtype=np.float64)

    for i in range(len(df)):
        equity[i] = cash + shares * float(df.loc[i, "close"])

        if i == len(df) - 1:
            break

        if bool(blocked.iat[i + 1]):
            continue

        target = int(desired.iat[i])
        next_open = float(df.loc[i + 1, "open"])
        if next_open <= 0:
            continue

        if target == 1 and shares == 0.0:
            exec_price = next_open * (1.0 + s)
            shares = cash / (exec_price * (1.0 + fee_rate))
            fee_paid = shares * exec_price * fee_rate
            fees_paid += fee_paid
            cash = 0.0
            trades += 1
        elif target == 0 and shares > 0.0:
            exec_price = next_open * (1.0 - s)
            gross = shares * exec_price
            fee_paid = gross * fee_rate
            cash = gross - fee_paid
            fees_paid += fee_paid
            shares = 0.0
            trades += 1

    equity_s = pd.Series(equity)
    returns = equity_s.pct_change().fillna(0.0)
    return BacktestResult(name="sma_crossover", equity=equity_s, returns=returns, trades=trades, fees_paid=fees_paid)


def print_summary(result: BacktestResult, *, start: pd.Timestamp, end: pd.Timestamp, periods_per_year: float, initial_cash: float) -> None:
    total_return = float((result.equity.iloc[-1] / initial_cash) - 1.0)
    cagr = _cagr(result.equity / initial_cash, start=start, end=end)
    mdd = _max_drawdown(result.equity)
    sharpe = _sharpe(result.returns, periods_per_year=periods_per_year)
    fees_pct = (result.fees_paid / initial_cash) if initial_cash != 0 else float("nan")
    print(
        f"{result.name}: "
        f"total_return={total_return:.2%} "
        f"cagr={cagr:.2%} "
        f"max_drawdown={mdd:.2%} "
        f"sharpe={sharpe:.2f} "
        f"trades={result.trades} "
        f"fees_paid={fees_pct:.2%}"
    )


def main() -> None:
    args = _parse_args()
    csv_path = Path(args.csv) if args.csv else _find_latest_processed_csv()
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    df = load_ohlcv_csv(csv_path)
    granularity_seconds = int(args.granularity_seconds) if args.granularity_seconds else _infer_granularity_seconds(csv_path, df)

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

    start = df.loc[0, "time"]
    end = df.loc[len(df) - 1, "time"]
    periods_per_year = (365.25 * 24 * 3600) / cfg.granularity_seconds

    gap, gap_ranges = compute_gaps(df, granularity_seconds=cfg.granularity_seconds)
    missing_candles = int(sum(((e - s).total_seconds() / cfg.granularity_seconds) + 1 for s, e in gap_ranges))

    print(f"Dataset: {cfg.csv_path}")
    print(f"Rows: {len(df)}  Start: {start}  End: {end}  Granularity: {cfg.granularity_seconds}s")
    if gap_ranges:
        print(f"Gaps: {len(gap_ranges)}  Missing candles (approx): {missing_candles}")
        for s, e in gap_ranges[:5]:
            print(f"- missing: {s} → {e}")
    else:
        print("Gaps: 0")

    print(
        "Costs: "
        f"execution={cfg.execution} "
        f"maker_fee={cfg.fee_schedule.maker_fee_bps:.2f}bps "
        f"taker_fee={cfg.fee_schedule.taker_fee_bps:.2f}bps "
        f"slippage={cfg.slippage_bps:.2f}bps"
    )

    bh = backtest_buy_and_hold(df, cfg)
    sma = backtest_sma_crossover(df, cfg)

    print_summary(bh, start=start, end=end, periods_per_year=periods_per_year, initial_cash=cfg.initial_cash)
    print_summary(sma, start=start, end=end, periods_per_year=periods_per_year, initial_cash=cfg.initial_cash)


if __name__ == "__main__":
    main()

