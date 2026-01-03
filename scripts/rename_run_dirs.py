from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rename run directories to the readable naming convention.")
    parser.add_argument("--runs-dir", default="research/output/runs", help="Runs directory (default: research/output/runs).")
    parser.add_argument("--index", default="research/output/index.csv", help="Run registry index (default: research/output/index.csv).")
    return parser.parse_args()


def _parse_dt(value: str) -> datetime:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_run_id(cfg: dict[str, object]) -> str:
    symbol = str(cfg.get("symbol") or "unknown")
    granularity_seconds = int(cfg.get("granularity_seconds") or 0)
    data_start = str(cfg.get("data_start") or "")
    data_end = str(cfg.get("data_end") or "")
    config_hash = str(cfg.get("config_hash") or "")[:8]
    created_at = str(cfg.get("created_at_utc") or "")

    if data_start.endswith("Z"):
        data_start = data_start[:-1]
    if data_end.endswith("Z"):
        data_end = data_end[:-1]
    start_date = data_start.split("T")[0] if "T" in data_start else data_start
    end_date = data_end.split("T")[0] if "T" in data_end else data_end

    strategies = cfg.get("strategies") or []
    if isinstance(strategies, list):
        strategy_count = len(strategies) + 1  # + buy_and_hold
    else:
        strategy_count = 0

    if created_at:
        ts = _parse_dt(created_at).strftime("%Y%m%dT%H%M%SZ")
    else:
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return f"{symbol}_{granularity_seconds}s_{start_date}_{end_date}_x{strategy_count}_{config_hash}_{ts}"


def _dedupe_name(target: Path) -> Path:
    if not target.exists():
        return target
    counter = 1
    while True:
        candidate = target.with_name(f"{target.name}_{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def _update_index(index_path: Path, old: str, new: str) -> None:
    if not index_path.exists():
        return
    lines = index_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        return
    header = lines[0]
    if "run_id" not in header:
        return
    updated = [header]
    for line in lines[1:]:
        parts = line.split(",")
        if not parts:
            continue
        if parts[0] == old:
            parts[0] = new
        updated.append(",".join(parts))
    index_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def main() -> None:
    args = _parse_args()
    runs_dir = Path(args.runs_dir)
    if not runs_dir.exists():
        raise SystemExit(f"Runs dir not found: {runs_dir}")
    index_path = Path(args.index)

    for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        cfg_path = run_dir / "config.json"
        if not cfg_path.exists():
            print(f"Skipping (no config.json): {run_dir.name}")
            continue
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        old_name = run_dir.name
        new_name = _build_run_id(cfg)
        target = _dedupe_name(run_dir.parent / new_name)
        if target.name == old_name:
            continue
        run_dir.rename(target)
        cfg["run_id"] = target.name
        (target / "config.json").write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
        _update_index(index_path, old=old_name, new=target.name)
        print(f"Renamed {old_name} -> {target.name}")


if __name__ == "__main__":
    main()
