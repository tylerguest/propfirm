from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SmaCrossoverConfig:
    fast_window: int = 20
    slow_window: int = 100


def generate_target_position(df: pd.DataFrame, cfg: SmaCrossoverConfig) -> pd.Series:
    if cfg.fast_window <= 0 or cfg.slow_window <= 0:
        raise ValueError("SMA windows must be > 0")
    if cfg.fast_window >= cfg.slow_window:
        raise ValueError("fast_window must be < slow_window")

    close = df["close"].astype("float64")
    sma_fast = close.rolling(cfg.fast_window, min_periods=cfg.fast_window).mean()
    sma_slow = close.rolling(cfg.slow_window, min_periods=cfg.slow_window).mean()

    signal = (sma_fast > sma_slow).astype("float64")
    return signal.shift(1).fillna(0.0)
