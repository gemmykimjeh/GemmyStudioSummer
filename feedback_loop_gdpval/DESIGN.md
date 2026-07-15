# Rubric-Grounded Reflective Loop (RGR) — GDPval Feedback Loop v1

A minimal, ablatable feedback loop designed **specifically for GDPval's structure**, runnable
end-to-end on a **Gemini Flash free-tier key** (~2–3 LLM calls per task). Built on the existing
`test_benchmark` harness (`harness/benchmarks/gdpval.py`, `harness/agents/ace_gdpval.py`) and
the ACE loop skeleton — but it deliberately drops most of the machinery in
`ReAct_feedback_loop/feedback_loop.md` (dual playbooks, Thompson selector, 4th Verifier agent),
following AEL's own ablation finding that the simplest variant won.

---

## 0. Why GDPval needs a *different* loop than AppWorld/τ-bench

Everything below follows from four structural facts about GDPval that make the AppWorld-shaped
ACE loop a poor fit:

| GDPval structural fact | Consequence for loop design |
|---|---|
| **F1. One-shot, non-interactive.** The trajectory is `prompt → deliverable`. No tool errors, no environment steps, no intermediate observations. | ACE's step-level credit attribution (its main strength on AppWorld) has nothing to attribute over. The learning signal must come from the **grader**, not the trajectory. |
| **F2. The rubric is a per-criterion signal, not a scalar.** Every gold task ships `rubric_json`: a checklist of pointed criteria, each marked met/not-met by the grader. | We get *structured, localized* failure feedback for free — richer than AppWorld's binary reward. The Reflector should **consume failed criteria directly** instead of re-deriving lessons from a transcript. This is the single biggest GDPval-specific leverage point. |
| **F3. Almost no domain repetition.** Gold set = 220 tasks = 44 occupations × 5 tasks. An occupation-keyed memory would fire at most 4 times, ever. | Memory must generalize at the **deliverable-genre level** (report / spreadsheet / slides / analysis / communication), *not* the occupation level. Sector (9 sectors ≈ 24 tasks each) is the coarsest useful domain key; deliverable-type is better. Your existing learned playbook (`ace_playbook_gdpval.txt`) confirms this empirically: every high-`helpful` bullet is genre-level ("produce the file, not a description of the file"), none is occupation-level. |
| **F4. Failure modes are heavily clustered.** OpenAI's own analysis: the dominant model error class is *instruction following* — missing deliverables, ignored reference data, incomplete coverage of requested sections. The GDPval paper also shows increased prompting/scaffolding measurably improves scores. | A small **fixed taxonomy of failure modes** covers most of the mass. That means a large part of "learning" can be **non-LLM** (rule-based tagging + counters), reserving LLM calls for the long tail. Prompt optimization has direct evidence of working on this benchmark. |

## Sources → what we take

| Source | Idea taken | Becomes |
|---|---|---|
| **ACE** (`ace-agent/ace`) | Itemized playbook, delta apply, FAISS dedup, helpful/harmful counters | store + loop skeleton (reused as-is) |
| **ReasoningBank** (Ouyang et al., 2025) | Distill from failures *and* successes; `title/description/content` memory items; embed-and-retrieve | learned-bullet schema + retrieval |
| **AEL** `auto_distill` (your prior analysis) | Non-LLM structured extraction | the **Failure-Mode Tagger** (§3, M4) — zero-LLM-call learning path |
| **ASSAY** | Immutable seed library | the **Guardrail Library** (§3, M5): pre-written bullets *activated* by counters |
| **Dynamic Cheatsheet** (Suzgun et al., 2025) | One cumulative global memory is a strong baseline | the `__global__` section + ablation arm A1 |
| **GEPA** (Agrawal et al., 2025) | Reflective prompt evolution beats RL at tiny rollout budgets | **v2 extension only** (§8) — explicitly out of scope for v1 |

## What we *drop* from `ReAct_feedback_loop/feedback_loop.md` and why

1. **Dual playbooks (concrete/abstract)** — with ≤5 tasks per occupation there isn't enough
   signal to populate two stores; the concrete store would starve (F3).
2. **Thompson sampling selector** — 20–80 task runs give the bandit almost nothing to converge
   on; it adds variance to exactly the A/B comparison we want to be clean.
3. **LLM Verifier (4th agent)** — replaced by a *free* structural check: every Reflector insight
   must cite the `rubric_item_id`(s) that motivated it, or it is rejected deterministically
   (§3, M6). Evidence-grounding without an API call.
4. **LLM Curator call** — replaced by deterministic merge: FAISS dedup (already in ACE's
   `bulletpoint_analyzer.py`) + counter arithmetic. Saves 1 call/task on the free tier.

---

