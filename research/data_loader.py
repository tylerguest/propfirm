from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataSpec:
    symbol: str | None = None
    granularity_seconds: int | None = None


_FILENAME_RE = re.compile(r"(?P<symbol>[A-Z0-9-]+)_(?P<granularity>\d+)s_")


def _parse_filename(path: Path) -> tuple[str | None, int | None]:
    m = _FILENAME_RE.search(path.name)
    if not m:
        return None, None
    symbol = m.group("symbol")
    granularity = int(m.group("granularity"))
    return symbol, granularity


def find_processed_csv(spec: DataSpec) -> Path:
    processed_dir = Path("data/processed")
    candidates = list(processed_dir.glob("*.csv"))
    if not candidates:
        raise SystemExit("No CSVs found in data/processed. Run `python3 research/fetch_history.py` first.")

    filtered: list[Path] = []
    for path in candidates:
        symbol, granularity = _parse_filename(path)
        if spec.symbol and symbol and symbol != spec.symbol:
            continue
        if spec.granularity_seconds and granularity and granularity != spec.granularity_seconds:
            continue
        if spec.symbol and symbol is None:
            continue
        if spec.granularity_seconds and granularity is None:
            continue
        filtered.append(path)

    if not filtered:
        raise SystemExit(f"No CSVs match: symbol={spec.symbol} granularity_seconds={spec.granularity_seconds}")

    return max(filtered, key=lambda p: p.stat().st_mtime)
