# GAIA Reproduction — Session Handoff

**Target:** OpenHands Index row `gaia / claude-sonnet-4-5 = 72.7%` (120/165, $0.87/inst, 258 s/inst)
**Working dir:** `/Users/yusifgasimov/benchmarks`
**Date of this handoff:** 2026-08-06
**Status:** Environment fully configured and validated. **No full run has been executed.**
Next action is a re-baseline pilot at `temperature=0.0`, then the full 165-instance run.

---

## 1. Current state — one-line summary

Everything is aligned with the reference run. Three image layers and one config change were
required to get there, all verified. The pilot results collected *before* the temperature fix
are **not valid as fidelity measurements** and must be re-established.

---

## 2. Exact pins (do not change)

| Thing | Value | Verified how |
|---|---|---|
| `OpenHands/benchmarks` | `4c1c1e8a` | `git rev-parse` |
| SDK submodule | `9836b772` | `git -C vendor/software-agent-sdk rev-parse` |
| SDK full SHA (authoritative) | `9836b77283495e33d18dd71dbfd61be86db854c7` | original run's `params.json` |
| `BROWSER_TOOLS` in system prompt | PRESENT | grep on `system_prompt.j2` |
| Agent-server image tag | `ghcr.io/openhands/eval-agent-server:9836b77-gaia-binary` | derived from SDK short SHA |

The submodule is **deliberately dirty** — `git status` shows only `M vendor/software-agent-sdk`.
Do **not** commit it, do not `git pull`, do not run `git submodule update` (it will revert the
pointer to the stale `62c2e7cf`).

If the submodule ever reverts:
```bash
cd vendor/software-agent-sdk
git fetch origin +refs/pull/2547/head:refs/remotes/origin/pr-2547
git checkout 9836b772
cd ../.. && uv sync --dev      # NOT `make build` — it re-runs submodule update
```

### Primary-source validation

Fetched from the published archive (free, no API cost):

```
https://results.eval.all-hands.dev/gaia/litellm_proxy-claude-sonnet-4-5-20250929/23440285883/metadata/params.json
```

```json
{
  "timestamp": "2026-03-23T13:41:45Z",
  "sdk_commit": "9836b77283495e33d18dd71dbfd61be86db854c7",
  "llm_config": { "model": "litellm_proxy/claude-sonnet-4-5-20250929", "temperature": 0.0 },
  "trigger_reason": ".../issue/AGE-991/sdk-change-description-webbroswertool",
  "max_retries": 3, "tool_preset": "default", "agent_type": "default"
}
```

This independently confirms the SDK pin, the run timestamp, **and `temperature: 0.0`**.

---

## 3. Configuration changes made

### 3a. LLM config — `temperature: 0.0` (CRITICAL)

`.llm_config/sonnet45.json` (gitignored, perms `600`):

```json
{
  "model": "anthropic/claude-sonnet-4-5-20250929",
  "api_key": "<ANTHROPIC KEY — in file, not reproduced here>",
  "temperature": 0.0
}
```

Backup of the pre-fix version: `.llm_config/sonnet45.json.bak-notemp`

**Why this matters.** The SDK declares `temperature: float | None`, and `None` means *provider
default* (~1.0 for Anthropic), **not** 0.0. A bare config with only `model` + `api_key` silently
runs at ~1.0 — a genuine divergence from the 72.7% baseline. Setting it in the LLM config is
**not** a source modification; that file is a CLI input.

> **Always verify `"temperature": 0.0` in the run's `metadata.json`, not just in the config file.**

### 3b. Credentials

| Item | Location / status |
|---|---|
| Anthropic key | inside `.llm_config/sonnet45.json` (perms 600, gitignored) |
| Tavily key | `.llm_config/tavily.env` — `source` it before every run |
| HuggingFace | logged in via `hf auth login`, token name `gaia`; GAIA terms accepted |

⚠️ **All three secrets were pasted into a chat transcript. Rotate them when convenient:**
Anthropic console, Tavily dashboard, huggingface.co/settings/tokens.

Verify HF access: `load_dataset('gaia-benchmark/GAIA','2023_all',split='validation')` → 165.

---

## 4. Container image — 3 added layers

Base is the **original GHCR artifact**, pulled not rebuilt. Dockerfiles are saved in
`outputs/docker/` (the originals lived in a session-scoped scratchpad and are gone).

