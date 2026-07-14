"""The evaluation orchestrator.

Drives the ``load_tasks -> setup -> agent.run -> score -> teardown`` loop for a
given (agent, benchmark, task subset) with:

* **Parallelism** — up to ``concurrency`` task-trials in flight (thread pool;
  the work is I/O-bound on model/API calls).
* **k trials per task (pass^k)** — each task is run ``k`` times; ``compute_pass_hat_k``
  applies the official unbiased estimator ``C(c, k) / C(n, k)``.
* **Per-task timeout** — a trial exceeding ``timeout`` seconds is recorded as a
  timeout ``Result`` instead of hanging the run.
* **Failure isolation** — any exception in setup/run/score becomes an error
  ``Result``; one dead trial never aborts the sweep.
* **Checkpointing / resume** — each ``Result`` is appended to ``results.jsonl``
  as it completes; re-running with ``resume=True`` skips ``(task_id, trial)``
  pairs already present.
* **Outputs** — full results as JSONL, a per-trial ``summary.csv``, and a
  per-domain ``passk_summary.csv`` (pass@1 + pass^k).

.. note::
   Timeouts use ``concurrent.futures`` cancellation, which cannot forcibly kill
   a thread already inside a blocking C call. The trial is *reported* as timed
   out and the slot is freed for accounting, but a wedged worker thread may
   linger until its call returns. For hard isolation use a process pool or an
   external watchdog; see README > Limitations.
"""

from __future__ import annotations

import csv
import json
import time
import traceback
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from math import comb
from pathlib import Path
from typing import Callable

from harness.agent import Agent
from harness.benchmark import Benchmark
from harness.schema import Result, Task, Trajectory


def _run_one(agent: Agent, benchmark: Benchmark, task: Task, trial: int) -> Result:
    """Run and score a single task-trial with full failure isolation."""
    start = time.perf_counter()
    env = None
    mock = benchmark.mock  # Benchmark base defines mock=False
    try:
        env = benchmark.setup(task)
        trajectory = agent.run(task, env)
        if not isinstance(trajectory, Trajectory):  # defensive: bad agent
            raise TypeError(
                f"{agent.name}.run returned {type(trajectory).__name__}, "
                "expected Trajectory"
            )
        result = benchmark.score(task, trajectory, env)
        # The benchmark doesn't know these; stamp them here.
        result.agent = agent.name
        result.trial = trial
        result.mock = mock
        result.cost = result.cost or trajectory.cost
        result.wall_time = result.wall_time or trajectory.wall_time or (
            time.perf_counter() - start
        )
        return result
    except Exception as exc:  # noqa: BLE001 — isolation is the whole point
        return Result(
            task_id=task.id,
            benchmark=benchmark.name,
            agent=agent.name,
            trial=trial,
            success=False,
            score=0.0,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            wall_time=time.perf_counter() - start,
            mock=mock,
            metrics={"domain": task.metadata.get("domain", benchmark.name)},
        )
    finally:
        if env is not None:
            try:
                benchmark.teardown(env)
            except Exception:  # noqa: BLE001
                pass


def _load_completed(results_path: Path) -> set[tuple[str, int]]:
    """(task_id, trial) pairs already present in a checkpoint (for resume)."""
    done: set[tuple[str, int]] = set()
    if not results_path.exists():
        return done
    with results_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done.add((rec["task_id"], rec.get("trial", 0)))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def run(
    agent: Agent,
    benchmark: Benchmark,
    *,
    limit: int | None = None,
    k: int = 1,
    concurrency: int = 4,
    timeout: float | None = None,
    output_dir: str | Path = "runs",
    resume: bool = False,
    progress: Callable[[Result], None] | None = None,
) -> list[Result]:
    """Evaluate ``agent`` on ``benchmark`` (``k`` trials/task) and return results.

    Results are written to ``<output_dir>/<benchmark>__<agent>/`` as
    ``results.jsonl`` (appended live), ``summary.csv`` (per trial), and
    ``passk_summary.csv`` (per-domain pass@1 + pass^k).
    """
    run_dir = Path(output_dir) / f"{benchmark.name}__{agent.name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"

    completed = _load_completed(results_path) if resume else set()
    if not resume and results_path.exists():
        results_path.unlink()

    tasks = benchmark.load_tasks(limit=limit)
    jobs = [
        (t, trial)
        for t in tasks
        for trial in range(k)
        if (t.id, trial) not in completed
    ]

    results: list[Result] = []
    with results_path.open("a", encoding="utf-8") as ckpt:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {
                pool.submit(_run_one, agent, benchmark, t, trial): (t, trial)
                for (t, trial) in jobs
            }
            pending = set(futures)
            while pending:
                done, pending = wait(
                    pending, timeout=timeout, return_when=FIRST_COMPLETED
                )
                if not done:
                    stuck = next(iter(pending))
                    task, trial = futures[stuck]
                    stuck.cancel()
                    pending.discard(stuck)
                    result = Result(
                        task_id=task.id,
                        benchmark=benchmark.name,
                        agent=agent.name,
                        trial=trial,
                        success=False,
                        score=0.0,
                        error=f"timeout after {timeout}s",
                        wall_time=timeout or 0.0,
                        mock=benchmark.mock,
                        metrics={"domain": task.metadata.get("domain", benchmark.name)},
                    )
                    _record(result, results, ckpt, progress)
                    continue
                for fut in done:
                    _record(fut.result(), results, ckpt, progress)

    _write_summary(run_dir / "summary.csv", results)
    _write_passk_summary(run_dir / "passk_summary.csv", results, k)
    return results


