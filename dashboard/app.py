from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from research.data_contract import compute_gaps
from research.fetch_history import fetch_exchange_products

@st.cache_data(show_spinner=False)
def _read_json(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _read_csv(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path)


@st.cache_data(show_spinner=False)
def _load_registry_cached(root: str, mtime: float) -> pd.DataFrame | None:
    index_path = Path(root) / "index.csv"
    if not index_path.exists():
        return None
    try:
        return pd.read_csv(index_path)
    except Exception:
        return None


def _load_registry(root: Path) -> pd.DataFrame | None:
    index_path = root / "index.csv"
    if not index_path.exists():
        return None
    return _load_registry_cached(str(root), index_path.stat().st_mtime)


def _products_cache_path() -> Path:
    return Path("data/processed/products.json")


def _load_products_cache() -> list[dict[str, Any]]:
    path = _products_cache_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [p for p in payload if isinstance(p, dict)]
    except Exception:
        return []
    return []


def _save_products_cache(products: list[dict[str, Any]]) -> None:
    path = _products_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(products, indent=2, sort_keys=True), encoding="utf-8")


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


def _run_dir_signature(root: Path) -> tuple[tuple[str, float], ...]:
    entries: list[tuple[str, float]] = []
    for candidate in _candidate_run_dirs(root):
        if not candidate.exists():
            continue
        for path in candidate.iterdir():
            if not path.is_dir():
                continue
            metrics = path / "metrics.csv"
            key_path = metrics if metrics.exists() else path
            try:
                entries.append((str(path), key_path.stat().st_mtime))
            except OSError:
                continue
    return tuple(sorted(entries, key=lambda item: item[0]))


@st.cache_data(show_spinner=False)
def _load_runs_cached(root_str: str, signature: tuple[tuple[str, float], ...]) -> list[dict[str, object]]:
    root = Path(root_str)
    runs: list[dict[str, object]] = []
    for candidate in _candidate_run_dirs(root):
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
                cfg = _read_json(str(config), config.stat().st_mtime)
                df = _read_csv(str(metrics), metrics.stat().st_mtime)
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
                cfg = _read_json(str(config), config.stat().st_mtime)
                df = _read_csv(str(metrics), metrics.stat().st_mtime)
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
        return _read_csv(str(candidates[0]), candidates[0].stat().st_mtime)
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


@st.cache_data(show_spinner=False)
def _validate_dataset(path: str, granularity_seconds: int, mtime: float) -> dict[str, object]:
    df = _read_csv(path, mtime)
    if df.empty:
        return {"rows": 0}
    if "time" not in df.columns:
        return {"rows": len(df), "error": "missing time column"}
    df["time"] = pd.to_datetime(df["time"], utc=True)
    gap, gap_ranges = compute_gaps(df, granularity_seconds=int(granularity_seconds))
    missing_candles = 0
    for start, end in gap_ranges:
        missing_candles += int(((end - start).total_seconds() / granularity_seconds) + 1)
    return {
        "rows": len(df),
        "start": str(df["time"].min()),
        "end": str(df["time"].max()),
        "gaps": int(gap.sum()),
        "missing_candles": int(missing_candles),
        "gap_ranges": gap_ranges,
    }


def _load_dataset_symbols(index_path: Path) -> list[str]:
    if not index_path.exists():
        return []
    df = _read_csv(str(index_path), index_path.stat().st_mtime)
    if "product_id" not in df.columns:
        return []
    return sorted({str(s) for s in df["product_id"].dropna().tolist()})


