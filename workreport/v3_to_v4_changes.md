# v3 → v4 — The Real Prompt/Architecture Changes, Marked

> **How this was reconstructed.** The v4 edits were **never committed to git** (developed and run
> locally, committed only later at v5). The authoritative record is the run itself — the switch
> happened on **Jul 16** between the morning `_concrete` / `_abstract` dual logs (**v3**) and the
> afternoon `_single` logs (**v4**). Every claim below is a direct v3-vs-v4 diff of the actual
> `detailed_llm_logs/`.
>
> | Era | Curator log | Playbook shape |
> |---|---|---|
> | **v3** | `curator_gdpval_s_5_curate_concrete_20260716_113555_650.json` (Jul 16 11:35) | two playbooks (concrete + abstract) |
> | **v4** | `curator_gdpval_s_1_curate_single_20260716_151907_360.json` (Jul 16 15:19) | one playbook + immutable RULEBOOK |

**v4 is the biggest single architectural change in the project.** Five things landed together:

| # | Change at v4 | New at v4? |
|---|---|---|
| 1 | **Two playbooks → ONE playbook + an immutable RULEBOOK** | ✅ |
| 2 | **DELETE operation** — the Curator can finally remove a bullet (and it actually executes) | ✅ |
| 3 | **STANDING WARNING** — a standing order to hunt down and DELETE capability-disclaimer bullets | ✅ |
| 4 | Reflector emits `concrete_insight` + `abstract_insight`; Curator files by predefined **section** | ✅ |
| 5 | **Generator: JSON envelope → raw deliverable** (`final_answer` field dropped; `CITED:` footer added) | ✅ |

> ⚠️ **Yes — the DELETE function was added at v3 → v4.** Before v4 the Curator prompt offered **only
> ADD** (and the baseline DELETE handler was an unimplemented stub). At v4 DELETE is both **offered
> in the prompt** and **implemented in code**, and the v4 run actually emitted DELETE operations
> (proof below). What is *not* yet present at v4: the anti-**"rubric"** standing warning — that is a
> v5 addition (only the disclaimer warning exists at v4; verified `STANDING WARNING` count = 1).

---

## Change 1 — two playbooks collapse into one playbook + an immutable RULEBOOK

### The Curator's "Philosophy" line was rewritten

```diff
- # v3 (concrete curator):
- **Playbook Philosophy (CONCRETE):** You maintain a playbook of SPECIFIC, situation-tied rules —
- exact APIs, parameters, formulas, and error signatures. Preserve concrete detail; do NOT
- over-generalize. Add bullets only when they capture a concrete, reusable specific.

+ # v4 (single curator):
+ **Playbook Philosophy (SINGLE, sectioned):** You maintain ONE playbook organized into predefined
+ SECTIONS that act as the bullet tags. CONCRETE-type sections hold specific, situation-tied rules:
+ OUTPUT FORMAT & STRUCTURE RULES, TOOL & API USAGE, FORMULAS & CALCULATIONS, CODE SNIPPETS &
+ TEMPLATES, COMMON MISTAKES TO AVOID, VERIFICATION CHECKLIST. ABSTRACT-type sections hold general,
+ transferable principles: GENERAL PRINCIPLES, PROBLEM-SOLVING HEURISTICS, TRANSFERABLE STRATEGIES,
+ FAILURE PATTERNS & RECOVERY, SELF-VERIFICATION HABITS. The reflection may contain BOTH a
+ concrete_insight and an abstract_insight — when each is genuinely new, ADD it and set the
+ `section` field to the single best-fitting predefined section. Do NOT invent new sections.
```

The concrete/abstract split moved **from two separate playbooks** (each with its own curator) **into
sections of one playbook** curated by one curator.

### The full v4 Curator prompt (verbatim)

The complete prompt as sent to the model, from `curator_gdpval_s_1_curate_single_20260716_151907_360.json`
(2,943 tokens). This is task `s_1`, so the learned playbook is still empty (section headers only) —
which makes the new structure easy to read. The `+` markers flag what is new versus v3.

