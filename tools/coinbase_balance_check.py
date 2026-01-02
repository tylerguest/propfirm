from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519


DEFAULT_BASE_URL = "https://api.coinbase.com"
DEFAULT_PATH = "/api/v3/brokerage/accounts"


@dataclass(frozen=True)
class CdpJwtAuth:
    key_name: str
    private_key_pem: str
    jwt_alg: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check Coinbase Brokerage balances via CDP JWT auth.")
    p.add_argument("--env-file", default=None, help="Optional path to a .env file to load before reading env vars.")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL (default: https://api.coinbase.com).")
    p.add_argument("--path", default=DEFAULT_PATH, help=f"Request path (default: {DEFAULT_PATH}).")
    p.add_argument("--timeout", type=int, default=30, help="Request timeout seconds (default: 30).")
    p.add_argument("--currency", default=None, help="Optional currency filter (e.g., USD).")
    p.add_argument("--min-balance", type=float, default=None, help="Optional min balance filter.")
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
    return jwt.encode(
        {"iss": "cdp", "sub": auth.key_name, "nbf": now, "exp": now + 120, "uri": uri_claim},
        auth.private_key_pem,
        algorithm=auth.jwt_alg,
        headers={"kid": auth.key_name},
    )


def main() -> None:
    args = _parse_args()
    env_path = Path(args.env_file) if args.env_file else Path(__file__).resolve().parents[1] / ".env"
    _load_dotenv(env_path)

    auth = _load_cdp_jwt_auth_from_env()
    base_url = str(args.base_url).rstrip("/")
    path = str(args.path)

    token = _build_cdp_jwt(auth, method="GET", host="api.coinbase.com", path=path)
    url = f"{base_url}{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=int(args.timeout))
    r.raise_for_status()
    payload = r.json()
    accounts = payload.get("accounts", [])
    if not isinstance(accounts, list):
        raise SystemExit(f"Unexpected response: {payload}")

    currency_filter = str(args.currency).upper() if args.currency else None
    min_balance = args.min_balance
    shown = 0

    for acct in accounts:
        if not isinstance(acct, dict):
            continue
        currency = str(acct.get("currency") or "").upper()
        if currency_filter and currency != currency_filter:
            continue
        bal = acct.get("available_balance") or {}
        value = str(bal.get("value") or "")
        if min_balance is not None:
            try:
                if float(value) < float(min_balance):
                    continue
            except ValueError:
                continue
        name = str(acct.get("name") or "").strip()
        print(f"{name or currency}: {value} {currency}")
        shown += 1

    if shown == 0:
        print("No accounts matched filters.")


if __name__ == "__main__":
    main()

