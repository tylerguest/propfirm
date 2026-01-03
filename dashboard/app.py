from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st


def _candidate_run_dirs(root: Path) -> list[Path]:
    candidates = [root]
    runs_child = root / "runs"
    if runs_child.exists():
        candidates.append(runs_child)
    seen = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _load_runs(runs_dir: Path) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    for candidate in _candidate_run_dirs(runs_dir):
        if not candidate.exists():
            continue
        for path in candidate.iterdir():
            if not path.is_dir():
                continue
            metrics = path / "metrics.csv"
            config = path / "config.json"
            fills = path / "fills.csv"
            if not metrics.exists() or not config.exists():
                continue
            try:
                cfg = json.loads(config.read_text(encoding="utf-8"))
                df = pd.read_csv(metrics)
            except Exception:
                continue
            runs.append(
                {
                    "path": path,
                    "symbol": str(cfg.get("symbol") or "unknown"),
                    "config": cfg,
                    "metrics": df,
                    "fills_path": fills if fills.exists() else None,
                }
            )
    return runs


def _pick_latest_runs(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for run in runs:
        symbol = str(run["symbol"])
        current = latest.get(symbol)
        run_ts = str(run["config"].get("created_at_utc") or "")
        current_ts = str(current["config"].get("created_at_utc") or "") if current else ""
        if current is None or run_ts > current_ts:
            latest[symbol] = run
    return list(latest.values())


def _load_latest_sweep(sweeps_dir: Path, *, symbol: str) -> pd.DataFrame | None:
    if not sweeps_dir.exists():
        return None
    candidates = sorted(sweeps_dir.glob(f"{symbol}_sma_crossover_*.csv"), reverse=True)
    if not candidates:
        return None
    try:
        return pd.read_csv(candidates[0])
    except Exception:
        return None


def _plot_heatmap(df: pd.DataFrame, *, x: str, y: str, value: str, title: str) -> None:
    pivot = df.pivot(index=y, columns=x, values=value)
    plt.figure(figsize=(6, 4))
    plt.imshow(pivot, aspect="auto")
    plt.colorbar(label=value)
    plt.xticks(range(len(pivot.columns)), pivot.columns, rotation=45)
    plt.yticks(range(len(pivot.index)), pivot.index)
    plt.title(title)
    plt.xlabel(x)
    plt.ylabel(y)
    st.pyplot(plt.gcf())
    plt.close()


def main() -> None:
    st.set_page_config(page_title="Research Dashboard", layout="wide")
    st.title("Research Dashboard")

    runs_root = Path(st.sidebar.text_input("Runs directory", "research/output"))
    runs = _load_runs(runs_root)
    if not runs:
        st.info("No run artifacts found. Run backtests first.")
        return

    latest_only = st.sidebar.checkbox("Latest run per symbol", value=True)
    if latest_only:
        runs = _pick_latest_runs(runs)

    symbols = sorted({str(r["symbol"]) for r in runs})
    symbol = st.sidebar.selectbox("Symbol", symbols)
    symbol_runs = [r for r in runs if r["symbol"] == symbol]
    run_ids = [
        r["path"].name
        for r in sorted(
            symbol_runs,
            key=lambda r: str(r["config"].get("created_at_utc") or ""),
            reverse=True,
        )
    ]
    run_id = st.sidebar.selectbox("Run", run_ids)
    run = next(r for r in symbol_runs if r["path"].name == run_id)

    st.subheader(f"{symbol} — {run_id}")
    st.caption(str(run["path"]))

    metrics = run["metrics"]
    metrics["total_return"] = pd.to_numeric(metrics["total_return"], errors="coerce")
    metrics["fees_paid_pct"] = pd.to_numeric(metrics["fees_paid_pct"], errors="coerce")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Total Return (by strategy)**")
        st.bar_chart(metrics.set_index("strategy")["total_return"])
    with col2:
        st.markdown("**Fees Paid % (by strategy)**")
        st.bar_chart(metrics.set_index("strategy")["fees_paid_pct"])

    st.markdown("**Metrics Table**")
    st.dataframe(metrics.sort_values(by="total_return", ascending=False), width="stretch")

    with st.expander("Run config"):
        st.json(run["config"])

    st.markdown("**Equity + Drawdown (per strategy)**")
    equity_files = sorted(run["path"].glob("equity_*.csv"))
    if equity_files:
        strategy_names = [p.stem.replace("equity_", "") for p in equity_files]
        strat = st.selectbox("Equity strategy", strategy_names)
        eq_path = run["path"] / f"equity_{strat}.csv"
        eq_df = pd.read_csv(eq_path)
        eq_df["time_utc"] = pd.to_datetime(eq_df["time_utc"], utc=True)
        eq_df = eq_df.sort_values("time_utc")
        eq_df["equity"] = pd.to_numeric(eq_df["equity"], errors="coerce")
        eq_df["drawdown"] = (eq_df["equity"] / eq_df["equity"].cummax()) - 1.0
        col1, col2 = st.columns(2)
        with col1:
            st.line_chart(eq_df.set_index("time_utc")["equity"])
        with col2:
            st.line_chart(eq_df.set_index("time_utc")["drawdown"])
    else:
        st.info("No equity curves found. Re-run backtests to generate equity_*.csv files.")

    st.markdown("**Parameter Sweep (SMA crossover)**")
    sweep_df = _load_latest_sweep(Path("research/output/sweeps"), symbol=symbol)
    if sweep_df is None:
        st.info("No SMA sweep found. Run: python3 research/backtests/param_sweep.py --symbol <SYMBOL>")
    else:
        sweep_df["fast_sma"] = sweep_df["fast_sma"].astype(int)
        sweep_df["slow_sma"] = sweep_df["slow_sma"].astype(int)
        sweep_df["total_return"] = pd.to_numeric(sweep_df["total_return"], errors="coerce")
        _plot_heatmap(
            sweep_df,
            x="fast_sma",
            y="slow_sma",
            value="total_return",
            title=f"{symbol} SMA sweep (total_return)",
        )

    fills_path = run["fills_path"]
    if fills_path:
        fills = pd.read_csv(fills_path)
        st.markdown("**Fills (paper/backtest)**")
        st.dataframe(fills.tail(200), width="stretch")

    st.markdown("**Multi-symbol comparison (latest runs)**")
    rows: list[pd.DataFrame] = []
    for r in runs:
        m = r["metrics"].copy()
        m["symbol"] = r["symbol"]
        rows.append(m)
    all_metrics = pd.concat(rows, ignore_index=True)
    all_metrics["total_return"] = pd.to_numeric(all_metrics["total_return"], errors="coerce")
    comparison = (
        all_metrics.groupby("strategy", as_index=False)["total_return"]
        .median()
        .sort_values(by="total_return", ascending=False)
    )
    st.bar_chart(comparison.set_index("strategy")["total_return"])
    st.dataframe(comparison, width="stretch")


if __name__ == "__main__":
    main()
