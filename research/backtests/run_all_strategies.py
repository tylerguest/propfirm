from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from research.backtests.coinbase_backtest import (
    BacktestConfig,
    FeeSchedule,
    backtest_buy_and_hold,
    backtest_strategy,
    print_summary,
)
from research.backtests.engine import FillEvent
from research.data_contract import apply_gap_policy, infer_granularity_seconds, load_ohlcv_csv
from research.data_loader import DataSpec, find_processed_csv
from research.strategies.registry import list_strategies


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run all registered strategies on a dataset.")
    p.add_argument("--csv", default=None, help="Path to processed OHLCV CSV (default: newest match in data/processed).")
    p.add_argument("--symbol", default=None, help="Optional symbol filter (e.g., BTC-USD).")
    p.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols to run (overrides --symbol). Example: BTC-USD,ETH-USD,SOL-USD",
    )
    p.add_argument("--granularity-seconds", type=int, default=None, help="Override candle granularity in seconds.")
    p.add_argument("--execution", choices=["maker", "taker"], default=None, help="Assumed execution type (default: taker).")
    p.add_argument("--slippage-bps", type=float, default=None, help="Slippage model in bps (default: 5).")
    p.add_argument("--gap-cooldown-bars", type=int, default=None, help="Bars to block trades after a detected gap (default: 1).")
    p.add_argument(
        "--gap-policy",
        choices=["skip", "forward_fill", "segment"],
        default=None,
        help="Gap handling policy (default: segment).",
    )
    p.add_argument("--maker-fee-bps", type=float, default=None, help="Maker fee in bps (default: 2.5).")
    p.add_argument("--taker-fee-bps", type=float, default=None, help="Taker fee in bps (default: 6.5).")
    p.add_argument("--fast-sma", type=int, default=None, help="Fast SMA window (default: 20).")
    p.add_argument("--slow-sma", type=int, default=None, help="Slow SMA window (default: 100).")
    p.add_argument("--initial-cash", type=float, default=None, help="Initial cash in quote currency units (default: 1.0).")
    p.add_argument(
        "--save-artifacts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Save metrics/config outputs per run (default: true).",
    )
    p.add_argument(
        "--output-dir",
        default="research/output",
        help="Base directory for saved runs (default: research/output).",
    )
    p.add_argument(
        "--config",
        default=None,
        help="Optional JSON config file for run settings (default: none).",
    )
    return p.parse_args()


@dataclass(frozen=True)
class RunConfig:
    execution: str
    slippage_bps: float
    gap_cooldown_bars: int
    gap_policy: str
    maker_fee_bps: float
    taker_fee_bps: float
    fast_sma: int
    slow_sma: int
    initial_cash: float


