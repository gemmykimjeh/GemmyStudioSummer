"""gdpval — GDPval real-world economically-valuable knowledge-work tasks (real).

This is **GDPval** (openai/gdpval), NOT the Artificial-Analysis "GDPval-AA" variant.

(a) License:        per OpenAI's GDPval release (dataset: huggingface.co/datasets/
                    openai/gdpval — 220 open "gold" tasks across 44 occupations /
                    9 sectors, of the full 1320).
(b) Infrastructure: LIGHT. No VM/Docker, so no --env-endpoint. Each task is an
                    open-ended professional deliverable; the agent's final message
                    IS the deliverable (tool-free Env). Grading needs an Anthropic
                    key (the LLM judge). Missing key -> precise precheck error.
(c) Scoring:        the OFFICIAL GDPval rubric. Every task ships a ``rubric_json``:
                    a checklist of criteria, each worth ``score`` points. An LLM
                    grader decides which criteria the deliverable meets; the score
                    is (earned points / max positive points), clamped to [0, 1] —
                    the same rubric artifact OpenAI's automated grader uses. (Human
                    expert pairwise grading is the gold standard but not automatable
                    here.) No invented rubric; the criteria come from the dataset.
(d) Data:           https://huggingface.co/datasets/openai/gdpval

Flow: load_tasks (HF dataset; prompt + rubric, with attachment text injected via
the INPUT bridge ``gdpval_files``) -> setup (tool-free Env; the final message is
the deliverable) -> score (run the agent's code through the OUTPUT bridge
``gdpval_codegen`` to VERIFY the file it produced, then grade the verified
manifest with ``grade_with_retry`` over the official rubric_json).

Deliverables are FILES (.xlsx/.docx/.pdf/.pptx). Grading the agent's raw text
would credit a pasted markdown table as a real spreadsheet, so the grader is
shown a verified manifest of what actually exists on disk (see gdpval_codegen);
``grade_with_retry`` stops a malformed judge reply from scoring a good file 0.
"""

from __future__ import annotations

import json
import os
import re

from harness.benchmarks._util import interleave
from harness.benchmark import Benchmark, Env
from harness.registry import register_benchmark
from harness.schema import Result, Task, ToolResult, ToolSpec, Trajectory

_DATASET = "openai/gdpval"

# A rubric grader. GDPval's own automated grader is not open source, but the
# scoring *artifact* (rubric_json, a list of pointed criteria) ships with every
# task; we grade against it verbatim. The judge only decides met/not-met per
# criterion — it does not invent criteria or weights.
GRADER_TEMPLATE = """\
You are an expert grader for a professional work task. Grade the SUBMISSION \
strictly against the RUBRIC below. The rubric is a checklist of independent \
criteria; for each one decide whether the submission clearly meets it.

Be strict and literal: only mark a criterion met if the submission actually \
satisfies it. If the submission is a plan, a refusal, or is missing the \
requested content, those criteria are NOT met. Do not give credit for intent.

# TASK
{prompt}

# SUBMISSION
{submission}

# RUBRIC (JSON list of criteria)
{rubric}

Return ONLY a JSON array, one object per rubric criterion, in the same order:
[{{"rubric_item_id": "<id>", "met": true|false}}, ...]
No prose, no code fences — just the JSON array."""

_INSTRUCTIONS = (
    "You are completing a real professional work task. Produce the COMPLETE "
    "requested deliverable as your final response — the full document, analysis, "
    "table, or content itself, not a summary, outline, or plan. Be thorough and "
    "specific: your output is graded against a detailed rubric of concrete "
    "requirements. Any attached reference files are described within the task text."
)


def _anthropic():
    """Return an Anthropic client, or raise with actionable guidance."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "gdpval grading needs ANTHROPIC_API_KEY (the rubric judge). "
            "Set it and re-run.")
    try:
        import anthropic  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            'gdpval needs the anthropic SDK: pip install -e ".[gdpval]".') from exc
    return anthropic.Anthropic()


def _text_of(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text")


def _parse_grades(text: str) -> dict[str, bool]:
    """Extract {rubric_item_id: met} from the judge's JSON array (robustly)."""
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1 or e < s:
        return {}
    try:
        arr = json.loads(text[s:e + 1])
    except Exception:  # noqa: BLE001
        return {}
    grades: dict[str, bool] = {}
    for item in arr:
        if isinstance(item, dict) and "rubric_item_id" in item:
            grades[str(item["rubric_item_id"])] = bool(item.get("met"))
    return grades


