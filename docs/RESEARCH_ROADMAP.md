# Research Roadmap — Backtesting + Strategy Lab

Goal: a tight, repeatable research pipeline that produces comparable results and clear promotion decisions.

## Phase 0 — Foundations (P0)
- [x] Data contract defined (OHLCV schema, timezone, gap policy).
- [x] Data loader validates and enforces contract.
- [x] Historical fetcher with overwrite/refresh support.
- [x] Walk-forward + parameter discipline documented.

## Phase 1 — Backtest Core (P0)
- [x] Shared backtest engine (execution timing + positions + equity).
- [x] Strategy registry with consistent signal interface.
- [x] Fee + slippage model (maker/taker, bps).
- [x] Run artifacts saved (metrics, fills, config, equity curves).

## Phase 2 — Strategy Library (P0)
- [x] Baselines (buy/hold, do-nothing).
- [x] Trend/mean-reversion mix (SMA/EMA, Donchian, Bollinger, RSI).
- [x] Momentum/time-based strategy.
- [x] Add 1 volatility-targeted strategy.
- [x] Add 1 regime filter (trend vs chop).

## Phase 3 — Experiment Discipline (P0)
- [x] Configs folder with versioned run params (fees, slippage, gap policy).
- [x] Run registry/index (symbol, timeframe, config hash, commit).
- [x] Deterministic run IDs (hash of config + data range).
- [ ] Compare-runs view (same symbol/timeframe across configs).

## Phase 4 — Robustness (P1)
- [x] Walk-forward runner (train/validation splits with no leakage).
- [ ] Sensitivity analysis (parameter stability across ranges).
- [ ] Stress tests (fees/slippage spikes, missing data segments).
- [ ] Out-of-sample holdout results captured.

## Phase 5 — Promotion Rules (P1)
- [ ] Strategy checklist template (thesis, failure modes, do-not-trade).
- [ ] Promotion gate: must pass 3 metrics thresholds + robustness tests.
- [ ] Versioned “approved strategies” list with date + rationale.

## Phase 6 — Research Reporting (P1)
- [ ] One-page research summary per run (HTML/MD).
- [ ] Equity + drawdown + exposure charts per strategy.
- [ ] Fees/slippage attribution chart.

## Phase 7 — Strategy Research Loop (P1)
**Disciplined workflow:**
- [ ] Define hypothesis.
- [ ] Build features.
- [ ] Train/select (parameter choice rules).
- [ ] Validate out-of-sample.
- [ ] Stress test.
- [ ] Ship to paper-trade.
- [ ] Compare live vs backtest (drift detection).

**Pitfalls to guard against:**
- [ ] Lookahead bias.
- [ ] Survivorship bias (if using equities).
- [ ] Overfitting.
- [ ] Regime dependence (only works in one period).

## Next Up (Recommended Order)
1) Create configs + run registry (Phase 3).
2) Add volatility-targeted strategy + regime filter (Phase 2).
3) Build walk-forward runner (Phase 4).
