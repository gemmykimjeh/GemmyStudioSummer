# v2 → v3 — The Real Prompt/Seed Changes, Marked

> **How this was reconstructed.** The v3 edits were **never committed to git** — developed and run
> locally, committed only later (at v5). So the authoritative record is the run itself. Every claim
> below is a **direct v2-vs-v3 diff** of the actual `detailed_llm_logs/` and playbook snapshots:
>
> | Era | Reflector / Curator log | Playbook snapshot | Counter format |
> |---|---|---|---|
> | **v2** | `..._concrete_20260715_160834_914.json` (Jul 15 16:08) | `_bak_..._concrete.txt.1784169020` (Jul 15 19:55) | **integer** |
> | **v3** | `..._concrete_20260716_113551_529.json` (Jul 16 11:35) | `_bak_..._concrete.txt.1784180909` (Jul 16 12:54) | **float** |
>
> Task shown for prompts: `s_5` (a CleanTech CTO design-doc task; content irrelevant).

**⚠️ Correction to the informal description.** Comparing the two runs directly, only **part** of
what is usually attributed to v3 was actually new at v3. The precise picture:

| Claimed v3 change | What the diff actually shows | New at v3? |
|---|---|---|
| Anti-hedging / disclaimer seed bullets | 3 of the 4 (`fmt-00003`, `fmt-00012`, `err-00009`) **already existed at v2**. Only **`fmt-00014`** — the strongest "never write a disclaimer" bullet — was **added** at v3. | **Partly** — one bullet added |
| Reflector capability-disclaimer clause | The `Focus (CONCRETE)` header existed at v2; v3 **appended two sentences** to it. | **Yes** |
| Reflector/Curator see grader's per-criterion feedback | The `Criteria NOT met` list was **already in v2** (and in baseline). What changed is the **counting**: helpful/harmful counters went from integer ±1 to **grader-score-weighted floats**. | **Yes, but it's the counting, not the miss-list** |

---

## Change 1 — one seed bullet added (`fmt-00014`)

Exact ID presence, counted in the two snapshots:

| Seed bullet | v2 (Jul 15) | v3 (Jul 16) |
|---|:---:|:---:|
| `fmt-00003` "render full content in text" | ✅ present | ✅ present |
| `fmt-00012` "artifact is content, not reasoning" | ✅ present | ✅ present |
| `err-00009` "retrieve before you hedge" | ✅ present | ✅ present |
| **`fmt-00014` "never open/pad with what you cannot do"** | ❌ **absent** | ✅ **added** |

**The bullet that was added at v3** (verbatim, `seed_concrete_playbook.txt`):

```diff
+ [fmt-00014] :: Never open or pad the deliverable with statements about what you cannot do or how
+ the reader should use it (e.g. "I can't produce this file", "copy the following into..."). If the
+ requested artifact is a form this channel cannot emit directly (binary, interactive, image),
+ render its full content as text and present THAT as the artifact itself; keep any note about
+ capability limits out of the deliverable entirely (put it in reasoning if it matters).
```

The three that were **already there at v2** (shown for context, not changed at v3):

```text
  [fmt-00003] :: When you cannot emit a real binary or interactive file, render its full content faithfully in text …
  [fmt-00012] :: The artifact contains content, not reasoning about content. No meta-commentary … no placeholders …
  [err-00009] :: Retrieve before you hedge: if a value is in an attachment or reachable by tool, open it …
```

---

## Change 2 — two sentences appended to the Reflector's CONCRETE header

This is the only edit to the Reflector prompt itself. The first two sentences existed at v2; v3
appended the anti-disclaimer clause (marked `+`):

```diff
  You are an expert analyst and educator. Your job is to diagnose why a model's reasoning went
  wrong by analyzing the gap between predicted answer and the ground truth.

  **Focus (CONCRETE):** Produce SPECIFIC, situation-tied insights — exact APIs, parameters,
  formulas, error signatures, and step-level fixes tied to THIS trajectory. Do not generalize away
  the concrete detail.
+ Recommend only fixes achievable with the means this environment actually provides — never
+ prescribe a capability the attempt had no access to (running code, external tools, or emitting a
+ file type the channel cannot produce), and never conclude the deliverable should announce what it
+ could not produce: such a capability disclaimer is a defect to remove, not a rule to add, and the
+ fix for a missing artifact is to render its full content as text. Tag any bullet that encouraged
+ such a disclaimer or an unavailable-capability workaround as harmful.
```

