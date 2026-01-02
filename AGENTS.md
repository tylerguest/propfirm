# AGENTS.md

This repo is a solo crypto prop firm toolkit with a manual-first trade journal and Coinbase sync tools.

## Quick orientation
- Core journaling docs: `journal/README.md`.
- Local (sensitive) data lives in `journal/local/` and is git-ignored.
- Templates live in `journal/templates/`.
- Coinbase ledger sync tool: `journal/tools/sync_coinbase_ledger.py`.
- Coinbase fills sync tool: `journal/tools/sync_coinbase_fills.py`.

## Coinbase ledger sync notes (based on current behavior)
- Default `--min-time` is `2026-01-01T00:00:00Z`, so runs often return zero rows unless you override it.
- If you expect transactions but see none, rerun with `--debug` and/or set `--types` to match Coinbase transaction `type` values.
- Example dry run:
  - `python3 journal/tools/sync_coinbase_ledger.py --dry-run --debug --accounts USDC --min-time 2024-01-01T00:00:00Z`

## Common workflows
- Initialize local journal files:
  - `python3 journal/tools/journal_cli.py init`
- Sync fills:
  - `python3 journal/tools/sync_coinbase_fills.py`
- Sync ledger:
  - `python3 journal/tools/sync_coinbase_ledger.py --dry-run`
- Derive trades:
  - `python3 journal/tools/derive_trades_from_fills.py`
- Validate schemas:
  - `python3 journal/tools/journal_cli.py validate`

## Secrets and safety
- Do not commit real account data or notes from `journal/local/`.
- Coinbase credentials live in environment variables or a local `.env` file; keep them out of git.

## Agent guidance
- Prefer manual-first workflows; automation should append to `journal/local/*.csv` only.
- When editing tools, keep outputs deterministic and CSV headers stable.
