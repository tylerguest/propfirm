from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TrendRegimeFilterConfig:
    window: int = 50
    corr_threshold: float = 0.3  # correlation with time index


def generate_target_position(df: pd.DataFrame, cfg: TrendRegimeFilterConfig) -> pd.Series:
    if cfg.window <= 2:
        raise ValueError("window must be > 2")
    if not (0 < cfg.corr_threshold <= 1):
        raise ValueError("corr_threshold must be in (0, 1]")

    close = df["close"].astype("float64")
    log_price = close.where(close > 0)
    log_price = np.log(log_price).ffill()

    time_idx = pd.Series(range(len(df)), index=df.index, dtype="float64")
    corr = log_price.rolling(cfg.window, min_periods=cfg.window).corr(time_idx)
    signal = (corr >= cfg.corr_threshold).astype("float64")
    return signal.shift(1).fillna(0.0)
