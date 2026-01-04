from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

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


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            return [c.strip() for c in row]
    return []


def _ensure_journal_fills_file(path: Path, template_path: Path) -> list[str]:
    if path.exists():
        return _read_csv_header(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(template_path.read_bytes())
    return _read_csv_header(path)


def _load_existing_fill_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return set()
        return {str(row.get("fill_id") or "").strip() for row in reader if str(row.get("fill_id") or "").strip()}


def _hash_fill_id(*, run_id: str, symbol: str, strategy: str, time_utc: str, side: str, price: str, qty_base: str) -> str:
    payload = "|".join([run_id, symbol, strategy, time_utc, side, price, qty_base])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _append_run_fills_to_journal(run_dir: Path) -> int:
    config_path = run_dir / "config.json"
    fills_path = run_dir / "fills.csv"
    if not config_path.exists() or not fills_path.exists():
        return 0
    cfg = _read_json(str(config_path), config_path.stat().st_mtime)
    symbol = str(cfg.get("symbol") or "").strip()
    run_id = str(cfg.get("run_id") or run_dir.name)
    if not symbol:
        return 0

    journal_fills = Path("journal/local/fills.csv")
    template_fills = Path("journal/templates/fills.csv")
    fieldnames = _ensure_journal_fills_file(journal_fills, template_fills)
    if not fieldnames:
        return 0

    existing_ids = _load_existing_fill_ids(journal_fills)
    appended = 0
    with fills_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return 0
        rows: list[dict[str, str]] = []
        for row in reader:
            time_utc = str(row.get("time_utc") or "").strip()
            strategy = str(row.get("strategy") or "").strip()
            side = str(row.get("side") or "").strip().lower()
            price = str(row.get("price") or "").strip()
            qty_base = str(row.get("qty_base") or "").strip()
            qty_quote = str(row.get("qty_quote") or "").strip()
            fee_quote = str(row.get("fee_quote") or "").strip()
            if not time_utc or not side:
                continue
            fill_id = _hash_fill_id(
                run_id=run_id,
                symbol=symbol,
                strategy=strategy,
                time_utc=time_utc,
                side=side,
                price=price,
                qty_base=qty_base,
            )
            if fill_id in existing_ids:
                continue
            order_id = f"{run_id}:{strategy}:{time_utc}"
            rows.append(
                {
                    "trade_id": "",
                    "fill_id": fill_id,
                    "time_utc": time_utc,
                    "venue": "paper",
                    "account": "",
                    "symbol": symbol,
                    "strategy": strategy,
                    "side": side,
                    "order_type": "sim",
                    "liquidity": "sim",
                    "price": price,
                    "qty_base": qty_base,
                    "qty_quote": qty_quote,
                    "fee_quote": fee_quote,
                    "fee_rate_bps": "",
                    "order_id": order_id,
                    "trade_type": "backtest",
                    "sequence_timestamp": "",
                    "size_in_quote": "",
                    "retail_portfolio_id": "",
                    "notes": "",
                }
            )
            existing_ids.add(fill_id)
            appended += 1

    if not rows:
        return 0
    with journal_fills.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return appended


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


_GRANULARITY_LABELS = {
    60: "1m",
    300: "5m",
    900: "15m",
    3600: "1h",
    21600: "6h",
    86400: "1d",
}


def _format_granularity(seconds: int) -> str:
    label = _GRANULARITY_LABELS.get(seconds)
    return f"{label} ({seconds}s)" if label else f"{seconds}s"


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
    heat_df = df[[x, y, value]].copy()
    heat_df[x] = pd.to_numeric(heat_df[x], errors="coerce")
    heat_df[y] = pd.to_numeric(heat_df[y], errors="coerce")
    heat_df[value] = pd.to_numeric(heat_df[value], errors="coerce")
    heat_df = heat_df.dropna()
    st.markdown(f"**{title}**")
    st.vega_lite_chart(
        heat_df,
        {
            "mark": {"type": "rect", "cornerRadius": 2},
            "encoding": {
                "x": {"field": x, "type": "ordinal", "sort": "ascending", "axis": {"labelAngle": 0}},
                "y": {"field": y, "type": "ordinal", "sort": "ascending"},
                "color": {
                    "field": value,
                    "type": "quantitative",
                    "scale": {"scheme": "viridis"},
                    "legend": {"title": value},
                },
                "tooltip": [
                    {"field": x, "type": "ordinal", "title": x},
                    {"field": y, "type": "ordinal", "title": y},
                    {"field": value, "type": "quantitative", "title": value},
                ],
            },
            "width": "container",
            "height": 420,
            "autosize": {"type": "fit", "contains": "padding"},
        },
        use_container_width=True,
    )
    top_cols = [x, y, value, "cagr", "max_drawdown", "sharpe", "trades", "fees_paid_pct"]
    top_source = df.copy()
    for col in top_cols:
        if col in top_source.columns:
            top_source[col] = pd.to_numeric(top_source[col], errors="coerce")
    top = top_source.sort_values(by=value, ascending=False).head(5).reset_index(drop=True)
    top.index = top.index + 1
    st.markdown("**Top 5 combinations**")
    display_cols = [c for c in top_cols if c in top.columns]
    st.dataframe(top[display_cols], width="stretch")


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

        .section-title {
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 1.2px;
          color: var(--muted);
          margin-bottom: 6px;
        }

        .section-subtitle {
          font-size: 13px;
          color: var(--text);
          margin-bottom: 10px;
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
        [
            "Data",
            "Run",
            "Overview",
            "Run Detail",
            "Compare",
            "Sweeps",
            "Fills",
            "Journal",
            "Multi-Symbol",
        ],
        horizontal=True,
        label_visibility="collapsed",
        key="active_tab",
    )

    if tab == "Data":
        _render_header("Research", "Data fetch and dataset index.")
        st.markdown(
            "<div class='section-title'>Data Fetch</div><div class='section-subtitle'>Coinbase Exchange → build a local dataset index.</div>",
            unsafe_allow_html=True,
        )
        products_cache = _load_products_cache()
        index_path = Path("data/processed/index.csv")
        with st.form("fetch_history"):
            col_left, col_right = st.columns([2, 1], gap="large")
            with col_left:
                st.markdown("<div class='section-title'>Universe</div>", unsafe_allow_html=True)
                quote_currency = st.text_input("Quote currency filter", value="USD")
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
                st.markdown("<div class='section-title'>Timeframe</div>", unsafe_allow_html=True)
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
                    label_options = {_format_granularity(int(g)): int(g) for g in options}
                    selected_labels = st.multiselect(
                        "Granularities (Coinbase labels)",
                        options=list(label_options.keys()),
                        default=[],
                    )
                    selected_granularities = [label_options[label] for label in selected_labels]
                    granularities_input = ",".join(str(g) for g in selected_granularities)
                else:
                    label_options = {_format_granularity(int(g)): int(g) for g in standard_granularities}
                    selected_labels = st.multiselect(
                        "Granularities (Coinbase labels)",
                        options=list(label_options.keys()),
                        default=[],
                    )
                    selected_granularities = [label_options[label] for label in selected_labels]
                    granularities_input = ",".join(str(g) for g in selected_granularities)
                years = st.number_input("Years back", min_value=1, max_value=20, value=5, step=1)
                start = st.text_input("Start (ISO8601, optional)", value="")
                end = st.text_input("End (ISO8601, optional)", value="")

            with col_right:
                st.markdown("<div class='section-title'>Execution</div>", unsafe_allow_html=True)
                limit_per_request = st.number_input("Limit per request", min_value=50, max_value=300, value=300, step=10)
                attempt_gap_fill = st.checkbox("Attempt gap fill", value=True)
                base_url = st.text_input("Base URL", value="https://api.exchange.coinbase.com")
                st.markdown("<div class='section-title'>Storage</div>", unsafe_allow_html=True)
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

            st.markdown("<div class='section-title'>Fetch Output</div>", unsafe_allow_html=True)
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
                    log_html = (
                        "<div id='fetch-log' style='height:300px; overflow:auto; "
                        "background:#0f141e; border:1px solid rgba(255,255,255,0.06); "
                        "border-radius:10px; padding:10px; font-family:monospace; "
                        "font-size:12px; white-space:pre-wrap;'>"
                        + "\n".join(log_lines[-400:])
                        + "</div><script>"
                        "const el=document.getElementById('fetch-log');"
                        "if(el){el.scrollTop=el.scrollHeight;}"
                        "</script>"
                    )
                    output_placeholder.empty()
                    output_placeholder.markdown(
                        "<div class='panel-sub'>Fetching log (auto-scroll)</div>",
                        unsafe_allow_html=True,
                    )
                    components.html(log_html, height=330)
                returncode = process.wait()

            if returncode == 0:
                st.success("Fetch completed.")
            else:
                st.error(f"Fetch failed with exit code {returncode}.")

        st.markdown("<div class='section-title'>Dataset Index</div>", unsafe_allow_html=True)
        index_path = Path("data/processed/index.csv")
        if index_path.exists():
            index_df = _read_csv(str(index_path), index_path.stat().st_mtime)
            st.dataframe(index_df.tail(200), width="stretch")
            st.caption("Validation is enforced during fetch; gaps/missing candles are reported in the live log.")
        else:
            st.info("No index.csv found yet. Run a fetch to generate it.")

    elif tab == "Run":
        _render_header("Research", "Create backtest runs from datasets.")
        mode = st.radio("Backtest type", options=["Backtest", "Walk-forward"], horizontal=True)
        submitted = False
        if mode == "Backtest":
            st.markdown("**Create Run (Backtest Runner)**")
            st.caption("Runs `research/backtests/run_all_strategies.py` from the dashboard.")
        else:
            st.markdown("**Walk-Forward Runner**")
            st.caption("Runs `research/backtests/walk_forward.py` and stores results under `research/output/walk_forward`.")

        if mode == "Backtest":
            with st.form("run_backtest"):
                backtest_runner = st.selectbox(
                    "Backtest runner",
                    options=[
                        "Multi-strategy (run_all_strategies.py)",
                        "Single strategy (coinbase_backtest.py)",
                    ],
                    index=0,
                )
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
                    labels = [_format_granularity(int(g)) for g in available_granularities]
                    label_to_value = dict(zip(labels, available_granularities))
                    selected_label = st.selectbox(
                        "Granularity (Coinbase labels)",
                        options=labels,
                        index=0,
                    )
                    selected_granularity = label_to_value[selected_label]
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
                st.markdown("**Time window**")
                window_type = st.radio(
                    "Time window type",
                    options=["Years", "Months", "Weeks", "Days", "Hours", "Custom (start/end)"],
                    horizontal=True,
                    label_visibility="collapsed",
                )
                window_value = None
                start = ""
                end = ""
                if window_type == "Years":
                    window_value = st.number_input("Years back", min_value=1, max_value=20, value=5, step=1, key="win_years")
                elif window_type == "Months":
                    window_value = st.number_input("Months back", min_value=1, max_value=60, value=6, step=1, key="win_months")
                elif window_type == "Weeks":
                    window_value = st.number_input("Weeks back", min_value=1, max_value=260, value=12, step=1, key="win_weeks")
                elif window_type == "Days":
                    window_value = st.number_input("Days back", min_value=1, max_value=3650, value=90, step=1, key="win_days")
                elif window_type == "Hours":
                    window_value = st.number_input("Hours back", min_value=1, max_value=8760, value=24, step=1, key="win_hours")
                else:
                    start = st.text_input("Start (ISO8601, required)", value="", key="win_start")
                    end = st.text_input("End (ISO8601, optional)", value="", key="win_end")
                if backtest_runner.startswith("Multi-strategy"):
                    config_path = st.text_input("Config path", value="research/configs/backtest_base.json")
                    output_dir = st.text_input("Output directory", value="research/output")
                    save_artifacts = True
                    append_journal = True
                    extra_args = st.text_input("Extra CLI args (optional)", value="")
                else:
                    strategy_name = st.text_input("Strategy name", value="sma_crossover")
                    execution = st.selectbox("Execution", options=["maker", "taker"], index=1)
                    slippage_bps = st.number_input("Slippage (bps)", min_value=0.0, max_value=50.0, value=5.0, step=0.5)
                    gap_cooldown = st.number_input("Gap cooldown (bars)", min_value=0, max_value=10, value=1, step=1)
                    gap_policy = st.selectbox("Gap policy", options=["segment", "skip", "forward_fill"], index=0)
                    maker_fee_bps = st.number_input("Maker fee (bps)", min_value=0.0, max_value=20.0, value=2.5, step=0.1)
                    taker_fee_bps = st.number_input("Taker fee (bps)", min_value=0.0, max_value=20.0, value=6.5, step=0.1)
                    fast_sma = st.number_input("Fast SMA", min_value=2, max_value=200, value=20, step=1)
                    slow_sma = st.number_input("Slow SMA", min_value=5, max_value=300, value=100, step=1)
                    initial_cash = st.number_input("Initial cash", min_value=0.1, max_value=100.0, value=1.0, step=0.1)
                    extra_args = st.text_input("Extra CLI args (optional)", value="")
                submitted = st.form_submit_button("Run backtest")

        if mode == "Backtest" and submitted:
            def _fmt_dt(dt: datetime) -> str:
                return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

            def _compute_window() -> tuple[str, str] | None:
                if window_type == "Custom (start/end)":
                    if not start.strip():
                        st.error("Start is required for a custom range.")
                        return None
                    end_str = end.strip() or _fmt_dt(datetime.now(tz=UTC))
                    return start.strip(), end_str
                end_dt = datetime.now(tz=UTC)
                if window_type == "Months":
                    start_dt = (pd.Timestamp(end_dt) - pd.DateOffset(months=int(window_value))).to_pydatetime()
                elif window_type == "Weeks":
                    start_dt = end_dt - timedelta(weeks=int(window_value))
                elif window_type == "Days":
                    start_dt = end_dt - timedelta(days=int(window_value))
                elif window_type == "Hours":
                    start_dt = end_dt - timedelta(hours=int(window_value))
                else:
                    start_dt = (pd.Timestamp(end_dt) - pd.DateOffset(years=int(window_value))).to_pydatetime()
                return _fmt_dt(start_dt), _fmt_dt(end_dt)

            run_started_at = datetime.now(tz=UTC)
            if backtest_runner.startswith("Multi-strategy"):
                args = [sys.executable, "research/backtests/run_all_strategies.py"]
            else:
                args = [sys.executable, "research/backtests/coinbase_backtest.py"]
            symbols = [s.strip() for s in symbols_input.split(",") if s.strip()]
            if backtest_runner.startswith("Multi-strategy"):
                if len(symbols) == 1:
                    args += ["--symbol", symbols[0]]
                elif symbols:
                    args += ["--symbols", ",".join(symbols)]
                if config_path:
                    args += ["--config", config_path]
                if output_dir:
                    args += ["--output-dir", output_dir]
                if selected_granularity is not None:
                    args += ["--granularity-seconds", str(int(selected_granularity))]
                window = _compute_window()
                if window is None:
                    return
                args += ["--start", window[0], "--end", window[1]]
                args.append("--save-artifacts" if save_artifacts else "--no-save-artifacts")
                if extra_args.strip():
                    args += extra_args.split()
            else:
                if symbols:
                    if len(symbols) > 1:
                        st.warning("Single strategy runner supports one symbol; using the first selection.")
                    args += ["--symbol", symbols[0]]
                if selected_granularity is not None:
                    args += ["--granularity-seconds", str(int(selected_granularity))]
                args += ["--strategy", strategy_name]
                args += ["--execution", execution]
                args += ["--slippage-bps", str(float(slippage_bps))]
                args += ["--gap-cooldown-bars", str(int(gap_cooldown))]
                args += ["--gap-policy", str(gap_policy)]
                args += ["--maker-fee-bps", str(float(maker_fee_bps))]
                args += ["--taker-fee-bps", str(float(taker_fee_bps))]
                args += ["--fast-sma", str(int(fast_sma))]
                args += ["--slow-sma", str(int(slow_sma))]
                args += ["--initial-cash", str(float(initial_cash))]
                window = _compute_window()
                if window is None:
                    return
                args += ["--start", window[0], "--end", window[1]]
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
                if backtest_runner.startswith("Multi-strategy") and save_artifacts and append_journal:
                    output_root = Path(output_dir)
                    registry = _load_registry(output_root)
                    appended_total = 0
                    appended_runs: list[str] = []
                    if registry is not None and not registry.empty and "created_at_utc" in registry.columns:
                        registry = registry.copy()
                        registry["created_at_utc"] = pd.to_datetime(registry["created_at_utc"], utc=True, errors="coerce")
                        symbol_set = set(symbols) if symbols else set()
                        cutoff = pd.Timestamp(run_started_at) - pd.Timedelta(minutes=2)
                        recent = registry[registry["created_at_utc"] >= cutoff]
                        if symbol_set and "symbol" in recent.columns:
                            recent = recent[recent["symbol"].isin(symbol_set)]
                        for run_id in recent["run_id"].astype(str).tolist():
                            run_dir = output_root / "runs" / run_id
                            appended_total += _append_run_fills_to_journal(run_dir)
                            appended_runs.append(run_id)
                    if appended_total:
                        st.success(f"Appended {appended_total} fill(s) to journal/local/fills.csv.")
                        with st.spinner("Deriving trades from journal fills..."):
                            derive = subprocess.run(
                                [sys.executable, "journal/tools/derive_trades_from_fills.py"],
                                capture_output=True,
                                text=True,
                            )
                        if derive.returncode == 0:
                            st.success("Derived trades: journal/local/trades.csv updated.")
                            if appended_runs:
                                with st.spinner("Generating daily report..."):
                                    report = subprocess.run(
                                        [
                                            sys.executable,
                                            "reporting/generate_daily_report.py",
                                            "--runs-root",
                                            str(output_root),
                                            *[f"--run-id={rid}" for rid in appended_runs],
                                        ],
                                        capture_output=True,
                                        text=True,
                                    )
                                if report.returncode == 0:
                                    st.success("Daily report updated.")
                                else:
                                    st.error("Daily report generation failed. See logs below.")
                                    st.code((report.stdout or "") + (report.stderr or ""))
                        else:
                            st.error("Trade derivation failed. See logs below.")
                            st.code((derive.stdout or "") + (derive.stderr or ""))
                    else:
                        st.info("No new fills appended to journal.")
            else:
                st.error(f"Backtest failed with exit code {returncode}.")
        elif mode == "Walk-forward":
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
                        if "compare_eq_loaded" not in st.session_state:
                            st.session_state.compare_eq_loaded = False
                        strat = st.selectbox("Compare equity for strategy", strategies, key="compare_equity")
                        load_eq = st.button("Load equity + drawdown", key="compare_eq_button")
                        if load_eq:
                            st.session_state.compare_eq_loaded = True
                        if st.session_state.compare_eq_loaded:
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
        index_path = Path("data/processed/index.csv")
        symbols = _load_dataset_symbols(index_path)
        symbol = symbols[0] if symbols else None
        if st.session_state.get("overview_symbol") in symbols:
            symbol = st.session_state.get("overview_symbol")
        _render_header("Parameter Sweeps", f"{symbol}" if symbol else "")
        st.markdown("**Parameter Sweep (SMA crossover)**")
        if not symbols:
            st.info("No datasets indexed yet. Run a data fetch first.")
        else:
            symbol = st.selectbox("Symbol", symbols, index=symbols.index(symbol), key="sweep_symbol")
            granularity_seconds = None
            sweep_dataset_info = None
            if index_path.exists():
                index_df = _read_csv(str(index_path), index_path.stat().st_mtime)
                symbol_rows = index_df[index_df["product_id"] == symbol]
                if not symbol_rows.empty and "granularity_seconds" in symbol_rows.columns:
                    available = sorted({int(g) for g in symbol_rows["granularity_seconds"].dropna().tolist()})
                    if available:
                        label_map = {_format_granularity(int(g)): int(g) for g in available}
                        selected_label = st.selectbox(
                            "Granularity",
                            options=list(label_map.keys()),
                            key="sweep_granularity",
                        )
                        granularity_seconds = label_map[selected_label]
                        latest_rows = symbol_rows[symbol_rows["granularity_seconds"].astype(int) == granularity_seconds]
                        if not latest_rows.empty:
                            latest_row = latest_rows.sort_values("dataset_end").iloc[-1]
                            sweep_dataset_info = {
                                "path": str(latest_row.get("processed_path", "")),
                                "start": str(latest_row.get("dataset_start", "")),
                                "end": str(latest_row.get("dataset_end", "")),
                            }
            if sweep_dataset_info:
                st.caption(
                    f"Dataset: `{sweep_dataset_info['path']}` "
                    f"({sweep_dataset_info['start']} → {sweep_dataset_info['end']})"
                )

            st.markdown("**Sweep window**")
            sweep_window_type = st.radio(
                "Sweep window type",
                options=["Full dataset", "Years", "Months", "Weeks", "Days", "Hours", "Custom (start/end)"],
                horizontal=True,
                label_visibility="collapsed",
                key="sweep_window_type",
            )
            sweep_window_value = None
            sweep_start = ""
            sweep_end = ""
            if sweep_window_type == "Years":
                sweep_window_value = st.number_input("Years back", min_value=1, max_value=20, value=5, step=1, key="sweep_years")
            elif sweep_window_type == "Months":
                sweep_window_value = st.number_input("Months back", min_value=1, max_value=60, value=6, step=1, key="sweep_months")
            elif sweep_window_type == "Weeks":
                sweep_window_value = st.number_input("Weeks back", min_value=1, max_value=260, value=12, step=1, key="sweep_weeks")
            elif sweep_window_type == "Days":
                sweep_window_value = st.number_input("Days back", min_value=1, max_value=3650, value=90, step=1, key="sweep_days")
            elif sweep_window_type == "Hours":
                sweep_window_value = st.number_input("Hours back", min_value=1, max_value=8760, value=24, step=1, key="sweep_hours")
            elif sweep_window_type == "Custom (start/end)":
                sweep_start = st.text_input("Start (ISO8601, required)", value="", key="sweep_start")
                sweep_end = st.text_input("End (ISO8601, optional)", value="", key="sweep_end")

            run_sweep = st.button("Run sweep", type="primary")
            if run_sweep:
                args = [sys.executable, "research/backtests/param_sweep.py", "--symbol", symbol]
                if granularity_seconds:
                    args += ["--granularity-seconds", str(int(granularity_seconds))]
                if sweep_window_type != "Full dataset":
                    if sweep_window_type == "Custom (start/end)":
                        if not sweep_start.strip():
                            st.error("Start is required for a custom range.")
                            return
                        args += ["--start", sweep_start.strip()]
                        if sweep_end.strip():
                            args += ["--end", sweep_end.strip()]
                    else:
                        end_dt = datetime.now(tz=UTC)
                        if sweep_window_type == "Months":
                            start_dt = (pd.Timestamp(end_dt) - pd.DateOffset(months=int(sweep_window_value))).to_pydatetime()
                        elif sweep_window_type == "Weeks":
                            start_dt = end_dt - timedelta(weeks=int(sweep_window_value))
                        elif sweep_window_type == "Days":
                            start_dt = end_dt - timedelta(days=int(sweep_window_value))
                        elif sweep_window_type == "Hours":
                            start_dt = end_dt - timedelta(hours=int(sweep_window_value))
                        else:
                            start_dt = (pd.Timestamp(end_dt) - pd.DateOffset(years=int(sweep_window_value))).to_pydatetime()
                        args += [
                            "--start",
                            start_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "--end",
                            end_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        ]
                log_lines: list[str] = []
                output_placeholder = st.empty()
                with st.spinner("Running sweep..."):
                    process = subprocess.Popen(
                        args,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )
                    assert process.stdout is not None
                    for line in process.stdout:
                        cleaned = line.rstrip()
                        if cleaned.startswith("Wrote sweep:"):
                            continue
                        if cleaned:
                            log_lines.append(cleaned)
                            output_placeholder.text_area(
                                "Sweep log",
                                value="\n".join(log_lines[-200:]),
                                height=200,
                                disabled=True,
                                label_visibility="collapsed",
                            )
                    returncode = process.wait()
                if returncode == 0:
                    st.success("Sweep completed.")
                else:
                    st.error(f"Sweep failed with exit code {returncode}.")

            sweep_df = _load_latest_sweep(runs_root / "sweeps", symbol=symbol)
            if sweep_df is None:
                st.info("No SMA sweep found yet for this symbol.")
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

    elif tab == "Journal":
        _render_header("Journal", "Paper/sim fills → trades → daily reports.")
        cfg_path = Path("journal/config.json")
        cfg = _read_json(str(cfg_path), cfg_path.stat().st_mtime) if cfg_path.exists() else {}
        paths = cfg.get("paths", {}) if isinstance(cfg, dict) else {}
        fills_path = Path(paths.get("fills", "journal/local/fills.csv"))
        trades_path = Path(paths.get("trades", "journal/local/trades.csv"))
        report_log_path = Path(paths.get("report_daily_log", "reporting/daily_report_log.csv"))
        report_md_path = Path(paths.get("report_daily_md", "reporting/daily_report.md"))

        st.caption(
            "Journal view is filtered by run + strategy and derived from fills/trades. "
            "Daily report rows are auto-built per run; use the summary below for per-strategy totals."
        )

        run_ids: list[str] = []
        fills_df = pd.DataFrame()
        if fills_path.exists():
            fills_df = _read_csv(str(fills_path), fills_path.stat().st_mtime)
            if "order_id" in fills_df.columns:
                fills_df["run_id"] = fills_df["order_id"].astype(str).str.split(":", n=1).str[0]
                run_ids = sorted({rid for rid in fills_df["run_id"].dropna().astype(str) if rid})
            if "strategy" not in fills_df.columns:
                fills_df["strategy"] = ""
            if "notes" in fills_df.columns:
                needs_backfill = fills_df["strategy"].astype(str).str.strip() == ""
                fills_df.loc[needs_backfill, "strategy"] = (
                    fills_df.loc[needs_backfill, "notes"].astype(str).str.replace("strategy=", "", regex=False)
                )

        selected_run = st.selectbox("Run filter", options=["All runs"] + run_ids)
        if selected_run != "All runs" and not fills_df.empty:
            fills_df = fills_df[fills_df.get("run_id", "") == selected_run]

        strategy_options: list[str] = []
        if not fills_df.empty:
            strategy_options = sorted({s for s in fills_df["strategy"].dropna().astype(str) if s})
        selected_strategy = st.selectbox("Strategy filter", options=["All strategies"] + strategy_options)
        if selected_strategy != "All strategies" and not fills_df.empty:
            fills_df = fills_df[fills_df.get("strategy", "") == selected_strategy]

        with st.expander("Fills", expanded=True):
            if fills_path.exists():
                st.caption(f"{len(fills_df)} fill(s) in {fills_path}")
                st.dataframe(fills_df.tail(200), width="stretch")
            else:
                st.info(f"No fills found at {fills_path}.")

        with st.expander("Trades", expanded=True):
            if trades_path.exists():
                trades_df = _read_csv(str(trades_path), trades_path.stat().st_mtime)
                if selected_run != "All runs" and not fills_df.empty and "trade_id" in fills_df.columns:
                    trade_ids = set(fills_df["trade_id"].dropna().astype(str).tolist())
                    if trade_ids:
                        trades_df = trades_df[trades_df["trade_id"].astype(str).isin(trade_ids)]
                if not trades_df.empty:
                    realized = pd.to_numeric(trades_df.get("realized_pnl_quote", 0), errors="coerce")
                    entry_qty = pd.to_numeric(trades_df.get("entry_qty_quote", 0), errors="coerce")
                    exit_qty = pd.to_numeric(trades_df.get("exit_qty_quote", 0), errors="coerce")
                    entry_fee = pd.to_numeric(trades_df.get("entry_fee_quote", 0), errors="coerce")
                    exit_fee = pd.to_numeric(trades_df.get("exit_fee_quote", 0), errors="coerce")
                    fallback = exit_qty - entry_qty - entry_fee - exit_fee
                    trades_df["realized_pnl_quote_calc"] = realized.fillna(0.0)
                    missing_mask = realized.isna() | (realized == 0)
                    trades_df.loc[missing_mask, "realized_pnl_quote_calc"] = fallback.loc[missing_mask]
                st.caption(f"{len(trades_df)} trade(s) in {trades_path}")
                st.dataframe(trades_df.tail(200), width="stretch")
            else:
                st.info(f"No trades found at {trades_path}.")

        with st.expander("Daily Report", expanded=True):
            if not fills_df.empty:
                fees_paid = pd.to_numeric(fills_df.get("fee_quote", 0), errors="coerce").fillna(0.0).sum()
                trades_count = 0
                if "trade_id" in fills_df.columns:
                    trades_count = len({t for t in fills_df["trade_id"].dropna().astype(str) if t})
                realized_pnl = ""
                if trades_path.exists():
                    trades_df = _read_csv(str(trades_path), trades_path.stat().st_mtime)
                    if "trade_id" in fills_df.columns:
                        trade_ids = set(fills_df["trade_id"].dropna().astype(str).tolist())
                        if trade_ids:
                            trades_df = trades_df[trades_df["trade_id"].astype(str).isin(trade_ids)]
                    realized = pd.to_numeric(trades_df.get("realized_pnl_quote", 0), errors="coerce")
                    entry_qty = pd.to_numeric(trades_df.get("entry_qty_quote", 0), errors="coerce")
                    exit_qty = pd.to_numeric(trades_df.get("exit_qty_quote", 0), errors="coerce")
                    entry_fee = pd.to_numeric(trades_df.get("entry_fee_quote", 0), errors="coerce")
                    exit_fee = pd.to_numeric(trades_df.get("exit_fee_quote", 0), errors="coerce")
                    fallback = exit_qty - entry_qty - entry_fee - exit_fee
                    realized_pnl = realized.fillna(fallback).fillna(0.0).sum()
                total_pnl = ""
                unrealized_pnl = ""
                if selected_run != "All runs" and selected_strategy != "All strategies":
                    run_dir = Path("research/output") / "runs" / selected_run
                    cfg_path = run_dir / "config.json"
                    equity_path = run_dir / f"equity_{selected_strategy}.csv"
                    if equity_path.exists() and cfg_path.exists():
                        cfg = _read_json(str(cfg_path), cfg_path.stat().st_mtime)
                        initial_cash = cfg.get("initial_cash")
                        try:
                            initial_cash = float(initial_cash)
                        except Exception:
                            initial_cash = None
                        equity_df = _read_csv(str(equity_path), equity_path.stat().st_mtime)
                        if initial_cash is not None and not equity_df.empty and "equity" in equity_df.columns:
                            end_equity = pd.to_numeric(equity_df["equity"], errors="coerce").dropna().iloc[-1]
                            total_pnl = float(end_equity) - float(initial_cash)
                            if realized_pnl != "":
                                unrealized_pnl = float(total_pnl) - float(realized_pnl)
                st.markdown("**Run/Strategy Summary (from journal)**")
                st.write(
                    {
                        "realized_pnl": float(realized_pnl) if realized_pnl != "" else 0.0,
                        "unrealized_pnl": float(unrealized_pnl) if unrealized_pnl != "" else "",
                        "total_pnl": float(total_pnl) if total_pnl != "" else "",
                        "fees_paid": float(fees_paid),
                        "trades_count": trades_count,
                    }
                )
            if report_log_path.exists():
                report_df = _read_csv(str(report_log_path), report_log_path.stat().st_mtime)
                if selected_run != "All runs" and "notes" in report_df.columns:
                    report_df = report_df[
                        report_df["notes"].astype(str).str.contains(f"run_id={selected_run}", na=False)
                    ]
                st.caption(f"{len(report_df)} report row(s) in {report_log_path}")
                st.dataframe(report_df.tail(30), width="stretch")
            else:
                st.info(f"No report log found at {report_log_path}.")
            if report_md_path.exists():
                st.markdown("**Latest report summary**")
                st.code(report_md_path.read_text(encoding="utf-8"))

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
