# Proposed Changes to OpenHands — Generalised Across Our Benchmark Suite

**Basis:** trajectory analysis of the 49 incorrect instances from the GAIA-1 reproduction run (2026-08-06)
**Target code:** `agent-sdk @ 9836b772` (= `v1.14.0-60-g9836b772`)
**Companions:** `260806_..._results.md` (nine harness principles) · `260806_gaia_error_analysis_en.md` (error analysis)

---

## 0. Scope and limitation — read first

**The only thing measured is 165 GAIA instances.** Every claim below about generalising to
TB2, SWE-bench or GDPval is a **structural argument, not a measurement.** Whether the same
bottleneck dominates in those benchmarks has not been checked.

§10 specifies a procedure to test this **at zero API cost**. Run §10 before implementing any of
these proposals. If its results diverge from GAIA, **the proposals must be split per benchmark**
rather than applied uniformly.

---

## 1. Our suite spans three output modalities

| Modality | Benchmarks | Termination | Scoring |
|---|---|---|---|
| **Value-answer** | GAIA · BrowseComp · simple-evals | emit a string | exact match |
| **State-mutation** | **TB2** · SWE-bench · tau-bench · OSWorld · AutomationBench | environment reaches target state | run tests |
| **Artifact-judged** | **GDPval** | submit a document | LLM grader |

Why the distinction matters: a fix that falls naturally out of GAIA analysis — e.g. "make the
answer string a typed field" — is **dead weight in TB2, which has no answer string at all.**
The fix has to live at a layer that is not modality-specific.

---

## 2. One failure runs through all three modalities: termination without verification

In every modality the episode ends when **the agent declares it is done**. That declaration
currently carries **no accountability whatsoever.**

```
GAIA        declares an answer found  →  no source check          →  'Jerome Wiesner' (plausible, wrong)
TB2         declares it built         →  never checked as grader will  →  tests fail
SWE-bench   declares it fixed         →  suite never run          →  regression
tau-bench   declares it handled       →  final DB state unchecked →  rule violation persists
GDPval      declares it complete      →  brief requirements uncompared →  missing items
```

### Evidence observed in GAIA

| Metric | Correct (median) | Wrong (median) |
|---|---|---|
| Steps | 10 | 28 |
| Browser calls | 0 | 9 |
| Hedging language | **0 / 116** | **10 / 49** |

```
Accuracy by step count
   0–10 : 85.0%      20–35 : 54.1%      60+  : 33.3%
  10–20 : 81.2%      35–60 : 29.4%      ← flat past 20 steps
```

**Past 20 steps, effort stops converting into accuracy.** And hedging appearing in zero correct
answers means **the agent knows when it does not know — and that signal is discarded at the
output boundary.**

The bottleneck is neither planning nor tooling. It is the **absence of a belief-management
layer.**

---

## 3. Proposal 1 — Accountable termination 🔴 highest priority

**File:** `openhands-sdk/openhands/sdk/tool/builtins/finish.py:21`

### Current

```python
class FinishAction(Action):
    message: str = Field(description="Final message to send to the user.")
```

The termination declaration carries exactly one blob of free text.

### Proposed

```python
class FinishAction(Action):
    message: str

    # ── universal across modalities ───────────────────
    verification: str = Field(
        description="What you actually ran or checked to confirm this is correct. "
                    "Report what you executed, not what you believe.")
    unverified: list[str] = Field(
        default_factory=list,
        description="Claims or requirements you could not check.")
    confidence: Literal["high", "medium", "low"] = "medium"

    # ── value-answer benchmarks only ──────────────────
    final_answer: str | None = Field(
        default=None,
        description="Direct answer with no surrounding prose.")
```

### Why this shape

The point is **not** `final_answer`. It is **`verification` / `unverified` / `confidence`**,
which carry meaning independent of output modality:

