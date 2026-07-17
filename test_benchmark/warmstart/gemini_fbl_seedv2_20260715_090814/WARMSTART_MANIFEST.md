# Warm-start manifest — fbl dual-playbook GDPval run

Snapshot taken: 2026-07-15 ~09:08 KST. Purpose: resume the 100-task fbl run
after the Gemini free-tier daily quota resets, without losing learned state.

## What happened
- Run: `RUN_ID=gemini_fbl_seedv2`, agent `ace_dual_gdpval`, ACE_PATH=ReAct_feedback_loop,
  model `gemini-3.1-flash-lite` (provider gemini; grader via LiteLLM proxy :4000).
- Progress at snapshot: **~40 tasks fully processed** (last full task #44).
  Learned playbook grew from seeds (10/11 bullets) to **concrete=49 bullets,
  abstract=47 bullets**.
- STALLED at ~2026-07-14 20:13 KST: Gemini free tier limit
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` = **500 requests/day/model**
  exhausted. Agent + grader both draw from this pool.
- Quota resets at **midnight Pacific ≈ 16:00 KST 2026-07-15**. (NOT KST midnight.)
- The launched process was left ALIVE (user: do not kill); it keeps retrying
  uselessly (429s don't consume quota) until reset or its 1000-retry cap.
- NOTE: at ~500 counted requests per ~40 tasks, the free tier yields ~40-60
  tasks/day → 100 tasks needs ~2 daily-reset cycles.

## Snapshot contents (this folder)
- `ace_dual_gdpval_concrete.txt` — learned CONCRETE playbook (49 bullets) at task 44
- `ace_dual_gdpval_abstract.txt` — learned ABSTRACT playbook (47 bullets) at task 44
- `ace_dual_gdpval_selector.json` — Thompson selector posteriors
- `run_dir/` — harness run dir (`runs/gemini_fbl_seedv2/`, graded-task records
  for `--resume`) as of the clean 40-task state
- `dual_fbl_100.snapshot.log` — full run log up to snapshot

## How warm-start works (already wired)
- `ace_dual_gdpval._ensure()` reloads `ace_dual_gdpval_{concrete,abstract}.txt` +
  `_selector.json` from cwd on restart (instead of seeds) → learning survives.
- harness `--resume` skips tasks already recorded in `runs/gemini_fbl_seedv2/`.
- `self._step` restarts at 0 (cosmetic: only affects call_ids/total_samples,
  not correctness — playbook state comes from the reloaded files).

## RESUME PROCEDURE (after 16:00 KST 2026-07-15, once quota probes OK)
From `C:\GemmyStudioSummer\test_benchmark`:

1. (Optional) Verify quota is back:
   curl -s -o /dev/null -w "%{http_code}" -X POST \
     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions" \
     -H "Authorization: Bearer $GEMINI_API_KEY" -H "Content-Type: application/json" \
     -d '{"model":"gemini-3.1-flash-lite","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
   # 200 = ready, 429 = still exhausted

2. If the LIVE run kept running and polluted `runs/gemini_fbl_seedv2/` or the cwd
   playbook files with quota-failures, RESTORE this clean snapshot first:
   cp WARMSTART/ace_dual_gdpval_{concrete,abstract}.txt WARMSTART/ace_dual_gdpval_selector.json  <test_benchmark>/
   rm -rf runs/gemini_fbl_seedv2 && cp -rp WARMSTART/run_dir runs/gemini_fbl_seedv2
   # (only if live state is corrupted; otherwise skip — live state is newer/better)

3. Do NOT clear cwd `ace_dual_gdpval_*` files (that would restart from seeds).
   Then resume (same run-id → --resume skips done tasks, agent reloads playbook):
   export GEMINI_API_KEY=$(grep '^GEMINI_API_KEY=' .env | cut -d= -f2-)
   ARM=fbl LIMIT=100 RUN_ID=gemini_fbl_seedv2 bash scripts/run_gdpval_gemini.sh
   # look for "[ace_dual] RESUMED from persisted state" on startup.
