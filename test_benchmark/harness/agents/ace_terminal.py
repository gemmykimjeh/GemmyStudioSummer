"""ace_terminal — shell/terminal agent with an ACE self-evolving playbook.

The Terminal-Bench 2.0 counterpart of ``ace_browsecomp`` (browsecomp) and
``ace_tau`` (tau-bench). Same two-layer composition, re-grounded on shell work:

* **ReAct (the base agent)** — a generic Anthropic function-calling loop over the
  benchmark's ``Env`` tools. ``terminal_bench`` exposes exactly one tool,
  ``bash``, so the agent solves the task by running shell commands in the task
  container. Nothing here is hardcoded to that name: the loop is built from
  ``env.tools()`` / ``env.call_tool()``, so any tool-shaped Env works.
* **ACE (the context layer)** — ace's own ``Reflector`` + ``Curator``, reused
  *unchanged* from the ace repo. After each task the agent reflects on its
  trajectory against a real pass/fail signal and the Curator grows a persistent
  **playbook**, injected into the next task's system prompt.

Why this is a new agent rather than a reuse
-------------------------------------------
``ace_browsecomp``'s learning layer reads ``task.metadata["problem"]`` /
``["answer"]`` and matches an ``Exact Answer:`` regex — keys terminal_bench does
not have, which would make every task reflect as "does NOT match" and teach the
playbook noise. ``ace_gdpval`` / ``ace_dual_gdpval`` never call ``env.call_tool``
at all (single-shot document generation), so they would leave the container
untouched and score 0. Neither was adaptable; this module is the shell-native
version. The existing agents are left alone.

The feedback signal (the hard part)
-----------------------------------
``TerminalBenchEnv`` has no ``reward()``. The verdict is produced by
``TerminalBench.score()``, which copies the task's ``tests/`` into the container
and runs ``test.sh`` — a **destructive, after-the-fact** operation. So the ACE
layer cannot simply call ``env.reward()`` mid-``run()`` the way ``ace_tau`` does.

We solve it without touching the runner or the benchmark: ``_tb_verifier``
``docker commit``s the task container to a throwaway image, starts a **shadow
container** from it, and runs the official verifier *there*. The scored container
is never mutated, so ``TerminalBench.score()`` still produces a pristine official
verdict. The shadow reward is used **only** as ACE's learning feedback, never as
the reported score.

Cost of that choice: the verifier runs twice per task (ours + the real one), and
``test.sh`` does ``apt-get`` + fetches uv/pytest each time. Set
``feedback="none"`` to skip it and reflect on the trajectory alone (weaker
signal, no extra cost). ``feedback="inplace"`` runs the verifier directly in the
scored container — cheapest and gives ACE the real reward, but it **contaminates
the official score** and is offered only for debugging.

All ace imports are lazy (inside methods) so importing this module during
registry autoload never requires the ace repo or its deps.

Config (constructor args; only ``model`` / ``max_steps`` reach it from the CLI —
the rest are env vars or programmatic):
  model            reasoning model for the shell loop (default claude-sonnet-5)
  max_steps        runaway backstop; the real bound is the task's official
                   [agent].timeout_sec from task.toml
  api_provider     ace LLM provider for Reflector/Curator ($ACE_API_PROVIDER)
  ace_path         ace repo on sys.path ($ACE_PATH; default ReAct = baseline;
                   point it at ReAct_feedback_loop for the FBL arm)
  playbook_out     file the evolving playbook is written to after each task
                   ($ACE_PLAYBOOK_OUT)
  playbook_in      optional warm-start playbook to load ($ACE_PLAYBOOK_IN)
  feedback         "shadow" (default) | "none" | "inplace" ($ACE_TB_FEEDBACK)
  curator_frequency  run the Curator every N tasks
  token_budget     playbook token budget passed to the Curator

Because the playbook is state carried across ``run()`` calls, run the sweep with
``--concurrency 1`` (a lock serializes mutation as a safety net, but ordering is
only meaningful when sequential).
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

# Per-million-token prices (USD): (input, output) — shell-loop telemetry only.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_SYSTEM_PROMPT = (
    "You are an expert systems engineer working inside a Linux container. Solve "
    "the task by running shell commands with the available tool.\n"
    "Work empirically: inspect the environment before you change it (ls, cat, "
    "which, --help), then act, then VERIFY what you did actually took effect. Do "
    "not assume a command succeeded — check exit status and re-read the result.\n"
    "Prefer small, checkable steps over one long chained command, so a failure "
    "tells you which part broke. If a command errors, read the error text before "
    "retrying; do not repeat an identical failing command.\n"
    "Write every artifact the task asks for, at exactly the path it specifies. "
    "When the task is fully done and verified, stop calling tools and give a "
    "brief summary of what you changed."
)

_BULLET_ID_RE = re.compile(r"\[([a-z]{3,}-\d{5})\]")


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


@register_agent("ace_terminal")
class ACETerminalAgent(Agent):
    """Shell ReAct loop + ace Reflector/Curator evolving a shared playbook."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_steps: int = 300,   # backstop only; the real bound is the task's time budget
        max_tokens: int = 8192,
        api_provider: str | None = None,
        ace_path: str | None = None,
        playbook_out: str | None = None,
        playbook_in: str | None = None,
        feedback: str | None = None,
        curator_frequency: int = 1,
        token_budget: int = 80000,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.api_provider = api_provider or os.environ.get(
            "ACE_API_PROVIDER", "anthropic")
        # Default to the BASELINE ace repo; ACE_PATH switches the A/B arm.
        self.ace_path = ace_path or os.environ.get(
            "ACE_PATH", r"C:\GemmyStudioSummer\ReAct")
        self.playbook_out = playbook_out or os.environ.get(
            "ACE_PLAYBOOK_OUT", "playbooks/ace_playbook_terminal.txt")
        self.playbook_in = playbook_in or os.environ.get("ACE_PLAYBOOK_IN")
        self.feedback = (feedback or os.environ.get("ACE_TB_FEEDBACK", "shadow")).lower()
        if self.feedback not in {"shadow", "none", "inplace"}:
            raise ValueError(
                f"feedback must be 'shadow', 'none' or 'inplace' (got {self.feedback!r})")
        self.curator_frequency = curator_frequency
        self.token_budget = token_budget
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        # Lazily-initialised state (nothing heavy at import/registration time).
        self._ready = False
        self._lock = threading.Lock()
        self._client = None            # native anthropic client (shell loop)
        self._reflector = None         # ace Reflector
        self._curator = None           # ace Curator
        self._helpers = None           # ace helper fns (bound in _ensure)
        self.playbook = ""             # THE evolving context
        self.next_global_id = 1
        self._step = 0
        self._log_dir = None

    # -- one-time setup: wire in the ace brain -----------------------------
    def _ensure(self) -> None:
        if self._ready:
            return

        # Make the ace repo importable (its modules are top-level: ace, llm,
        # utils, playbook_utils, logger). Load its .env for ANTHROPIC_API_KEY.
        if self.ace_path and self.ace_path not in sys.path:
            sys.path.insert(0, self.ace_path)
        try:
            from dotenv import load_dotenv  # noqa: PLC0415
            load_dotenv(os.path.join(self.ace_path, ".env"))
            load_dotenv()  # also honour a .env in the current working dir
        except Exception:  # noqa: BLE001 - dotenv is best-effort
            pass

        if not self._api_key:
            self._api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; ace_terminal cannot run. Put it in "
                f"{os.path.join(self.ace_path, '.env')} or the environment."
            )

        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'ace_terminal needs the anthropic SDK: pip install -e ".[claude]".'
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)

        # Reuse ace's Reflector/Curator (unchanged logic) + its helpers. Both the
        # baseline repo and ReAct_feedback_loop export these with a compatible
        # signature (the FBL one adds keyword args that all have defaults), so
        # ACE_PATH switches arms without touching this module.
        try:
            from ace import Reflector, Curator  # noqa: PLC0415
            from utils import initialize_clients  # noqa: PLC0415
            from playbook_utils import (  # noqa: PLC0415
                get_next_global_id, update_bullet_counts,
                get_playbook_stats, extract_playbook_bullets,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"ace_terminal could not import the ace repo from {self.ace_path!r} "
                f"({exc}). Set ACE_PATH and ensure ace's deps (openai, tiktoken, "
                "numpy, python-dotenv) are installed in this environment."
            ) from exc

        gen_c, ref_c, cur_c = initialize_clients(self.api_provider)
        self._reflector = Reflector(ref_c, self.api_provider, self.model, self.max_tokens)
        self._curator = Curator(cur_c, self.api_provider, self.model, self.max_tokens)
        self._helpers = {
            "update_bullet_counts": update_bullet_counts,
            "get_playbook_stats": get_playbook_stats,
            "extract_playbook_bullets": extract_playbook_bullets,
        }

        # Warm-start from a previous sweep if asked, else ace's empty sections.
        self.playbook = _EMPTY_PLAYBOOK
        if self.playbook_in:
            try:
                self.playbook = Path(self.playbook_in).read_text(encoding="utf-8")
                print(f"[ace_terminal] warm-started playbook from {self.playbook_in} "
                      f"({len(self.playbook)} chars)")
            except OSError as exc:
                print(f"[ace_terminal] could not read playbook_in "
                      f"{self.playbook_in!r} ({exc}); starting empty")
        self.next_global_id = get_next_global_id(self.playbook)

        # ace's Reflector/Curator/logger write into a log dir with a real parent.
        self._log_dir = os.path.abspath(
            os.path.join("ace_terminal_run", "detailed_llm_logs"))
        os.makedirs(self._log_dir, exist_ok=True)
        self._ready = True

    # -- Agent API ---------------------------------------------------------
    def run(self, task: Task, env: Env) -> Trajectory:
        self._ensure()
        # Serialize: the playbook is shared state mutated across tasks. Run the
        # sweep with --concurrency 1; this lock is a corruption safety-net.
        with self._lock:
            return self._run_locked(task, env)

    def _run_locked(self, task: Task, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()

        # 1) Shell ReAct loop, guided by the current playbook.
        summary, trace = self._solve(task, env, traj)

        # 2) Real pass/fail from the official verifier, on a COPY of the
        #    container so the scored one stays pristine (see module docstring).
        verdict = self._reward_signal(task, env)

        # 3) ACE reflection + curation → grow the playbook.
        self._adapt(task, summary, trace, verdict)

        traj.final_output = summary
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- (a) the base agent: generic tool loop -----------------------------
    def _solve(self, task: Task, env: Env, traj: Trajectory) -> tuple[str | None, str]:
        client = self._client
        tools = [
            {"name": s.name, "description": s.description, "input_schema": s.parameters}
            for s in env.tools()
        ]

        system = _SYSTEM_PROMPT
        extra = env.instructions()
        if extra:
            system += f"\n\n# Environment guidance\n{extra}"
        if self.playbook.strip():
            system += (
                "\n\n# PLAYBOOK — lessons accumulated from previous tasks. "
                "Apply the relevant ones; each bullet is tagged with an id like "
                "[abc-00001], cite the ids you rely on.\n" + self.playbook
            )

        traj.add(Step(type="user_message", text=env.observation()))
        messages = [{"role": "user", "content": env.observation()}]
        in_price, out_price = _price_for(self.model)
        trace_parts: list[str] = []
        last_text: str | None = None

        # Terminal-Bench bounds an attempt by TIME, per task (task.toml
        # [agent].timeout_sec), not by a step count. Enforce that budget here;
        # max_steps is only a runaway-loop backstop.
        budget = float(task.metadata.get("agent_timeout_sec") or 0) or None
        t0 = time.perf_counter()

        for _ in range(self.max_steps):
            if budget and time.perf_counter() - t0 > budget:
                trace_parts.append(f"[agent budget of {budget:g}s exhausted]")
                break
            response = client.messages.create(
                model=self.model, max_tokens=self.max_tokens,
                system=system, tools=tools, messages=messages,
            )
            usage = response.usage
            traj.tokens += (usage.input_tokens or 0) + (usage.output_tokens or 0)
            traj.cost += ((usage.input_tokens or 0) * in_price
                          + (usage.output_tokens or 0) * out_price) / 1_000_000

            tool_uses = []
            turn_text: list[str] = []
            for block in response.content:
                if block.type == "text":
                    turn_text.append(block.text)
                    trace_parts.append(block.text)
                    traj.add(Step(type="assistant_message", text=block.text))
                elif block.type == "tool_use":
                    tool_uses.append(block)
            if turn_text:
                last_text = "\n".join(turn_text)

            if response.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tu in tool_uses:
                result = env.call_tool(tu.name, tu.input or {})
                traj.add(Step(type="tool_call", name=tu.name,
                              arguments=dict(tu.input or {}), output=result.output,
                              is_error=result.is_error))
                trace_parts.append(f"[{tu.name}({tu.input})] -> {result.output}")
                tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                     "content": result.output, "is_error": result.is_error})
            messages.append({"role": "user", "content": tool_results})

        return last_text, "\n".join(trace_parts)

    # -- (b) the learning signal: official verifier, non-destructively -----
    def _reward_signal(self, task: Task, env: Env):
        """Official 1/0 reward via the shared shadow-container verifier.

        See ``_tb_verifier`` for why the container is committed first. Returns a
        ``VerifierResult`` whose ``reward`` is ``None`` when no verdict could be
        obtained — the caller then reflects without ground truth rather than
        inventing one.
        """
        from harness.agents._tb_verifier import shadow_reward  # noqa: PLC0415
        return shadow_reward(task, env, self.feedback)

    # -- (c) the ACE layer: reflect + curate the playbook ------------------
    def _cite_bullets(self, trace: str) -> list:
        """Which playbook bullets the attempt used, via the SHARED helper — the
        FBL arm calls the same one so the A/B can't be skewed by citation rate."""
        from harness.agents._tb_verifier import cite_bullets  # noqa: PLC0415
        return cite_bullets(self._client, self.model, self.playbook, trace)

    def _adapt(self, task: Task, summary: str | None, trace: str, verdict) -> None:
        H = self._helpers
        question = task.prompt
        predicted = (summary or "").strip()

        # Ground truth here is the task's own test suite, not a reference string.
        # When no reward could be obtained, VerifierResult.summary() says so and
        # we reflect WITHOUT ground truth rather than asserting a verdict we
        # don't have.
        reward, solved, feedback = verdict.reward, verdict.solved, verdict.summary()

        bullet_ids = self._cite_bullets(trace)
        bullets_used = H["extract_playbook_bullets"](self.playbook, bullet_ids)

        step_id = f"terminal_s_{self._step + 1}"
        try:
            reflection, bullet_tags, _ = self._reflector.reflect(
                question=question, reasoning_trace=trace,
                predicted_answer=predicted,
                ground_truth=("the task's tests/test.sh must exit 0"
                              if reward is not None else None),
                environment_feedback=feedback, bullets_used=bullets_used,
                use_ground_truth=reward is not None, use_json_mode=False,
                call_id=f"{step_id}_reflect", log_dir=self._log_dir,
            )
        except Exception as exc:  # noqa: BLE001 - never abort the sweep on brain error
            print(f"[ace_terminal] reflect failed on {task.id}: {exc}")
            self._step += 1
            return

        if bullet_tags:
            self.playbook = H["update_bullet_counts"](self.playbook, bullet_tags)

        self._step += 1
        if self._step % self.curator_frequency == 0:
            try:
                stats = H["get_playbook_stats"](self.playbook)
                self.playbook, self.next_global_id, ops, _ = self._curator.curate(
                    current_playbook=self.playbook, recent_reflection=reflection,
                    question_context=question[:2000], current_step=self._step,
                    total_samples=max(self._step, 1), token_budget=self.token_budget,
                    playbook_stats=stats, use_ground_truth=reward is not None,
                    use_json_mode=False, call_id=step_id, log_dir=self._log_dir,
                    next_global_id=self.next_global_id,
                )
                n_ops = len(ops)
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_terminal] curate failed on {task.id}: {exc}")
                n_ops = 0
        else:
            n_ops = 0

        # Persist + report so playbook generation is observable live.
        try:
            os.makedirs(os.path.dirname(self.playbook_out) or ".", exist_ok=True)
            with open(self.playbook_out, "w", encoding="utf-8") as f:
                f.write(self.playbook)
        except Exception:  # noqa: BLE001
            pass
        mark = "n/a" if solved is None else ("PASS" if solved else "FAIL")
        print(f"[ace_terminal] task {self._step} ({task.id}): verifier={mark} "
              f"curator_ops={n_ops} playbook_chars={len(self.playbook)} "
              f"-> {self.playbook_out}")


_EMPTY_PLAYBOOK = """## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""