| Benchmark | Contents of `verification` |
|---|---|
| GAIA | "confirmed at source URL X, corroborated by independent source Y" |
| **TB2** | "ran `pytest tests/` → 12 passed" · "`curl localhost:8080` → 200" |
| SWE-bench | "ran the full suite, no regressions" |
| tau-bench | "queried final DB state, attached" |
| GDPval | "checked all 7 requirements in the brief section by section" |

### Why this is *stronger* in state-mutation benchmarks

In GAIA, "I cited a source" is subjective. In TB2 and SWE-bench the command written into
`verification` **can be re-executed by the harness and compared.** False verification claims
become automatically detectable — a property GAIA cannot offer.

### Side effect — an abstention channel

`confidence` becomes the only route by which "I don't know" can reach the outcome. Today the
agent writes *"unable to access…"* — **explicitly declaring failure** — and then fills in the
answer field anyway (`0b260a57`). The calibration exists internally; the interface cannot
receive it.

### Backward compatibility

Give `verification` a default and existing code keeps working. Benchmark adapters use the new
fields when present and fall back to `message` when not.

**Cost:** days · **Risk:** low · **Applies to:** all modalities

---

## 4. Proposal 2 — A ledger of verified claims, with invalidation 🔴 core

`ConversationState` currently holds only a sequence of events. Add structured state:

```python
class VerifiedClaim(BaseModel):
    claim: str                      # "port 8080 responds" / "test_auth passes"
                                    # / "source X states Y"
    check: str                      # the command or URL used — in re-runnable form
    checked_at_step: int
    outcome: str
    invalidated_by: str | None = None   # which later action invalidated this
```

The agent manipulates it through `record_claim` / `recheck_claim` tools.

### Invalidation tracking is what generalises this

This is a dimension GAIA does not have and state-mutation benchmarks do — **claims go stale.**

| Benchmark | What the ledger catches |
|---|---|
| GAIA | unsupported claims; intermediate hops never corroborated |
| **TB2** | **a test that passed earlier, broken by a later config change** |
| SWE-bench | my patch breaking a different test (regression) |
| tau-bench | DB state confirmed in an earlier turn, invalidated by a later one |
| GDPval | a requirement satisfied in the draft, dropped during revision |

The GAIA-only version of this idea was an "evidence ledger". Adding invalidation makes it
**both more general and more powerful.**

### What it enables

| Impossible today | With the ledger |
|---|---|
| "what supported this claim?" | queryable |
| "was it independently corroborated?" | recheck count |
| provenance surviving compaction | ledger excluded from summarisation (Proposal 3) |
| evidence-based stopping | "are all claims still valid?" |
| detecting a wrong intermediate hop | hop-by-hop evidence is traceable |

**Cost:** weeks · **Risk:** medium · **Applies to:** all modalities

---

## 5. Proposal 3 — Keep the condenser from erasing the ledger 🔴 critical for TB2

**File:** `openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py`

```python
max_size: int = 240      # fires above 240 events
keep_first: int = 2      # first 2 are never summarised
```

On firing, the back half is folded into an LLM-generated summary — and **which claim came from
which source dissolves into prose.**

### Why this bites harder in TB2

Terminal work produces exploding build logs and stack traces, so **compaction fires far more
often.** What was already tried and failed gets absorbed into a summary, and **the agent retries
the same thing.**

In GAIA this only hurt multi-hop instances (Level 3, 53.8%). In state-mutation benchmarks it
becomes a default failure mode.

### Proposal

Exclude the `claims` ledger from summarisation permanently, the way `keep_first` protects the
opening events. Compaction can then occur without losing "what has been confirmed and what is
still valid."

**Cost:** days · **Risk:** low · **Applies to:** all (greatest in TB2 / SWE-bench)

---

## 6. Proposal 4 — Turn repetition detection into a strategy-switch trigger 🟡

**File:** `openhands-sdk/openhands/sdk/conversation/stuck_detector.py`

This asset already exists. It detects repeated action/observation pairs, repeated errors, agent
monologue, and alternating patterns.

```python
MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION = 20
```

**Today, detection only sets `STUCK` and ends the episode.** The detector exists; its only
disposition is "give up".