| Tag | Size | Contents | Status |
|---|---|---|---|
| `gaia-orig-backup:9836b77` | 4.69 GB | untouched GHCR original | rollback |
| `gaia-mcpfix:test` | 4.80 GB | **inert** `ENV` attempt — kept as negative control | do not use |
| `gaia-mcpfix:v2` | 4.83 GB | + MCP pin | batches 1 & 2 ran here |
| `gaia-mcpfix:v3` | 5.08 GB | + ffmpeg | batch-1 rerun ran here |
| **`gaia-mcpfix:v4`** | **14.5 GB** | **+ whisper** | **ACTIVE** `sha256:d5f08355…` |

Active image is set by tagging:
```bash
docker tag gaia-mcpfix:v4 ghcr.io/openhands/eval-agent-server:9836b77-gaia-binary
```
`create_docker_workspace` checks `local_image_exists()` first, so a local tag wins and no pull
occurs. Rebuild any layer from `outputs/docker/Dockerfile.v*`.

### Layer 1 — MCP fetch pin (`Dockerfile.v2-mcp-pin`)

`run_infer.py:345` declares the fetch MCP server with **no version**
(`{"command":"uvx","args":["mcp-server-fetch"]}`), so `uvx` re-resolves against PyPI on every
launch. Today that yields `mcp-server-fetch 2026.7.10` + `mcp 2.0.0`, and `mcp` 2.0.0 renamed
`McpError` → `MCPError`, so the server dies on import.

Our fix writes `/etc/uv/uv.toml` (+ user copy) with `exclude-newer = "2026-03-23T00:00:00Z"`,
which resolves to exactly `mcp-server-fetch 2025.4.7` + `mcp 1.26.0`.

> ⚠️ **A previous attempt using `ENV UV_EXCLUDE_NEWER` was completely inert.** The agent-server
> spawns MCP children with a **sanitized environment**, so the variable was stripped before
> `uvx` ran. It passed a naive `docker run` test (which inherits the full env) and failed in the
> real spawn path. **Any env-var-based fix in this image must be tested under
> `env -i PATH=… HOME=…`, not plain `docker run`.**

The parallel team used a narrower, arguably cleaner form achieving the same versions:
`uv tool install "mcp-server-fetch==2025.4.7" --with "mcp==1.26.0"`. Consider switching on
any future re-image.

### Layer 2 — ffmpeg (`Dockerfile.v3-ffmpeg`)

`run_infer.py:270` runs `sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg` per
instance against a **30 s timeout**; the install measures ~27 s and loses the race under
concurrency, after which the killed apt holds the dpkg lock and the fallback also fails.

Pre-installed → the same command now completes in **4 s**. Verified 0 ffmpeg failures across a
full 17-instance run.

### Layer 3 — whisper (`Dockerfile.v4-whisper`)

The `.mp3` tasks are solved with **whisper**, not ffmpeg. The agent runs
`pip install openai-whisper` against a **120 s** timeout; measured cold install is ~474 s, plus a
139 MB checkpoint download. The original run's infrastructure absorbed both; local bandwidth
cannot.

> **Critical detail:** checkpoints must be preloaded as the **`openhands`** user. Preloading as
> root populates `/root/.cache/whisper`, and the agent (running as `openhands`) would silently
> re-download 705 MB at runtime, defeating the layer. The Dockerfile splits `USER` accordingly.

Verified with `--network none`: `whisper` at `/usr/local/bin/whisper`; `tiny.pt`/`base.pt`/
`small.pt` cached under `/home/openhands/.cache/whisper`; `pip install openai-whisper` returns in
**1 s**; end-to-end transcription of an mp3 in 7 s.

### Deliberately NOT added

`tesseract-ocr`, `poppler-utils`, `python3.11` — the parallel team's audit of all 165 original
instance logs shows these apt installs **failed in the original run too** (the agent omitted
`sudo`). Adding them would make us *better* than the baseline rather than equal to it. Hold this
line.

---

## 5. Host environment — non-obvious and important

| | |
|---|---|
| Host | macOS 26.5.1, **arm64** (Apple Silicon) |
| Docker | **Colima** (NOT Docker Desktop), `docker context show` → `colima` |
| VM | macOS Virtualization.Framework **with Rosetta** |
| Image arch | **linux/amd64 only** → runs emulated |

