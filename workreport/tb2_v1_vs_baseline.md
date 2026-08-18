# Terminal-Bench 2.0 — v1 feedback-loop ACE: problems, fixes, sources, expected effects

*Date: 2026-07-30*

v1 (`ace_v1_terminal`) is a **restart**: baseline ACE on the exact same NexAU0 seed
plus a small set of deliberate changes. This document is written as a chain —
**what problem we found → whether we chose to fix it → where the fix comes from and
whether that source is trustworthy → how we changed it → what we expect it to do.**

> The A/B partner is baseline ACE (`ace_terminal`). Both are ACE-on-NexAU0 and share
> the same seed, environment, grader, model, and citation helper — so any difference
> is one of the changes below, nothing else. Baseline runs: `tb2_iter40_baseline_run.md`,
> `tb2_iter40_coldstart_run.md`.

---

## 0. Problems discovered (the full list)

From the baseline ITER-40 runs and their 40 per-task traces:

| # | Problem | Where it lives | Fixed in v1? |
|---|---|---|---|
| P1 | Agent **declares "done" without verifying** — early termination (≤6 cmds, artifact never checked) and "almost-passed" (1–2 tests short) | ACE brain / prompt (axes 1,7) | **yes** (Changes 2) |
| P2 | Learned playbook **only accretes** — harmful / "I cannot" / bug-lesson bullets never removed | ACE brain (axis 7) | **yes** (Change 3) |
| P3 | No **immutable floor** of must-follow rules; at cold start the playbook is empty, so basic discipline must be re-learned slowly, per run | ACE brain (axes 1,7) | **yes** (Change 2) |
| P4 | No **citation signal** → helpful/harmful counting and any prune/DELETE have nothing to act on | ACE brain (axis 7) | **yes** (Change 1) |
| P5 | Harness confounders: server tasks failed (non-persistent session), long commands hard-killed (180s), learning signal graded a copy that disagreed with the score | Environment / verifier | **fixed separately** (shared by both arms — NOT a v1 change) |
| P6 | `chess-best-move`: model cannot **perceive the PNG** | Tool (axes 2,3) | **no — out of ACE scope** |
| P7 | `pytorch-model-cli`, `video-processing`: hard **CV-algorithm coding** (image/video is the program's input, not the agent's perception) | Model capability / skills (axes 5,6) | **no — out of scope** |

**What we chose to fix in v1:** P1–P4 — because they all live inside the two axes
ACE already touches ({1} system prompt, {7} long-term memory), so fixing them keeps
v1 a clean ACE-vs-ACE comparison. P5 was a real bug but it belongs to the shared
harness (fixed for both arms, so it is not part of the A/B). P6/P7 are deliberately
**not** fixed: solving them needs new tools (axes 2/3) or skills/sub-agents (axes 5/6),
which is by definition outside ACE — attempting them would break the clean comparison
and, per the AHE paper, is exactly the ceiling that separates context-evolution (ACE)
from harness-evolution (AHE).

---

## 1. Change 1 — raw deliverable + trailing `CITED:` line  (fixes P4)

- **Problem.** Without a signal for which playbook bullets an attempt used, ACE's
  helpful/harmful counting — and therefore any DELETE/prune — has nothing to act on.
  (Separately, a GDPval finding: wrapping the deliverable in a JSON envelope made the
  generator over-summarize; on the shell substrate the deliverable is already raw, so
  only the citation part is a real addition here.)
- **Source & trust.** The `CITED:` convention is **ACE-native** (it is how ACE's own
  generator reports bullet usage), and the "drop the JSON envelope" half is our own
  **GDPval A/B finding** (documented in this repo's GDPval reports). Trust: **high** —
  it is within ACE's own design and low-risk (the fallback `cite_bullets` call still
  runs if the model omits the line, so citation rate is a property of the harness, not
  the arm).
- **How.** The adapter's context block asks the model to end with `CITED: [id] …`;
  if missing/none, the shared `cite_bullets` helper recovers ids. Axis 1 only; the
  seed and the raw deliverable are unchanged.
- **Expected effect.** Turns on the feedback that Changes 2/3 need; on its own it does
  not change task success, it makes the learning loop observable and actionable.

## 2. Change 2 — immutable rulebook, injected at the end of the context  (fixes P1, P3)

- **Problem.** Most baseline failures are "declared done without verifying," and at
  cold start there is no floor of must-follow discipline — the empty playbook has to
  re-learn "check the file exists, keep the service up, walk the requirements" slowly.
- **Source & trust.** The *mechanism* (one immutable rulebook injected last, never
  edited by the curator) is our own **feedback_loop v2** design, which showed value on
  the GDPval A/B (`AB_rulebook_v6_v61.md`) — so the mechanism is **trustworthy on our
  own prior evidence**, though that evidence is GDPval, not TB2. The rulebook *content*
  was **re-authored from the GDPval-era rulebook for the shell domain**: this is
  hand-authored expert heuristics, **medium-low trust for TB2** — plausible but not yet
  TB2-validated, and we already caught two document/shell mismatches in it (see below).
