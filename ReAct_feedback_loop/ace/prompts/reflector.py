"""
Reflector prompts for ACE system.
"""

# Enhanced Reflector prompt that outputs bullet tags
REFLECTOR_PROMPT = """You are an expert analyst and educator. Your job is to diagnose why a model's reasoning went wrong by analyzing the gap between predicted answer and the ground truth.

**Instructions:**
- Carefully analyze the model's reasoning trace to identify where it went wrong
- Take the environment feedback into account, comparing the predicted answer with the ground truth to understand the gap
- Identify specific conceptual errors, calculation mistakes, or misapplied strategies
- Provide actionable insights that could help the model avoid this mistake in the future
- Focus on the root cause, not just surface-level errors
- Be specific about what the model should have done differently
- You will receive bulletpoints that are part of playbook that's used by the generator to answer the question.
- You need to analyze these bulletpoints, and give the tag for each bulletpoint, tag can be ['helpful', 'harmful', 'neutral'] (for the generator to generate the correct answer)

Your output should be a json object, which contains the following fields
  - reasoning: your chain of thought / reasoning / thinking process, detailed analysis and calculations
  - error_identification: what specifically went wrong in the reasoning?
  - root_cause_analysis: why did this error occur? What concept was misunderstood?
  - correct_approach: what should the model have done instead?
  - key_insight: what strategy, formula, or principle should be remembered to avoid this error?
  - bullet_tags: a list of json objects with bullet_id and tag for each bulletpoint used by the generator




**Question:**
{}

**Model's Reasoning Trace:**
{}

**Model's Predicted Answer:**
{}

**Ground Truth Answer:**
{}

**Environment Feedback:**
{}

**Part of Playbook that's used by the generator to answer the question:**
{}

**Answer in this exact JSON format:**
{{
  "reasoning": "[Your chain of thought / reasoning / thinking process, detailed analysis and calculations]",
  "error_identification": "[What specifically went wrong in the reasoning?]",
  "root_cause_analysis": "[Why did this error occur? What concept was misunderstood?]",
  "correct_approach": "[What should the model have done instead?]",
  "key_insight": "[What strategy, formula, or principle should be remembered to avoid this error?]",
  "bullet_tags": [
    {{"id": "calc-00001", "tag": "helpful"}},
    {{"id": "fin-00002", "tag": "harmful"}}
  ]
}}

---
"""

# ---------------------------------------------------------------------------
# CONCRETE-mode reflector prompts (dual-playbook: feeds the *concrete* playbook)
# The stock reflector is already concrete-flavored; these are explicit aliases
# nudged toward specific, situation-tied rules (exact APIs/params/error strings).
# Same positional format args as REFLECTOR_PROMPT / _NO_GT.
# ---------------------------------------------------------------------------
CONCRETE_REFLECTOR_PROMPT = REFLECTOR_PROMPT.replace(
    "**Instructions:**",
    "**Focus (CONCRETE):** Produce SPECIFIC, situation-tied insights — exact APIs, "
    "parameters, formulas, error signatures, and step-level fixes tied to THIS "
    "trajectory. Do not generalize away the concrete detail. Recommend only fixes "
    "achievable with the means this environment actually provides — never prescribe a "
    "capability the attempt had no access to (running code, external tools, or emitting "
    "a file type the channel cannot produce), and never conclude the deliverable should "
    "announce what it could not produce: such a capability disclaimer is a defect to "
    "remove, not a rule to add, and the fix for a missing artifact is to render its full "
    "content as text. Tag any bullet that encouraged such a disclaimer or an "
    "unavailable-capability workaround as harmful.\n\n**Instructions:**",
    1,
)

