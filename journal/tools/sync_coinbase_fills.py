from __future__ import annotations

import argparse
import base64
import csv
import hmac
import json
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from datetime import UTC, datetime


DEFAULT_BASE_URL = "https://api.exchange.coinbase.com"
DEFAULT_BROKERAGE_BASE_URL = "https://api.coinbase.com"
DEFAULT_JWT_ALG = "ES256"


@dataclass(frozen=True)
class CoinbaseAuth:
    api_key: str
    api_secret_b64: str
    passphrase: str

    def signing_key(self) -> bytes:
        return base64.b64decode(self.api_secret_b64)


@dataclass(frozen=True)
class CdpJwtAuth:
    key_name: str
    private_key_pem: str
    jwt_alg: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync Coinbase fills into journal/local/fills.csv (auto-journal).")
    p.add_argument("--out", default="journal/local/fills.csv", help="Output fills CSV path.")
    p.add_argument(
        "--base-url",
        default=os.environ.get("COINBASE_EXCHANGE_BASE_URL", DEFAULT_BASE_URL),
        help="Coinbase Exchange API base URL (legacy HMAC mode).",
    )
    p.add_argument(
        "--brokerage-base-url",
        default=os.environ.get("COINBASE_BROKERAGE_BASE_URL", DEFAULT_BROKERAGE_BASE_URL),
        help="Coinbase Brokerage base URL (Advanced Trade JWT mode).",
    )
    p.add_argument("--product-id", default=None, help="Optional product filter (e.g., BTC-USD).")
    p.add_argument(
        "--from-json",
        default=None,
        help="Offline mode: path to a JSON file containing a list of fill objects (no network/auth).",
    )
    p.add_argument("--env-file", default=None, help="Optional path to a .env file to load before reading env vars.")
    p.add_argument(
        "--min-time",
        default=None,
        help="Only include fills at/after this UTC time (ISO8601). Example: 2026-01-01T00:00:00Z",
    )
    p.add_argument("--limit", type=int, default=100, help="Fills per request (default: 100).")
    p.add_argument("--max-pages", type=int, default=200, help="Max pages to fetch (default: 200).")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False, help="Fetch but do not write.")
    return p.parse_args()


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]
        # Do not override values already set in the environment.
        os.environ.setdefault(key, value)


def _load_auth_from_env() -> CoinbaseAuth:
    api_key = (os.environ.get("COINBASE_API_KEY") or "").strip()
    api_secret = (os.environ.get("COINBASE_API_SECRET") or "").strip()
    passphrase = (os.environ.get("COINBASE_API_PASSPHRASE") or "").strip()
    if not api_key or not api_secret:
        raise SystemExit(
            "Missing Coinbase Exchange creds. Set COINBASE_API_KEY and COINBASE_API_SECRET in `.env` (and COINBASE_API_PASSPHRASE if your key requires it)."
        )
    if passphrase == "":
        print("Warning: COINBASE_API_PASSPHRASE is empty. If your key requires it, auth will fail.")
    return CoinbaseAuth(api_key=api_key, api_secret_b64=api_secret, passphrase=passphrase)


