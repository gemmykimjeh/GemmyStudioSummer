# GAIA Reproduction — Cross-Device Configuration Comparison

**Ours** = this machine (`/Users/yusifgasimov/benchmarks`)
**Theirs** = the parallel setup, per the configuration summary provided.

Everything marked ✅ VERIFIED was checked directly against this machine's files/images during
this session. Items marked ⚠️ UNVERIFIED could not be confirmed here and need action.

---

## Verdict at a glance

| Area | Status |
|---|---|
| Source code (benchmarks + SDK) | ✅ **Functionally identical** |
| MCP `fetch` pin | ✅ **Same resolved versions**, different delivery mechanism |
| ffmpeg layer | ❌ **Real difference — they have it, we don't** |
| `temperature` | ⚠️ **Claim disputed — likely a doc error on their side, needs confirmation** |
| Workers | Difference, throughput only — not a correctness concern |
| Host platform | Difference (we emulate amd64), affects runtime not results |

Two things to act on: the **ffmpeg layer** (adopt theirs) and the **temperature claim**
(resolve which of us is right before either result is quoted as a reproduction).

---

## 1. Source code

### 1a. `OpenHands/benchmarks`

| | Commit |
|---|---|
| Theirs | `3a0cc314` |
| Ours | `4c1c1e8a` |

These differ, but harmlessly. ✅ VERIFIED: `3a0cc314` is the **direct parent** of `4c1c1e8a`.

```
$ git merge-base --is-ancestor 3a0cc314 4c1c1e8a   →  true (ours is newer)
$ git log --oneline 3a0cc314..4c1c1e8a
  4c1c1e8a docs: add resume run-infer documentation to all benchmark READMEs (#562)
```

The entire delta:

```
$ git diff --stat 3a0cc314 4c1c1e8a
 benchmarks/commit0/README.md              | 2 ++
 benchmarks/gaia/README.md                 | 2 ++
 benchmarks/multiswebench/README.md        | 2 ++
 benchmarks/openagentsafety/README.MD      | 2 ++
 benchmarks/swebench/README.md             | 2 ++
 benchmarks/swebenchmultilingual/README.md | 2 ++
 benchmarks/swebenchmultimodal/README.md   | 2 ++
 benchmarks/swefficiency/README.md         | 2 ++
 benchmarks/swtbench/README.md             | 2 ++
 9 files changed, 18 insertions(+)
```

**Nine README files, 18 inserted lines, zero executable code.** No difference in behaviour.
Note the brief specifies `4c1c1e8a`, so ours follows the brief; theirs is one docs commit behind.
Either is fine.

### 1b. SDK submodule

| | Commit |
|---|---|
| Theirs | `9836b772` (`v1.14.0-60-g9836b772`) |
| Ours | `9836b772` |

✅ **Identical.** Both deliberately override the recorded pointer:

```
recorded : 62c2e7cf   ← what benchmarks pins
actual   : 9836b772   ← the PR #2547 head actually used by the reference run
```

✅ VERIFIED on our side: `BROWSER_TOOLS` is present in
`openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2`, and the submodule survived
`make build` without snapping back. Both setups leave the working tree dirty (submodule
pointer only), which is the intended state.

Supporting evidence for this being the right commit — the GHCR image tags:

```
9836b77-gaia-binary  → 200 (exists)
62c2e7c-gaia-binary  → 404 (never built)
```

So the stale pointer could not have produced a runnable image in this configuration anyway.

### 1c. Modified files

Both: **none**. Only the submodule pointer moves. ✅ Matches on both sides.

---

## 2. Container image

Both start from the same GHCR artifact (`ghcr.io/openhands/eval-agent-server:9836b77-gaia-binary`,
built ~4 months ago) and layer on top. Both keep an untouched backup.

### 2a. MCP `fetch` pin — same outcome, different mechanism

| | Approach |
|---|---|
| Theirs | `uv tool install mcp-server-fetch==2025.4.7 --with mcp==1.26.0` |
| Ours | `/etc/uv/uv.toml` (+ user copy) with `exclude-newer = "2026-03-23T00:00:00Z"` |

✅ VERIFIED: ours resolves to **exactly the same pair** they pinned explicitly —

```
mcp_server_fetch-2025.4.7.dist-info
mcp-1.26.0.dist-info
```

We independently diagnosed the same root cause they describe: `run_infer.py:345` declares
`{"command": "uvx", "args": ["mcp-server-fetch"]}` with **no version**, so `uvx` re-resolves at
every launch and today picks `mcp-server-fetch 2026.7.10` + `mcp 2.0.0`, which dies on the
`McpError` → `MCPError` rename. Their phrase "tool vanishes silently" is accurate and worth
underlining — the failure is not loud.

