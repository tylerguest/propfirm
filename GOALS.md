# GOALS — Solo “One-Man” Crypto Prop Firm (Coinbase-first, Local-first)

## 1) Overview
- **Project name:** (TBD)
- **Objective:** build an automated crypto trading system for Coinbase (Advanced Trade) that runs locally on my own hardware.
- **Philosophy:** strict separation of concerns between **Research (offline)** and **Production (live execution)**.
- **Primary constraint:** risk management + reliability > raw returns.
- **Status:** pre-alpha / foundation phase.

## 2) Guiding Principles
- **Risk-first:** preserve capital; define hard loss/exposure limits and enforce them in code.
- **Local-first:** run bots locally; cloud is optional for alerts/backups only.
- **Simple + observable:** fewer components, structured logs, metrics, and fast incident response.
- **Reproducible:** strategy code, configs, and backtests are versioned and repeatable.
- **Secure by default:** least-privilege keys, secrets never committed, auditable changes.

## 3) Scope (V1)
- **Exchange:** Coinbase only.
- **Markets:** spot only to start (no leverage) unless explicitly expanded later.
- **Automation:** 1–2 bots max, with paper/sim mode parity with live code paths.
- **Ops:** local monitoring + remote notifications; daily/weekly reporting.

## 4) Non-Goals (V1)
- Multi-exchange routing/arbitrage.
- HFT / ultra-low-latency trading.
- External capital, public “prop” program, or multi-user platform features.
- Complex derivatives (perps/options) until spot execution is rock-solid.

## 5) High-Level Diagram
Paste a Mermaid diagram here when ready (keep it simple: data → signals → risk → execution → journaling → reporting).

## 6) Repo / Directory Structure (Proposed)
```text
/prop-firm-root
├── /data                   # Raw & processed historical data (gitignored)
│   ├── /raw                # Original exports / downloads
│   └── /processed          # Cleaned/normalized datasets for backtesting
├── /research               # Offline lab (notebooks + backtests)
│   ├── /notebooks
│   ├── /backtests
│   └── fetch_history.py    # Download candles/trades for research + replay
├── /src                    # Production source code (live + paper/sim)
│   ├── /ingestion          # REST + WebSocket market data + reconnection
│   ├── /strategies         # Signal generators
│   ├── /execution          # OMS: orders, retries, idempotency, fill tracking
│   ├── /risk               # Gatekeeper: limits, kill-switch, cooldowns
│   ├── /portfolio          # Positions, exposure, allocation (optional V1)
│   └── /reporting          # PnL, slippage, fees, daily summaries
├── /logs                   # Structured logs (gitignored)
├── .env.example            # Example env vars (no secrets)
├── requirements.txt
└── main.py                 # Entry point (paper/sim/live mode)
```

## 7) Component Breakdown
### A) Research Lab (Offline)
Goal: develop and validate strategies before any live deployment.
- Data pipeline: `research/fetch_history.py` produces clean, replayable datasets.
- Backtesting: replay historical data with realistic fees + slippage assumptions.
- Promotion rule: a strategy must have a documented thesis + failure modes + “do not trade” rules.

### B) Production Environment (Live — Local)
Goal: flawless execution and risk enforcement on a local machine.
- Ingestion: real-time WebSocket feeds + robust reconnect/backoff.
- Signal engine: computes indicators and signals deterministically from a defined data source.
- Risk gatekeeper (critical, runs *before* every order):
  - max position/notional exposure
  - max daily loss / drawdown stop
  - max open orders + rate limiting
  - cooldowns after errors/vol spikes
  - kill-switch (manual + automatic)
- Execution / OMS:
  - order placement (limit/market), retries, idempotency, and reconciliation
  - fill tracking + position state

### C) Post-Trade Analytics
Goal: continuous improvement and auditability.
- Trade journal: persist every order + fill + error + state transition.
- Reporting: daily PnL, drawdown, fees, slippage, win/loss, error counts.

## 8) Risk & Trading Policy (Baseline)
- Define a fixed risk budget per day/week and per strategy.
- Enforce hard stops at max daily loss; require manual review to resume.
- Cap exposure by asset and total notional.
- Track execution quality (slippage, partial fills, rejects, latency).

## 9) Security Requirements
- API keys are least-privilege (**trade only**, withdrawals disabled).
- Secrets are stored locally and encrypted (never in git).
- Separate keys for paper vs live.

## 10) Operational Workflows
### Phase 1: Research Cycle
- Fetch/refresh data: `python research/fetch_history.py`
- Iterate in notebooks/backtests.
- If successful: port the strategy logic into `src/strategies/` with versioned config.

### Phase 2: Release Cycle (Local)
- Tag/release a version of the bot config + strategy.
- Start/restart the bot locally (single command).
- Verify health checks + alerting.

### Phase 3: Monitoring
- Check alerts (Discord/Telegram/etc.).
- Review daily report + errors.
- Weekly performance review and parameter drift checks.

## 11) Infrastructure (Local-first)
- Primary runtime: dedicated local machine (UPS recommended).
- Process management: Docker or systemd (pick one and standardize).
- Database: SQLite to start; move to Postgres only if needed.
- Remote services: optional alerts + encrypted backups only.

## 12) Roadmap & Milestones
- [ ] Step 1: Coinbase connectivity + market data + local run/stop + secrets handling
- [ ] Step 2: Paper/sim trading loop with end-to-end journaling + reporting
- [ ] Step 3: Risk gatekeeper module (hard limits + kill-switch)
- [ ] Step 4: Backtesting harness + first strategy candidate + walk-forward sanity checks
- [ ] Step 5: Limited live trading (small size) + alert drills + tighten limits

## 13) Definition of Done (V1)
- Runs locally for 30 days with routine reviews only (no babysitting).
- Fails safe (flat/paused) on errors; alerts fire on every critical failure.
- Daily reporting includes PnL, drawdown, fees, slippage, and error rates.
- One strategy has documented thesis + backtest methodology + live trading rules.