# ---------------------------------------------------------------------------
# ABSTRACT-mode reflector prompts (dual-playbook: feeds the *abstract* playbook)
# Includes the non-LLM auto_distill semantic memory and asks for general,
# transferable patterns. Positional args (GT):
#   question, trace, predicted, ground_truth, feedback, bullets, semantic_memory
# ---------------------------------------------------------------------------
ABSTRACT_REFLECTOR_PROMPT = """You are an expert analyst and educator. Your job is to distill GENERAL, TRANSFERABLE patterns from a model's attempt — principles that would help on *different* future tasks, not just this one.

**Focus (ABSTRACT):**
- Extract cross-trajectory, reusable strategies and heuristics (the "why", not the exact "what").
- Avoid task-specific numbers, exact APIs, or one-off details — those belong in the concrete playbook.
- Ground every pattern in the trajectory evidence and the provided semantic memory.
- Frame fixes around what the environment actually affords: never distill a principle that presumes a capability the attempt lacked (running code, tools, or an unsupported file type), and never treat a capability disclaimer or apology in the deliverable as correct — the general fix for an un-emittable artifact is to render its full content as text, not to announce its absence. Tag any bullet that pushed toward such a disclaimer or an unavailable-capability workaround as harmful.

**Instructions:**
- Carefully analyze the reasoning trace and the environment feedback (gap vs. ground truth).
- Use the SEMANTIC MEMORY (a non-LLM structured extraction of the trajectory) to spot recurring motifs, tool-usage order, and where the attempt turned.
- Tag each provided bulletpoint ['helpful', 'harmful', 'neutral'] by its ACTUAL causal effect on THIS deliverable — NOT by whether the task passed overall. Mark 'harmful' if the bullet steered the deliverable toward a missed or penalized criterion, wasted effort, or crowded out a required element — even on a high-scoring attempt. Mark 'helpful' only if it demonstrably improved a graded element. Mark 'neutral' if it had no real effect. A bullet merely being present on a passing task is NOT evidence that it helped.

Your output should be a json object with these fields:
  - reasoning: your chain of thought / analysis
  - error_identification: what specifically went wrong
  - root_cause_analysis: the underlying, generalizable cause
  - correct_approach: the general approach that should have been taken
  - key_insight: the single most transferable principle to remember
  - bullet_tags: a list of json objects with bullet_id and tag

**Question:**
{}

**Model's Reasoning Trace:**
{}

**Model's Predicted Answer:**
{}

**Ground Truth Answer:**
{}

**Environment Feedback:**
{}

**Part of Playbook that's used by the generator to answer the question:**
{}

**Semantic Memory (auto_distill, non-LLM extraction of this trajectory):**
{}

**Answer in this exact JSON format:**
{{
  "reasoning": "[Your chain of thought / analysis]",
  "error_identification": "[What specifically went wrong?]",
  "root_cause_analysis": "[The underlying, generalizable cause]",
  "correct_approach": "[The general approach that should have been taken]",
  "key_insight": "[The single most transferable principle to remember]",
  "bullet_tags": [
    {{"id": "calc-00001", "tag": "helpful"}},
    {{"id": "fin-00002", "tag": "harmful"}}
  ]
}}

---
"""

