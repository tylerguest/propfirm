#!/usr/bin/env bash
set -euo pipefail

# Fetch multi-symbol data and run all strategies on each symbol.
python3 research/fetch_history.py --product-ids BTC-USD,ETH-USD,SOL-USD,ADA-USD --granularity-seconds 3600 --years 5
python3 research/backtests/run_all_strategies.py --symbols BTC-USD,ETH-USD,SOL-USD,ADA-USD
