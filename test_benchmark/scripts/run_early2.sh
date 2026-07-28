#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$HERE"
for rid in gemini_fbl_v63_e2 gemini_fbl_v63_e3; do
  res="runs/$rid/gdpval__ace_dual_gdpval/results.jsonl"
  [ -f "$res" ] && [ "$(wc -l < "$res")" -ge 33 ] && { echo ">>> SKIP $rid (done)"; continue; }
  [ -f playbooks/ace_dual_gdpval_single.txt ] && mv playbooks/ace_dual_gdpval_single.txt "playbooks/_bak_ace_dual_gdpval_single.txt.$(date +%s)"
  echo ">>> [$(date '+%T')] START $rid (early 33, cold)"
  GEMINI_API_KEY=sk-proxy-rotation ARM=fbl LIMIT=33 RUN_ID="$rid" ACE_SHOW_PLAYBOOK=1 \
    bash scripts/run_gdpval_gemini.sh > /dev/null 2>&1
  echo ">>> [$(date '+%T')] DONE $rid -> $(wc -l < "$res" 2>/dev/null || echo 0)/33"
done
echo ">>> EARLY REPEATS COMPLETE $(date '+%T')"
