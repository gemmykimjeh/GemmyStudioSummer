# Terminal-Bench 2.0 — Baseline ACE (NexAU0, Gemini) ITER-40 Run + Harness Fixes

*Date: 2026-07-29*

First full run of the baseline ACE arm (`ace_terminal` = ACE-on-NexAU0) on the
stratified **ITER-40** subset, on Gemini via the 6-key rotation proxy. Below: the
results, exact token usage, the harness confounders the traces exposed, and the
fixes made in response.

> **Read the results as CONFOUNDED.** This run (07-28) predates every fix in §4.
> The numbers reflect the *old* harness (per-command hard-kill, non-persistent
> shell, shadow-copy learning signal). They are the baseline that motivated the
> fixes, not a clean measurement of ACE. A de-confounded cold-start re-run is the
> next step (§5).

---

## 1. Results

**40/40 tasks · PASS 17 / FAIL 23 · pass@1 = 42%** (baseline ACE, gemini-3.1-flash-lite).

**By difficulty** (ratio matches the full 89-set 4/55/30):

| difficulty | pass | note |
|---|---|---|
| easy | 0/2 (0%) | TB2 "easy" ≠ easy for a flash-lite agent |
| medium | 16/25 (64%) | where the model's competence sits |
| hard | 1/13 (8%) | mostly expected fails |

**By domain (pass/total):** software-engineering 3/13, system-administration 2/3,
scientific-computing 2/2, data-processing 2/2, security 1/3, debugging 1/3,
file-operations 1/3, mathematics 1/2, model-training 1/2, data-science 1/1,
optimization 1/1, personal-assistant 1/1; games 0/1, machine-learning 0/1,
data-querying 0/1, video-processing 0/1.

**Failure taxonomy (23):**

| # | type | tasks |
|---|---|---|
| 11 | almost-passed (1–2 tests short) | build-cython-ext, cobol-modernization, db-wal-recovery, fix-code-vulnerability, gcode-to-text, kv-store-grpc, llm-inference-batching-scheduler, overfull-hbox, password-recovery, sparql-university, torch-tensor-parallelism |
| 5 | output/impl mismatch | build-pmars, gpt2-codegolf, model-extraction-relu-logits, polyglot-c-py, polyglot-rust-c |
| 3 | vision/image (model limit) | chess-best-move, pytorch-model-cli, video-processing |
| 3 | early termination (≤6 cmds, artifact unverified) | configure-git-webserver, headless-terminal, torch-pipeline-parallelism |
| 1 | time-budget exhausted | make-doom-for-mips |

**Structural health:** 0 non-seed tool calls (all `run_shell_command`), 0 crashes
(`error=null` on all 40), is_background used 3×, commands/task 5–130 (median 13).
The pipeline itself was sound — every failure was a real task/model failure.

**Biggest lever:** the 11 "almost-passed" (48% of failures) are the highest-value
targets — a better playbook/rulebook could flip these.

---

## 2. Token usage & cost

