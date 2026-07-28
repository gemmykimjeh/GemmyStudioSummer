#!/usr/bin/env bash
# Full GDPval (220 tasks) across 4 arms, SEQUENTIALLY, in order:
#   1) baseline ACE   (ace_gdpval, ReAct repo)          cold
#   2) pure LLM       (+files +tools, no scaffold)
#   3) rulebook-only  (ace_dual_gdpval, playbook HIDDEN) cold
#   4) v6.3           (ace_dual_gdpval, playbook shown)  cold
#
# Writes a machine-readable STATUS file the monitor watches:
#   status/full220.status  (one line per event: ISO_TIME | ARM | STATE | detail)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$HERE"
SP="/c/Users/wooja/AppData/Local/Temp/claude/C--GemmyStudioSummer/83c0eccb-4e41-4331-9ab7-755da18bf404/scratchpad"
mkdir -p status logs
ST="status/full220.status"
say(){ echo "$(date '+%Y-%m-%dT%H:%M:%S') | $*" >> "$ST"; }

clear_pb(){ # $1 = playbook filename to back up
  [ -f "playbooks/$1" ] && mv "playbooks/$1" "playbooks/_bak_$1.$(date +%s)" || true
}

say "DRIVER | START | 4 arms x 220 tasks"

# ---- 1. baseline ACE ----
BRES="runs/gemini_baseline_220/gdpval__ace_gdpval/results.jsonl"
if [ ! -f "$BRES" ] || [ "$(wc -l < "$BRES" 2>/dev/null || echo 0)" -lt 220 ]; then
  clear_pb ace_playbook_gdpval.txt
  say "baseline | START"
  GEMINI_API_KEY=sk-proxy-rotation ARM=baseline LIMIT=220 RUN_ID=gemini_baseline_220 \
    bash scripts/run_gdpval_gemini.sh > /dev/null 2>&1
  say "baseline | DONE | $(wc -l < "$BRES" 2>/dev/null || echo 0)/220"
else say "baseline | SKIP | already complete"; fi

# ---- 2. pure LLM (+files +tools) ----
PRES="$SP/pure220_tools.jsonl"
if [ ! -f "$PRES" ] || [ "$(wc -l < "$PRES" 2>/dev/null || echo 0)" -lt 220 ]; then
  say "pure | START"
  N=220 FILES=1 TOOLS=1 OUT="$PRES" \
    GEMINI_BASE_URL=http://localhost:4000/v1 GEMINI_API_KEY=sk-proxy-rotation \
    ANTHROPIC_BASE_URL=http://localhost:4000 \
    HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 PYTHONUTF8=1 \
    "$HERE/.venv/Scripts/python.exe" "$SP/pure_llm_baseline.py" > logs/pure220_live.log 2>&1
  say "pure | DONE | $(wc -l < "$PRES" 2>/dev/null || echo 0)/220"
else say "pure | SKIP | already complete"; fi

# ---- 3. rulebook-only ----
RRES="runs/gemini_rulebook_220/gdpval__ace_dual_gdpval/results.jsonl"
if [ ! -f "$RRES" ] || [ "$(wc -l < "$RRES" 2>/dev/null || echo 0)" -lt 220 ]; then
  clear_pb ace_dual_gdpval_single.txt
  say "rulebook | START"
  GEMINI_API_KEY=sk-proxy-rotation ARM=fbl LIMIT=220 RUN_ID=gemini_rulebook_220 ACE_SHOW_PLAYBOOK=0 \
    bash scripts/run_gdpval_gemini.sh > /dev/null 2>&1
  say "rulebook | DONE | $(wc -l < "$RRES" 2>/dev/null || echo 0)/220"
else say "rulebook | SKIP | already complete"; fi

# ---- 4. v6.3 ----
VRES="runs/gemini_v63_220/gdpval__ace_dual_gdpval/results.jsonl"
if [ ! -f "$VRES" ] || [ "$(wc -l < "$VRES" 2>/dev/null || echo 0)" -lt 220 ]; then
  clear_pb ace_dual_gdpval_single.txt
  say "v63 | START"
  GEMINI_API_KEY=sk-proxy-rotation ARM=fbl LIMIT=220 RUN_ID=gemini_v63_220 ACE_SHOW_PLAYBOOK=1 \
    bash scripts/run_gdpval_gemini.sh > /dev/null 2>&1
  say "v63 | DONE | $(wc -l < "$VRES" 2>/dev/null || echo 0)/220"
else say "v63 | SKIP | already complete"; fi

say "DRIVER | COMPLETE | all 4 arms finished"
