# Self-Improving Feedback Loop — Simplified Spec (abstraction-free)

A test-time, no-retraining feedback loop for Hermes Agent that evolves a flat memory of
reusable workflows.

**This is the abstraction-free variant.** Compared to the full spec it drops everything from
AWM: no `abstract`/`concrete` axis, no `SectionProfile`, no promotion, no compression, no
link/children, no per-level counts, no `harm_reason` (content/fit) split. A workflow is simply
a **strategy** or a **preventive** guardrail; it accrues `helpful`/`harmful`; that is the whole
memory model.

## Sources

| Source | Contribution | Lands in |
| --- | --- | --- |
| **ACE** | Loop engine: Generator/Reflector/Curator, itemized playbook, delta updates, dedup, helpful/harmful counters | M1, M4, M5, M6 |
| **ASSAY** | Immutable predefined seed library (attribution dropped) | M0 seed init |
| **SGV** | Synthesize a verdict where no label/verifier exists | M3 |
| **ReasoningBank** | Distill *preventive* guardrails from failures (`kind` axis) | M4 branch + `kind` |
| **(ours)** | Evidence-grounded (decisive-step) credit attribution | M4 |

Mental model: **ACE runs the loop, ASSAY lays the foundation, SGV manufactures a verdict where
the benchmark gives none, ReasoningBank mines failures for guardrails.**

---

## 1. Confirmed design decisions

1. **ASSAY contributes seed only.** Predefined skillset initializes the Playbook; causal
attribution / per-task masking dropped.
2. **Seed is immutable.** Exempt from masking and modification (protected region).
3. **One unit: `workflow`.** No abstraction axis. Size is step count, not a type.
4. **Two-layer object model.** A **Playbook** holds **workflows** (the only unit).
5. **Itemized, vector-indexed store, not a prose blob.** Enables surgical deltas + top-K.
6. **Generator sees a retrieved top-K subset.** Seed is **pinned** (always injected); learned
workflows fill the remaining K / budget.
7. **Learning is confidence-gated.** M3 always yields a `Signal`; learning fires when
`signal.confidence ≥ τ`. Real verifiers pass at 1.0; SGV verdicts pass only when confident.
Confidence lives only at the M3 signal + τ gate — not carried per-workflow.
8. **SGV is a ground-truth synthesizer (M3).** For benchmarks lacking a label/execution
verifier, SGV produces `{verdict, confidence}` so downstream sees a uniform signal.
9. **Preventive dimension via a `kind` axis.** `kind ∈ {strategy, preventive}`. Failures yield
`kind=preventive` guardrails (ReasoningBank), stored in a pitfalls section.

---

## 2. Object model

```
workflow (the only stored unit)
  ├── id: str
  ├── title: str                       short handle (embeds well)
  ├── description: str                  one-line summary (embeds well)
  ├── content: str                      routine / guardrail text
  ├── section: str                      domain/section tag
  ├── kind: "strategy" | "preventive"   do vs avoid
  ├── origin: "seed" | "learned"
  ├── protected: bool                   true ⟺ origin == seed
  ├── helpful_count: int
  ├── harmful_count: int
  ├── created_at: timestamp
  └── embedding: vector                 over title+description+content
```

- `origin=seed ⟹ protected=true`. M5/M7 never edit/remove seed (counters may still update).
- **helpful/harmful are usage counters, not content-quality labels.** A preventive workflow
("verify page id before load-more") can hold `helpful=6 harmful=0` — the guardrail helped 6×.

---

## 3. Module architecture

| # | Module | Responsibility | Interface | Implementation location | Swappable |
| --- | --- | --- | --- | --- | --- |
| M0 | **Playbook store** | Persist workflows; vector index; seed init | CRUD + `search(q,k)` | SQLite/vector store + seed loader | store backend |
| M1 | **Retriever** | Top-K subset (seed pinned) | `retrieve(task,k,budget)->[Workflow]` | Scoring fn over M0 (§5) | weights |
| M2 | **Generator** | Execute on subset; emit trajectory | `run(task,subset)->Trajectory` | Adapter over **Hermes** (MCP) | agent |
| M3 | **Signal** | Verdict+confidence for a trajectory | `get_signal(task,traj)->Signal` | `env` adapter **or** `sgv` (§6) | backend/benchmark |
| M4 | **Reflector** | Attribute credit; distill strategy/preventive | `reflect(traj,subset,signal)->Reflection` | ACE Reflector **prompt** + attribution (§7) | LM + prompt |
| M5 | **Curator** | Merge deltas; dedup; conflict; route preventive | `integrate(reflection,playbook,τ)->Playbook` | ACE Curator **prompt** + deterministic apply (§8) | LM + prompt |
| M6 | **Counters** | Apply helpful/harmful | inside M5 apply | Deterministic | rule |
| M7 | **Refine (offline)** | Dedup + prune stale/harmful | `refine(playbook)` | Hermes 7-day cycle | on/off |

M0, M1, M3, M4, M5, M6 are new code. M2 adapts Hermes. M7 is scheduled.

---

## 4. Data types

