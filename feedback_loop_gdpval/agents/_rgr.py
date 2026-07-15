"""_rgr — Rubric-Grounded Reflective loop: the non-LLM core (M1, M4, M5, M7 helpers).

Everything here is deterministic (no API calls). The LLM pieces (Generator M2,
Grader M3, gated Reflector M6) live in the agent. See ../DESIGN.md §3.

Design decisions this file encodes:
  - Sections are DELIVERABLE-TYPE keys, not occupations (DESIGN.md F3).
  - A fixed 6-mode failure taxonomy covers most GDPval error mass (F4); each mode
    has a pre-written guardrail bullet activated by counters (ASSAY-style seeds).
  - Failed rubric criteria are mapped to modes by keyword rules (the auto_distill
    analog); unmatched -> FM_other (logged, never guessed).
"""

from __future__ import annotations

import json
import os
import re

# --------------------------------------------------------------------------
# Deliverable-type sections (M1 routes into exactly one of these + __global__)
# --------------------------------------------------------------------------
SECTIONS = ["__global__", "document", "spreadsheet", "presentation",
            "analysis", "communication"]

_ROUTE_RULES: list[tuple[str, re.Pattern]] = [
    ("spreadsheet", re.compile(
        r"\b(spreadsheet|\.xlsx|\.csv|excel|workbook|tab titled|second tab|"
        r"pivot|formula|cell[s]?\b)", re.I)),
    ("presentation", re.compile(
        r"\b(slide[s]?|deck|presentation|\.pptx|powerpoint|keynote)", re.I)),
    ("communication", re.compile(
        r"\b(email|memo|letter|message|reply|respond to|customer|client "
        r"communication|correspondence)", re.I)),
    ("analysis", re.compile(
        r"\b(analy[sz]e|analysis|variance|forecast|model the|calculat|"
        r"regression|statistic|dataset|data set)", re.I)),
    # document is the fallback (reports, briefs, plans, specs, docx/pdf prose)
]


def route_section(prompt: str) -> str:
    """M1: pick the deliverable-type section from the task prompt (keyword rules)."""
    for name, pat in _ROUTE_RULES:
        if pat.search(prompt or ""):
            return name
    return "document"


# --------------------------------------------------------------------------
# Failure-mode taxonomy (M4) + pre-written guardrail bullets (M5)
# --------------------------------------------------------------------------
# Each mode: keyword pattern over a FAILED criterion's text -> the mode fires.
FAILURE_MODES: dict[str, dict] = {
    "FM1_deliverable_missing": {
        "pattern": re.compile(
            r"\b(deliverable|final (file|output|document|report)|actual (file|content)|"
            r"provide[sd]? the (file|document)|must be provided|attach|downloadable|"
            r"produce[sd]? the (report|file|document))", re.I),
        "section": "__global__",
        "guardrail": (
            "Produce the COMPLETE requested deliverable as the final response — the "
            "full document/table/content itself, never a summary, outline, plan, or "
            "description of what you would produce. A missing deliverable fails "
            "regardless of how detailed the surrounding explanation is."),
    },
    "FM2_format_noncompliance": {
        "pattern": re.compile(
            r"\b(format|structure|\.xlsx|\.csv|\.docx|\.pptx|tab|column|heading|"
            r"section header|slide count|layout|template)", re.I),
        "section": "__global__",
        "guardrail": (
            "Match the requested format exactly: file type, named tabs/sheets, "
            "column layout, section headings, and slide/section counts. Treat every "
            "explicit structural instruction in the prompt as a hard requirement."),
    },
    "FM3_coverage_gap": {
        "pattern": re.compile(
            r"\b(include[sd]?|cover[sd]?|address(es|ed)?|section on|missing|omit|"
            r"all (of )?the|each (of )?the|every|stakeholder|requirement[s]?)", re.I),
        "section": "__global__",
        "guardrail": (
            "Treat any enumerated list of required topics/sections/stakeholders in "
            "the prompt as a mandatory checklist. Map each item to a concrete place "
            "in the deliverable and confirm none is dropped before finishing."),
    },
    "FM4_reference_ignored": {
        "pattern": re.compile(
            r"\b(reference (file|material|data|document)|attached|provided (data|"
            r"file)|source (file|document)|use the (data|figures|numbers)|"
            r"per the (attached|reference))", re.I),
        "section": "__global__",
        "guardrail": (
            "Use the facts, figures, and constraints from the reference material "
            "described in the task. Do not invent values or contradict the provided "
            "data; ground specific claims in the reference content."),
    },
    "FM5_quantitative_error": {
        "pattern": re.compile(
            r"\b(calculat|comput|sum|total|average|percentage|figure[s]?|number[s]?|"
            r"units?|accurate|correct value|workings?|math|arithmetic)", re.I),
        "section": "analysis",
        "guardrail": (
            "For any quantitative task, show the calculation steps and units, and "
            "double-check totals/percentages. When the prompt asks for workings, "
            "include them explicitly rather than only the final number."),
    },
    "FM6_unsupported_claims": {
        "pattern": re.compile(
            r"\b(cite|citation|source[s]?|reference[s]?|url|link[s]?|evidence|"
            r"support(ed|ing)?|spec sheet|verifiable)", re.I),
        "section": "__global__",
        "guardrail": (
            "When the prompt asks for sources, citations, links, or verifiable "
            "evidence, provide concrete references (URLs / named sources / spec "
            "sheets) for each relevant claim rather than unsupported assertions."),
    },
}