def _load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise SystemExit(f"Config not found: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _resolve_run_config(args: argparse.Namespace) -> RunConfig:
    cfg = _load_config(args.config)

    def pick(name: str, default: Any) -> Any:
        value = getattr(args, name)
        if value is not None:
            return value
        if name in cfg:
            return cfg[name]
        return default

    return RunConfig(
        execution=str(pick("execution", "taker")),
        slippage_bps=float(pick("slippage_bps", 5.0)),
        gap_cooldown_bars=int(pick("gap_cooldown_bars", 1)),
        gap_policy=str(pick("gap_policy", "segment")),
        maker_fee_bps=float(pick("maker_fee_bps", 2.5)),
        taker_fee_bps=float(pick("taker_fee_bps", 6.5)),
        fast_sma=int(pick("fast_sma", 20)),
        slow_sma=int(pick("slow_sma", 100)),
        initial_cash=float(pick("initial_cash", 1.0)),
    )


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _sharpe(returns: pd.Series, *, periods_per_year: float) -> float:
    r = returns.dropna()
    if len(r) < 10:
        return float("nan")
    std = float(r.std(ddof=1))
    if std == 0.0:
        return float("nan")
    return float((r.mean() / std) * (periods_per_year**0.5))


def _cagr(equity: pd.Series, *, start: pd.Timestamp, end: pd.Timestamp) -> float:
    years = (end - start).total_seconds() / (365.25 * 24 * 3600)
    if years <= 0:
        return float("nan")
    return float(equity.iloc[-1] ** (1.0 / years) - 1.0)


def _metrics_row(
    *,
    name: str,
    equity: pd.Series,
    returns: pd.Series,
    trades: int,
    fees_paid: float,
    initial_cash: float,
    start: pd.Timestamp,
    end: pd.Timestamp,
    periods_per_year: float,
) -> dict[str, str]:
    total_return = float((equity.iloc[-1] / initial_cash) - 1.0)
    cagr = _cagr(equity / initial_cash, start=start, end=end)
    mdd = _max_drawdown(equity)
    sharpe = _sharpe(returns, periods_per_year=periods_per_year)
    fees_pct = (fees_paid / initial_cash) if initial_cash != 0 else float("nan")
    return {
        "strategy": name,
        "total_return": f"{total_return:.6f}",
        "cagr": f"{cagr:.6f}",
        "max_drawdown": f"{mdd:.6f}",
        "sharpe": f"{sharpe:.6f}",
        "trades": str(trades),
        "fees_paid_pct": f"{fees_pct:.6f}",
    }


def _get_git_commit() -> str:
    head_path = Path(".git/HEAD")
    if not head_path.exists():
        return "unknown"
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref = head.split(" ", 1)[1]
        ref_path = Path(".git") / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        return "unknown"
    return head[:40]


def _config_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _run_id(*, symbol: str, strategy_count: int, config_hash: str) -> str:
    return f"{symbol}_x{strategy_count}_{config_hash[:12]}"


def _append_registry_row(*, output_dir: Path, row: dict[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "index.csv"
    is_new = not index_path.exists()
    fieldnames = list(row.keys())
    with index_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def _write_artifacts(
    *,
    out_dir: Path,
    config: dict[str, object],
    metrics_rows: list[dict[str, str]],
    fills: list[FillEvent],
    equity_curves: dict[str, pd.Series],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")

    with (out_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "strategy",
                "total_return",
                "cagr",
                "max_drawdown",
                "sharpe",
                "trades",
                "fees_paid_pct",
            ],
        )
        writer.writeheader()
        for row in metrics_rows:
            writer.writerow(row)

    with (out_dir / "fills.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "time_utc",
                "strategy",
                "side",
                "price",
                "qty_base",
                "qty_quote",
                "fee_quote",
            ],
        )
        writer.writeheader()
        for fill in fills:
            writer.writerow(
                {
                    "time_utc": pd.Timestamp(fill.time_utc).isoformat().replace("+00:00", "Z"),
                    "strategy": fill.strategy,
                    "side": fill.side,
                    "price": f"{fill.price:.10f}".rstrip("0").rstrip("."),
                    "qty_base": f"{fill.qty_base:.10f}".rstrip("0").rstrip("."),
                    "qty_quote": f"{fill.qty_quote:.10f}".rstrip("0").rstrip("."),
                    "fee_quote": f"{fill.fee_quote:.10f}".rstrip("0").rstrip("."),
                }
            )

    for name, equity in equity_curves.items():
        safe = name.replace(" ", "_")
        path = out_dir / f"equity_{safe}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["time_utc", "equity"])
            writer.writeheader()
            for ts, value in equity.items():
                writer.writerow(
                    {
                        "time_utc": pd.Timestamp(ts).isoformat().replace("+00:00", "Z"),
                        "equity": f"{float(value):.10f}".rstrip("0").rstrip("."),
                    }
                )


def _run_for_symbol(args: argparse.Namespace, *, symbol: str | None) -> None:
    run_cfg = _resolve_run_config(args)
    if args.csv:
        csv_path = Path(args.csv)
    else:
        spec = DataSpec(symbol=symbol)
        csv_path = find_processed_csv(spec)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    df = load_ohlcv_csv(csv_path)
    granularity_seconds = int(args.granularity_seconds) if args.granularity_seconds else infer_granularity_seconds(csv_path, df)

    cfg = BacktestConfig(
        csv_path=csv_path,
        granularity_seconds=granularity_seconds,
        fee_schedule=FeeSchedule(maker_fee_bps=run_cfg.maker_fee_bps, taker_fee_bps=run_cfg.taker_fee_bps),
        execution=run_cfg.execution,
        slippage_bps=run_cfg.slippage_bps,
        gap_cooldown_bars=run_cfg.gap_cooldown_bars,
        fast_sma=run_cfg.fast_sma,
        slow_sma=run_cfg.slow_sma,
        initial_cash=run_cfg.initial_cash,
    )

    start = df.loc[0, "time"]
    end = df.loc[len(df) - 1, "time"]
    periods_per_year = (365.25 * 24 * 3600) / cfg.granularity_seconds

    segments, gap_ranges = apply_gap_policy(
        df,
        granularity_seconds=cfg.granularity_seconds,
        policy=str(run_cfg.gap_policy),
    )

    print(f"Dataset: {cfg.csv_path}")
    print(f"Rows: {len(df)}  Start: {start}  End: {end}  Granularity: {cfg.granularity_seconds}s")
    print(f"Gap policy: {run_cfg.gap_policy}")
    if gap_ranges:
        missing_candles = int(sum(((e - s).total_seconds() / cfg.granularity_seconds) + 1 for s, e in gap_ranges))
        print(f"Gaps: {len(gap_ranges)}  Missing candles (approx): {missing_candles}")
        for s, e in gap_ranges[:5]:
            print(f"- missing: {s} → {e}")
    else:
        print("Gaps: 0")

    if str(run_cfg.gap_policy) == "segment":
        if segments:
            df = max(segments, key=len)
    else:
        df = segments[0] if segments else df

    print(
        "Costs: "
        f"execution={cfg.execution} "
        f"maker_fee={cfg.fee_schedule.maker_fee_bps:.2f}bps "
        f"taker_fee={cfg.fee_schedule.taker_fee_bps:.2f}bps "
        f"slippage={cfg.slippage_bps:.2f}bps"
    )

    bh = backtest_buy_and_hold(df, cfg)
    print_summary(bh, start=start, end=end, periods_per_year=periods_per_year, initial_cash=cfg.initial_cash)

    metrics_rows = [
        _metrics_row(
            name=bh.name,
            equity=bh.equity,
            returns=bh.returns,
            trades=bh.trades,
            fees_paid=bh.fees_paid,
            initial_cash=cfg.initial_cash,
            start=start,
            end=end,
            periods_per_year=periods_per_year,
        )
    ]
    fills: list[FillEvent] = list(bh.fills)
    equity_curves: dict[str, pd.Series] = {"buy_and_hold": pd.Series(bh.equity.values, index=df["time"])}

    for name in list_strategies():
        result = backtest_strategy(df, cfg, strategy_name=name)
        print_summary(result, start=start, end=end, periods_per_year=periods_per_year, initial_cash=cfg.initial_cash)
        fills.extend(result.fills)
        equity_curves[name] = pd.Series(result.equity.values, index=df["time"])
        metrics_rows.append(
            _metrics_row(
                name=result.name,
                equity=result.equity,
                returns=result.returns,
                trades=result.trades,
                fees_paid=result.fees_paid,
                initial_cash=cfg.initial_cash,
                start=start,
                end=end,
                periods_per_year=periods_per_year,
            )
        )

    if args.save_artifacts:
        run_symbol = symbol or "latest"
        created_at = datetime.now(tz=UTC)
        strategies = list_strategies()
        config_payload = {
            "symbol": run_symbol,
            "csv_path": str(cfg.csv_path),
            "granularity_seconds": cfg.granularity_seconds,
            "data_start": pd.Timestamp(start).isoformat().replace("+00:00", "Z"),
            "data_end": pd.Timestamp(end).isoformat().replace("+00:00", "Z"),
            "gap_policy": run_cfg.gap_policy,
            "execution": cfg.execution,
            "maker_fee_bps": cfg.fee_schedule.maker_fee_bps,
            "taker_fee_bps": cfg.fee_schedule.taker_fee_bps,
            "slippage_bps": cfg.slippage_bps,
            "gap_cooldown_bars": cfg.gap_cooldown_bars,
            "fast_sma": cfg.fast_sma,
            "slow_sma": cfg.slow_sma,
            "initial_cash": cfg.initial_cash,
            "strategies": strategies,
        }
        cfg_hash = _config_hash(config_payload)
        output_root = Path(args.output_dir)
        run_dir = output_root / "runs" / _run_id(symbol=run_symbol, strategy_count=len(metrics_rows), config_hash=cfg_hash)
        _write_artifacts(
            out_dir=run_dir,
            config={
                **config_payload,
                "run_id": run_dir.name,
                "config_hash": cfg_hash,
                "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
                "git_commit": _get_git_commit(),
            },
            metrics_rows=metrics_rows,
            fills=fills,
            equity_curves=equity_curves,
        )
        _append_registry_row(
            output_dir=output_root,
            row={
                "run_id": run_dir.name,
                "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
                "symbol": run_symbol,
                "granularity_seconds": str(cfg.granularity_seconds),
                "data_start": pd.Timestamp(start).isoformat().replace("+00:00", "Z"),
                "data_end": pd.Timestamp(end).isoformat().replace("+00:00", "Z"),
                "gap_policy": str(run_cfg.gap_policy),
                "execution": cfg.execution,
                "maker_fee_bps": f"{cfg.fee_schedule.maker_fee_bps:.6f}",
                "taker_fee_bps": f"{cfg.fee_schedule.taker_fee_bps:.6f}",
                "slippage_bps": f"{cfg.slippage_bps:.6f}",
                "gap_cooldown_bars": str(cfg.gap_cooldown_bars),
                "fast_sma": str(cfg.fast_sma),
                "slow_sma": str(cfg.slow_sma),
                "initial_cash": f"{cfg.initial_cash:.6f}",
                "strategies": "|".join(strategies),
                "csv_path": str(cfg.csv_path),
                "config_hash": cfg_hash,
                "git_commit": _get_git_commit(),
            },
        )
        print(f"Wrote artifacts: {run_dir}")


def main() -> None:
    args = _parse_args()
    if args.csv and args.symbols:
        raise SystemExit("--symbols cannot be used with --csv (use one or the other).")

    if args.symbols:
        symbols = [s.strip() for s in str(args.symbols).split(",") if s.strip()]
    else:
        symbols = [str(args.symbol).strip()] if args.symbol else [None]

    for symbol in symbols:
        _run_for_symbol(args, symbol=symbol)


if __name__ == "__main__":
    main()
