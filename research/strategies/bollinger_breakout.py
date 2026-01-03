from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class BollingerBreakoutConfig:
    window: int = 20
    num_std: float = 2.0
    exit_on_mid: bool = True


def generate_target_position(df: pd.DataFrame, cfg: BollingerBreakoutConfig) -> pd.Series:
    if cfg.window <= 0:
        raise ValueError("window must be > 0")
    if cfg.num_std <= 0:
        raise ValueError("num_std must be > 0")

    close = df["close"].astype("float64")
    mid = close.rolling(cfg.window, min_periods=cfg.window).mean()
    std = close.rolling(cfg.window, min_periods=cfg.window).std(ddof=0)
    upper = mid + cfg.num_std * std

    position = pd.Series(0.0, index=df.index)
    in_pos = False

    for i in range(len(close)):
        if not in_pos and close.iat[i] >= upper.iat[i]:
            in_pos = True
        elif in_pos and cfg.exit_on_mid and close.iat[i] <= mid.iat[i]:
            in_pos = False
        position.iat[i] = 1.0 if in_pos else 0.0

    return position.shift(1).fillna(0.0)
