from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import jwt
import requests


DEFAULT_HOST = "api.coinbase.com"
DEFAULT_BASE_URL = f"https://{DEFAULT_HOST}"
DEFAULT_PATH = "/api/v3/brokerage/accounts"
DEFAULT_JWT_ALG = "ES256"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke test Coinbase Advanced Trade (JWT/ES256) API auth.")
    p.add_argument("--method", default="GET", help="HTTP method (default: GET).")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"Host used in JWT uri claim (default: {DEFAULT_HOST}).")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Base URL (default: {DEFAULT_BASE_URL}).")
    p.add_argument("--path", default=DEFAULT_PATH, help=f"Request path (default: {DEFAULT_PATH}).")
    p.add_argument("--timeout", type=int, default=30, help="Request timeout seconds (default: 30).")
    p.add_argument("--max-bytes", type=int, default=2000, help="Max response bytes to print (default: 2000).")
    p.add_argument("--private-key-file", default=None, help="Optional path to PEM private key file.")
    p.add_argument("--env-file", default=None, help="Optional path to a .env file to load before reading env vars.")
    p.add_argument(
        "--jwt-alg",
        default=os.environ.get("COINBASE_JWT_ALG", DEFAULT_JWT_ALG),
        help="JWT signing algorithm (default: ES256). For Ed25519 keys, use EdDSA.",
    )
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


def _load_private_key(*, private_key_file: str | None) -> str:
    if private_key_file:
        return Path(private_key_file).read_text(encoding="utf-8")

    private_key = (os.environ.get("COINBASE_PRIVATE_KEY") or "").strip()
    if not private_key:
        raise SystemExit("Missing COINBASE_PRIVATE_KEY (or pass --private-key-file).")
    private_key = private_key.replace("\\n", "\n")
    # Fast sanity checks to avoid confusing cryptography errors.
    if "-----BEGIN" not in private_key or "PRIVATE KEY" not in private_key or "-----END" not in private_key:
        raise SystemExit(
            "COINBASE_PRIVATE_KEY does not look like a PEM private key.\n"
            "Expected something like:\n"
            "  -----BEGIN PRIVATE KEY-----\n"
            "  ...\n"
            "  -----END PRIVATE KEY-----\n"
            "Tips:\n"
            "- If you only have COINBASE_API_SECRET (base64), that is a legacy Exchange/HMAC secret and cannot be used for JWT.\n"
            "- For Advanced Trade JWT auth you must create a CDP/Advanced Trade API key and use its EC private key.\n"
            "- Easiest: save the PEM to a file and run with `--private-key-file path/to/key.pem`.\n"
        )
    return private_key


def _normalize_jwt_alg(value: str) -> str:
    v = value.strip()
    if not v:
        return DEFAULT_JWT_ALG
    upper = v.upper()
    if upper in {"EDDSA", "ED25519"}:
        return "EdDSA"
    if upper == "ES256":
        return "ES256"
    raise SystemExit("Unsupported --jwt-alg. Use ES256 or EdDSA.")


def main() -> None:
    args = _parse_args()
    env_path = Path(args.env_file) if args.env_file else Path(__file__).resolve().parents[1] / ".env"
    _load_dotenv(env_path)

    key_name = (os.environ.get("COINBASE_KEY_NAME") or "").strip()
    if not key_name:
        raise SystemExit("Missing COINBASE_KEY_NAME in environment.")

    private_key = _load_private_key(private_key_file=args.private_key_file)
    jwt_alg = _normalize_jwt_alg(str(args.jwt_alg))

    method = str(args.method).upper()
    host = str(args.host)
    path = str(args.path)
    base_url = str(args.base_url).rstrip("/")

    now = int(time.time())
    uri_claim = f"{method} {host}{path}"

    try:
        token = jwt.encode(
            {"iss": "cdp", "sub": key_name, "nbf": now, "exp": now + 120, "uri": uri_claim},
            private_key,
            algorithm=jwt_alg,
            headers={"kid": key_name},
        )
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            "Failed to sign JWT. This usually means the private key format doesn't match the algorithm.\n"
            f"- jwt_alg: {jwt_alg}\n"
            "Tips:\n"
            "- If your key is Ed25519, set `COINBASE_JWT_ALG=EdDSA` or run with `--jwt-alg EdDSA`.\n"
            "- If your key is P-256 EC, use ES256.\n"
            "- If you're storing the key in `.env`, use literal `\\n` sequences (one-line) or use `--private-key-file`.\n"
            f"Original error: {e}"
        ) from e

    url = f"{base_url}{path}"
    r = requests.request(method, url, headers={"Authorization": f"Bearer {token}"}, timeout=int(args.timeout))

    print("url:", url)
    print("status:", r.status_code)
    body = r.content[: int(args.max_bytes)]
    try:
        print(body.decode("utf-8"))
    except UnicodeDecodeError:
        print(body)


if __name__ == "__main__":
    main()
