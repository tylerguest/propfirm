from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519


DEFAULT_BASE_URL = "https://api.coinbase.com"
DEFAULT_MIN_TIME = "2026-01-01T00:00:00Z"
DEFAULT_STATE_FILE = "journal/local/ledger_state.json"

LEDGER_COLUMNS = [
    "time_utc",
    "venue",
    "account",
    "type",
    "amount",
    "currency",
    "transaction_id",
    "reference",
    "notes",
]


@dataclass(frozen=True)
class CdpJwtAuth:
    key_name: str
    private_key_pem: str
    jwt_alg: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sync Coinbase deposits/withdrawals into journal/local/ledger.csv (auto-ledger)."
    )
    p.add_argument("--out", default="journal/local/ledger.csv", help="Output ledger CSV path.")
    p.add_argument("--base-url", default=None, help="Coinbase base URL (default: https://api.coinbase.com).")
    p.add_argument("--env-file", default=None, help="Optional path to a .env file to load before reading env vars.")
    p.add_argument(
        "--min-time",
        default=None,
        help=f"Only include transactions at/after this UTC time (ISO8601). Default: {DEFAULT_MIN_TIME}",
    )
    p.add_argument(
        "--types",
        default="fiat_deposit,fiat_withdrawal,exchange_deposit,exchange_withdrawal,receive,send",
        help=(
            "Comma-separated Coinbase transaction types to import "
            "(default: fiat_deposit,fiat_withdrawal,exchange_deposit,exchange_withdrawal,receive,send)."
        ),
    )
    p.add_argument("--limit", type=int, default=100, help="Transactions per request (default: 100).")
    p.add_argument("--max-pages", type=int, default=50, help="Max pages per account (default: 50).")
    p.add_argument(
        "--accounts",
        default=None,
        help="Optional comma-separated account currency filter (e.g., USD,USDC,BTC). Default: all accounts.",
    )
    p.add_argument(
        "--from-json",
        default=None,
        help="Offline mode: path to a JSON file containing a list of transaction objects (no network/auth).",
    )
    p.add_argument("--debug", action=argparse.BooleanOptionalAction, default=False, help="Print debug info (no secrets).")
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
        os.environ.setdefault(key, value)


def _load_cdp_jwt_auth_from_env() -> CdpJwtAuth:
    key_name = (os.environ.get("COINBASE_KEY_NAME") or "").strip()
    private_key = (os.environ.get("COINBASE_PRIVATE_KEY") or "").strip()
    if not key_name or not private_key:
        raise SystemExit("Set COINBASE_KEY_NAME and COINBASE_PRIVATE_KEY (PEM) in your environment.")

    private_key_pem = private_key.replace("\\n", "\n")

    try:
        key_obj = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            "COINBASE_PRIVATE_KEY is not a valid PEM private key.\n"
            "Tip: store it as a single line with literal `\\n` sequences, or load from a file and export it.\n"
            f"Original error: {e}"
        ) from e

    if isinstance(key_obj, ed25519.Ed25519PrivateKey):
        detected_alg = "EdDSA"
    elif isinstance(key_obj, ec.EllipticCurvePrivateKey):
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

    return CdpJwtAuth(key_name=key_name, private_key_pem=private_key_pem, jwt_alg=detected_alg)


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
            "Failed to sign JWT.\n"
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


def _split_next_uri(uri: str) -> tuple[str, dict[str, Any]]:
    parsed = urlparse(uri)
    path = parsed.path
    params_raw = parse_qs(parsed.query)
    params: dict[str, Any] = {k: (v[0] if len(v) == 1 else v) for k, v in params_raw.items()}
    return path, params


