"""CLI entry point.

    python -m harness.run --agent claude_sdk --benchmark tau_bench --limit 5

Run ``python -m harness.run --list`` to see registered agents and benchmarks.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness import registry
from harness._win import ensure_utf8_mode
from harness.repro import collect_provenance
from harness.runner import compute_pass_hat_k, run, summarize
from harness.schema import Result


def _print_progress(result: Result) -> None:
    mark = "PASS" if result.success else ("ERR " if result.error else "FAIL")
    tag = "[MOCK] " if result.mock else ""
    line = f"  {tag}[{mark}] {result.task_id:<24} score={result.score:.3f}"
    if result.cost:
        line += f" cost=${result.cost:.4f}"
    if result.error:
        line += f"  ({result.error.splitlines()[0]})"
    print(line, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness",
        description="Run any agent against any registered benchmark.",
    )
    p.add_argument("--agent", default="claude_sdk",
                   help="registered agent name (default: claude_sdk)")
    p.add_argument("--benchmark", help="registered benchmark name (e.g. tau_bench)")
    p.add_argument("--limit", type=int, default=None, help="max tasks to run")
    p.add_argument("--k", type=int, default=1,
                   help="trials per task for pass^k (default 1)")
    p.add_argument("--concurrency", type=int, default=4, help="parallel tasks")
    p.add_argument("--timeout", type=float, default=None,
                   help="per-task timeout in seconds")
    p.add_argument("--output-dir", default="runs",
                   help="parent dir for run directories (default: runs)")
    p.add_argument("--run-id", default=None,
                   help="run directory name under --output-dir (default: UTC "
                        "timestamp). Reuse an id to group several benchmarks "
                        "into one run directory.")
    p.add_argument("--resume", action="store_true",
                   help="skip task ids already in the checkpoint")
    p.add_argument("--model", default=None,
                   help="model id override (agent-specific, e.g. claude-opus-4-8)")
    p.add_argument("--max-steps", type=int, default=None,
                   help="agent step budget override")
    p.add_argument("--native-tools", action="store_true",
                   help="A-mode: let the agent use its OWN native tools on the "
                        "benchmark's local workspace (claude_agent_sdk). Needs an "
                        "A-capable benchmark (e.g. swe_bench); B-only benchmarks "
                        "error with guidance. Omit for the tool-bridge (B) mode.")
    # tau_bench (and other real-vs-mock benchmarks) options:
    p.add_argument("--real", action=argparse.BooleanOptionalAction, default=True,
                   help="use the real upstream benchmark (default). "
                        "--no-real uses the offline mock fallback.")
    p.add_argument("--split", default="test", help="task split (e.g. test)")
    p.add_argument("--user-model", default="gpt-4o",
                   help="user-simulator model (separate from --model)")
    p.add_argument("--user-provider", default=None,
                   help="user-sim provider (inferred from --user-model if unset)")
    p.add_argument("--user-strategy", default="llm",
                   help="user-simulator strategy (llm|human|react|verify|reflection)")
    # terminal_bench (Docker-backed) options:
    p.add_argument("--env-endpoint", default=None,
                   help="Docker endpoint for infra-heavy benches (sets DOCKER_HOST); "
                        "omit to use the local Docker daemon.")
    p.add_argument("--tasks", default=None,
                   help="comma-separated task-id subset (e.g. terminal_bench --tasks hello-world)")
    # osworld (OSWorld-Verified) provider options:
    p.add_argument("--provider", default=None,
                   help="osworld VM provider: docker|vmware|virtualbox|aws|attach "
                        "(default docker). 'attach' needs --env-endpoint and is a "
                        "non-verified wiring smoke-test.")
    p.add_argument("--meta", default=None,
                   help="osworld task list under evaluation_examples/ "
                        "(test_all.json | test_nogdrive.json | test_small.json)")
    p.add_argument("--path-to-vm", default=None,
                   help="osworld: .vmx path (vmware) / EC2 instance id (aws)")
    p.add_argument("--region", default=None, help="osworld aws region")
    p.add_argument("--snapshot-name", default=None,
                   help="osworld snapshot (default init_state; aws uses the AMI id)")
    p.add_argument("--enable-proxy", action="store_true",
                   help="osworld: enable proxy for tasks that require it")
    p.add_argument("--list", action="store_true",
                   help="list registered agents and benchmarks, then exit")
    return p


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_mode("harness.run")
    args = build_parser().parse_args(argv)
    registry.load_builtins()

    if args.list:
        print("agents:    ", ", ".join(registry.available_agents()))
        print("benchmarks:", ", ".join(registry.available_benchmarks()))
        return 0

    if not args.agent or not args.benchmark:
        print("error: --agent and --benchmark are required "
              "(or use --list)", file=sys.stderr)
        return 2

    agent_kwargs: dict = {}
    if args.model is not None:
        agent_kwargs["model"] = args.model
    if args.max_steps is not None:
        agent_kwargs["max_steps"] = args.max_steps
    if args.native_tools:
        agent_kwargs["native_tools"] = True  # A-mode; _filter_kwargs drops it for
                                             # agents that don't support it

    # Benchmark kwargs are filtered to each adapter's __init__ (registry), so
    # passing tau-bench-specific options to a benchmark that ignores them is safe.
    benchmark_kwargs = {
        "real": args.real,
        "split": args.split,
        "user_model": args.user_model,
        "user_provider": args.user_provider,
        "user_strategy": args.user_strategy,
        "env_endpoint": args.env_endpoint,
        "task_ids": args.tasks,
    }
    # osworld provider options — only inject when set (so the adapter's own
    # defaults stand); _filter_kwargs drops these for benchmarks that ignore them.
    for key, val in (("provider", args.provider), ("meta", args.meta),
                     ("path_to_vm", args.path_to_vm), ("region", args.region),
                     ("snapshot_name", args.snapshot_name)):
        if val is not None:
            benchmark_kwargs[key] = val
    if args.enable_proxy:
        benchmark_kwargs["enable_proxy"] = True

    try:
        agent = registry.get_agent(args.agent, **agent_kwargs)
        benchmark = registry.get_benchmark(args.benchmark, **benchmark_kwargs)
    except (KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # One execution = one run directory (reuse --run-id to group benchmarks).
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running {agent.name} on {benchmark.name} "
          f"(limit={args.limit}, k={args.k}, concurrency={args.concurrency}) "
          f"-> {run_dir}")
    if benchmark.mock:
        print("  [MOCK] shape-faithful reproduction -- NOT an official upstream "
              "score. Real upstream integration pending.")
    try:
        results = run(
            agent,
            benchmark,
            limit=args.limit,
            k=args.k,
            concurrency=args.concurrency,
            timeout=args.timeout,
            output_dir=run_dir,
            resume=args.resume,
            progress=_print_progress,
        )
    except NotImplementedError as exc:
        # A Phase-2 stub benchmark can't even produce tasks — report its
        # guidance cleanly instead of dumping a traceback.
        print(f"\nerror: benchmark {benchmark.name!r} is not implemented.\n{exc}",
              file=sys.stderr)
        return 1

    stats = summarize(results)
    mock_run = any(r.mock for r in results)

    # Append this execution's manifest (config snapshot + provenance) so the run
    # directory is fully reproducible and reportable. One line per invocation;
    # grouping several benchmarks under one --run-id appends several records.
    manifest = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "argv": list(argv) if argv is not None else sys.argv[1:],
        "agent": agent.name,
        "benchmark": benchmark.name,
        "mock": mock_run,
        "config": {
            "model": args.model, "max_steps": args.max_steps,
            "limit": args.limit, "k": args.k, "concurrency": args.concurrency,
            "timeout": args.timeout, "split": args.split, "real": args.real,
            "user_model": args.user_model, "user_provider": args.user_provider,
            "user_strategy": args.user_strategy, "env_endpoint": args.env_endpoint,
            "tasks": args.tasks,
        },
        "summary": stats,
        "provenance": collect_provenance(root=Path(__file__).resolve().parents[1]),
    }
    with (run_dir / "manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest) + "\n")
    print("\n=== summary" + (" [MOCK]" if mock_run else "") + " ===")
    print(f"tasks:        {stats['n']}")
    print(f"success rate: {stats['success_rate']:.1%}")
    print(f"mean score:   {stats['mean_score']:.3f}")
    print(f"total cost:   ${stats['total_cost']:.4f}")
    print(f"errors:       {stats['errors']}")

    # pass@1 / pass^k, broken down by domain (the tau-bench headline metric).
    report = compute_pass_hat_k(results, args.k)
    print(f"\n=== pass^{args.k} by domain" + (" [MOCK]" if mock_run else "") + " ===")
    header = f"  {'domain':<16} {'tasks':>5} {'trials':>6} {'pass@1':>7} " \
             f"{'pass^' + str(args.k):>7}"
    print(header)
    for domain in sorted(d for d in report if d != "__overall__"):
        row = report[domain]
        print(f"  {domain:<16} {row['n_tasks']:>5} {row['trials']:>6} "
              f"{row['pass_at_1']:>7.3f} {row['pass_hat_k']:>7.3f}")
    ov = report["__overall__"]
    print(f"  {'OVERALL':<16} {ov['n_tasks']:>5} {ov['trials']:>6} "
          f"{ov['pass_at_1']:>7.3f} {ov['pass_hat_k']:>7.3f}")

    # Optional benchmark-specific tail report (e.g. AutomationBench domain +
    # false-success-claim breakdown, public-vs-official caveat).
    extra = benchmark.report(results)
    if extra:
        print()
        print(extra)

    print(f"\nresults:      {run_dir}/{benchmark.name}__{agent.name}/")
    print(f"report:       python -m harness.report {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
