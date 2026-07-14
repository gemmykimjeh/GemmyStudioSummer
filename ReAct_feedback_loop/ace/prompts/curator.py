"""
Curator prompts for ACE system.
"""

# Curator prompt for intelligent playbook management
CURATOR_PROMPT = """You are a master curator of knowledge. Your job is to identify what new insights should be added to an existing playbook based on a reflection from a previous attempt.

**Context:**
- The playbook you created will be used to help answering similar questions. 
- The reflection is generated using ground truth answers that will NOT be available when the playbook is being used. So you need to come up with content that can aid the playbook user to create predictions that likely align with ground truth. 

**CRITICAL: You MUST respond with valid JSON only. Do not use markdown formatting or code blocks.**

**Instructions:**
- Review the existing playbook and the reflection from the previous attempt
- Identify ONLY the NEW insights, strategies, or mistakes that are MISSING from the current playbook
- Avoid redundancy - if similar advice already exists, only add new content that is a perfect complement to the existing playbook
- Do NOT regenerate the entire playbook - only provide the additions needed
- Focus on quality over quantity - a focused, well-organized playbook is better than an exhaustive one
- Format your response as a PURE JSON object with specific sections
- For any operation if no new content to add, return an empty list for the operations field
- Be concise and specific - each addition should be actionable


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

**Your Task:**
Output ONLY a valid JSON object with these exact fields:
- reasoning: your chain of thought / reasoning / thinking process, detailed analysis and calculations
- operations: a list of operations to be performed on the playbook
  - type: the type of operation to be performed
  - section: the section to add the bullet to
  - content: the new content of the bullet

**Available Operations:**
1. ADD: Create new bullet points with fresh IDs
    - section: the section to add the new bullet to
    - content: the new content of the bullet. Note: no need to include the bullet_id in the content like '[ctx-00263] helpful=1 harmful=0 ::', the bullet_id will be added by the system.

**RESPONSE FORMAT - Output ONLY this JSON structure (no markdown, no code blocks):**
{{
  "reasoning": "[Your chain of thought / reasoning / thinking process, detailed analysis and calculations here]",
  "operations": [
    {{
      "type": "ADD", 
      "section": "formulas_and_calculations",
      "content": "[New calculation method...]"
    }}
  ]
}}

---
"""

CURATOR_PROMPT_NO_GT = """You are a master curator of knowledge. Your job is to identify what new insights should be added to an existing playbook based on a reflection from a previous attempt.

**Context:**
- The playbook you created will be used to help answering similar questions. 
- The reflection is generated using environment feedback that will NOT be available when the playbook is being used.

**CRITICAL: You MUST respond with valid JSON only. Do not use markdown formatting or code blocks.**

**Instructions:**
- Review the existing playbook and the reflection from the previous attempt
- Identify ONLY the NEW insights, strategies, or mistakes that are MISSING from the current playbook
- Avoid redundancy - if similar advice already exists, only add new content that is a perfect complement to the existing playbook
- Do NOT regenerate the entire playbook - only provide the additions needed
- Focus on quality over quantity - a focused, well-organized playbook is better than an exhaustive one
- Format your response as a PURE JSON object with specific sections
- For any operation if no new content to add, return an empty list for the operations field
- Be concise and specific - each addition should be actionable


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

**Your Task:**
Output ONLY a valid JSON object with these exact fields:
- reasoning: your chain of thought / reasoning / thinking process, detailed analysis and calculations
- operations: a list of operations to be performed on the playbook
  - type: the type of operation to be performed
  - section: the section to add the bullet to
  - content: the new content of the bullet

**Available Operations:**
1. ADD: Create new bullet points with fresh IDs
    - section: the section to add the new bullet to
    - content: the new content of the bullet. Note: no need to include the bullet_id in the content like '[ctx-00263] helpful=1 harmful=0 ::', the bullet_id will be added by the system.

**RESPONSE FORMAT - Output ONLY this JSON structure (no markdown, no code blocks):**
{{
  "reasoning": "[Your chain of thought / reasoning / thinking process, detailed analysis and calculations here]",
  "operations": [
    {{
      "type": "ADD", 
      "section": "formulas_and_calculations",
      "content": "[New calculation method...]"
    }}
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

CONCRETE_CURATOR_PROMPT = CURATOR_PROMPT.replace("**Instructions:**", _CONCRETE_CURATOR_NUDGE, 1)
CONCRETE_CURATOR_PROMPT_NO_GT = CURATOR_PROMPT_NO_GT.replace("**Instructions:**", _CONCRETE_CURATOR_NUDGE, 1)
ABSTRACT_CURATOR_PROMPT = CURATOR_PROMPT.replace("**Instructions:**", _ABSTRACT_CURATOR_NUDGE, 1)
ABSTRACT_CURATOR_PROMPT_NO_GT = CURATOR_PROMPT_NO_GT.replace("**Instructions:**", _ABSTRACT_CURATOR_NUDGE, 1)