On our side it was *not* silent, because it killed the whole run:

```
mcp.shared.exceptions.McpError: Connection closed
  → 500 on POST /api/conversations/{id}/events
  → 0 instances produced results
```

**Difference that matters:** our `exclude-newer` is **container-wide**; theirs is scoped to the
one tool. Ours therefore also constrains any other `uv` operation in the container — notably
`uvx playwright install chromium` (`openhands-tools/.../browser_use/impl.py:122`). Arguably that
is *more* faithful to a March-2026 environment, but it is a broader surface than necessary and
was not something the reference run did deliberately.

> **Recommendation:** theirs is the cleaner mechanism. If we re-image, prefer the scoped
> `uv tool install` pin. Our runs so far show no ill effect from the broad version, so this is
> a tidiness/robustness point, not a correctness bug.

**Lesson learned on our side (documented so it isn't repeated):** our *first* attempt delivered
the pin as `ENV UV_EXCLUDE_NEWER` in the image. That was **inert** — the agent-server spawns MCP
children with a sanitized environment, so the variable was stripped before `uvx` ran. It passed a
naive `docker run` test (which inherits the full env) and failed in the real spawn path. The
config-file approach was validated with a proper negative control under `env -i PATH=… HOME=…`.

### 2b. ffmpeg — ❌ REAL DIFFERENCE

| | ffmpeg |
|---|---|
| Theirs | `apt-get install ffmpeg` baked into the image |
| Ours | **not installed** |

Their rationale: a 27 s install against a 30 s timeout; original run failed 1/165, their local
failed 2/5.

We independently observed this failure mode during our QEMU-era run:

```
WARNING - Failed to install ffmpeg: Command timed out after 30.0 seconds
ERROR   - FFmpeg installation failed completely:
          E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 143 (apt-get)
```

⚠️ **UNVERIFIED:** whether our two *successful* pilot runs were affected. Two greps over
`outputs/gaia-pilot*/…/logs/` returned **contradictory** counts (one suggested 12 and 23 matching
lines; a per-file check returned 0 affected instances), and the reconciling check was not
completed. **This number should not be quoted until re-checked.**

> **Action:** re-run the count cleanly, and adopt their ffmpeg layer regardless. It is a
> strict improvement — it removes a timeout-dependent failure that silently degrades any GAIA
> task needing audio/video handling. This is the single most valuable thing to take from their
> configuration.

---

## 3. Runtime parameters

### 3a. Confirmed identical

✅ VERIFIED from our `metadata.json`:

| Parameter | Value |
|---|---|
| level | `2023_all` |
| split | `validation` |
| model | `anthropic/claude-sonnet-4-5-20250929` |
| tool_preset | `default` |
| agent_type | `default` |
| max_iterations | `500` |
| condenser | enabled, `max_size=240`, `keep_first=2` |
| critic | `PassCritic` |
| max_retries | `3` |
| workspace_type | `docker` |
| dataset | `gaia-benchmark/GAIA` |

### 3b. ⚠️ `temperature` — their claim is disputed

They list **`temperature 0.0`** under "Identical". Our run records:

```json
"temperature": null
```

✅ VERIFIED — and the SDK is explicit about what `null` means:

```
openhands-sdk/openhands/sdk/llm/llm.py:195
  temperature: float | None = Field(
    "Defaults to None (uses provider default temperature)."
```

For Anthropic the provider default is **1.0**, not 0.0. So if they genuinely run at 0.0, we are
sampling very differently and the two results are not directly comparable.

**However, I think their documentation is more likely wrong than their setup.** They report *zero
source modifications*, and nothing in the unmodified path sets temperature: the brief's
`.llm_config` template contains only `model` and `api_key`, and no GAIA config file assigns a
temperature. An unmodified run therefore lands on `null`, exactly as ours did. Their "0.0" is
probably a transcription of an assumed default rather than an observed value — unless they added
`"temperature": 0.0` to their own `.llm_config` JSON, which would be a *config* change even
though it is not a *source* change.

There is a second, subtler consequence. The retry path is gated on an exact comparison:

```python
# benchmarks/utils/evaluation.py:385
if attempt > 1 and original_temperature == 0.0:
    logger.info("Adjusting temperature from 0.0 to 0.1 for retry attempt")
```

At `temperature=0.0` a retry re-rolls at 0.1; at `null` this **never fires**, so our retries
repeat at the provider default. Different retry semantics, not just different sampling.

> **Action — do this before either result is published as a reproduction.** Ask them to print the
> actual value from their `metadata.json` (not their config file). If it really is `0.0`, one of
> us must change to match, and we should decide which is faithful to the reference run. The index
> does **not** publish this: ✅ VERIFIED that neither
> `results/claude-sonnet-4-5/metadata.json` nor `scores.json` records any temperature, so the
> original value cannot be recovered from the index and would have to come from the run archive.

### 3c. Deliberate, understood differences

| Parameter | Reference | Theirs | Ours | Impact |
|---|---|---|---|---|
| workspace | `remote` | `docker` | `docker` | Forced — no `RUNTIME_API_KEY`. Same on both. |
| routing | `litellm_proxy/` | `anthropic/` | `anthropic/` | Same model. Cost accounting differs from the index's proxy-measured $0.87. |
| workers | 30 | 4 | 4 (pilots), **8 planned** for full run | Throughput only. Does not affect per-instance results. |

The workers difference is the only live one: they cite a 16.5 GB memory constraint at 4. We saw
**zero 429s at 4**, so 8 is a reasonable step up here, but it is a divergence from their setup and
should be noted if runtimes are compared.

---

## 4. Host platform — ours is the unusual one

Not in their summary, but material:

| | Ours |
|---|---|
| Host | macOS 26.5.1, **arm64** (Apple Silicon) |
| Docker | Colima on macOS Virtualization.Framework, **Rosetta** enabled |
| Image arch | **linux/amd64 only** → runs **emulated** |

✅ VERIFIED the image is single-arch amd64, so on this machine every container is translated.
This cost us a full debugging cycle: under **QEMU** (before Rosetta), `uv` emitted jemalloc
warnings on **stderr**, which corrupted the MCP stdio handshake and surfaced as the agent-server
500. Enabling Rosetta removed the stderr noise entirely.

Practical effect: **~27% slower per instance** than the reference (327 s vs 258 s in the smoke
test). If they are on native amd64 Linux, runtime comparisons between our two setups are not
apples-to-apples — but **scores should still be comparable**, since emulation affects speed, not
model output.

---

## 5. Our results so far (for their comparison)

Two disjoint stratified 17-instance samples, seeds 42 and 43:

| | Batch 1 | Batch 2 | Combined |
|---|---|---|---|
| Score | 14/17 = 82.4% | 14/17 = 82.4% | **28/34 = 82.4%** |
| Reference (same subsets) | 13/17 = 76.5% | 13/17 = 76.5% | **26/34 = 76.5%** |
| Cost | $10.79 ($0.635/inst) | $10.44 ($0.614/inst) | $21.23 ($0.624/inst) |
| Runtime @ 4 workers | 23m52s | 24m48s | — |
| Tavily calls / errors | 84 / 0 | 87 / 0 | 171 / 0 |
| Tool errors | `fetch_fetch: 2` | `fetch_fetch: 1` | — |

**+5.9 pts vs reference on 34 instances**, which sits inside the ~±8 pt binomial band at that n —
read as *matches reference*, not *beats it*. Both batches landing on identical 14/17 with the same
1-regression / 2-gain churn is a strong stability signal.

Index target being reproduced (✅ VERIFIED from `scores.json`):

```json
{"benchmark": "gaia", "score": 72.7, "cost_per_instance": 0.87,
 "average_runtime": 258.0, "agent_version": "v1.15.0",
 "submission_time": "2026-03-23T15:26:18+00:00"}
```

Note the model-level `metadata.json` reports `agent_version: v1.8.3` / January submission — that
is the *model row*, not the GAIA row. Use the per-benchmark entry in `scores.json`.

---

## 6. Action items

1. **Adopt their ffmpeg layer.** Strict improvement, removes a silent timeout-dependent failure.
2. **Resolve the temperature question** before either number is published. Get their observed
   `metadata.json` value, not their intended one.
3. **Re-check our ffmpeg failure count** — current numbers are contradictory and unusable.
4. *(Optional)* Narrow our MCP pin from container-wide `exclude-newer` to their scoped
   `uv tool install` form on the next re-image.
5. **Decide workers** for the full run — 8 (ours) vs 4 (theirs) — if runtimes are to be compared.

## 7. Bottom line

The two setups are **substantively the same experiment**. The source code is identical in
behaviour, the SDK commit matches exactly, the MCP fix resolves to the same versions, and every
consequential runtime parameter agrees. The genuine gaps are **ffmpeg** (theirs is better; adopt
it) and **temperature** (probably a documentation artifact, but must be confirmed). Our host
platform difference affects speed, not correctness.