> ⚠️ **Rosetta is mandatory, not an optimization.** Under plain QEMU, `uv` emits jemalloc
> warnings on **stderr**, which corrupt the MCP stdio handshake and surface as
> `500 Internal Server Error` on `POST /api/conversations/{id}/events` — every instance fails,
> zero results. Enabling Rosetta removed the stderr noise entirely.

If Docker is ever restarted/recreated:
```bash
colima start --vm-type vz --vz-rosetta --cpu 8 --memory 12 --disk 100
```
**Test that Rosetta is really active** (this is the decisive check):
```bash
docker run --rm --platform linux/amd64 --entrypoint bash \
  ghcr.io/openhands/eval-agent-server:9836b77-gaia-binary -c 'uv --version 2>&1 1>/dev/null'
# Must print NOTHING. Any jemalloc/QEMU output = Rosetta is off.
```
Note: enabling "Use Rosetta" in **Docker Desktop** has no effect here — Docker Desktop is not the
active daemon.

Runtime cost of emulation: ~**27% slower** per instance than the reference (327 s vs 258 s).

---

## 6. Results so far — and why they are NOT a reproduction

**All runs below used `temperature = None` (~1.0), i.e. the WRONG sampling regime.**
They validate the *harness*, not fidelity to 72.7%.

| Run | Image | Score | Cost | Tavily |
|---|---|---|---|---|
| Smoke (2 inst) | v2 | 2/2 | — | 5 |
| Batch 1 (17, seed 42) | v2 | 14/17 = 82.4% | $10.79 | 84 |
| Batch 2 (17, seed 43, disjoint) | v2 | 14/17 = 82.4% | $10.44 | 87 |
| Batch 1 re-run | v3 | **11/17 = 64.7%** | $13.41 | 94 |
| **Mean of the three batches** | | **13/17 = 76.5%** | | |
| Reference on those subsets | | 13/17 = 76.5% | | |

**Key finding: run-to-run variance at provider-default temperature is ~±3 instances on 17
(≈ ±17 percentage points).** Batch 1 scored 14/17 then 11/17 on the *same* instances with an
effectively identical config (v2→v3 differs only by ffmpeg, which cannot cause regressions; none
of the 3 flipped tasks is audio-related).

So the earlier "82.4%, +5.9 pts vs reference" reading was **over-confident** — a single high draw
from a wide distribution. The mean across three batches lands exactly on reference. Expect much
tighter spread at `temperature=0.0`.

**Success band for a full run: 69–76%** (±3.5 pp binomial at 1σ over 165, plus web variance).
GAIA is *not* fully deterministic even at 0.0 — the web changes between runs.

Result files:
```
outputs/RESULTS_batch1.json              Batch 1 (v2)
outputs/RESULTS_batch2.json              Batch 2 (v2)
outputs/RESULTS_batch1_rerun_v3.json     Batch 1 re-run (v3)
outputs/RESULTS_batch1_rerun_v3.md       write-up incl. flipped-instance analysis
outputs/COMPARISON_vs_other_device.md    cross-device config comparison
outputs/docker/Dockerfile.v{2,3,4}-*     image layer definitions
pilot_ids.txt / pilot2_ids.txt           stratified 17-instance sets (seeds 42 / 43)
```

---

## 7. Tavily budget — the binding constraint

| | |
|---|---|
| Plan | **Researcher (free), 1,000 credits/month** |
| Metered at handoff | **~198** |
| Observed calls | ~270 (smoke 5 + b1 84 + b2 87 + rerun 94) |
| Rate | **~5 calls/instance** |
| Full run needs | **~825** |

> The usage counter **lags 30–60 minutes** but is accurate once caught up (it read 6 while 84
> calls had already landed, then jumped to 88, then 198). Do not trust an unchanged reading
> mid-run; plan against the observed rate.

**A full run will likely exceed the remaining balance.** Exhaustion returns HTTP **432** and the
agent simply continues **without** search — silent degradation, not a crash. Options: wait for
the monthly reset, or add ~$12 pay-as-you-go to remove the cap (recommended by the parallel team
for exactly this reason).

---

## 8. Gotchas discovered (each cost real debugging time)

1. **`--workspace docker` is mandatory.** Default is `remote`, which needs `RUNTIME_API_KEY`.
2. **Rosetta, not QEMU** — see §5. Zero results otherwise.
3. **MCP env vars are stripped** on subprocess spawn — see §4 Layer 1. Test with `env -i`.
4. **`output.jsonl` is written incrementally**, not only at the end — partial results are
   readable mid-run. But `output_errors.jsonl` existing (and `output.jsonl` at 0 bytes) is the
   signal of a failed run.
