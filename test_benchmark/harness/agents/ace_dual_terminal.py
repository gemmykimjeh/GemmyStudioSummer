"""ace_dual_terminal — Terminal-Bench 2.0 adapter for the FEEDBACK-LOOP ACE.

Sibling of ``ace_terminal`` (the baseline-shaped bridge: one plain playbook,
Reflector + Curator called directly). This one drives the real machine that
lives in ``ReAct_feedback_loop/ace/ace.py``, exactly as ``ace_dual_gdpval`` does
for GDPval — so a terminal_bench A/B is arm-for-arm comparable with the GDPval
A/B we already run.

Naming note: "dual" is **legacy**. The live path in the feedback-loop repo is
*not* the concrete/abstract two-playbook design (``ACE._dual_learn``, now dead
code for these adapters) — it is **ONE learned playbook + an IMMUTABLE
RULEBOOK**, the canonical feedback_loop v2 path. The name is kept only to match
``ace_dual_gdpval`` so the two arms line up on the CLI.

What the feedback-loop machine actually adds over the baseline
--------------------------------------------------------------
  ① Generator view = ``ACE._single_view()``: the LEARNED PLAYBOOK (bullets filed
     under a predefined section taxonomy) followed by the IMMUTABLE RULEBOOK
     last, for recency — must-follow ground rules that take precedence and are
     never edited. We inject a SHELL rulebook
     (``configs/rulebook_terminal.txt``) via ACE's own ``rulebook_path`` arg:
     the repo's packaged rulebook is written for document deliverables
     ("produce the actual .xlsx", "no preamble") and would tell a shell agent to
     write a document instead of running commands. Override with
     ``$ACE_TB_RULEBOOK``. The ACE repo itself is not modified.
  ② ``ACE._single_learn()`` per task: ONE reflect in ``mode="dual"`` (thinks
     concrete AND abstract, but writes into ONE playbook) → ONE tag-aware
     Curator in ``mode="single"`` → FAISS dedup → ``prune_harmful_bullets`` →
     a cross-task compress pass every k tasks.
  ③ Grader-aligned counting: the reflector's helpful/harmful tags are weighted
     by the ACTUAL score rather than ±1, with a score-independent floor so an
     explicit "harmful" tag always bites.

This adapter does NOT re-implement any of that — it instantiates the real ``ACE``
object and calls its own methods, so all learning logic stays in ``ace.py``. The
adapter only: shows the Generator view to the shell loop, drives the tools,
produces the correctness signal, and calls ``ACE._single_learn``.

Why the ACE Generator is bypassed
---------------------------------
``ACE.generator.generate()`` is single-shot: one prompt in, one deliverable out.
That fits GDPval (write a document) but not a shell task, which needs a
multi-turn tool loop. So the tool loop here is ours (identical in shape to
``ace_terminal``'s, built from ``env.tools()`` / ``env.call_tool()``), while the
**learning** half is the feedback-loop repo's, unchanged. The Generator's
``CITED:`` convention is preserved: the model is asked to end with the playbook
bullet ids it used, which feeds ACE's helpful/harmful counting.

The correctness signal
----------------------
GDPval has a rubric grader; terminal_bench has the task's own test suite. We use
the OFFICIAL verifier via ``_tb_verifier.shadow_reward`` — ``docker commit`` the
container, run ``tests/test.sh`` on the copy, read ``reward.txt``. The scored
container is left pristine, so ``TerminalBench.score()`` still reports the real
official verdict; the shadow reward is learning feedback only. Failing test
names from the verifier's CTRF json are fed to the Reflector as the analogue of
GDPval's "rubric criteria not met" list.

Because the reward is binary, ``score`` is 0.0/1.0 and ``penalty_weight`` stays
0.0 (there is no partial-credit / negative-criterion concept here).

The playbook carries across tasks → run with ``--concurrency 1``. ACE_PATH must
point at the feedback-loop repo (it defaults there). All ace imports are lazy so
registry autoload never needs the repo.
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

_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
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
    "brief summary of what you changed, then a final line listing the playbook "
    "bullet ids you actually relied on, exactly as:\n"
    "CITED: [abc-00001] [def-00002]   (write 'CITED: none' if you used none)"
)

_BULLET_ID_RE = re.compile(r"\[([a-z]{3,}-\d{5})\]")
_CITED_RE = re.compile(r"CITED:\s*(.+)", re.IGNORECASE)


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


@register_agent("ace_dual_terminal")
class ACEDualTerminalAgent(Agent):
    """Terminal-Bench 2.0 <-> feedback-loop ACE (single playbook + rulebook)."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_steps: int = 300,   # backstop only; the real bound is the task's time budget
        max_tokens: int = 8000,
        api_provider: str | None = None,
        ace_path: str | None = None,
        rulebook_path: str | None = None,
        out_prefix: str = "playbooks/ace_dual_terminal",
        feedback: str | None = None,
        curator_frequency: int = 1,
        token_budget: int = 80000,
        use_bulletpoint_analyzer: bool = True,
        dedup_threshold: float = 0.85,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.api_provider = api_provider or os.environ.get("ACE_API_PROVIDER", "anthropic")
        # Must point at the FEEDBACK-LOOP repo (not the baseline ReAct).
        self.ace_path = ace_path or os.environ.get(
            "ACE_PATH", r"C:\GemmyStudioSummer\ReAct_feedback_loop")
        # The repo's own rulebook is written for DOCUMENT deliverables and is
        # actively wrong for shell work (see configs/rulebook_terminal.txt).
        # ACE takes rulebook_path, so we override it WITHOUT touching the repo.
        self.rulebook_path = rulebook_path or os.environ.get(
            "ACE_TB_RULEBOOK",
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "configs", "rulebook_terminal.txt"))
        self.out_prefix = out_prefix
        self.feedback = (feedback or os.environ.get("ACE_TB_FEEDBACK", "shadow")).lower()
        from harness.agents._tb_verifier import FEEDBACK_MODES  # noqa: PLC0415
        if self.feedback not in FEEDBACK_MODES:
            raise ValueError(
                f"feedback must be one of {FEEDBACK_MODES} (got {self.feedback!r})")
        self.curator_frequency = curator_frequency
        self.token_budget = token_budget
        self.use_bulletpoint_analyzer = use_bulletpoint_analyzer
        self.dedup_threshold = dedup_threshold
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        self._ready = False
        self._lock = threading.Lock()
        self._client = None          # native anthropic client (shell loop)
        self._ace = None             # the real feedback-loop ACE orchestrator
        self._step = 0
        self._log_dir = None

    # -- one-time setup: instantiate the real feedback-loop ACE ------------
    def _ensure(self) -> None:
        if self._ready:
            return
        if self.ace_path and self.ace_path not in sys.path:
            sys.path.insert(0, self.ace_path)
        try:
            from dotenv import load_dotenv  # noqa: PLC0415
            load_dotenv(os.path.join(self.ace_path, ".env"))
            load_dotenv()
        except Exception:  # noqa: BLE001
            pass

        if not self._api_key:
            self._api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set; ace_dual_terminal cannot run. Put it "
                f"in {os.path.join(self.ace_path, '.env')} or the environment.")

        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'ace_dual_terminal needs the anthropic SDK: pip install -e ".[claude]".'
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)

        try:
            from ace import ACE  # noqa: PLC0415 - the feedback-loop orchestrator
            from playbook_utils import get_next_global_id  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"ace_dual_terminal could not import the feedback-loop ace repo from "
                f"{self.ace_path!r} ({exc}). Set ACE_PATH to ReAct_feedback_loop.") from exc

        # The real ACE (all agents on the same model). The single playbook starts
        # from ACE's skeleton; the immutable rulebook loads from ace/rulebook.txt.
        self._ace = ACE(
            api_provider=self.api_provider,
            generator_model=self.model,
            reflector_model=self.model,
            curator_model=self.model,
            max_tokens=self.max_tokens,
            use_bulletpoint_analyzer=self.use_bulletpoint_analyzer,
            bulletpoint_analyzer_threshold=self.dedup_threshold,
            rulebook_path=self.rulebook_path,   # shell rulebook, not the doc one
        )
        if not os.path.exists(self.rulebook_path):
            # ACE falls back to "# RULEBOOK (empty)" and prints a warning, which
            # is easy to miss in a long sweep — say it loudly instead.
            print(f"[ace_dual_terminal] WARNING: rulebook not found at "
                  f"{self.rulebook_path} — running with an EMPTY rulebook.")

        # --- Resume support -------------------------------------------------
        # A long run can die mid-way and be resumed with harness --resume, which
        # SKIPS already-scored tasks and only calls this agent for the rest. The
        # in-memory playbook must NOT restart from the skeleton on resume, or
        # cross-task learning is lost. A truly fresh run has no persisted file.
        # (The rulebook is immutable → always reloaded from the repo, never here.)
        spath = f"{self.out_prefix}_single.txt"
        try:
            if os.path.exists(spath):
                with open(spath, encoding="utf-8") as f:
                    s = f.read()
                if s.strip():
                    self._ace.single_pb = s
                    self._ace.playbook = s
                    self._ace.next_single_id = get_next_global_id(s)
                    print(f"[ace_dual_terminal] RESUMED single playbook "
                          f"({len(s)} chars, next_id={self._ace.next_single_id}). "
                          f"Rulebook is immutable → reloaded from the repo.")
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_dual_terminal] resume-reload skipped ({exc}); starting fresh")

        self._log_dir = os.path.abspath(
            os.path.join("ace_dual_terminal_run", "detailed_llm_logs"))
        os.makedirs(self._log_dir, exist_ok=True)
        self._ready = True

    # -- Agent API ---------------------------------------------------------
    def run(self, task: Task, env: Env) -> Trajectory:
        self._ensure()
        with self._lock:  # shared playbook -> run with --concurrency 1
            return self._run_locked(task, env)

    def _run_locked(self, task: Task, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()
        # STEP 0: the feedback-loop view = learned playbook + immutable rulebook.
        shown_pb = self._ace._single_view()
        # STEP 1: shell loop under that view; collect the cited bullet ids.
        summary, trace, bullet_ids = self._solve(task, env, traj, shown_pb)
        # STEP 2/3: official verifier → ACE._single_learn (ACE owns the learning).
        self._adapt(task, env, summary, trace, bullet_ids)
        traj.final_output = summary
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- STEP 1: the shell loop (generic over env.tools()) -----------------
    def _solve(self, task: Task, env: Env, traj: Trajectory,
               shown_pb: str) -> tuple[str | None, str, list[str]]:
        client = self._client
        tools = [
            {"name": s.name, "description": s.description, "input_schema": s.parameters}
            for s in env.tools()
        ]

        system = _SYSTEM_PROMPT
        extra = env.instructions()
        if extra:
            system += f"\n\n# Environment guidance\n{extra}"
        if shown_pb.strip():
            system += ("\n\n# CONTEXT — learned playbook + immutable rulebook. "
                       "Each bullet is tagged with an id like [abc-00001]; cite the "
                       "ids you rely on in the final CITED: line.\n" + shown_pb)

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

        trace = "\n".join(trace_parts)
        return last_text, trace, self._cited(last_text, shown_pb, trace)

    def _cited(self, final_text: str | None, shown_pb: str, trace: str) -> list[str]:
        """Which playbook bullets the attempt used.

        Prefers the model's own trailing ``CITED:`` line (ACE's convention, free
        — no extra call), but falls back to the SHARED focused-question helper
        when that line is missing or says none. That fallback is not optional
        polish: a weak model self-reports ``CITED: none`` even when it plainly
        applied a bullet, and citations drive ACE's helpful/harmful counting and
        ``prune_harmful_bullets``. Using the same helper as ``ace_terminal``
        keeps citation rate a property of the harness, not of the arm.
        """
        from harness.agents._tb_verifier import cite_bullets  # noqa: PLC0415
        if final_text:
            m = _CITED_RE.search(final_text)
            if m:
                # Only the CITED: line counts — ids merely quoted mid-transcript
                # are not claims of use.
                ids = _BULLET_ID_RE.findall(m.group(1))
                if ids:
                    return ids
        return cite_bullets(self._client, self.model, shown_pb, trace)

    # -- STEP 2/3: official verifier → ACE's own learning ------------------
    def _adapt(self, task: Task, env: Env, summary: str | None, trace: str,
               bullet_ids: list[str]) -> None:
        from harness.agents._tb_verifier import shadow_reward  # noqa: PLC0415
        ace = self._ace
        step_id = f"terminal_s_{self._step + 1}"

        verdict = shadow_reward(task, env, self.feedback)
        self._step += 1
        pb_before = len(ace.single_pb)

        if self._step % self.curator_frequency == 0:
            try:
                ace._single_learn(
                    question=task.prompt,
                    context=env.instructions(),
                    gen_response=trace,               # the full shell transcript
                    final_answer=(summary or "").strip(),
                    is_correct=bool(verdict.solved),
                    target="the task's own tests/test.sh must exit 0",
                    bullet_ids=bullet_ids,
                    step=self._step,
                    step_id=step_id,
                    total_samples=max(self._step, 1),
                    config_params={
                        "token_budget": self.token_budget,
                        "use_json_mode": False,   # robust extract_json_from_text path
                        # No verdict → reflect WITHOUT ground truth rather than
                        # asserting a pass/fail we do not have.
                        "no_ground_truth": verdict.reward is None,
                    },
                    log_dir=self._log_dir,
                    environment_feedback=verdict.summary(),  # + failing test names
                    score=verdict.reward,          # binary 0.0/1.0 drives weighting
                    penalty_weight=0.0,            # no negative-criterion concept here
                )
            except Exception as exc:  # noqa: BLE001 - never abort the sweep
                print(f"[ace_dual_terminal] single_learn failed on {task.id}: {exc}")

        self._persist()
        mark = "n/a" if verdict.solved is None else ("PASS" if verdict.solved else "FAIL")
        print(f"[ace_dual_terminal] task {self._step} ({task.id}): verifier={mark} "
              f"bullets_cited={len(bullet_ids)} "
              f"playbook={pb_before}->{len(ace.single_pb)}")

    # -- persist the learned playbook after each task ----------------------
    # (The rulebook is immutable → never written; it reloads from ace/rulebook.txt.)
    def _persist(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.out_prefix) or ".", exist_ok=True)
            with open(f"{self.out_prefix}_single.txt", "w", encoding="utf-8") as f:
                f.write(self._ace.single_pb)
        except Exception:  # noqa: BLE001
            pass
