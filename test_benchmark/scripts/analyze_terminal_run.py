"""Per-task cause analysis for a terminal_bench run — why each task scored 1 or 0.

Terminal-Bench gives a bare 1/0 per task, which says nothing about *why*. This
reconstructs each attempt and attributes the outcome, so a run tells you what to
fix: the agent's reasoning, the tool surface, the step budget, or the harness.

Sources (nothing extra needs to be recorded during the run):
  * ``runs/<id>/terminal_bench__<agent>/results.jsonl`` — official score, the
    verifier's per-test counts, command count, wall time, harness errors.
  * ``<agent>_run/detailed_llm_logs/reflector_*.json`` — the FULL shell
    transcript (every command + its combined output + exit status) plus the
    environment feedback that names the failing tests. The runner does not
    persist trajectories, so these logs are the only record of what the agent
    actually did; they are matched back to tasks by prompt text, not by index,
    because a task that errors before the ACE step writes no log and would
    otherwise shift every later mapping.

Two layers of attribution:

  **Mechanical** (always run, free, deterministic) — signals that indicate a
  HARNESS/TOOL limitation rather than a reasoning failure. These are the ones
  worth acting on, because no amount of prompting fixes them:
    - ``no_commands``       agent never ran anything (prompt/loop problem)
    - ``step_budget``       hit --max-steps: it was still working when cut off
    - ``bash_timeout``      commands killed at the 120s per-call cap
    - ``output_truncated``  tool output clipped at 4000 chars — the agent was
                            shown an incomplete picture and may have acted on it
    - ``high_error_rate``   >40% of commands exited non-zero (flailing)
    - ``no_verifier``       the shadow verifier produced no reward, so ACE
                            learned from this task with no ground truth
    - ``setup_error``       image pull / container start failed (not the agent)

  **Qualitative** (``--llm``) — one focused call per task over the transcript,
  classifying the root cause and naming the single highest-value fix. Costs one
  request per task; routed through whatever ANTHROPIC_BASE_URL points at, so it
  can run on the same rotation proxy as the sweep.

Usage:
    python scripts/analyze_terminal_run.py runs/tb2_baseline_40
    python scripts/analyze_terminal_run.py runs/tb2_baseline_40 --llm
    python scripts/analyze_terminal_run.py runs/tb2_baseline_40 --llm --md report.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

_TOOL_CALL_RE = re.compile(r"\[bash\(\{")
_EXIT_RE = re.compile(r"\[exit (\d+)\]")
_TIMEOUT_MARK = "[command timed out after 120s]"
_TRUNC_MARK = "...[truncated]"
_FAILED_TESTS_RE = re.compile(r"Tests NOT passed:\n((?:- .*\n?)+)")


# ----------------------------------------------------------------- loading
def load_results(run_dir: Path) -> tuple[list[dict], str]:
    """Results + the agent name, from whichever terminal_bench__* dir exists."""
    cands = sorted(run_dir.glob("terminal_bench__*"))
    if not cands:
        sys.exit(f"no terminal_bench__* results dir under {run_dir}")
    d = cands[0]
    agent = d.name.split("terminal_bench__", 1)[1]
    f = d / "results.jsonl"
    if not f.exists():
        sys.exit(f"{f} not found")
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows, agent


def load_reflector_logs(agent: str) -> list[dict]:
    """Every reflector call, newest last. Each holds one task's transcript."""
    d = Path(f"{agent}_run") / "detailed_llm_logs"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("reflector_*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        p = rec.get("prompt")
        out.append({"file": f.name,
                    "prompt": p if isinstance(p, str) else json.dumps(p)})
    return out


def match_log_to_task(logs: list[dict], task_prompt: str) -> dict | None:
    """Match by prompt text, not by index.

    A task that fails in setup never reaches the ACE step, so it writes no
    reflector log; index-based pairing would silently shift every later task's
    analysis onto the wrong transcript.
    """
    probe = " ".join(task_prompt.split())[:120]
    if not probe:
        return None
    for rec in logs:
        if probe in " ".join(rec["prompt"].split()):
            return rec
    return None


# ------------------------------------------------------------- mechanical
def mechanical_signals(transcript: str, result: dict, max_steps: int) -> list[str]:
    flags = []
    n_cmds = result.get("metrics", {}).get("num_commands")
    if result.get("error"):
        flags.append("setup_error")
    if n_cmds == 0:
        flags.append("no_commands")
    elif n_cmds and n_cmds >= max_steps:
        flags.append("step_budget")
    if not transcript:
        return flags
    if _TIMEOUT_MARK in transcript:
        flags.append(f"bash_timeout×{transcript.count(_TIMEOUT_MARK)}")
    if _TRUNC_MARK in transcript:
        flags.append(f"output_truncated×{transcript.count(_TRUNC_MARK)}")
    calls = len(_TOOL_CALL_RE.findall(transcript))
    errs = len(_EXIT_RE.findall(transcript))
    if calls and errs / calls > 0.4:
        flags.append(f"high_error_rate({errs}/{calls})")
    if "No verifier result is available" in transcript:
        flags.append("no_verifier")
    return flags


def failing_tests(transcript: str) -> list[str]:
    m = _FAILED_TESTS_RE.search(transcript or "")
    if not m:
        return []
    return [l.lstrip("- ").strip() for l in m.group(1).splitlines() if l.strip()]


# -------------------------------------------------------------- llm layer
_LLM_PROMPT = """You are analysing one attempt at a Terminal-Bench 2.0 shell task.

The agent had exactly ONE tool: `bash` (each call runs a command in a Linux
container and returns combined stdout/stderr; 120s per call; output clipped at
4000 chars). It scored {score} ({verdict}).

TASK INSTRUCTION:
{instruction}

WHAT THE AGENT DID (full transcript of commands and their output):
{transcript}

{tests_block}
Answer STRICTLY as JSON with these keys:
  "root_cause": one short sentence — the single decisive reason for this outcome.
  "category": exactly one of
      "correct"            solved it properly
      "lucky"              passed, but the transcript shows it did not verify or
                           only accidentally satisfied the tests
      "reasoning"          wrong approach / misread the task / bad plan
      "knowledge"          didn't know the tool, API, format, or domain fact
      "verification"       did the work but never checked, and shipped it broken
      "tool_limit"         the single-bash-tool surface, the 120s cap, or the
                           4000-char output clip actually blocked it
      "step_budget"        ran out of steps while still making progress
      "environment"        missing dependency, network, or broken image
      "harness"            our adapter/verifier misbehaved, not the agent
  "evidence": the specific command or output line that shows it (quote it).
  "fix": the single highest-value change, and say whether it targets the
         PROMPT, the TOOLS, the BUDGET, or the PLAYBOOK.
Output only the JSON object."""


def llm_analyse(client, model: str, task, result: dict, transcript: str,
                tests: list[str]) -> dict:
    tests_block = ("TESTS THAT FAILED:\n" + "\n".join(f"- {t}" for t in tests) + "\n"
                   if tests else "")
    prompt = _LLM_PROMPT.format(
        score=result.get("score"),
        verdict="PASSED" if result.get("success") else "FAILED",
        instruction=(task.prompt or "")[:2500],
        transcript=(transcript or "(no transcript recorded)")[:14000],
        tests_block=tests_block)
    try:
        r = client.messages.create(model=model, max_tokens=700,
                                   messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {"root_cause": text[:200],
                                                 "category": "unparsed"}
    except Exception as exc:  # noqa: BLE001
        return {"root_cause": f"(analysis call failed: {exc})", "category": "unparsed"}


# ------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--llm", action="store_true",
                    help="add per-task LLM root-cause classification (1 call/task)")
    ap.add_argument("--model", default=os.environ.get("ANALYZE_MODEL",
                                                      "gemini-3.1-flash-lite"))
    ap.add_argument("--max-steps", type=int, default=50,
                    help="the run's step budget, to detect exhaustion")
    ap.add_argument("--md", help="also write a markdown report here")
    ap.add_argument("--json", dest="json_out", help="also write the raw analysis JSON")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from harness import registry  # noqa: PLC0415
    registry.load_builtins()

    run_dir = Path(args.run_dir)
    results, agent = load_results(run_dir)
    logs = load_reflector_logs(agent)
    tasks = {t.id: t for t in registry.get_benchmark("terminal_bench").load_tasks()}

    client = None
    if args.llm:
        import anthropic  # noqa: PLC0415
        client = anthropic.Anthropic()

    rows = []
    for res in results:
        tid = res["task_id"]
        task = tasks.get(tid)
        rec = match_log_to_task(logs, task.prompt) if task else None
        transcript = rec["prompt"] if rec else ""
        row = {
            "task_id": tid,
            "difficulty": res.get("metrics", {}).get("difficulty"),
            "success": res.get("success"),
            "score": res.get("score"),
            "num_commands": res.get("metrics", {}).get("num_commands"),
            "tests": f"{res.get('metrics', {}).get('tests_passed')}/"
                     f"{res.get('metrics', {}).get('tests_total')}",
            "wall_time": round(res.get("wall_time") or 0, 1),
            "harness_error": (res.get("error") or "").splitlines()[0] if res.get("error") else None,
            "failing_tests": failing_tests(transcript),
            "signals": mechanical_signals(transcript, res, args.max_steps),
            "transcript_found": bool(rec),
        }
        if client:
            row["analysis"] = llm_analyse(client, args.model, task, res,
                                          transcript, row["failing_tests"])
            print(f"  analysed {tid}", flush=True)
        rows.append(row)

    # ---- console summary ------------------------------------------------
    n = len(rows)
    n_pass = sum(1 for r in rows if r["success"])
    print(f"\n=== {run_dir.name} · {agent} · {n_pass}/{n} passed "
          f"({n_pass / n:.0%})" if n else "no results")
    by_diff = Counter((r["difficulty"], bool(r["success"])) for r in rows)
    print("\nby difficulty:")
    for d in ("easy", "medium", "hard"):
        p, f = by_diff[(d, True)], by_diff[(d, False)]
        if p + f:
            print(f"  {d:<7} {p}/{p + f} passed")

    sig = Counter(s.split("×")[0].split("(")[0] for r in rows for s in r["signals"])
    if sig:
        print("\nmechanical signals (harness/tool limits — these are actionable):")
        for k, v in sig.most_common():
            print(f"  {k:<20} {v} task(s)")

    if client:
        cat = Counter(r["analysis"].get("category") for r in rows)
        print("\nroot-cause categories:")
        for k, v in cat.most_common():
            print(f"  {k:<14} {v}")

    missing = [r["task_id"] for r in rows if not r["transcript_found"]]
    if missing:
        print(f"\nno transcript for {len(missing)} task(s) "
              f"(failed before the ACE step): {', '.join(missing[:8])}")

    print("\nper task:")
    for r in sorted(rows, key=lambda x: (x["success"], x["difficulty"] or "")):
        mark = "PASS" if r["success"] else "FAIL"
        line = (f"  [{mark}] {r['task_id']:<34} {r['difficulty'] or '?':<7} "
                f"tests={r['tests']:<7} cmds={r['num_commands']}")
        if r["signals"]:
            line += f"  ⚑ {', '.join(r['signals'])}"
        print(line)
        # A near-miss (all but one test) is a very different problem from a
        # total miss, and the binary reward hides that — always name the gap.
        if r["failing_tests"]:
            print(f"         failed: {', '.join(t.split('::')[-1] for t in r['failing_tests'][:6])}")
        if r.get("analysis"):
            a = r["analysis"]
            print(f"         cause[{a.get('category')}]: {a.get('root_cause')}")
            print(f"         fix: {a.get('fix')}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    if args.md:
        _write_md(Path(args.md), run_dir.name, agent, rows)
        print(f"wrote {args.md}")
    return 0


def _write_md(path: Path, run_id: str, agent: str, rows: list[dict]) -> None:
    n, n_pass = len(rows), sum(1 for r in rows if r["success"])
    L = [f"# terminal_bench cause analysis — {run_id} ({agent})", "",
         f"**{n_pass}/{n} passed ({n_pass / n:.0%})**" if n else "no results", "",
         "| task | diff | verdict | tests | cmds | signals | root cause | fix |",
         "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["success"], x["difficulty"] or "")):
        a = r.get("analysis") or {}
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["task_id"], r["difficulty"] or "?",
            "PASS" if r["success"] else "FAIL", r["tests"], r["num_commands"],
            ", ".join(r["signals"]) or "—",
            (a.get("category", "") + ": " + a.get("root_cause", "")).strip(": ") or "—",
            a.get("fix", "—")))
    sig = Counter(s.split("×")[0].split("(")[0] for r in rows for s in r["signals"])
    if sig:
        L += ["", "## Harness / tool limits (actionable without touching the agent)", ""]
        L += [f"- `{k}` — {v} task(s)" for k, v in sig.most_common()]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