Measured for this run (shell loop from the 40 trace dumps; ACE brain from the
Reflector/Curator logs, filtered to the run's time window):

| component | calls | input | output | total |
|---|---|---|---|---|
| shell loop (agent) | — | ~94%* | ~6%* | **4,340,888** |
| ACE brain (40 reflect + 40 curate) | 80 | 796,703 | 32,344 | **829,047** |
| **total** | | **≈ 4.88M** | **≈ 0.29M** | **≈ 5.17M tokens** |

\* shell in/out is estimated — the trajectory stores only the combined per-task
sum; the brain's measured split is 96/4 and the shell loop is even more
input-heavy (it resends the growing transcript every turn). `cite_bullets` (1
call/task, ~0.1M) is uncounted — negligible.

- **Gemini actual cost: $0** (free tier, 6-key RPD rotation).
- ~129K tokens/task average (max: password-recovery 559K over 130 commands).
- **84% of tokens are the shell loop** — that's where both cost and any caching
  benefit concentrate.

**If run on the Claude API instead** (same token volume, **no prompt caching**):

| model | in/out per M | est. total (40 tasks) | per task |
|---|---|---|---|
| Haiku 4.5 | $1 / $5 | ≈ $6 | ~$0.16 |
| Sonnet 5 (default) | $3 / $15 | ≈ $19 | ~$0.48 |
| Opus 4.8 | $5 / $25 (repo est.) | ≈ $32 | ~$0.80 |

Opus pricing is the repo's assumed rate; at a standard Opus tier ($15/$75) it
would be ~$95 — wide band. **Prompt caching** matters a lot here: the workload is
94% input with a stable, re-sent prefix, ideal for caching, which typically cuts
input cost 50–80% on agentic loops → **Sonnet ~$10 instead of ~$19**.

---

## 3. Problems found (harness confounders on judging ACE)

Re-examining the 40 traces surfaced harness-level confounders — failures that
trace to the *scaffold*, not ACE's playbook or the model. These are exactly the
components AHE evolves; ACE's in-context playbook cannot fix them.

**① `is_background` / no persistent session — server & daemon tasks.** The old
Env ran each command as a fresh, stateless `docker exec bash -lc`. A server the
agent backgrounded died the moment that exec returned, so at grading time no
server was running. *Evidence:* kv-store-grpc failed `test_real_grpc_server_running`;
its trace shows the gRPC server started then gone.

**② Per-command 180s HARD-KILL — long build/VM tasks.** The old Env killed any
single command at 180s. Real terminal-bench's 180s is a *wait*, not a kill, and
NexAU's own shell tool defaults to 300s. *Evidence:* make-doom-for-mips hit the
180s kill 4× on `node vm.js` and its cross-compile build; the agent noted "vm.js
is timing out."

**③ shadow-reward ≠ official score — the ACE learning signal.** ACE learns from a
pass/fail signal it computes *during* `run()`; the official score runs *after*, in
a separate step. To avoid double-grading the scored container, the adapter graded
a `docker commit` COPY (a "shadow" container). But a filesystem copy loses running
processes, so server tasks fail on the copy though they passed for real.
*Evidence:* **5/40** had shadow=Fail but official=Pass — nginx-request-logging,
pypi-server, git-multibranch, hf-model-inference, largest-eigenval. ACE learned
"I failed" on tasks it actually passed (12.5%, all pessimistic).

**Also noted (not code bugs, but caveats on the measurement):**
- **Bug-lessons baked into the playbook.** The learned playbook contains
  `[sai-00035]`/`[sai-00054]` "avoid backgrounding with `&`; it's terminated on
  shell exit" — a workaround learned from the *broken* Env (①). Warm-starting from
  this playbook would carry a now-wrong lesson forward.
- **ACE brain = flash-lite.** We are measuring "ACE with a weak Reflector/Curator,"
  which caps the ceiling. (The 46-bullet playbook was coherent, so this is a
  scoping caveat, not a defect.)
- **Editing friction (no edit tool).** The seed's single `run_shell_command` forces
  full-file heredoc rewrites (up to 22× on one file, polyglot-rust-c). This is a
  *property of NexAU0* (both arms share it), not an ACE-vs-baseline confounder.

---

## 4. Fixes made

### (a) Env → real terminal-bench execution semantics (`terminal_bench.py`)

Replaced the stateless `docker exec` with a **persistent in-container shell
session** (a single long-lived `bash` fed commands over a FIFO, output collected to
a log; fd 9 held rw so the FIFO never EOFs). This reproduces real terminal-bench
semantics *without* tmux (which the TB2 images lack):

- **Persistent session** → cwd, env vars and **background processes persist**
  across commands. Fixes ①.
- **Timeout = wait, not kill** → on the 180s wait a slow command returns its
  output-so-far and **keeps running**; the per-task budget still bounds total time.
  Fixes ②.
- Fixed a Windows-host CRLF bug (payload sent as raw bytes so commands don't arrive
  as `cmd$'\r'`).

*Validated live:* a backgrounded server persists and is reachable from a separate
`docker exec` (pypi image: port LISTENING); cwd/env/file/background all persist; a
command past the wait finishes instead of being killed.

### (b) Learning signal → "grade once, share" (`terminal_bench.py`, `_tb_verifier.py`, both adapters)

Dropped the shadow copy for the default path. New `TerminalBenchEnv.official_verdict()`
runs the verifier in the **real scored container exactly once** and caches it; both
`TerminalBench.score()` (report) and the ACE learner (new `feedback="shared"`
default) read that **same** result. This is the industry-standard "one real grading,
shared between report and learner" — the learning signal can no longer disagree with
the reported score, and the verifier runs once/task instead of twice (also cheaper).

*Validated live:* pypi-server (an old shadow≠official case) now grades
learning = official = 1.0, note "official verifier (shared, in-container)."

**Tests:** 32/32 pass (`test_terminal_bench`, `test_ace_terminal`, `test_ace_dual_terminal`).

**Files:** `harness/benchmarks/terminal_bench.py`, `harness/agents/_tb_verifier.py`,
`harness/agents/ace_terminal.py`, `harness/agents/ace_dual_terminal.py`. Also added
per-task full-trace dumps (`ace_terminal_run/traces/*.json`) so attempts are
inspectable offline.

---

## 5. Status & next step

**Fixed & verified:** confounders ①, ②, ③ (mechanism-tested live). Tests green.

**Still open:**
- The 42% above is **still confounded** (pre-fix Env + shadow) → no clean ACE
  number yet.
- Current playbook carries the **bug-lesson bullets** (§3) → must start fresh.
- The new Env has **un-stress-tested edges** (does the seed agent handle "still
  running" well? does session-init succeed on every image, or silently fall back to
  stateless?).

**Next:** a **cold-start ITER-40 re-run** — empty playbook + fixed Env + `shared`
grading. That both yields the first de-confounded ACE number and exercises the new
Env at scale. A short smoke (a server task + a normal task) runs first to catch any
regression before committing the full 40.
