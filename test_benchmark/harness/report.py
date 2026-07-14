"""Generate a markdown report from a run directory (read-only).

    python -m harness.report runs/<run-id>/                 # one run
    python -m harness.report runs/<old>/ runs/<new>/        # A/B / before/after diff

This project builds and *verifies wiring* between agents and benchmarks; it is
not a performance-measurement campaign. Real executions were deliberately small
(connection checks; many [MOCK]/demo). This report is written to reflect that
honestly, so a small N is never dressed up as a representative score.

The report reads ONLY what a run directory already contains — it never executes
a benchmark. It emits, in order:

  1. A **connection / verification status matrix** (agent x benchmark) — the
     headline: WIRED-REAL / WIRED-MOCK / STUB / INFRA-MISSING / "-" (not run here).
  2. A **score summary** for measured cells only, each tagged with N and
     [MOCK]/small, plus a failure-type breakdown for benches that have samples.
  3. A **multi-run diff** when two run dirs are given (else "데이터 부족").
  4. A **provenance + license/data/scoring** footer (both code and data licenses
     where they differ, and how our scoring differs from the official numbers).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from harness import registry
from harness.benchmarks._stub import StubBenchmark

# --- static capability catalog (code + data facts; the report's footer + infra) -
# infra ∈ {none, keys, docker, vm}. license_data is set only when it differs from
# the code license (e.g. SWE-bench's code vs its dataset).
CATALOG: dict[str, dict] = {
    "tau_bench": {
        "infra": "keys",
        "license_code": "MIT — sierra-research/tau-bench",
        "license_data": None,
        "data": "τ-bench retail/airline tasks (upstream repo, external/tau-bench)",
        "scoring": "official final-DB-state reward + required-info match; pass^k",
        "official_diff": "public task set; small N for wiring, not the official "
                         "leaderboard sweep. Ships an offline [MOCK] fallback.",
    },
    "automation_bench": {
        "infra": "none",
        "license_code": "MIT — zapier/AutomationBench",
        "license_data": None,
        "data": "AutomationBench public task set (upstream repo / datasets)",
        "scoring": "deterministic final-state assertions (no LLM judge)",
        "official_diff": "public subset; small N for wiring. Ships an offline "
                         "[MOCK] fallback.",
    },
    "swe_bench": {
        "infra": "docker",
        "license_code": "MIT — swe-bench/SWE-bench (harness code)",
        "license_data": "princeton-nlp/SWE-bench_Verified: no license stated on the HF "
                        "card; each instance derives from its source GitHub repo "
                        "under that repo's own license (Django BSD, astropy BSD, ...)",
        "data": "SWE-bench Verified (500 instances)",
        "scoring": "official swebench.harness.run_evaluation — resolved iff all "
                   "FAIL_TO_PASS + PASS_TO_PASS pass",
        "official_diff": "Verified split, official scoring unaltered. Runner image "
                         "built + wiring verified; a full scored instance is heavy "
                         "(multi-GB images) and was not executed.",
    },
    "terminal_bench": {
        "infra": "docker",
        "license_code": "Apache-2.0 — laude-institute/terminal-bench-2 (Terminal-Bench 2.0)",
        "license_data": "tasks Apache-2.0; prebuilt images (alexgshaw/*, HF harborframework/terminal-bench-2.0) bundle third-party software under their own licenses",
        "data": "Terminal-Bench 2.0 (89 tasks, external/terminal-bench-2)",
        "scoring": "official 2.0 verifier — run the task's tests/test.sh -> reward.txt (1/0)",
        "official_diff": "runs the real 2.0 tasks + prebuilt images + official verifier "
                         "(not the old terminal-bench-core/original set). Small N for wiring.",
    },
    "browsecomp": {
        "infra": "keys",
        "license_code": "MIT — openai/simple-evals",
        "license_data": None,
        "data": "BrowseComp public test CSV (openaipublic blob)",
        "scoring": "official grader model verdict correct: yes|no -> 1.0/0.0",
        "official_diff": "official grader; browsing fulfilled via Anthropic "
                         "server-side web tools (implementation detail). Small N.",
    },
    "osworld": {
        "infra": "vm",
        "license_code": "Apache-2.0 — xlang-ai/OSWorld (OSWorld-Verified)",
        "license_data": None,
        "data": "OSWorld-Verified test_all.json (369 tasks) / test_nogdrive (361)",
        "scoring": "official DesktopEnv.evaluate() on verified evaluators — reward [0,1]",
        "official_diff": "drives a real provider VM (docker/vmware/aws) with per-task "
                         "snapshot-revert. You provision the provider. Not exercised "
                         "without one; 'attach' mode is a non-verified wiring check.",
    },
    "gdpval": {
        "infra": "keys",
        "license_code": "adapter: ours (MIT, this repo) — GDPval has no upstream code repo",
        "license_data": "openai/gdpval: no license identifier stated on the HF card; "
                        "governed by OpenAI's GDPval release terms",
        "data": "GDPval 220 open gold tasks / 44 occupations (openai/gdpval)",
        "scoring": "official rubric (rubric_json checklist) graded by an LLM judge; "
                   "earned/max points in [0, 1]",
        "official_diff": "GDPval, NOT GDPval-AA. Rubric-based automated grading "
                         "(human expert pairwise is GDPval's gold standard). Small N.",
    },
}

_INFRA_LABEL = {"none": "keys/none", "keys": "API key",
                "docker": "Docker", "vm": "desktop VM"}

_STATUS_LEGEND = {
    "WIRED-REAL": "real (non-mock) results present — end-to-end wiring verified",
    "WIRED-MOCK": "only [MOCK] results — shape-faithful, NOT an upstream score",
    "STUB": "adapter not implemented (raises NotImplementedError)",
    "INFRA-MISSING": "real adapter, but the run hit a Docker/VM/endpoint precheck",
    "—": "not exercised in this run directory",
}

_SMALL_N = 20  # at/under this, a score is flagged "connection-check, small"
_INFRA_ERR = ("docker", "vm ", "desktop vm", "cannot reach", "not reachable",
              "env-endpoint", "endpoint", "/dev/kvm", "daemon", "runner image")


# --------------------------------------------------------------------------- IO
def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_run(run_dir: Path) -> dict:
    """Read a run directory: manifests + all results, grouped by (agent, bench)."""
    manifests: list[dict] = []
    for mp in sorted(run_dir.glob("**/manifest.jsonl")):
        manifests.extend(_read_jsonl(mp))
    records: list[dict] = []
    for rp in sorted(run_dir.glob("**/results.jsonl")):
        records.extend(_read_jsonl(rp))
    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in records:
        by_cell[(r.get("agent", "?"), r.get("benchmark", "?"))].append(r)
    return {"dir": run_dir, "manifests": manifests, "records": records,
            "by_cell": by_cell}


# ----------------------------------------------------------------- aggregation
def _is_stub(bench: str) -> bool:
    try:
        return issubclass(registry.benchmark_class(bench), StubBenchmark)
    except KeyError:
        return False


def _cell_status(bench: str, recs: list[dict]) -> str:
    if _is_stub(bench):
        return "STUB"
    if not recs:
        return "—"
    if any(r.get("mock") for r in recs):
        return "WIRED-MOCK"
    # real records present: distinguish an infra precheck failure from a real run
    errs = [(r.get("error") or "").lower() for r in recs]
    all_failed = all(not r.get("success") and r.get("error") for r in recs)
    if all_failed and any(any(tok in e for tok in _INFRA_ERR) for e in errs):
        return "INFRA-MISSING"
    return "WIRED-REAL"


def _cell_stats(recs: list[dict]) -> dict:
    tasks = {r.get("task_id") for r in recs}
    n = len(recs)
    succ = sum(1 for r in recs if r.get("success"))
    return {
        "n_tasks": len(tasks), "trials": n,
        "success_rate": (succ / n) if n else 0.0,
        "mean_score": (sum(float(r.get("score", 0.0)) for r in recs) / n) if n else 0.0,
        "cost": sum(float(r.get("cost", 0.0)) for r in recs),
        "errors": sum(1 for r in recs if r.get("error")),
        "mock": any(r.get("mock") for r in recs),
    }


def _classify_error(err: str) -> str:
    e = (err or "").lower()
    if not e:
        return "ok"
    if "timeout" in e:
        return "timeout"
    if any(tok in e for tok in _INFRA_ERR):
        return "infra"
    if "api_key" in e or "anthropic_api_key" in e or "key" in e:
        return "auth/key"
    if "empty" in e:
        return "empty-output"
    return "other"


# --------------------------------------------------------------- md rendering
def _esc(cell: object) -> str:
    return str(cell).replace("|", "\\|")


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    line = "| " + " | ".join(_esc(h) for h in headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(_esc(c) for c in r) + " |" for r in rows)
    return "\n".join([line, sep, body]) if rows else line + "\n" + sep


def _status_matrix(run: dict, agents: list[str], benches: list[str]) -> str:
    headers = ["agent \\ benchmark"] + benches
    rows = []
    for a in agents:
        row = [a]
        for b in benches:
            row.append(_cell_status(b, run["by_cell"].get((a, b), [])))
        rows.append(row)
    # infra requirement row (bench-level, explains potential INFRA-MISSING)
    infra_row = ["_infra needed_"] + [
        _INFRA_LABEL.get(CATALOG.get(b, {}).get("infra", "none"), "?") for b in benches]
    rows.append(infra_row)
    return _md_table(headers, rows)


def _provenance_md(manifests: list[dict]) -> str:
    if not manifests:
        return "_No manifest.jsonl in this run directory — provenance unavailable._"
    prov = manifests[-1].get("provenance", {})
    models = sorted({(m.get("config") or {}).get("model") or "(agent default)"
                     for m in manifests})
    pkgs = prov.get("packages", {})
    pkg_str = ", ".join(f"{k} {v}" for k, v in pkgs.items() if v) or "n/a"
    git = prov.get("git_commit") or prov.get("git_note") or "unknown"
    if prov.get("git_dirty"):
        git += " (dirty)"
    hermes = prov.get("hermes_commit") or prov.get("hermes_note") or "unknown"
    lines = [
        f"- **run id**: {manifests[-1].get('run_id', '?')} "
        f"({len(manifests)} invocation(s))",
        f"- **git commit**: {git}",
        f"- **Hermes commit**: {hermes} (`{prov.get('hermes_path', '?')}`)",
        f"- **models used**: {', '.join(models)}",
        f"- **python**: {prov.get('python', '?')} · {prov.get('platform', '?')}",
        f"- **packages**: {pkg_str}",
    ]
    return "\n".join(lines)


def _score_table(run: dict) -> str:
    rows = []
    for (a, b), recs in sorted(run["by_cell"].items()):
        if _is_stub(b):
            continue
        s = _cell_stats(recs)
        tags = []
        if s["mock"]:
            tags.append("[MOCK]")
        if s["n_tasks"] <= _SMALL_N:
            tags.append(f"small (N={s['n_tasks']}, connection-check)")
        rows.append([
            a, b, s["n_tasks"], s["trials"],
            f"{s['success_rate']:.3f}", f"{s['mean_score']:.3f}",
            f"${s['cost']:.4f}", " ".join(tags) or "—",
        ])
    if not rows:
        return "_No measured cells in this run directory._"
    return _md_table(
        ["agent", "benchmark", "N tasks", "trials", "success", "mean score",
         "cost", "tags"], rows)


def _failure_breakdown(run: dict) -> str:
    by_bench: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (_a, b), recs in run["by_cell"].items():
        if _is_stub(b):
            continue
        for r in recs:
            by_bench[b][_classify_error(r.get("error") or "")] += 1
    rows = []
    for b in sorted(by_bench):
        cats = by_bench[b]
        total = sum(cats.values())
        detail = ", ".join(f"{k}:{v}" for k, v in sorted(cats.items()) if k != "ok")
        rows.append([b, total, cats.get("ok", 0), detail or "—"])
    if not rows:
        return "_No samples to break down._"
    return _md_table(["benchmark", "trials", "ok", "failures (type:count)"], rows)


def _diff_md(run_a: dict, run_b: dict) -> str:
    cells = sorted(set(run_a["by_cell"]) | set(run_b["by_cell"]))
    rows = []
    for (a, b) in cells:
        ra, rb = run_a["by_cell"].get((a, b)), run_b["by_cell"].get((a, b))
        sa = _cell_stats(ra)["mean_score"] if ra else None
        sb = _cell_stats(rb)["mean_score"] if rb else None
        if sa is None and sb is None:
            continue
        delta = (f"{sb - sa:+.3f}" if sa is not None and sb is not None else "n/a")
        rows.append([f"{a} × {b}",
                     "—" if sa is None else f"{sa:.3f}",
                     "—" if sb is None else f"{sb:.3f}", delta])
    if not rows:
        return "_데이터 부족: 두 run에 공통으로 측정된 셀이 없음._"
    return _md_table(["agent × benchmark", "A mean", "B mean", "Δ (B−A)"], rows)


def _license_footer(benches: list[str]) -> str:
    rows = []
    for b in benches:
        c = CATALOG.get(b, {})
        lic = c.get("license_code", "?")
        if c.get("license_data"):
            lic += f" · **data:** {c['license_data']}"
        rows.append([b, lic, c.get("data", "?"), c.get("scoring", "?"),
                     c.get("official_diff", "?")])
    return _md_table(
        ["benchmark", "license", "data source", "scoring", "vs official"], rows)


def render(run_dirs: list[Path]) -> str:
    registry.load_builtins()
    runs = [load_run(d) for d in run_dirs]
    primary = runs[0]

    benches = registry.available_benchmarks()
    agents = registry.available_agents()
    # include any agent that appears in results but isn't registered
    for (a, _b) in primary["by_cell"]:
        if a not in agents:
            agents.append(a)

    out: list[str] = []
    out.append(f"# Harness report — `{primary['dir']}`")
    out.append("")
    out.append("> **Scope:** this project builds and verifies agent↔benchmark "
               "*wiring*. Real runs are small connection-checks; scores below are "
               "NOT representative performance. `[MOCK]` = shape-only, not an "
               "upstream score.")
    out.append("")
    out.append("## Provenance")
    out.append(_provenance_md(primary["manifests"]))
    out.append("")
    out.append("## 1. Connection / verification status  (agent × benchmark)")
    out.append("")
    out.append(_status_matrix(primary, agents, benches))
    out.append("")
    n_stub = sum(1 for b in benches if _is_stub(b))
    out.append(f"**Code wiring (registry):** {len(benches) - n_stub}/{len(benches)} "
               f"benchmark adapters implemented (real), {n_stub} stub. Cells above "
               "show only what *this* run directory exercised; un-run cells are `—`.")
    out.append("")
    out.append("**Legend** — " + " · ".join(
        f"`{k}`: {v}" for k, v in _STATUS_LEGEND.items()))
    out.append("")
    out.append("## 2. Score summary  (measured cells only)")
    out.append("")
    out.append(_score_table(primary))
    out.append("")
    out.append("### Failure-type breakdown")
    out.append(_failure_breakdown(primary))
    out.append("")
    out.append("## 3. Run comparison (diff)")
    out.append("")
    if len(runs) >= 2:
        out.append(f"Comparing **A** = `{runs[0]['dir']}` vs **B** = "
                   f"`{runs[1]['dir']}`.")
        out.append("")
        out.append(_diff_md(runs[0], runs[1]))
    else:
        out.append("_데이터 부족: 비교하려면 run 디렉토리를 2개 주세요 "
                   "(`python -m harness.report A/ B/`)._")
    out.append("")
    out.append("## 4. Licenses, data sources & scoring vs official")
    out.append("")
    out.append(_license_footer(benches))
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="harness.report",
        description="Render a markdown report from a run directory (read-only).")
    p.add_argument("run_dirs", nargs="+", type=Path,
                   help="run directory (give two for a diff report)")
    p.add_argument("--output", type=Path, default=None,
                   help="also write the markdown here "
                        "(default: <first run dir>/report.md)")
    args = p.parse_args(argv)

    for d in args.run_dirs:
        if not d.is_dir():
            print(f"error: not a directory: {d}", file=sys.stderr)
            return 2

    md = render(args.run_dirs)
    print(md)
    out_path = args.output or (args.run_dirs[0] / "report.md")
    try:
        out_path.write_text(md, encoding="utf-8")
        print(f"\n<!-- wrote {out_path} -->")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
