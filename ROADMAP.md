# ROADMAP — Solo Crypto Prop Firm (Coinbase-first, Local-first)

This is a manual-first roadmap: start trading manually on a small account (currently ~$94), while building the research, data, journaling, and risk process so that **automation/bots are the last thing left** later.

## V1 Success Criteria (Manual Trading + Research Stack)
- You can backtest a strategy end-to-end on Coinbase data with a documented execution + fee model.
- Backtests are strategy-specific but run through a shared engine and can be applied across multiple tickers.
- Every manual trade is journaled with enough detail to audit decisions and results.
- Daily/weekly reporting exists (PnL, drawdown, fees, exposure, notes).
- A written risk policy is followed consistently (position sizing + daily loss stop).

## Phase 0 — Foundations (P0)
**Goal:** a safe local setup that you can run and maintain without Docker.
- [x] Standardize local runtime: Python `venv` (or equivalent) + pinned dependencies.
- [x] Add `.env.example` (no secrets) + document required env vars.
- [x] Secrets policy baseline: keep `.env` out of git; use least-privilege keys if/when needed.
- [ ] Structured logging (JSON preferred) + log rotation plan.
- [x] Local data dirs: `data/` and `logs/` gitignored; backup plan (encrypted).
- [ ] “One command” run targets for research tooling (e.g., fetch data, run backtests, generate report).

## Phase 1 — Data Pipeline for Backtesting (P0)
**Goal:** collect/normalize Coinbase market data so backtests are easy and repeatable.
- [x] Historical candles download for BTC-USD (1h, ~5y) into `data/raw` + `data/processed`.
- [ ] Expand to 3–5 symbols.
- [x] Normalized data format (timestamps, timezones, gaps; standard OHLCV columns).
- [x] Data validation checks (dedupe + gap detection).
- [ ] Versioned assumptions: timeframe(s), data source, and gap policy.

## Phase 2 — Backtesting Harness (Start ASAP) (P0)
**Goal:** iterate on strategies quickly with guardrails against self-deception.
- [ ] Standardize a backtest architecture:
  - **Engine:** data loading, gap handling, execution timing (e.g., signal on close → execute next open), fees/slippage, positions/equity
  - **Strategies:** small, strategy-specific modules that output a target position or orders
  - **Runner:** applies the same strategy across multiple tickers/timeframes and writes a comparable results table
- [x] Make costs explicit and realistic:
  - VIP 4 spot fees: **2.5 bps maker** / **6.5 bps taker** (run both as best-case vs stress-case)
  - Slippage model: start with fixed bps, then upgrade later if needed
- [x] Baselines: buy-and-hold + at least one simple trend strategy (SMA crossover).
- [ ] Add “do nothing” baseline.
- [x] Initial scripts exist: `research/backtests/hello_world_backtest.py` and `research/backtests/coinbase_backtest.py`.
- [ ] Output artifacts per run:
  - metrics summary (CSV/JSON)
  - trade blotter (every fill with timestamp/price/fee)
  - config snapshot (parameters + cost assumptions)
- [ ] Walk-forward / train-test split conventions (even if simple at first).
- [ ] Parameter discipline:
  - avoid “one set of params per ticker” overfitting
  - prefer global ranges and evaluate robustness across tickers and time windows

## Phase 3 — Manual Trading Journal + Reporting (P0)
**Goal:** you can always answer: “what did I do, and did it work?”
- [x] Choose journaling format (CSV) and standardize required fields:
  - timestamp, symbol, side, size, entry/exit, fees, thesis, invalidation, notes, outcome
- [x] Journal scaffolding + tools: templates, init/validate, fill sync, trade derivation.
- [x] Manual ledger entry workflow (deposits/withdrawals) via CLI.
- [ ] Daily report script/template: starting equity, ending equity, realized PnL, max drawdown, notes.
- [ ] Weekly review: what setups worked, what didn’t, and what to change in backtests.

## Phase 4 — Risk Policy (Manual-First) (P0)
**Goal:** protect the account while you learn and while the stack matures.
- [ ] Define fixed rules: max loss per day, max loss per trade, max exposure, max number of trades/day.
- [ ] Define “do not trade” conditions (low liquidity, high volatility spikes, news windows, fatigue).
- [ ] Track compliance: every trade records whether rules were followed.

## Phase 5 — Monitoring & Process (P1)
**Goal:** make this sustainable as a solo operator.
- [ ] Calendar cadence: daily review, weekly strategy review, monthly risk review.
- [ ] Lightweight alerts (optional): missed data fetches, report generation failures, disk full.
- [ ] Backup/restore test (monthly): can you restore your data + journals?

## Phase 6 — Automation Readiness (Design First) (P1)
**Goal:** when you decide to build bots, the interfaces and constraints already exist.
- [ ] Keep the strategy interface consistent between backtests and (future) live trading.
- [ ] Define an execution interface (place/cancel/query) that can be mocked for tests.
- [ ] Define risk gatekeeper rules as pure functions (testable without exchange access).
- [ ] Decide storage for production journaling (SQLite still fine for V1 automation).

## Phase 7 — Bot Implementation (Later) (P2)
**Goal:** automation becomes “swap manual execution for code” rather than a new project.
- [ ] Coinbase authenticated REST client + rate-limit handling.
- [ ] (Optional) WebSocket ingestion for live prices.
- [ ] OMS/execution wrapper (idempotency, retries, reconciliation).
- [ ] Risk gatekeeper enforced before every order + kill-switch.
- [ ] Paper/sim mode parity with journaling + reporting.

## Phase 8 — Automated Live Launch (Small, Controlled) (P2)
**Goal:** validate the automated loop with tiny risk, only after the manual stack is proven.
- [ ] Live checklist: keys, limits, kill-switch verified, alerts tested, reconciliation tested.
- [ ] Start with a hard cap (e.g., $50–$200 notional) and strict daily loss limit.
- [ ] Run “incident drills” (disconnect WS, force restart, simulate API errors) and confirm fail-safe.

## Phase 9 — Hardening + Scale (P2)
**Goal:** expand only after stability is proven.
- [ ] Multi-strategy portfolio allocation and shared risk budget.
- [ ] Better execution simulation (order book / partial fills) if needed.
- [ ] Multi-account support (only if you later formalize “prop” structure).
- [ ] Optional: minimal cloud footprint for notifications + encrypted offsite backups.

## Always-On Process (Solo Operator)
- **Daily:** health, PnL/drawdown, exposure, errors, open orders, upcoming maintenance.
- **Weekly:** strategy review, slippage/fees audit, parameter drift, incident retro.
- **Monthly:** risk limits review, dependency updates, backup restore test, data quality audit.

## Top Priorities Checklist (If You Do Nothing Else)
- [ ] Backtesting harness + data pipeline are working and repeatable.
- [ ] Manual trade journaling + daily/weekly review cadence is consistent.
- [ ] Written risk policy exists and you track compliance.
- [ ] Automation is a “final swap-in”, not a rewrite.

## Next Up (Recommended Order)
1) Expand data to 3–5 symbols and document gap policy + assumptions.
2) Add backtest run artifacts (metrics CSV + blotter + config snapshot).
3) Add daily report template (equity, PnL, drawdown, fees, notes).
4) Draft a simple risk policy (max loss per day/trade, max exposure).
