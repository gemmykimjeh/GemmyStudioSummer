"""ace_browsecomp — ReAct browsing agent with an ACE self-evolving playbook.

**Browsecomp-only.** Formerly registered as ``ace_react``; renamed because the
learning layer is welded to browsecomp, not generic. ``_adapt`` derives its
feedback from ``task.metadata["problem"]`` / ``["answer"]`` and an
``Exact Answer:`` regex, so on a benchmark without those keys the reference is
empty, every task reflects as "does NOT match", and the playbook learns from
noise. For shell/terminal tasks use ``ace_terminal`` instead.

This is the paper's "ReAct + ACE" composition, assembled for a *browsing*
benchmark (browsecomp) instead of AppWorld:

* **ReAct (the base agent)** — a generic Anthropic function-calling loop over the
  benchmark's generic ``Env`` tools (``web_search`` / ``web_fetch``). Identical in
  spirit to ``claude_sdk``; it is NOT the AppWorld-welded ReAct from ace-appworld.
* **ACE (the context layer)** — ace's own ``Reflector`` + ``Curator`` (reused
  *unchanged*, imported from the ``ace`` repo). After each task the agent reflects
  on its trajectory against the reference answer and the Curator grows a persistent
  **playbook**, which is injected into the next task's system prompt.

The playbook starts EMPTY (no AppWorld-style initial playbook) and is written to
disk after every task, so you can literally watch it get generated across the
browsecomp stream. Because the playbook is state carried across ``run()`` calls,
**this agent must be run with ``--concurrency 1``** (a lock serializes mutation as
a safety net, but ordering is only meaningful when sequential).

Note: the reference answer is used as the Reflector's ground-truth *feedback* to
drive playbook learning. That is legitimate for observing playbook generation /
online adaptation, but it means a run is NOT an official leaderboard score — the
benchmark's own grader (in ``score``) still produces the real 0/1 verdict.

All ace imports are lazy (inside methods) so merely importing this module during
registry autoload never requires the ace repo or its deps to be present.

Config (constructor args, filtered by the registry from CLI ``--model`` etc.):
  model            reasoning model for the browse loop (default claude-opus-4-8)
  max_steps        browse-loop tool-call budget
  api_provider     ace LLM provider for Reflector/Curator (default "anthropic")
  ace_path         path to the ace repo (default C:\\GemmyStudioSummer\\ReAct) — added to sys.path
  playbook_out     file the evolving playbook is written to after each task
  curator_frequency  run the Curator every N tasks (default 1)
  token_budget     playbook token budget passed to the Curator
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

# Per-million-token prices (USD): (input, output) — for browse-loop telemetry only.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_SYSTEM_PROMPT = (
    "You are a meticulous research agent. Use the available tools to gather "
    "evidence before answering; do not answer from memory. When done, stop "
    "calling tools and give your final answer in exactly the format the task "
    "requests."
)

_BULLET_ID_RE = re.compile(r"\[([a-z]{3,}-\d{5})\]")
_EXACT_ANSWER_RE = re.compile(r"Exact Answer:\s*(.+)", re.IGNORECASE)


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


@register_agent("ace_browsecomp")
class ACEBrowseCompAgent(Agent):
    """ReAct browse loop + ace Reflector/Curator evolving a shared playbook."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5",
        max_steps: int = 30,
        max_tokens: int = 4096,
        api_provider: str | None = None,
        ace_path: str = r"C:\GemmyStudioSummer\ReAct",
        playbook_out: str = "ace_playbook_browsecomp.txt",
        curator_frequency: int = 1,
        token_budget: int = 80000,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.api_provider = api_provider or os.environ.get("ACE_API_PROVIDER", "anthropic")
        self.ace_path = ace_path
        self.playbook_out = playbook_out
        self.curator_frequency = curator_frequency
        self.token_budget = token_budget
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        # Lazily-initialised state (nothing heavy at import/registration time).
        self._ready = False
        self._lock = threading.Lock()
        self._client = None            # native anthropic client (browse loop)
        self._reflector = None         # ace Reflector
        self._curator = None           # ace Curator
        self._helpers = None           # ace helper fns (bound in _ensure)
        self.playbook = ""             # THE evolving context (starts empty)
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
                "ANTHROPIC_API_KEY is not set; ace_browsecomp cannot run. Put it in "
                f"{os.path.join(self.ace_path, '.env')} or the environment."
            )

        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'ace_browsecomp needs the anthropic SDK: pip install -e ".[claude]".'
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)

        # Reuse ace's Reflector/Curator (unchanged logic) + its helpers.
        try:
            from ace import Reflector, Curator  # noqa: PLC0415
            from utils import initialize_clients  # noqa: PLC0415
            from playbook_utils import (  # noqa: PLC0415
                get_next_global_id, update_bullet_counts,
                get_playbook_stats, extract_playbook_bullets,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"ace_browsecomp could not import the ace repo from {self.ace_path!r} "
                f"({exc}). Set ace_path and ensure ace's deps (openai, tiktoken, "
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

        # Empty playbook with ace's standard sections; ids start at 1.
        self.playbook = _EMPTY_PLAYBOOK
        self.next_global_id = get_next_global_id(self.playbook)

        # ace's Reflector/Curator/logger write into a log dir with a real parent.
        self._log_dir = os.path.abspath(os.path.join("ace_browsecomp_run", "detailed_llm_logs"))
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

        # 1) ReAct browse loop, guided by the current playbook.
        final_answer, reasoning_trace = self._browse(task, env, traj)

        # 2) ACE reflection + curation → grow the playbook (online, GT feedback).
        self._adapt(task, final_answer, reasoning_trace)

        traj.final_output = final_answer
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- (a) the base agent: generic ReAct tool loop -----------------------
    def _browse(self, task: Task, env: Env, traj: Trajectory) -> tuple[str | None, str]:
        client = self._client
        tools = [
            {"name": s.name, "description": s.description, "input_schema": s.parameters}
            for s in env.tools()
        ]

        system = _SYSTEM_PROMPT
        extra = env.instructions()
        if extra:
            system += f"\n\n# Task-specific guidance\n{extra}"
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

        for _ in range(self.max_steps):
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

    # -- (b) the ACE layer: reflect + curate the playbook ------------------
    def _cite_bullets(self, trace: str) -> list:
        """Ask which playbook bullets were used (the browse ReAct loop doesn't emit
        a structured bullet_ids field like ACE's Generator), feeding ACE's counting."""
        if not self.playbook.strip() or "[" not in self.playbook:
            return []
        try:
            resp = self._client.messages.create(
                model=self.model, max_tokens=200,
                messages=[{"role": "user", "content":
                    "PLAYBOOK:\n" + self.playbook + "\n\nAGENT TRANSCRIPT:\n" + trace[:5000]
                    + "\n\nList ONLY the ids of the playbook bullets that were relevant to or "
                    "applied in this transcript, as bracketed ids separated by spaces, e.g. "
                    "[err-00001] [ctx-00003]. If none, write: none"}])
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return _BULLET_ID_RE.findall(text)
        except Exception:  # noqa: BLE001
            return []

    def _adapt(self, task: Task, final_answer: str | None, reasoning_trace: str) -> None:
        H = self._helpers
        question = task.metadata.get("problem") or task.prompt
        reference = task.metadata.get("answer", "")
        predicted = (final_answer or "").strip()

        # Cheap correctness signal for the Reflector (not the official grade).
        m = _EXACT_ANSWER_RE.search(predicted)
        extracted = (m.group(1).strip() if m else predicted)
        correct = bool(reference) and reference.strip().lower() in extracted.lower()
        feedback = ("Predicted answer matches the reference." if correct
                    else "Predicted answer does NOT match the reference.")

        bullet_ids = self._cite_bullets(reasoning_trace)
        bullets_used = H["extract_playbook_bullets"](self.playbook, bullet_ids)

        step_id = f"browsecomp_s_{self._step + 1}"
        try:
            reflection, bullet_tags, _ = self._reflector.reflect(
                question=question, reasoning_trace=reasoning_trace,
                predicted_answer=predicted, ground_truth=reference,
                environment_feedback=feedback, bullets_used=bullets_used,
                use_ground_truth=True, use_json_mode=False,
                call_id=f"{step_id}_reflect", log_dir=self._log_dir,
            )
        except Exception as exc:  # noqa: BLE001 - never abort the sweep on brain error
            print(f"[ace_browsecomp] reflect failed on {task.id}: {exc}")
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
                    playbook_stats=stats, use_ground_truth=True, use_json_mode=False,
                    call_id=step_id, log_dir=self._log_dir,
                    next_global_id=self.next_global_id,
                )
                n_ops = len(ops)
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_browsecomp] curate failed on {task.id}: {exc}")
                n_ops = 0
        else:
            n_ops = 0

        # Persist + report so playbook generation is observable live.
        try:
            with open(self.playbook_out, "w", encoding="utf-8") as f:
                f.write(self.playbook)
        except Exception:  # noqa: BLE001
            pass
        print(f"[ace_browsecomp] task {self._step}: correct~={correct} "
              f"curator_ops={n_ops} playbook_chars={len(self.playbook)} "
              f"-> {self.playbook_out}")


_EMPTY_PLAYBOOK = """## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""
