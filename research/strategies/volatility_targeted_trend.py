from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class VolatilityTargetedTrendConfig:
    trend_window: int = 50
    vol_window: int = 24
    max_volatility: float = 0.01  # per-bar volatility cap (e.g., 1% per hour)


def generate_target_position(df: pd.DataFrame, cfg: VolatilityTargetedTrendConfig) -> pd.Series:
    if cfg.trend_window <= 1:
        raise ValueError("trend_window must be > 1")
    if cfg.vol_window <= 1:
        raise ValueError("vol_window must be > 1")
    if cfg.max_volatility <= 0:
        raise ValueError("max_volatility must be > 0")

    close = df["close"].astype("float64")
    returns = close.pct_change()
    volatility = returns.rolling(cfg.vol_window, min_periods=cfg.vol_window).std()
    trend = close > close.rolling(cfg.trend_window, min_periods=cfg.trend_window).mean()

    signal = (trend & (volatility <= cfg.max_volatility)).astype("float64")
    return signal.shift(1).fillna(0.0)
