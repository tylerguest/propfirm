from __future__ import annotations

import pandas as pd


def generate_target_position(df: pd.DataFrame) -> pd.Series:
    return pd.Series(0.0, index=df.index)
