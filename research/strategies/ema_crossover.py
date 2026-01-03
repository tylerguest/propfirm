from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class EmaCrossoverConfig:
    fast_span: int = 12
    slow_span: int = 26


def generate_target_position(df: pd.DataFrame, cfg: EmaCrossoverConfig) -> pd.Series:
    if cfg.fast_span <= 0 or cfg.slow_span <= 0:
        raise ValueError("EMA spans must be > 0")
    if cfg.fast_span >= cfg.slow_span:
        raise ValueError("fast_span must be < slow_span")

    close = df["close"].astype("float64")
    ema_fast = close.ewm(span=cfg.fast_span, adjust=False).mean()
    ema_slow = close.ewm(span=cfg.slow_span, adjust=False).mean()

    signal = (ema_fast > ema_slow).astype("float64")
    return signal.shift(1).fillna(0.0)
