# ROADMAP — Automated Solo Crypto Prop Firm (Coinbase-first, Local-first)

Primary objective: build a **local automated** trading system with paper/sim parity, strong risk gates, and reliable journaling.

## V1 Success Criteria
- Paper/sim trading loop runs end-to-end with journaling + reporting.
- Risk gatekeeper blocks bad trades and enforces hard limits.
- Live trading starts at small size with alerts + reconciliation.

## Phase 0 — Foundations (P0)
- [x] Standardize local runtime (Python venv).
- [x] Secrets policy baseline (.env excluded from git).
- [x] Local data/log dirs + backup plan.
- [x] Structured logging plan.
- [x] One-command research run script.

## Phase 1 — Research + Backtesting (P0)
- [x] Fetch and normalize Coinbase 1h candles (BTC, ETH, SOL, ADA).
- [x] Data validation (gaps + dedupe).
- [x] Backtest architecture: engine + strategies + runner.
- [x] Fee + slippage modeling (maker/taker, bps).
- [x] Artifacts per run: metrics.csv + fills.csv + config.json.
- [x] Walk-forward conventions + parameter discipline documented.

## Phase 2 — Paper/Sim Trading Loop (P0)
**Goal:** live-like loop with no real orders.
- [x] Market data ingestion (replay from historical candles).
- [x] Strategy runner that consumes the stream and emits signals.
- [x] Paper execution (simulated fills) using the same execution interface as live.
- [ ] End-to-end journaling for paper trades (fills + trades + daily report).

## Phase 3 — Risk Gatekeeper (P0)
**Goal:** block unsafe orders *before* execution.
- [ ] Hard limits: max loss/day, max loss/trade, max exposure.
- [ ] Cooldowns after losses/volatility spikes.
- [ ] Kill-switch (manual + automatic).
- [ ] Compliance tracking per trade (rules followed/violations).

## Phase 4 — Live Execution (Small, Controlled) (P1)
**Goal:** minimal live deployment with tight limits.
- [ ] Coinbase authenticated REST client + rate-limit handling.
- [ ] Order placement + idempotency + reconciliation.
- [ ] Alerts on failures + daily health check.
- [ ] Start with tiny notional and strict daily loss stop.

## Phase 5 — Monitoring + Ops (P1)
- [ ] Weekly review cadence (1-page summary).
- [ ] Backup/restore test (monthly).
- [ ] Incident drills (API errors, WS disconnects).

## Phase 6 — Research Dashboard (P1)
**Goal:** a single view for strategy performance and costs.
- [ ] Dashboard shows latest run per symbol.
- [ ] Equity + drawdown charts per strategy.
- [ ] Fees/slippage impact summary.
- [ ] Exportable HTML report.

## Next Up (Recommended Order)
1) Finish paper/sim journaling (fills → trades → daily report).
2) Implement risk gatekeeper (limits + cooldowns + kill-switch).
3) Wire Coinbase live execution (orders + reconciliation + alerts).
