# Error Analysis — How the Wrong Answers Differ, and Why They Were Produced

**Scope:** the **49 of 165** incorrect instances from the GAIA-1 reproduction run (2026-08-06)
**Sources:** our `output.jsonl` + the original archive's `output.jsonl` (165 trajectories)
**Companions:** `260806_..._delta.md` (environment) · `260806_..._results.md` (results, 9 harness principles)

---

## 0. Summary

The 49 errors fall into **two populations with different bottlenecks**.

| | n | Signature | Bottleneck |
|---|---|---|---|
| **Search / verification failures** | 39 | ≥28 steps, heavy browser use, prose answers | no layer managing beliefs |
| **Computation / reading failures** | 10 | 0 searches, 0 browser calls, terminal only | had the data, processed it wrong |

Of the nine harness principles, **§11.1, §11.2, §11.3 and §11.4 directly produced errors**.
**§11.5–§11.7 produced none — because we blocked them in advance**, which is itself the
argument for pre-flight verification.

---

## 1. How the answers differ from ground truth — six types

### Type A. No answer was submitted at all (12)

The final output is a **report**, not an answer.

```
gt   : '13'
ours : "After extensive research across Wikipedia's featured articles from 2022…"

gt   : '0.2'
ours : 'Based on my research, I found that: 1. **March 2021 paper**: Nicastro…'
```

All 12 lack the `<solution>` tag. **Three of them contained the correct answer in prose:**

```
872bfbb1  gt 'pears, bananas'   ours "…**Grapefruits, Pears, Bananas**…"
d5141ca5  gt '19/02/2009'       correct date present in the text
7a4a336d  gt '1:41.614'         correct lap time present in the text
```

### Type B. Wrong granularity (5)

The agent knew the answer but got **how much to say** wrong.

```
gt 'THE CASTLE'         ours 'INT. THE CASTLE - DAY'     (surrounding slug included)
gt 'diamond'            ours 'crystalline diamond'        (qualifier added)
gt 'Brunei, China, Morocco, Singapore'
                        ours '…, Venezuela'               (one element too many)
gt '17'                 ours '17000'                      (unit scale)
gt 'egalitarian'        ours 'Egalitarianism'             (morphology)
```

### Type C. Wrong because it *corrected* the source (1)

```
gt   : 'Picnic is in Ploybius Plaza.'
ours : 'Picnic is in Polybius Plaza.'
```

`Ploybius` is a typo in the source material; the agent **fixed the spelling** and lost the
point. The instance took only 4 steps. It read accurately, corrected sensibly, and failed.

### Type D. Quantity mismatch (31 — the largest type)

31 errors have a numeric ground truth, splitting into two very different regimes.

**Near misses (precision):**
```
0.0424 → 0.0429      17.056 → 16.919      85 → 90      +4.6 → +4.4
```

**Large deviations (enumeration failure):**
```
55 → 194      60 → 2115      4 → 20      39 → 70      16000 → 12000
```

The latter are "count everything" tasks. The agent samples and extrapolates.

### Type E. Wrong entity (11)

```
gt 'Claude Shannon'  ours 'Jerome Wiesner'   (same institution, era, and video)
gt 'inference'       ours 'element'
gt 'backtick'        ours 'grave'            (grave accent *is* a backtick — a synonym)
gt 'Rd5'             ours 'Nxc3'             (chess move)
```

All are **plausible** wrong answers — not random, but a neighbouring candidate.

### Type F. Wrong list membership (7)

```
gt 'Indonesia, Myanmar'      ours 'Myanmar, Timor-Leste'
gt 'Braintree, Honolulu'     ours 'Honolulu, Quincy'
```

`9318445f` is emblematic: in a long fraction sequence the **first 10 terms match the original
run exactly**, diverging from the 11th — and the whole episode took **2 steps**. The method was
right; it did not run to completion.

---

## 2. Agent-side — why these answers were produced

### 2.1 Trajectory statistics

| Metric | Correct (median) | Wrong (median) |
|---|---|---|
| Steps | **10** | **28** |
| Cost | $0.26 | $1.02 |
| Browser calls | **0** | **9** |
| Prose final answer | 1 / 116 | 12 / 49 |
| Hedging language | **0 / 116** | **10 / 49** |

```
Accuracy by step count
   0–10 : 85.0%      20–35 : 54.1%      60+  : 33.3%
  10–20 : 81.2%      35–60 : 29.4%
```

> **Caveat:** harder problems take more steps *and* fail more often, so step count is partly a
> proxy for difficulty. Causality cannot be asserted. What difficulty alone does not explain is
> the **flat region past 20 steps** — beyond that point additional effort stops buying accuracy.

### 2.2 Cause ① — no verification loop (the root)