_ACTIVATION_THRESHOLD = int(os.environ.get("RGR_GUARDRAIL_K", "2"))


def tag_failures(missed_criteria: list[dict]) -> tuple[dict[str, int], list[str]]:
    """M4: map each failed criterion to a failure mode by keyword rules.

    Returns (mode_hits, unmatched_criteria_texts). A single criterion may hit
    multiple modes; every criterion that matches nothing is returned so the run
    can log FM_other rate (a taxonomy-coverage diagnostic, DESIGN.md §6.4).
    """
    hits: dict[str, int] = {}
    unmatched: list[str] = []
    for c in missed_criteria:
        text = str(c.get("criterion", ""))
        matched = False
        for mode, spec in FAILURE_MODES.items():
            if spec["pattern"].search(text):
                hits[mode] = hits.get(mode, 0) + 1
                matched = True
        if not matched:
            unmatched.append(text)
    return hits, unmatched


class Counters:
    """Persisted failure-mode counters + which guardrails are already active."""

    def __init__(self, path: str):
        self.path = path
        self.data = {"modes": {}, "activated": []}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:  # noqa: BLE001
                pass
        self.data.setdefault("modes", {})
        self.data.setdefault("activated", [])

    def bump(self, mode_hits: dict[str, int]) -> None:
        for mode, n in mode_hits.items():
            self.data["modes"][mode] = self.data["modes"].get(mode, 0) + n

    def newly_activated(self) -> list[str]:
        """Modes that crossed the threshold and are not yet activated."""
        out = []
        for mode, count in self.data["modes"].items():
            if count >= _ACTIVATION_THRESHOLD and mode not in self.data["activated"]:
                out.append(mode)
        return out

    def mark_activated(self, mode: str) -> None:
        if mode not in self.data["activated"]:
            self.data["activated"].append(mode)

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# M6 deterministic reject rules (replace the LLM Verifier)
# --------------------------------------------------------------------------
# A capitalized token that isn't a common sentence-start word — a proxy for a
# task-specific proper noun (company/person/product name).
_CAP_TOKEN = re.compile(r"\b([A-Z][a-z]{2,})\b")
# common words that legitimately start sentences / appear capitalized generically
_CAP_STOPWORDS = {
    "the", "a", "an", "for", "when", "always", "never", "use", "produce",
    "match", "treat", "provide", "include", "ensure", "avoid", "do", "if",
    "this", "that", "these", "those", "each", "every", "any", "all", "your",
    "remember", "consider", "note", "make", "keep", "check", "confirm",
}


def _proper_tokens(text: str) -> set[str]:
    """Capitalized tokens that look like task-specific names (not generic words)."""
    return {
        m.group(1).lower()
        for m in _CAP_TOKEN.finditer(text or "")
        if m.group(1).lower() not in _CAP_STOPWORDS
    }


def insight_is_valid(insight: dict, prompt: str) -> tuple[bool, str]:
    """Deterministic gate on a Reflector insight (DESIGN.md §3 M6).

    Reject if: no rubric evidence, target section unknown, too short, or the
    content copies task-specific capitalized names from the prompt (forces
    genre-level generality and blocks rubric/prompt leakage).
    """
    if not insight.get("evidence"):
        return False, "no rubric evidence"
    if insight.get("section") not in SECTIONS:
        return False, f"unknown section {insight.get('section')!r}"
    content = str(insight.get("content", ""))
    if len(content.strip()) < 15:
        return False, "content too short"
    prompt_names = _proper_tokens(prompt)
    leaked = _proper_tokens(content) & prompt_names
    if leaked:
        return False, f"leaks task-specific term(s) {sorted(leaked)}"
    return True, "ok"
