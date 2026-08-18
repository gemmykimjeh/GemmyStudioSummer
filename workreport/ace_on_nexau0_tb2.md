# ACE-on-NexAU0 for Terminal-Bench 2.0 (Gemini) — Work Report

*Date: 2026-07-28*

Re-grounded the Terminal-Bench 2.0 ACE arms onto the **NexAU0 seed** substrate (the
one the AHE paper actually compares ACE on), wired them to run on **Gemini** through
the existing 6-key rotation proxy, validated end-to-end with a live scored task, and
designed a stratified **ITER-40** iteration batch.

---

## 1. Why: ACE is a context layer, not a harness

ACE (Reflector/Curator/playbook) is a **context/memory layer** that rides on top of some
base agent harness. The original ACE work used **ReAct** as that harness; but that ReAct
is welded to AppWorld (`base_react.py` imports `AppWorld`, acts via `world.execute()`), so
it cannot be reused for a shell benchmark.

The AHE paper (*Agentic Harness Engineering*, arXiv:2604.25850) does **not** run ACE on
ReAct — it runs ACE on **NexAU0**, the deliberately minimal NexAU *seed* harness, so the
harness is held fixed and only the context layer varies. Verbatim from the paper:

> "ACE distills natural-language playbooks the agent reads in-context … neither method
> opens the surrounding scaffolding to edits."

Reported numbers (Terminal-Bench 2, 89 tasks, GPT-5.4): NexAU0 seed **69.7%**, ACE
**68.9%**, AHE **77.0%**. ACE lands *below* the bare seed — the gap is attributed to ACE's
narrow action space (prompt-only), which is exactly the "harness fixed, context evolves"
comparison.

**Decision:** put our TB2 ACE arms on the *same* seed AHE used (ACE-on-NexAU0), rather than
our earlier hand-written shell prompt/loop.

## 2. The NexAU0 seed, re-implemented verbatim

New shared module **`harness/agents/_nexau0.py`** reproduces the seed from the AHE repo
(`agents/code_agent_simple`). It is a faithful re-implementation of the *seed* (not a vendor
of the NexAU framework), which is all the harness-fixed comparison needs.

| Seed property | Source of truth | Our reproduction |
|---|---|---|
| Single tool `run_shell_command` | `tool_descriptions/run_shell_command.tool.yaml` | ✅ verbatim schema+description (`command`, `description`, `is_background`, `dir_path`; `command` required) |
| Minimal system prompt | `systemprompt.md` | ✅ **char-identical prose** (1 tool + 3 rules + 3 runtime vars: date / username / working dir) |
| `max_iterations` 300 | `code_agent.yaml` | ✅ `max_steps=300` |
| No middleware / skills / sub-agents / long-term memory | `code_agent.yaml` (nothing wired) | ✅ none |
| ACE = playbook read in-context | AHE paper | ✅ appended to the seed prompt as the only add-on |

The seed's single tool is mapped onto the benchmark's `bash` tool at call time, so the model
only ever sees `run_shell_command`; `dir_path` → `cd <dir> && …`.

### Documented, deliberate gaps (irreducible under our setup)

- **Base model**: Gemini/Claude, not the paper's GPT-5.4. Inherent to this repo.
- **`is_background`**: the real NexAU tool backgrounds at the process-group level; the TB2 Env
  exposes only a foreground `bash`, so we emulate it in the adapter (`nohup … &`). The **model**
  still never writes `&`, so the seed's rule holds on the model side. This is the one
  irreducible mechanism gap.
- **Sampling** (temperature/top_p/max_tokens) left at our defaults, not the paper's 0.7/0.95/32k
  (kept to avoid perturbing our A/B).

Both TB2 adapters (`ace_terminal` = baseline ReAct-ACE; `ace_dual_terminal` = feedback-loop
ACE with single playbook + immutable rulebook) were refactored to call `_nexau0.solve` and
layer only their ACE context (playbook / learned-playbook+rulebook view + `CITED:` contract)
on top. The verifier signal (`_tb_verifier.shadow_reward`) and all learning logic are unchanged.

## 3. Gemini integration (rotation proxy)

