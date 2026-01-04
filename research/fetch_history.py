from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests


COINBASE_EXCHANGE_API = "https://api.exchange.coinbase.com"
SUPPORTED_GRANULARITIES = {60, 300, 900, 3600, 21600, 86400}


@dataclass(frozen=True)
class FetchConfig:
    product_id: str
    granularity_seconds: int
    start: datetime
    end: datetime
    out_dir_raw: Path
    out_dir_processed: Path
    base_url: str = COINBASE_EXCHANGE_API


def _floor_to_interval(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    floored = epoch - (epoch % seconds)
    return datetime.fromtimestamp(floored, tz=UTC)


def _ceil_to_interval(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    if epoch % seconds == 0:
        return datetime.fromtimestamp(epoch, tz=UTC)
    ceiled = epoch + (seconds - (epoch % seconds))
    return datetime.fromtimestamp(ceiled, tz=UTC)


def _http_get_with_backoff(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any],
    timeout_seconds: int = 30,
    max_attempts: int = 8,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout_seconds)
            if resp.status_code in {429, 500, 502, 503, 504}:
                sleep_s = min(60.0, (2**attempt) + (0.1 * attempt))
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            sleep_s = min(60.0, (2**attempt) + (0.1 * attempt))
            time.sleep(sleep_s)
            continue
    raise RuntimeError(f"Failed GET after {max_attempts} attempts: {url}") from last_exc


def fetch_exchange_products(*, base_url: str) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "propfirm-research-fetch-history/1.0",
        }
    )
    resp = _http_get_with_backoff(session, f"{base_url}/products", params={})
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected products response shape: {type(payload)}: {payload}")
    return payload


def load_products_from_file(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            # Accept a list of product ids or product objects.
            if payload and isinstance(payload[0], dict):
                return [str(p["id"]).strip() for p in payload if "id" in p]
            return [str(p).strip() for p in payload]
        raise ValueError("JSON products file must be a list.")
    if path.suffix.lower() in {".csv", ".tsv"}:
        df = pd.read_csv(path)
        if "product_id" in df.columns:
            return [str(p).strip() for p in df["product_id"].dropna().tolist()]
        if "id" in df.columns:
            return [str(p).strip() for p in df["id"].dropna().tolist()]
        raise ValueError("CSV products file must include 'product_id' or 'id' column.")
    raise ValueError("Unsupported products file type. Use .json or .csv.")


def fetch_coinbase_exchange_candles(
    cfg: FetchConfig,
    *,
    limit_per_request: int = 300,
) -> pd.DataFrame:
    if cfg.granularity_seconds <= 0:
        raise ValueError("granularity_seconds must be > 0")
    if cfg.start >= cfg.end:
        raise ValueError("start must be before end")

    start = _floor_to_interval(cfg.start, cfg.granularity_seconds)
    end = _ceil_to_interval(cfg.end, cfg.granularity_seconds)

    if limit_per_request < 10:
        raise ValueError("limit_per_request must be >= 10")

    # Coinbase candle endpoints can behave as if `end` is inclusive, which can produce
    # `limit_per_request + 1` candles if we ask for a full `limit_per_request` intervals.
    # Use (limit - 1) intervals per window to keep results bounded and avoid boundary gaps.
    max_window_seconds = cfg.granularity_seconds * (limit_per_request - 1)
    windows = math.ceil((end - start).total_seconds() / max_window_seconds)

    candles: list[list[float]] = []
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": "propfirm-research-fetch-history/1.0",
        }
    )

    url = f"{cfg.base_url}/products/{cfg.product_id}/candles"
    cursor = start
    window_index = 0
    while cursor <= end:
        window_index += 1
        window_start = cursor
        window_end = min(end, window_start + timedelta(seconds=max_window_seconds))
        params = {
            "start": window_start.isoformat().replace("+00:00", "Z"),
            "end": window_end.isoformat().replace("+00:00", "Z"),
            "granularity": cfg.granularity_seconds,
        }

        resp = _http_get_with_backoff(session, url, params=params)
        payload = resp.json()
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected response shape: {type(payload)}: {payload}")

        candles.extend(payload)

        print(
            f"[{window_index}/{windows}] "
            f"Fetched {len(payload):4d} candles for {cfg.product_id} "
            f"({params['start']} → {params['end']})"
        )

        cursor = window_end + timedelta(seconds=cfg.granularity_seconds)
        if cursor > end:
            break

    df = pd.DataFrame(candles, columns=["time", "low", "high", "open", "close", "volume"])
    if df.empty:
        raise RuntimeError("No candles fetched (empty dataset).")

    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.drop_duplicates(subset=["time"], keep="last").sort_values("time").reset_index(drop=True)

    df = df.astype(
        {
            "low": "float64",
            "high": "float64",
            "open": "float64",
            "close": "float64",
            "volume": "float64",
        }
    )

    return df