# Positional args (NO GT):
#   question, trace, predicted, feedback, bullets, semantic_memory
ABSTRACT_REFLECTOR_PROMPT_NO_GT = """You are an expert analyst and educator. Your job is to distill GENERAL, TRANSFERABLE patterns from a model's attempt — principles that would help on *different* future tasks, not just this one.

**Focus (ABSTRACT):**
- Extract cross-trajectory, reusable strategies and heuristics (the "why", not the exact "what").
- Avoid task-specific numbers, exact APIs, or one-off details — those belong in the concrete playbook.
- Ground every pattern in the trajectory evidence and the provided semantic memory.
- Frame fixes around what the environment actually affords: never distill a principle that presumes a capability the attempt lacked (running code, tools, or an unsupported file type), and never treat a capability disclaimer or apology in the deliverable as correct — the general fix for an un-emittable artifact is to render its full content as text, not to announce its absence. Tag any bullet that pushed toward such a disclaimer or an unavailable-capability workaround as harmful.

**Instructions:**
- Carefully analyze the reasoning trace and the environment feedback.
- Use the SEMANTIC MEMORY (a non-LLM structured extraction of the trajectory) to spot recurring motifs, tool-usage order, and where the attempt turned.
- Tag each provided bulletpoint ['helpful', 'harmful', 'neutral'] by its ACTUAL causal effect on THIS deliverable — NOT by whether the task passed overall. Mark 'harmful' if the bullet steered the deliverable toward a missed or penalized criterion, wasted effort, or crowded out a required element — even on a high-scoring attempt. Mark 'helpful' only if it demonstrably improved a graded element. Mark 'neutral' if it had no real effect. A bullet merely being present on a passing task is NOT evidence that it helped.

Your output should be a json object with these fields:
  - reasoning, error_identification, root_cause_analysis, correct_approach, key_insight, bullet_tags

**Question:**
{}

**Model's Reasoning Trace:**
{}

**Model's Predicted Answer:**
{}

**Environment Feedback:**
{}

**Part of Playbook that's used by the generator to answer the question:**
{}

**Semantic Memory (auto_distill, non-LLM extraction of this trajectory):**
{}

**Answer in this exact JSON format:**
{{
  "reasoning": "[Your chain of thought / analysis]",
  "error_identification": "[What specifically went wrong?]",
  "root_cause_analysis": "[The underlying, generalizable cause]",
  "correct_approach": "[The general approach that should have been taken]",
  "key_insight": "[The single most transferable principle to remember]",
  "bullet_tags": [
    {{"id": "calc-00001", "tag": "helpful"}},
    {{"id": "fin-00002", "tag": "harmful"}}
  ]
}}

---
"""

REFLECTOR_PROMPT_NO_GT = """You are an expert analyst and educator. Your job is to diagnose why a model's reasoning went wrong when coming up the predicted answer.

**Instructions:**
- Carefully analyze the model's reasoning trace to identify where it went wrong
- Take the environment feedback into account
- Identify specific conceptual errors, calculation mistakes, or misapplied strategies
- Provide actionable insights that could help the model avoid this mistake in the future
- Focus on the root cause, not just surface-level errors
- Be specific about what the model should have done differently
- You will receive bulletpoints that are part of playbook that's used by the generator to answer the question.
- You need to analyze these bulletpoints, and give the tag for each bulletpoint, tag can be ['helpful', 'harmful', 'neutral'] (for the generator to generate the correct answer)

Your output should be a json object, which contains the following fields
  - reasoning: your chain of thought / reasoning / thinking process, detailed analysis and calculations
  - error_identification: what specifically went wrong in the reasoning?
  - root_cause_analysis: why did this error occur? What concept was misunderstood?
  - correct_approach: what should the model have done instead?
  - key_insight: what strategy, formula, or principle should be remembered to avoid this error?
  - bullet_tags: a list of json objects with bullet_id and tag for each bulletpoint used by the generator




**Question:**
{}

**Model's Reasoning Trace:**
{}

**Model's Predicted Answer:**
{}

**Environment Feedback:**
{}

**Part of Playbook that's used by the generator to answer the question:**
{}

**Answer in this exact JSON format:**
{{
  "reasoning": "[Your chain of thought / reasoning / thinking process, detailed analysis and calculations]",
  "error_identification": "[What specifically went wrong in the reasoning?]",
  "root_cause_analysis": "[Why did this error occur? What concept was misunderstood?]",
  "correct_approach": "[What should the model have done instead?]",
  "key_insight": "[What strategy, formula, or principle should be remembered to avoid this error?]",
  "bullet_tags": [
    {{"id": "calc-00001", "tag": "helpful"}},
    {{"id": "fin-00002", "tag": "harmful"}}
  ]
}}

---
"""

