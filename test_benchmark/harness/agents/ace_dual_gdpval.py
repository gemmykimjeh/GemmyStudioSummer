"""ace_dual_gdpval — GDPval adapter for the DUAL-PLAYBOOK ACE feedback loop.

Sibling of `ace_gdpval` (single-playbook thin bridge). This one drives the FULL
feedback_loop.md machine that lives in `ReAct_feedback_loop/ace/ace.py`:

  ① The ACE Generator reads BOTH playbooks (a labeled two-part view — concrete
     rules to apply directly + abstract principles to guide judgment). No Thompson.
  ② ACE Generator produces the deliverable AND emits `bullet_ids`.  [generator]
  ③ GDPval's OWN rubric grader = the benchmark correctness signal. [data_processor role]
  ④ ACE dual-learning per task: ONE merged Reflector call → {concrete_insight,
     abstract_insight} → Curator ×2, each playbook edited separately from its
     insight stream, seed bullets protected.               [ACE._dual_learn]

The adapter does NOT re-implement the dual loop — it instantiates the real
`ACE` object and calls its own components/methods, so all learning logic (seeds,
two curators, dedup) stays in ace.py. The adapter only: provides task/context to
the Generator, produces the rubric feedback, tags helpful/harmful on the cited
bullets, and calls `ACE._dual_learn`.

Playbooks START from the packaged seeds and carry across tasks → run with
`--concurrency 1`. Point ACE_PATH at the feedback-loop repo (default below).
All ace imports are lazy so registry autoload never needs the ace repo.
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


@register_agent("ace_dual_gdpval")
class ACEDualGDPvalAgent(Agent):
    """GDPval <-> dual-playbook ACE (both-playbook read + merged reflector + 2 curators)."""

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        max_tokens: int = 8000,
        api_provider: str | None = None,
        ace_path: str | None = None,
        grader_model: str = "claude-sonnet-4-6",
        out_prefix: str = "playbooks/ace_dual_gdpval",
        curator_frequency: int = 1,
        token_budget: int = 80000,
        success_threshold: float = 0.5,
        learn_max_score: float = 0.75,
        use_bulletpoint_analyzer: bool = True,
        dedup_threshold: float = 0.85,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.api_provider = api_provider or os.environ.get("ACE_API_PROVIDER", "anthropic")
        # Must point at the DUAL-PLAYBOOK repo (not the baseline ReAct).
        self.ace_path = ace_path or os.environ.get(
            "ACE_PATH", r"C:\GemmyStudioSummer\ReAct_feedback_loop")
        self.grader_model = grader_model
        self.out_prefix = out_prefix
        self.curator_frequency = curator_frequency
        self.token_budget = token_budget
        self.success_threshold = success_threshold
        # v6: only LEARN from tasks that left room to improve. A task scoring above
        # this is already good; distilling "lessons" from it mostly adds playbook
        # bulk and post-hoc rationalisation of what already worked. Set to 1.0 to
        # learn from every task (the v5 behaviour).
        self.learn_max_score = learn_max_score
        self.use_bulletpoint_analyzer = use_bulletpoint_analyzer
        self.dedup_threshold = dedup_threshold
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

        self._ready = False
        self._lock = threading.Lock()
        self._client = None          # native anthropic client (rubric grader only)
        self._ace = None             # the real dual-playbook ACE orchestrator
        self._extract_answer = None  # ACE final-answer extractor
        self._grade = None           # GDPval's own grader helpers
        self._pb = None              # playbook_utils helpers
        self._step = 0
        self._log_dir = None

    # -- one-time setup: instantiate the real dual-playbook ACE ------------
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
                "ANTHROPIC_API_KEY is not set; ace_dual_gdpval cannot run. Put it "
                f"in {os.path.join(self.ace_path, '.env')} or the environment.")

        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'ace_dual_gdpval needs the anthropic SDK: pip install -e ".[claude]".'
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key)  # grader only

        try:
            from ace import ACE  # noqa: PLC0415 - the dual-playbook orchestrator
            from utils import extract_answer  # noqa: PLC0415
            from playbook_utils import (  # noqa: PLC0415
                extract_playbook_bullets, update_bullet_counts, get_next_global_id,
            )
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                f"ace_dual_gdpval could not import the dual-playbook ace repo from "
                f"{self.ace_path!r} ({exc}). Set ace_path to ReAct_feedback_loop.") from exc

        from harness.benchmarks.gdpval import (  # noqa: PLC0415 - reuse official grader
            GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of, grade_with_retry,
        )
        self._grade = (GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of, grade_with_retry)

        # The real dual-playbook ACE (all agents on the same model).
        # Both playbooks auto-load from the packaged seeds.
        self._ace = ACE(
            api_provider=self.api_provider,
            generator_model=self.model,
            reflector_model=self.model,
            curator_model=self.model,
            max_tokens=self.max_tokens,
            use_bulletpoint_analyzer=self.use_bulletpoint_analyzer,
            bulletpoint_analyzer_threshold=self.dedup_threshold,
        )
        # Ablation toggle: ACE_SHOW_PLAYBOOK=0 hides the LEARNED playbook from the
        # generator (the immutable rulebook still shows, learning still runs), to
        # measure what the playbook contributes on top of the rulebook.
        self._ace.show_playbook = os.environ.get("ACE_SHOW_PLAYBOOK", "1") != "0"
        if not self._ace.show_playbook:
            print("[ace_dual] ABLATION: learned playbook HIDDEN from generator "
                  "(rulebook only); learning still runs.")
        self._extract_answer = extract_answer
        self._pb = {
            "extract_playbook_bullets": extract_playbook_bullets,
            "update_bullet_counts": update_bullet_counts,
        }

        # --- Resume support -------------------------------------------------
        # A long run can die mid-way (e.g. free-tier rate limit) and be resumed
        # with harness --resume, which SKIPS already-graded tasks and only calls
        # this agent for the remaining ones. So the in-memory playbooks must NOT
        # restart from the seeds on resume — reload the persisted state from the
        # last successful task so cross-task learning survives. A truly fresh
        # run has no persisted files (the launcher clears them) → starts from
        # the packaged seeds inside ACE().
        spath = f"{self.out_prefix}_single.txt"
        try:
            if os.path.exists(spath):
                with open(spath, encoding="utf-8") as f:
                    s = f.read()
                if s.strip():
                    self._ace.single_pb = s
                    self._ace.playbook = s
                    self._ace.next_single_id = get_next_global_id(s)
                    print(f"[ace_dual] RESUMED single playbook "
                          f"({len(s)} chars, next_id={self._ace.next_single_id}). "
                          f"Rulebook is immutable → reloaded from file.")
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_dual] resume-reload skipped ({exc}); starting fresh")

        self._log_dir = os.path.abspath(os.path.join("ace_dual_gdpval_run", "detailed_llm_logs"))
        os.makedirs(self._log_dir, exist_ok=True)
        self._ready = True

    # -- Agent API ---------------------------------------------------------
    def run(self, task: Task, env: Env) -> Trajectory:
        self._ensure()
        with self._lock:  # shared playbooks -> run with --concurrency 1
            return self._run_locked(task, env)

    def _run_locked(self, task: Task, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()
        # STEP 0 + STEP 1: Generator reads BOTH playbooks (labeled two-part view).
        arm, shown_pb = self._select(env)
        deliverable, bullet_ids = self._generate(task, env, traj, shown_pb)
        # grade → dual learning (counting happens inside the merged reflect).
        self._adapt(task, env, deliverable, bullet_ids, arm)
        traj.final_output = deliverable
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- STEP 0: Generator always reads BOTH playbooks (no Thompson) --------
    def _select(self, env: Env) -> tuple[str, str]:
        return "single", self._ace._single_view()

    # -- STEP 1: ACE Generator produces deliverable + bullet_ids -----------
    def _generate(self, task: Task, env: Env, traj: Trajectory, shown_pb: str) -> tuple[str, list]:
        obs = env.observation()
        # FBL-only fix: tell the model the EXACT source filenames sitting in its working
        # directory (codegen copies attachments there), so file-building code READS them
        # (pd.read_excel('X.xlsx')) instead of retyping the injected data — retyping large
        # tables truncates the script and fabricates wrong values (the top data-task failure).
        import os as _os, urllib.parse as _up  # noqa: PLC0415
        srcs = [_up.unquote(_os.path.basename(u.split("?")[0]))
                for u in (task.metadata.get("reference_file_urls") or [])]
        if srcs:
            obs += ("\n\n[SOURCE FILES present in your working directory — if you write code that "
                    "builds a file, READ these by their exact name (e.g. pd.read_excel('"
                    + srcs[0] + "')) instead of retyping the data shown above; retyping a large "
                    "table truncates your script and fabricates wrong values. Files: "
                    + ", ".join(f"'{s}'" for s in srcs) + "]")
        traj.add(Step(type="user_message", text=obs))
        response, bullet_ids, info = self._ace.generator.generate(
            question=obs,
            playbook=shown_pb,
            context=env.instructions(),          # GDPval deliverable guidance
            use_json_mode=False,                 # RAW deliverable + trailing CITED: marker (no JSON envelope)
            call_id=f"gdpval_s_{self._step + 1}_gen",
            log_dir=self._log_dir,
        )
        self._raw_response = response            # kept for dual-learn trajectory
        deliverable = (self._extract_answer(response) or "").strip()
        deliverable = self._verify_repair_code(task, deliverable)   # FBL-only execute-and-repair
        in_p, out_p = _price_for(self.model)
        pt = info.get("prompt_num_tokens", 0) or 0
        rt = info.get("response_num_tokens", 0) or 0
        traj.tokens += pt + rt
        traj.cost += (pt * in_p + rt * out_p) / 1_000_000
        traj.add(Step(type="assistant_message", text=deliverable))
        return deliverable, bullet_ids

    # -- FBL-only: execute-and-repair the code deliverable -------------------
    def _verify_repair_code(self, task: Task, deliverable: str, max_tries: int = 2) -> str:
        """If the deliverable is file-building code that FAILS to run, feed the traceback
        back and let the model fix the bug (a weak model repairs a concrete error far
        better than it writes perfect code first try). Returns working code, or the last
        attempt. Model-agnostic; only touches FBL."""
        try:
            from harness.benchmarks.gdpval_codegen import run_codegen, _extract_code  # noqa: PLC0415
            from harness.benchmarks.gdpval_files import _fetch  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return deliverable
        if not _extract_code(deliverable):
            return deliverable                       # not a code deliverable — leave prose as-is
        srcs = []
        for u in (task.metadata.get("reference_file_urls") or []):
            try:
                srcs.append(_fetch(u))
            except Exception:  # noqa: BLE001
                pass
        schema = self._source_schema(srcs)           # actual sheet/column names -> fix KeyErrors
        cg = run_codegen(deliverable, source_files=srcs)
        tries = 0
        while cg.get("fail_type") == "model" and tries < max_tries:
            tries += 1
            fixed = self._repair_llm((cg.get("error") or "")[-700:], _extract_code(deliverable), schema)
            if not fixed:
                break
            cg2 = run_codegen(fixed, source_files=srcs)
            print(f"[ace_dual] code repair {tries}: {cg.get('fail_type')} -> {cg2.get('fail_type')}",
                  flush=True)
            deliverable = fixed
            if cg2.get("ok"):
                break
            cg = cg2
        return deliverable

    def _source_schema(self, srcs) -> str:
        """Actual sheet names + column headers of the source files, so a repair can fix
        wrong-column KeyErrors (the model guessed a column that isn't there)."""
        lines = []
        for path, fn in srcs:
            ext = fn.lower().rsplit(".", 1)[-1]
            try:
                if ext in ("xlsx", "xlsm"):
                    import openpyxl  # noqa: PLC0415
                    wb = openpyxl.load_workbook(path, read_only=True)
                    for ws in wb.worksheets:
                        hdr = [str(c.value) for c in next(ws.iter_rows(max_row=1), []) if c.value is not None]
                        lines.append(f"{fn} -> sheet '{ws.title}' columns: {hdr}")
                    wb.close()
                elif ext == "csv":
                    import csv  # noqa: PLC0415
                    with open(path, encoding="utf-8", errors="replace") as f:
                        lines.append(f"{fn} -> columns: {next(csv.reader(f), [])}")
            except Exception:  # noqa: BLE001
                pass
        return "\n".join(lines)

    def _repair_llm(self, err: str, code: str, schema: str = "") -> str:
        """One focused fix call: given the traceback (+ actual source schema) and the failing
        script, return corrected code."""
        try:
            import os as _os  # noqa: PLC0415
            from openai import OpenAI  # noqa: PLC0415
            cli = OpenAI(base_url=_os.environ.get("GEMINI_BASE_URL", "http://localhost:4000/v1"),
                         api_key=_os.environ.get("GEMINI_API_KEY", "sk-proxy-rotation"))
            sch = (f"\n\nACTUAL SOURCE SCHEMA (use these EXACT sheet/column names):\n{schema}"
                   if schema else "")
            r = cli.chat.completions.create(
                model=self.model, max_tokens=8192, temperature=0.0,
                messages=[{"role": "user", "content":
                           "This Python script FAILED. Fix the bug and output ONLY the COMPLETE "
                           "corrected script in one ```python block. Read source files by exact name "
                           "(e.g. pd.read_excel), reference only columns/sheets that actually exist, "
                           "keep it compact, and the LAST line must save the output file."
                           + sch + "\n\nERROR:\n" + err + "\n\nSCRIPT:\n```python\n" + code + "\n```"}])
            return (r.choices[0].message.content or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    # -- grade + dual learning ---------------------------------------------
    def _adapt(self, task: Task, env: Env, deliverable: str, bullet_ids: list,
               arm: str) -> None:
        ace = self._ace
        GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of, grade_with_retry = self._grade
        rubric = task.metadata.get("rubric") or []
        step_id = f"gdpval_s_{self._step + 1}"

        if not deliverable or not rubric:
            self._step += 1
            print(f"[ace_dual] task {self._step}: skipped (empty deliverable/rubric)")
            return

        # --- GDPval's OWN rubric grader = the benchmark correctness signal ---
        try:
            # Office-file deliverables: run the agent's code -> real file, grade THAT,
            # so the learning signal matches how the harness scores it.
            from harness.benchmarks.gdpval_codegen import run_codegen, grader_submission  # noqa: PLC0415
            from harness.benchmarks.gdpval_files import _fetch  # noqa: PLC0415
            _srcs = []
            for _u in (task.metadata.get("reference_file_urls") or []):
                try:
                    _srcs.append(_fetch(_u))
                except Exception:  # noqa: BLE001
                    pass
            _cg = run_codegen(deliverable, source_files=_srcs)
            gsub = grader_submission(deliverable, _cg)
            gprompt = GRADER_TEMPLATE.format(
                prompt=task.prompt, submission=gsub,
                rubric=json.dumps([{"rubric_item_id": c.get("rubric_item_id"),
                                    "score": c.get("score"),
                                    "criterion": c.get("criterion"),
                                    "required": c.get("required")} for c in rubric],
                                   ensure_ascii=False))
            grades = grade_with_retry(self._client, self.grader_model, 4096,
                                      gprompt, len(rubric))
            rs = _score_rubric(rubric, grades)
        except Exception as exc:  # noqa: BLE001
            print(f"[ace_dual] grader failed on {task.id}: {exc}")
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
        # Penalties: NEGATIVE-scored criteria that were MET = the deliverable did
        # the punished thing and the grader SUBTRACTED points. This is the
        # strongest "docked points" signal — surface it for the reflector/curator
        # and use it to blame the cited bullets (penalty_weight below).
        triggered = [c for c in rubric
                     if (c.get("score") or 0) < 0
                     and grades.get(str(c.get("rubric_item_id")), False)]
        if triggered:
            fb.append("Penalties TRIGGERED (met NEGATIVE criteria — these SUBTRACTED points):")
            for c in triggered[:10]:
                fb.append(f"- ({c.get('score')} pts) {c.get('criterion')}")
        req_missed = any(c.get("required") and not grades.get(str(c.get("rubric_item_id")), False)
                         for c in rubric)
        if rs["points_earned"] < 0:
            penalty_weight = 1.0
        elif triggered or req_missed:
            penalty_weight = 0.5
        else:
            penalty_weight = 0.0
        environment_feedback = "\n".join(fb)
        ground_truth = "The deliverable is graded against these criteria:\n" + "\n".join(
            f"- ({c.get('score')} pts{', REQUIRED' if c.get('required') else ''}) "
            f"{c.get('criterion')}" for c in rubric)
        is_correct = rs["score"] >= self.success_threshold and rs["required_ok"]

        # --- STEP 2/3: dual-playbook learning (ACE owns it). The ONE merged
        # reflect inside _dual_learn produces both insights AND the bullet_tags
        # used for helpful/harmful counting — no separate counting call. ---
        self._step += 1
        pb_before = len(ace.single_pb)
        # v6 gate: every task still REFLECTS (so helpful/harmful tagging, the
        # grader-aligned counting, and the net-harmful prune keep seeing all the
        # evidence), but only tasks with headroom may GROW the playbook. Above
        # learn_max_score there is little genuine lesson to add — only bulk.
        allow_growth = rs["score"] <= self.learn_max_score
        if self._step % self.curator_frequency == 0:
            try:
                ace._single_learn(
                    question=task.prompt,
                    context=env.instructions(),
                    gen_response=self._raw_response,
                    final_answer=deliverable,
                    is_correct=is_correct,
                    target=ground_truth,
                    bullet_ids=bullet_ids,
                    step=self._step,
                    step_id=step_id,
                    total_samples=max(self._step, 1),
                    config_params={
                        "token_budget": self.token_budget,
                        "use_json_mode": False,      # robust extract_json_from_text path
                        "no_ground_truth": False,
                    },
                    log_dir=self._log_dir,
                    environment_feedback=environment_feedback,  # rubric score + missed criteria
                    score=rs["score"],            # A/C: continuous grader score drives weighting
                    penalty_weight=penalty_weight,  # B: blame cited bullets for docked points
                    allow_growth=allow_growth,      # v6: no ADD on already-good tasks
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[ace_dual] single_learn failed on {task.id}: {exc}")

        self._persist()
        print(f"[ace_dual] task {self._step}: rubric={rs['score']:.2f} pass~={is_correct} "
              f"bullets_cited={len(bullet_ids)} playbook={pb_before}->{len(ace.single_pb)}"
              f"{'' if allow_growth else f' [no-growth: score>{self.learn_max_score}]'}")

    # -- persist the single learned playbook after each task ---------------
    # (The rulebook is immutable → never written; it reloads from ace/rulebook.txt.)
    def _persist(self) -> None:
        ace = self._ace
        try:
            os.makedirs(os.path.dirname(self.out_prefix) or ".", exist_ok=True)
            with open(f"{self.out_prefix}_single.txt", "w", encoding="utf-8") as f:
                f.write(ace.single_pb)
        except Exception:  # noqa: BLE001
            pass
