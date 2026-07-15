"""rgr_gdpval — Rubric-Grounded Reflective loop for GDPval (Gemini free-tier).

A minimal, ABLATABLE feedback loop built for GDPval's structure (see ../DESIGN.md).
Registered as agent name `rgr_gdpval`. Four loop modes select the ablation arm:

    a0  no memory (one-shot; instructions only)                      -> floor
    a1  a0 + non-LLM guardrails (M4 tagger + M5 activator, global)   -> zero-call learning
    a2  a1 + gated LLM Reflector (M6) + deterministic curate (M7)    -> LLM reflection value
    a3  a2 + deliverable-type routing (M1)                           -> full RGR

Per task: Generator (1 call) + Grader (1 call) + Reflector (<=1, gated by score).
All generation/reflection go through the multi-key rotating Gemini client so the
run survives free-tier RPM limits; the grader stays on the LiteLLM proxy (:4000).

This adapter does NOT depend on the ace repo — it is self-contained so the new
folder can be dropped in without touching ReAct/ or ReAct_feedback_loop/.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from harness.agent import Agent
from harness.benchmark import Env
from harness.registry import register_agent
from harness.schema import Step, Task, Trajectory

# reuse GDPval's OWN grader artifacts (rubric template + parsers)
from harness.benchmarks.gdpval import (
    GRADER_TEMPLATE, _parse_grades, _score_rubric, _text_of,
)

# RGR core (deterministic) + key rotation
import sys as _sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in _sys.path:
    _sys.path.insert(0, _HERE)
from _rgr import (  # noqa: E402
    SECTIONS, route_section, tag_failures, Counters,
    FAILURE_MODES, insight_is_valid,
)
from _keyring import RotatingGeminiClient  # noqa: E402


_INSTRUCTIONS = (
    "You are completing a real professional work task. Produce the COMPLETE "
    "requested deliverable as your final response — the full document, analysis, "
    "table, or content itself, not a summary, outline, or plan. Be thorough and "
    "specific: your output is graded against a detailed rubric of concrete "
    "requirements. Any attached reference files are described within the task text."
)

_REFLECTOR_PROMPT = """\
You improve a reusable playbook of GENRE-LEVEL work habits for professional \
deliverables. You are given a task, the deliverable that was produced, and the \
rubric criteria it FAILED. Extract at most 2 general, reusable insights that would \
help on FUTURE tasks of the same genre — never anything specific to this task's \
subject, company, or people.

Return ONLY a JSON array (no prose, no code fences):
[
  {{"kind": "strategy"|"pitfall",
    "section": one of {sections},
    "content": "<one general reusable sentence, no proper nouns from the task>",
    "evidence": ["<rubric_item_id you are addressing>", ...]}}
]
Rules: every insight MUST cite at least one failed rubric_item_id in "evidence". \
Keep "content" free of task-specific names/numbers. If nothing generalizes, return [].

# TASK (truncated)
{prompt}

# DELIVERABLE (truncated)
{deliverable}