````text
You are a master curator of a playbook. From a reflection on a previous attempt, decide what to add to the existing playbook. The playbook guides FUTURE attempts (the reflection's ground truth will NOT be available then), so add content that helps produce correct answers.

# + NEW AT v4: single sectioned playbook (replaces the v3 "CONCRETE" one-line philosophy)
**Playbook Philosophy (SINGLE, sectioned):** You maintain ONE playbook organized into predefined SECTIONS that act as the bullet tags. CONCRETE-type sections hold specific, situation-tied rules: OUTPUT FORMAT & STRUCTURE RULES, TOOL & API USAGE, FORMULAS & CALCULATIONS, CODE SNIPPETS & TEMPLATES, COMMON MISTAKES TO AVOID, VERIFICATION CHECKLIST. ABSTRACT-type sections hold general, transferable principles: GENERAL PRINCIPLES, PROBLEM-SOLVING HEURISTICS, TRANSFERABLE STRATEGIES, FAILURE PATTERNS & RECOVERY, SELF-VERIFICATION HABITS. The reflection may contain BOTH a concrete_insight and an abstract_insight — when each is genuinely new, ADD it and set the `section` field to the single best-fitting predefined section (concrete_insight -> a concrete-type section; abstract_insight -> an abstract-type section). Do NOT invent new sections.

**Instructions:**
- Review the current playbook and the reflection; ADD only genuinely new, actionable insights that are MISSING — no duplicates or near-duplicates of existing advice.
- Do NOT regenerate the playbook; output only the operations needed. If nothing is worth adding, return an empty operations list.
- Keep it focused and specific: a tight playbook beats an exhaustive one.

**Training Context:**
- Total token budget: 80000 tokens
- Training progress: Sample 1 out of 1

**Current Playbook Stats:**
{
  "total_bullets": 0,
  "high_performing": 0,
  "problematic": 0,
  "unused": 0,
  "by_section": {}
}

**Recent Reflection:**
{
  "reasoning": "The model failed to provide a comprehensive sample that satisfied all categorical constraints (e.g., specific country/division combinations like Italy/Corporate Loans). It also failed to include the full population in the output, which prevented the verification of the 'all divisions/sub-divisions' coverage requirement. The sample size calculation was performed correctly in theory but failed to account for the finite population correction (FPC) factor required by the rubric, and the final output was not formatted as a multi-tab Excel-style structure as requested.",
  "error_identification": "1. Incomplete sampling: Failed to include required specific entity/division combinations (e.g., Italy, Greece, Luxembourg). 2. Missing data: Did not include the full population list in the output, making it impossible to verify full coverage. 3. Calculation error: Omitted the Finite Population Correction (FPC) in the sample size formula. 4. Formatting: Failed to present the output as a multi-tab structure.",
  # + NEW AT v4: reflection now carries TWO insights (concrete_insight + abstract_insight)
  "concrete_insight": "When performing audit sampling, the required sample size 'n' must be adjusted using the Finite Population Correction (FPC) formula: n_adj = n / (1 + (n-1)/N), where N is the total population size. Ensure the final sample selection explicitly includes at least one row for every unique combination of Division, Sub-Division, and Country specified in the audit scope, and verify this by cross-referencing the selected rows against a complete list of unique categories found in the population data.",
  "abstract_insight": "When a task requires coverage across multiple categorical dimensions, create a 'coverage matrix' or checklist before selecting samples. Map every required category to at least one data point in your selection set to ensure no constraints are missed. Always verify that the scope of your output matches the scope of the input (e.g., if the task implies a full population review, do not truncate the dataset in the final deliverable).",
  "bullet_tags": [
    {"id": "calc-00001", "tag": "helpful"},
    {"id": "fin-00002", "tag": "helpful"}
  ]
}

# + NEW AT v4: 12 predefined sections (6 concrete-type + 5 abstract-type + OTHERS), starts empty
**Current Playbook:**
## OUTPUT FORMAT & STRUCTURE RULES

## TOOL & API USAGE

## FORMULAS & CALCULATIONS

## CODE SNIPPETS & TEMPLATES

## COMMON MISTAKES TO AVOID

## VERIFICATION CHECKLIST

## GENERAL PRINCIPLES

## PROBLEM-SOLVING HEURISTICS

## TRANSFERABLE STRATEGIES

## FAILURE PATTERNS & RECOVERY

## SELF-VERIFICATION HABITS

## OTHERS

**Question Context:**
You are completing a real professional work task. Produce the COMPLETE requested deliverable as your final response — the full document, analysis, table, or content itself, not a summary, outline, or plan. Be thorough and specific: your output is graded against a detailed rubric of concrete requirements. Any attached reference files are described within the task text.

# + NEW AT v4: the entire immutable RULEBOOK block below (27 rules, no counters, never edited)
## RULEBOOK — immutable ground rules; respect them, and NEVER add, edit, delete, or target these ids:
# ==========================================================================
# RULEBOOK — immutable, must-follow rules. Checked on EVERY task. A bible for every run.
# Never added to, edited, or deleted by the reflector/curator. These take
# precedence over anything in the learned playbook. (Merged from the former
# concrete + abstract seeds; concrete = specific rules, abstract = principles.)
# ==========================================================================

## OUTPUT FORMAT & DELIVERABLE RULES (concrete)
[rb-fmt-01] :: Produce the requested artifact itself as the deliverable — the actual document, table, dataset, code, or file, with its specified name, structure, sections, and fields — not a summary, outline, plan, or a description of how you would make it.
[rb-fmt-02] :: Reproduce every element the task explicitly names (titles, sections, columns, fields, ordering, units, format) exactly, and fill in all required content; do not merely describe what each part should contain.
[rb-fmt-03] :: When you cannot emit a real binary or interactive file, render its full content faithfully in text (each sheet/section as a labeled table or block, with all headers, values, and formulas shown) so nothing required is omitted.
[rb-fmt-04] :: Never open or pad the deliverable with statements about what you cannot do or how the reader should use it (e.g. "I can't produce this file", "copy the following into..."). If the requested artifact is a form this channel cannot emit directly (binary, interactive, image), render its full content as text and present THAT as the artifact itself; keep any note about capability limits out of the deliverable entirely (put it in reasoning if it matters).
[rb-fmt-05] :: The artifact contains content, not reasoning about content. No meta-commentary, no 'this section would include…', no placeholders — every slot holds the real value.
[rb-fmt-06] :: Get straight to the point from the very first line: open with the substance of the deliverable itself (its title, first section, or first result) — no preamble, greeting, task restatement, or "Here is / Below is" warm-up before the content begins.

## TOOL & API USAGE (concrete)
[rb-api-01] :: Read a tool/API's specification (required parameters, argument names, return shape) before the first call; do not guess argument names or types.
[rb-api-02] :: For paginated or batched results, iterate over all pages/batches (e.g. increment the page index until it is exhausted) instead of using only the first.
[rb-api-03] :: The content of any attached or referenced file is already included in the task text (spreadsheets as tables, images transcribed). Read and use that provided content exactly before writing; never ignore, approximate, or simulate it.

## FORMULAS & CALCULATIONS (concrete)
[rb-calc-01] :: In multi-step calculations, show intermediate values and carry units through each step; round only at the final answer, not mid-calculation.
[rb-calc-02] :: Guard computations against error and edge cases: handle division-by-zero, empty/None, and out-of-range inputs, and flag missing or invalid data before finalizing the result.

## COMMON MISTAKES TO AVOID (concrete)
[rb-err-01] :: Do not perform an irreversible or destructive action (delete, send, overwrite, purchase, submit) without first confirming the exact target and that the action is actually required.
[rb-err-02] :: Retrieve before you hedge: if a value is in an attachment or reachable by tool, open it. Only after retrieval fails may you mark a value unavailable — and never replace a required value with commentary about it.
[rb-err-03] :: If the response format is structured (JSON, XML, CSV), it MUST parse. Escape every quote/newline/backslash inside string fields, emit exactly one well-formed object with nothing before or after it, and double-check that a long embedded document has not broken the envelope.

## VERIFICATION CHECKLIST (concrete)
[rb-chk-01] :: Before finalizing, re-read the original request and confirm every explicit requirement and constraint (each named element, format, scope, and limit) is present and satisfied.

## GENERAL PRINCIPLES (abstract)
[rb-prin-01] :: Understand precisely what the task is asking for — its goal, its deliverable, and its constraints — and respond in exactly the form the task requires.
[rb-prin-02] :: Deliver the thing itself, not a description of the thing: when a work product is requested, produce the product, not a report about how you would produce it.
[rb-prin-03] :: Restate the goal in your own terms before acting; solving the wrong problem well is the most expensive kind of error.
[rb-prin-04] :: Prefer the smallest reversible step that yields new information, and verify it worked before committing to the next step.
[rb-prin-05] :: Separate your internal reasoning (scratchpad) from the final artifact; the delivered artifact should be clean, direct, and self-contained.
[rb-prin-06] :: Favor concrete specifics over abstract description: fill in the actual values, worked step-by-step calculations, and exact references (named sources, formulas, identifiers) instead of restating what the answer "would" contain. A precise, fully-instantiated answer beats a correct-but-generic one.

## PROBLEM-SOLVING HEURISTICS (abstract)
[rb-prob-01] :: Decompose a complex task into ordered sub-goals; satisfy the explicit, verifiable requirements first and leave optional polish for last.
[rb-prob-02] :: When blocked, isolate the root cause before attempting fixes; treat symptoms only once you can name the underlying cause.

## TRANSFERABLE STRATEGIES (abstract)
[rb-strat-01] :: Gather the relevant context or specification before producing output rather than acting on unstated assumptions.
[rb-strat-02] :: Scale the deliverable to the professional norm for its type — a memo is not a paragraph, a report is not a page. Under-delivering on length is as much a failure as missing a section.

## FAILURE PATTERNS & RECOVERY (abstract)
[rb-fail-01] :: Repeating an identical failing action rarely helps; after a failure, change the approach instead of retrying the same thing.

## SELF-VERIFICATION HABITS (abstract)
[rb-verif-01] :: Before declaring the task done, check the result against every explicit requirement and against one independent sanity check; an unmet named constraint fails the task even if the overall approach is sound.

**Operations** — ADD creates a new bullet under a section (the system assigns the bullet_id, so do not include one in `content`).

**Respond with ONE valid JSON object and nothing else — no markdown, no code fences:**
{
  "reasoning": "[brief analysis]",
  "operations": [
    {"type": "ADD", "section": "formulas_and_calculations", "content": "[new insight...]"}
  ]
}

# + NEW AT v4: standing anti-disclaimer order + the DELETE operation it introduces
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
  {"type": "DELETE", "bullet_id": "<that bullet's id>", "reason": "capability-disclaimer bullet"}
Protected/seed bullets are never deleted — do not target them.
````

> The `# + NEW AT v4:` comment lines are annotations added for this doc — they are **not** in the
> real prompt. Everything else is verbatim. The remaining sub-sections below zoom into the three
> pieces that matter most.

### A new immutable RULEBOOK is now injected into the Curator prompt

At v3 there was no rulebook. At v4 the prompt gains a RULEBOOK block the agents may never edit
(verbatim, `+` = new at v4):

```diff
+ ## RULEBOOK — immutable ground rules; respect them, and NEVER add, edit, delete, or target these ids:
+ # ==========================================================================
+ # RULEBOOK — immutable, must-follow rules. Checked on EVERY task. A bible for every run.
+ # Never added to, edited, or deleted by the reflector/curator. These take
+ # precedence over anything in the learned playbook. (Merged from the former
+ # concrete + abstract seeds; concrete = specific rules, abstract = principles.)
+ # ==========================================================================
+ ## OUTPUT FORMAT & DELIVERABLE RULES (concrete)
+ [rb-fmt-01] :: Produce the requested artifact itself as the deliverable …
+   … (27 rules total, carrying NO helpful/harmful counters) …
```

Note the last parenthetical: the rulebook was **built by promoting the former concrete + abstract
seeds** into immutable rules. The anti-hedging seeds from v3 (e.g. `fmt-00014`) became `rb-fmt-*`
rules that no longer carry counters and can never be tagged, pruned, or overridden.

---

## Change 2 — the DELETE operation (this is the one you asked about)

### v3: ADD is the only operation

```diff
  # v3 curator — operations block:
  **Available Operations:**
  1. ADD: Create new bullet points with fresh IDs
      - section: the section to add the new bullet to
      - content: the new content of the bullet …
- # (no DELETE, no UPDATE — ADD is the only thing offered, and the baseline DELETE
- #  handler in code was an unimplemented stub that silently dropped the op)
```

### v4: DELETE is offered *and* implemented

At v4 the DELETE operation is introduced through the standing-maintenance block (verbatim, `+`):

```diff
+ MAINTENANCE (cleanup): scan the CURRENT playbook shown above. For every EXISTING
+ non-protected bullet that matches the pattern above, emit a DELETE operation:
+   {"type": "DELETE", "bullet_id": "<that bullet's id>", "reason": "capability-disclaimer bullet"}
+ Protected/seed bullets are never deleted — do not target them.
```

> **Nuance worth marking precisely.** The main response-format JSON template at v4 still shows only
> an `ADD` example — the `DELETE` shape is defined in the MAINTENANCE block, not the schema example.
> But it is a first-class, implemented operation: the harness code (`playbook_utils.py`) now marks
> and drops DELETE'd bullets on rebuild, where the baseline stub had silently ignored them.

### Proof it actually fired in the v4 run

DELETE was not just offered — the v4 run emitted real DELETE operations. **6 single-curator calls on
Jul 16 emitted a DELETE.** A real example (`curator_gdpval_s_11_curate_single_20260716_160135_184.json`):

```json
{
  "reasoning": "… I am also deleting two existing bullets that violate the 'no capability disclaimer' rule.",
  "operations": [
    { "type": "ADD", "section": "OUTPUT FORMAT & STRUCTURE RULES", "content": "…" },
    { "type": "DELETE", "bullet_id": "fmt-00021", "reason": "capability-disclaimer bullet" },
    { "type": "DELETE", "bullet_id": "…", "reason": "capability-disclaimer bullet" }
  ]
}
```

This is the first point in the project where the notebook could **shrink**, not just grow.

---

## Change 3 — Generator: JSON envelope → raw deliverable

The Generator prompt changed too — and this was a deliberate v4 fix. Workreport 260716 records the
reason: *"a lot of answers were shorter than pure LLM, so I deleted the JSON frame for generator and
made it a raw answer → slightly got better."* The JSON wrapper was truncating long deliverables and
forcing the model to spend tokens on escaping instead of content.

> Both v3 and v4 Generators already carried a **trust-level header** (the CONCRETE/ABSTRACT reading
> guidance) — that predates v4. What changed at v4 is the **output contract**: how the deliverable is
> emitted, and how bullets are cited.

### 3a. Trust header renamed to match the new rulebook

```diff
  # v3 (dual era):
- **How to read the playbook — it has up to two blocks, by TRUST level:**
- - **STANDING RULES** (at the END, if present) — verified, must-follow rules. … they take precedence over everything above.
- - **LEARNED HINTS** (above) — patterns extracted from PAST tasks; some may not fit …

  # v4 (single + rulebook):
+ **How to read what you are given — two blocks by TRUST level:**
+ - **RULEBOOK** (at the END) — immutable, must-follow ground rules. Hard constraints: violating any one fails the task … Always check them.
+ - **LEARNED PLAYBOOK** (above) — hints extracted from PAST tasks …
```

### 3b. Output contract: JSON `final_answer` → the raw deliverable itself

**v3 instructions + output schema (verbatim):**

```diff
- - Keep `reasoning` SHORT — it is a private scratchpad and is NOT graded. Spend your effort on `final_answer`.
- - `final_answer` is the ONLY thing evaluated: make it the COMPLETE, detailed deliverable … written out IN FULL at professional length …
- - **OUTPUT INTEGRITY (mandatory):** your ENTIRE response must be ONE valid, parseable JSON object and nothing else … Inside string values (ESPECIALLY the long `final_answer`), escape every special character correctly: `\"` … `\n` … `\\` … A long embedded document … must NOT break the JSON envelope.
-
- Your output should be a json object, which contains the following fields:
- - reasoning: a BRIEF private scratchpad … NOT graded — keep it short.
- - bullet_ids: … include their bullet_id in this list
- - final_answer: the COMPLETE, detailed deliverable IN FULL … This is the graded output; put the entire work product here.
```

**v4 instructions (verbatim):**

```diff
+ - Plan briefly in your head, but DO NOT print any planning or scratchpad. Your output is the deliverable only.
+ - Your ENTIRE output IS the graded deliverable: the COMPLETE actual work product (document, table, analysis, memo, …) written IN FULL at professional length — never a summary, outline, plan, or hedge. If the task implies a long document, write the long document.
+ - Write it as plain text / markdown, exactly as it should be delivered to the client. NO JSON, no wrapper object, no code fences around the whole thing.
+ - CITATION LINE: after the deliverable, on the VERY LAST line, list the bullet_id of every rulebook/playbook bullet you actually used — each wrapped in square brackets — prefixed with `CITED:`. If you used none, write just `CITED:`. This line is NOT part of the deliverable; it is stripped before grading. Do not write bullet ids anywhere else.
```

**What changed, in one line:** the deliverable stopped being a string *inside* a JSON object
(`{"reasoning":…, "bullet_ids":[…], "final_answer":"…"}`) and became **the entire response**, with
citations moved to a trailing `CITED: [id] [id]` line that is stripped before grading. The
`reasoning` and `bullet_ids` fields are gone; `final_answer` is gone as a field because the whole
output *is* the answer.

> **Why this matters beyond tidiness.** The JSON envelope was a real failure source: long documents
> with quotes/tables/newlines broke the parse, and the baseline extractor needed a hand-rolled
> unterminated-string recovery path. Removing the envelope removed that entire failure mode — and,
> per the workreport, nudged deliverable length back up toward the pure-LLM baseline.

---

## Change 4 — the STANDING WARNING against re-learning disclaimer bullets

New at v4 (verbatim, `+`). This is what gives the Curator a *standing* reason to issue the DELETEs
above, rather than waiting for a reflection to mention them:

```diff
+ **STANDING WARNING — do NOT (re)learn capability-disclaimer bullets:**
+ The most recurring harmful bullet in this playbook is the "acknowledge the
+ limitation" pattern. NEVER ADD a bullet that advises the writer to:
+ - state it "cannot" / "is unable to" produce a file or binary/interactive artifact,
+ - add a "professional note" or disclaimer about a platform/format limitation,
+ - tell the reader to copy the content into another application, or
+ - assume tools/code the environment does not provide (e.g. a Python interpreter,
+   python-pptx, openpyxl, "generate the file programmatically").
+ These FEEL helpful but LOWER the score: the grader marks the artifact/format
+ criteria as unmet precisely because the deliverable announced it could not
+ produce them. The correct rule is ALWAYS: render the full content directly as
+ the deliverable, with no disclaimer.
```

> **Not yet at v4 (added at v5):** the second standing warning — *"never write the word 'rubric'"* —
> and its matching DELETE-cleanup. At v4 there is exactly **one** STANDING WARNING (disclaimer only).

---

## Change 5 — Reflector output: two insights, filed by section

The dual reflector (concrete + abstract, two calls) becomes one reflector emitting both a
`concrete_insight` and an `abstract_insight`, which the single Curator files into the matching
concrete-type or abstract-type section. (This is the mechanism the new Philosophy line above
describes.)

---

## Summary — exactly what was touched at v3 → v4

| Component | Change at v4 | Evidence |
|---|---|---|
| **Playbook structure** | 2 playbooks → **1 playbook + immutable RULEBOOK** (built from the old seeds) | Philosophy line rewrite; RULEBOOK block injected |
| **Curator operations** | ADD-only → **ADD + DELETE** (offered *and* implemented) | DELETE format in MAINTENANCE block; 6 real DELETEs emitted in the run |
| **Curator standing order** | none → **STANDING WARNING** to DELETE capability-disclaimer bullets | verbatim block, absent in v3 |
| **Generator output** | JSON `{reasoning, bullet_ids, final_answer}` → **raw deliverable + `CITED:` footer** | v3 vs v4 generator logs; workreport 260716 |
| **Reflector output** | concrete/abstract via two calls/playbooks → one call, `concrete_insight` + `abstract_insight` filed by section | Philosophy line |

**Still NOT present at v4** (added at v5): the anti-**"rubric"** standing warning + its DELETE
cleanup (v4 has only the disclaimer warning). At v4 the key unlocks are: **for the first time the
notebook can lose a bullet, an immutable rulebook sits above the mutable playbook, and the generator
emits the raw deliverable instead of a JSON-wrapped `final_answer`.**

> **Correction to earlier notes.** The raw-deliverable / `CITED:` generator rewrite was previously
> attributed to v5 (because the prompt files were only committed to git at v5). The run logs and
> workreport 260716 place the actual change at **v4** — the git commit lagged the work.
