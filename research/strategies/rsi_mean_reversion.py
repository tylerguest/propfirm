from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class RsiMeanReversionConfig:
    period: int = 14
    entry_rsi: float = 30.0
    exit_rsi: float = 50.0


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def generate_target_position(df: pd.DataFrame, cfg: RsiMeanReversionConfig) -> pd.Series:
    if cfg.period <= 0:
        raise ValueError("period must be > 0")
    if cfg.entry_rsi >= cfg.exit_rsi:
        raise ValueError("entry_rsi must be < exit_rsi")

    close = df["close"].astype("float64")
    rsi = _rsi(close, cfg.period)

    position = pd.Series(0.0, index=df.index)
    in_pos = False

    for i in range(len(rsi)):
        if not in_pos and rsi.iat[i] <= cfg.entry_rsi:
            in_pos = True
        elif in_pos and rsi.iat[i] >= cfg.exit_rsi:
            in_pos = False
        position.iat[i] = 1.0 if in_pos else 0.0

    return position.shift(1).fillna(0.0)
