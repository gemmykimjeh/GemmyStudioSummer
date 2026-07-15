# feedback_loop_gdpval — Rubric-Grounded Reflective loop (RGR) v1

A GDPval-specific, ablatable feedback loop that runs on **Gemini Flash free-tier**
keys with **multi-key rotation** (survives per-minute rate limits; resumes across
day boundaries). Self-contained: it plugs into the existing `test_benchmark`
harness without modifying `ReAct/` or `ReAct_feedback_loop/`.

- **Design rationale:** [`DESIGN.md`](./DESIGN.md) — why GDPval needs a different
  loop than AppWorld/τ-bench, the memory structure, the ablation arms, risks.
- **Agent:** `agents/rgr_gdpval.py` (registered as `rgr_gdpval`)
- **Deterministic core (no LLM calls):** `agents/_rgr.py`
- **Multi-key Gemini rotation:** `agents/_keyring.py`

## The four ablation arms

| MODE | What runs | Isolates |
|---|---|---|
| `a0` | one-shot, no memory | floor |
| `a1` | + non-LLM guardrails (failure-mode counters → pre-written bullets) | the **zero-LLM-call** learning path |
| `a2` | + gated LLM Reflector over failed rubric criteria + curate | value of LLM reflection |
| `a3` | + deliverable-type routing (full RGR) | value of genre routing |

Per task: Generator (1 call) + Grader (1 call) + Reflector (≤1, only when
`score < 0.7` or a required criterion failed) → **2–3 Gemini calls/task**.

## Setup (VS Code terminal)

From `test_benchmark/` (its `.venv` and deps are the ones used):

```bash
cd test_benchmark
# deps already there for gdpval; RGR adds only openai + anthropic (usually present)
pip install -e ".[gdpval]"      # if not already installed
```

Get **3–4 free keys** from https://aistudio.google.com/apikey and export them
(comma-separated). Copy `configs/.env.example` to `test_benchmark/.env` and edit,
or just export in the shell:

```bash
export GEMINI_API_KEYS="key1,key2,key3,key4"
```

Start the **grader proxy** once (separate terminal; it needs only one key):

```bash
cd test_benchmark
PYTHONUTF8=1 GEMINI_API_KEY="key1" \
  litellm --config ../feedback_loop_gdpval/configs/litellm_gemini.yaml --port 4000
```

## Run

One arm:

```bash
# from repo root
MODE=a0 LIMIT=20 bash feedback_loop_gdpval/scripts/run_rgr_gdpval.sh
MODE=a3 LIMIT=20 bash feedback_loop_gdpval/scripts/run_rgr_gdpval.sh
```

All four arms back-to-back (resumable):

```bash
LIMIT=20 bash feedback_loop_gdpval/scripts/run_all_arms.sh
```

Compare arms (from `test_benchmark/`):

```bash
python -m harness.compare runs/rgr_a0 runs/rgr_a3
```

## How multi-key rotation handles free-tier limits

`_keyring.py` wraps the OpenAI-compatible Gemini endpoint used by ACE's
`utils.py`. On a `429 / RESOURCE_EXHAUSTED`, the current key is parked for
`GEMINI_KEY_COOLDOWN` seconds (default 65) and the next live key is used. When
**all** keys are cooling down it sleeps until the soonest one frees up. When
every key hits the **daily** cap, the harness's JSONL `--resume` lets you re-run
the same command the next day — the playbook (`rgr_playbook_gdpval_<mode>.json`)
and failure counters (`rgr_counters_gdpval_<mode>.json`) persist, so learning
continues where it stopped.

Keep `--concurrency 1` (set by the scripts): the playbook is shared, cross-task
state, and free-tier RPM is low anyway.

## Files written per arm (in `test_benchmark/`)

- `runs/rgr_<mode>/…` — harness results (JSONL, resumable)
- `rgr_playbook_gdpval_<mode>.json` — learned + guardrail bullets
- `rgr_counters_gdpval_<mode>.json` — failure-mode counters + activation state

## Notes

- The grader is Gemini too (via the proxy), so absolute scores carry self-grading
  bias — fine for A/B **deltas**, not absolute claims. See DESIGN.md §6.
- Watch the `fm_other=` field in the per-task log line: if unmatched failed
  criteria exceed ~30%, the failure-mode keyword rules in `_rgr.py` need a pass.
- GDPval released a v2 (rubrics + deliverables) on HuggingFace; make sure the
  `gdpval` adapter pulls `rubric_json` from the current dataset revision.
