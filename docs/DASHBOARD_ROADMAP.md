# Dashboard Roadmap (Research + Execution + Risk)

Goal: a single hub that scales from research to live trading without rework.

## Sector 1 — Research Dashboard (P0)
- [x] Metrics table + totals per strategy
- [x] Fees paid % per strategy
- [x] Equity curve + drawdown (per strategy)
- [x] Parameter sweep heatmaps
- [x] Multi‑symbol comparison view

## Sector 2 — Paper/Live Execution (P0)
- [ ] Live/paper positions view (by symbol)
- [ ] Open orders + recent fills
- [ ] Current strategy signals
- [ ] Execution health (latency, errors, retries)

## Sector 3 — Risk & Compliance (P0)
- [ ] Daily loss limit remaining
- [ ] Max exposure by symbol
- [ ] Rule violations log
- [ ] Kill‑switch status + last triggered

## Sector 4 — Reporting (P1)
- [ ] Daily PnL summary (realized + unrealized)
- [ ] Weekly summary snapshot
- [ ] Fees + slippage attribution

## Sector 5 — Ops (P1)
- [ ] Data freshness / last update time
- [ ] Disk space + backup status
- [ ] Alerts history

## Phase Milestones
### Phase A — Command Center (P0)
- [ ] Top‑level “Today” panel (PnL, drawdown, exposure, limit remaining)

### Phase B — Compliance Status (P0)
- [ ] Risk limit status (green/yellow/red) with clear thresholds

## Next Up (Recommended)
1) Add a top “Today” strip (account equity, day PnL, risk headroom, last sync).
2) Add positions + recent fills panel for paper/live.
3) Add open orders + execution health (latency, errors, retries).
