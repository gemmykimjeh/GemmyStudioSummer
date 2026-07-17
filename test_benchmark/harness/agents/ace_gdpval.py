"""ace_gdpval — ACE on GDPval (tool-free). Thin bridge; ACE owns the learning.

Design principle: the adapter does NOT re-implement ACE. It wires GDPval into
ACE's own components and lets ACE do generation, bullet-id citation, reflection,
helpful/harmful counting, and curation:

  ① ACE **Generator** produces the deliverable AND emits `bullet_ids` (which
     playbook bullets it used) — this is ACE's citation mechanism, not a regex.
  ② The benchmark-specific correctness signal (ACE's `data_processor` role) is
     GDPval's OWN rubric grader → per-criterion feedback.
  ③ ACE **Reflector** tags the used bullets helpful/harmful; `update_bullet_counts`
     moves the counts; ACE **Curator** grows/prunes the playbook using those counts.

So the "reference → count → curate" loop runs inside ACE. The adapter only:
provides the task/context to the Generator, produces the rubric feedback, and
calls ACE's Reflector/Curator in online (predict-once-then-update) order.

Playbook starts EMPTY and carries across tasks → run with `--concurrency 1`.
All ace/gdpval imports are lazy so registry autoload never needs the ace repo.
"""

from __future__ import annotations

import json
import os
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


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICING.items():
        if model.startswith(prefix):
            return price
    return (0.0, 0.0)


