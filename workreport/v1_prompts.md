# v1 — The Four Agents, and How Each Differs From Baseline

> **What v1 is.** The first feedback-loop version: a **dual-playbook, 4-agent** design assembled from
> papers (AEL → concrete/abstract split + Thompson selector; EDV → a Verifier; ReasoningBank →
> learn from failure; ASSAY → seeds). One task runs **7 LLM calls**: Generator → 3 Reflector passes
> (concrete, abstract, count) → Verifier → 2 Curators (concrete, abstract).
>
> **Source.** All blocks below are verbatim from the real v1 run on **Jul 14** (`ace_dual_gdpval_run`,
> task `s_1`, ~17:53). The prompt templates were never committed to git; the logs are the record.
>
> | Agent | Log file (Jul 14, task s_1) | Baseline counterpart |
> |---|---|---|
> | Generator | `generator_..._gen_20260714_175340_878.json` | same role in baseline |
> | Reflector ×3 | `reflect_concrete` / `reflect_abstract` / `reflect_count` (…175353 / 175356 / 175350) | ONE reflector in baseline |
> | Verifier | `verifier_..._verify_20260714_175358_190.json` | **none** — new at v1 |
> | Curator ×2 | `curate_concrete` / `curate_abstract` (…175359 / 175402) | ONE curator in baseline |

**Diff convention below:** lines identical to baseline are plain; **`+` marks text that is new at
v1**; `[BASELINE ONLY]` marks text v1 removed. The Verifier has no baseline counterpart, so it is
shown whole.

---

# 1 · GENERATOR — diff vs baseline

**Finding: the Generator prompt *template* is byte-identical to baseline.** Same role sentence, same
instructions, same JSON output schema. Nothing in the template changed at v1.

```diff
  You are an analysis expert tasked with answering questions using your knowledge, a curated playbook of strategies and insights and a reflection that goes over the diagnosis of all previous mistakes made while answering the question.

  **Instructions:**
  - Read the playbook carefully and apply relevant strategies, formulas, and insights
  - Pay attention to common mistakes listed in the playbook and avoid them
  - Show your reasoning step-by-step
  - Be concise but thorough in your analysis
  - If the playbook contains relevant code snippets or formulas, use them appropriately
  - Double-check your calculations and logic before providing the final answer

  Your output should be a json object, which contains the following fields:
  - reasoning: your chain of thought / reasoning / thinking process, detailed analysis and calculations
  - bullet_ids: each line in the playbook has a bullet_id ... you should include their bullet_id in this list
  - final_answer: your concise final answer
```

**What actually differs is not the template but the `**Playbook:**` block that gets filled in.** At
v1 the Thompson selector picks **one** of the two playbooks and injects only that one. On task s_1 it
injected the **concrete** playbook (sectioned), and the abstract playbook was not shown:

```diff
  **Playbook:**
+ ## OUTPUT FORMAT & STRUCTURE RULES
+ [fmt-00001] helpful=0 harmful=0 :: Reproduce the requested output format literally: if a schema, headings, table columns, file type, or field names are specified, match them exactly ...
+ [fmt-00002] helpful=0 harmful=0 :: Lead the deliverable with the direct answer / one-line summary ...
+ ## TOOL & API USAGE
+ [api-00003] helpful=0 harmful=0 :: Read a tool/API's specification ... before the first call; do not guess argument names or types.
+ ...
- [BASELINE ONLY] a single 7-section playbook (STRATEGIES & INSIGHTS, FORMULAS & CALCULATIONS, ... OTHERS), and no Thompson selection — baseline always shows its one and only playbook.
```

> **Net for the Generator:** at v1 the change is architectural (which playbook, chosen how), **not**
> a prompt-wording change. The generator instruction text is exactly baseline's.

---

# 2 · REFLECTOR — diff vs baseline

Baseline has **one** Reflector call. v1 fans it into **three** passes over the same trajectory.

## 2a. `reflect_concrete` — baseline reflector + a CONCRETE focus header

