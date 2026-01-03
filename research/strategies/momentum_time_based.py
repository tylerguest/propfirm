from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TimeMomentumConfig:
    lookback: int = 20
    min_return: float = 0.0


def generate_target_position(df: pd.DataFrame, cfg: TimeMomentumConfig) -> pd.Series:
    if cfg.lookback <= 0:
        raise ValueError("lookback must be > 0")

    close = df["close"].astype("float64")
    ret = close.pct_change(periods=cfg.lookback)
    signal = (ret > cfg.min_return).astype("float64")
    return signal.shift(1).fillna(0.0)
