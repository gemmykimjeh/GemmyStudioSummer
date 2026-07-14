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
    "trajectory. Do not generalize away the concrete detail.\n\n**Instructions:**",
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

**Instructions:**
- Carefully analyze the reasoning trace and the environment feedback (gap vs. ground truth).
- Use the SEMANTIC MEMORY (a non-LLM structured extraction of the trajectory) to spot recurring motifs, tool-usage order, and where the attempt turned.
- Tag each provided bulletpoint as ['helpful', 'harmful', 'neutral'] for producing the correct answer.

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

**Instructions:**
- Carefully analyze the reasoning trace and the environment feedback.
- Use the SEMANTIC MEMORY (a non-LLM structured extraction of the trajectory) to spot recurring motifs, tool-usage order, and where the attempt turned.
- Tag each provided bulletpoint as ['helpful', 'harmful', 'neutral'] for producing the correct answer.

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
    "trajectory. Do not generalize away the concrete detail.\n\n**Instructions:**",
    1,
)