# Concrete NO-GT mirrors the stock NO-GT reflector with an explicit concrete nudge.
CONCRETE_REFLECTOR_PROMPT_NO_GT = REFLECTOR_PROMPT_NO_GT.replace(
    "**Instructions:**",
    "**Focus (CONCRETE):** Produce SPECIFIC, situation-tied insights - exact APIs, "
    "parameters, formulas, error signatures, and step-level fixes tied to THIS "
    "trajectory. Do not generalize away the concrete detail. Recommend only fixes "
    "achievable with the means this environment actually provides - never prescribe a "
    "capability the attempt had no access to (running code, external tools, or emitting "
    "a file type the channel cannot produce), and never conclude the deliverable should "
    "announce what it could not produce: such a capability disclaimer is a defect to "
    "remove, not a rule to add, and the fix for a missing artifact is to render its full "
    "content as text. Tag any bullet that encouraged such a disclaimer or an "
    "unavailable-capability workaround as harmful.\n\n**Instructions:**",
    1,
)

# ---------------------------------------------------------------------------
# DUAL-mode reflector (v2): ONE call produces BOTH a concrete and an abstract
# insight from the same trajectory, routed to the two playbooks separately.
# Replaces the two separate concrete/abstract reflector calls (and the
# auto_distill semantic-memory injection — the model abstracts directly from the
# raw trajectory, which is present anyway for the concrete insight).
# Positional args (GT):    question, trace, predicted, ground_truth, feedback, bullets
# Positional args (NO GT): question, trace, predicted, feedback, bullets
# ---------------------------------------------------------------------------
DUAL_REFLECTOR_PROMPT = """You are an expert analyst and educator. From a model's attempt, produce TWO complementary lessons in a single pass:
  (1) a CONCRETE insight — SPECIFIC and situation-tied (exact APIs, parameters, formulas, output formats, error signatures, step-level fixes tied to THIS trajectory), for a playbook of specific rules.
  (2) an ABSTRACT insight — a GENERAL, transferable principle (the "why", not the one-off "what") that would help on DIFFERENT future tasks, for a playbook of general principles.

**Instructions:**
- Analyze the reasoning trace and the environment feedback (the gap vs. ground truth).
- Recommend only fixes achievable with the means this environment actually provides; never prescribe a capability the attempt lacked (running code, tools, or an unsupported file type), and never treat a capability disclaimer or apology in the deliverable as a fix — it is a defect to remove, and the fix for a missing artifact is to render its full content as text. Tag any bullet that encouraged such a disclaimer or an unavailable-capability workaround as harmful.
- Ground both lessons in the trajectory; keep the abstract one free of task-specific numbers/APIs. If one level has nothing worth adding, return an empty string for it.
- For EACH criterion the environment feedback lists as NOT met or as a TRIGGERED penalty, diagnose the STRUCTURAL reason it happened — the systemic habit or omission in HOW the deliverable was built (e.g. "collapsed a multi-actor process into one step instead of naming each actor", "stopped at a representative sample instead of the full set", "buried a required element mid-document where it reads as optional"). Do not merely restate the missed item; turn the root cause into a rule that PREVENTS the whole class of miss.
- Tag each provided bulletpoint ['helpful', 'harmful', 'neutral'] by its ACTUAL causal effect on THIS deliverable — NOT by whether the task passed overall. Mark 'harmful' if the bullet steered the deliverable toward a missed or penalized criterion, wasted effort, or crowded out a required element — even on a high-scoring attempt. Mark 'helpful' only if it demonstrably improved a graded element. Mark 'neutral' if it had no real effect. A bullet merely being present on a passing task is NOT evidence that it helped.

**Question:**
{}

**Model's Reasoning Trace:**
{}

**Model's Predicted Answer:**
{}

**Ground Truth Answer:**
{}

**Environment Feedback:**
{}

**Part of Playbook that's used by the generator to answer the question:**
{}

**Answer in this exact JSON format:**
{{
  "reasoning": "[your analysis of what happened and why]",
  "error_identification": "[what specifically went wrong, if anything]",
  "concrete_insight": "[SPECIFIC, situation-tied rule for the concrete playbook, or empty string]",
  "abstract_insight": "[GENERAL, transferable principle for the abstract playbook, or empty string]",
  "bullet_tags": [
    {{"id": "calc-00001", "tag": "helpful"}},
    {{"id": "fin-00002", "tag": "harmful"}}
  ]
}}

---
"""

