"""
Curator prompts for ACE system.
"""

# Curator prompt for intelligent playbook management
CURATOR_PROMPT = """You are a master curator of a playbook. From a reflection on a previous attempt, decide what to add to the existing playbook. The playbook guides FUTURE attempts (the reflection's ground truth will NOT be available then), so add content that helps produce correct answers.

**Instructions:**
- Review the current playbook and the reflection; ADD only genuinely new, actionable insights that are MISSING — no duplicates or near-duplicates of existing advice.
- Do NOT regenerate the playbook; output only the operations needed. If nothing is worth adding, return an empty operations list.
- Keep it focused and specific: a tight playbook beats an exhaustive one.


**Training Context:**
- Total token budget: {token_budget} tokens
- Training progress: Sample {current_step} out of {total_samples}

**Current Playbook Stats:**
{playbook_stats}

**Recent Reflection:**
{recent_reflection}

**Current Playbook:**
{current_playbook}

**Question Context:**
{question_context}

**Operations** — ADD creates a new bullet under a section (the system assigns the bullet_id, so do not include one in `content`).

**Respond with ONE valid JSON object and nothing else — no markdown, no code fences:**
{{
  "reasoning": "[brief analysis]",
  "operations": [
    {{"type": "ADD", "section": "formulas_and_calculations", "content": "[new insight...]"}}
  ]
}}

---
"""

CURATOR_PROMPT_NO_GT = """You are a master curator of a playbook. From a reflection on a previous attempt, decide what to add to the existing playbook. The playbook guides FUTURE attempts (the reflection's environment feedback will NOT be available then), so add content that helps produce correct answers.

**Instructions:**
- Review the current playbook and the reflection; ADD only genuinely new, actionable insights that are MISSING — no duplicates or near-duplicates of existing advice.
- Do NOT regenerate the playbook; output only the operations needed. If nothing is worth adding, return an empty operations list.
- Keep it focused and specific: a tight playbook beats an exhaustive one.


**Training Context:**
- Total token budget: {token_budget} tokens
- Training progress: Sample {current_step} out of {total_samples}

**Current Playbook Stats:**
{playbook_stats}

**Recent Reflection:**
{recent_reflection}

**Current Playbook:**
{current_playbook}

**Question Context:**
{question_context}

**Operations** — ADD creates a new bullet under a section (the system assigns the bullet_id, so do not include one in `content`).

**Respond with ONE valid JSON object and nothing else — no markdown, no code fences:**
{{
  "reasoning": "[brief analysis]",
  "operations": [
    {{"type": "ADD", "section": "formulas_and_calculations", "content": "[new insight...]"}}
  ]
}}

---
"""

# ===========================================================================
# Dual-playbook curator prompts (mode-specific).
# Derived from the stock CURATOR_PROMPT / _NO_GT by injecting a maintenance
# philosophy line. Same named format fields (token_budget, current_step,
# total_samples, playbook_stats, recent_reflection, current_playbook,
# question_context) so curator.curate() can .format() them identically.
# ===========================================================================

_CONCRETE_CURATOR_NUDGE = (
    "**Playbook Philosophy (CONCRETE):** You maintain a playbook of SPECIFIC, "
    "situation-tied rules — exact APIs, parameters, formulas, and error "
    "signatures. Preserve concrete detail; do NOT over-generalize. Add bullets "
    "only when they capture a concrete, reusable specific.\n\n**Instructions:**"
)
_ABSTRACT_CURATOR_NUDGE = (
    "**Playbook Philosophy (ABSTRACT):** You maintain a playbook of GENERAL, "
    "TRANSFERABLE principles. Keep bullets abstract and cross-task; generalize "
    "or drop over-specific insights (exact numbers/APIs belong in the concrete "
    "playbook). Add bullets only when they capture a reusable general "
    "pattern.\n\n**Instructions:**"
)

