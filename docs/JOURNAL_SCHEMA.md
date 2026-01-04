# Journal Schema — Required Fields + Usage

This doc locks the minimum required columns for paper/sim journaling.

## fills.csv (source of truth)
Required columns (used by `journal/tools/derive_trades_from_fills.py`):
- `fill_id` (dedupe key)
- `time_utc` (ISO8601 UTC)
- `symbol`
- `strategy`
- `side` (buy/sell)
- `price`
- `qty_base`
- `qty_quote`
- `fee_quote`
- `liquidity` (maker/taker)
- `order_id`
- `trade_id` (optional; derived if blank)

Recommended (kept if available):
- `venue`, `account`, `order_type`, `fee_rate_bps`, `trade_type`, `sequence_timestamp`
- `size_in_quote`, `retail_portfolio_id`, `notes`

## trades.csv (derived summary)
Required columns (filled by derivation):
- `trade_id`
- `created_at_utc`, `updated_at_utc`
- `venue`, `account`, `symbol`
- `product_type`
- `direction`
- `entry_time_utc`, `entry_avg_price`, `entry_qty_base`, `entry_qty_quote`, `entry_fee_quote`
- `exit_time_utc`, `exit_avg_price`, `exit_qty_base`, `exit_qty_quote`, `exit_fee_quote`
- `realized_pnl_quote`
- `holding_time_hours`

Optional plan/context (manual or automated later):
- `strategy`, `setup`, `thesis`, `planned_entry_*`, `planned_stop_price`
- `planned_take_profit_price`, `planned_size_quote`, `planned_risk_quote`
- `planned_r_multiple_target`, `rules_followed`, `rule_violations`, `tags`, `notes`, `post_mortem`

## ledger.csv (cashflows)
Required columns:
- `time_utc` (ISO8601 UTC)
- `type` (deposit/withdrawal/fee/transfer)
- `amount`
- `currency`
- `transaction_id` (dedupe key when available)

Recommended:
- `venue`, `account`, `reference`, `notes`

## Dedupe Rules (current)
See `journal/config.json`:
- fills: `fill_id` (fallback: order/time/symbol/side/price/qty_base)
- trades: `trade_id`
- ledger: `transaction_id` + time/amount/currency