Reused the existing GDPval Gemini proxy — **LiteLLM on `:4000` fronting 6 Gemini free-tier
keys, failing over only on RPD (daily-quota) 429s** (`configs/litellm_gemini.yaml` +
`rotation_cooldown.py`). No new proxy needed, because the seed shell loop already speaks the
Anthropic API, and the proxy already exposes an Anthropic endpoint (the GDPval grader uses it
the same way).

Two surfaces, one proxy:

```
seed shell loop  ─ anthropic SDK ─▶ :4000        (Anthropic endpoint)  ┐
ACE brain (R/C)  ─ OpenAI client ─▶ :4000/v1      (OpenAI endpoint)     ├▶ 6× Gemini keys
                                                                        ┘   RPD 429 → retire key ~4h
                                                                            RPM/TPM → 65s cooldown
```

Wiring (env only; the anthropic SDK auto-reads `ANTHROPIC_BASE_URL`):

- shell loop → `ANTHROPIC_BASE_URL=http://localhost:4000`
- ACE brain → `GEMINI_BASE_URL=http://localhost:4000/v1` + `ACE_API_PROVIDER=gemini`

Code touch-ups in both adapters: explicit `base_url` pass-through + startup log; a misconfig
warning (proxy base_url but `ACE_API_PROVIDER=anthropic`); `_PRICING["gemini"] = (0,0)`
(free tier). New launcher **`scripts/run_terminal_gemini.sh`** mirrors `run_gdpval_gemini.sh`
(arms baseline/fbl, both surfaces routed, `--concurrency 1`, `--resume`, `TASKS`/`ENDPOINT`
passthrough, `PYTHONUNBUFFERED=1`).

## 4. Validation

- **Offline unit tests**: 28/28 pass (`tests/test_ace_terminal.py`, `tests/test_ace_dual_terminal.py`).
- **Seed prompt**: char-diff against the repo's `systemprompt.md` — identical prose.
- **Anthropic→Gemini tool translation** (proxy probe): Gemini returned a `run_shell_command`
  tool call with correct params; `stop_reason=tool_use`.
- **Live end-to-end scored task** (`adaptive-rejection-sampler`, medium):

  | success | score | official verifier | shell commands | model | cost | wall |
  |---|---|---|---|---|---|---|
  | **true** | **1.0** | **9/9 tests** | 7 | gemini-3.1-flash-lite | $0.00 | 179 s |

  ACE brain also ran on Gemini (`reflector`/`curator` logs show `"model":"gemini-3.1-flash-lite"`);
  playbook grew; container was torn down.

**Operational note:** Docker Desktop **Resource Saver** was observed auto-pausing the idle task
container, which stalls `docker exec`. Disable it before large sweeps.

## 5. ITER-40 — the iteration batch

Goal: a representative subset for a *debug → fix → re-run* loop (not sharding). Taking the first
N is biased because TB2 spreads difficulty and domain. ITER-40 (~45%, matching the GDPval
100/220 practice) stratifies on **three axes** at once. Deterministic greedy selection
(new-category-first → shortest-timeout → long-task cap); nested so **first 12 ⊂ first 24 ⊂ 40**
(grow the batch, reuse prior results via resume-skip).

**Axis ① difficulty — replicates the full-set ratio**

| | easy | medium | hard |
|---|---|---|---|
| Full 89 | 4 (4.5%) | 55 (62%) | 30 (34%) |
| ITER-40 | 2 (5%) | 25 (62.5%) | 13 (32.5%) |

**Axis ② domain — all 16/16 categories covered** (big domains proportionally larger; the 7
singleton domains each get 1, so any domain-specific failure surfaces)