def grade_with_retry(client, model, max_tokens, prompt, n_criteria, tries=3) -> dict[str, bool]:
    """Grade with retries so measurement noise cannot score a good deliverable 0.

    A truncated/malformed judge reply parses to ``{}``, and the scorer reads any
    criterion absent from the dict as unmet -> a good submission scores 0 purely
    from noise. Guards, in order:

      (1) deterministic first attempt (temperature 0) so a clean reply is stable;
          jitter to 0.5 on retry — at temperature 0 a malformed reply reproduces
          identically, so the jitter is what lets it escape;
      (2) up to ``tries`` attempts, so one transient hiccup is not the final score;
      (3) a transient error is caught and retried (not scored as 0);
      (4) require at least half the criteria to parse before accepting the reply,
          so 3-of-94 parsed is treated as a bad read, not a valid verdict.
    """
    grades: dict[str, bool] = {}
    need = max(1, n_criteria // 2)                         # (4) at least half must parse
    for attempt in range(tries):                           # (2) up to `tries` attempts
        try:
            g = client.messages.create(
                model=model, max_tokens=max_tokens,
                temperature=0.0 if attempt == 0 else 0.5,  # (1) deterministic, then jitter
                messages=[{"role": "user", "content": prompt}],
            )
            grades = _parse_grades(_text_of(g))
        except Exception:                                  # noqa: BLE001 (3) retry, not 0
            grades = {}
        if len(grades) >= need:
            break
    return grades


def submission_for_grader(deliverable: str, cg: dict) -> str:
    """Frame the submission for the judge using the VERIFIED codegen outcome.

    GDPval deliverables are files. Instead of grading the agent's raw text (which
    lets a pasted markdown table pass as a real .xlsx), we run the agent's code,
    verify what it actually produced, and tell the judge exactly what exists on
    disk. Framing is keyed on ``cg['fail_type']`` (see gdpval_codegen):

      none  -> a real, non-empty file exists: grade the verified manifest + content
      model -> the code produced no file: file/format/structure criteria NOT met
      env   -> this machine could not run the code: grade intended content only
      no_code -> prose deliverable / execution disabled: grade the text as-is
    """
    ft = cg.get("fail_type", "no_code")
    if ft == "none" and cg.get("ok"):
        manifest = ", ".join(cg.get("files") or [])
        return (f"[The agent produced these ACTUAL deliverable files by running its code: "
                f"{manifest}. Their real extracted contents + verified STRUCTURE follow — grade "
                f"file-type and structure criteria against THIS verified manifest and content.]\n"
                f"{cg.get('extracted', '')}")
    if ft == "model":
        return ("[The agent's code FAILED to produce the required deliverable file (it is broken or "
                "incomplete). No deliverable file exists. Score every criterion that requires the "
                "file, its format, or its structure as NOT met; credit only content fully present "
                "below.]\n" + deliverable)
    if ft == "env":
        return ("[The agent wrote code to build the deliverable file, but THIS environment could "
                "not execute it (a tooling limitation, not a content error). Grade the intended "
                "content and structure the code specifies as if it were the delivered artifact — "
                "do NOT penalize the missing binary itself.]\n" + deliverable)
    return deliverable  # no_code: prose deliverable, grade as-is


def _score_rubric(rubric: list[dict], grades: dict[str, bool]) -> dict:
    """Official rubric scoring: earned / max positive points, clamped to [0, 1]."""
    max_total = sum(c["score"] for c in rubric if c.get("score", 0) > 0)
    earned = 0.0
    n_met = 0
    required_ok = True
    for c in rubric:
        rid = str(c.get("rubric_item_id"))
        met = grades.get(rid, False)
        if met:
            n_met += 1
            earned += c.get("score", 0)  # negatives (penalties) subtract when met
        elif c.get("required"):
            required_ok = False
    raw = (earned / max_total) if max_total > 0 else 0.0
    score = max(0.0, min(1.0, raw))
    return {"score": score, "points_earned": earned, "points_max": max_total,
            "n_criteria": len(rubric), "n_met": n_met, "required_ok": required_ok}


class GDPvalEnv(Env):
    """Tool-free: the agent's final message is the deliverable."""

    def __init__(self, task: Task) -> None:
        self.task = task

    def observation(self) -> str:
        return self.task.prompt

    def instructions(self) -> str:
        return _INSTRUCTIONS

    def tools(self) -> list[ToolSpec]:
        return []

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        return ToolResult(
            output=f"no tools available in this task; unknown tool {name!r}. "
                   "Write the deliverable directly as your response.",
            is_error=True)


@register_benchmark("gdpval")
class GDPval(Benchmark):
    """GDPval: produce a professional deliverable; graded on the official rubric."""

    def __init__(
        self,
        grader_model: str = "claude-sonnet-4-6",
        dataset_name: str = _DATASET,
        split: str = "train",
        success_threshold: float = 0.5,
        max_grader_tokens: int = 4096,
    ) -> None:
        self.grader_model = grader_model
        self.dataset_name = dataset_name
        self.split = split
        self.success_threshold = success_threshold
        self.max_grader_tokens = max_grader_tokens

    # -- tasks ------------------------------------------------------------
    def load_tasks(self, limit: int | None = None) -> list[Task]:
        try:
            from datasets import load_dataset  # lazy
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'gdpval needs the datasets package: pip install -e ".[gdpval]".'
            ) from exc
        ds = load_dataset(self.dataset_name, split=self.split)

        # Group by occupation and interleave so a small --limit spans occupations.
        by_occ: dict[str, list[int]] = {}
        for i in range(len(ds)):
            by_occ.setdefault(ds[i]["occupation"], []).append(i)
        order = interleave([idxs for _, idxs in sorted(by_occ.items())])

        tasks: list[Task] = []
        for i in order:
            r = ds[i]
            try:
                rubric = json.loads(r["rubric_json"])
            except Exception:  # noqa: BLE001 - skip a malformed rubric
                rubric = []
            ref_urls = r.get("reference_file_urls") or []
            # INPUT bridge: extract each attachment to text and append it to the
            # prompt so the (text-only) agent can read the reference files. Same
            # text for every arm; controlled by GDPVAL_INJECT_FILES.
            prompt = r["prompt"]
            try:
                from harness.benchmarks import gdpval_files  # noqa: PLC0415 (lazy)
                prompt = gdpval_files.augment_prompt(prompt, ref_urls)
            except Exception as exc:  # noqa: BLE001 - never fail loading over I/O
                print(f"[gdpval] file injection skipped for {r['task_id']}: {exc}")
            tasks.append(Task(
                id=r["task_id"], benchmark="gdpval",
                prompt=prompt,
                metadata={
                    "task_id": r["task_id"],
                    "sector": r["sector"],
                    "occupation": r["occupation"],
                    "rubric": rubric,
                    "raw_prompt": r["prompt"],
                    "n_reference_files": len(ref_urls),
                    "reference_file_urls": ref_urls,
                    "deliverable_file_urls": r.get("deliverable_file_urls") or [],
                },
            ))
            if limit is not None and len(tasks) >= limit:
                break
        return tasks

    # -- setup ------------------------------------------------------------
    def setup(self, task: Task) -> Env:
        _anthropic()  # precheck the grader key now, with clear guidance if absent
        return GDPvalEnv(task)

    # -- scoring (official rubric) ---------------------------------------
    def score(self, task: Task, trajectory: Trajectory, env: Env) -> Result:
        base = dict(task_id=task.id, benchmark=self.name, agent="",
                    cost=trajectory.cost, wall_time=trajectory.wall_time)
        deliverable = (trajectory.final_output or "").strip()
        rubric = task.metadata.get("rubric") or []
        occ = task.metadata.get("occupation")
        sector = task.metadata.get("sector")
        if not deliverable:
            return Result(**base, success=False, score=0.0,
                          metrics={"empty_deliverable": True, "occupation": occ,
                                   "sector": sector, "n_criteria": len(rubric)})
        if not rubric:
            return Result(**base, success=False, score=0.0,
                          error="task has no rubric_json criteria to grade against",
                          metrics={"occupation": occ, "sector": sector})

        # OUTPUT bridge: execute the agent's code, verify the real file it wrote,
        # and frame the submission for the judge by the verified outcome. When the
        # deliverable is prose (no code) or execution is disabled, this returns the
        # text unchanged (fail_type "no_code").
        cg = {"fail_type": "no_code", "ok": False, "files": []}
        if os.environ.get("GDPVAL_CODEGEN", "1") != "0":
            try:
                from harness.benchmarks.gdpval_codegen import run_codegen  # noqa: PLC0415
                from harness.benchmarks import gdpval_files  # noqa: PLC0415
                attachments = gdpval_files.local_paths(
                    task.metadata.get("reference_file_urls") or [])
                cg = run_codegen(deliverable, attachments=attachments)
            except Exception as exc:  # noqa: BLE001 - never fail scoring over the sandbox
                print(f"[gdpval] codegen skipped for {task.id}: {exc}")
        submission = submission_for_grader(deliverable, cg)

        prompt = GRADER_TEMPLATE.format(
            prompt=task.prompt, submission=submission,
            rubric=json.dumps([{"rubric_item_id": c.get("rubric_item_id"),
                                "score": c.get("score"),
                                "criterion": c.get("criterion"),
                                "required": c.get("required")} for c in rubric],
                              ensure_ascii=False),
        )
        client = _anthropic()
        grades = grade_with_retry(client, self.grader_model, self.max_grader_tokens,
                                  prompt, len(rubric))
        rs = _score_rubric(rubric, grades)
        success = rs["score"] >= self.success_threshold and rs["required_ok"]
        return Result(
            **base, success=success, score=rs["score"],
            metrics={"rubric_score": rs["score"],
                     "points_earned": rs["points_earned"],
                     "points_max": rs["points_max"],
                     "n_criteria": rs["n_criteria"], "n_met": rs["n_met"],
                     "required_ok": rs["required_ok"],
                     "deliverable_chars": len(deliverable),
                     "fail_type": cg.get("fail_type"),
                     "produced_files": cg.get("files") or [],
                     "occupation": occ, "sector": sector},
        )

    def report(self, results: list[Result]) -> str | None:
        graded = [r for r in results if "rubric_score" in r.metrics]
        if not graded:
            return None
        avg = sum(r.metrics["rubric_score"] for r in graded) / len(graded)
        return (f"GDPval: mean rubric score {avg:.3f} over {len(graded)} task(s) "
                f"(success = rubric_score >= {self.success_threshold}).")
