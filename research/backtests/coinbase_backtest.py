from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from research.backtests.engine import BacktestResult, FillEvent, run_target_position
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
    parser.add_argument("--symbol", default=None, help="Optional symbol filter (e.g., BTC-USD).")
    parser.add_argument("--granularity-seconds", type=int, default=None, help="Override candle granularity in seconds.")

    parser.add_argument("--execution", choices=["maker", "taker"], default="taker", help="Assumed execution type (default: taker).")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage model in bps (default: 5).")
    parser.add_argument("--gap-cooldown-bars", type=int, default=1, help="Bars to block trades after a detected gap (default: 1).")
    parser.add_argument(
        "--gap-policy",
        choices=["skip", "forward_fill", "segment"],
        default="segment",
        help="Gap handling policy (default: segment).",
    )

    # Coinbase VIP 4 spot fees (user-provided): 0.025% maker | 0.065% taker
    parser.add_argument("--maker-fee-bps", type=float, default=2.5, help="Maker fee in bps (default: 2.5).")
    parser.add_argument("--taker-fee-bps", type=float, default=6.5, help="Taker fee in bps (default: 6.5).")

    parser.add_argument("--fast-sma", type=int, default=20, help="Fast SMA window (default: 20).")
    parser.add_argument("--slow-sma", type=int, default=100, help="Slow SMA window (default: 100).")
    parser.add_argument(
        "--strategy",
        default="sma_crossover",
        help="Strategy name (default: sma_crossover).",
    )

    parser.add_argument("--initial-cash", type=float, default=1.0, help="Initial cash in quote currency units (default: 1.0).")
    return parser.parse_args()


def _find_latest_processed_csv() -> Path:
    return find_processed_csv(DataSpec())


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
    fills = [
        FillEvent(
            time_utc=pd.Timestamp(df.loc[0, "time"]),
            strategy="buy_and_hold",
            side="buy",
            price=entry_price,
            qty_base=shares,
            qty_quote=shares * entry_price,
            fee_quote=fee_paid,
        )
    ]

    equity = (cash + shares * df["close"].astype("float64")).reset_index(drop=True)
    returns = equity.pct_change().fillna(0.0)
    return BacktestResult(
        name="buy_and_hold",
        equity=equity,
        returns=returns,
        trades=trades,
        fees_paid=fees_paid,
        fills=fills,
    )


def backtest_strategy(df: pd.DataFrame, cfg: BacktestConfig, *, strategy_name: str) -> BacktestResult:
    if cfg.initial_cash <= 0:
        raise ValueError("initial_cash must be > 0")

    strategy = get_strategy(strategy_name)
    if strategy.name == "sma_crossover":
        strategy_cfg = SmaCrossoverConfig(fast_window=cfg.fast_sma, slow_window=cfg.slow_sma)
    else:
        strategy_cfg = strategy.config_type()

    desired = strategy.generate_target_position(df, strategy_cfg).astype("int8")
    fee_rate = cfg.fee_schedule.fee_rate(execution=cfg.execution)
    return run_target_position(
        df,
        target_position=desired,
        initial_cash=cfg.initial_cash,
        fee_rate=fee_rate,
        slippage_bps=cfg.slippage_bps,
        granularity_seconds=cfg.granularity_seconds,
        gap_cooldown_bars=cfg.gap_cooldown_bars,
        strategy_name=strategy.name,
    )


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

    start = df.loc[0, "time"]
    end = df.loc[len(df) - 1, "time"]
    periods_per_year = (365.25 * 24 * 3600) / cfg.granularity_seconds

    segments, gap_ranges = apply_gap_policy(
        df,
        granularity_seconds=cfg.granularity_seconds,
        policy=str(args.gap_policy),
    )
    missing_candles = int(sum(((e - s).total_seconds() / cfg.granularity_seconds) + 1 for s, e in gap_ranges))

    print(f"Dataset: {cfg.csv_path}")
    print(f"Rows: {len(df)}  Start: {start}  End: {end}  Granularity: {cfg.granularity_seconds}s")
    print(f"Gap policy: {args.gap_policy}")
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

    if str(args.gap_policy) == "segment":
        if segments:
            df = max(segments, key=len)
    else:
        df = segments[0] if segments else df

    bh = backtest_buy_and_hold(df, cfg)
    strat = backtest_strategy(df, cfg, strategy_name=str(args.strategy))

    print_summary(bh, start=start, end=end, periods_per_year=periods_per_year, initial_cash=cfg.initial_cash)
    print_summary(strat, start=start, end=end, periods_per_year=periods_per_year, initial_cash=cfg.initial_cash)


if __name__ == "__main__":
    main()
