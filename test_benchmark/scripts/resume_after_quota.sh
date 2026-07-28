#!/usr/bin/env bash
# Wait for Gemini RPD quota to reset, then finish rulebook (27 left) and run v6.3.
# Single-process, sequential. No driver, no concurrency -> no duplicate corruption.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$HERE"
SP="/c/Users/wooja/AppData/Local/Temp/claude/C--GemmyStudioSummer/83c0eccb-4e41-4331-9ab7-755da18bf404/scratchpad"
ST="status/full220.status"
say(){ echo "$(date '+%Y-%m-%dT%H:%M:%S') | $*" | tee -a "$ST"; }
PY="$HERE/.venv/Scripts/python.exe"

quota_ok(){  # 1 test call through the proxy; 0 exit = quota alive
  GEMINI_BASE_URL=http://localhost:4000/v1 GEMINI_API_KEY=sk-proxy-rotation "$PY" - <<'EOF' 2>/dev/null
from openai import OpenAI
import sys
try:
    r=OpenAI(base_url="http://localhost:4000/v1",api_key="x").chat.completions.create(
        model="gemini-3.1-flash-lite",max_tokens=5,messages=[{"role":"user","content":"hi"}])
    sys.exit(0 if (r.choices[0].message.content or "") else 1)
except Exception:
    sys.exit(1)
EOF
}

# kill any stray runners first (prevent a hibernated driver racing us)
say "RESUME-WAIT | START | waiting for quota reset"
pkill -f "harness.run" 2>/dev/null || true; pkill -f "run_full220" 2>/dev/null || true

# poll quota every 5 min
while ! quota_ok; do sleep 300; done
say "QUOTA | RESET detected"

# proxy must be up
curl -s -o /dev/null -w "" http://localhost:4000/health || { say "ERROR | proxy down"; exit 1; }

dedup(){ # $1 results.jsonl -> keep last per task_id
  "$PY" - "$1" <<'EOF'
import json,sys
p=sys.argv[1]; seen={}
for l in open(p,encoding="utf-8"):
    r=json.loads(l); seen[r["task_id"]]=r
open(p,"w",encoding="utf-8").write("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in seen.values()))
print(f"deduped -> {len(seen)}")
EOF
}

# ---- rulebook: finish remaining (single run, resume) ----
RRES="runs/gemini_rulebook_220/gdpval__ace_dual_gdpval/results.jsonl"
dedup "$RRES"
if [ "$(wc -l < "$RRES" 2>/dev/null||echo 0)" -lt 220 ]; then
  say "rulebook | RESUME | $(wc -l < "$RRES")/220"
  GEMINI_API_KEY=sk-proxy-rotation ARM=fbl LIMIT=220 RUN_ID=gemini_rulebook_220 ACE_SHOW_PLAYBOOK=0 \
    bash scripts/run_gdpval_gemini.sh > logs/gemini_rulebook_220_live.log 2>&1
  dedup "$RRES"
  say "rulebook | DONE | $(wc -l < "$RRES")/220"
else say "rulebook | SKIP | already 220"; fi

# ---- v6.3: cold start, full 220 ----
VRES="runs/gemini_v63_220/gdpval__ace_dual_gdpval/results.jsonl"
if [ "$(wc -l < "$VRES" 2>/dev/null||echo 0)" -lt 220 ]; then
  # cold start only if fresh (no partial results yet)
  if [ ! -f "$VRES" ]; then
    [ -f playbooks/ace_dual_gdpval_single.txt ] && mv playbooks/ace_dual_gdpval_single.txt "playbooks/_bak_ace_dual_gdpval_single.txt.$(date +%s)" || true
  fi
  say "v63 | START | $(wc -l < "$VRES" 2>/dev/null||echo 0)/220"
  GEMINI_API_KEY=sk-proxy-rotation ARM=fbl LIMIT=220 RUN_ID=gemini_v63_220 ACE_SHOW_PLAYBOOK=1 \
    bash scripts/run_gdpval_gemini.sh > logs/gemini_v63_220_live.log 2>&1
  dedup "$VRES"
  say "v63 | DONE | $(wc -l < "$VRES")/220"
else say "v63 | SKIP | already 220"; fi

say "RESUME-WAIT | COMPLETE | rulebook + v63 finished"