5. **Exit code 0 means nothing.** The harness catches instance exceptions and exits clean.
   **Check `output.jsonl` size.**
6. **`temperature` defaults to provider default, not 0.0** — see §3a.
7. **Whisper checkpoints must be cached for the `openhands` user** — see §4 Layer 3.
8. **The reference output path is nested**, not `outputs/<dir>/output.jsonl`:
   `outputs/<dir>/gaia-2023_all-validation/anthropic/claude-sonnet-4-5-20250929_sdk_9836b77_maxiter_500/output.jsonl`
9. **Never pass `--select` and `--n-limit` together** — `eval_limit` is silently ignored.
10. **`--n-limit` is not a random sample** — GAIA uses `df.head(n)`, dataset order. Use
    `--select` with a stratified ID file for any meaningful subset.
11. **Index version labels are unreliable.** Model-level `metadata.json` says `v1.8.3`; the gaia
    row in `scores.json` says `v1.15.0`; the actual code is `9836b772`, in neither. **Pin by SHA.**

---

## 9. Exact next commands

Always `source .llm_config/tavily.env` first — `run_infer.py:330` hard-asserts on the key.

### Step 1 — Re-baseline at temperature 0.0 (~$10, ~25 min, ~85 credits)

```bash
cd /Users/yusifgasimov/benchmarks
source .llm_config/tavily.env
uv run python -m benchmarks.gaia.run_infer \
    .llm_config/sonnet45.json \
    --level 2023_all --split validation --workspace docker \
    --select pilot_ids.txt --num-workers 4 \
    --output-dir outputs/gaia-baseline-t0 \
    2>&1 | tee outputs/gaia-baseline-t0.log
```

**Then immediately verify the temperature actually took effect:**
```bash
grep -o '"temperature":[^,]*' outputs/gaia-baseline-t0/*/*/*/metadata.json    # must be 0.0
```

Reusing `pilot_ids.txt` gives a direct comparison against the two runs already measured on those
same 17 instances (14/17 and 11/17 at default temperature).

Score it:
```bash
uv run python -m benchmarks.gaia.get_score --file outputs/gaia-baseline-t0/*/*/*/output.jsonl
```
Reference on this subset: **13/17 = 76.5%**.

### Step 2 — Full run (~$103, ~2–2.5 h at 8 workers)

Only after Step 1 looks sane, and after resolving the Tavily budget.

```bash
source .llm_config/tavily.env
uv run python -m benchmarks.gaia.run_infer \
    .llm_config/sonnet45.json \
    --level 2023_all --split validation --workspace docker \
    --num-workers 8 --output-dir outputs/gaia-sonnet45 \
    2>&1 | tee outputs/gaia-sonnet45-run.log
```

Resumable — re-running the same command with the same `--output-dir` skips completed instances.
Drop to 4 workers if 429s appear (none seen at 4).

### Step 3 — Score and per-instance diff

```bash
uv run python -m benchmarks.gaia.get_score --file outputs/gaia-sonnet45/*/*/*/output.jsonl
```
Compare against `/tmp/openhands-index-results/results/claude-sonnet-4-5/instance_results/gaia.json`
(re-clone if `/tmp` was cleared:
`git clone https://github.com/OpenHands/openhands-index-results.git /tmp/openhands-index-results`).

---

## 10. Open items

1. **Tavily budget** — resolve before the full run (§7). Highest priority.
2. **Re-baseline at 0.0** — no valid fidelity measurement exists yet.
3. **ffmpeg failure count in the v2 pilots** — never cleanly measured; two greps disagreed.
   Moot going forward (v4 has ffmpeg), but the earlier numbers should not be quoted.
4. **Optional:** narrow the MCP pin from container-wide `exclude-newer` to the scoped
   `uv tool install` form (§4 Layer 1).
5. **Rotate the three secrets** (§3b).

---

## 11. Expected outcome

With config and image now matching the reference, a full 165-instance run should land in
**69–76%** against the target of 72.7%. Projected cost ~**$103** (reference $142.50 through the
LiteLLM proxy; our direct-key runs have consistently come in below reference per instance).
Wall clock ~2–2.5 h at 8 workers, inflated ~27% by amd64 emulation.
