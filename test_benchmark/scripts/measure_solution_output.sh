#!/usr/bin/env bash
# Measure how much output the OFFICIAL Terminal-Bench 2.0 solution produces,
# per command — the unit our `bash` tool's output cap actually governs.
#
# Why: the adapter clips each tool result at _MAX_OUTPUT chars, a number we
# invented. TB2 defines no such cap (harbor captures a tmux pane), so there is
# no spec value to copy. The answer key is the next best evidence: whatever a
# correct solution legitimately prints, the agent must be able to see.
#
# Method: run solution/solve.sh inside the task's own image under `set -x` with
# a unique PS4 marker, merge stdout+stderr, then split on the marker so each
# segment is one command's output. Reports per-task max and total.
#
#   bash scripts/measure_solution_output.sh fix-git build-pmars ...
#   bash scripts/measure_solution_output.sh $(cat /tmp/trunc_tasks.txt)
#
# Env: TIMEOUT (default 600s per task).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASKS_DIR="$HERE/external/terminal-bench-2"
TIMEOUT="${TIMEOUT:-600}"
MARK='@@CMD@@'

# Elapsed time is measured too: it grounds the per-command timeout the same way
# output size grounds the output cap. If the official solution needs longer than
# our cap to run, the cap is the reason the task is unsolvable, not the agent.
printf '%-34s %10s %10s %7s %s\n' task max_cmd total secs note
for t in "$@"; do
  d="$TASKS_DIR/$t"
  sol="$d/solution/solve.sh"
  [ -f "$sol" ] || { printf '%-34s %10s %10s %8s %s\n' "$t" - - - "no solve.sh"; continue; }
  img=$(grep -oP 'docker_image = "\K[^"]+' "$d/task.toml" 2>/dev/null)
  [ -n "$img" ] || { printf '%-34s %10s %10s %8s %s\n' "$t" - - - "no image"; continue; }

  c="measure-$(echo "$t" | tr -cd 'a-z0-9-')-$$"
  if ! docker run -d --name "$c" --entrypoint sleep "$img" infinity >/dev/null 2>&1; then
    printf '%-34s %10s %10s %8s %s\n' "$t" - - - "container failed"; continue
  fi

  docker cp "$sol" "$c:/solve.sh" >/dev/null 2>&1
  # Normalise CRLF from the Windows checkout, then trace every command with a
  # marker so the merged output can be split back into per-command segments.
  docker exec "$c" sh -c "sed -i 's/\r\$//' /solve.sh 2>/dev/null || true" >/dev/null 2>&1
  t0=$(date +%s)
  out=$(timeout "$TIMEOUT" docker exec "$c" bash -c \
        "export PS4='$MARK '; set -x; source /solve.sh" 2>&1)
  rc=$?
  secs=$(( $(date +%s) - t0 ))

  # Split on the marker; the largest segment is the biggest single-command output.
  read -r maxb totalb ncmds <<<"$(printf '%s' "$out" | awk -v m="$MARK" '
    BEGIN{max=0; tot=0; n=0; cur=0}
    index($0,m)==1 {if(cur>max)max=cur; tot+=cur; cur=0; n++; next}
    {cur+=length($0)+1}
    END{if(cur>max)max=cur; tot+=cur; print max, tot, n}')"

  note=""; [ $rc -eq 124 ] && note="TIMED OUT (${TIMEOUT}s)"
  [ $rc -ne 0 ] && [ $rc -ne 124 ] && note="solve.sh exit $rc"
  printf '%-34s %10s %10s %7s %s\n' "$t" "$maxb" "$totalb" "$secs" "$note"

  docker rm -f "$c" >/dev/null 2>&1
done
