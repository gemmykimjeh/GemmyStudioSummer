#!/usr/bin/env bash
# Repeat-measurement driver: rulebook-only x2 and FBL v6.1 x2, run SEQUENTIALLY.
#
# Why: per-task codegen variance dominates this benchmark — 18 of 100 tasks swing
# by >0.3 between identical configurations, which is larger than the ~0.03 effects
# being compared. One run per arm cannot separate signal from lottery. Averaging
# repeats per task does.
#
# Sequential, not parallel: the free-tier keys have per-minute limits, and two
# concurrent runs would contend for them and distort both.
#
#   bash scripts/run_repeats.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
mkdir -p logs playbooks

# arm|run_id|show_playbook
RUNS=(
  "rulebook|gemini_rulebook_r2|0"
  "v61|gemini_fbl_v61_r2|1"
  "rulebook|gemini_rulebook_r3|0"
  "v61|gemini_fbl_v61_r3|1"
)

echo ">>> repeat driver started $(date '+%F %T')  (${#RUNS[@]} runs, sequential)"
for spec in "${RUNS[@]}"; do
  IFS='|' read -r arm rid show <<< "$spec"
  res="runs/$rid/gdpval__ace_dual_gdpval/results.jsonl"

  if [ -f "$res" ] && [ "$(wc -l < "$res")" -ge 100 ]; then
    echo ">>> SKIP $rid (already complete)"; continue
  fi

  # Cold start: every run must begin from the packaged seeds, never inherit the
  # previous run's learned playbook (that would silently warm-start it).
  if [ -f playbooks/ace_dual_gdpval_single.txt ]; then
    mv playbooks/ace_dual_gdpval_single.txt "playbooks/_bak_ace_dual_gdpval_single.txt.$(date +%s)"
  fi

  echo ">>> [$(date '+%T')] START $rid  (arm=$arm show_playbook=$show)"
  ACE_SHOW_PLAYBOOK="$show" GEMINI_API_KEY=sk-proxy-rotation \
    ARM=fbl LIMIT=100 RUN_ID="$rid" \
    bash scripts/run_gdpval_gemini.sh > /dev/null 2>&1

  n=$(wc -l < "$res" 2>/dev/null || echo 0)
  echo ">>> [$(date '+%T')] DONE  $rid -> $n/100"
  # Snapshot this run's learned playbook alongside its results for later inspection.
  [ -f playbooks/ace_dual_gdpval_single.txt ] && \
    cp playbooks/ace_dual_gdpval_single.txt "playbooks/final_${rid}.txt"
done
echo ">>> ALL REPEATS COMPLETE $(date '+%F %T')"
