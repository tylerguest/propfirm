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
  python3 research/backtests/run_all_strategies.py --csv data/processed/BTC-USD_3600s_2021-01-03_2026-01-02.csv --config research/configs/backtest_base.json
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

## Research Runner
- One-command research run:
  ```bash
  bash scripts/run_research.sh
  ```

## Outputs
- Backtest runs are saved under `research/output/runs/`
- Run registry lives at `research/output/index.csv`
- Sweeps live under `research/output/sweeps/`