### Proposed — intervene once, then continue

```python
if detector.is_stuck() and not state.strategy_switch_injected:
    on_event(MessageEvent(source="environment", content=(
        "Your recent actions repeat without progress. Before acting again, state: "
        "(1) what you have established, and the check that confirmed it; "
        "(2) what remains unknown; "
        "(3) a different approach — not a variation of the current one.")))
    state.strategy_switch_injected = True
    # do NOT terminate as STUCK
```

### Value by modality

In GAIA, median browser calls were 0 when correct and 9 when wrong — the symptom of drift.
**In state-mutation benchmarks this is worth more**: cycling variations of the same fix against
a failing build is the canonical failure, and there **progress is objectively measurable**
(number of passing tests). The trigger can therefore add a stronger condition:
"passing-test count has not increased in N steps."

**Cost:** days · **Risk:** low · **Applies to:** all modalities

---

## 7. Proposal 5 — Clean-room verification phase 🟡 greatest in state-mutation

When the agent declares a candidate result, re-verify from a clean state.

```
exploration phase → candidate declared
                       ↓
verification phase : fresh context = (task, candidate, claims ledger)
                     instruction  = "verify exactly as the grader will"
                       ↓
                 pass → submit     fail → return to the weakest claim
```

### It targets a failure GAIA cannot expose

State-mutation benchmarks have this failure:

> **The agent verifies in a way favourable to itself and stops.**
> It runs one test instead of the suite; it checks the service differently from the grader;
> it confirms success inside the dirty environment it created.

So the rule for the verification phase must be **"verify the way the grader will."** TB2 and
SWE-bench have an actual test command, so this is **literally executable**; GAIA and GDPval can
only approximate it.

### Rationale

In GAIA, accuracy is flat past 20 steps (29% at 35–60, 33% beyond 60). **Additional exploration
does not convert into accuracy.** Reallocating the same budget to *verification* plausibly does
— which is a testable hypothesis (§11).

**Cost:** weeks · **Risk:** medium · **Applies to:** state-mutation ≫ value-answer

---

## 8. Proposals withdrawn

Derived from GAIA analysis, retracted after considering the full suite.

| Withdrawn | Reason |
|---|---|
| Editing the `<BROWSER_TOOLS>` wording | It lives inside the browser prompt block, so it never fires on the TB2 path. The same principle — *terminate on verification, not on a timer* — **belongs in Proposal 1's schema**, where it applies to every modality |
| "Enumerate exhaustively" prompt | Specific to GAIA's numeric questions; pure prompt bloat for TB2 / SWE-bench |
| Making `final_answer` mandatory | Value-answer only. **Demoted to optional** |
| "Preserve source typos" rule | **Benchmark gaming.** In the `Ploybius` → `Polybius` case the agent behaved correctly; this is the harness's job to specify, not the agent's to guess |

The first row is the main lesson of this revision. A one-line prompt edit looked cheap, but
**it only works on the GAIA path.** Putting the same principle in the termination schema costs
slightly more and covers everything.

---

## 9. Priority and applicability

| # | Proposal | Cost | GAIA | TB2 | SWE-b | tau | GDPval |
|---|---|---|---|---|---|---|---|
| 1 | Accountable termination | days | ●●● | ●●● | ●●● | ●●● | ●●● |
| 3 | Condenser preserves ledger | days | ●● | ●●● | ●●● | ●● | ●● |
| 4 | Strategy-switch trigger | days | ●● | ●●● | ●●● | ●● | ●● |
| 2 | Verified-claims ledger | weeks | ●●● | ●●● | ●●● | ●●● | ●●● |
| 5 | Clean-room verification | weeks | ● | ●●● | ●●● | ●● | ●● |

**Start with Proposal 1.** It is a single schema change, low-risk, and it is the substrate for
the others — Proposal 2's ledger populates `verification`, and Proposal 5 re-executes it.

---

## 10. Validating the generalisation — do this first, at zero API cost

