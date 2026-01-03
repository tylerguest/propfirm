# Prop Firm Cheatsheet (Solo / Coinbase / Local-First)

This is your quick-reference sheet for **running the stack**, **journaling**, and **Coinbase Advanced SDK** notes.

## Core Setup
- Create venv: `python3 -m venv .venv && source .venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Copy env: `cp .env.example .env` and fill in keys (never commit `.env`)

### Required env (CDP/Advanced Trade)
- `COINBASE_KEY_NAME` (API key ID)
- `COINBASE_PRIVATE_KEY` (PEM, one line with `\n`)
- `COINBASE_JWT_ALG` = `ES256` (P-256) or `EdDSA` (Ed25519)

## Coinbase Auth + Balances
- JWT smoke test: `python3 tools/coinbase_auth_test.py`
- Key permissions: `python3 tools/coinbase_auth_test.py --path /api/v3/brokerage/key_permissions`
- Balance check: `python3 tools/coinbase_balance_check.py --currency USDC`

## Market Data + Backtests
- Fetch candles: `python3 research/fetch_history.py`
- Hello world backtest: `python3 research/backtests/hello_world_backtest.py`
- Fee-aware backtest: `python3 research/backtests/coinbase_backtest.py`

## Journal (Local, Git-Ignored)
- Init local CSVs: `python3 journal/tools/journal_cli.py init`
- Sync fills: `python3 journal/tools/sync_coinbase_fills.py`
- Derive trades: `python3 journal/tools/derive_trades_from_fills.py`
- Validate: `python3 journal/tools/journal_cli.py validate`

### Ledger (Deposits / Withdrawals)
- Sync ledger: `python3 journal/tools/sync_coinbase_ledger.py --dry-run --debug --accounts USDC`
- If ledger is empty but you see balance changes, your API key likely lacks **transaction history access**.
  - CDP/Advanced Trade keys do **not** expose Coinbase App v2 transactions.
  - For exact deposits/withdrawals, use Coinbase App OAuth with scopes:
    - `wallet:accounts:read`, `wallet:transactions:read`
- Manual ledger entry (fast): `python3 journal/tools/ledger_cli.py add --type deposit --amount 95.00 --currency USDC --reference "funding" --notes "manual entry"`

## Repo Layout (What goes where)
- `data/raw/` + `data/processed/`: historical candles
- `research/`: data collection + backtests
- `journal/`: templates + local journal + tools
- `tools/`: auth and balance helpers
- `docs/GOALS.md` / `docs/ROADMAP.md`: roadmap + priorities

## Coinbase Advanced Python SDK (Docs Review)
Docs: https://coinbase.github.io/coinbase-advanced-py/

### RESTClient constructor (from docs)
`RESTClient(api_key=None, api_secret=None, key_file=None, base_url="https://api.coinbase.com", timeout=None, verbose=False, rate_limit_headers=False)`

Key ideas:
- `api_key` / `api_secret` or `key_file` (path to key file).
- `base_url` defaults to `https://api.coinbase.com`.
- `verbose` enables debug logging.
- `rate_limit_headers` exposes rate limit info.

### Common REST methods (from docs sections)
- Accounts: `get_accounts()`, `get_account()`
- Products: `get_products()`, `get_product()`, `get_product_book()`, `get_best_bid_ask()`
- Market data: `get_candles()`, `get_market_trades()`
- Orders: `create_order()` + market/limit helpers
- Orders history + fills: `get_fills()` (see Orders/Fills section in docs)

### JWT helpers (auth docs)
- Build REST JWT: uses `format_jwt_uri(method, path)` then sign with key/secret.
- Build WebSocket JWT: similar but without REST path.
- JWT URI format example: `GET api.coinbase.com/api/v3/brokerage/accounts`

### WebSockets (docs)
- Market data feed: Websocket API Client
- User feed: Websocket User API Client (requires auth)

## Manual Trading Workflow (Minimal)
- Pre-trade plan in `journal/local/trades.csv` (thesis + risk)
- Execute trades manually on Coinbase
- Sync fills → derive trades → review PnL
- Record deposits/withdrawals in `ledger.csv`

## Troubleshooting Quick Hits
- If JWT errors: check `COINBASE_JWT_ALG` vs key type (Ed25519 -> EdDSA, P-256 -> ES256).
- If ledger shows 0 rows: it’s usually API scope (transactions not exposed to CDP key).
- If backtest gaps: Coinbase candles may have missing hours; verify in output.