| Domain | n | Tasks |
|---|---|---|
| software-engineering (29% of set) | 13 | cobol-modernization, build-pmars, git-leak-recovery, headless-terminal, kv-store-grpc, polyglot-c-py, pypi-server, cancel-async-tasks, gpt2-codegolf, make-doom-for-mips, polyglot-rust-c, torch-pipeline-parallelism, torch-tensor-parallelism |
| debugging | 3 | overfull-hbox, build-cython-ext, merge-diff-arc-agi-task |
| file-operations | 3 | db-wal-recovery, extract-elf, gcode-to-text |
| system-administration | 3 | git-multibranch, nginx-request-logging, configure-git-webserver |
| security | 3 | openssl-selfsigned-cert, fix-code-vulnerability, password-recovery |
| scientific-computing | 2 | modernize-scientific-stack, adaptive-rejection-sampler |
| model-training | 2 | count-dataset-tokens, pytorch-model-cli |
| mathematics | 2 | largest-eigenval, model-extraction-relu-logits |
| data-processing | 2 | log-summary-date-ranges, multi-source-data-merger |
| games / data-science / data-querying / machine-learning / personal-assistant / optimization / video-processing | 1 each | chess-best-move, hf-model-inference, sparql-university, llm-inference-batching-scheduler, constraints-scheduling, portfolio-optimization, video-processing |

**Axis ③ cost (agent timeout) — protects cycle time**

| timeout | n | note |
|---|---|---|
| 600–900 s | 36 | standard budget (dominant) |
| 1200 s | 1 | constraints-scheduling |
| 1800 s | 1 | llm-inference-batching-scheduler |
| 3600 s | 2 | portfolio-optimization, video-processing (**long, capped at 2**) |
| ≥7200 s (mega) | 0 | two 2h-budget tasks **deliberately deferred to the full run** |

So 36/40 sit at the standard budget — no single long task dominates a cycle.

**Note on "hard":** several hard tasks keep a 900 s agent budget while their expert-time
estimates are enormous (`gpt2-codegolf` 2400 min, `sparql-university` 800 min,
`model-extraction-relu-logits`/`make-doom-for-mips` 480 min). The agent gets ~15 min for tasks
a human expert would take tens of hours on → these are expected to mostly fail, which is normal,
useful signal. Batch expert-time span: 15 min → 40 h (median 60 min).

### The batch (paste into `TASKS=`)

```
overfull-hbox,modernize-scientific-stack,build-pmars,chess-best-move,count-dataset-tokens,db-wal-recovery,git-multibranch,hf-model-inference,fix-code-vulnerability,model-extraction-relu-logits,sparql-university,llm-inference-batching-scheduler,log-summary-date-ranges,constraints-scheduling,portfolio-optimization,adaptive-rejection-sampler,build-cython-ext,extract-elf,gcode-to-text,git-leak-recovery,video-processing,cancel-async-tasks,configure-git-webserver,gpt2-codegolf,cobol-modernization,headless-terminal,kv-store-grpc,largest-eigenval,merge-diff-arc-agi-task,multi-source-data-merger,nginx-request-logging,openssl-selfsigned-cert,polyglot-c-py,pypi-server,pytorch-model-cli,make-doom-for-mips,password-recovery,polyglot-rust-c,torch-pipeline-parallelism,torch-tensor-parallelism
```

Nested tiers: **first 12** (…`llm-inference-batching-scheduler`) for a fast harness-bug pass;
**first 24** (…`gpt2-codegolf`) for a lighter cycle; **40** for a GDPval-parity run.

## 6. How to run

```bash
# Terminal 1: the 6-key rotation proxy
bash scripts/start_proxy.sh

# Terminal 2: ITER-40, baseline arm (ARM=fbl for the feedback-loop arm, same tasks)
ARM=baseline RUN_ID=tb2_gem_iter40_base TASKS=<the-40-list-above> \
  bash scripts/run_terminal_gemini.sh
```

Requires Docker reachable and `external/terminal-bench-2` present. `TASKS` overrides `LIMIT`.
Disable Docker Desktop Resource Saver before large sweeps.

## 7. Files

| File | Change |
|---|---|
| `harness/agents/_nexau0.py` | **new** — verbatim NexAU0 seed substrate (prompt + tool + loop) |
| `harness/agents/ace_terminal.py` | re-grounded on the seed; `ANTHROPIC_BASE_URL` routing; gemini pricing; misconfig warning |
| `harness/agents/ace_dual_terminal.py` | same, for the feedback-loop arm |
| `scripts/run_terminal_gemini.sh` | **new** — TB2-on-Gemini launcher (both surfaces via the proxy; `TASKS`/`ENDPOINT` passthrough) |

Unchanged: `harness/benchmarks/terminal_bench.py` (the Env/verifier — the stage, not the
substrate) and `harness/agents/_tb_verifier.py`.
