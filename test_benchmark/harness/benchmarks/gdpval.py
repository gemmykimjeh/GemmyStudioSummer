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

Flow: load_tasks (HF dataset; prompt + rubric) -> setup (tool-free Env; the final
message is the deliverable) -> score (LLM grader over the official rubric_json).
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


def grade_with_retry(client, model, max_tokens, prompt, n_criteria, tries=3):
    """Call the LLM rubric grader robustly and return {rubric_item_id: met}.

    The grader (Gemini via the proxy) occasionally returns malformed/truncated/empty
    output that ``_parse_grades`` can't read -> every criterion counts as unmet -> a
    good deliverable is scored 0. That spurious-zero is pure measurement noise. This
    wrapper removes it: temperature 0 for a deterministic verdict, and up to ``tries``
    attempts until we get a usable verdict set (>= half the criteria parsed); retries
    jitter the temperature so a deterministically-malformed reply can't repeat, and a
    transient proxy/throttle error just retries instead of scoring 0.
    """
    grades: dict = {}
    need = max(1, n_criteria // 2)
    for attempt in range(tries):
        try:
            g = client.messages.create(
                model=model, max_tokens=max_tokens,
                temperature=0.0 if attempt == 0 else 0.5,
                messages=[{"role": "user", "content": prompt}],
            )
            grades = _parse_grades(_text_of(g))
        except Exception:  # noqa: BLE001 - transient proxy/throttle -> retry, don't score 0
            grades = {}
        if len(grades) >= need:
            break
    return grades


class GDPvalEnv(Env):
    """Tool-free: the agent's final message is the deliverable."""

    def __init__(self, task: Task) -> None:
        self.task = task
        self._obs: str | None = None

    def observation(self) -> str:
        # Inject the extracted content of the task's reference files (xlsx/pdf/docx/
        # ...) so the agent reads real attachments instead of hallucinating. Cached
        # per-Env (fetched once). Toggle with env GDPVAL_INJECT_FILES=0.
        if self._obs is None:
            from harness.benchmarks.gdpval_files import reference_files_text  # noqa: PLC0415
            self._obs = self.task.prompt + reference_files_text(self.task.metadata)
        return self._obs

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
            tasks.append(Task(
                id=r["task_id"], benchmark="gdpval",
                prompt=r["prompt"],
                metadata={
                    "task_id": r["task_id"],
                    "sector": r["sector"],
                    "occupation": r["occupation"],
                    "rubric": rubric,
                    "n_reference_files": len(r.get("reference_file_urls") or []),
                    "reference_file_urls": r.get("reference_file_urls") or [],
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

        # Real GDPval deliverables are FILES. If the agent emitted Python that writes
        # an office file (xlsx/docx/pdf/pptx), run it and grade the REAL file content +
        # a verified manifest, so file-type/structure criteria are facts, not guesses.
        from harness.benchmarks.gdpval_codegen import run_codegen, grader_submission  # noqa: PLC0415
        from harness.benchmarks.gdpval_files import _fetch  # noqa: PLC0415
        srcs = []
        for u in (task.metadata.get("reference_file_urls") or []):
            try:
                srcs.append(_fetch(u))          # (cached_path, original_filename)
            except Exception:  # noqa: BLE001
                pass
        cg = run_codegen(deliverable, source_files=srcs)
        submission = grader_submission(deliverable, cg)

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
        # Cause-tracking (for structural diagnosis, not printed): which criteria were
        # missed + what file type the gold deliverable is, so we can later separate
        # "lost points to a file/format we don't produce" from genuine content misses.
        missed = [{"pts": c.get("score"), "req": bool(c.get("required")),
                   "c": (c.get("criterion") or "")[:160]}
                  for c in rubric if not grades.get(str(c.get("rubric_item_id")), False)]
        import os as _os  # noqa: PLC0415
        gold_types = sorted({_os.path.splitext(u.split("?")[0])[1].lower()
                             for u in (task.metadata.get("deliverable_file_urls") or [])})
        return Result(
            **base, success=success, score=rs["score"],
            metrics={"rubric_score": rs["score"],
                     "points_earned": rs["points_earned"],
                     "points_max": rs["points_max"],
                     "n_criteria": rs["n_criteria"], "n_met": rs["n_met"],
                     "required_ok": rs["required_ok"],
                     "deliverable_chars": len(deliverable),
                     "missed": missed,
                     "gold_types": gold_types,
                     "codegen_files": cg.get("files", []),
                     "codegen_fail": cg.get("fail_type", "none"),
                     "occupation": occ, "sector": sector},
        )

    def report(self, results: list[Result]) -> str | None:
        graded = [r for r in results if "rubric_score" in r.metrics]
        if not graded:
            return None
        avg = sum(r.metrics["rubric_score"] for r in graded) / len(graded)
        return (f"GDPval: mean rubric score {avg:.3f} over {len(graded)} task(s) "
                f"(success = rubric_score >= {self.success_threshold}).")
