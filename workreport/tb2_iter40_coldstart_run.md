# Terminal-Bench 2.0 — Cold-start ITER-40 (baseline ACE, fixed harness)

*Date: 2026-07-29*

A clean re-run of the baseline ACE arm (`ace_terminal` = ACE-on-NexAU0) on the
stratified **ITER-40** subset, on Gemini via the 6-key rotation proxy. Unlike the
earlier baseline run, this one starts from an **empty playbook** and runs on the
**fixed harness** (persistent session, wait semantics, grade-once-shared verifier).
It is the first de-confounded baseline ACE measurement.

> Per request this write-up covers **this run only** — no comparison to the earlier
> confounded run.

---

## 1. Setup

- Agent: `ace_terminal` (baseline ACE = ReAct-style shell loop + Reflector/Curator).
- Substrate: NexAU0 seed (single `run_shell_command`, minimal prompt, no
  middleware/skills/sub-agents/memory).
- Model: `gemini-3.1-flash-lite` for both the shell loop and the ACE brain,
  through the LiteLLM `:4000` rotation proxy (6 keys, RPD failover). Cost $0.
- **Cold start:** empty playbook (`ACE_PLAYBOOK_IN` unset), grown fresh over the run.
- Harness: fixed Env (persistent in-container shell, wait-not-kill with graceful
  reset, stdin isolated) + `shared` grading (one real in-container verifier run,
  shared by the report and the ACE learning signal).
- Feedback signal `shared`; `--concurrency 1`; per-task trace dumps on.

---

## 2. Results

**40/40 · PASS 18 · FAIL 22 · pass rate 45% · crashes 0.**

**By difficulty:** easy 1/2 · medium 12/25 · hard 5/13.

**Failure taxonomy (22):**

| # | type |
|---|---|
| 10 | almost-passed (1–2 tests short) |
| 5 | output/impl mismatch |
| 3 | vision/image (model limit) |
| 2 | time-budget exhausted |
| 2 | early termination (artifact unverified) |

The single largest bucket is **almost-passed (45% of failures)** — the agent gets
most tests and misses one or two. That is where the headroom for a better
playbook / feedback loop concentrates.

---

## 3. Run health (clean)

| check | value |
|---|---|
| crashes | **0** |
| deadlocks | **0** |
| non-seed tool calls | **0** (all `run_shell_command`) |
| learning signal == official score | 39/40 (1 edge) |
| traces captured | **40/40** |
| commands/task min·median·max | 4 · 15 · 66 |

This is the first ITER-40 run with no harness casualties: no crashes, no
deadlocks, full traces, and (via `shared` grading) the ACE learning signal equals
the reported score.

---

## 4. Tokens & cost

| component | tokens |
|---|---|
| shell loop (40 traces) | 3,277,248 |
| ACE brain (reflect + curate) | ~0.6M |
| **total** | **≈ 3.9M** |

Gemini free tier → **actual cost $0**. (On the Claude API the same volume is
roughly Sonnet ~$14–15 without caching, less with it; Haiku ~$5.)

Playbook grew from empty over the run, learning fresh (no bug-lessons carried in
from a prior harness).

---

## 5. Bugs found & fixed during this run

The run doubled as a stress test of the new Env and surfaced two real bugs, both
fixed and verified live:

1. **Interactive-stdin deadlock (caught by the smoke).** In the persistent
   session, an interactive command (`apt-get` → debconf "Geographic area:") read
   the command FIFO as its stdin and hung the whole session. **Fix:** the session
   reads its command stream from `/dev/fd/9` but runs each command with
   `stdin=/dev/null`, so interactive prompts get EOF (the old non-interactive
   behaviour) while cwd/env/background still persist.

2. **Wedged-session crash (caught by the full run — 3 tasks).** A foreground
   command that outlived the wait kept holding the single session, so feeding the
   next command's FIFO write blocked for 30s and crashed the whole task
   (`TimeoutExpired` on `cat >> …`). Affected `llm-inference-batching-scheduler`,
   `merge-diff-arc-agi-task`, `overfull-hbox`. **Fix:** the FIFO write/poll is
   guarded — on a wedge the session is reset (its shell killed; nohup-detached
   background jobs survive) and the tool returns a graceful error instead of
   crashing. The three tasks were then re-run cleanly (5/6, 3/5, 2/4 — all real
   attempts, no crash), completing the 40/40.

---

## 6. Takeaways

- First **clean, de-confounded** baseline ACE number on ITER-40: **45% (18/40)**,
  0 crashes, 0 deadlocks, full traces, learning == official.
- Failures cluster in **almost-passed (10)** — the most promising target for a
  feedback-loop / rulebook improvement.
- The fixed harness (persistent session + shared grading) held up across a full
  run after two bugs were ironed out; both fixes are defensive (graceful
  degradation, no crashes).

Artifacts: results `runs/tb2_gem_iter40_cold/`, traces `ace_terminal_run/traces/`,
playbook `playbooks/ace_playbook_terminal_cold.txt`.