```
Trajectory:
  steps: list                 # ordered actions/tool calls/observations, each with an index
  consulted_ids: list[str]    # retrieved workflows the agent actually used
  outcome_raw: any            # env-native result if any

Signal:
  verdict: "success" | "failure"
  confidence: float           # [0,1]; env backend = 1.0
  source: "env" | "sgv" | "self_consistency"
  rubric: list[str] | null    # SGV criteria (audit)

Attribution:                  # one per non-neutral consulted workflow
  workflow_id: str
  contribution: "helpful" | "harmful"     # neutral attributions are omitted
  decisive_step: int          # index into Trajectory.steps; the evidence

Candidate:                    # a proposed memory item
  title, description, content, section: str
  kind: "strategy" | "preventive"
  update_target_id: str | null           # non-null ⟹ propose UPDATE of an existing workflow

Reflection:                   # M4 output
  lessons: str                # diagnostic notes (not stored)
  candidates: list[Candidate]
  attributions: list[Attribution]

DeltaOp:                      # M5 output, applied deterministically
  type: "ADD" | "UPDATE" | "REMOVE"
  target_id: str | null
  payload: dict | null
```

---

## 5. M1 — Retriever

```
score(w) = α·sim(task,w) + β·usefulness(w) + γ·freshness(w),   α ≫ β,γ
usefulness(w) = (helpful+s)/(helpful+harmful+2s),  s≈1
```

- **sim**: cosine(task, title+description+content embedding); optional BM25 hybrid for exact
tokens (API/tool names). Dominant term.
- **usefulness**: Laplace-smoothed reliability (new (0,0) workflow → neutral 0.5).
- **freshness**: mild recency bonus.

Return top-K by score, capped by `budget`. **Seed = Pin:** seed workflows bypass scoring and
are always injected; only learned workflows compete for the remaining K / budget. **`kind`:**
preventives compete in the same score; optional `reserve_preventive_k` reserves a slice so
"what to avoid here" always surfaces (default off).

---

## 6. M3 — Signal (SGV as ground-truth synthesizer)

M3 selects a backend per benchmark and **always returns a `Signal`**.

- **`env`**: deterministic verifier adapter (tests, exit codes, DB-state/reward, answer match)
→ `{verdict, confidence=1.0, source="env"}`.
- **`sgv`**: two sequential LLM calls with **enforced isolation**:
    1. *Rubric call.* Input = **task spec only** (trajectory withheld at the call boundary — the
    isolation defeats agreement bias; enforce in code). Output = `rubric`, criteria derived
    from and quoting the task's explicit deliverables/constraints (R1, §11).
    2. *Verify call.* Input = task + rubric + trajectory → `{verdict, confidence}`.
- **`self_consistency`** (discrete-answer tasks only, e.g. BrowseComp): N rollouts; answer
agreement → confidence.

`confidence` is the single downstream knob; weak/ambiguous cases fall below τ and are not
learned from (§11).

---

## 7. M4 — Reflector: credit attribution + extraction

M4 does two jobs. It sees at most the retrieved subset, never the whole Playbook.

### 7a. Attribution (evidence-grounded, single layer)

Goal: credit tied to the steps that mattered, not blanket "it was in context."

1. **Decisive-step identification.** Name the trajectory steps that actually determined the
outcome; ignore the rest for attribution.
2. **Usage filter.** Only workflows in `consulted_ids` are candidates.
3. **Per-workflow contribution, tied to evidence.** For each consulted workflow: **helpful**
(influenced a decisive step that went right), **harmful** (following it caused/contributed
to a decisive step that went wrong), or **neutral** (consulted but not tied to any decisive
step → **omitted**, no counter change). Each emitted `Attribution` cites its `decisive_step`.
4. **Confidence gating.** Attributions are honored by M6 only if `signal.confidence ≥ τ`.

**Prompt-structure requirement:** (i) list decisive steps + why; (ii) per consulted workflow,
cite the affected step and its success/failure → contribution; (iii) emit strict JSON =
`Reflection`.

### 7b. Extraction (strategy vs preventive)

Branch on `signal.verdict`:

- **success** → `Candidate`s with `kind="strategy"` capturing the reusable positive routine.
- **failure (ReasoningBank branch)** → ≥1 `Candidate` with `kind="preventive"` in the canonical
guardrail form **"When/Before ⟨trigger⟩, ⟨check or avoid⟩ to prevent ⟨failure mode⟩."** This
is the exact ReasoningBank borrow point — a prompt-level conditional, not a new module.

ReasoningBank splits: **extraction → M4 prompt (here); representation → `kind` field + M5
pitfalls routing.** It depends on M3's verdict, so a low-confidence "failure" is silenced by the
same τ.

---

## 8. M5 — Curator (integration) + M6 counters

**Op generation (LM).** The Curator prompt turns `candidates` into `DeltaOp`s: ADD only
genuinely new workflows to the correct section, route `kind=preventive` to the pitfalls section
(e.g. `## COMMON MISTAKES TO AVOID`), avoid duplicates, keep edits localized, never wholesale-
rewrite. `update_target_id` → UPDATE.

**Apply (deterministic).** DeltaOps apply mechanically to M0. Protected (seed) ids are skipped
for UPDATE/REMOVE.