# FAILED RUBRIC CRITERIA
{failed}
"""


def _fmt_playbook(bullets: list[dict], section: str | None, mode: str) -> str:
    """Render the playbook subset the Generator sees.

    a0: nothing. a1: only __global__ guardrails. a2: __global__ (all learned+guardrail).
    a3: __global__ + the routed section, ranked by helpful-harmful.
    """
    if mode == "a0":
        return ""
    if mode == "a1":
        keep = [b for b in bullets if b["section"] == "__global__"]
    elif mode == "a2":
        keep = [b for b in bullets if b["section"] == "__global__"]
    else:  # a3
        keep = [b for b in bullets if b["section"] in ("__global__", section)]
    if not keep:
        return ""
    keep.sort(key=lambda b: (b["helpful"] - b["harmful"]), reverse=True)
    lines = ["# PLAYBOOK — reusable habits from earlier tasks (apply where relevant):"]
    for b in keep[:40]:
        lines.append(f"- {b['content']}")
    return "\n".join(lines)


@register_agent("rgr_gdpval")
class RGRGDPvalAgent(Agent):
    def __init__(
        self,
        model: str = "gemini-3.1-flash-lite",
        max_tokens: int = 8000,
        loop_mode: str = "a3",
        grader_model: str = "gemini-3.1-flash-lite",
        reflect_threshold: float = 0.7,
        success_threshold: float = 0.5,
        playbook_out: str = "rgr_playbook_gdpval.json",
        counters_out: str = "rgr_counters_gdpval.json",
        dedup_threshold: float = 0.85,
        **_ignore,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.loop_mode = os.environ.get("RGR_MODE", loop_mode)
        assert self.loop_mode in ("a0", "a1", "a2", "a3"), self.loop_mode
        self.grader_model = grader_model
        self.reflect_threshold = reflect_threshold
        self.success_threshold = success_threshold
        # per-arm output filenames so arms don't clobber each other
        self.playbook_out = playbook_out.replace(".json", f"_{self.loop_mode}.json")
        self.counters_out = counters_out.replace(".json", f"_{self.loop_mode}.json")
        self.dedup_threshold = dedup_threshold

        self._ready = False
        self._lock = threading.Lock()
        self._gemini = None          # rotating generator/reflector client
        self._grader = None          # LiteLLM-proxied anthropic-shaped client
        self.bullets: list[dict] = []
        self.counters: Counters | None = None
        self._next_id = 1
        self._step = 0

    # -- setup -------------------------------------------------------------
    def _ensure(self) -> None:
        if self._ready:
            return
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:  # noqa: BLE001
            pass

        self._gemini = RotatingGeminiClient(model_hint=self.model)

        # grader: reuse the existing LiteLLM proxy path (anthropic SDK -> :4000)
        import anthropic
        base = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:4000")
        self._grader = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "sk-proxy"), base_url=base)

        self.counters = Counters(self.counters_out)
        # a1/a2/a3 carry a playbook; a0 never reads/writes one
        if os.path.exists(self.playbook_out) and self.loop_mode != "a0":
            try:
                with open(self.playbook_out, encoding="utf-8") as f:
                    self.bullets = json.load(f)
                self._next_id = max((b["id"] for b in self.bullets), default=0) + 1
            except Exception:  # noqa: BLE001
                self.bullets = []
        self._ready = True
        print(f"[rgr] mode={self.loop_mode} model={self.model} "
              f"bullets={len(self.bullets)}")

    # -- Agent API ---------------------------------------------------------
    def run(self, task: Task, env: Env) -> Trajectory:
        self._ensure()
        with self._lock:  # shared playbook -> run with --concurrency 1
            return self._run_locked(task, env)

    def _run_locked(self, task: Task, env: Env) -> Trajectory:
        start = time.perf_counter()
        traj = Trajectory()
        section = route_section(task.prompt) if self.loop_mode == "a3" else "document"

        deliverable = self._generate(task, env, traj, section)
        if self.loop_mode != "a0" and deliverable:
            self._adapt(task, deliverable, section)

        traj.final_output = deliverable
        traj.final_state = env.snapshot()
        traj.wall_time = time.perf_counter() - start
        return traj

    # -- M2 Generator ------------------------------------------------------
    def _generate(self, task: Task, env: Env, traj: Trajectory, section: str) -> str:
        obs = env.observation()
        traj.add(Step(type="user_message", text=obs))
        playbook = _fmt_playbook(self.bullets, section, self.loop_mode)
        sys_prompt = _INSTRUCTIONS + (("\n\n" + playbook) if playbook else "")
        try:
            resp = self._gemini.chat.completions.create(
                model=self.model, max_tokens=self.max_tokens,
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": obs}])
            deliverable = (resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            if usage:
                traj.tokens += (getattr(usage, "prompt_tokens", 0) or 0) + \
                               (getattr(usage, "completion_tokens", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            print(f"[rgr] generate failed on {task.id}: {exc}")
            deliverable = ""
        traj.add(Step(type="assistant_message", text=deliverable))
        return deliverable

    # -- M3 Grade + M4 tag + M5 activate + M6 reflect + M7 curate ----------
    def _adapt(self, task: Task, deliverable: str, section: str) -> None:
        rubric = task.metadata.get("rubric") or []
        if not rubric:
            self._step += 1
            return

        # M3: GDPval's own rubric grader (via proxy)
        try:
            gprompt = GRADER_TEMPLATE.format(
                prompt=task.prompt, submission=deliverable,
                rubric=json.dumps([{"rubric_item_id": c.get("rubric_item_id"),
                                    "score": c.get("score"),
                                    "criterion": c.get("criterion"),
                                    "required": c.get("required")} for c in rubric],
                                   ensure_ascii=False))
            g = self._grader.messages.create(
                model=self.grader_model, max_tokens=4096,
                messages=[{"role": "user", "content": gprompt}])
            grades = _parse_grades(_text_of(g))
            rs = _score_rubric(rubric, grades)
        except Exception as exc:  # noqa: BLE001
            print(f"[rgr] grader failed on {task.id}: {exc}")
            self._step += 1
            return

        missed = [c for c in rubric
                  if not grades.get(str(c.get("rubric_item_id")), False)]

        # M4: non-LLM failure-mode tagging
        mode_hits, unmatched = tag_failures(missed)
        self.counters.bump(mode_hits)

        # M5: activate pre-written guardrails when a mode crosses threshold
        n_activated = 0
        for mode in self.counters.newly_activated():
            spec = FAILURE_MODES[mode]
            self._add_bullet(content=spec["guardrail"], section=spec["section"],
                             kind="pitfall", origin="guardrail",
                             evidence=[mode])
            self.counters.mark_activated(mode)
            n_activated += 1
        self.counters.save()

        # M6: gated LLM Reflector (a2/a3 only; skip in a1)
        n_insights = 0
        gate = (rs["score"] < self.reflect_threshold) or (not rs["required_ok"])
        if self.loop_mode in ("a2", "a3") and gate and missed:
            n_insights = self._reflect_and_curate(task, deliverable, section, missed)

        self._save_playbook()
        self._step += 1
        unmatched_rate = len(unmatched) / max(len(missed), 1)
        print(f"[rgr] task {self._step} mode={self.loop_mode} "
              f"score={rs['score']:.2f} req_ok={rs['required_ok']} "
              f"missed={len(missed)} fm_hits={sum(mode_hits.values())} "
              f"fm_other={unmatched_rate:.0%} activated={n_activated} "
              f"insights={n_insights} bullets={len(self.bullets)}")

    # -- M6 reflect + M7 curate -------------------------------------------
    def _reflect_and_curate(self, task: Task, deliverable: str,
                            section: str, missed: list[dict]) -> int:
        failed_txt = "\n".join(
            f'- id={c.get("rubric_item_id")}'
            f'{" [REQUIRED]" if c.get("required") else ""}: {c.get("criterion")}'
            for c in missed[:20])
        prompt = _REFLECTOR_PROMPT.format(
            sections=SECTIONS, prompt=task.prompt[:2500],
            deliverable=deliverable[:2500], failed=failed_txt)
        try:
            r = self._gemini.chat.completions.create(
                model=self.model, max_tokens=1500,
                messages=[{"role": "user", "content": prompt}])
            raw = (r.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[rgr] reflect failed on {task.id}: {exc}")
            return 0

        insights = self._parse_insights(raw)
        n = 0
        for ins in insights[:2]:
            if self.loop_mode == "a2":
                ins["section"] = "__global__"   # a2 has no routing
            ok, why = insight_is_valid(ins, task.prompt)
            if not ok:
                print(f"[rgr]   rejected insight ({why})")
                continue
            self._add_bullet(content=ins["content"], section=ins["section"],
                             kind=ins.get("kind", "strategy"), origin="learned",
                             evidence=ins.get("evidence", []))
            n += 1
        return n

    @staticmethod
    def _parse_insights(text: str) -> list[dict]:
        s, e = text.find("["), text.rfind("]")
        if s == -1 or e == -1 or e < s:
            return []
        try:
            arr = json.loads(text[s:e + 1])
            return [x for x in arr if isinstance(x, dict)]
        except Exception:  # noqa: BLE001
            return []

    # -- M7 store: dedup + counters ---------------------------------------
    def _add_bullet(self, content: str, section: str, kind: str,
                    origin: str, evidence: list) -> None:
        norm = re.sub(r"\s+", " ", content.strip().lower())
        # cheap deterministic dedup (token Jaccard) — no embeddings needed at v1 scale
        for b in self.bullets:
            if b["section"] != section:
                continue
            if _jaccard(norm, b["_norm"]) >= self.dedup_threshold:
                b["helpful"] += 1        # reinforce survivor instead of duplicating
                return
        self.bullets.append({
            "id": self._next_id, "section": section, "content": content.strip(),
            "kind": kind, "origin": origin, "evidence": evidence,
            "helpful": 0, "harmful": 0, "usage_count": 0, "_norm": norm,
        })
        self._next_id += 1

    def _save_playbook(self) -> None:
        if self.loop_mode == "a0":
            return
        try:
            with open(self.playbook_out, "w", encoding="utf-8") as f:
                json.dump(self.bullets, f, indent=2, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
