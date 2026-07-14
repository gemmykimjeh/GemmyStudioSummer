"""Build a comparison table across agents on the same benchmark task set.

Reads the per-agent ``results.jsonl`` written by the runner and aligns them by
task id, so you can compare e.g. ``hermes`` vs ``claude_sdk`` head-to-head.

    python -m harness.compare --benchmark tau_bench --agents hermes claude_sdk
    python -m harness.compare --benchmark tau_bench --agents hermes claude_sdk --output-dir runs
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _load(results_path: Path) -> dict[str, dict]:
    """task_id -> {success, score} for the first trial of each task."""
    out: dict[str, dict] = {}
    if not results_path.exists():
        return out
    for line in results_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("trial", 0) != 0:
            continue  # compare on trial 0 for a clean 1:1 table
        out[r["task_id"]] = {
            "success": bool(r.get("success")),
            "score": float(r.get("score", 0.0)),
            "domain": (r.get("metrics") or {}).get("domain", ""),
        }
    return out


def build_comparison(benchmark: str, agents: list[str],
                     output_dir: str | Path = "runs") -> dict:
    base = Path(output_dir)
    per_agent = {a: _load(base / f"{benchmark}__{a}" / "results.jsonl")
                 for a in agents}
    task_ids = sorted({t for m in per_agent.values() for t in m})
    rows = []
    for tid in task_ids:
        row = {"task_id": tid}
        for a in agents:
            cell = per_agent[a].get(tid)
            row[f"{a}_success"] = "" if cell is None else int(cell["success"])
            row[f"{a}_score"] = "" if cell is None else round(cell["score"], 3)
        rows.append(row)
    summary = {}
    for a in agents:
        vals = list(per_agent[a].values())
        n = len(vals)
        summary[a] = {
            "n": n,
            "pass_rate": (sum(v["success"] for v in vals) / n) if n else 0.0,
            "mean_score": (sum(v["score"] for v in vals) / n) if n else 0.0,
        }
    return {"agents": agents, "rows": rows, "summary": summary}


def _write_csv(path: Path, comp: dict) -> None:
    agents = comp["agents"]
    fields = ["task_id"] + [f"{a}_{k}" for a in agents for k in ("success", "score")]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(comp["rows"])


def _print_table(comp: dict) -> None:
    agents = comp["agents"]
    header = f"{'task_id':<22}" + "".join(f"{a[:14]:>16}" for a in agents)
    print(header)
    print("-" * len(header))
    for row in comp["rows"]:
        line = f"{row['task_id']:<22}"
        for a in agents:
            s, sc = row.get(f"{a}_success", ""), row.get(f"{a}_score", "")
            mark = "" if s == "" else ("PASS" if s else "FAIL")
            line += f"{mark + ' ' + str(sc):>16}"
        print(line)
    print("-" * len(header))
    print(f"{'PASS RATE':<22}" + "".join(
        f"{comp['summary'][a]['pass_rate']:>16.3f}" for a in agents))
    print(f"{'MEAN SCORE':<22}" + "".join(
        f"{comp['summary'][a]['mean_score']:>16.3f}" for a in agents))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harness.compare")
    p.add_argument("--benchmark", required=True)
    p.add_argument("--agents", nargs="+", required=True,
                   help="two or more agent names to compare")
    p.add_argument("--output-dir", default="runs")
    args = p.parse_args(argv)

    comp = build_comparison(args.benchmark, args.agents, args.output_dir)
    if not comp["rows"]:
        print("no results found; run the agents first "
              f"(looked under {args.output_dir}/{args.benchmark}__<agent>/)",
              file=sys.stderr)
        return 1

    print(f"=== {args.benchmark}: " + " vs ".join(args.agents) + " ===")
    _print_table(comp)
    out_csv = Path(args.output_dir) / f"compare_{args.benchmark}_{'_'.join(args.agents)}.csv"
    _write_csv(out_csv, comp)
    print(f"\nwrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
