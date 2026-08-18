# Reproduction Delta Report — OpenHands GAIA-1 (claude-sonnet-4-5, 72.7 %)

**Target:** OpenHands Index entry `gaia / claude-sonnet-4-5 = 72.7 %`
**Provenance of the original run:**
`results.eval.all-hands.dev/gaia/litellm_proxy-claude-sonnet-4-5-20250929/23440285883/`
(`output.report.json`: 165 submitted / 164 completed / 120 resolved → 120 / 165 = 72.7 %)

---

## 1. Source code — ZERO modifications

| Repo | Pinned to | Tracked files modified |
|---|---|---|
| `OpenHands/benchmarks` | `3a0cc314` (main @ 2026-03-23) | **none** |
| `OpenHands/agent-sdk` (submodule) | `9836b772` = `v1.14.0-60-g9836b772` | **none** |

The single `git status` entry is the submodule pointer, moved on purpose:

```
recorded pin : 62c2e7cf   ← what benchmarks@3a0cc314 records
actual       : 9836b772   ← what params.json says the original run used
```

`9836b772` is a **PR-branch head that was never merged** (`git tag --contains` is empty; it is
not an ancestor of `v1.15.0`). It adds a 12-line `<BROWSER_TOOLS>` system-prompt block that
governs browser strategy — action caps, CAPTCHA handling, "commit to your best answer after
20 steps". GAIA is browser-heavy, so this materially changes behaviour. Confirmed live: the
string `Max 10 browser actions per sub-task` appears in our instance logs.

> The index's own version labels are unreliable. `metadata.json` says `v1.8.3` — a version
> that exists in no git tag, no `pyproject.toml`, and not on PyPI. `scores.json` says
> `v1.15.0` — a real tag that does **not** contain this commit. Pin by SHA, never by version.

## 2. Container image — 3 added layers

Base: `ghcr.io/openhands/eval-agent-server:9836b77-gaia-binary`, pulled from GHCR. This is the
**original artifact** of the 2026-03 run (created 4 months ago), not a rebuild.

Every layer meets the same three-part test: **(a)** the original demonstrably had it,
**(b)** it is re-fetched over the network on every instance, **(c)** it fails in our
environment. Rollback tags are kept at each step.

| Tag | Contents |
|---|---|
| `…-orig` | untouched GHCR original (4.69 GB) |
| `…-pre-whisper` | + Layer 1 + Layer 2 (5.04 GB) |
| `…` (active) | + Layer 3 (14.5 GB) |

### Layer 1 — MCP version pin

```dockerfile
RUN uv tool install "mcp-server-fetch==2025.4.7" --with "mcp==1.26.0"
```

`run_infer.py` declares the fetch MCP server with **no version**:
`{"command": "uvx", "args": ["mcp-server-fetch"]}` — note that tavily right beside it *is*
pinned at `@0.2.1`. `uvx` re-resolves against PyPI on every launch. Today that yields
`mcp-server-fetch 2026.7.10 + mcp 2.0.0`, and `mcp` 2.0.0 renamed `McpError` → `MCPError`,
so the server dies with `ImportError` before serving a request. The agent then runs with the
`fetch` tool silently absent — no crash, no log line, just worse answers.

Verified after the pin: JSON-RPC `initialize` + `tools/list` return
`serverInfo {name: mcp-fetch, version: 1.26.0}` and `tools: ["fetch"]`, and the server starts
with networking disabled — it no longer depends on PyPI state at all.

### Layer 2 — ffmpeg pre-install

```dockerfile
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg
```

`run_infer.py:270` installs ffmpeg per instance at setup. Measured in-container:
`apt-get update` 2 s + `install ffmpeg` 25 s = **27 s against a 30 s command timeout**.
Under concurrency this overruns; the killed apt then holds the dpkg lock, so the fallback
install fails too.

| | ffmpeg install failures |
|---|---|
| Original (their cloud runtime, 30 workers) | **1 / 165** |
| Ours (local Docker, 2 workers) | **2 / 5** |

Full audit of all 165 original per-instance logs: 164 succeeded, 1 failed (`ed58682d` — an
instance with no attachment, so ffmpeg was not needed, and it was scored **correct** anyway).
All three ffmpeg-relevant instances (the `.mp3` tasks) installed successfully in the original;
their logs show `which ffmpeg → /usr/bin/ffmpeg`, with `command not found` count = 0.
**No instance in the 72.7 % failed for lack of ffmpeg.**