@register_agent("ace_gdpval")
class ACEGDPvalAgent(Agent):
    """Thin bridge: GDPval <-> ACE's Generator/Reflector/Curator (ACE owns learning)."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_tokens: int = 8000,
        api_provider: str | None = None,
        ace_path: str | None = None,
        grader_model: str = "claude-sonnet-4-6",
        playbook_out: str = "ace_playbook_gdpval.txt",
        curator_frequency: int = 1,
        token_budget: int = 80000,
        success_threshold: float = 0.5,
        use_bulletpoint_analyzer: bool = True,
        dedup_threshold: float = 0.85,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.api_provider = api_provider or os.environ.get("ACE_API_PROVIDER", "anthropic")
        # A/B axis: baseline ACE (ReAct) vs feedback-loop (ReAct_feedback_loop).
        # Toggle with env ACE_PATH; the two arms are separate harness runs.
        self.ace_path = ace_path or os.environ.get("ACE_PATH", r"C:\GemmyStudioSummer\ReAct")
        self.grader_model = grader_model
        self.playbook_out = playbook_out
        self.curator_frequency = curator_frequency
        self.token_budget = token_budget
        self.success_threshold = success_threshold
        self.use_bulletpoint_analyzer = use_bulletpoint_analyzer
        self.dedup_threshold = dedup_threshold
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        self._ready = False
        self._lock = threading.Lock()
        self._client = None          # native anthropic client (rubric grader only)
        self._generator = None       # ACE Generator (emits bullet_ids)
        self._reflector = None       # ACE Reflector
        self._curator = None         # ACE Curator
        self._analyzer = None        # ACE BulletpointAnalyzer (grow-and-refine de-dup)
        self._extract_answer = None  # ACE final-answer extractor
        self._grade = None           # GDPval's own grader helpers
        self._helpers = None
        self.playbook = ""
        self.next_global_id = 1
        self._step = 0
        self._log_dir = None

    # -- one-time setup: wire in ACE's components --------------------------
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
                "ANTHROPIC_API_KEY is not set; ace_gdpval cannot run. Put it in "
                f"{os.path.join(self.ace_path, '.env')} or the environment.")

        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'ace_gdpval needs the anthropic SDK: pip install -e ".[claude]".'
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)  # grader only

        try:
            from ace import Generator, Reflector, Curator, BulletpointAnalyzer  # noqa: PLC0415
            from utils import initialize_clients, extract_answer  # noqa: PLC0415
            from playbook_utils import (  # noqa: PLC0415
                get_next_global_id, update_bullet_counts,
                get_playbook_stats, extract_playbook_bullets,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"ace_gdpval could not import the ace repo from {self.ace_path!r} "
                f"({exc}). Set ace_path and install ace's deps.") from exc

        from harness.benchmarks.gdpval import (  # noqa: PLC0415 - reuse official grader
            GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of, grade_with_retry,
        )
        self._grade = (GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of, grade_with_retry)

        gen_c, ref_c, cur_c = initialize_clients(self.api_provider)
        self._generator = Generator(gen_c, self.api_provider, self.model, self.max_tokens)
        self._reflector = Reflector(ref_c, self.api_provider, self.model, self.max_tokens)
        self._curator = Curator(cur_c, self.api_provider, self.model, self.max_tokens)
        self._extract_answer = extract_answer
        if self.use_bulletpoint_analyzer:
            # grow-and-refine de-dup/merge (paper §3.2); uses the curator client for LLM merges.
            self._analyzer = BulletpointAnalyzer(cur_c, self.model, self.max_tokens)
        self._helpers = {
            "update_bullet_counts": update_bullet_counts,
            "get_playbook_stats": get_playbook_stats,
            "extract_playbook_bullets": extract_playbook_bullets,
        }
        # Warm start: continue a prior run from its saved playbook instead of
        # starting empty. The playbook is the only cross-task state; restoring it
        # plus the id/step counters makes a resumed 51-100 equivalent to a
        # continuous 1-100 run. Set env ACE_PLAYBOOK_IN=<playbook file> and
        # ACE_STEP_START=<# tasks already done> when resuming.
        _pb_in = os.environ.get("ACE_PLAYBOOK_IN")
        if _pb_in and os.path.exists(_pb_in):
            with open(_pb_in, encoding="utf-8") as _f:
                self.playbook = _f.read()
            self._step = int(os.environ.get("ACE_STEP_START", "0"))
            print(f"[ace_gdpval] warm start from {_pb_in}: "
                  f"{len(self.playbook)} chars, step={self._step}")
        else:
            self.playbook = _EMPTY_PLAYBOOK
        self.next_global_id = get_next_global_id(self.playbook)
        self._log_dir = os.path.abspath(os.path.join("ace_gdpval_run", "detailed_llm_logs"))
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
        deliverable, bullet_ids = self._generate(task, env, traj)
        self._adapt(task, deliverable, bullet_ids)
        traj.final_output = deliverable
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- (a) ACE Generator produces deliverable + bullet_ids ---------------
    def _generate(self, task: Task, env: Env, traj: Trajectory) -> tuple[str, list]:
        traj.add(Step(type="user_message", text=env.observation()))
        # ACE Generator injects the playbook itself and returns which bullets it used.
        response, bullet_ids, info = self._generator.generate(
            question=env.observation(),
            playbook=self.playbook,
            context=env.instructions(),          # GDPval deliverable guidance
            use_json_mode=True,
            call_id=f"gdpval_s_{self._step + 1}_gen",
            log_dir=self._log_dir,
        )
        deliverable = (self._extract_answer(response) or "").strip()
        in_p, out_p = _price_for(self.model)
        pt = info.get("prompt_num_tokens", 0) or 0
        rt = info.get("response_num_tokens", 0) or 0
        traj.tokens += pt + rt
        traj.cost += (pt * in_p + rt * out_p) / 1_000_000
        traj.add(Step(type="assistant_message", text=deliverable))
        return deliverable, bullet_ids

    # -- (b) benchmark feedback (data_processor role) + ACE reflect/count/curate
    def _adapt(self, task: Task, deliverable: str, bullet_ids: list) -> None:
        H = self._helpers
        GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of, grade_with_retry = self._grade
        rubric = task.metadata.get("rubric") or []
        step_id = f"gdpval_s_{self._step + 1}"

        if not deliverable or not rubric:
            self._step += 1
            print(f"[ace_gdpval] task {self._step}: skipped (empty deliverable/rubric)")
            return

        # --- GDPval's OWN rubric grader = the benchmark correctness signal ---
        try:
            gprompt = GRADER_TEMPLATE.format(
                prompt=task.prompt, submission=deliverable,
                rubric=json.dumps([{"rubric_item_id": c.get("rubric_item_id"),
                                    "score": c.get("score"),
                                    "criterion": c.get("criterion"),
                                    "required": c.get("required")} for c in rubric],
                                   ensure_ascii=False))
            grades = grade_with_retry(self._client, self.grader_model, 4096,
                                      gprompt, len(rubric))
            rs = _score_rubric(rubric, grades)
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_gdpval] grader failed on {task.id}: {exc}")
            self._step += 1
            return

        missed = [c for c in rubric
                  if not grades.get(str(c.get("rubric_item_id")), False)]
        fb = [f"Rubric score {rs['score']:.2f} ({rs['n_met']}/{rs['n_criteria']} met; "
              f"required_ok={rs['required_ok']})."]
        if missed:
            fb.append("Criteria NOT met:")
            for c in missed[:20]:
                fb.append(f"- ({c.get('score')} pts)"
                          f"{' [REQUIRED]' if c.get('required') else ''} {c.get('criterion')}")
        environment_feedback = "\n".join(fb)
        ground_truth = "The deliverable is graded against these criteria:\n" + "\n".join(
            f"- ({c.get('score')} pts{', REQUIRED' if c.get('required') else ''}) "
            f"{c.get('criterion')}" for c in rubric)
        correct = rs["score"] >= self.success_threshold and rs["required_ok"]

        # --- ACE Reflector: tag the bullets the Generator actually cited ---
        bullets_used = H["extract_playbook_bullets"](self.playbook, bullet_ids)
        try:
            reflection, bullet_tags, _ = self._reflector.reflect(
                question=task.prompt, reasoning_trace=deliverable[:6000],
                predicted_answer=deliverable[:6000], ground_truth=ground_truth,
                environment_feedback=environment_feedback, bullets_used=bullets_used,
                use_ground_truth=True, use_json_mode=False,  # robust bullet_tags substring extraction
                call_id=f"{step_id}_reflect", log_dir=self._log_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_gdpval] reflect failed on {task.id}: {exc}")
            self._step += 1
            return

        # --- ACE counting: move helpful/harmful on the cited bullets ---
        if bullet_tags:
            self.playbook = H["update_bullet_counts"](self.playbook, bullet_tags)

        self._step += 1
        n_ops = 0
        if self._step % self.curator_frequency == 0:
            try:
                stats = H["get_playbook_stats"](self.playbook)
                self.playbook, self.next_global_id, ops, _ = self._curator.curate(
                    current_playbook=self.playbook, recent_reflection=reflection,
                    question_context=task.prompt[:2000], current_step=self._step,
                    total_samples=max(self._step, 1), token_budget=self.token_budget,
                    playbook_stats=stats, use_ground_truth=True, use_json_mode=True,
                    call_id=step_id, log_dir=self._log_dir,
                    next_global_id=self.next_global_id)
                n_ops = len(ops)
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_gdpval] curate failed on {task.id}: {exc}")

        # grow-and-refine (paper §3.2): de-duplicate / merge semantically similar
        # bullets after each update. Runs proactively (online, after each delta).
        chars_before = len(self.playbook)
        if self._analyzer is not None:
            try:
                self.playbook = self._analyzer.analyze(
                    playbook=self.playbook, threshold=self.dedup_threshold, merge=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_gdpval] analyzer failed on {task.id}: {exc}")

        try:
            with open(self.playbook_out, "w", encoding="utf-8") as f:
                f.write(self.playbook)
        except Exception:  # noqa: BLE001
            pass
        print(f"[ace_gdpval] task {self._step}: rubric={rs['score']:.2f} pass~={correct} "
              f"bullets_cited={len(bullet_ids)} tags={len(bullet_tags)} "
              f"curator_ops={n_ops} playbook_chars={chars_before}->{len(self.playbook)} (dedup)")


_EMPTY_PLAYBOOK = """## STRATEGIES & INSIGHTS

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## PROBLEM-SOLVING HEURISTICS

## CONTEXT CLUES & INDICATORS

## OTHERS"""