The loop is `act → observe → decide`. It contains no:

- hypothesis tracking — "I believe X on the basis of source Y"
- cross-checking — "does an independent source confirm X?"
- confidence estimate — "am I sure enough to answer?"
- backtracking — "this path is wrong; restart from a different angle"

Consequently the agent stops when it has **an answer**, not when it has **evidence**. To the
policy, a *plausible* answer and a *verified* answer are indistinguishable. Every Type E error
has this shape — `Jerome Wiesner` is entirely plausible given the institution, era and source
video.

### 2.3 Cause ② — cannot switch strategy after failure

Median browser calls: **0** when correct, **9** when wrong. Browsing is the symptom of "search
did not answer it directly," and by the time it is invoked the instance is usually already lost.

Notably, the `<BROWSER_TOOLS>` prompt we pinned the commit for **is aware of this**:

> *"If 20+ total steps without converging, stop exploring and commit to your best answer."*

The **diagnosis is correct** — we measured the inflection at exactly 20 steps. But the
prescription is *"commit to your best answer"*, which, when there is no good answer, instructs
the model to **produce a confident guess**. Type A (12 prose answers) is the result.

### 2.4 Cause ③ — calibration is produced, then discarded

```
Hedging ("unable to", "approximately", "based on my research")
  Correct  0 / 116   ← not a single instance
  Wrong   10 / 49
```

The agent **knows when it does not know** — the uncertainty is right there in the output text.
But the answer space is a single string, so **"I don't know" has no channel to become an
outcome.** The signal exists internally and is discarded at the output boundary.

`0b260a57` is the extreme case: it wrote *"After conducting an extensive search using multiple
tools and approaches, I have been unable to access…"* — **explicitly declaring failure** — and
still filled in the answer field.

### 2.5 Cause ④ — cannot enumerate exhaustively

Among the 31 numeric errors, the large deviations (55→194, 60→2115, 4→20) are all tasks
requiring a **complete** count over a set. The agent's search is inherently sampling, and it has
no mechanism to decide "have I seen all of them?". The 10 matching terms in `9318445f` show the
shape: correct method, no completion criterion.

### 2.6 Cause ⑤ — multiplicative decay over hops

```
Level 1 : 79.2%      Level 2 : 69.8%      Level 3 : 53.8%
```

GAIA difficulty is effectively the number of chained hops. At ~90% per hop, five hops give
0.9⁵ ≈ 59%, close to the observed Level 3 figure. Cause ① amplifies here: with no detector for a
wrong intermediate hop, the first error propagates to the end and emerges as a **confident**
wrong answer.

### 2.7 A separate population — 10 computation failures

Ten errors used **zero searches and zero browser calls**, only the terminal:

```
50f58759 (37 steps) · 6359a0b1 · e142056d · df6561b2 · cca530fc
4d51c4bf · 99c9cc74 · cca70ce6 · ded28325 · 9318445f
```

These failed **with the data already in hand**. The bottleneck is arithmetic, reading or
transcription — not retrieval — so causes ①–⑤ do not apply. `cca530fc` (chess) submitted a move
after 8 steps and zero searches, i.e. without real analysis.

### 2.8 In one sentence

> **It searches but does not verify.**
> Problems whose answers are easy to find are solved well (85% within 10 steps); problems whose
> answers are not get **longer wandering followed by a confident, unsupported answer**
> (29% beyond 35 steps).
> The bottleneck is neither planning nor tooling — it is the **absence of a belief-management
> layer**.

---

## 3. Harness-side — which of the nine principles caused these errors

Mapping the principles in `results.md` §11 onto the observed error types.

| Principle | Role | Errors | Magnitude |
|---|---|---|---|
| §11.1 Typed egress | **caused directly** | 3 of the 12 Type A held correct answers | −1.8 pp |
| §11.2 Output contract | **caused directly** | 5 Type B + 1 Type C | −3.6 pp |
| §11.3 Partial normalisation | **caused directly** | `Egalitarianism`, `grave`, … | (overlaps §11.2) |
| §11.4 Mutable environment | **caused directly** | 6 of the 11 the original won | −3.6 pp |
| §11.5 Assert preconditions | **pre-empted** | 0 | — |
| §11.6 Runtime dependencies | **pre-empted** | 0 | — |
| §11.7 Absolute timeouts | **pre-empted** | 0 | — |
| §11.8 Report an interval | affects interpretation | not individual errors | — |
| §11.9 Unifying principle | supersedes the above | — | — |

### 3.1 §11.1 — typed egress (direct cause)

The `<solution>` tag is a **soft contract** inside free text; nothing happens if it is omitted.
12 of ours and 10 of the original's violated it, and **3 on each side were lost while holding
the correct answer.**