### Layer 3 — openai-whisper + torch + model weights

```dockerfile
RUN python3 -m pip install --no-cache-dir openai-whisper
RUN python3 -c "import whisper; [whisper.load_model(m) for m in ('base','tiny','small')]"
ENV PATH="/home/openhands/.local/bin:${PATH}"
```

The `.mp3` tasks are not solved with ffmpeg — the agent transcribes them with **whisper**:

```
$ which whisper
$ pip install openai-whisper [timeout: 120.0s]
$ export PATH=$PATH:/home/openhands/.local/bin && whisper /workspace/file.mp3
```

Isolated cold-install timing in our container: **474 s against the agent's 120 s timeout**
(3.95× over; even the agent's maximum observed timeout of 300 s cannot cover it). On top of
that, `whisper` downloads a **139 MB model checkpoint** at first use — the original logs show
this completing at 180 MiB/s, i.e. their infrastructure was roughly 28× faster than ours.
Without this layer the mp3 tasks fail for bandwidth reasons alone; the original resolved
2 of 3.

Verified with networking disabled: whisper transcribes a real GAIA `.mp3` in 11 s, and
`pip install openai-whisper` now returns in **1 s** instead of 474 s.

### Deliberately NOT added

| Candidate | Why excluded |
|---|---|
| `tesseract-ocr`, `poppler-utils`, `python3.11` | The original's apt installs **all failed** (agent omitted `sudo`; no `Setting up` lines; `pdftotext: command not found` in 9 instances). Adding them would make us better than the original. |
| The other 19 pip packages | Isolated timings are 1–10 s against 30–60 s timeouts — margins of 6–60×. ffmpeg failed at a 1.2× margin; nothing else is close. No evidence, no change. |
| 22 external URLs (`curl`/`wget`) | Task-specific web content (arXiv PDFs, nature.com, clinicaltrials.gov, worldbank ZIP…). Cannot be pre-staged; inherent GAIA variance. |

## 3. Runtime parameters

### Identical to the original `params.json`

| Parameter | Value |
|---|---|
| level / split | `2023_all` / `validation` (165 instances) |
| model | `claude-sonnet-4-5-20250929` |
| temperature | `0.0` |
| tool_preset / agent_type | `default` / `default` |
| max_iterations | `500` |
| condenser | on, `max_size=240`, `keep_first=2` |
| critic | `pass` |
| max_retries | `3` |

### Necessarily different

| Parameter | Original | Ours | Reason |
|---|---|---|---|
| workspace | `remote` (`runtime.eval.all-hands.dev`) | `docker` (local) | no `RUNTIME_API_KEY` |
| model routing | `litellm_proxy/claude-sonnet-4-5-20250929` | `anthropic/claude-sonnet-4-5-20250929` | same model, direct API |
| num_workers | `30` | `4` | 15 GB Docker memory ceiling |

### LLM configuration — field-by-field verification

The `LLM` object is serialised in full into each run's `metadata.json`, so the original's
settings can be compared against ours exactly rather than inferred. Diffing the original
archive's `metadata.json` against ours (`eval_outputs/gaia_repro_hard/.../metadata.json`):

**36 of 38 fields are byte-identical**, including every sampling and budget parameter:

| Field | Value (both) |
|---|---|
| `temperature` | **0.0** |
| `top_p` / `top_k` / `seed` | `null` / `null` / `null` |
| `max_output_tokens` | `64000` |
| `max_input_tokens` | `200000` |
| `extended_thinking_budget` | `200000` |
| `reasoning_effort` | `"high"` |
| `caching_prompt` / `prompt_cache_retention` | `true` / `"24h"` |
| `native_tool_calling` | `true` |
| `num_retries` / `retry_min_wait` / `retry_max_wait` / `retry_multiplier` | `5` / `8` / `64` / `8.0` |
| `timeout` | `300` |
| `max_message_chars` | `30000` |
| `drop_params` / `modify_params` | `true` / `true` |
| `enable_encrypted_reasoning` | `true` |
| `stream` / `disable_stop_word` / `log_completions` | `false` / `false` / `false` |

The only two differences are the expected proxy-routing ones:

```
base_url : original "https://llm-proxy.eval.all-hands.dev"   ours null
model    : original "litellm_proxy/claude-sonnet-4-5-…"      ours "anthropic/claude-sonnet-4-5-…"
```

#### On `temperature` specifically

This is worth stating explicitly because it is easy to get wrong. The SDK declares:

```python
temperature: float | None = Field(
    description="... Defaults to None (uses provider default temperature)."
)
```

so an LLM config that omits `temperature` resolves to `None`, and the provider's own default
applies (for Anthropic, **not** 0.0). Verified empirically:

```
LLM(model=…, api_key=…)                     → temperature = None
LLM(model=…, api_key=…, temperature=0.0)    → temperature = 0.0
```

The original explicitly set `0.0`, recorded independently in two places:

```
CDN  metadata/params.json : "llm_config": {"model": "litellm_proxy/…", "temperature": 0.0}
archive metadata.json     : temperature = 0.0
```

We match it by writing `"temperature": 0.0` into the LLM config that `run_gaia.sh` generates.
Note that the LLM config is a **command-line input file**, not source code — setting it is
fully consistent with "zero source modifications". All three of our runs (smoke / pilot /
hard) record `temperature = 0.0` in their `metadata.json`.

> Anyone reproducing this from a bare config containing only `model` and `api_key` will land
> on `temperature = null` and therefore run at the provider default — a genuine divergence
> from the 72.7 % baseline. Check `llm.temperature` in your own `metadata.json` before
> trusting a reproduction.

### Host-side workarounds (never reach the agent)

- `uv sync --no-install-package multi-swe-bench` — that wheel is corrupt (duplicate ZIP
  entries); confirmed unused by the GAIA path. Consequence: `uv run` must use `--no-sync`.
- The harness cannot run on native Windows (`benchmarks/utils/evaluation_utils.py` imports
  POSIX-only `fcntl`). Everything runs under WSL2 Ubuntu 24.04.

## 4. Unavoidable drift — measured, not assumed

Nothing here was changed by us. Each item was A/B tested at **zero API cost** by parsing the
**real GAIA attachment files** under both the original and current versions and comparing
content fingerprints.

**ffmpeg** `7.1.3-0+deb13u1` → `7.1.5-0+deb13u1` (Debian repo moved; a runtime install today
yields the same 7.1.5, so this is independent of pre-installing).

**Packages the agent pip-installs mid-task** — 38 / 165 original instances did this. 11 drifted:

| Package | Original → Today | Parse result |
|---|---|---|
| CoolProp | 7.2.0 → **8.0.0** (major) | `PropsSI` values identical |
| numpy | 2.4.3 → 2.5.1 | identical |
| pandas | 3.0.1 → 3.0.5 | xlsx/csv shape, dtypes, content hash identical |
| pillow | 12.1.1 → 12.3.0 | pixel bytes + base64 payload identical |
| lxml | 6.0.2 → 6.1.1 | docx/pptx text identical |
| biopython | 1.86 → 1.87 | pdb atom coordinates identical |
| idna | 3.11 → 3.18 | 656 real domains + 10 IDN edge cases identical |
| cffi, requests, charset-normalizer, typing-extensions | patch bumps | identical |

Unchanged: `openpyxl 3.1.5`, `PyPDF2 3.0.1`, `python-docx 1.2.0`, `XlsxWriter 3.2.9`,
`et-xmlfile 2.0.0`, `defusedxml 0.7.1`.

Coverage: every parseable attachment in the validation split — 13 xlsx, 10 images, 3 pdf,
3 mp3, 2 zip, and one each of csv / docx / pptx / pdb / jsonld.
**50 fingerprints compared, 0 mismatches, 0 errors.** No pins required.

## 5. Full audit of the original's runtime installs (all 165 instances, 34.6 MB of logs)

| Category | Finding |
|---|---|
| Distinct agent shell commands | 447 |
| Agent-set timeouts | 3 s ×1, 5 s ×2, 10 s ×9, 30 s ×8, 60 s ×33, 120 s ×8, 180 s ×3, 300 s ×1 |
| `pip install` | 28 distinct commands across 38 instances — **0 failures** |
| `apt install` | 3 instances — **3 failures** (missing `sudo`) |
| `curl` / `wget` | 45 distinct commands, 26 instances, 22 unique external URLs |
| `git clone` | 1 instance (2 repos, succeeded) |
| whisper model download | 2 instances, 139 MB each at 180 MiB/s |
| `Command timed out` | **1** (the ffmpeg case) |
| Network errors (DNS / SSL / timeout) | **0** |
| Disk-full | **0** |
| OOM (`Killed` child process) | 2 instances — both still ended via `finish_tool`; one scored correct |

Conclusion: **no instance in the 72.7 % lost points to an install failure.** The original's
infrastructure had effectively perfect networking; our bandwidth is the only real variable,
which is precisely what Layers 2 and 3 neutralise.

## 6. Files we added (not upstream code)

Untracked helper scripts only; the harness itself is untouched.

```
preflight.sh          full pre-run gate (pins, tools, in-container MCP, dataset, resources)
g0_verify.sh          static verification
run_gaia.sh           smoke / pilot / full driver, parameters fixed to params.json
run_gaia_select.sh    same, restricted to an instance-ID list
verify_run.py/.sh     post-run gate over output.jsonl
inspect_traj.py       trajectory health check
inspect_hard.py       attachment-path check + comparison against the original
pin_mcp_layer.sh      builds image Layer 1
build_whisper_layer.sh builds image Layer 3
container_mcp_check.sh in-container MCP verification
prebuild_ws*.py       container build/boot validation
REPRO_README.md       environment spec
hard5.txt             selected Level-3 instance IDs
```

## 7. Verification performed before the full run

| Check | Result |
|---|---|
| Commit pins live at runtime | `<BROWSER_TOOLS>` prompt observed in instance logs |
| Source modifications | **0** in both repos |
| LLM config vs original | **36 / 38 fields identical**; only `base_url` and `model` differ (proxy routing) |
| `temperature` | `0.0` in original (`params.json` + archive `metadata.json`) and in all three of our runs |
| Tool registry | `terminal, file_editor, task_tracker, browser_tool_set` |
| fetch MCP (in container) | JSON-RPC handshake OK, works offline |
| tavily MCP (in container, with key) | `serverInfo {tavily-mcp 0.2.0}`, 4 tools exposed |
| tavily calls during pilots | **90 calls, 0 errors** (`is_error` field) |
| fetch MCP errors | 4, all remote-side (403 / 405 / robots.txt) — correct behaviour |
| whisper offline | transcribes a real GAIA mp3 in 11 s; `pip install` 474 s → 1 s |
| Attachment cache | 38 / 38 required files present |
| Attachment paths | xlsx/csv read via file_editor + terminal; jpg delivered as base64 vision input |
| ffmpeg after Layer 2 | install failures 0, dpkg-lock errors 0 |
| Image auto-detection | `local_image_exists` = True → fast `DockerWorkspace` path |
| Agreement with the original | **8 / 10 instances** (the 2 mismatches point in opposite directions → web variance, not systematic bias) |
| Cost (level-weighted, 11 measured instances) | ≈ **$105** (original: $142.50) |

## 8. Known non-determinism

GAIA is **not deterministic at `temperature=0.0`**. Instance `c61d22de` scored False on one run
and True on another with identical code and settings — the web changes between runs. The
official scorer is also strict on synonyms: `grave` vs `backtick` → wrong;
`Egalitarianism` vs `egalitarian` → wrong (though `Egalitarian` → correct). One original mp3
instance transcribed the audio perfectly and still failed because it wrote `lemon juice`
instead of `freshly squeezed lemon juice`.

Binomial 1σ over 165 instances ≈ 3.5 pp, plus web variance on top.
**Treat 69–76 % as a successful reproduction of 72.7 %.**

## 9. Open item

**Tavily credits.** Measured 8.2 searches/instance locally; the level-weighted projection for
165 instances is ~795–927 against the free tier's 1,000/month. Tavily's usage counter has not
moved (reads 6 while 174 real calls were made across two machines), so the true remaining
balance is unknown. Exhaustion returns HTTP 432 and the agent simply continues without search
— silent degradation, not a crash. $12 of pay-as-you-go removes both the cap and the ambiguity.