def _load_cdp_jwt_auth_from_env() -> CdpJwtAuth | None:
    key_name = (os.environ.get("COINBASE_KEY_NAME") or "").strip()
    private_key = (os.environ.get("COINBASE_PRIVATE_KEY") or "").strip()
    if not key_name and not private_key:
        return None
    if not key_name or not private_key:
        raise SystemExit("Set both COINBASE_KEY_NAME and COINBASE_PRIVATE_KEY (PEM).")

    private_key_pem = private_key.replace("\\n", "\n")

    # Detect key type from PEM and pick the correct JWT alg automatically.
    try:
        key_obj = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            "COINBASE_PRIVATE_KEY is not a valid PEM private key.\n"
            "Tip: store it as a single line with literal `\\n` sequences, or load from a file and export it.\n"
            f"Original error: {e}"
        ) from e

    detected_alg: str
    if isinstance(key_obj, ed25519.Ed25519PrivateKey):
        detected_alg = "EdDSA"
    elif isinstance(key_obj, ec.EllipticCurvePrivateKey):
        # Advanced Trade expects P-256 for ES256.
        if key_obj.curve.name != "secp256r1":
            raise SystemExit(f"Unsupported EC curve for ES256: {key_obj.curve.name} (expected secp256r1)")
        detected_alg = "ES256"
    else:
        raise SystemExit(f"Unsupported private key type: {type(key_obj)}")

    jwt_alg_raw = (os.environ.get("COINBASE_JWT_ALG") or "").strip()
    if jwt_alg_raw:
        jwt_alg_upper = jwt_alg_raw.upper()
        configured_alg = "EdDSA" if jwt_alg_upper in {"EDDSA", "ED25519"} else ("ES256" if jwt_alg_upper == "ES256" else "")
        if not configured_alg:
            raise SystemExit("Unsupported COINBASE_JWT_ALG. Use ES256 or EdDSA.")
        if configured_alg != detected_alg:
            print(f"Warning: COINBASE_JWT_ALG={configured_alg} does not match key type; using {detected_alg}.")
    jwt_alg = detected_alg

    return CdpJwtAuth(key_name=key_name, private_key_pem=private_key_pem, jwt_alg=jwt_alg)


def _sign_request(auth: CoinbaseAuth, *, ts: str, method: str, request_path: str, body: str) -> str:
    message = f"{ts}{method.upper()}{request_path}{body}".encode("utf-8")
    signature = hmac.new(auth.signing_key(), message, sha256).digest()
    return base64.b64encode(signature).decode("utf-8")


def _request_path(path: str, params: dict[str, Any] | None) -> str:
    if not params:
        return path
    # Keep this stable for signing: `requests` will percent-encode, but these params are simple.
    parts: list[str] = []
    for k in sorted(params.keys()):
        v = params[k]
        if v is None:
            continue
        parts.append(f"{k}={v}")
    return path + "?" + "&".join(parts)


def _get(session: requests.Session, auth: CoinbaseAuth, *, base_url: str, path: str, params: dict[str, Any]) -> requests.Response:
    ts = str(time.time())
    rp = _request_path(path, params)
    signature = _sign_request(auth, ts=ts, method="GET", request_path=rp, body="")
    headers = {
        "CB-ACCESS-KEY": auth.api_key,
        "CB-ACCESS-SIGN": signature,
        "CB-ACCESS-TIMESTAMP": ts,
        "CB-ACCESS-PASSPHRASE": auth.passphrase,
        "Accept": "application/json",
        "User-Agent": "propfirm-journal-sync/1.0",
    }
    url = base_url.rstrip("/") + path
    resp = session.get(url, params=params, headers=headers, timeout=30)
    if resp.status_code in {429, 500, 502, 503, 504}:
        time.sleep(2.0)
        resp = session.get(url, params=params, headers=headers, timeout=30)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e} body={resp.text[:500]!r}") from e
    return resp


def _build_cdp_jwt(auth: CdpJwtAuth, *, method: str, host: str, path: str) -> str:
    now = int(time.time())
    uri_claim = f"{method.upper()} {host}{path}"
    try:
        return jwt.encode(
            {"iss": "cdp", "sub": auth.key_name, "nbf": now, "exp": now + 120, "uri": uri_claim},
            auth.private_key_pem,
            algorithm=auth.jwt_alg,
            headers={"kid": auth.key_name},
        )
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            "Failed to sign JWT for Advanced Trade.\n"
            f"- jwt_alg: {auth.jwt_alg}\n"
            "Tip: if your key is Ed25519 use EdDSA; if it is P-256 EC use ES256.\n"
            f"Original error: {e}"
        ) from e


def _parse_iso8601_utc(value: str) -> datetime:
    v = value.strip()
    if not v:
        raise ValueError("empty timestamp")
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _extract_fill_time(raw: dict[str, Any]) -> datetime | None:
    ts = raw.get("created_at") or raw.get("trade_time")
    if not ts:
        return None
    try:
        return _parse_iso8601_utc(str(ts))
    except Exception:
        return None


