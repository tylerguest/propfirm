# ROADMAP — Solo Crypto Prop Firm (Coinbase-first, Local-first)

This is a manual-first roadmap: start trading manually on a small account (currently ~$94), while building the research, data, journaling, and risk process so that **automation/bots are the last thing left** later.

## V1 Success Criteria (Manual Trading + Research Stack)
- You can backtest a strategy end-to-end on Coinbase data (fees/slippage assumptions versioned).
- Every manual trade is journaled with enough detail to audit decisions and results.
- Daily/weekly reporting exists (PnL, drawdown, fees, exposure, notes).
- A written risk policy is followed consistently (position sizing + daily loss stop).

## Phase 0 — Foundations (P0)
**Goal:** a safe local setup that you can run and maintain without Docker.
- [ ] Standardize local runtime: Python `venv` (or equivalent) + pinned dependencies.
- [ ] Add `.env.example` (no secrets) + document required env vars.
- [ ] Secrets policy: if/when you use API keys, make them least-privilege (trade only; withdrawals disabled); separate paper vs live keys.
- [ ] Structured logging (JSON preferred) + log rotation plan.
- [ ] Local data dirs: `data/` and `logs/` gitignored; backup plan (encrypted).
- [ ] “One command” run targets for research tooling (e.g., fetch data, run backtests, generate report).

## Phase 1 — Data Pipeline for Backtesting (P0)
**Goal:** collect/normalize Coinbase market data so backtests are easy and repeatable.
- [ ] Historical candles download (start with 1–5 symbols).
- [ ] Normalized data format (timestamps, timezones, gaps, corporate actions not relevant for crypto).
- [ ] Data validation checks (missing candles, duplicates, outliers).
- [ ] Versioned assumptions: timeframe(s), fee model, slippage model, and data source.

## Phase 2 — Backtesting Harness (Start ASAP) (P0)
**Goal:** iterate on strategies quickly with guardrails against self-deception.
- [ ] Backtest runner that loads normalized data and produces metrics + equity curve.
- [ ] Explicit fees/slippage; no “free fills”.
- [ ] Walk-forward / train-test split conventions (even if simple at first).
- [ ] Baselines: buy-and-hold + “do nothing” + simple moving average crossover.
- [ ] Output artifacts: run config + results are saved for comparison.

## Phase 3 — Manual Trading Journal + Reporting (P0)
**Goal:** you can always answer: “what did I do, and did it work?”
- [ ] Choose journaling format (CSV/SQLite/Notion/etc.) and standardize required fields:
  - timestamp, symbol, side, size, entry/exit, fees, thesis, invalidation, notes, outcome
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
- [ ] Define a single strategy interface: inputs (data), outputs (signals), config schema.
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