- **How.** `configs/rulebook_terminal.txt` (25 shell-native rules) is injected at the
  END of the context (recency = must-follow) and kept OUT of `self.playbook`, so the
  Reflector/Curator structurally cannot add/edit/delete it. During review we fixed the
  two mismatches that would actively hurt TB2 — rb-fmt-05 now says a required **service
  is left running** (a naive "clean up" reading would kill the server the verifier
  checks), and rb-chk-02 no longer tells the agent to hunt for a **test suite the task
  never ships** — and trimmed two generic/duplicate rules.
- **Expected effect.** Directly targets P1: the finishing rules (confirm the file
  after writing, leave services up, walk the requirement list, verify by an independent
  falsifiable check, do implied checks) push the agent to not stop early. Because the
  rulebook is present from **task 1** (vs baseline learning from empty), any finishing
  gain should show **early**, not after many tasks. **Caveat:** this is a static
  expert rulebook, so a gain is closer to "we handed it the finishing rules" than
  "the loop learned them," and it only helps if gemini-3.1-flash-lite actually obeys —
  baseline traces show this model tends to over-confidently stop.

## 3. Change 3 — playbook DELETE + standing warning  (fixes P2)

- **Problem.** The baseline curator can only ADD, so the playbook only grows; harmful,
  wrong, or self-defeating ("I cannot…", give-up) bullets are never removed, and we
  observed exactly such bug-lesson/disclaimer bullets persisting.
- **Source & trust.** DELETE is part of **ACE's own designed operation set** — the
  curator code enumerates `ADD / UPDATE / MERGE / DELETE`; baseline simply shipped ADD
  and left DELETE an unimplemented stub. So this is **within-ACE, high trust**: we are
  finishing a sanctioned ACE operation, not inventing a new mechanism. (Our
  feedback_loop repo had already implemented it; we ported that exact branch.)
- **How.** `apply_curator_operations` gains a DELETE branch (the ONLY function that
  differs from baseline — verified by AST diff), and the curator prompt now offers a
  DELETE op plus a STANDING WARNING to remove self-defeating / capability-disclaimer
  bullets. Protected/seed bullets are never targeted. Axis 7.
- **Expected effect.** Lets the learned playbook **shed** harmful bullets instead of
  only accreting — over a run it should keep the playbook cleaner and stop it from
  teaching "give up" habits back to the agent. Effect is cumulative, so it matters more
  on longer runs than on a 2-task smoke.

---

## 4. What v1 cannot fix, and how to read the A/B

- **P6 tool-ceiling (`chess-best-move`).** The seed's text-only `run_shell_command`
  cannot hand the model a PNG; solving it needs a new tool (axes 2/3). Outside ACE by
  definition — the textbook AHE-vs-ACE ceiling. **Do not attribute this failure to v1.**
- **P7 hard-coding (`pytorch-model-cli`, `video-processing`).** These are hard CV-
  algorithm coding tasks; the image/video is the *program's* input, not the agent's
  perception. Neither ACE context nor a simple tool fixes a wrong algorithm — a
  stronger model or a skill/sub-agent (axes 5/6) would. **Not a v1 weakness.**
- **Fair reading.** The A/B is only meaningful on the **addressable slice** (P1–P4):
  did the finishing/early-termination and "almost-passed" failures improve, and did it
  show up early? Successes/failures on P5–P7 tasks say nothing about ACE vs v1.

---

## 5. File-level delta and run

Delta vs baseline (everything else is byte-identical; verified by AST/byte diff):

| File | Change |
|---|---|
| `harness/agents/ace_v1_terminal.py` | **new**; mirrors `ace_terminal` + rulebook(end) + CITED + DELETE-capable curate; `ACE_PATH`→`ReAct_feedback_loop` |
| `ReAct_feedback_loop/ace/prompts/curator.py` | offer DELETE + standing warning |
| `ReAct_feedback_loop/playbook_utils.py` | `apply_curator_operations`: DELETE branch (only this fn differs) |
| `configs/rulebook_terminal.txt` | 25-rule immutable shell rulebook (2 mismatches fixed, 2 rules trimmed) |
| `scripts/run_terminal_gemini.sh` | `ARM=v1` → `ace_v1_terminal` |

Removed in the restart: `ace_dual_terminal.py` (+ test) and the old feedback-loop
machinery (dual playbook, auto_distill, FAISS, Thompson, grader-counting,
`_single_view`/`_single_learn`).

```bash
bash scripts/start_proxy.sh                        # 6-key Gemini rotation proxy on :4000
ARM=v1 RUN_ID=tb2_v1_iter40_cold TASKS=<ITER-40> \
  bash scripts/run_terminal_gemini.sh              # v1 cold-start, empty playbook
```

v1 cold-start ITER-40 is running (rulebook 6320 chars loaded, both surfaces via the
proxy). Results → `runs/tb2_v1_iter40_cold/`, traces → `ace_v1_terminal_run/traces/`,
playbook → `playbooks/ace_playbook_v1_cold.txt`. Numbers + the baseline-vs-v1
comparison on the addressable slice go here when it completes.
