# GDPval grader — what changed vs. the original

Compares the rubric grader (LLM judge) in `test_benchmark/harness/benchmarks/gdpval.py`
between the initial commit (`18371e7`) and today. Code quoted directly from git.

---

## Problem 1 (the big one): the grader had no way to check that a file existed

### Original

The judge received the agent's **raw text** and nothing else:

```python
prompt = GRADER_TEMPLATE.format(
    prompt=task.prompt, submission=deliverable,   # <- just the agent's text
    rubric=json.dumps([...]),
)
```

There was **no file production and no file verification anywhere** in the original
grading path — no code execution, no produced-file manifest, no reference to the
gold deliverable's type.

### Why that produced invalid scores

GDPval deliverables are **files** (.xlsx / .docx / .pdf / .pptx), and the rubrics
contain criteria such as:

> "The final deliverable is a single Excel workbook with file extension .xlsx."
> "The workbook contains a table of historical annual rate increases for 2020–2024."

The agent could not produce a file, so it **pasted the content inline** — a markdown
table that *looks like* a spreadsheet. The judge, having only that text and no way to
verify anything, would **read the pasted content and award the points anyway**.

This is the invalid case: **credit granted for a file that was never created.**
Format and structure criteria ("is a .xlsx", "has these sheets/tabs", "cells are
formatted as currency") were being judged against prose that merely resembled the
artifact. The benchmark silently degraded from "produce the deliverable" to
"describe the deliverable," and scores were not honest.

### Fixed

The agent now emits code, the harness **executes it**, a **real file** is produced,
and the judge is shown a verified manifest of what actually exists on disk
(`gdpval_codegen.grader_submission`):

```python
if ft == "none" and cg.get("ok"):
    manifest = ", ".join(cg["files"])
    return (f"[The agent produced these ACTUAL deliverable files by running its code: "
            f"{manifest}. Their real extracted contents + verified STRUCTURE follow — grade "
            f"file-type and structure criteria against THIS verified manifest and content.]\n"
            f"{cg['extracted']}")
```

Crucially, failure is also reported honestly instead of being hidden. When the
agent's code is broken, the judge is **explicitly told not to give file credit**:

```python
if ft == "model":
    return ("[The agent's code FAILED to produce the required deliverable file (it is broken or "
            "incomplete). No deliverable file exists. Score every criterion that requires the "
            "file, its format, or its structure as NOT met; credit only content fully present "
            "below.]\n" + deliverable)
```

And when the *environment* (not the agent) is at fault — e.g. Windows Application
Control blocking the CAD kernel — the judge is told to grade intended content and
not penalize the missing binary:

```python
if ft == "env":
    return ("[The agent wrote code to build the deliverable file, but THIS environment could "
            "not execute it (a tooling limitation, not a content error). Grade the intended "
            "content and structure the code specifies as if it were the delivered artifact — "
            "do NOT penalize the missing binary itself.]\n" + deliverable)
```

**Net effect:** file/format/structure criteria are now scored against a real,
executed artifact — earned when the file exists, denied when it does not, and waived
only when our own tooling is provably the blocker.

---

## Problem 2: a malformed judge reply scored a good deliverable 0

### Original

One call, no retry, no temperature, no validation of the reply:

```python
g = client.messages.create(
    model=self.grader_model, max_tokens=self.max_grader_tokens,
    messages=[{"role": "user", "content": prompt}],
)
grades = _parse_grades(_text_of(g))
```

The parser returns an **empty dict** when it cannot read the reply:

```python
def _parse_grades(text) -> dict[str, bool]:
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1 or e < s:
        return {}          # malformed
    try:
        arr = json.loads(text[s:e + 1])
    except Exception:
        return {}          # bad JSON
```

and the scorer treats **any criterion absent from the dict as unmet**. So a truncated
or malformed judge reply meant *every* criterion failed → **score 0**.

Four concrete defects:

| # | Defect | Consequence |
|---|---|---|
| 1 | Malformed/truncated reply → `{}` → all criteria unmet | A good deliverable scores **0 from pure measurement noise**. Measured: a submission that scored 0.000 re-graded to **0.707** consistently |
| 2 | No `temperature` set (default, non-deterministic) | The same submission scores differently across runs — corrupts A/B comparison |
| 3 | No retry | One transient hiccup becomes the final score |
| 4 | No check on how much of the reply parsed | 3 of 94 criteria parsed still counted as a valid verdict; the other 91 auto-failed |

### Fixed — `grade_with_retry`

```python
def grade_with_retry(client, model, max_tokens, prompt, n_criteria, tries=3):
    grades: dict = {}
    need = max(1, n_criteria // 2)          # (3) at least half must parse
    for attempt in range(tries):            # (2) up to 3 attempts
        try:
            g = client.messages.create(
                model=model, max_tokens=max_tokens,
                temperature=0.0 if attempt == 0 else 0.5,   # (1) deterministic, then jitter
                messages=[{"role": "user", "content": prompt}],
            )
            grades = _parse_grades(_text_of(g))
        except Exception:                   # (4) transient error -> retry, not 0
            grades = {}
        if len(grades) >= need:
            break
    return grades
```

| Change | What it fixes |
|---|---|
| **temperature 0 on the first attempt** | Deterministic verdicts; removes run-to-run score drift (defect 2) |
| **Up to 3 attempts** | Escapes transient malformed replies (defects 1, 3) |
| **Require ≥ half the criteria to parse** | A half-read verdict is no longer mistaken for a valid one (defect 4) |
| **Jitter temperature to 0.5 on retry** | At temperature 0 a malformed reply reproduces *identically*; jitter is what lets it escape |
| **Catch transport errors and retry** | A momentary proxy drop no longer becomes an instant 0 |

**Measured effect:** FBL v5 over 100 tasks produced **4 zeros** (pure+tools on the same
pipeline: 14). Re-grading those 4 showed 3 were **consistently 0** — genuine content
failures, not noise. Zeros are now a trustworthy signal.

> Correction to an earlier claim: `max_grader_tokens` was **already 4096 in the original**.
> The "1500 → 4096" statement made earlier was wrong — that 1500 belongs to the
> image/audio description call in `gdpval_files.py` and has nothing to do with the grader.

---

## Still broken: total grader failure is silently recorded as 0

If all 3 attempts fail, `grades = {}` is **returned as if it were a verdict**, and the
caller reads it as "nothing met" → **score 0**.

> **Root cause:** "the judge said nothing" and "the judge said no to everything" are
> encoded identically. The return type `{criterion_id: bool}` has no room to express
> *measurement failed*, so failure is signalled as a degenerate value of a normal return.

**This change also introduced a regression.** The original let the exception
**propagate**, so a dead grader surfaced as a visible task error. The current code
swallows it and returns 0 — a **loud failure turned into a silent wrong number**.

This actually bit us. During the gold-ceiling measurement (2026-07-17) the proxy died,
every gold reference file came back `0.000 / n_met=0`, and the near-conclusion was
"the harness under-credits even the gold answers." Only reading the raw response
revealed `WinError 10061` (connection refused). The docstring also oversells: it says a
transient proxy error "just retries instead of scoring 0," which holds only when a
retry succeeds — after 3 failures it still scores 0.

**Recommended fix:** return the verdict *plus* whether grading succeeded (raise, or
return `None`, after 3 failures) so the caller records **"measurement failed"** rather
than 0. That makes this class of illusion structurally impossible.

**Current status:** the guard exists only in the analysis script `gold_ceiling100.py`
(health check + `grader_failed` when zero verdicts parse). **The harness itself is
still unpatched.**

---

## Summary

| | Original | Now |
|---|---|---|
| **File deliverables** | No file ever produced; judge read pasted text and **gave credit for files that did not exist** | Code is executed, a **real file** is produced, judge grades a **verified manifest**; broken code is explicitly denied file credit; environment-blocked cases are waived |
| **Malformed judge reply** | Scored **0** (measurement noise) | temperature 0 + up to 3 retries + ≥half-parsed validation |
| **Determinism** | Unset temperature → drifting scores | temperature 0 first attempt |
| **Total grader failure** | Exception propagated (visible) | **Silently scored 0** ← last remaining hole |