def find_missing_ranges(df: pd.DataFrame, *, granularity_seconds: int) -> list[tuple[datetime, datetime]]:
    freq = pd.Timedelta(seconds=granularity_seconds)
    start = df["time"].min()
    end = df["time"].max()
    expected = pd.date_range(start=start, end=end, freq=freq, tz="UTC")
    actual = pd.DatetimeIndex(df["time"])
    missing = expected.difference(actual)

    if missing.empty:
        return []

    missing = missing.sort_values()
    ranges: list[tuple[datetime, datetime]] = []
    range_start = missing[0]
    prev = missing[0]
    for ts in missing[1:]:
        if ts - prev != freq:
            ranges.append((range_start.to_pydatetime(), prev.to_pydatetime()))
            range_start = ts
        prev = ts
    ranges.append((range_start.to_pydatetime(), prev.to_pydatetime()))
    return ranges


def validate_candles(df: pd.DataFrame, *, granularity_seconds: int) -> list[tuple[datetime, datetime]]:
    if df.empty:
        raise ValueError("empty dataframe")
    if "time" not in df.columns:
        raise ValueError("missing time column")

    missing_ranges = find_missing_ranges(df, granularity_seconds=granularity_seconds)
    if not missing_ranges:
        print("Validation: OK (no gaps detected)")
        return []

    freq = pd.Timedelta(seconds=granularity_seconds)
    print(f"Validation: WARNING ({len(missing_ranges)} missing range(s); expected freq {freq})")
    for start, end in missing_ranges[:10]:
        missing_count = int(((end - start).total_seconds() / granularity_seconds) + 1)
        print(f"- missing {missing_count} candle(s): {start.isoformat()}Z → {end.isoformat()}Z")
    return missing_ranges