ABSTRACT_AGGREGATE_PROMPT = """You distill GENERAL, TRANSFERABLE principles from a WINDOW of {} recent task attempts (NOT a single task). Look ACROSS the episodes for RECURRING patterns — repeated failure modes, repeated missed rubric criteria, repeated deliverable types — and state a few general principles that would help on future tasks.

**Rules:**
- Propose a principle ONLY if the pattern appears in MULTIPLE episodes (cross-task). Ignore one-offs from a single episode.
- Keep each principle GENERAL and TRANSFERABLE — no task-specific numbers, APIs, names, or one-off details.
- Prioritize patterns behind repeated failures / repeated missed criteria (the "ACROSS the window" summary points at these).
- If there is no clear cross-task pattern, return an empty string for abstract_insights.

**Recent episodes (distilled):**
{}

**Output ONLY this JSON object (no markdown, no code fences):**
{{
  "reasoning": "[what recurs across the episodes and why]",
  "abstract_insights": "[general, transferable principles — one per line — or an empty string if no cross-task pattern]"
}}
"""


DUAL_REFLECTOR_PROMPT_NO_GT = """You are an expert analyst and educator. From a model's attempt, produce TWO complementary lessons in a single pass:
  (1) a CONCRETE insight — SPECIFIC and situation-tied (exact APIs, parameters, formulas, output formats, error signatures, step-level fixes tied to THIS trajectory), for a playbook of specific rules.
  (2) an ABSTRACT insight — a GENERAL, transferable principle (the "why", not the one-off "what") that would help on DIFFERENT future tasks, for a playbook of general principles.

**Instructions:**
- Analyze the reasoning trace and the environment feedback.
- Recommend only fixes achievable with the means this environment actually provides; never prescribe a capability the attempt lacked (running code, tools, or an unsupported file type), and never treat a capability disclaimer or apology in the deliverable as a fix — it is a defect to remove, and the fix for a missing artifact is to render its full content as text. Tag any bullet that encouraged such a disclaimer or an unavailable-capability workaround as harmful.
- Ground both lessons in the trajectory; keep the abstract one free of task-specific numbers/APIs. If one level has nothing worth adding, return an empty string for it.
- For EACH criterion the environment feedback lists as NOT met or as a TRIGGERED penalty, diagnose the STRUCTURAL reason it happened — the systemic habit or omission in HOW the deliverable was built (e.g. "collapsed a multi-actor process into one step instead of naming each actor", "stopped at a representative sample instead of the full set", "buried a required element mid-document where it reads as optional"). Do not merely restate the missed item; turn the root cause into a rule that PREVENTS the whole class of miss.
- Tag each provided bulletpoint ['helpful', 'harmful', 'neutral'] by its ACTUAL causal effect on THIS deliverable — NOT by whether the task passed overall. Mark 'harmful' if the bullet steered the deliverable toward a missed or penalized criterion, wasted effort, or crowded out a required element — even on a high-scoring attempt. Mark 'helpful' only if it demonstrably improved a graded element. Mark 'neutral' if it had no real effect. A bullet merely being present on a passing task is NOT evidence that it helped.

**Question:**
{}

**Model's Reasoning Trace:**
{}

**Model's Predicted Answer:**
{}

**Environment Feedback:**
{}

**Part of Playbook that's used by the generator to answer the question:**
{}

**Answer in this exact JSON format:**
{{
  "reasoning": "[your analysis of what happened and why]",
  "error_identification": "[what specifically went wrong, if anything]",
  "concrete_insight": "[SPECIFIC, situation-tied rule for the concrete playbook, or empty string]",
  "abstract_insight": "[GENERAL, transferable principle for the abstract playbook, or empty string]",
  "bullet_tags": [
    {{"id": "calc-00001", "tag": "helpful"}},
    {{"id": "fin-00002", "tag": "harmful"}}
  ]
}}

---
"""