def _brokerage_get(
    session: requests.Session,
    auth: CdpJwtAuth,
    *,
    base_url: str,
    path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    host = "api.coinbase.com"
    token = _build_cdp_jwt(auth, method="GET", host=host, path=path)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "propfirm-journal-sync/1.0"}
    url = base_url.rstrip("/") + path
    resp = session.get(url, params=params, headers=headers, timeout=30)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e} body={resp.text[:500]!r}") from e
    payload = resp.json()
    if not isinstance(payload, dict):
        raise SystemExit(f"Unexpected brokerage response: {type(payload)} {payload}")
    return payload


def _read_existing_fill_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return set()
        ids: set[str] = set()
        for row in reader:
            fid = (row.get("fill_id") or "").strip()
            if fid:
                ids.add(fid)
        return ids


def _compute_fee_rate_bps(*, fee_quote: float, qty_quote: float) -> float:
    if qty_quote <= 0:
        return 0.0
    return (fee_quote / qty_quote) * 10_000.0


def _normalize_fill(raw: dict[str, Any]) -> dict[str, str]:
    # Support both:
    # - Coinbase Exchange fills: created_at, liquidity (M/T), fee
    # - Coinbase Brokerage fills: trade_time, liquidity_indicator, commission
    created_at = str(raw.get("created_at") or raw.get("trade_time") or "").strip()

    side_raw = str(raw.get("side") or "").strip()
    side = side_raw.lower() if side_raw else ""

    liquidity_raw = str(raw.get("liquidity") or raw.get("liquidity_indicator") or "").strip().upper()
    if liquidity_raw in {"M", "MAKER"}:
        liquidity = "maker"
    elif liquidity_raw in {"T", "TAKER"}:
        liquidity = "taker"
    else:
        liquidity = ""

    product_id = str(raw.get("product_id") or "").strip()
    order_id = str(raw.get("order_id") or "").strip()

    # Exchange: trade_id is numeric, Brokerage: entry_id/trade_id are strings.
    entry_id = str(raw.get("entry_id") or "").strip()
    trade_id = str(raw.get("trade_id") or "").strip()

    price = float(raw.get("price") or 0.0)
    size = float(raw.get("size") or 0.0)
    size_in_quote = bool(raw.get("size_in_quote") or False)
    if size_in_quote and price > 0:
        qty_quote = size
        qty_base = qty_quote / price
    else:
        qty_base = size
        qty_quote = price * qty_base

    fee_quote = float(raw.get("fee") or raw.get("commission") or 0.0)
    fee_rate_bps = _compute_fee_rate_bps(fee_quote=fee_quote, qty_quote=qty_quote)

    # A stable unique id for de-duping/appending.
    id_component = entry_id or trade_id
    fill_id = f"{order_id}:{id_component}" if order_id and id_component else json.dumps(raw, sort_keys=True)

    return {
        "trade_id": "",  # derived later (group fills into a trade episode)
        "fill_id": fill_id,
        "time_utc": created_at,
        "venue": "coinbase",
        "account": "",
        "symbol": product_id,
        "side": side,
        "order_type": "",
        "liquidity": liquidity,
        "price": f"{price:.10f}".rstrip("0").rstrip("."),
        "qty_base": f"{qty_base:.10f}".rstrip("0").rstrip(".") if qty_base else "",
        "qty_quote": f"{qty_quote:.10f}".rstrip("0").rstrip(".") if qty_quote else "",
        "fee_quote": f"{fee_quote:.10f}".rstrip("0").rstrip("."),
        "fee_rate_bps": f"{fee_rate_bps:.4f}".rstrip("0").rstrip("."),
        "order_id": order_id,
        "trade_type": str(raw.get("trade_type") or "").strip(),
        "sequence_timestamp": str(raw.get("sequence_timestamp") or "").strip(),
        "size_in_quote": str(raw.get("size_in_quote") or "").strip(),
        "retail_portfolio_id": str(raw.get("retail_portfolio_id") or "").strip(),
        "notes": "",
    }