# Placed at the very BOTTOM of the curator prompt (recency = better recall). The
# capability-disclaimer / "acknowledge the limitation" bullet is the single most
# recurring harmful pattern: the reflector keeps re-deriving it because it FEELS
# helpful, so we both forbid ADDing it and instruct active cleanup via DELETE.
# Braces are escaped ({{ }}) because these strings are .format()-ed by curate().
_CURATOR_DISCLAIMER_GUARD = """

**STANDING WARNING — do NOT (re)learn capability-disclaimer bullets:**
The most recurring harmful bullet in this playbook is the "acknowledge the
limitation" pattern. NEVER ADD a bullet that advises the writer to:
- state it "cannot" / "is unable to" produce a file or binary/interactive artifact,
- add a "professional note" or disclaimer about a platform/format limitation,
- tell the reader to copy the content into another application, or
- assume tools/code the environment does not provide (e.g. a Python interpreter,
  python-pptx, openpyxl, "generate the file programmatically").
These FEEL helpful but LOWER the score: the grader marks the artifact/format
criteria as unmet precisely because the deliverable announced it could not
produce them. The correct rule is ALWAYS: render the full content directly as
the deliverable, with no disclaimer.

MAINTENANCE (cleanup): scan the CURRENT playbook shown above. For every EXISTING
non-protected bullet that matches the pattern above, emit a DELETE operation:
  {{"type": "DELETE", "bullet_id": "<that bullet's id>", "reason": "capability-disclaimer bullet"}}
Protected/seed bullets are never deleted — do not target them.

**STANDING WARNING — never write the word "rubric" into a bullet:**
The agent that reads this playbook sees ONLY the task and its attachments; it
NEVER sees a rubric or grading criteria (those exist only after submission). A
bullet that says to consult the rubric, treat it as a schema, or cover every
rubric item is therefore impossible to act on — and it overfits to one graded
example. **Any bullet containing the word "rubric" is deleted automatically**, so
adding one wastes the slot. Keep the underlying lesson and phrase it as a
self-standing instruction instead:
- NOT "ensure every rubric item has a section" -> "address each named party,
  step and category individually rather than covering the set with one example"
- NOT "check the rubric for required columns" -> "never assume column names;
  inspect the source file's actual headers before using them"

MAINTENANCE (cleanup): for every EXISTING non-protected bullet whose text
contains "rubric", emit:
  {{"type": "DELETE", "bullet_id": "<that bullet's id>", "reason": "rubric-referencing bullet"}}
"""

CONCRETE_CURATOR_PROMPT = CURATOR_PROMPT.replace("**Instructions:**", _CONCRETE_CURATOR_NUDGE, 1) + _CURATOR_DISCLAIMER_GUARD
CONCRETE_CURATOR_PROMPT_NO_GT = CURATOR_PROMPT_NO_GT.replace("**Instructions:**", _CONCRETE_CURATOR_NUDGE, 1) + _CURATOR_DISCLAIMER_GUARD
ABSTRACT_CURATOR_PROMPT = CURATOR_PROMPT.replace("**Instructions:**", _ABSTRACT_CURATOR_NUDGE, 1) + _CURATOR_DISCLAIMER_GUARD
ABSTRACT_CURATOR_PROMPT_NO_GT = CURATOR_PROMPT_NO_GT.replace("**Instructions:**", _ABSTRACT_CURATOR_NUDGE, 1) + _CURATOR_DISCLAIMER_GUARD

# ---------------------------------------------------------------------------
# SINGLE-mode curator: ONE playbook that mixes concrete + abstract bullets,
# distinguished by an inline predefined tag. The reflection (dual reflector)
# carries both a concrete_insight and an abstract_insight; the curator adds each
# genuinely-new one as its OWN bullet with the matching tag.
# ---------------------------------------------------------------------------
_SINGLE_CURATOR_NUDGE = (
    "**Playbook Philosophy (SINGLE, sectioned):** You maintain ONE playbook "
    "organized into predefined SECTIONS that act as the bullet tags. "
    "CONCRETE-type sections hold specific, situation-tied rules: OUTPUT FORMAT & "
    "STRUCTURE RULES, TOOL & API USAGE, FORMULAS & CALCULATIONS, CODE SNIPPETS & "
    "TEMPLATES, COMMON MISTAKES TO AVOID, VERIFICATION CHECKLIST. ABSTRACT-type "
    "sections hold general, transferable principles: GENERAL PRINCIPLES, "
    "PROBLEM-SOLVING HEURISTICS, TRANSFERABLE STRATEGIES, FAILURE PATTERNS & "
    "RECOVERY, SELF-VERIFICATION HABITS. The reflection may contain BOTH a "
    "concrete_insight and an abstract_insight — when each is genuinely new, ADD it "
    "and set the `section` field to the single best-fitting predefined section "
    "(concrete_insight -> a concrete-type section; abstract_insight -> an "
    "abstract-type section). Do NOT invent new sections.\n\n**Instructions:**"
)

SINGLE_CURATOR_PROMPT = CURATOR_PROMPT.replace("**Instructions:**", _SINGLE_CURATOR_NUDGE, 1) + _CURATOR_DISCLAIMER_GUARD
SINGLE_CURATOR_PROMPT_NO_GT = CURATOR_PROMPT_NO_GT.replace("**Instructions:**", _SINGLE_CURATOR_NUDGE, 1) + _CURATOR_DISCLAIMER_GUARD
