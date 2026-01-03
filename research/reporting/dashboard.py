from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a local research dashboard from run artifacts.")
    p.add_argument("--runs-dir", default="research/output", help="Directory containing run folders.")
    p.add_argument("--out-dir", default="research/output/dashboard", help="Output directory for dashboard.")
    return p.parse_args()


def _load_runs(runs_dir: Path) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    for path in runs_dir.iterdir():
        if not path.is_dir():
            continue
        metrics = path / "metrics.csv"
        config = path / "config.json"
        if not metrics.exists() or not config.exists():
            continue
        try:
            cfg = json.loads(config.read_text(encoding="utf-8"))
            df = pd.read_csv(metrics)
        except Exception:
            continue
        symbol = str(cfg.get("symbol") or "unknown")
        runs.append({"path": path, "symbol": symbol, "config": cfg, "metrics": df})
    return runs


def _pick_latest_runs(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for run in runs:
        symbol = str(run["symbol"])
        current = latest.get(symbol)
        if current is None or run["path"].name > current["path"].name:
            latest[symbol] = run
    return list(latest.values())


def _plot_bar(df: pd.DataFrame, *, x: str, y: str, title: str, out_path: Path) -> None:
    plt.figure(figsize=(8, 4))
    plt.bar(df[x].astype(str), df[y].astype(float))
    plt.title(title)
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def _render_html(*, out_dir: Path, sections: list[dict[str, object]]) -> None:
    lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'><title>Research Dashboard</title></head><body>",
        "<h1>Research Dashboard</h1>",
        "<p>Auto-generated from latest run artifacts per symbol.</p>",
    ]
    for section in sections:
        lines.append(f"<h2>{section['title']}</h2>")
        if "table_html" in section:
            lines.append(section["table_html"])  # already safe HTML from pandas
        if "img" in section:
            lines.append(f"<img src='{section['img']}' style='max-width:900px;'>")
    lines.append("</body></html>")
    (out_dir / "index.html").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    runs_dir = Path(args.runs_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = _load_runs(runs_dir)
    if not runs:
        raise SystemExit("No run artifacts found. Run backtests first.")
    latest = _pick_latest_runs(runs)

    sections: list[dict[str, object]] = []

    # Per-symbol summary tables and charts.
    for run in sorted(latest, key=lambda r: r["symbol"]):
        symbol = str(run["symbol"])
        metrics = run["metrics"]
        metrics_sorted = metrics.sort_values(by="total_return", ascending=False)
        table_html = metrics_sorted.to_html(index=False)
        sections.append({"title": f"{symbol} — Metrics", "table_html": table_html})

        chart_path = out_dir / f"{symbol}_total_return.png"
        _plot_bar(metrics_sorted, x="strategy", y="total_return", title=f"{symbol} total_return", out_path=chart_path)
        sections.append({"title": f"{symbol} — Total Return", "img": chart_path.name})

        fees_path = out_dir / f"{symbol}_fees_paid_pct.png"
        _plot_bar(metrics_sorted, x="strategy", y="fees_paid_pct", title=f"{symbol} fees_paid_pct", out_path=fees_path)
        sections.append({"title": f"{symbol} — Fees Paid %", "img": fees_path.name})

    _render_html(out_dir=out_dir, sections=sections)
    print(f"Wrote dashboard: {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