def sync_fills_exchange(
    *,
    out_path: Path,
    base_url: str,
    product_id: str | None,
    limit: int,
    max_pages: int,
    dry_run: bool,
    min_time: datetime | None,
) -> None:
    existing_ids = _read_existing_fill_ids(out_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    params: dict[str, Any] = {"limit": int(limit)}
    if product_id:
        params["product_id"] = product_id

    new_rows: list[dict[str, str]] = []

    # Offline mode (fixture testing)
    if base_url.startswith("file://"):
        raise SystemExit("base_url must be an http(s) URL; use --from-json for offline mode.")

    before: str | None = None
    auth = _load_auth_from_env()
    for page in range(1, max_pages + 1):
        page_params = dict(params)
        if before:
            page_params["before"] = before

        resp = _get(session, auth, base_url=base_url, path="/fills", params=page_params)
        payload = resp.json()
        if not isinstance(payload, list):
            raise SystemExit(f"Unexpected response: {type(payload)} {payload}")
        if not payload:
            break

        added_this_page = 0
        old_in_page = 0
        for item in payload:
            if not isinstance(item, dict):
                continue
            if min_time is not None:
                t = _extract_fill_time(item)
                if t is not None and t < min_time:
                    old_in_page += 1
                    continue
            row = _normalize_fill(item)
            if row["fill_id"] in existing_ids:
                continue
            existing_ids.add(row["fill_id"])
            new_rows.append(row)
            added_this_page += 1

        print(f"page {page}: fetched={len(payload)} new={added_this_page}")

        if min_time is not None and old_in_page == len(payload):
            break

        before = resp.headers.get("cb-before")
        if not before:
            break
        if added_this_page == 0:
            break

    if dry_run:
        print(f"Dry run: would append {len(new_rows)} fill(s) to {out_path}")
        return

    # Write: ensure header exists, append rows.
    file_exists = out_path.exists()
    with out_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "trade_id",
                "fill_id",
                "time_utc",
                "venue",
                "account",
                "symbol",
                "side",
                "order_type",
                "liquidity",
                "price",
                "qty_base",
                "qty_quote",
                "fee_quote",
                "fee_rate_bps",
                "order_id",
                "trade_type",
                "sequence_timestamp",
                "size_in_quote",
                "retail_portfolio_id",
                "notes",
            ],
        )
        if not file_exists:
            writer.writeheader()
        for row in new_rows:
            writer.writerow(row)

    print(f"Wrote {len(new_rows)} new fill(s) to {out_path}")