**Dedup.** Two layers, both on: prompt discipline + embedding analyzer (merge workflows with
cosine similarity ≥ 0.9). With no promotion machinery, dedup is the sole bloat control, so run
both.

**M6 counters (deterministic, inside apply, gated by confidence ≥ τ).** Per `Attribution`:
`helpful++` on helpful, `harmful++` on harmful. Seed counters may update; seed never removed.

---

## 9. M7 — Refine (offline)

Rides Hermes's 7-day cycle. LM pass that catches semantic duplicates the analyzer missed and
archives stale / consistently-harmful **learned** workflows. Seed exempt.

---

## 10. Decisions — all resolved

No open decisions. Seed retrieval = Pin (hybrid pin+floor only if the seed set grows large).

Suggested implementation order: M0 (store + seed loader) → M1 (retriever) → M2 (Hermes adapter)
→ M3 (signal: env first, then sgv) → M4 (reflector: attribution then extraction) → M5/M6
(curator + counters) → M7 (offline refine).

---

## 11. SGV failure mode — rubric irrelevance (recommendations)

Inverting the order (rubric before answer) removes agreement bias but adds risk: the rubric can
be off-target. Recommendations, not locked:

> **R1 — ground the rubric in the task.** Extract and quote explicit deliverables/constraints
first, then derive one rubric item per requirement. In the M3 `sgv` rubric prompt.
**R2 — keep τ conservative so ambiguity fails safe.** Shaky rubrics yield low confidence; high
τ drops them below the gate. "When unsure, don't learn."
**R3 — cross-verify the rubric.** Generate k×, keep recurring items only. Cheap; enable
selectively.
> 

Fallback = decision 7: if rubric trust can't be established, high τ leaves such tasks frozen.
SGV is a signal source, never a τ override.

---

## 12. Benchmark signal mapping

| Benchmark | M3 backend | Signal |
| --- | --- | --- |
| SWE-Bench Verified | env | test pass/fail |
| Terminal-Bench 2.0 | env | execution verification |
| OSWorld-Verified | env | per-task verifier |
| τ-bench | env | reward / DB-state + policy |
| BrowseComp | env or self_consistency | answer match |
| AutomationBench | env | success criteria |
| **GDPval** | **sgv** | synthesized verdict, gated by confidence ≥ τ |

Downstream modules read only `Signal`; row differences vanish past M3.

---

## 13. The loop

```mermaid
flowchart TD
    SEED["ASSAY seed (immutable)"] -->|M0 init| PB[("M0 Playbook<br/>flat workflow store")]
    PB -->|M1 top-K (seed pinned)| GEN["M2 Generator (Hermes)"]
    GEN -->|Trajectory + consulted_ids| SIG["M3 Signal<br/>env OR SGV → {verdict, confidence}"]
    SIG -->|confidence ≥ τ| REF["M4 Reflector<br/>decisive-step credit<br/>+ strategy/preventive extraction"]
    SIG -->|confidence < τ| PB
    REF -->|Reflection: candidates + attributions| CUR["M5 Curator<br/>delta · dedup(0.9) · route preventive · skip seed"]
    CUR -->|apply + M6 counters| PB
    OFF["M7 Refine (offline)"] -.->|dedup / prune| PB
```

---

## 14. Provenance of borrowed techniques → location

| Technique | Source | Location |
| --- | --- | --- |
| Generator/Reflector/Curator roles | ACE | M2/M4/M5 |
| Itemized playbook + delta ops | ACE | M0 + M5 |
| helpful/harmful counters | ACE | schema + M6 |
| Embedding dedup (0.9) | ACE analyzer | M5 apply |
| Top-K retrieval | ACE third-party | M1 |
| Immutable seed | ASSAY (seed only) | M0 + `protected` |
| Two-step self-verification | SGV | M3 `sgv` (isolation in code) |
| Preventive-from-failure | ReasoningBank | M4 prompt branch + `kind` + M5 pitfalls |
| title/description schema | ReasoningBank | schema; M1 embeds |
| Decisive-step credit attribution | ours | M4 §7a |

---

## 15. Hermes integration notes

- **M2 = Hermes** via MCP; test-time, no retraining.
- **Within-session context compaction** is Hermes's own concern; the loop is cross-episode.
- **Seed immutability** ⟶ a protected region Hermes's self-improvement engine cannot edit.
- **Per-session caching** is no longer a conflict (causal masking gone); Playbook ops land at
session boundaries or in M7.

---

## Summary

- A flat, abstraction-free module chain (M0–M7). One unit (`workflow`), one counter layer
(`helpful`/`harmful`), no promotion/compression/SectionProfile.
- **M3 SGV** synthesizes verdicts so every benchmark yields a `Signal`, gated by τ.
- **M4** attributes credit via decisive-step evidence (single layer) and extracts
`kind=strategy` on success / `kind=preventive` guardrails on failure (ReasoningBank).
- **M5** integrates as deltas, dedups (0.9), routes preventives to the pitfalls section; seed is
never modified.
- All decisions resolved; implement in order M0 → M1 → M2 → M3 → M4 → M5/M6 → M7.