Per §0, the evidence is GAIA-only. Before implementing anything, measure the following on TB2
logs (either our own small runs or the archive of a tbench.ai leaderboard submission).

```
① Success rate by step count
   → Does it flatten past ~20 steps as in GAIA?
   → If yes, "effort does not convert to accuracy" holds outside GAIA.

② Share of failed episodes that terminated WITHOUT running the tests     ← decisive
   → High: Proposal 1's rationale holds beyond GAIA; proceed as written.
   → Low : TB2's bottleneck is elsewhere; the proposals must be split per benchmark.

③ Count of tests that passed and were later broken by subsequent actions
   → Confirms whether Proposal 2's invalidation tracking is actually needed.

④ Hedging rate in the final message (success vs failure)
   → Does GAIA's 0/116 vs 10/49 pattern reproduce?
   → If so, the calibration signal exists independent of output modality.
```

**② is the decision criterion.** If it is high, as in GAIA, proceed with Proposals 1–5 as
written. If it is low, identify TB2's real bottleneck before building anything.

---

## 11. If written as a paper

### Claim

> **Agent benchmarks measure search competence, not epistemic competence.**
> The bottleneck is not per-step capability but the **absence of a belief-management layer**,
> and this is independent of output modality.

### Evidence (GAIA, n = 165)

```
0–10 steps  85.0%        solved well when the answer is directly findable
35–60 steps 29.4%
  60+ steps 33.3%        ← flat: effort does not convert into accuracy

Hedging     correct 0/116 · wrong 10/49    calibration exists, then is discarded
Browser     correct median 0 · wrong 9     cannot switch strategy after failure
Level 1/2/3   79.2 / 69.8 / 53.8%          multiplicative decay at ~90% per hop
```

### The falsifiable prediction — this is where the strength lies

> Applying Proposals 1, 2 and 5 raises accuracy **only in the ≥20-step band, leaving the
> 0–10-step band unchanged.**

This is a far stronger claim than "our method scores +X". It names a mechanism and predicts
**where the gain appears and where it does not**, so it fails visibly if wrong.

### Extended to multiple modalities

The same prediction, specialised:

| Modality | Prediction |
|---|---|
| Value-answer (GAIA) | accuracy rises only in the long-episode band |
| State-mutation (TB2, SWE-b) | **a drop in "terminated without verifying" mediates the rise in success rate** |
| Artifact-judged (GDPval) | a drop in missing requirements mediates the rise in grader score |

Predicting the **mediation** relationship, not just the score delta, makes a substantially
stronger causal claim than a headline number.

### Secondary result

Hedging language in the output text predicts correctness (0/116 correct vs 10/49 wrong). This
quantifies a calibration signal the model already produces for free and the interface throws
away — small, but clean, and independently publishable.

---

## 12. Relationship to ACE

This diagnosis constrains ACE's design directly.

- If the playbook accumulates **"how to search / how to operate"**, it cannot touch the
  bottleneck above.
- It must accumulate **"what I judged trustworthy"** — verification procedures, corroboration
  habits, stopping criteria — to address it.
- **Proposal 2's claims ledger is the natural substrate for exactly that.** ACE can mine the
  ledger for rules such as *"claims of this type require two independent sources"* or
  *"re-run the test suite after this class of edit."*

In other words, these proposals improve OpenHands **and** build the socket that ACE plugs into.

### Instrument for attributing an ACE gain

| Observation | Interpretation |
|---|---|
| Accuracy rises in the ≥20-step band | verification / strategy switching improved — **a real gain** |
| Accuracy rises only in the 0–10-step band | easy tasks solved faster |
| Hedging falls **and** accuracy rises | confidence calibration improved |
| Only prose answers decrease | format compliance improved — **not a reasoning gain** |
| (TB2) "terminated without verifying" falls | accountable termination internalised |

The second-to-last row matters most: **ACE can raise the benchmark score by improving answer
formatting alone.** Unless reasoning gains and formatting gains are reported separately, the
result cannot be interpreted.
