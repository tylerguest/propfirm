# Trade Journal (Manual-First)

This folder holds a **manual trading journal** designed for a solo crypto prop firm. It’s split into:

- `journal/templates/trades.csv`: **one row per trade idea / position** (the “summary” view)
- `journal/templates/fills.csv`: **one row per exchange fill** (the “truth” / audit log)
- `journal/templates/ledger.csv`: **cashflows** (deposits/withdrawals/transfers/fees) that affect account equity

Keep your real journal in `journal/local/` (ignored by git) so you can store sensitive notes and exact sizing without committing it.

## Quick Start
- Initialize local journal files (creates empty CSVs with headers):
  - `python3 journal/tools/journal_cli.py init`
- Your local files will be:
  - `journal/local/trades.csv`
  - `journal/local/fills.csv`
  - `journal/local/ledger.csv`

## Automatic Sync (No Manual Entry)
If you have Coinbase API credentials, you can sync fills automatically:
- Sync fills from Coinbase into `journal/local/fills.csv`:
  - `python3 journal/tools/sync_coinbase_fills.py`
- Sync deposits/withdrawals from Coinbase into `journal/local/ledger.csv`:
  - `python3 journal/tools/sync_coinbase_ledger.py --dry-run`
  - If you see 0 rows but you know you deposited/withdrew, rerun with `--debug` to see which transaction `type` values Coinbase is returning, then add them via `--types`.
- Derive trade episodes and write `journal/local/trades.csv`:
  - `python3 journal/tools/derive_trades_from_fills.py`

Notes:
- `fills.csv` is the source of truth (every fill + fee + maker/taker).
- `trades.csv` is derived from fills and can be regenerated at any time.
- `sync_coinbase_fills.py` supports two modes and auto-selects based on env vars:
  - **Advanced Trade (JWT)** if `COINBASE_KEY_NAME` + `COINBASE_PRIVATE_KEY` are set
  - **Exchange (HMAC)** otherwise (`COINBASE_API_KEY` + `COINBASE_API_SECRET` + passphrase if required)
- For Advanced Trade, `sync_coinbase_fills.py` auto-detects whether your PEM is Ed25519 (EdDSA) or P-256 EC (ES256).

## Testing / Smoke Test
You can test the whole pipeline **without API keys** using the included fixture:
- Reset local journal:
  - `python3 journal/tools/journal_cli.py init --force`
- Import sample fills (offline mode):
  - `python3 journal/tools/sync_coinbase_fills.py --from-json journal/tools/testdata/fills_sample.json`
- Import sample ledger entries (offline mode):
  - `python3 journal/tools/sync_coinbase_ledger.py --from-json journal/tools/testdata/ledger_transactions_sample.json`
- Derive trades:
  - `python3 journal/tools/derive_trades_from_fills.py`
- Validate schemas:
  - `python3 journal/tools/journal_cli.py validate`

Expected outcome:
- `journal/local/fills.csv` has 2 rows appended.
- `journal/local/ledger.csv` has 1 row appended (a single deposit).
- `journal/local/trades.csv` has 1 derived row (one buy then one sell closes the episode).

## Should You Include Deposits?
Yes—track **deposits/withdrawals** (and other cashflows) in `ledger.csv`.

- Trading PnL answers: “Did my trading make money?”
- Ledger answers: “Did my account equity change because I added/removed cash?”

Keep them separate so performance metrics don’t get distorted by funding events.

### Adding Today’s Deposit
If you want to record your initial funding without syncing anything, append a ledger entry:
- `python3 journal/tools/ledger_cli.py add --type deposit --amount 94.00 --currency USD --reference "initial funding"`

## How to Use (Recommended)
1) Create a `trade_id` in `trades.csv` when you plan a trade (thesis + risk).
2) Log each fill in `fills.csv` (entry/exit, fees, maker/taker).
3) (Optional) Copy computed aggregates from fills into trades (avg entry/exit, realized PnL, R-multiple).

If you are not journaling by hand, skip (1) and just use the automatic sync + derivation workflow.

## Notes on Fees
- Coinbase spot fees depend on whether you’re **maker** or **taker**.
- If you place a limit order that posts to the book and later gets hit, that’s typically **maker**.
- If you cross the spread (market order or marketable limit), that’s typically **taker**.

## Schema
### trades.csv (one row per trade)
Minimum fields to be useful:
- `trade_id`, `created_at_utc`, `symbol`, `direction`, `thesis`, `planned_entry`, `planned_stop`, `planned_risk_quote`, `rules_followed`

Everything else is either:
- planning detail (what you *intended*), or
- outcome detail (what actually happened), often derived from fills.

### fills.csv (one row per fill)
This is the auditable record:
- timestamps, side, price, size, fee, and maker/taker.

## Validation
Run:
- `python3 journal/tools/journal_cli.py validate`

It checks basic column presence and timestamp parsing.
