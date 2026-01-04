# Paper/Sim Journaling — Implementation Outline

Goal: finish paper/sim journaling (fills → trades → daily report) so risk and reporting are grounded in real execution data.

## Phase A — Define the Journal Contract (Day 1)
- A1. Validate schemas
  - Confirm required columns and types for:
    - `journal/templates/fills.csv`
    - `journal/templates/trades.csv`
    - `journal/templates/ledger.csv`
  - Decide canonical IDs:
    - `fill_id`, `order_id`, `trade_id`, `run_id`, `strategy_id`, `symbol`, `time_utc`
- A2. Add journal config
  - Create `journal/config.json` with paths and toggles:
    - write mode, dedupe keys, report settings
  - Ensure `.env.example` documents anything needed

## Phase B — Ingest Paper/Sim Fills (Day 1–2)
- B1. Wire backtest fills to journal
  - Backtest runner optionally appends to `journal/local/fills.csv`
  - De‑duplicate on `fill_id` or `(order_id, time_utc, symbol, side, price, size)`
- B2. Paper mode hooks
  - Sim execution uses same fill schema as live
  - Write fills at execution time, not end of run

## Phase C — Derive Trades from Fills (Day 2)
- C1. Build trade aggregation
  - Group fills by `(strategy_id, symbol, position_id)` or `(order_id)`
  - Compute: entry/exit time, avg entry/exit price, size, gross PnL, fees, net PnL, duration
- C2. Make it idempotent
  - Update `journal/tools/derive_trades_from_fills.py`
  - Stable `trade_id` hash so reruns do not duplicate trades

## Phase D — Daily Report Pipeline (Day 3)
- D1. Report inputs
  - Use `journal/local/trades.csv`, `journal/local/fills.csv`, optional `ledger.csv`
  - Metrics: daily PnL, drawdown, fees, slippage proxy, win/loss stats
- D2. Report artifacts
  - Write `reporting/daily_report.md`
  - Append to `reporting/daily_report_log.csv`
  - Include summary metrics + top trades + anomalies

## Phase E — Automation Hooks (Day 3–4)
- E1. One‑command flow
  - `python main.py --mode paper --run-id <...>` writes fills → trades → report
- E2. Idempotent runs
  - Re‑running does not duplicate fills/trades/reports

## Phase F — Dashboard Integration (Day 4)
- F1. Add “Journal” tab
  - Latest fills + trades + daily report summary
- F2. Today panel
  - Pull from report log: PnL, drawdown, fees, risk remaining

## Phase G — Validation & Safety (Day 5)
- G1. Cross‑checks
  - Fills sum to trades; fees reconcile; equity curve matches report
- G2. Edge cases
  - Partial fills, multi‑fill orders, same‑timestamp fills, missing data
