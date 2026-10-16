#!/usr/bin/env bash
# Overnight experiments on ONE game instance (plugin port 8477), one job after another.
#   1. search algorithms compared (beam widths, A*, weighted A*, two heuristics) - all deterministic
#   2. repeated runs: local search from the PB, 5 seeds, 1500 evaluations each -> mean +- std
# A job whose "done" marker exists is skipped, so the script can be restarted after a crash.
# It never starts or stops the game.
cd "$(dirname "$0")/.." || exit 1
PY=./.venv/Scripts/python
mkdir -p logs/night results/compare results/repeats
STATUS=logs/night/status.txt

run() {   # name, then the command
  local name=$1; shift
  if [ -f "logs/night/$name.done" ]; then echo "$(date +%T) skip $name (done)" >> "$STATUS"; return; fi
  echo "$(date +%T) start $name: $*" >> "$STATUS"
  "$@" > "logs/night/$name.log" 2>&1
  local rc=$?
  echo "$(date +%T) end $name rc=$rc: $(tail -n 2 "logs/night/$name.log" | head -n 1)" >> "$STATUS"
  [ $rc -eq 0 ] && touch "logs/night/$name.done"
}

B=180000   # expansion budget for A*: what beam search (width 40) needed to finish (177,163)

run beam1    $PY -u -m tmsearch.search --beam 1  --out results/compare --tag cmp
run beam10a  $PY -u -m tmsearch.search --beam 10 --out results/compare --tag cmpA
run beam10b  $PY -u -m tmsearch.search --beam 10 --out results/compare --tag cmpB
run beam20   $PY -u -m tmsearch.search --beam 20 --out results/compare --tag cmp
run wastar2_pb   $PY -u -m tmsearch.astar --heuristic pb --weight 2   --budget $B
run wastar1.5_pb $PY -u -m tmsearch.astar --heuristic pb --weight 1.5 --budget $B
for seed in 1 2 3 4 5; do
  run local_seed$seed $PY -u -m tmsearch.local --seed $seed --warp --max-evals 1500 --budget 2400 \
      --out results/repeats --tag rep
done
run astar_pb     $PY -u -m tmsearch.astar --heuristic pb --weight 1   --budget $B
run astar        $PY -u -m tmsearch.astar --weight 1  --budget $B
run wastar5      $PY -u -m tmsearch.astar --weight 5  --budget $B
run wastar20     $PY -u -m tmsearch.astar --weight 20 --budget $B
echo "$(date +%T) ALL DONE" >> "$STATUS"