def attempt_gap_fill(
    df: pd.DataFrame,
    *,
    cfg: FetchConfig,
    missing_ranges: list[tuple[datetime, datetime]],
    limit_per_request: int,
    padding_bars: int = 2,
) -> pd.DataFrame:
    if not missing_ranges:
        return df

    padding = timedelta(seconds=cfg.granularity_seconds * padding_bars)
    fixed = df
    for start, end in missing_ranges:
        sub_cfg = FetchConfig(
            product_id=cfg.product_id,
            granularity_seconds=cfg.granularity_seconds,
            start=max(cfg.start, start - padding),
            end=min(cfg.end, end + padding),
            out_dir_raw=cfg.out_dir_raw,
            out_dir_processed=cfg.out_dir_processed,
            base_url=cfg.base_url,
        )
        patch_df = fetch_coinbase_exchange_candles(sub_cfg, limit_per_request=limit_per_request)
        fixed = (
            pd.concat([fixed, patch_df], ignore_index=True)
            .drop_duplicates(subset=["time"], keep="last")
            .sort_values("time")
            .reset_index(drop=True)
        )
    return fixed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Coinbase historical candles into data/raw and data/processed.")
    parser.add_argument("--product-id", default="BTC-USD", help="Coinbase product id (default: BTC-USD).")
    parser.add_argument(
        "--product-ids",
        default=None,
        help="Comma-separated product ids (overrides --product-id). Example: BTC-USD,ETH-USD,SOL-USD",
    )
    parser.add_argument(
        "--products-file",
        default=None,
        help="Path to JSON/CSV file with product ids (overrides --product-id).",
    )
    parser.add_argument(
        "--fetch-products",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fetch product list from Coinbase Exchange (default: false).",
    )
    parser.add_argument(
        "--quote-currency",
        default="USD",
        help="Filter fetched products by quote currency when using --fetch-products (default: USD).",
    )
    parser.add_argument(
        "--granularity-seconds",
        type=int,
        default=3600,
        help="Candle granularity in seconds (default: 3600 for 1h).",
    )
    parser.add_argument(
        "--granularities-seconds",
        default=None,
        help="Comma-separated granularities (overrides --granularity-seconds). Example: 300,900,3600",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="How many years back from now to fetch (default: 5). Ignored if --start is set.",
    )
    parser.add_argument("--start", default=None, help="UTC start datetime (ISO8601). Example: 2021-01-01T00:00:00Z")
    parser.add_argument("--end", default=None, help="UTC end datetime (ISO8601). Default: now.")
    parser.add_argument(
        "--incremental",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch only data after the latest indexed dataset (default: true).",
    )
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Overwrite existing datasets for the symbol/granularity (default: false).",
    )
    parser.add_argument(
        "--limit-per-request",
        type=int,
        default=300,
        help="Max candles per request window (default: 300).",
    )
    parser.add_argument(
        "--attempt-gap-fill",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Attempt to refetch missing candle ranges (default: true).",
    )
    parser.add_argument("--base-url", default=COINBASE_EXCHANGE_API, help="API base URL (default: api.exchange.coinbase.com).")
    parser.add_argument("--raw-dir", default="data/raw", help="Output dir for raw export (default: data/raw).")
    parser.add_argument("--processed-dir", default="data/processed", help="Output dir for processed export (default: data/processed).")
    return parser.parse_args()


