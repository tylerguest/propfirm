# Backtesting Conventions

## Walk-Forward Splits (Default)
- Use a rolling split: **2 years train / 1 year test**, stepped forward by 1 year.
- Example on 5 years:
  - Train: 2021–2022, Test: 2023
  - Train: 2022–2023, Test: 2024
  - Train: 2023–2024, Test: 2025

## Parameter Discipline
- Use a **single parameter grid** across all tickers.
- Choose parameters on **train slices only**.
- Evaluate on test slices without retuning.
- Prefer parameters that are **robust across symbols** over best-in-class on one symbol.

## Reporting
- Report metrics for each test slice and the combined test result.
