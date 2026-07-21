"""Shared Terminal-Bench 2.0 learning signal for the ACE terminal agents.

Not an agent — helpers used by ``ace_terminal`` and ``ace_dual_terminal``:
``shadow_reward`` (the correctness signal) and ``cite_bullets`` (which playbook
bullets the attempt used). Both arms MUST use the same implementation of each,
or the A/B measures our plumbing instead of the feedback loop — see
``cite_bullets`` for the concrete way that bit us.

Why this exists
---------------
``TerminalBenchEnv`` has no ``reward()``. The verdict is produced by
``TerminalBench.score()``, which copies the task's ``tests/`` into the container
and runs ``test.sh`` — a **destructive, after-the-fact** operation. An ACE agent
needs that verdict *during* ``run()`` to learn from it, but grading early would
leave the official scorer looking at a contaminated container.

``shadow_reward`` resolves that without touching the runner or the benchmark:

  ``shadow``  (default) ``docker commit`` the task container to a throwaway
              image, start a **shadow container** from it, run the official
              verifier there, then delete both. The scored container is left
              byte-identical, so ``TerminalBench.score()`` still produces a
              pristine official verdict.
  ``inplace`` run the verifier directly in the scored container. Cheapest, but
              it **contaminates the official score** — debugging only.
  ``none``    skip the verifier entirely.

The reward returned here is ACE's *learning feedback* only. It is never reported
as the benchmark score — that always comes from ``TerminalBench.score()``.

Cost: ``shadow`` runs the verifier twice per task (ours + the real one), and
``test.sh`` does ``apt-get`` + fetches uv/pytest each time.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

FEEDBACK_MODES = ("shadow", "none", "inplace")

#: Learned-playbook bullet ids, e.g. [fmt-00037]. Deliberately does NOT match
#: the rulebook's [rb-fmt-01] form: the rulebook is immutable and never counted,
#: so crediting/blaming its ids would corrupt helpful/harmful.
BULLET_ID_RE = re.compile(r"\[([a-z]{3,}-\d{5})\]")


def cite_bullets(client, model: str, playbook: str, trace: str,
                 max_chars: int = 5000) -> list[str]:
    """Which playbook bullets did this attempt actually use?

    One focused LLM call over the transcript. Feeds ACE's helpful/harmful
    counting, which in turn drives the score-weighted tagging and
    ``prune_harmful_bullets`` — so if this returns nothing, a large part of the
    feedback loop silently stops running.

    **Why not just ask the agent to end with a ``CITED:`` line?** We tried, and
    on a weak model (gemini-3.1-flash-lite) it answered ``CITED: none`` even on
    a task where it demonstrably applied a playbook bullet (hit a git merge
    conflict and resolved it exactly as ``[fail-00001]`` prescribes), while this
    focused call on the same task/model cited two bullets. Self-report during a
    long tool loop is unreliable. Both arms use THIS function so the citation
    rate is a property of the shared harness, not of the arm — otherwise the A/B
    penalises whichever arm happens to self-report less.

    Returns [] (never raises) when there is nothing to cite or the call fails.
    """
    if not playbook.strip() or "[" not in playbook:
        return []
    try:
        resp = client.messages.create(
            model=model, max_tokens=200,
            messages=[{"role": "user", "content":
                "PLAYBOOK:\n" + playbook + "\n\nAGENT TRANSCRIPT:\n" + trace[:max_chars]
                + "\n\nList ONLY the ids of the playbook bullets that were relevant to or "
                "applied in this transcript, as bracketed ids separated by spaces, e.g. "
                "[err-00001] [ctx-00003]. If none, write: none"}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", None) == "text")
        return BULLET_ID_RE.findall(text)
    except Exception:  # noqa: BLE001 - citation is best-effort, never fatal
        return []


@dataclass
class VerifierResult:
    """Outcome of one verifier run. ``reward is None`` means "no verdict"."""

    reward: float | None = None
    note: str = ""
    #: Names of the individual tests that failed (from the verifier's CTRF json).
    #: The terminal-bench analogue of GDPval's "rubric criteria not met" list —
    #: a far sharper reflection signal than a bare 0.
    failed_tests: list[str] = field(default_factory=list)
    tests_total: int | None = None
    tests_passed: int | None = None

    @property
    def solved(self) -> bool | None:
        return None if self.reward is None else self.reward >= 1.0

    def summary(self) -> str:
        """Human/LLM-readable feedback line for the ACE Reflector."""
        if self.reward is None:
            return (f"No verifier result is available for this attempt ({self.note}). "
                    "Judge the attempt only from the transcript: which commands "
                    "actually confirmed their effect, and where did the agent assume "
                    "success without checking?")
        head = (f"The task's official test suite "
                f"{'PASSED' if self.solved else 'FAILED'} ({self.note}).")
        if self.tests_total:
            head += f" {self.tests_passed or 0}/{self.tests_total} tests passed."
        if self.failed_tests:
            head += ("\nTests NOT passed:\n"
                     + "\n".join(f"- {t}" for t in self.failed_tests[:20]))
        head += ("\nIdentify what in the approach made it work, stated generally "
                 "enough to transfer to other shell tasks."
                 if self.solved else
                 "\nIdentify the specific step where the attempt went wrong — a wrong "
                 "path, an unverified assumption, a missing artifact, or a misread "
                 "error — not a generic 'be more careful'.")
        return head


def _cleanup(commands, endpoint) -> None:
    from harness.benchmarks.terminal_bench import _docker  # noqa: PLC0415
    for args in commands:
        try:
            _docker(args, endpoint, timeout=120)
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            pass


def _read_failed_tests(target: str, endpoint, _docker) -> tuple[list[str], int | None, int | None]:
    """Per-test detail from the CTRF json the verifier emits. Best-effort."""
    r = _docker(["exec", target, "cat", "/logs/verifier/ctrf.json"], endpoint, timeout=60)
    if r.returncode != 0:
        return [], None, None
    try:
        results = json.loads(r.stdout).get("results", {}) or {}
    except Exception:  # noqa: BLE001
        return [], None, None
    summary = results.get("summary", {}) or {}
    failed = [t.get("name") or "<unnamed>"
              for t in (results.get("tests") or [])
              if (t.get("status") or "").lower() not in ("passed", "skipped")]
    return failed, summary.get("tests"), summary.get("passed")


def shadow_reward(task, env, mode: str = "shadow") -> VerifierResult:
    """Run the task's own ``tests/test.sh`` and return its 1/0 reward.

    Never raises for an expected failure — returns a ``VerifierResult`` with
    ``reward=None`` and a ``note`` explaining why, so the caller can reflect
    without ground truth rather than inventing a verdict.
    """
    if mode == "none":
        return VerifierResult(note="verifier not run (feedback='none')")
    if mode not in FEEDBACK_MODES:
        return VerifierResult(note=f"unknown feedback mode {mode!r}")
    try:
        from harness.benchmarks.terminal_bench import (  # noqa: PLC0415
            TerminalBenchEnv, _docker)
    except ImportError as exc:  # noqa: BLE001
        return VerifierResult(note=f"terminal_bench adapter unavailable ({exc})")
    if not isinstance(env, TerminalBenchEnv):
        # These agents are generic enough to run elsewhere; skip the
        # terminal-bench-specific reward rather than failing the task.
        return VerifierResult(note=f"env {type(env).__name__} is not a TerminalBenchEnv")

    tests_dir = Path(task.metadata.get("task_dir", "")) / "tests"
    if not (tests_dir / "test.sh").exists():
        return VerifierResult(note=f"no tests/test.sh under {tests_dir}")
    ep = env.endpoint
    timeout = float(task.metadata.get("verifier_timeout_sec", 900)) + 120

    target, cleanup = env.container, []
    if mode == "shadow":
        image = f"tb2-shadow-{uuid.uuid4().hex[:8]}"
        target = f"{image}-c"
        commit = _docker(["commit", env.container, image], ep, timeout=600)
        if commit.returncode != 0:
            return VerifierResult(
                note="docker commit failed: " + (commit.stderr or commit.stdout)[-300:])
        cleanup.append(["rmi", "-f", image])
        run = _docker(["run", "-d", "--name", target, "--entrypoint", "sleep",
                       image, "infinity"], ep, timeout=600)
        if run.returncode != 0:
            _cleanup(cleanup, ep)
            return VerifierResult(
                note="shadow container failed to start: "
                     + (run.stderr or run.stdout)[-300:])
        cleanup.insert(0, ["rm", "-f", target])

    try:
        _docker(["exec", target, "mkdir", "-p", "/tests", "/logs/verifier"], ep, timeout=60)
        cp = _docker(["cp", f"{tests_dir}/.", f"{target}:/tests/"], ep, timeout=120)
        if cp.returncode != 0:
            return VerifierResult(
                note="failed to copy tests: " + (cp.stderr or cp.stdout)[-300:])
        # Windows checkouts leave CRLF in the shell scripts, which breaks bash in
        # the Linux container ($'\r': command not found).
        _docker(["exec", target, "sh", "-c",
                 r"find /tests -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true"],
                ep, timeout=60)
        try:
            _docker(["exec", target, "bash", "/tests/test.sh"], ep, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - incl. TimeoutExpired
            return VerifierResult(note=f"verifier did not complete ({type(exc).__name__})")
        r = _docker(["exec", target, "cat", "/logs/verifier/reward.txt"], ep, timeout=60)
        if r.returncode != 0:
            return VerifierResult(note="verifier wrote no reward.txt")
        try:
            reward = float((r.stdout or "").strip())
        except ValueError:
            return VerifierResult(note=f"unparseable reward.txt: {(r.stdout or '')[:80]!r}")
        failed, total, passed = _read_failed_tests(target, ep, _docker)
        return VerifierResult(reward=reward, note=f"official verifier, {mode} mode",
                              failed_tests=failed, tests_total=total, tests_passed=passed)
    finally:
        _cleanup(cleanup, ep)