def _parse_dt(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _find_latest_indexed_end(
    *,
    index_path: Path,
    product_id: str,
    granularity_seconds: int,
) -> datetime | None:
    if not index_path.exists():
        return None
    df = pd.read_csv(index_path)
    if df.empty:
        return None
    subset = df[
        (df["product_id"] == product_id) & (df["granularity_seconds"].astype(int) == int(granularity_seconds))
    ]
    if subset.empty:
        return None
    latest = subset.sort_values("dataset_end").iloc[-1]
    return _parse_dt(str(latest["dataset_end"]) + "T00:00:00Z")


def main() -> None:
    args = _parse_args()

    end = _parse_dt(args.end) if args.end else datetime.now(tz=UTC)
    if args.start:
        start = _parse_dt(args.start)
    else:
        start = end - timedelta(days=365 * int(args.years))

    if args.product_ids:
        product_ids = [p.strip() for p in str(args.product_ids).split(",") if p.strip()]
    elif args.products_file:
        product_ids = load_products_from_file(Path(args.products_file))
    elif args.fetch_products:
        products = fetch_exchange_products(base_url=str(args.base_url))
        quote = str(args.quote_currency).strip().upper()
        product_ids = [
            str(p["id"]).strip()
            for p in products
            if p.get("quote_currency") == quote and p.get("status") == "online"
        ]
        if not product_ids:
            raise RuntimeError(f"No products found for quote_currency={quote} with status=online.")
    else:
        product_ids = [str(args.product_id).strip()]

    if args.granularities_seconds:
        granularities = [int(x.strip()) for x in str(args.granularities_seconds).split(",") if x.strip()]
    else:
        granularities = [int(args.granularity_seconds)]
    invalid_granularities = [g for g in granularities if g not in SUPPORTED_GRANULARITIES]
    if invalid_granularities:
        print(
            "Warning: removing unsupported granularities "
            f"{sorted(set(invalid_granularities))}. Supported: {sorted(SUPPORTED_GRANULARITIES)}"
        )
        granularities = [g for g in granularities if g in SUPPORTED_GRANULARITIES]
    if not granularities:
        raise ValueError(f"No valid granularities supplied. Supported: {sorted(SUPPORTED_GRANULARITIES)}")

    out_dir_raw = Path(args.raw_dir)
    out_dir_processed = Path(args.processed_dir)
    out_dir_raw.mkdir(parents=True, exist_ok=True)
    out_dir_processed.mkdir(parents=True, exist_ok=True)

    def _granularity_label(seconds: int) -> str:
        label_map = {
            60: "1m",
            300: "5m",
            900: "15m",
            3600: "1h",
            21600: "6h",
            86400: "1d",
        }
        return label_map.get(seconds, f"{seconds}s")

    def _clean_old_outputs(*, symbol: str, granularity_seconds: int) -> None:
        prefixes = [
            f"{symbol}_{granularity_seconds}s_",
            f"{symbol}_{_granularity_label(granularity_seconds)}_",
        ]
        for prefix in prefixes:
            for path in out_dir_raw.glob(f"{prefix}*.json"):
                path.unlink(missing_ok=True)
            for path in out_dir_processed.glob(f"{prefix}*.csv"):
                path.unlink(missing_ok=True)

    index_path = out_dir_processed / "index.csv"
    index_header = [
        "product_id",
        "granularity_seconds",
        "dataset_start",
        "dataset_end",
        "processed_path",
    ]

    for product_id in product_ids:
        for granularity_seconds in granularities:
            fetch_start = start
            if not args.start and args.incremental:
                latest_end = _find_latest_indexed_end(
                    index_path=index_path,
                    product_id=product_id,
                    granularity_seconds=int(granularity_seconds),
                )
                if latest_end is not None:
                    fetch_start = latest_end + timedelta(seconds=int(granularity_seconds))

            if fetch_start >= end:
                print(
                    f"Skipping {product_id} {granularity_seconds}s: "
                    f"start={fetch_start.isoformat()} >= end={end.isoformat()}"
                )
                continue

            cfg = FetchConfig(
                product_id=product_id,
                granularity_seconds=int(granularity_seconds),
                start=fetch_start,
                end=end,
                out_dir_raw=out_dir_raw,
                out_dir_processed=out_dir_processed,
                base_url=str(args.base_url),
            )

            if args.overwrite:
                _clean_old_outputs(symbol=cfg.product_id.replace("/", "-"), granularity_seconds=cfg.granularity_seconds)
            df = fetch_coinbase_exchange_candles(cfg, limit_per_request=int(args.limit_per_request))
            missing_ranges = validate_candles(df, granularity_seconds=cfg.granularity_seconds)
            if missing_ranges and args.attempt_gap_fill:
                print("Attempting to refetch missing ranges...")
                df = attempt_gap_fill(
                    df,
                    cfg=cfg,
                    missing_ranges=missing_ranges,
                    limit_per_request=int(args.limit_per_request),
                )
                missing_ranges = validate_candles(df, granularity_seconds=cfg.granularity_seconds)
                if missing_ranges:
                    print("Gap fill incomplete: some ranges are still missing. Consider refetching those windows later.")

            safe_product = cfg.product_id.replace("/", "-")
            dataset_start = pd.Timestamp(df["time"].min()).date()
            dataset_end = pd.Timestamp(df["time"].max()).date()
            label = f"{safe_product}_{_granularity_label(cfg.granularity_seconds)}_{dataset_start}_{dataset_end}"

            raw_path = cfg.out_dir_raw / f"{label}.json"
            processed_path = cfg.out_dir_processed / f"{label}.csv"

            raw_records = [
                {
                    "time": row["time"].isoformat(),
                    "low": float(row["low"]),
                    "high": float(row["high"]),
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
                for row in df.to_dict(orient="records")
            ]
            raw_path.write_text(json.dumps(raw_records, indent=2), encoding="utf-8")
            df.to_csv(processed_path, index=False)

            if not index_path.exists():
                index_path.write_text(",".join(index_header) + "\n", encoding="utf-8")
            with index_path.open("a", encoding="utf-8") as f:
                f.write(
                    f"{cfg.product_id},{cfg.granularity_seconds},"
                    f"{dataset_start},{dataset_end},{processed_path}\n"
                )

            print(f"Wrote raw:       {raw_path}")
            print(f"Wrote processed: {processed_path}")


if __name__ == "__main__":
    main()
