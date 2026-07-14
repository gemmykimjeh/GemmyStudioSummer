"""Tests for the A/B experiment runner: stats + end-to-end (offline)."""

from __future__ import annotations

import random

from harness import registry
from harness.experiment import (
    bootstrap_ci,
    load_config,
    paired_delta_ci,
    run_experiment,
)


def test_bootstrap_ci_brackets_mean_and_degenerate():
    rng = random.Random(1)
    vals = [0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]  # mean 0.6
    lo, hi = bootstrap_ci(vals, n_boot=1000, rng=rng)
    assert lo < 0.6 < hi                       # non-degenerate, brackets the mean
    assert bootstrap_ci([1.0, 1.0, 1.0]) == (1.0, 1.0)   # deterministic -> zero width


def test_paired_delta_significant_vs_noise():
    base = {f"t{i}": 0.0 for i in range(6)}
    cond = {f"t{i}": 1.0 for i in range(6)}
    d, lo, hi = paired_delta_ci(base, cond, n_boot=1000, rng=random.Random(1))
    assert d == 1.0 and lo > 0                 # CI excludes 0 -> significant
    d0, lo0, hi0 = paired_delta_ci(base, base, n_boot=1000, rng=random.Random(1))
    assert d0 == 0.0 and lo0 <= 0 <= hi0       # no effect -> includes 0 (noise)


def test_run_experiment_end_to_end_offline(tmp_path):
    registry.load_builtins()
    cfg = {
        "name": "t",
        "benchmarks": ["tau_bench"],
        "benchmark_args": {"tau_bench": {"real": False}},
        "repeats": 2, "limit": 2, "seed": 1, "concurrency": 2,
        "baseline": "off",
        "conditions": [
            {"name": "off", "agent": "scripted", "agent_args": {"max_steps": 0}},
            {"name": "on", "agent": "scripted", "agent_args": {"max_steps": 50}},
        ],
        "output_dir": str(tmp_path / "exp"),
    }
    out = run_experiment(cfg, progress=False)
    exp = tmp_path / "exp"
    assert (exp / "summary.csv").exists()
    assert (exp / "deltas.csv").exists()
    assert (exp / "t_passrate.png").exists()

    m = out["metrics"]
    assert m["on"]["tau_bench"]["pass_rate"] == 1.0    # oracle solves
    assert m["off"]["tau_bench"]["pass_rate"] == 0.0   # no actions
    d = next(x for x in out["deltas"] if x["condition"] == "on")
    assert d["delta_pp"] == 100.0 and d["significant"]


def test_dry_run_lists_plan_without_executing():
    cfg = {
        "name": "t",
        "benchmarks": ["tau_bench", "automation_bench"],
        "conditions": [
            {"name": "a", "agent": "scripted"},
            {"name": "b", "agent": "scripted"},
        ],
    }
    out = run_experiment(cfg, dry_run=True)
    assert out["dry_run"] is True
    assert len(out["plan"]) == 4      # 2 conditions x 2 benchmarks


def test_load_config(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("name: x\nbenchmarks: [tau_bench]\nconditions: []\n",
                 encoding="utf-8")
    assert load_config(p)["name"] == "x"
