from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd


TIME_COLUMN_PRIMARY = "time"
TIME_COLUMN_ALIASES = {"time_utc"}
REQUIRED_OHLCV = ["open", "high", "low", "close", "volume"]
GapPolicy = Literal["skip", "forward_fill", "segment"]


@dataclass(frozen=True)
class OhlcvContract:
    time_column: str = TIME_COLUMN_PRIMARY
    required_columns: tuple[str, ...] = tuple(REQUIRED_OHLCV)


def find_latest_processed_csv() -> Path:
    processed_dir = Path("data/processed")
    candidates = sorted(processed_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("No CSVs found in data/processed. Run `python3 research/fetch_history.py` first.")
    return candidates[0]


def load_ohlcv_csv(path: Path, *, contract: OhlcvContract | None = None) -> pd.DataFrame:
    contract = contract or OhlcvContract()
    df = pd.read_csv(path)

    cols = set(df.columns)
    time_col = contract.time_column if contract.time_column in cols else None
    if time_col is None:
        for alias in TIME_COLUMN_ALIASES:
            if alias in cols:
                time_col = alias
                break
    if time_col is None:
        raise SystemExit(f"CSV missing time column (expected '{contract.time_column}' or {sorted(TIME_COLUMN_ALIASES)}).")

    missing = [c for c in contract.required_columns if c not in cols]
    if missing:
        raise SystemExit(f"CSV missing required columns: {missing}")

    if time_col != TIME_COLUMN_PRIMARY:
        df = df.rename(columns={time_col: TIME_COLUMN_PRIMARY})

    df[TIME_COLUMN_PRIMARY] = pd.to_datetime(df[TIME_COLUMN_PRIMARY], utc=True)
    df = df.drop_duplicates(subset=[TIME_COLUMN_PRIMARY], keep="last").sort_values(TIME_COLUMN_PRIMARY).reset_index(drop=True)

    for col in REQUIRED_OHLCV:
        df[col] = df[col].astype("float64")

    return df


def infer_granularity_seconds(path: Path, df: pd.DataFrame) -> int:
    m = re.search(r"_(\d+)s_", path.name)
    if m:
        return int(m.group(1))

    diffs = df[TIME_COLUMN_PRIMARY].diff().dropna().dt.total_seconds()
    if diffs.empty:
        raise ValueError("cannot infer granularity (no diffs)")
    mode = diffs.mode()
    if mode.empty:
        raise ValueError("cannot infer granularity (no mode)")
    return int(mode.iloc[0])


def compute_gaps(df: pd.DataFrame, *, granularity_seconds: int) -> tuple[pd.Series, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    freq = pd.Timedelta(seconds=granularity_seconds)
    diffs = df[TIME_COLUMN_PRIMARY].diff()
    gap = diffs > freq

    gap_ranges: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    gap_indices = gap[gap].index.tolist()
    for idx in gap_indices:
        prev_ts = df.loc[idx - 1, TIME_COLUMN_PRIMARY]
        ts = df.loc[idx, TIME_COLUMN_PRIMARY]
        missing_start = prev_ts + freq
        missing_end = ts - freq
        gap_ranges.append((missing_start, missing_end))

    return gap.fillna(False), gap_ranges


def _segment_by_gaps(df: pd.DataFrame, gap: pd.Series) -> list[pd.DataFrame]:
    segments: list[pd.DataFrame] = []
    start_idx = 0
    for idx in gap[gap].index.tolist():
        if idx - start_idx > 1:
            segments.append(df.iloc[start_idx:idx].reset_index(drop=True))
        start_idx = idx
    if start_idx < len(df):
        segments.append(df.iloc[start_idx:].reset_index(drop=True))
    return segments


def _forward_fill_gaps(df: pd.DataFrame, *, granularity_seconds: int) -> pd.DataFrame:
    freq = pd.Timedelta(seconds=granularity_seconds)
    full_index = pd.date_range(df[TIME_COLUMN_PRIMARY].min(), df[TIME_COLUMN_PRIMARY].max(), freq=freq)
    base = df.set_index(TIME_COLUMN_PRIMARY).reindex(full_index)

    # Preserve close values, then forward-fill for missing candles.
    base["close"] = base["close"].ffill()
    for col in ["open", "high", "low"]:
        base[col] = base[col].fillna(base["close"])
    base["volume"] = base["volume"].fillna(0.0)

    base = base.reset_index().rename(columns={"index": TIME_COLUMN_PRIMARY})
    return base


def apply_gap_policy(
    df: pd.DataFrame,
    *,
    granularity_seconds: int,
    policy: GapPolicy,
) -> tuple[list[pd.DataFrame], list[tuple[pd.Timestamp, pd.Timestamp]]]:
    gap, gap_ranges = compute_gaps(df, granularity_seconds=granularity_seconds)
    if policy == "skip":
        return [df.reset_index(drop=True)], gap_ranges
    if policy == "forward_fill":
        return [_forward_fill_gaps(df, granularity_seconds=granularity_seconds)], gap_ranges
    if policy == "segment":
        return _segment_by_gaps(df, gap), gap_ranges
    raise ValueError(f"Unknown gap policy: {policy}")
