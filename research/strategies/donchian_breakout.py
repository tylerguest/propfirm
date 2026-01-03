from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class DonchianBreakoutConfig:
    lookback: int = 20
    exit_lookback: int = 10


def generate_target_position(df: pd.DataFrame, cfg: DonchianBreakoutConfig) -> pd.Series:
    if cfg.lookback <= 0 or cfg.exit_lookback <= 0:
        raise ValueError("lookback values must be > 0")
    if cfg.exit_lookback > cfg.lookback:
        raise ValueError("exit_lookback must be <= lookback")

    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")

    breakout_high = high.rolling(cfg.lookback, min_periods=cfg.lookback).max()
    breakout_low = low.rolling(cfg.exit_lookback, min_periods=cfg.exit_lookback).min()

    position = pd.Series(0.0, index=df.index)
    in_pos = False

    for i in range(len(close)):
        if not in_pos and close.iat[i] >= breakout_high.iat[i]:
            in_pos = True
        elif in_pos and close.iat[i] <= breakout_low.iat[i]:
            in_pos = False
        position.iat[i] = 1.0 if in_pos else 0.0

    return position.shift(1).fillna(0.0)