That the loss is identical on both sides matters: this is a **structural property of the
harness**, not of our environment.

**Structural fix:** make the answer a typed argument of the terminating tool
(`finish(final_answer: str)`). The agent then **cannot terminate** without emitting a parseable
answer, and the failure mode disappears by design rather than by a better regex.

### 3.2 §11.2 — the output contract is part of the task (direct cause)

Types B and C fall here entirely. The question does **not state the required granularity**, yet
the scorer accepts only one canonical form. The benchmark therefore measures capability **and**
format-guessing jointly, making failures unattributable.

Type C (`Ploybius` → `Polybius`) is the sharpest evidence. The agent **read the source
accurately, corrected it sensibly, and lost.** What that instance measured was not capability
but "can you infer the unstated rule that source typos must be preserved?"

### 3.3 §11.3 — partial normalisation (direct cause)

The scorer absorbs case (`Egalitarian` passes) but not morphology (`Egalitarianism` fails). It
accepts `bacon` yet rejects `grave` as a synonym for `backtick`.

It signals "surface form is irrelevant", then **punishes a different surface variation**. The
boundary is undocumented and therefore **unlearnable**.

### 3.4 §11.4 — mutable environment (direct cause)

Six of the 11 instances the original won come from search returning different sources four
months later:

```
gt 2018   original 2018   ours 1987
gt 1927   original 1927   ours 1915
gt +4.6   original +4.6   ours +4.4
```

Additionally `c61d22de` flipped False→True between two of **our own** runs at `temperature=0.0`.
**There is no fixed ground truth.**

### 3.5 §11.5–§11.7 — pre-empted, therefore absent

All three contributed **zero** errors. Not by luck — by prior intervention.

| Principle | Latent failure | Intervention | Loss avoided (est.) |
|---|---|---|---|
| §11.5 preconditions | MCP dies → agent proceeds without web search | in-container JSON-RPC handshake check | large |
| §11.6 runtime deps | `uvx mcp-server-fetch` unpinned → ImportError | pinned 2025.4.7 + mcp 1.26.0 | large |
| §11.7 absolute timeouts | ffmpeg 27 s/30 s, whisper 474 s/120 s | pre-installed into the image | the 3 mp3 instances, etc. |

The verification data supports this: tavily 852 calls with **0 errors**, 0 ffmpeg failures, and
mp3 accuracy of 2/3 — identical to the original.

**These three did not "cause errors"; they *would have*, and were blocked.** Unblocked, their
damage would have entered the score in a form indistinguishable from capability failure.

### 3.6 An extension to §11.2 — an answer space with no abstention

This analysis surfaced a point not in the original list.

The agent **produces** uncertainty (hedging in 10/49 wrong answers). But the answer space is a
single string, so **abstention is inexpressible**. The calibration signal is discarded at the
output boundary, and "I don't know" is coerced into "a wrong answer".

This can be read as a special case of §11.2, but the remedy differs: specifying the format is
not enough — the answer space must **admit `unknown`**. Scoring `unknown` as incorrect but
tallying it separately would let capability and calibration be measured apart.

---

## 4. Conclusions

### Attribution of the 49 errors

```
Harness-induced   ~10–11 (≈20%, worth ~6 pp of score)
  §11.1 extraction        3
  §11.2/§11.3 format      6
  §11.4 environment      (the dominant component of the gap vs the original)

Genuine capability ~34–38 (≈80%)
  no verification · enumeration failure · hop decay · computation errors
```

### What the agent needs

Not planning and not tools, but a **belief-management** layer: hypothesis tracking,
cross-checking, confidence, and a stopping rule.

### What the harness needs

§11.9's unifying requirement — **make "the agent was wrong" automatically distinguishable from
"the measurement was wrong", inside the artifact.** §11.5–§11.7 contributing zero errors here is
a direct consequence of spending much of a working session on that distinction; that cost should
be paid once by the harness author, not repeatedly by every user.

### Implications for the ACE experiment

If the playbook accumulates **"how to search"**, it cannot touch causes ①–⑤. It must accumulate
**"what I judged trustworthy"** — verification procedure, cross-checking habits, stopping
criteria — to address the actual bottleneck.

The metrics in this analysis then become the **instrument for attributing any ACE gain**:

| Observation | Interpretation |
|---|---|
| Accuracy rises in the ≥20-step band | verification / strategy switching improved (real gain) |
| Accuracy rises only in the 0–10-step band | easy problems solved faster |
| Hedging falls **and** accuracy rises | confidence calibration improved |
| Only prose answers decrease | §11.1 format compliance improved — *not* a reasoning gain |

The last row matters most: **ACE can raise the benchmark score by improving answer formatting
alone.** Reasoning gains and formatting gains must be reported separately.