def _record(
    result: Result,
    results: list[Result],
    ckpt,
    progress: Callable[[Result], None] | None,
) -> None:
    results.append(result)
    ckpt.write(result.model_dump_json() + "\n")
    ckpt.flush()
    if progress is not None:
        progress(result)


def _write_summary(path: Path, results: list[Result]) -> None:
    """Per-trial CSV: one row per (task, trial)."""
    fieldnames = [
        "task_id", "trial", "benchmark", "mock", "domain", "agent",
        "success", "score", "cost", "wall_time", "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id": r.task_id,
                "trial": r.trial,
                "benchmark": r.benchmark,
                "mock": "[MOCK]" if r.mock else "",
                "domain": r.metrics.get("domain", ""),
                "agent": r.agent,
                "success": int(r.success),
                "score": round(r.score, 4),
                "cost": round(r.cost, 6),
                "wall_time": round(r.wall_time, 3),
                "error": (r.error or "").splitlines()[0] if r.error else "",
            })


def _pass_hat_k_task(successes: list[bool], k: int) -> float | None:
    """Official unbiased pass^k for one task: C(c, k) / C(n, k).

    ``n`` = trials run, ``c`` = successful trials. Returns None if fewer than
    ``k`` trials exist (can't estimate pass^k). Reduces to the all-pass
    indicator when ``n == k``.
    """
    n = len(successes)
    c = sum(1 for s in successes if s)
    if n < k:
        return None
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


def compute_pass_hat_k(results: list[Result], k: int) -> dict:
    """Per-domain and overall pass@1 and pass^k.

    Returns ``{domain: {n_tasks, trials, pass_at_1, pass_hat_k}, ...,
    "__overall__": {...}}``. ``pass@1`` is the mean per-trial success rate;
    ``pass^k`` is the mean over tasks of the unbiased C(c,k)/C(n,k) estimator.
    """
    by_domain: dict[str, dict[str, list[bool]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in results:
        domain = r.metrics.get("domain", r.benchmark)
        by_domain[domain][r.task_id].append(r.success)

    report: dict[str, dict] = {}
    all_tasks: dict[str, list[bool]] = {}
    for domain, tasks in by_domain.items():
        report[domain] = _aggregate(tasks, k)
        for tid, succ in tasks.items():
            all_tasks[f"{domain}/{tid}"] = succ
    report["__overall__"] = _aggregate(all_tasks, k)
    return report


def _aggregate(tasks: dict[str, list[bool]], k: int) -> dict:
    n_tasks = len(tasks)
    trials = sum(len(s) for s in tasks.values())
    trial_succ = sum(sum(1 for x in s if x) for s in tasks.values())
    pass_hat = [
        v for v in (_pass_hat_k_task(s, k) for s in tasks.values()) if v is not None
    ]
    return {
        "n_tasks": n_tasks,
        "trials": trials,
        "pass_at_1": (trial_succ / trials) if trials else 0.0,
        "pass_hat_k": (sum(pass_hat) / len(pass_hat)) if pass_hat else 0.0,
    }


def _write_passk_summary(path: Path, results: list[Result], k: int) -> None:
    report = compute_pass_hat_k(results, k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["domain", "n_tasks", "trials", "pass@1", f"pass^{k}"])
        for domain in sorted(d for d in report if d != "__overall__"):
            row = report[domain]
            writer.writerow([
                domain, row["n_tasks"], row["trials"],
                round(row["pass_at_1"], 4), round(row["pass_hat_k"], 4),
            ])
        ov = report["__overall__"]
        writer.writerow([
            "OVERALL", ov["n_tasks"], ov["trials"],
            round(ov["pass_at_1"], 4), round(ov["pass_hat_k"], 4),
        ])


def summarize(results: list[Result]) -> dict:
    """Aggregate stats over a results list (handy for CLI output and tests)."""
    n = len(results)
    if n == 0:
        return {"n": 0, "success_rate": 0.0, "mean_score": 0.0,
                "total_cost": 0.0, "errors": 0}
    return {
        "n": n,
        "success_rate": sum(r.success for r in results) / n,
        "mean_score": sum(r.score for r in results) / n,
        "total_cost": sum(r.cost for r in results),
        "errors": sum(1 for r in results if r.error),
    }