def _cdp_get_json(
    session: requests.Session,
    auth: CdpJwtAuth,
    *,
    base_url: str,
    path: str,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    host = "api.coinbase.com"
    token = _build_cdp_jwt(auth, method="GET", host=host, path=path)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "propfirm-ledger-sync/1.0"}
    url = base_url.rstrip("/") + path
    resp = session.get(url, params=params or {}, headers=headers, timeout=30)
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise requests.HTTPError(f"{e} body={resp.text[:500]!r}") from e
    payload = resp.json()
    if not isinstance(payload, dict):
        raise SystemExit(f"Unexpected response type: {type(payload)} {payload}")
    return payload


def _ensure_header(path: Path) -> None:
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
        if header is None:
            raise SystemExit(f"Ledger file exists but is empty: {path}")
        if [c.strip() for c in header] != LEDGER_COLUMNS:
            raise SystemExit(f"Ledger header mismatch in {path}. Expected: {LEDGER_COLUMNS}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(LEDGER_COLUMNS)


def _load_existing_dedupe(path: Path) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return set(), set()
        ids: set[str] = set()
        keys: set[str] = set()
        for row in reader:
            txid = (row.get("transaction_id") or "").strip()
            if txid:
                ids.add(txid)
                continue
            key = "|".join(
                [
                    (row.get("time_utc") or "").strip(),
                    (row.get("type") or "").strip(),
                    (row.get("amount") or "").strip(),
                    (row.get("currency") or "").strip(),
                    (row.get("reference") or "").strip(),
                ]
            )
            if key.strip("|"):
                keys.add(key)
        return ids, keys


def _signed_amount(raw_amount: str, *, negative: bool) -> str:
    v = str(raw_amount).strip()
    if v.startswith("+"):
        v = v[1:]
    if v.startswith("-"):
        v = v[1:]
    if negative and v:
        return "-" + v
    return v


def _extract_amount(tx: dict[str, Any]) -> tuple[str, str] | None:
    amt = tx.get("amount")
    if isinstance(amt, dict):
        value = amt.get("amount")
        currency = amt.get("currency")
        if value is None or currency is None:
            return None
        return str(value), str(currency).upper()
    return None


def _extract_time_utc(tx: dict[str, Any]) -> str | None:
    for key in ("created_at", "timestamp", "time", "occurred_at"):
        raw = tx.get(key)
        if not raw:
            continue
        try:
            dt = _parse_iso8601_utc(str(raw))
        except Exception:
            continue
        return dt.isoformat().replace("+00:00", "Z")
    return None


def _extract_details(tx: dict[str, Any]) -> tuple[str, str]:
    details = tx.get("details")
    if isinstance(details, dict):
        title = str(details.get("title") or "").strip()
        subtitle = str(details.get("subtitle") or "").strip()
        return title, subtitle
    return "", ""


def _tx_to_ledger_row(
    tx: dict[str, Any],
    *,
    account_label: str,
    allowed_types: set[str],
) -> dict[str, str] | None:
    tx_type = str(tx.get("type") or "").strip()
    if tx_type == "" or (allowed_types and tx_type not in allowed_types):
        return None

    when = _extract_time_utc(tx)
    amt = _extract_amount(tx)
    if when is None or amt is None:
        return None

    raw_value, currency = amt

    ledger_type: str
    negative: bool
    if tx_type in {"fiat_deposit", "exchange_deposit", "deposit", "receive"}:
        ledger_type, negative = "deposit", False
    elif tx_type in {"fiat_withdrawal", "exchange_withdrawal", "withdrawal", "send"}:
        ledger_type, negative = "withdrawal", True
    else:
        # Unknown tx type for ledger semantics; ignore.
        return None

    title, subtitle = _extract_details(tx)
    reference = title or tx_type
    notes = subtitle or str(tx.get("status") or "").strip()

    txid = str(tx.get("id") or tx.get("transaction_id") or "").strip()

    return {
        "time_utc": when,
        "venue": "coinbase",
        "account": account_label,
        "type": ledger_type,
        "amount": _signed_amount(raw_value, negative=negative),
        "currency": currency,
        "transaction_id": txid,
        "reference": reference,
        "notes": notes,
    }


def _iter_v2_accounts(
    session: requests.Session,
    auth: CdpJwtAuth,
    *,
    base_url: str,
    limit: int,
) -> list[dict[str, Any]]:
    path = "/v2/accounts"
    params: dict[str, Any] | None = {"limit": limit}
    payload = _cdp_get_json(session, auth, base_url=base_url, path=path, params=params)
    data = payload.get("data")
    if not isinstance(data, list):
        raise SystemExit(f"Unexpected /v2/accounts response shape: {payload}")
    return [x for x in data if isinstance(x, dict)]


def _iter_v2_transactions(
    session: requests.Session,
    auth: CdpJwtAuth,
    *,
    base_url: str,
    account_id: str,
    limit: int,
    max_pages: int,
    min_time: datetime,
) -> list[dict[str, Any]]:
    path = f"/v2/accounts/{account_id}/transactions"
    params: dict[str, Any] | None = {"limit": limit}
    out: list[dict[str, Any]] = []

    for _page in range(max_pages):
        payload = _cdp_get_json(session, auth, base_url=base_url, path=path, params=params)
        data = payload.get("data")
        if not isinstance(data, list):
            raise SystemExit(f"Unexpected {path} response shape: {payload}")
        batch = [x for x in data if isinstance(x, dict)]
        out.extend(batch)

        # If these are reverse-chronological (common), stop once we see older data.
        oldest: datetime | None = None
        for item in reversed(batch):
            when = _extract_time_utc(item)
            if when is None:
                continue
            try:
                dt = _parse_iso8601_utc(when)
            except Exception:
                continue
            oldest = dt
            break
        if oldest is not None and oldest < min_time:
            break

        pagination = payload.get("pagination")
        if not isinstance(pagination, dict):
            break
        next_uri = pagination.get("next_uri")
        if not next_uri:
            break
        next_path, next_params = _split_next_uri(str(next_uri))
        path, params = next_path, next_params

    return out


def _append_rows(path: Path, rows: list[dict[str, str]]) -> None:
    _ensure_header(path)
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        for row in rows:
            w.writerow(row)


def main() -> None:
    args = _parse_args()

    env_path = Path(args.env_file) if args.env_file else Path(__file__).resolve().parents[2] / ".env"
    _load_dotenv(env_path)

    out_path = Path(args.out)
    min_time_raw = str(args.min_time).strip() if args.min_time else (os.environ.get("COINBASE_LEDGER_MIN_TIME") or DEFAULT_MIN_TIME)
    min_time = _parse_iso8601_utc(min_time_raw)

    allowed_types = {t.strip() for t in str(args.types).split(",") if t.strip()}
    account_filter: set[str] | None = None
    if args.accounts:
        account_filter = {x.strip().upper() for x in str(args.accounts).split(",") if x.strip()}

    existing_ids, existing_keys = _load_existing_dedupe(out_path)

    if args.from_json:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("data"), list):
            txs = raw["data"]
        elif isinstance(raw, list):
            txs = raw
        else:
            raise SystemExit("--from-json must contain a list or an object with a `data` list.")

        new_rows: list[dict[str, str]] = []
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            when = _extract_time_utc(tx)
            if when is None:
                continue
            if _parse_iso8601_utc(when) < min_time:
                continue
            account_label = str(tx.get("account") or "").strip()
            row = _tx_to_ledger_row(tx, account_label=account_label, allowed_types=allowed_types)
            if row is None:
                continue
            txid = row["transaction_id"].strip()
            key = "|".join([row["time_utc"], row["type"], row["amount"], row["currency"], row["reference"]])
            if txid and txid in existing_ids:
                continue
            if (not txid) and key in existing_keys:
                continue
            if txid:
                existing_ids.add(txid)
            else:
                existing_keys.add(key)
            new_rows.append(row)

        if args.dry_run:
            print(f"Dry run: would append {len(new_rows)} ledger row(s) to {out_path}")
            return
        _append_rows(out_path, new_rows)
        print(f"Appended {len(new_rows)} ledger row(s) to {out_path}")
        return

    auth = _load_cdp_jwt_auth_from_env()
    base_url = (
        str(args.base_url).strip()
        if args.base_url
        else (os.environ.get("COINBASE_BROKERAGE_BASE_URL") or DEFAULT_BASE_URL)
    ).rstrip("/")

    with requests.Session() as session:
        accounts = _iter_v2_accounts(session, auth, base_url=base_url, limit=int(args.limit))
        if args.debug:
            print(f"min_time: {min_time.isoformat().replace('+00:00','Z')}")
            print(f"accounts fetched: {len(accounts)}")

        total_new = 0
        total_seen = 0
        new_rows: list[dict[str, str]] = []
        type_counts: dict[str, int] = {}
        account_tx_counts: dict[str, int] = {}

        for account in accounts:
            account_id = str(account.get("id") or "").strip()
            currency = str(account.get("currency") or "").strip().upper()
            name = str(account.get("name") or "").strip()
            if not account_id:
                continue
            if account_filter is not None and currency and currency not in account_filter:
                continue

            label = name or currency or account_id
            if currency and name and currency not in name:
                label = f"{name} ({currency})"

            txs = _iter_v2_transactions(
                session,
                auth,
                base_url=base_url,
                account_id=account_id,
                limit=int(args.limit),
                max_pages=int(args.max_pages),
                min_time=min_time,
            )
            if args.debug:
                account_tx_counts[label] = len(txs)

            for tx in txs:
                tx_type = str(tx.get("type") or "").strip()
                if tx_type:
                    type_counts[tx_type] = type_counts.get(tx_type, 0) + 1
                when = _extract_time_utc(tx)
                if when is None:
                    continue
                if _parse_iso8601_utc(when) < min_time:
                    continue

                row = _tx_to_ledger_row(tx, account_label=label, allowed_types=allowed_types)
                if row is None:
                    continue
                total_seen += 1

                txid = row["transaction_id"].strip()
                key = "|".join([row["time_utc"], row["type"], row["amount"], row["currency"], row["reference"]])
                if txid and txid in existing_ids:
                    continue
                if (not txid) and key in existing_keys:
                    continue
                if txid:
                    existing_ids.add(txid)
                else:
                    existing_keys.add(key)
                new_rows.append(row)

        total_new = len(new_rows)
        if args.debug:
            if accounts:
                # Show the busiest accounts first.
                top_accounts = sorted(account_tx_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
                if top_accounts:
                    print("txs per account (top):")
                    for acct, n in top_accounts:
                        print(f"- {acct}: {n}")
            if type_counts:
                top_types = sorted(type_counts.items(), key=lambda kv: kv[1], reverse=True)[:25]
                print("transaction types seen (top):")
                for t, n in top_types:
                    print(f"- {t}: {n}")
            if not accounts:
                print("No accounts returned from /v2/accounts. Your key may not have Coinbase App API permissions/scopes.")
            elif not type_counts:
                print("No transactions returned from /v2/accounts/{id}/transactions (or all filtered by min_time).")
            elif total_seen == 0:
                print(
                    "No deposit/withdraw candidates matched. If your deposit uses a different `type`, add it via `--types`.\n"
                    "Example: `--types fiat_deposit,exchange_deposit,fiat_withdrawal,exchange_withdrawal,receive,send`"
                )
        if args.dry_run:
            print(f"Dry run: would append {total_new} ledger row(s) to {out_path} (candidates={total_seen})")
            return

        _append_rows(out_path, new_rows)
        print(f"Appended {total_new} ledger row(s) to {out_path} (candidates={total_seen})")


if __name__ == "__main__":
    main()
