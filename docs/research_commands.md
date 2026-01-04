# Research Commands

## Backtests
- Run all strategies (single symbol):
  ```bash
  python3 research/backtests/run_all_strategies.py --symbol BTC-USD --config research/configs/backtest_base.json
  ```
- Run all strategies (multiple symbols):
  ```bash
  python3 research/backtests/run_all_strategies.py --symbols BTC-USD,ETH-USD,SOL-USD,ADA-USD --config research/configs/backtest_base.json
  ```
- Run with a specific CSV:
  ```bash
  python3 research/backtests/run_all_strategies.py --csv data/processed/BTC-USD_1h_2021-01-03_2026-01-02.csv --config research/configs/backtest_base.json
  ```
- Walk-forward splits (2y train / 1y test):
  ```bash
  python3 research/backtests/walk_forward.py --symbol BTC-USD --train-years 2 --test-years 1 --step-years 1
  ```

## Parameter Sweeps
- SMA sweep (example):
  ```bash
  python3 research/backtests/param_sweep.py --symbol BTC-USD
  ```

## Data Fetch
- Update/overwrite historical candles:
  ```bash
  python3 research/fetch_history.py --product-ids BTC-USD,ETH-USD,SOL-USD,ADA-USD --granularity-seconds 3600 --overwrite
  ```
- Fetch multiple granularities for multiple symbols:
  ```bash
  python3 research/fetch_history.py --product-ids BTC-USD,ETH-USD,SOL-USD --granularities-seconds 300,900,3600
  ```
- Fetch all USD spot products from Coinbase Exchange:
  ```bash
  python3 research/fetch_history.py --fetch-products --quote-currency USD --granularities-seconds 3600
  ```

## Index Maintenance
- Rebuild dataset index from existing CSVs:
  ```bash
  python3 scripts/rebuild_processed_index.py
  ```

## Research Runner
- One-command research run:
  ```bash
  bash scripts/run_research.sh
  ```

## Outputs
- Backtest runs are saved under `research/output/runs/`
- Run registry lives at `research/output/index.csv`
- Sweeps live under `research/output/sweeps/`
