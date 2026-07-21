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
import math
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


def _select_bullets(bullets: list[dict], section: str | None, mode: str) -> list[dict]:
    """The playbook subset the Generator sees, ranked by helpful-harmful.

    a0: nothing. a1: only __global__ guardrails. a2: __global__ (all learned+seed).
    a3: __global__ + the routed section. Ranking is real now that harmful is
    tracked (Change 2), so the top bullets are the ones that actually help.
    """
    if mode == "a0":
        return []
    if mode in ("a1", "a2"):
        keep = [b for b in bullets if b["section"] == "__global__"]
    else:  # a3
        keep = [b for b in bullets if b["section"] in ("__global__", section)]
    keep.sort(key=lambda b: (b["helpful"] - b["harmful"], b.get("usage_count", 0)),
              reverse=True)
    return keep[:40]


def _fmt_playbook(shown: list[dict]) -> str:
    if not shown:
        return ""
    lines = ["# PLAYBOOK — reusable habits from earlier tasks (apply where relevant):"]
    for b in shown:
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
        reflect_floor: float = 0.2,
        success_threshold: float = 0.5,
        playbook_out: str = "rgr_playbook_gdpval.json",
        counters_out: str = "rgr_counters_gdpval.json",
        dedup_threshold: float = 0.86,
        global_cap: int = 15,
        **_ignore,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.loop_mode = os.environ.get("RGR_MODE", loop_mode)
        assert self.loop_mode in ("a0", "a1", "a2", "a3"), self.loop_mode
        self.grader_model = grader_model
        self.reflect_threshold = reflect_threshold
        # Change 3(a): don't reflect on near-0 tool-ceiling failures — only learn
        # from the "productive middle" (reflect_floor < score < reflect_threshold).
        self.reflect_floor = float(os.environ.get("RGR_REFLECT_FLOOR", reflect_floor))
        self.success_threshold = success_threshold
        # per-arm output filenames so arms don't clobber each other
        self.playbook_out = playbook_out.replace(".json", f"_{self.loop_mode}.json")
        self.counters_out = counters_out.replace(".json", f"_{self.loop_mode}.json")
        # Change 4: dedup is now embedding cosine similarity (Jaccard fallback).
        self.dedup_threshold = float(os.environ.get("RGR_DEDUP_THRESHOLD", dedup_threshold))
        # Change 3(b): hard cap on __global__ so the always-shown pile can't dilute.
        self.global_cap = int(os.environ.get("RGR_GLOBAL_CAP", global_cap))

        self._ready = False
        self._lock = threading.Lock()
        self._gemini = None          # rotating generator/reflector client
        self._grader = None          # LiteLLM-proxied anthropic-shaped client
        self.bullets: list[dict] = []
        self.counters: Counters | None = None
        self._next_id = 1
        self._step = 0
        self._emb_cache: dict[int, list[float]] = {}   # bullet id -> embedding (in-memory)
        self._last_shown: list[int] = []               # bullet ids shown in the last prompt

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

        # Change 1: seed ALL 6 guardrails at startup (always-on), before task 1.
        # Replaces the "counter must hit 2" activation gate that rarely fired.
        # Seeds are origin="seed" and are protected from retirement/eviction.
        n_seeded = 0
        if self.loop_mode != "a0":
            n_seeded = self._seed_guardrails()

        self._ready = True
        print(f"[rgr] mode={self.loop_mode} model={self.model} "
              f"bullets={len(self.bullets)} (seeded {n_seeded} guardrails)")

    def _seed_guardrails(self) -> int:
        """Load every pre-written guardrail into memory once, as origin='seed'."""
        present = {b["evidence"][0] for b in self.bullets
                   if b.get("origin") in ("seed", "guardrail") and b.get("evidence")}
        n = 0
        for mode, spec in FAILURE_MODES.items():
            if mode in present:
                continue
            content = spec["guardrail"]
            self.bullets.append({
                "id": self._next_id, "section": spec["section"],
                "content": content.strip(), "kind": "pitfall", "origin": "seed",
                "evidence": [mode], "helpful": 0, "harmful": 0, "usage_count": 0,
                "_norm": re.sub(r"\s+", " ", content.strip().lower()),
            })
            self._next_id += 1
            n += 1
        return n

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
        shown = _select_bullets(self.bullets, section, self.loop_mode)
        self._last_shown = [b["id"] for b in shown]   # for usage/harmful tracking
        playbook = _fmt_playbook(shown)
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

        # Change 2: credit/blame the bullets that were in THIS task's prompt,
        # measured against a running baseline of scores seen so far. A task that
        # scored below baseline blames its shown bullets (harmful++); at/above
        # baseline reinforces them (helpful++). usage_count tracks exposure.
        worse = self._update_usage_and_counts(rs["score"])

        # M4: non-LLM failure-mode tagging (kept purely as an FM-coverage
        # diagnostic; guardrails are now seeded at startup, not activated here).
        mode_hits, unmatched = tag_failures(missed)
        self.counters.bump(mode_hits)
        self.counters.save()

        # M6: gated LLM Reflector (a2/a3 only). Change 3(a): the gate is now a
        # WINDOW — reflect only in the productive middle, skipping near-0
        # tool-ceiling failures (nothing to learn) and near-pass tasks alike.
        n_insights = 0
        gate = self.reflect_floor < rs["score"] < self.reflect_threshold
        if self.loop_mode in ("a2", "a3") and gate and missed:
            n_insights = self._reflect_and_curate(task, deliverable, section, missed)

        # Change 2: retire learned bullets that have proven net-harmful.
        n_retired = self._retire_bullets()
        # Change 3(b): hard-cap __global__, evicting the lowest-ranked learned.
        n_evicted = self._enforce_global_cap()

        self._save_playbook()
        self._step += 1
        unmatched_rate = len(unmatched) / max(len(missed), 1)
        print(f"[rgr] task {self._step} mode={self.loop_mode} "
              f"score={rs['score']:.2f} req_ok={rs['required_ok']} "
              f"missed={len(missed)} fm_hits={sum(mode_hits.values())} "
              f"fm_other={unmatched_rate:.0%} insights={n_insights} "
              f"{'WORSE ' if worse else ''}retired={n_retired} evicted={n_evicted} "
              f"bullets={len(self.bullets)} "
              f"baseline={self.counters.data.get('baseline', 0.0):.2f}")

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

    # -- M7 store: semantic dedup + counters ------------------------------
    def _add_bullet(self, content: str, section: str, kind: str,
                    origin: str, evidence: list) -> None:
        norm = re.sub(r"\s+", " ", content.strip().lower())
        # Change 4: dedup by embedding cosine similarity (Jaccard fallback when
        # the embedder is unavailable). This catches paraphrases token-Jaccard
        # misses, so reinforcement lands on the real survivor and the ranking
        # (helpful-harmful) reflects distinct advice.
        new_emb = self._embed(content)
        for b in self.bullets:
            if b["section"] != section:
                continue
            sim = None
            if new_emb is not None:
                b_emb = self._embed_of(b)
                if b_emb is not None:
                    sim = _cosine(new_emb, b_emb)
            if sim is None:                       # no embeddings -> heuristic
                sim = _jaccard(norm, b["_norm"])
            if sim >= self.dedup_threshold:
                b["helpful"] += 1                 # reinforce survivor, don't duplicate
                return
        bid = self._next_id
        self.bullets.append({
            "id": bid, "section": section, "content": content.strip(),
            "kind": kind, "origin": origin, "evidence": evidence,
            "helpful": 0, "harmful": 0, "usage_count": 0, "_norm": norm,
        })
        if new_emb is not None:
            self._emb_cache[bid] = new_emb
        self._next_id += 1

    # -- Change 2: usage / helpful / harmful + running baseline -----------
    def _update_usage_and_counts(self, score: float) -> bool:
        """Attribute this task's outcome to the bullets it was shown.

        Compares `score` to the running-mean baseline of PRIOR tasks; a
        below-baseline task blames its shown bullets (harmful++), otherwise it
        reinforces them (helpful++). Then folds `score` into the baseline.
        Returns whether this task scored below baseline.
        """
        shown = set(self._last_shown)
        data = self.counters.data
        n = int(data.get("n_scored", 0))
        baseline = data.get("baseline")  # None until the first task is scored
        worse = baseline is not None and score < float(baseline)
        for b in self.bullets:
            if b["id"] in shown:
                b["usage_count"] = b.get("usage_count", 0) + 1
                if baseline is None:
                    continue                     # no reference yet -> usage only
                if worse:
                    b["harmful"] += 1
                else:
                    b["helpful"] += 1
        mean = float(baseline) if baseline is not None else 0.0
        data["baseline"] = (mean * n + score) / (n + 1)
        data["n_scored"] = n + 1
        return worse

    # -- Change 2: retirement --------------------------------------------
    def _retire_bullets(self) -> int:
        """Drop learned bullets that have proven net-harmful with enough usage.

        Seeds and guardrails are protected — only origin=='learned' can retire.
        """
        keep, retired = [], 0
        for b in self.bullets:
            if (b.get("origin") == "learned"
                    and b.get("usage_count", 0) >= 4
                    and b.get("harmful", 0) > b.get("helpful", 0)):
                self._emb_cache.pop(b["id"], None)
                retired += 1
                print(f"[rgr]   retired learned bullet #{b['id']} "
                      f"(usage={b['usage_count']} harmful={b['harmful']} "
                      f"helpful={b['helpful']})")
                continue
            keep.append(b)
        self.bullets = keep
        return retired

    # -- Change 3(b): hard cap on __global__ ------------------------------
    def _enforce_global_cap(self) -> int:
        """Keep __global__ at <= global_cap, evicting the lowest-ranked learned.

        Seeds/guardrails are never evicted; only learned bullets are trimmed, so
        the always-on guardrails survive and only weak learned advice is dropped.
        """
        g = [b for b in self.bullets if b["section"] == "__global__"]
        if len(g) <= self.global_cap:
            return 0
        protected = [b for b in g if b.get("origin") != "learned"]
        learned = [b for b in g if b.get("origin") == "learned"]
        slots = max(0, self.global_cap - len(protected))
        learned.sort(key=lambda b: (b["helpful"] - b["harmful"], b.get("usage_count", 0)),
                     reverse=True)
        keep_ids = {b["id"] for b in protected} | {b["id"] for b in learned[:slots]}
        evicted = [b for b in learned[slots:]]
        for b in evicted:
            self._emb_cache.pop(b["id"], None)
        if evicted:
            self.bullets = [b for b in self.bullets
                            if b["section"] != "__global__" or b["id"] in keep_ids]
        return len(evicted)

    # -- embeddings -------------------------------------------------------
    def _embed(self, text: str) -> list[float] | None:
        if self._gemini is None:
            return None
        try:
            return self._gemini.embed(text)
        except Exception as exc:  # noqa: BLE001
            print(f"[rgr] embed failed: {exc}")
            return None

    def _embed_of(self, bullet: dict) -> list[float] | None:
        emb = self._emb_cache.get(bullet["id"])
        if emb is None:
            emb = self._embed(bullet["content"])
            if emb is not None:
                self._emb_cache[bullet["id"]] = emb
        return emb

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


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