def main() -> None:
    st.set_page_config(page_title="Research Dashboard", layout="wide", initial_sidebar_state="collapsed")
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&display=swap');

        :root {
          --bg: #0b0f14;
          --panel: #121824;
          --panel-2: #0f141e;
          --accent: #3dd6c6;
          --accent-2: #f59e0b;
          --muted: #8b94a7;
          --text: #e6edf3;
        }

        .stApp {
          background: radial-gradient(1200px 600px at 20% -10%, rgba(61, 214, 198, 0.18), transparent),
                      radial-gradient(800px 500px at 90% 10%, rgba(245, 158, 11, 0.12), transparent),
                      var(--bg);
          color: var(--text);
          font-family: "Space Grotesk", ui-sans-serif, sans-serif;
        }

        h1, h2, h3, h4 {
          font-family: "Space Grotesk", ui-sans-serif, sans-serif;
          letter-spacing: 0.4px;
        }

        .os-title {
          background: linear-gradient(120deg, rgba(61, 214, 198, 0.2), rgba(18, 24, 36, 0.9));
          padding: 14px 16px;
          border-radius: 12px;
          border: 1px solid rgba(61, 214, 198, 0.35);
          margin-bottom: 12px;
        }

        .panel {
          background: var(--panel);
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.06);
          padding: 14px 16px;
        }

        .panel-sub {
          background: var(--panel-2);
          border-radius: 12px;
          border: 1px solid rgba(255,255,255,0.05);
          padding: 10px 12px;
        }

        .metric-label {
          color: var(--muted);
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div class='os-title'><h1>Research Command Center</h1></div>", unsafe_allow_html=True)

    runs_root = Path("research/output")
    if "load_runs" not in st.session_state:
        st.session_state.load_runs = False
    latest_only = False

    all_runs: list[dict[str, object]] = []
    if st.session_state.load_runs:
        with st.spinner("Loading run artifacts..."):
            signature = _run_dir_signature(runs_root)
            all_runs = _load_runs_cached(str(runs_root), signature)

    runs = all_runs
    header_slot = st.empty()

    def _render_header(title: str | None, subtitle: str | None) -> None:
        if not title:
            header_slot.empty()
            return
        header_slot.markdown(
            f"<div class='panel'><h3>{title}</h3><p>{subtitle or ''}</p></div>",
            unsafe_allow_html=True,
        )

    tab = st.radio(
        "Tab",
        ["Data", "Run", "Walk-Forward", "Overview", "Run Detail", "Compare", "Sweeps", "Fills", "Multi-Symbol"],
        horizontal=True,
        label_visibility="collapsed",
        key="active_tab",
    )

    if tab == "Data":
        _render_header("Research Command Center", "Data fetch and dataset index.")
        st.markdown("**Data Fetch (Coinbase Exchange)**")
        st.caption("Runs `research/fetch_history.py` to build your local dataset index.")
        products_cache = _load_products_cache()
        index_path = Path("data/processed/index.csv")
        with st.form("fetch_history"):
            col_a, col_b = st.columns([2, 1])
            with col_a:
                quote_currency = st.text_input("Quote currency filter", value="USD")
            with col_b:
                refresh_products = st.form_submit_button("Refresh product list")

            filtered_products = products_cache
            if quote_currency:
                filtered_products = [
                    p for p in products_cache if str(p.get("quote_currency", "")).upper() == quote_currency.upper()
                ]
            product_ids = sorted({str(p.get("id")) for p in filtered_products if p.get("id")})

            if product_ids:
                product_ids_selected = st.multiselect(
                    "Product IDs (multi-select)",
                    options=product_ids,
                    default=[],
                )
                product_ids_input = ",".join(product_ids_selected)
            else:
                st.info("No cached products. Click “Refresh product list” to load from Coinbase.")
                product_ids_input = st.text_input("Product IDs (comma-separated)", value="")

            products_file = st.text_input("Products file (JSON/CSV)", value="")
            fetch_products = st.checkbox("Fetch products from exchange", value=False)
            if index_path.exists():
                index_df = _read_csv(str(index_path), index_path.stat().st_mtime)
                known_granularities = sorted(
                    {int(g) for g in index_df["granularity_seconds"].dropna().tolist()}
                )
            else:
                known_granularities = []

            standard_granularities = [60, 300, 900, 3600, 21600, 86400]
            if known_granularities:
                options = sorted(set(known_granularities + standard_granularities))
                selected_granularities = st.multiselect(
                    "Granularities (seconds)",
                    options=options,
                    default=[],
                )
                granularities_input = ",".join(str(g) for g in selected_granularities)
            else:
                selected_granularities = st.multiselect(
                    "Granularities (seconds)",
                    options=standard_granularities,
                    default=[],
                )
                granularities_input = ",".join(str(g) for g in selected_granularities)
            years = st.number_input("Years back", min_value=1, max_value=20, value=5, step=1)
            start = st.text_input("Start (ISO8601, optional)", value="")
            end = st.text_input("End (ISO8601, optional)", value="")
            limit_per_request = st.number_input("Limit per request", min_value=50, max_value=300, value=300, step=10)
            attempt_gap_fill = st.checkbox("Attempt gap fill", value=True)
            base_url = st.text_input("Base URL", value="https://api.exchange.coinbase.com")
            raw_dir = st.text_input("Raw dir", value="data/raw")
            processed_dir = st.text_input("Processed dir", value="data/processed")
            submitted = st.form_submit_button("Fetch data")

        if refresh_products:
            with st.spinner("Fetching product list..."):
                try:
                    products = fetch_exchange_products(base_url=base_url)
                    _save_products_cache(products)
                    st.success(f"Loaded {len(products)} products from Coinbase.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed to load products: {exc}")

        if submitted:
            args = [sys.executable, "research/fetch_history.py"]
            if fetch_products:
                args.append("--fetch-products")
                if quote_currency:
                    args += ["--quote-currency", quote_currency]
            elif products_file.strip():
                args += ["--products-file", products_file.strip()]
            else:
                product_ids = [s.strip() for s in product_ids_input.split(",") if s.strip()]
                if product_ids:
                    args += ["--product-ids", ",".join(product_ids)]
            granularities = [s.strip() for s in granularities_input.split(",") if s.strip()]
            if granularities:
                args += ["--granularities-seconds", ",".join(granularities)]
            if start.strip():
                args += ["--start", start.strip()]
            if end.strip():
                args += ["--end", end.strip()]
            else:
                args += ["--years", str(int(years))]
            args += ["--limit-per-request", str(int(limit_per_request))]
            if attempt_gap_fill:
                args.append("--attempt-gap-fill")
            else:
                args.append("--no-attempt-gap-fill")
            if base_url:
                args += ["--base-url", base_url]
            if raw_dir:
                args += ["--raw-dir", raw_dir]
            if processed_dir:
                args += ["--processed-dir", processed_dir]

            st.markdown("**Fetch output (live)**")
            output_placeholder = st.empty()
            log_lines: list[str] = []
            with st.spinner("Fetching data..."):
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    log_lines.append(line.rstrip())
                    output_placeholder.text_area(
                        "Log",
                        value="\n".join(log_lines[-400:]),
                        height=320,
                        disabled=True,
                    )
                returncode = process.wait()

            if returncode == 0:
                st.success("Fetch completed.")
            else:
                st.error(f"Fetch failed with exit code {returncode}.")

        st.markdown("**Dataset Index**")
        index_path = Path("data/processed/index.csv")
        if index_path.exists():
            index_df = _read_csv(str(index_path), index_path.stat().st_mtime)
            st.dataframe(index_df.tail(200), width="stretch")
            st.caption("Validation is enforced during fetch; gaps/missing candles are reported in the live log.")
        else:
            st.info("No index.csv found yet. Run a fetch to generate it.")

    elif tab == "Run":
        _render_header("Research Command Center", "Create backtest runs from datasets.")
        st.markdown("**Create Run (Backtest Runner)**")
        st.caption("Runs `research/backtests/run_all_strategies.py` from the dashboard.")
        with st.form("run_backtest"):
            symbols_input = ""
            index_path = Path("data/processed/index.csv")
            refresh_datasets = st.form_submit_button("Refresh dataset list")
            if refresh_datasets:
                st.cache_data.clear()
            if index_path.exists():
                index_df = _read_csv(str(index_path), index_path.stat().st_mtime)
                available_symbols = sorted({str(s) for s in index_df["product_id"].dropna().tolist()})
                available_granularities = sorted(
                    {int(g) for g in index_df["granularity_seconds"].dropna().tolist()}
                )
            else:
                index_df = None
                available_symbols = []
                available_granularities = []

            if available_granularities:
                selected_granularity = st.selectbox(
                    "Granularity (seconds)",
                    options=available_granularities,
                    index=0,
                )
            else:
                selected_granularity = None

            if available_symbols and index_df is not None:
                filtered_symbols = [
                    str(s)
                    for s in index_df[
                        index_df["granularity_seconds"].astype(int)
                        == (selected_granularity if selected_granularity is not None else index_df["granularity_seconds"])
                    ]["product_id"]
                    .dropna()
                    .tolist()
                ]
                if filtered_symbols:
                    available_symbols = sorted(set(filtered_symbols))
                selected_symbols = st.multiselect(
                    "Symbols (select from available datasets)",
                    options=available_symbols,
                    default=[s for s in ["BTC-USD", "ETH-USD", "SOL-USD"] if s in available_symbols],
                )
                symbols_input = ",".join(selected_symbols)
            else:
                symbols_input = st.text_input("Symbols (comma-separated)", value="BTC-USD")
            years_back = st.number_input("Years back (optional)", min_value=1, max_value=20, value=5, step=1)
            start = st.text_input("Start (ISO8601, optional)", value="")
            end = st.text_input("End (ISO8601, optional)", value="")
            config_path = st.text_input("Config path", value="research/configs/backtest_base.json")
            output_dir = st.text_input("Output directory", value="research/output")
            save_artifacts = st.checkbox("Save artifacts", value=True)
            extra_args = st.text_input("Extra CLI args (optional)", value="")
            submitted = st.form_submit_button("Run backtest")

        if submitted:
            args = [sys.executable, "research/backtests/run_all_strategies.py"]
            symbols = [s.strip() for s in symbols_input.split(",") if s.strip()]
            if len(symbols) == 1:
                args += ["--symbol", symbols[0]]
            elif symbols:
                args += ["--symbols", ",".join(symbols)]
            if config_path:
                args += ["--config", config_path]
            if output_dir:
                args += ["--output-dir", output_dir]
            if start.strip():
                args += ["--start", start.strip()]
            if end.strip():
                args += ["--end", end.strip()]
            elif years_back:
                args += ["--years", str(int(years_back))]
            args.append("--save-artifacts" if save_artifacts else "--no-save-artifacts")
            if extra_args.strip():
                args += extra_args.split()

            st.markdown("**Run output (live)**")
            output_placeholder = st.empty()
            log_lines: list[str] = []
            with st.spinner("Running backtest..."):
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    log_lines.append(line.rstrip())
                    output_placeholder.text_area(
                        "Log",
                        value="\n".join(log_lines[-400:]),
                        height=320,
                        disabled=True,
                    )
                returncode = process.wait()

            if returncode == 0:
                st.success("Backtest completed.")
                st.session_state.load_runs = True
            else:
                st.error(f"Backtest failed with exit code {returncode}.")

    elif tab == "Walk-Forward":
        _render_header("Research Command Center", "Walk-forward evaluation.")
        st.markdown("**Walk-Forward Runner**")
        st.caption("Runs `research/backtests/walk_forward.py` and stores results under `research/output/walk_forward`.")
        with st.form("walk_forward"):
            wf_csv = None
            wf_symbol = "BTC-USD"
            index_path = Path("data/processed/index.csv")
            if index_path.exists():
                wf_index = _read_csv(str(index_path), index_path.stat().st_mtime)
                if not wf_index.empty:
                    wf_index["granularity_seconds"] = wf_index["granularity_seconds"].astype(int)
                    wf_index = wf_index.sort_values(["product_id", "granularity_seconds", "dataset_end"])
                    dataset_choices = [
                        f"{row['product_id']} | {row['granularity_seconds']}s | {row['processed_path']}"
                        for _, row in wf_index.iterrows()
                    ]
                    selected = st.selectbox("Dataset (from index)", dataset_choices)
                    selected_row = wf_index.iloc[dataset_choices.index(selected)]
                    wf_csv = str(selected_row["processed_path"])
                    wf_symbol = str(selected_row["product_id"])
            if wf_csv is None:
                wf_symbol = st.text_input("Symbol", value="BTC-USD")
            wf_train = st.number_input("Train years", min_value=1, max_value=10, value=2, step=1)
            wf_test = st.number_input("Test years", min_value=1, max_value=5, value=1, step=1)
            wf_step = st.number_input("Step years", min_value=1, max_value=5, value=1, step=1)
            wf_gap_policy = st.selectbox("Gap policy", options=["segment", "skip", "forward_fill"], index=0)
            wf_output = st.text_input("Output directory", value="research/output/walk_forward")
            wf_extra = st.text_input("Extra CLI args (optional)", value="")
            wf_submitted = st.form_submit_button("Run walk-forward")

        if wf_submitted:
            args = [sys.executable, "research/backtests/walk_forward.py"]
            if wf_csv:
                args += ["--csv", wf_csv]
            else:
                args += ["--symbol", wf_symbol.strip()]
            args += [
                "--train-years",
                str(int(wf_train)),
                "--test-years",
                str(int(wf_test)),
                "--step-years",
                str(int(wf_step)),
                "--gap-policy",
                str(wf_gap_policy),
                "--output-dir",
                wf_output.strip(),
            ]
            if wf_extra.strip():
                args += wf_extra.split()

            st.markdown("**Walk-forward output (live)**")
            output_placeholder = st.empty()
            log_lines: list[str] = []
            with st.spinner("Running walk-forward..."):
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    log_lines.append(line.rstrip())
                    output_placeholder.text_area(
                        "Log",
                        value="\n".join(log_lines[-400:]),
                        height=320,
                        disabled=True,
                    )
                returncode = process.wait()

            if returncode == 0:
                st.success("Walk-forward completed.")
            else:
                st.error(f"Walk-forward failed with exit code {returncode}.")

        st.markdown("**Latest Walk-Forward Results**")
        wf_root = Path("research/output/walk_forward")
        if wf_root.exists():
            candidates = sorted([p for p in wf_root.iterdir() if p.is_dir()], reverse=True)
        else:
            candidates = []

        if candidates:
            latest = candidates[0]
            metrics_path = latest / "metrics.csv"
            config_path = latest / "config.json"
            if metrics_path.exists():
                wf_metrics = _read_csv(str(metrics_path), metrics_path.stat().st_mtime)
                if "phase" in wf_metrics.columns:
                    wf_metrics["total_return"] = pd.to_numeric(wf_metrics["total_return"], errors="coerce")
                    wf_metrics["sharpe"] = pd.to_numeric(wf_metrics["sharpe"], errors="coerce")
                    split = st.selectbox(
                        "Split",
                        options=sorted(wf_metrics["split_id"].unique().tolist()),
                        index=0,
                    )
                    phase = st.selectbox("Phase", options=sorted(wf_metrics["phase"].unique().tolist()), index=0)
                    subset = wf_metrics[(wf_metrics["split_id"] == split) & (wf_metrics["phase"] == phase)]
                    st.dataframe(subset.sort_values(by="total_return", ascending=False), width="stretch")
            else:
                st.info("No metrics.csv found in latest walk-forward run.")

            if config_path.exists():
                with st.expander("Walk-forward config"):
                    st.json(_read_json(str(config_path), config_path.stat().st_mtime))
        else:
            st.info("No walk-forward runs found yet.")

    elif tab == "Overview":
        if not st.session_state.load_runs:
            st.session_state.load_runs = True
            st.rerun()
        if not runs:
            st.info("No run artifacts found. Run backtests first.")
            return
        symbols = sorted({str(r["symbol"]) for r in runs})
        symbol = st.selectbox("Symbol", symbols, key="overview_symbol")
        symbol_runs = [r for r in runs if r["symbol"] == symbol]
        run_ids = [
            r["path"].name
            for r in sorted(
                symbol_runs,
                key=lambda r: str(r["config"].get("created_at_utc") or ""),
                reverse=True,
            )
        ]
        run_id = st.selectbox("Run", run_ids, key="overview_run")
        run = next(r for r in symbol_runs if r["path"].name == run_id)
        _render_header(f"{symbol} — {run_id}", str(run["path"]))
        metrics = run["metrics"]
        metrics["total_return"] = pd.to_numeric(metrics["total_return"], errors="coerce")
        metrics["fees_paid_pct"] = pd.to_numeric(metrics["fees_paid_pct"], errors="coerce")
        with st.spinner("Loading overview..."):
            view_mode = st.radio(
                "Overview mode",
                options=["Regular run", "Walk-forward (latest)"],
                horizontal=True,
            )

            if view_mode == "Walk-forward (latest)":
                wf_root = Path("research/output/walk_forward")
                wf_runs = sorted([p for p in wf_root.iterdir() if p.is_dir()], reverse=True) if wf_root.exists() else []
                if not wf_runs:
                    st.info("No walk-forward runs found.")
                    return
                wf_options: list[str] = []
                wf_map: dict[str, Path] = {}
                for run_dir in wf_runs:
                    cfg_path = run_dir / "config.json"
                    symbol_label = "unknown"
                    range_label = ""
                    if cfg_path.exists():
                        try:
                            cfg = _read_json(str(cfg_path), cfg_path.stat().st_mtime)
                            symbol_label = str(cfg.get("symbol") or "unknown")
                            data_start = str(cfg.get("data_start") or "")
                            data_end = str(cfg.get("data_end") or "")
                            if "T" in data_start and "T" in data_end:
                                range_label = f"{data_start.split('T')[0]}→{data_end.split('T')[0]}"
                        except Exception:
                            symbol_label = "unknown"
                    label = f"{symbol_label} | {range_label} | {run_dir.name}"
                    wf_options.append(label)
                    wf_map[label] = run_dir

                default_label = wf_options[0]
                for label in wf_options:
                    if symbol and label.startswith(f"{symbol} |"):
                        default_label = label
                        break

                selected_wf = st.selectbox("Walk-forward run", options=wf_options, index=wf_options.index(default_label))
                wf_latest = wf_map[selected_wf]
                _render_header(f"Walk-forward — {selected_wf}", str(wf_latest))
                wf_metrics_path = wf_latest / "metrics.csv"
                if not wf_metrics_path.exists():
                    st.info("No metrics.csv found in latest walk-forward run.")
                    return
                wf_metrics = _read_csv(str(wf_metrics_path), wf_metrics_path.stat().st_mtime)
                if wf_metrics.empty:
                    st.info("Walk-forward metrics are empty.")
                    return
                wf_metrics["total_return"] = pd.to_numeric(wf_metrics["total_return"], errors="coerce")
                wf_metrics["fees_paid_pct"] = pd.to_numeric(wf_metrics["fees_paid_pct"], errors="coerce")

                split = st.selectbox(
                    "Split",
                    options=sorted(wf_metrics["split_id"].unique().tolist()),
                    index=0,
                    key="wf_overview_split",
                )
                phase = st.selectbox(
                    "Phase",
                    options=sorted(wf_metrics["phase"].unique().tolist()),
                    index=0,
                    key="wf_overview_phase",
                )
                wf_subset = wf_metrics[(wf_metrics["split_id"] == split) & (wf_metrics["phase"] == phase)]

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Total Return (by strategy)**")
                    st.bar_chart(wf_subset.set_index("strategy")["total_return"])
                with col2:
                    st.markdown("**Fees Paid % (by strategy)**")
                    st.bar_chart(wf_subset.set_index("strategy")["fees_paid_pct"])

                st.markdown("**Metrics Table**")
                st.dataframe(wf_subset.sort_values(by="total_return", ascending=False), width="stretch")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Total Return (by strategy)**")
                    st.bar_chart(metrics.set_index("strategy")["total_return"])
                with col2:
                    st.markdown("**Fees Paid % (by strategy)**")
                    st.bar_chart(metrics.set_index("strategy")["fees_paid_pct"])

                st.markdown("**Metrics Table**")
                st.dataframe(metrics.sort_values(by="total_return", ascending=False), width="stretch")

    elif tab == "Run Detail":
        if not st.session_state.load_runs:
            st.session_state.load_runs = True
            st.rerun()
        if not runs:
            st.info("No run artifacts found. Run backtests first.")
            return
        symbols = sorted({str(r["symbol"]) for r in runs})
        symbol = st.selectbox("Symbol", symbols, key="detail_symbol")
        symbol_runs = [r for r in runs if r["symbol"] == symbol]
        run_ids = [
            r["path"].name
            for r in sorted(
                symbol_runs,
                key=lambda r: str(r["config"].get("created_at_utc") or ""),
                reverse=True,
            )
        ]
        run_id = st.selectbox("Run", run_ids, key="detail_run")
        run = next(r for r in symbol_runs if r["path"].name == run_id)
        _render_header(f"{symbol} — {run_id}", str(run["path"]))
        if "load_run_detail" not in st.session_state:
            st.session_state.load_run_detail = False
        if not st.session_state.load_run_detail:
            if st.button("Load Run Detail", key="load_run_detail_button"):
                st.session_state.load_run_detail = True
            else:
                st.info("Click to load equity curves and run config.")
                return
        progress_slot = st.empty()
        progress = progress_slot.progress(0.0, text="Loading run detail (0%)")
        with st.spinner("Loading run detail..."):
            st.markdown("**Equity + Drawdown (per strategy)**")
            equity_files = sorted(run["path"].glob("equity_*.csv"))
            progress.progress(0.1, text="Scanning equity files (10%)")
            if equity_files:
                strategy_names = [p.stem.replace("equity_", "") for p in equity_files]
                strat = st.selectbox("Equity strategy", strategy_names)
                eq_path = run["path"] / f"equity_{strat}.csv"
                eq_df = _read_csv(str(eq_path), eq_path.stat().st_mtime)
                progress.progress(0.5, text="Loading equity data (50%)")
                eq_df["time_utc"] = pd.to_datetime(eq_df["time_utc"], utc=True)
                eq_df = eq_df.sort_values("time_utc")
                eq_df["equity"] = pd.to_numeric(eq_df["equity"], errors="coerce")
                eq_df["drawdown"] = (eq_df["equity"] / eq_df["equity"].cummax()) - 1.0
                col1, col2 = st.columns(2)
                with col1:
                    st.line_chart(eq_df.set_index("time_utc")["equity"])
                with col2:
                    st.line_chart(eq_df.set_index("time_utc")["drawdown"])
                progress.progress(0.8, text="Rendering charts (80%)")
            else:
                st.info("No equity curves found. Re-run backtests to generate equity_*.csv files.")

            with st.expander("Run config"):
                progress.progress(0.9, text="Loading run config (90%)")
                st.json(run["config"])
        progress.progress(1.0, text="Run detail loaded (100%)")
        progress_slot.empty()

    elif tab == "Compare":
        if not st.session_state.load_runs:
            st.session_state.load_runs = True
            st.rerun()
        if not runs:
            st.info("No run artifacts found. Run backtests first.")
            return
        symbols = sorted({str(r["symbol"]) for r in runs})
        symbol = st.selectbox("Symbol", symbols, key="compare_symbol")
        with st.spinner("Loading compare view..."):
            st.markdown("**Compare Runs**")
            registry = _load_registry(runs_root)
            run_map = {str(r["path"].name): r for r in all_runs}
            compare_symbol = symbol
            if registry is not None and "symbol" in registry.columns and "run_id" in registry.columns:
                registry["symbol"] = registry["symbol"].astype(str)
                symbol_rows = registry[registry["symbol"] == compare_symbol].copy()
                run_ids = symbol_rows["run_id"].astype(str).tolist()
            else:
                run_ids = [str(r["path"].name) for r in all_runs if str(r["symbol"]) == compare_symbol]

            if len(run_ids) < 2:
                st.info("Need at least two runs for this symbol to compare.")
            else:
                default_a = run_ids[0]
                default_b = run_ids[1] if len(run_ids) > 1 else run_ids[0]
                col_a, col_b = st.columns(2)
                with col_a:
                    run_a = st.selectbox("Run A", run_ids, index=run_ids.index(default_a), key="compare_run_a")
                with col_b:
                    run_b = st.selectbox("Run B", run_ids, index=run_ids.index(default_b), key="compare_run_b")

                run_a_obj = run_map.get(run_a)
                run_b_obj = run_map.get(run_b)
                if run_a_obj and run_b_obj:
                    _render_header(
                        f"Compare — {compare_symbol}",
                        f"{run_a_obj['path'].name} vs {run_b_obj['path'].name}",
                    )
                    df_a = run_a_obj["metrics"].copy()
                    df_b = run_b_obj["metrics"].copy()
                    for col in ["total_return", "cagr", "max_drawdown", "sharpe", "fees_paid_pct"]:
                        df_a[col] = pd.to_numeric(df_a[col], errors="coerce")
                        df_b[col] = pd.to_numeric(df_b[col], errors="coerce")
                    merged = df_a.merge(df_b, on="strategy", suffixes=("_a", "_b"))
                    merged["delta_total_return"] = merged["total_return_b"] - merged["total_return_a"]
                    merged["delta_sharpe"] = merged["sharpe_b"] - merged["sharpe_a"]
                    st.markdown("**Run A vs Run B (metrics diff)**")
                    st.dataframe(
                        merged[
                            [
                                "strategy",
                                "total_return_a",
                                "total_return_b",
                                "delta_total_return",
                                "sharpe_a",
                                "sharpe_b",
                                "delta_sharpe",
                                "fees_paid_pct_a",
                                "fees_paid_pct_b",
                            ]
                        ].sort_values(by="delta_total_return", ascending=False),
                        width="stretch",
                    )

                    eq_files_a = sorted(run_a_obj["path"].glob("equity_*.csv"))
                    eq_files_b = sorted(run_b_obj["path"].glob("equity_*.csv"))
                    strategies = sorted(
                        {p.stem.replace("equity_", "") for p in eq_files_a}
                        & {p.stem.replace("equity_", "") for p in eq_files_b}
                    )
                    if strategies:
                        strat = st.selectbox("Compare equity for strategy", strategies, key="compare_equity")
                        eq_a_path = run_a_obj["path"] / f"equity_{strat}.csv"
                        eq_b_path = run_b_obj["path"] / f"equity_{strat}.csv"
                        eq_a = _read_csv(str(eq_a_path), eq_a_path.stat().st_mtime)
                        eq_b = _read_csv(str(eq_b_path), eq_b_path.stat().st_mtime)
                        eq_a["time_utc"] = pd.to_datetime(eq_a["time_utc"], utc=True)
                        eq_b["time_utc"] = pd.to_datetime(eq_b["time_utc"], utc=True)
                        eq_a = eq_a.sort_values("time_utc").set_index("time_utc")
                        eq_b = eq_b.sort_values("time_utc").set_index("time_utc")
                        eq_a["equity"] = pd.to_numeric(eq_a["equity"], errors="coerce")
                        eq_b["equity"] = pd.to_numeric(eq_b["equity"], errors="coerce")
                        st.line_chart(pd.DataFrame({"run_a": eq_a["equity"], "run_b": eq_b["equity"]}))
                    else:
                        st.info("No matching equity curves found for comparison.")

    elif tab == "Sweeps":
        symbol = None
        if runs:
            symbol = st.session_state.get("overview_symbol") or str(sorted({r["symbol"] for r in runs})[0])
        _render_header("Parameter Sweeps", f"{symbol}" if symbol else "")
        with st.spinner("Loading sweeps..."):
            st.markdown("**Parameter Sweep (SMA crossover)**")
            sweep_df = _load_latest_sweep(runs_root / "sweeps", symbol=symbol)
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

    elif tab == "Fills":
        if not st.session_state.load_runs:
            st.session_state.load_runs = True
            st.rerun()
        if not runs:
            st.info("No run artifacts found. Run backtests first.")
            return
        symbols = sorted({str(r["symbol"]) for r in runs})
        symbol = st.selectbox("Symbol", symbols, key="fills_symbol")
        symbol_runs = [r for r in runs if r["symbol"] == symbol]
        run_ids = [
            r["path"].name
            for r in sorted(
                symbol_runs,
                key=lambda r: str(r["config"].get("created_at_utc") or ""),
                reverse=True,
            )
        ]
        run_id = st.selectbox("Run", run_ids, key="fills_run")
        run = next(r for r in symbol_runs if r["path"].name == run_id)
        _render_header(f"{symbol} — {run_id}", str(run["path"]))
        with st.spinner("Loading fills..."):
            fills_path = run["fills_path"]
            if fills_path:
                fills_path = Path(fills_path)
                fills = _read_csv(str(fills_path), fills_path.stat().st_mtime)
                st.markdown("**Fills (paper/backtest)**")
                st.dataframe(fills.tail(200), width="stretch")
            else:
                st.info("No fills file found for this run.")

    elif tab == "Multi-Symbol":
        _render_header("Multi-symbol comparison", "Latest runs across symbols.")
        if not st.session_state.load_runs:
            st.session_state.load_runs = True
            st.rerun()
        if not runs:
            st.info("No run artifacts found. Run backtests first.")
            return
        with st.spinner("Loading multi-symbol view..."):
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