## 1. Free-tier budget (design constraint, not afterthought)

Gemini Flash free tier is rate-limited per-minute and per-day (limits shift; measure at run
time — the repo's `run_gdpval_gemini.sh` already pins `--concurrency 1`, keep that). Per-task
LLM budget for RGR v1:

| Call | Agent | Always? |
|---|---|---|
| 1 | Generator (deliverable) | yes |
| 2 | Rubric Grader (via LiteLLM proxy :4000, existing) | yes |
| 3 | Reflector | **gated**: only if `score < τ` (τ=0.7) *or* any `required` criterion failed |

→ **2–3 calls/task**; a 20-task arm ≈ 45–60 calls; both arms of an A/B fit in one free-tier
day even at conservative daily caps. All other modules are non-LLM. If the daily quota is hit
mid-run, the harness's JSONL `--resume` picks up next day with the playbook state intact.

## 2. Object model

```
Bullet (ACE-native, one new field):
  id; section; content
  metadata: { helpful, harmful, usage_count, timestamp,
              origin: "seed" | "guardrail" | "learned",
              evidence: [rubric_item_id, ...] }        # NEW — empty only for origin=seed

Sections = deliverable-type keys (NOT occupations):
  __global__ | document | spreadsheet | presentation | analysis | communication

FailureModeCounters (persisted JSON next to the playbook):
  { failure_mode: { count: int, activated: bool } }
```

Fixed failure-mode taxonomy (v1, keep to ~6; derived from GDPval's observed error classes and
your own learned playbook):

```
FM1 deliverable-missing     text summary instead of the requested file/content
FM2 format-noncompliance    wrong file type / structure (tabs, sections, slide count)
FM3 coverage-gap            requested section/topic/stakeholder absent
FM4 reference-ignored       reference-file facts not used or contradicted
FM5 quantitative-error      calculation / figure / units wrong or missing workings
FM6 unsupported-claims      no citations/sources/URLs where the prompt demands them
```

Each FM has a **pre-written guardrail bullet** (hand-authored once, like ASSAY seeds — several
already exist in `ace_playbook_gdpval.txt`, e.g. `err-00006` ≈ FM1, `ph-00008` ≈ FM6).

## 3. Module architecture (7 modules, 3 are non-LLM)

| # | Module | LLM? | Responsibility |
|---|---|---|---|
| M1 | **Router** | no | Tag task with deliverable type via keyword rules over the prompt ("spreadsheet/xlsx/tab" → spreadsheet; "slides/deck" → presentation; …). Fallback `document`. |
| M2 | **Generator** | 1 call | Produce deliverable. Context = task prompt + retrieved subset: all `__global__` bullets + bullets of the routed section, ranked by `helpful−harmful`, token-capped (start: 4k tokens of playbook). Emits `bullet_ids` used (ACE citation mechanism, already wired in `ace_gdpval.py`). |
| M3 | **Grader** | 1 call | Existing rubric grader → `{rubric_item_id: met}` + score. Unchanged. |
| M4 | **Failure-Mode Tagger** | no | The `auto_distill` analog. Map each *failed* criterion to an FM via keyword rules over the criterion text ("spreadsheet/format/.xlsx" → FM2; "cite/source/link" → FM6; …). Unmatched → `FM_other` (ignored by M5, still logged). Increment counters. |
| M5 | **Guardrail Activator** | no | When an FM counter crosses k (k=2), inject that FM's pre-written guardrail bullet into `__global__` (or the section where it fired most). Idempotent; guardrails are protected like seeds. |
| M6 | **Gated Reflector** | ≤1 call | Fires only on the gate in §1. Input: task prompt (truncated), deliverable (truncated to ~2k tokens), the **failed criteria verbatim**, and the used bullets. Output: ≤2 insights, each `{kind: strategy|pitfall, section, content, evidence:[rubric_item_ids]}` **plus** helpful/harmful tags for the used bullets. Deterministic reject rules replace the Verifier: drop any insight with empty/unknown `evidence`; drop any insight whose content contains task-specific proper nouns from the prompt (cheap regex over capitalized n-grams) — this forces genre-level generality and prevents rubric leakage (§6). |
| M7 | **Deterministic Curator** | no | Apply deltas: FAISS dedup at 0.85 against the target section (merge → increment `helpful` of survivor); append otherwise. Update helpful/harmful counters from M6 tags. Retire learned bullets with `usage_count ≥ 4 ∧ harmful > helpful`. Seeds/guardrails never retired. |

### Per-task flow

```
task ─ M1 route ─ M2 generate(playbook subset) ─ M3 grade
                                                   │
                              ┌────────────────────┤
                              ▼                    ▼
                    M4 tag failed criteria   gate: score<τ or required-fail?
                              │                    │ yes
                    M5 activate guardrails   M6 reflect (1 call)
                              └───────┬────────────┘
                                      ▼
                            M7 deterministic curate → playbook'
```

## 4. Why this memory structure (and not the alternatives)

- **Occupation-keyed memory** (natural first instinct): dead on arrival per F3 — 4 possible
  reuses per key. Rejected.
- **Single global cheatsheet** (Dynamic Cheatsheet): viable and is our ablation arm A1, but
  spreadsheet lessons ("use a second tab for workings") actively waste context — and can
  mislead — on essay-type tasks. Deliverable-type sections are the cheapest routing that
  matches how GDPval failures actually cluster.
- **Raw trajectory retrieval**: trajectories here are entire multi-page deliverables; top-1
  retrieval would blow the free-tier context/latency budget for marginal gain. Rejected.
- **Per-bullet embedding retrieval** (ReasoningBank-style top-k over all bullets): the right
  v2 move once the playbook exceeds the token cap; at v1 scale (tens of bullets) section
  routing + counter ranking is equivalent and has zero extra moving parts.

## 5. Experiment design (the point of keeping v1 small)

Four arms, one component apart each — run with the existing harness A/B machinery
(`--run-id`, `--resume`, `compare.py`), `--split train`, same task order, `--concurrency 1`:

| Arm | Config | Isolates |
|---|---|---|
| **A0** | no memory (stock one-shot, instructions only) | floor |
| **A1** | A0 + M4/M5 only (counters → guardrails; no Reflector; single `__global__`) | value of the **zero-LLM-call** learning path |
| **A2** | A1 + M6 gated Reflector + M7 | value of LLM reflection over rubric feedback |
| **A3** | A2 + M1 deliverable-type routing (full RGR) | value of genre routing vs global-only |

Protocol notes:
- **Interleave occupations** in task order (the HF gold set groups them); otherwise memory
  "warms up" inside one domain and the arms aren't comparable.
- Report score trajectory (rolling mean over task index), not just final mean — the whole
  hypothesis is *within-run improvement*.
- 20 tasks/arm is the free-tier-friendly smoke test; treat differences < ~5 points as noise
  and only interpret ordering A0→A3 after a 60–80 task run.
- Grader is itself Gemini Flash via the LiteLLM proxy → grader noise is shared across arms
  (good), but re-grade a fixed 5-task sample twice to estimate grader variance once.

## 6. Known risks / limitations (state these in the writeup)

1. **Rubric leakage.** Feeding failed criteria into memory risks memorizing the eval. Two
   mitigations are built in: tasks never repeat (F3), and M6's proper-noun reject rule forces
   insights to be genre-level. Still: never store criterion text verbatim as a bullet.
2. **Self-grading circularity.** Gemini generates *and* grades. Fine for A/B deltas (shared
   bias), not for absolute claims. If a few Anthropic-key graded tasks are affordable later,
   spot-check the ranking of arms holds.
3. **Tool-free ceiling.** The current Env is tool-free (final message = deliverable), so FM1/FM2
   guardrails can only push toward *complete inline content*, not actual `.xlsx` files. That
   caps absolute scores but does not affect the A/B question.
4. **Keyword rules (M1/M4) are brittle.** Log every unmatched criterion; if `FM_other` exceeds
   ~30% of failures, the taxonomy needs a pass — that's a finding, not a failure.

## 7. Implementation mapping (smallest diff)

All inside `test_benchmark/` — no changes to `ReAct/` or `ReAct_feedback_loop/` needed for v1:

- `harness/agents/ace_gdpval.py` — add `loop_mode: a0|a1|a2|a3` param; M1 router + M4 tagger +
  M5 activator as plain functions (~150 LOC, no new deps); M6 = one new prompt through the
  existing generator client; M7 reuses ACE's `apply_deltas` + `bulletpoint_analyzer`.
- `configs/` — one YAML per arm; `scripts/run_gdpval_gemini.sh` gains `ARM=a0|a1|a2|a3`.
- New: `harness/agents/_rgr.py` (taxonomy, guardrail library, counters persistence).

## 8. v2 candidates (explicitly deferred)

- **GEPA-lite prompt evolution**: hill-climb the per-deliverable-type Generator instruction
  using mean rubric score as fitness (population 1, mutate every 10 tasks). GEPA's
  sample-efficiency result makes this the most promising *prompt-optimization* direction, but
  it consumes rollouts the free tier doesn't have and confounds the memory ablation.
- Embedding top-k retrieval over bullets (when playbook > token cap).
- A draft→self-critique-against-prompt→revise Generator (2 calls) — biggest likely absolute
  gain, but doubles the budget; test only after A3 vs A0 is established.
