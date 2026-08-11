# Batch 1 Re-run on Image v3 — Results

**Run:** `outputs/gaia-pilot1-rerun-v3/`
**Instance set:** `pilot_ids.txt` — the *same* 17 IDs as Batch 1 (seed 42)
**Image:** `gaia-mcpfix:v3` = MCP pin + ffmpeg (`sha256:32ad214f…`)
**Workers:** 4 · **Completed:** 17/17 · **Failures:** 0

> **`temperature` recorded in `metadata.json`: `None`**
> This run launched *before* the temperature fix, so it ran at the Anthropic provider
> default (~1.0), not the reference's `0.0`. That matters for reading everything below.

---

## Headline

| Run | Image | Score |
|---|---|---|
| Batch 1 original | v2 (MCP pin only) | **14/17 = 82.4%** |
| **Batch 1 re-run** | **v3 (+ ffmpeg)** | **11/17 = 64.7%** |
| Reference (same 17) | — | 13/17 = 76.5% |

**A 3-instance swing on identical instances with an effectively identical configuration.**

| Metric | Re-run (v3) | Original (v2) |
|---|---|---|
| Score | 11/17 = 64.7% | 14/17 = 82.4% |
| Cost | $13.41 | $10.79 |
| Tavily calls / errors | 94 / **0** | 84 / 0 |
| Tool errors | `fetch_fetch: 2` | `fetch_fetch: 2` |
| ffmpeg failures | **0** ✅ | (unmeasured) |

---

## The instances that flipped

```
46719c30-f4c3-4cad-be07-d5cb21eee6bb   v2=True  →  v3=False
7619a514-5fa8-43ef-9143-83b66a43d7a4   v2=True  →  v3=False
d5141ca5-e7a0-469f-bf3e-e773507c86e2   v2=True  →  v3=False
```

**All three moved in the same direction — 3 regressions, 0 gains.**

The other 14 instances were stable across both runs.

---

## Interpretation

### This is not the ffmpeg layer

v2 → v3 differs *only* by a pre-installed ffmpeg. Pre-installing a package the harness would
otherwise install at runtime cannot make an instance fail — it removes a failure mode, it does
not add one. `d5141ca5` in particular was one of Batch 1's *newly-passing* instances, and none of
the three flipped tasks is audio-related.

✅ Confirmed working: **ffmpeg install failures = 0** across all 17 instances, versus the
timeout failures seen in the earlier QEMU run. The layer does what it was added to do.

### This is run-to-run variance, amplified by temperature

Both runs used `temperature = None` → Anthropic's provider default (~1.0), i.e. **full sampling
noise on every token**. The reference used `0.0`.

The magnitude is the story: **±3 instances on 17 ≈ ±17 percentage points** of swing between
identical configurations. That is far larger than the ±12 pt binomial band I previously quoted
for n=17, and it means:

> **Our earlier 82.4% was never a stable measurement.** It was one draw from a wide
> distribution, not an estimate of this configuration's accuracy.

A one-sided 3/3 flip is not by itself alarming — if flips were random-direction, all-same-direction
has ~25% probability. Combined with a plausible mechanism (high-temperature sampling) and no
plausible mechanism on the ffmpeg side, sampling noise is the explanation that fits.

### All three runs together

| Run | Score |
|---|---|
| Batch 1 (v2) | 14/17 |
| Batch 1 re-run (v3) | 11/17 |
| Batch 2 (v2) | 14/17 |
| **Mean** | **13/17 = 76.5%** |
| **Reference (both subsets)** | **13/17 = 76.5%** |

The *mean across runs lands exactly on the reference.* The spread is wide, but it is centred
correctly — consistent with a correct configuration observed through a noisy sampler.

This also revises the earlier "+5.9 pts vs reference" claim: with a third data point, the apparent
edge disappears. We match reference; we do not beat it.

### Cost moved too

$13.41 vs $10.79 (**+24%**) for the same 17 instances. Consistent with higher-temperature runs
wandering more — extra tool calls, longer trajectories. Tavily calls rose in step (94 vs 84).

---

## What this run does and does not establish

**Establishes:**
- ffmpeg layer works in the real harness (0 install failures) ✅
- Harness remains stable — 17/17 completed, no MCP errors, no rate limits, 0 Tavily errors ✅
- **Run-to-run variance at provider-default temperature is ~±3 instances on 17** ✅
- The v2 baseline of 82.4% was a high draw, not a reproducible level ✅

**Does not establish:**
- Anything about fidelity to the 72.7% reference — wrong temperature regime
- Any effect of the ffmpeg layer on score (it should be ~0 on this instance set; the 3 mp3
  tasks that would benefit are not in `pilot_ids.txt`)

---

## Tavily

- **94 calls, 0 errors** — 5.5/instance, up from 4.9
- Counter at save: see `RESULTS_batch1_rerun_v3.json`; it had reached **198** and is now tracking
  the observed totals (confirming it is accurate but lagged 30–60 min, not undercounting)

---

## Recommended next steps

1. **Re-baseline at `temperature = 0.0`.** The config is now fixed; verify the next run records
   `"temperature": 0.0` in its `metadata.json`, not just in the config file.
2. **Add the whisper layer before re-baselining**, so only one baseline has to be paid for.
3. **Expect much tighter variance at 0.0.** Note the parallel team's caveat that GAIA is still
   *not* fully deterministic at 0.0 — the web itself changes between runs — but nothing like the
   ±17 pts seen here.
4. **Treat 69–76% as the success band** for a full 165-instance run.

---

## Files

```
outputs/RESULTS_batch1.json            Batch 1 (v2)          14/17
outputs/RESULTS_batch2.json            Batch 2 (v2)          14/17
outputs/RESULTS_batch1_rerun_v3.json   Batch 1 re-run (v3)   11/17   ← this run
outputs/RESULTS_batch1_rerun_v3.md     this document
outputs/COMPARISON_vs_other_device.md  cross-device config comparison
```
