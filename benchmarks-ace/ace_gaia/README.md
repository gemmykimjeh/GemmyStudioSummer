# ACE on the OpenHands GAIA harness

Runs GAIA with ACE's feedback loop wrapped around each instance: the agent reads
a playbook, a Reflector diagnoses the attempt, a Curator turns that diagnosis
into new playbook entries, and the next instance reads the updated playbook.

This is ACE's **online mode** — one sequential pass, no train/test split. The
point is to find out whether the playbook helps, not to tune it.

## Running

```bash
cd /Users/yusifgasimov/benchmarks-ace
source .llm_config/tavily.env

./.venv/bin/python -m ace_gaia.run_ace \
    .llm_config/sonnet45.json \
    --level 2023_all --split validation --workspace docker \
    --select pilot_ids.txt --num-workers 1 \
    --output-dir outputs/gaia-ace-pilot \
    2>&1 | tee outputs/gaia-ace-pilot.log
```

Every GAIA flag still works. `--num-workers` must be 1 (see *Sequential* below).
ACE knobs: `--ace-max-num-rounds` (3), `--ace-curator-frequency` (1),
`--ace-token-budget` (80000), `--ace-use-bulletpoint-analyzer` (off),
`--ace-analyzer-threshold` (0.90). Defaults match ACE's own.

Score it with the existing tool — `test_result["score"]` is the pre-train answer:

```bash
./.venv/bin/python -m benchmarks.gaia.get_score --file outputs/gaia-ace-pilot/*/*/*/output.jsonl
```

## Reading the score

**`score` is the pre-train answer** — the agent's first attempt with whatever
playbook existed at that moment, before ground truth was consulted for that
instance. That is the only number that measures anything.

`ace_post_train_correct` is the answer after up to three ground-truth-driven
reflection rounds. It will be much higher and it measures the retry loop, not
the playbook. Do not report it as accuracy.

Per-instance fields added to `test_result`:

| Field | Meaning |
|---|---|
| `ace_step` | position in the run; instance 1 sees an empty playbook |
| `ace_pre_train_answer` / `ace_pre_train_correct` | the scored attempt |
| `ace_post_train_answer` / `ace_post_train_correct` | after reflection rounds |
| `ace_reflection_rounds` | rounds actually run (0 if correct first try) |
| `ace_bullet_ids_used` | bullets the agent reported using |
| `ace_bullets_in_playbook` | playbook size when this instance started |
| `ace_curator_operations` | entries the Curator added afterwards |
| `ace_skipped` | present when the instance errored and ACE was bypassed |

## Artefacts

Under `<output-dir>/.../ace/`:

- `playbooks/playbook_latest.md` and a per-step snapshot — the playbook, in
  ACE's own format (`[err-00001] helpful=2 harmful=0 :: text`).
- `rounds/<instance>_round<n>.json` — each round's answer, cited bullets and
  compacted trace. Kept separately because the harness's event persistence is
  keyed by (run, instance, attempt), so rounds would otherwise overwrite one
  another.
- `bullet_usage.jsonl` — ACE's own usage log.
- `llm_calls/` — full Reflector and Curator prompts and replies.

To see whether a bullet learned early was reused later, grep
`ace_bullet_ids_used` across `output.jsonl`.

## How it maps onto ACE

The OpenHands `Evaluation` loop stays the outer loop. What is ported from ACE is
`_train_single_sample`, in the same order:

1. generate → pre-train answer
2. on failure: up to `max_num_rounds` of reflect → tag bullets → regenerate →
   re-check, breaking early on correct
3. on success: reflect once, for tagging only
4. every `curator_frequency` steps: curate → apply ADD operations
5. optionally: BulletpointAnalyzer dedup

| ACE | Here |
|---|---|
| `Generator.generate` | `GAIAEvaluation.evaluate_instance` (the real agent) |
| `playbook` | `AgentContext.system_message_suffix` (uncached system block) |
| `context` | GAIA's attachment information |
| `reflection` | same suffix, below the playbook, on rounds ≥ 1 |
| `bullet_ids` | `<bullets_used>` tag, parsed with ACE's own regex |
| `reasoning_trace` | `trace.compact_trace` over the event log |
| `extract_answer` | GAIA's `_parse_solution_tag` |
| `answer_is_correct` | GAIA's `question_scorer` |
| `environment_feedback` | ACE's two fixed strings, verbatim |
| Reflector / Curator / prompts / playbook ops | vendored unmodified |

## Deliberate deviations

- **Reflection rounds reuse the instance's workspace.** ACE's Generator is
  stateless; here the harness owns workspace lifecycle and a fresh GAIA
  workspace costs minutes of image and file setup. Each round gets a fresh
  conversation, so the agent's context is clean; only files written by an
  earlier round persist.
- **Errored instances are skipped entirely.** ACE has no error path — an empty
  answer scores as an ordinary wrong one, which would send the Reflector to
  diagnose a Docker failure and let the Curator distil a bullet from it. That
  bullet would then sit in every later task's context. The harness's own error
  handling runs unchanged and ACE is bypassed for those instances.
- **The playbook is injected even when empty.** ACE always passes the playbook
  string, headers and all, so instance 1 sees empty sections. Kept for fidelity.
- **`timed_llm_call` is reimplemented** over litellm; see `vendor/README.md`.

## Sequential

`--num-workers 1` is enforced at construction. The playbook is mutated in place
between instances and the Curator receives `Sample N of M`, so concurrent
instances would train against inconsistent playbooks. `--n-critic-runs` must be
1 as well: ACE already retries through its reflection rounds, and nesting the
harness's critic loop around that multiplies agent runs without adding signal.

## Superseded

`playbook.py`, `roles.py` and `guardrail.py` are an earlier from-scratch
reimplementation of ACE, kept only for reference. Nothing imports them.