Everything after this header (the `**Instructions:**` list, the output schema) is **unchanged from
v2**.

> **Honest caveat, visible in the same v3 log.** The clause did not fully "take": on this task the
> Reflector still wrote *"it did not provide the output as a .docx file (which is impossible in this
> text-only environment, but the model should have acknowledged the constraint…)"* — half-endorsing
> a disclaimer despite the new instruction. This is why the defense had to be hardened later (v4
> immutable rulebook, v5 Curator DELETE + standing cleanup orders): a single prompt sentence was
> not enough.

---

## Change 3 — the grader feedback was already visible; the *counting* changed

### What did NOT change: the per-criterion miss list

Both v2 and v3 Reflectors receive the same `Environment Feedback` shape — score **plus** the list
of criteria not met. This was already true at v2 (and in baseline ACE):

```text
  # v2 reflector feedback (Jul 15) — miss-list ALREADY present
  **Environment Feedback:**
  Rubric score 0.00 (0/38 met; required_ok=True).
  Criteria NOT met:
  - (2 pts) The submitted deliverable is an Excel workbook file whose basename is 'Sample' …
  - (2 pts) The workbook contains a worksheet named exactly 'Sample Size Calculation' …
  …

  # v3 reflector feedback (Jul 16) — same shape
  **Environment Feedback:**
  Rubric score 0.90 (30/35 met; required_ok=True).
  Criteria NOT met:
  - (2 pts) Provides the design document as a .docx Microsoft Word file
  - (1 pts) Addresses scalability with at least one concrete approach …
  …
```

So "the Reflector can see which criteria were missed" is **not** a v3 change — do not claim it as one.

### What DID change: helpful/harmful counters became grader-score-weighted floats

The real v3 change tied to the grader is in the **counting**, provable from the playbook snapshots:

| | v2 snapshot (Jul 15 19:55) | v3 snapshot (Jul 16 12:54) |
|---|---|---|
| Counter values seen | `helpful=10 harmful=0`, `helpful=0 harmful=14`, `helpful=1 harmful=0` — **all integers** | `helpful=1.13 harmful=0.50`, `helpful=0.01 harmful=1`, `helpful=0 harmful=0.39` — **floats** |
| Fractional counters in file | **0** | **172** |

Before v3 a tag moved a counter by ±1. From v3 the move is weighted by the grader score, so a bullet
on a 0.90 task and a bullet on a 0.20 task no longer count the same. That is the concrete meaning of
"the Reflector and Curator now match the grader's scoring" — it lives in the **counter arithmetic**,
not in a new block of text in the prompt.

The Curator's prompt header, by contrast, is **byte-identical** between v2 and v3:

```text
  # IDENTICAL in v2 and v3:
  **Playbook Philosophy (CONCRETE):** You maintain a playbook of SPECIFIC, situation-tied rules —
  exact APIs, parameters, formulas, and error signatures. Preserve concrete detail; do NOT
  over-generalize. Add bullets only when they capture a concrete, reusable specific.
```

The Curator "reacts to the grader" only indirectly: it reads the Reflector's reflection (which was
written against the miss-list) and it operates on counters that are now score-weighted. No text in
the Curator prompt changed at v3.

---

## Summary — exactly what was touched at v3

| Component | Change at v3 | Evidence |
|---|---|---|
| **Seed playbook** | **+1 bullet** (`fmt-00014`, the anti-disclaimer one). The other 3 anti-hedge seeds pre-date v3. | ID present in v3 snapshot, absent in v2 |
| **Reflector prompt** | **+2 sentences** appended to the `Focus (CONCRETE)` header (capability-disclaimer clause). Nothing else changed. | header diff, v2 vs v3 logs |
| **Curator prompt** | **No text change.** | byte-identical header v2 vs v3 |
| **Counting logic** | integer ±1 → **grader-score-weighted floats** (this is the real "match the grader" change). | 0 vs 172 fractional counters |

**Not present yet at v3** (added later): the immutable **rulebook** file (v4), the Curator **DELETE**
operation and its **standing cleanup orders** for disclaimer/rubric bullets (v5). Confirmed absent:
`STANDING WARNING` / `MAINTENANCE` do not appear in the v3 curator prompt.
