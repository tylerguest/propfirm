from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.data_contract import compute_gaps


@dataclass(frozen=True)
class FillEvent:
    time_utc: pd.Timestamp
    strategy: str
    side: str
    price: float
    qty_base: float
    qty_quote: float
    fee_quote: float


@dataclass(frozen=True)
class BacktestResult:
    name: str
    equity: pd.Series
    returns: pd.Series
    trades: int
    fees_paid: float
    fills: list[FillEvent]


def _blocked_trade_mask(gap: pd.Series, *, cooldown_bars: int) -> pd.Series:
    if cooldown_bars < 0:
        raise ValueError("gap_cooldown_bars must be >= 0")
    blocked = pd.Series(False, index=gap.index)
    if cooldown_bars == 0:
        return blocked
    for idx in gap[gap].index:
        blocked.loc[idx : min(idx + cooldown_bars - 1, len(blocked) - 1)] = True
    return blocked


def run_target_position(
    df: pd.DataFrame,
    *,
    target_position: pd.Series,
    initial_cash: float,
    fee_rate: float,
    slippage_bps: float,
    granularity_seconds: int,
    gap_cooldown_bars: int,
    strategy_name: str,
) -> BacktestResult:
    if initial_cash <= 0:
        raise ValueError("initial_cash must be > 0")
    if len(target_position) != len(df):
        raise ValueError("target_position length must match df length")

    s = slippage_bps / 10_000.0
    gap, _ = compute_gaps(df, granularity_seconds=granularity_seconds)
    blocked = _blocked_trade_mask(gap, cooldown_bars=gap_cooldown_bars)

    cash = float(initial_cash)
    shares = 0.0
    fees_paid = 0.0
    trades = 0
    fills: list[FillEvent] = []

    equity = np.empty(len(df), dtype=np.float64)

    for i in range(len(df)):
        equity[i] = cash + shares * float(df.loc[i, "close"])

        if i == len(df) - 1:
            break

        if bool(blocked.iat[i + 1]):
            continue

        target = float(target_position.iat[i])
        target = 1.0 if target > 0.5 else 0.0
        next_open = float(df.loc[i + 1, "open"])
        if next_open <= 0:
            continue

        if target == 1.0 and shares == 0.0:
            exec_price = next_open * (1.0 + s)
            shares = cash / (exec_price * (1.0 + fee_rate))
            fee_paid = shares * exec_price * fee_rate
            fees_paid += fee_paid
            cash = 0.0
            trades += 1
            fills.append(
                FillEvent(
                    time_utc=pd.Timestamp(df.loc[i + 1, "time"]),
                    strategy=strategy_name,
                    side="buy",
                    price=exec_price,
                    qty_base=shares,
                    qty_quote=shares * exec_price,
                    fee_quote=fee_paid,
                )
            )
        elif target == 0.0 and shares > 0.0:
            exec_price = next_open * (1.0 - s)
            gross = shares * exec_price
            fee_paid = gross * fee_rate
            cash = gross - fee_paid
            fees_paid += fee_paid
            fills.append(
                FillEvent(
                    time_utc=pd.Timestamp(df.loc[i + 1, "time"]),
                    strategy=strategy_name,
                    side="sell",
                    price=exec_price,
                    qty_base=shares,
                    qty_quote=gross,
                    fee_quote=fee_paid,
                )
            )
            shares = 0.0
            trades += 1

    equity_s = pd.Series(equity)
    returns = equity_s.pct_change().fillna(0.0)
    return BacktestResult(
        name=strategy_name,
        equity=equity_s,
        returns=returns,
        trades=trades,
        fees_paid=fees_paid,
        fills=fills,
    )