```diff
  You are an expert analyst and educator. Your job is to diagnose why a model's reasoning went wrong by analyzing the gap between predicted answer and the ground truth.

+ **Focus (CONCRETE):** Produce SPECIFIC, situation-tied insights — exact APIs, parameters, formulas, error signatures, and step-level fixes tied to THIS trajectory. Do not generalize away the concrete detail.

  **Instructions:**
  - Carefully analyze the model's reasoning trace to identify where it went wrong
  - Take the environment feedback into account, comparing the predicted answer with the ground truth ...
  - ... (identical to baseline reflector instructions) ...
  - You need to analyze these bulletpoints, and give the tag ... ['helpful', 'harmful', 'neutral']
```

Output schema — **identical to baseline** (`reasoning`, `error_identification`, `root_cause_analysis`,
`correct_approach`, `key_insight`, `bullet_tags`):

```text
{
  "reasoning": "...",
  "error_identification": "...",
  "root_cause_analysis": "...",
  "correct_approach": "...",
  "key_insight": "...",
  "bullet_tags": [ {"id": "...", "tag": "helpful"} ]
}
```

> Note: at v1 the anti-disclaimer clause does **not** exist yet — that is appended to this same
> header at v3. At v1 the header stops at "Do not generalize away the concrete detail."

## 2b. `reflect_abstract` — entirely new framing (no baseline equivalent)

This pass has no baseline counterpart. It is re-pointed from "diagnose this task" to "distill
transferable patterns", and it consumes a new input: a non-LLM **SEMANTIC MEMORY** extraction.

```diff
+ You are an expert analyst and educator. Your job is to distill GENERAL, TRANSFERABLE patterns from a model's attempt — principles that would help on *different* future tasks, not just this one.

+ **Focus (ABSTRACT):**
+ - Extract cross-trajectory, reusable strategies and heuristics (the "why", not the exact "what").
+ - Avoid task-specific numbers, exact APIs, or one-off details — those belong in the concrete playbook.
+ - Ground every pattern in the trajectory evidence and the provided semantic memory.

+ **Instructions:**
+ - Carefully analyze the reasoning trace and the environment feedback (gap vs. ground truth).
+ - Use the SEMANTIC MEMORY (a non-LLM structured extraction of the trajectory) to spot recurring motifs, tool-usage order, and where the attempt turned.
+ - Tag each provided bulletpoint as ['helpful', 'harmful', 'neutral'] for producing the correct answer.
```

Output schema — same field *names* as baseline, but every field is re-scoped to the general level:

```diff
  {
    "reasoning": "[Your chain of thought / analysis]",
    "error_identification": "[What specifically went wrong?]",
+   "root_cause_analysis": "[The underlying, generalizable cause]",
+   "correct_approach": "[The general approach that should have been taken]",
+   "key_insight": "[The single most transferable principle to remember]",
    "bullet_tags": [ {"id": "...", "tag": "helpful"} ]
  }
```

## 2c. `reflect_count` — a third pass dedicated to counter-tagging

A third Reflector call (no baseline equivalent). Its header is the CONCRETE one, but its job is to
tag the cited bullets **helpful/harmful/neutral across the playbook so the counters can be updated** —
separated out from insight generation. It is the largest of the three prompts (~15 k chars) because
it carries the full set of cited bullets to tally.

> **Net for the Reflector:** baseline's single diagnostic call becomes **three** — concrete insight,
> abstract insight (new, semantic-memory-fed), and a dedicated counting pass. This is the "Reflector
> runs multiple times" cost that v2 later collapses back into one call.

---

# 3 · VERIFIER — new at v1 (no baseline counterpart)

Baseline has no Verifier. v1 inserts it between the Reflectors and the Curators (from EDV:
verify-before-write, default-reject-only-on-nameable-failure). It reads **both** candidate insight
streams and accepts or rejects each.

Full prompt head (verbatim):

```text
You are a strict, rigorous auditor. Hold a HIGH bar, but be fair: ACCEPT an insight stream when it clearly passes both checks below, and REJECT only when a check clearly fails. Do NOT reject just because you are uncertain, cautious, or because the insight overlaps with existing knowledge — reject only for a concrete, nameable failure.

You are given two candidate reflection streams produced from the SAME trajectory:
- CONCRETE stream — meant for a playbook of SPECIFIC, situation-tied rules (exact APIs, params, formulas, error signatures).
- ABSTRACT stream — meant for a playbook of GENERAL, TRANSFERABLE principles.

For EACH stream, check IN ORDER:
1. GROUNDED / TRUE TO TRAJECTORY: Is the insight supported by concrete evidence in the trajectory (steps, tool calls, outputs, the gap vs. ground truth)? Reject ONLY if it is clearly hallucinated, speculative, or contradicted by the trajectory.
2. LEVEL-FIT for its target playbook:
   - CONCRETE stream → is it concrete enough (specific, situation-tied)? Reject ONLY if it is a vague, generic platitude with no situational grounding.
   - ABSTRACT stream → is it general/transferable enough? ...
```