def sync_fills_brokerage(
    *,
    out_path: Path,
    brokerage_base_url: str,
    product_id: str | None,
    limit: int,
    max_pages: int,
    dry_run: bool,
    min_time: datetime | None,
) -> None:
    auth = _load_cdp_jwt_auth_from_env()
    if auth is None:
        raise SystemExit("Missing COINBASE_KEY_NAME/COINBASE_PRIVATE_KEY for Advanced Trade (JWT) mode.")

    existing_ids = _read_existing_fill_ids(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    cursor: str | None = None
    new_rows: list[dict[str, str]] = []

    path = "/api/v3/brokerage/orders/historical/fills"
    for page in range(1, max_pages + 1):
        params: dict[str, Any] = {"limit": int(limit)}
        if cursor:
            params["cursor"] = cursor
        if product_id:
            params["product_id"] = product_id

        payload = _brokerage_get(session, auth, base_url=brokerage_base_url, path=path, params=params)
        fills = payload.get("fills")
        if not isinstance(fills, list):
            raise SystemExit(f"Unexpected brokerage fills shape: {type(fills)} {payload}")

        added_this_page = 0
        old_in_page = 0
        for item in fills:
            if not isinstance(item, dict):
                continue
            if min_time is not None:
                t = _extract_fill_time(item)
                if t is not None and t < min_time:
                    old_in_page += 1
                    continue
            row = _normalize_fill(item)
            if row["fill_id"] in existing_ids:
                continue
            existing_ids.add(row["fill_id"])
            new_rows.append(row)
            added_this_page += 1

        cursor_val = payload.get("cursor")
        cursor = str(cursor_val) if cursor_val else None
        print(f"page {page}: fetched={len(fills)} new={added_this_page} cursor={cursor or ''}")

        if min_time is not None and old_in_page == len(fills):
            break
        if not cursor or added_this_page == 0:
            break

    if dry_run:
        print(f"Dry run: would append {len(new_rows)} fill(s) to {out_path}")
        return

    file_exists = out_path.exists()
    with out_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "trade_id",
                "fill_id",
                "time_utc",
                "venue",
                "account",
                "symbol",
                "side",
                "order_type",
                "liquidity",
                "price",
                "qty_base",
                "qty_quote",
                "fee_quote",
                "fee_rate_bps",
                "order_id",
                "trade_type",
                "sequence_timestamp",
                "size_in_quote",
                "retail_portfolio_id",
                "notes",
            ],
        )
        if not file_exists:
            writer.writeheader()
        for row in new_rows:
            writer.writerow(row)

    print(f"Wrote {len(new_rows)} new fill(s) to {out_path}")


def main() -> None:
    args = _parse_args()
    out_path = Path(args.out)
    env_path = Path(args.env_file) if args.env_file else Path(__file__).resolve().parents[2] / ".env"
    _load_dotenv(env_path)
    min_time = _parse_iso8601_utc(args.min_time) if args.min_time else None

    if args.from_json:
        existing_ids = _read_existing_fill_ids(out_path)
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise SystemExit("--from-json must contain a JSON array of fill objects")

        new_rows: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if min_time is not None:
                t = _extract_fill_time(item)
                if t is not None and t < min_time:
                    continue
            row = _normalize_fill(item)
            if args.product_id and row["symbol"] != args.product_id:
                continue
            if row["fill_id"] in existing_ids:
                continue
            existing_ids.add(row["fill_id"])
            new_rows.append(row)

        if args.dry_run:
            print(f"Dry run: would append {len(new_rows)} fill(s) to {out_path}")
            return

        file_exists = out_path.exists()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "trade_id",
                    "fill_id",
                    "time_utc",
                    "venue",
                    "account",
                    "symbol",
                    "side",
                    "order_type",
                    "liquidity",
                    "price",
                    "qty_base",
                    "qty_quote",
                    "fee_quote",
                    "fee_rate_bps",
                    "order_id",
                    "trade_type",
                    "sequence_timestamp",
                    "size_in_quote",
                    "retail_portfolio_id",
                    "notes",
                ],
            )
            if not file_exists:
                writer.writeheader()
            for row in new_rows:
                writer.writerow(row)
        print(f"Wrote {len(new_rows)} new fill(s) to {out_path} (offline mode)")
        return

    # Auto-select mode:
    # - If COINBASE_KEY_NAME/COINBASE_PRIVATE_KEY are present => Advanced Trade (JWT) mode
    # - Else => legacy Exchange (HMAC) mode
    if _load_cdp_jwt_auth_from_env() is not None:
        sync_fills_brokerage(
            out_path=out_path,
            brokerage_base_url=str(args.brokerage_base_url),
            product_id=str(args.product_id) if args.product_id else None,
            limit=int(args.limit),
            max_pages=int(args.max_pages),
            dry_run=bool(args.dry_run),
            min_time=min_time,
        )
    else:
        sync_fills_exchange(
            out_path=out_path,
            base_url=str(args.base_url),
            product_id=str(args.product_id) if args.product_id else None,
            limit=int(args.limit),
            max_pages=int(args.max_pages),
            dry_run=bool(args.dry_run),
            min_time=min_time,
        )


if __name__ == "__main__":
    main()
