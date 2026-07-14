"""A/B experiment runner: measure the performance delta from a config change.

Fix (benchmark set, task subset, base model); vary one thing across named
**conditions** (memory off/on, loop off/on, claude_sdk vs hermes, empty vs
pre-trained workspace, ...); run each condition on each benchmark N times; then
report per-condition pass rate with a bootstrap confidence interval, the delta
vs a baseline condition (in percentage points, with a paired-bootstrap CI so you
can tell signal from noise), and the cost change. Emits CSVs + a grouped bar
chart (conditions x benchmarks).

    python -m harness.experiment --config configs/loop_ab.yaml
    python -m harness.experiment --config configs/loop_ab.yaml --dry-run

Each condition is just ``registry.get_agent(agent, **agent_args)`` run through
``runner.run(..., k=repeats)`` — so a condition can flip any agent/benchmark
knob (e.g. Hermes ``hermes_home`` for a memory/skills workspace A/B) with no
experiment-runner changes. Config lives in ``configs/*.yaml``.
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import yaml

from harness import registry
from harness._win import ensure_utf8_mode
from harness.runner import run as run_benchmark
from harness.schema import Result

# ---------------------------------------------------------------- stats
def bootstrap_ci(
    values: list[float],
    statistic: Callable[[list[float]], float] = statistics.mean,
    n_boot: int = 2000,
    alpha: float = 0.05,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """Percentile bootstrap CI for ``statistic`` over ``values``.

    Resamples ``values`` with replacement ``n_boot`` times. Returns
    ``(lo, hi)`` at the ``alpha`` two-sided level. Degenerate (all-equal) inputs
    give a zero-width interval, which is the honest answer for deterministic data.
    """
    if not values:
        return (0.0, 0.0)
    rng = rng or random.Random(0)
    n = len(values)
    stats = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        stats.append(statistic(sample))
    stats.sort()
    lo = stats[int((alpha / 2) * n_boot)]
    hi = stats[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def paired_delta_ci(
    base_by_task: dict[str, float],
    cond_by_task: dict[str, float],
    n_boot: int = 2000,
    alpha: float = 0.05,
    rng: random.Random | None = None,
) -> tuple[float, float, float]:
    """Paired bootstrap CI for the mean per-task delta ``cond - base``.

    Resamples the shared task ids (paired), so it isolates the condition effect
    from task difficulty. Returns ``(delta, lo, hi)``. If the CI excludes 0 the
    difference is significant at ``alpha``.
    """
    rng = rng or random.Random(0)
    tasks = sorted(set(base_by_task) & set(cond_by_task))
    if not tasks:
        return (0.0, 0.0, 0.0)
    diffs = [cond_by_task[t] - base_by_task[t] for t in tasks]
    delta = statistics.mean(diffs)
    lo, hi = bootstrap_ci(diffs, n_boot=n_boot, alpha=alpha, rng=rng)
    return (delta, lo, hi)


# ---------------------------------------------------------------- aggregation
def _per_task(results: list[Result]) -> dict[str, dict[str, list]]:
    """task_id -> {success:[...], score:[...], cost:[...]} across repeats."""
    agg: dict[str, dict[str, list]] = defaultdict(
        lambda: {"success": [], "score": [], "cost": []})
    for r in results:
        agg[r.task_id]["success"].append(1.0 if r.success else 0.0)
        agg[r.task_id]["score"].append(r.score)
        agg[r.task_id]["cost"].append(r.cost)
    return agg


def _condition_metrics(results: list[Result], rng: random.Random,
                       n_boot: int) -> dict[str, Any]:
    """Pass rate (+ bootstrap CI over tasks), mean score, cost for one cell."""
    per_task = _per_task(results)
    task_pass = {t: statistics.mean(v["success"]) for t, v in per_task.items()}
    task_cost = {t: statistics.mean(v["cost"]) for t, v in per_task.items()}
    pass_vals = list(task_pass.values())
    pr = statistics.mean(pass_vals) if pass_vals else 0.0
    lo, hi = bootstrap_ci(pass_vals, n_boot=n_boot, rng=rng)
    return {
        "n_tasks": len(per_task),
        "n_trials": sum(len(v["success"]) for v in per_task.values()),
        "pass_rate": pr,
        "pr_ci_lo": lo,
        "pr_ci_hi": hi,
        "mean_score": statistics.mean(
            [s for v in per_task.values() for s in v["score"]]) if per_task else 0.0,
        "mean_cost_per_task": statistics.mean(list(task_cost.values()))
            if task_cost else 0.0,
        "total_cost": sum(r.cost for r in results),
        "errors": sum(1 for r in results if r.error),
        "_task_pass": task_pass,   # for paired deltas
        "_task_cost": task_cost,
    }


# ---------------------------------------------------------------- experiment
def run_experiment(config: dict, *, dry_run: bool = False,
                   progress: bool = True) -> dict:
    """Run every (condition x benchmark) cell, compute metrics + deltas, emit outputs."""
    name = config.get("name", "experiment")
    benchmarks = config["benchmarks"]
    conditions = config["conditions"]
    base_model = config.get("base_model")
    repeats = int(config.get("repeats", 3))
    limit = config.get("limit")
    seed = int(config.get("seed", 0))
    concurrency = int(config.get("concurrency", 4))
    timeout = config.get("timeout")
    n_boot = int(config.get("n_boot", 2000))
    bench_args_all: dict = config.get("benchmark_args", {})
    baseline = config.get("baseline") or conditions[0]["name"]
    out_dir = Path(config.get("output_dir", f"runs/exp_{name}"))
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = [(c["name"], b) for c in conditions for b in benchmarks]
    if dry_run:
        print(f"[dry-run] experiment '{name}': base_model={base_model} "
              f"repeats={repeats} limit={limit} baseline={baseline}")
        for c in conditions:
            print(f"  condition {c['name']}: agent={c['agent']} "
                  f"args={ {**({'model': base_model} if base_model else {}), **c.get('agent_args', {})} }")
        print(f"  benchmarks: {benchmarks}  ->  {len(plan)} cells x {repeats} repeats")
        return {"plan": plan, "dry_run": True}

    rng = random.Random(seed)
    # metrics[condition][benchmark] = cell metrics dict
    metrics: dict[str, dict[str, dict]] = defaultdict(dict)
    for cond in conditions:
        cname = cond["name"]
        agent_args = dict(cond.get("agent_args", {}))
        if base_model is not None:
            agent_args.setdefault("model", base_model)
        agent = registry.get_agent(cond["agent"], **agent_args)
        for bench_name in benchmarks:
            b_args = dict(bench_args_all.get(bench_name, {}))
            benchmark = registry.get_benchmark(bench_name, **b_args)
            if progress:
                print(f"[run] {cname} x {bench_name} "
                      f"(agent={cond['agent']}, repeats={repeats})", flush=True)
            results = run_benchmark(
                agent, benchmark, limit=limit, k=repeats,
                concurrency=concurrency, timeout=timeout,
                output_dir=out_dir / cname,
            )
            metrics[cname][bench_name] = _condition_metrics(results, rng, n_boot)

    deltas = _compute_deltas(metrics, benchmarks, conditions, baseline, rng, n_boot)
    _write_summary_csv(out_dir / "summary.csv", metrics, benchmarks)
    _write_deltas_csv(out_dir / "deltas.csv", deltas)
    png = _plot(out_dir / f"{name}_passrate.png", name, metrics, benchmarks,
                [c["name"] for c in conditions])
    _print_report(name, metrics, benchmarks, deltas, baseline, out_dir, png)
    return {"metrics": metrics, "deltas": deltas, "output_dir": str(out_dir)}


def _compute_deltas(metrics, benchmarks, conditions, baseline, rng, n_boot) -> list[dict]:
    rows = []
    for bench in benchmarks:
        base_cell = metrics.get(baseline, {}).get(bench)
        if not base_cell:
            continue
        for cond in conditions:
            cname = cond["name"]
            if cname == baseline:
                continue
            cell = metrics.get(cname, {}).get(bench)
            if not cell:
                continue
            delta, lo, hi = paired_delta_ci(
                base_cell["_task_pass"], cell["_task_pass"],
                n_boot=n_boot, rng=rng)
            cost_delta = cell["mean_cost_per_task"] - base_cell["mean_cost_per_task"]
            rows.append({
                "benchmark": bench,
                "condition": cname,
                "baseline": baseline,
                "delta_pp": 100 * delta,
                "delta_ci_lo_pp": 100 * lo,
                "delta_ci_hi_pp": 100 * hi,
                "significant": (lo > 0 or hi < 0),
                "cost_delta_per_task": cost_delta,
            })
    return rows


# ---------------------------------------------------------------- outputs
def _write_summary_csv(path: Path, metrics, benchmarks) -> None:
    fields = ["condition", "benchmark", "n_tasks", "n_trials", "pass_rate",
              "pr_ci_lo", "pr_ci_hi", "mean_score", "mean_cost_per_task",
              "total_cost", "errors"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for cond, benches in metrics.items():
            for bench in benchmarks:
                m = benches.get(bench)
                if not m:
                    continue
                w.writerow({
                    "condition": cond, "benchmark": bench,
                    "n_tasks": m["n_tasks"], "n_trials": m["n_trials"],
                    "pass_rate": round(m["pass_rate"], 4),
                    "pr_ci_lo": round(m["pr_ci_lo"], 4),
                    "pr_ci_hi": round(m["pr_ci_hi"], 4),
                    "mean_score": round(m["mean_score"], 4),
                    "mean_cost_per_task": round(m["mean_cost_per_task"], 6),
                    "total_cost": round(m["total_cost"], 6),
                    "errors": m["errors"],
                })


def _write_deltas_csv(path: Path, deltas: list[dict]) -> None:
    fields = ["benchmark", "condition", "baseline", "delta_pp",
              "delta_ci_lo_pp", "delta_ci_hi_pp", "significant",
              "cost_delta_per_task"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for d in deltas:
            w.writerow({**d,
                        "delta_pp": round(d["delta_pp"], 2),
                        "delta_ci_lo_pp": round(d["delta_ci_lo_pp"], 2),
                        "delta_ci_hi_pp": round(d["delta_ci_hi_pp"], 2),
                        "significant": int(d["significant"]),
                        "cost_delta_per_task": round(d["cost_delta_per_task"], 6)})


def _plot(path: Path, name, metrics, benchmarks, condition_names) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = range(len(benchmarks))
    n_cond = len(condition_names)
    width = 0.8 / max(1, n_cond)
    fig, ax = plt.subplots(figsize=(1.8 + 1.8 * len(benchmarks), 4.5))
    for i, cond in enumerate(condition_names):
        heights, lo_err, hi_err = [], [], []
        for bench in benchmarks:
            m = metrics.get(cond, {}).get(bench)
            pr = (m["pass_rate"] * 100) if m else 0.0
            heights.append(pr)
            lo_err.append(pr - (m["pr_ci_lo"] * 100 if m else 0.0))
            hi_err.append((m["pr_ci_hi"] * 100 if m else 0.0) - pr)
        offs = [xi + (i - (n_cond - 1) / 2) * width for xi in x]
        ax.bar(offs, heights, width, label=cond,
               yerr=[lo_err, hi_err], capsize=4)
    ax.set_xticks(list(x))
    ax.set_xticklabels(benchmarks)
    ax.set_ylabel("pass rate (%)")
    ax.set_title(f"{name}: pass rate by condition x benchmark (95% bootstrap CI)")
    ax.legend()
    ax.set_ylim(0, 105)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _print_report(name, metrics, benchmarks, deltas, baseline, out_dir, png) -> None:
    print(f"\n=== experiment '{name}' — pass rate (95% bootstrap CI) ===")
    for cond, benches in metrics.items():
        for bench in benchmarks:
            m = benches.get(bench)
            if not m:
                continue
            print(f"  {cond:<14} {bench:<18} "
                  f"pass={m['pass_rate']*100:5.1f}%  "
                  f"CI[{m['pr_ci_lo']*100:4.1f},{m['pr_ci_hi']*100:5.1f}]  "
                  f"cost/task=${m['mean_cost_per_task']:.4f}  "
                  f"(n={m['n_tasks']}x{m['n_trials']//max(1,m['n_tasks'])})")
    print(f"\n=== deltas vs baseline '{baseline}' (pp = percentage points) ===")
    if not deltas:
        print("  (only the baseline condition present)")
    for d in deltas:
        sig = "SIGNIFICANT" if d["significant"] else "not significant (noise)"
        print(f"  {d['condition']:<14} {d['benchmark']:<18} "
              f"Δ={d['delta_pp']:+5.1f}pp  "
              f"CI[{d['delta_ci_lo_pp']:+5.1f},{d['delta_ci_hi_pp']:+5.1f}]  "
              f"cost Δ=${d['cost_delta_per_task']:+.4f}  {sig}")
    print(f"\noutputs: {out_dir}/  (summary.csv, deltas.csv, {Path(png).name})")


# ---------------------------------------------------------------- CLI
def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="harness.experiment")
    p.add_argument("--config", required=True, help="path to a configs/*.yaml")
    p.add_argument("--dry-run", action="store_true",
                   help="print the condition x benchmark plan and exit")
    args = p.parse_args(argv)
    ensure_utf8_mode("harness.experiment")
    registry.load_builtins()
    run_experiment(load_config(args.config), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