Output schema (verbatim tail):

```json
{
  "concrete": {
    "accepted": true or false,
    "reason": "[why accepted/rejected, citing the failing check if rejected]",
    "verified_reflection": "[tightened insight text if accepted, else empty string]"
  },
  "abstract": {
    "accepted": true or false,
    "reason": "[why accepted/rejected, citing the failing check if rejected]",
    "verified_reflection": "[tightened insight text if accepted, else empty string]"
  }
}
```

> **Why it was killed at v2:** across 100 tasks this auditor was configured to default-accept-unless-
> nameable-failure and, in practice, **rejected 0 bullets** — one wasted LLM call per task for zero
> decisions. v2 removes it.

---

# 4 · CURATOR — diff vs baseline

Baseline has **one** Curator ("master curator of knowledge"). v1 runs **two** — one per playbook —
each identical to the baseline curator except for a one-paragraph **Playbook Philosophy** header
prepended that tells it which playbook it is tending.

## 4a. `curate_concrete`

```diff
  You are a master curator of knowledge. Your job is to identify what new insights should be added to an existing playbook based on a reflection from a previous attempt.

  **Context:**
  - The playbook you created will be used to help answering similar questions.
  - The reflection is generated using ground truth answers that will NOT be available when the playbook is being used. ...

  **CRITICAL: You MUST respond with valid JSON only. Do not use markdown formatting or code blocks.**

+ **Playbook Philosophy (CONCRETE):** You maintain a playbook of SPECIFIC, situation-tied rules — exact APIs, parameters, formulas, and error signatures. Preserve concrete detail; do NOT over-generalize. Add bullets only when they capture a concrete, reusable specific.

  **Instructions:**
  - Review the existing playbook and the reflection from the previous attempt
  - Identify ONLY the NEW insights, strategies, or mistakes that are MISSING ...
  - ... (identical to baseline curator instructions) ...
```

## 4b. `curate_abstract`

Same baseline body, with the abstract-flavoured philosophy header instead:

```diff
+ **Playbook Philosophy (ABSTRACT):** You maintain a playbook of GENERAL, TRANSFERABLE principles. Keep bullets abstract and cross-task; generalize or drop over-specific insights (exact numbers/APIs belong in the concrete playbook). Add bullets only when they capture a reusable general pattern.
```

Operations — **identical to baseline: ADD only.** DELETE does not exist yet (added at v4).

> **Net for the Curator:** baseline's single curator becomes **two** (one per playbook), each = the
> baseline curator + a one-line philosophy header. No new operations, no cleanup orders, no rulebook.

---

# Summary — baseline → v1, per agent

| Agent | Prompt-template change at v1 | Structural change at v1 |
|---|---|---|
| **Generator** | **None** — template byte-identical to baseline | Sees ONE Thompson-selected playbook (of two), sectioned |
| **Reflector** | `reflect_concrete` = baseline + CONCRETE header; `reflect_abstract` = new framing + SEMANTIC MEMORY; `reflect_count` = new | ONE call → **THREE** calls |
| **Verifier** | **Entirely new** (no baseline) | New 4th agent between Reflect and Curate |
| **Curator** | baseline curator + a one-line CONCRETE / ABSTRACT philosophy header | ONE curator → **TWO** (one per playbook); still ADD-only |

**What v1 did NOT have yet** (added later): the anti-disclaimer clause (v3), the immutable RULEBOOK
and DELETE (v4), the raw-deliverable generator and `CITED:` footer (v5), the growth gate (v6). At v1
the notebook could only grow, the generator prompt was still baseline's, and the loop cost ~7 LLM
calls per task — the two facts (0 verifier rejections, ~1.8× cost) that drove the v2 simplification.
