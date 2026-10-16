# TrackMania search agent (CSCI218)

Searches for a faster run on a TrackMania Nations Forever map than a human personal best,
using the game's own physics as the simulator.

- **Telemetry**: a replay is parsed (keyboard or analog gamepad inputs, checkpoint and medal times)
  and re-driven tick-by-tick through the game to record a 10 ms reference trajectory.
- **Beam search**: a time-layered beam search. Each step holds one keyboard action for 50 ms; a
  TMInterface plugin rewinds the game to a stored state, applies the action and reports the new car
  state. Nodes are ranked by how far ahead of the reference ghost they are (projected a short time
  ahead) and pruned when they leave a corridor around the line, fall too far behind, or miss a checkpoint.
- **Local search**: hill climbing over the whole input sequence (shift a key press, tap, lift, coast
  instead of braking, analog steering nudges/shifts). Each candidate is re-simulated from a cached
  state to the finish and kept only if it finishes earlier. Optional time warp re-aligns the rest of
  the run after an early change. Several game instances can run workers in parallel and share their
  best run through a file.
- **A\* / weighted A\***: best-first search with f = g + w·h over the same actions and pruning
  (g = race time). Two heuristics: remaining track length / 1000 km/h (admissible) and the time the
  PB still needed from that point (better informed, not admissible). The open list is capped by memory
  (each open node is a saved game state, ~9 KB, in a 32-bit game).
- **Output**: the best input sequence (`results/*.json`), verified by re-running it from the race start.
  A TMInterface script (`results/*.txt`) is also written, but TMInterface script playback does not
  reproduce the searched runs; use `tmsearch.play` (or `record`) to watch a run through the plugin.

## Requirements

- Windows, TrackMania Nations Forever (Steam) patched to 2.11.26
- TrackMania ModLoader with the TMInterface 2.2.1 mod enabled
- Python 3.12+ (tested on 3.14)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --no-deps pygbx==0.3
copy plugin\SearchBridge.as "$env:USERPROFILE\Documents\TMInterface\Plugins\"
```

Put the map in `Documents\TrackMania\Tracks\Challenges\My Challenges\` **before** starting the game
(TMInterface builds its map list at startup). For official maps, the map can be extracted from a
replay of it (replays embed the map file).

Start the game through ModLoader, then load the map (`map "My Challenges/B01-Race.Challenge.Gbx"`
in the TMInterface console). For parallel workers, start more instances with their own plugin port:

```powershell
TMLoader.exe run TmForever "default" /configstring="set sb_port 8478; set autologin 1; set skip_map_load_screens true; set unfocused_fps_limit false"
```

Never minimise a game window while a search runs (the game pauses its loop when minimised).

## Run

```powershell
# 1. Re-drive the PB replay and record the reference trajectory
.venv\Scripts\python -m tmsearch.reproduce data\replays\MIS_B01-Race.Replay.gbx

# 2. Beam search
.venv\Scripts\python -m tmsearch.search --beam 40

# 3. Local search from the beam result (one worker per game instance; share the best run)
.venv\Scripts\python -m tmsearch.local --start results\b40_s5_full_27660.json --budget 1800 --port 8477 --shared results\shared_best.json --from-tick 2000 --to-tick 2740 --warp --seed 33 --tag end

# 4. Learning curve + tweak list across all stages
.venv\Scripts\python -m tmsearch.progress

# 5. A* / weighted A* (same actions and pruning as the beam search)
.venv\Scripts\python -m tmsearch.astar --weight 2 --heuristic pb --budget 180000
```

## Experiments (one game instance)

`scripts/overnight.sh` runs everything in sequence on the game listening on port 8477 (it never starts or
stops the game; finished jobs are skipped on a restart): beam widths 1/10/10/20, A* and weighted A* with
both heuristics on a budget of 180,000 game steps, and local search from the PB with 5 seeds × 1,500
evaluations. Then:

```powershell
.venv\Scripts\python -m tmsearch.compare   # results/comparison.*, results/repeats.*, results/experiments_summary.txt
```

## Tests

```powershell
.venv\Scripts\python -m unittest discover -s tests -v          # 17 offline tests (no game needed)
$env:TM_PORT = 8477; .venv\Scripts\python -m unittest tests.test_game -v   # replays saved runs in a running game
```

## Report

`report/make_report.py` builds the group report draft (`report/CSCI218_B01_report_DRAFT.docx`) from the files in
`results/` and `data/`, following the structure in the project guidelines.

## Results (B01-Race)

| Run | Time |
|---|---|
| World record, oli10 (TMNF-X, 23 Sep 2024, human) | 25.23 s |
| Author medal | 26.20 s |
| **AI from my PB: beam + local + parallel local search (86 min)** | **27.28 s** |
| Local search (30 min) | 27.42 s |
| Beam search, beam 40, 50 ms steps (20 min) | 27.66 s |
| Gold medal | 27.80 s |
| My PB (baseline) | 27.82 s |

All times are verified by re-running the inputs from the race start in the game.

- **Beam search** follows the PB line for the first ~1,900 m; its gain (0.16 s) comes from the final
  drop: a short brake and coast before landing avoids the big speed loss the PB suffers after it.
  It is myopic: leads of 45 ms (5 s) and 96 ms (22 s) disappeared within a second.
- **Local search** (4,723 candidates, 125 kept) brings it to 27.42; **parallel local search** on four
  game instances (≈29,400 candidates, 129 kept) to 27.28. Workers on the first 20 s kept nothing;
  almost all gains are throttle timing in the last six seconds (`results/tweaks.md`).
- **World-record experiment**: oli10's 25.23 replay (analog gamepad) re-drives to exactly 25.230 in our
  pipeline (replays store steering with the opposite sign to TMInterface). Four local-search workers
  then refined it to **25.21** (20 ms under the WR) after 24,846 candidates in 35 min (`results/wr_stats.json`).

- **Algorithms compared** (one game instance, `results/comparison.md`): with a budget of 180,000 game steps,
  no A* or weighted A* variant got past 12% of the track (admissible distance heuristic: near breadth-first in
  time; weighted: dives into slow branches that pruning cuts; PB-time heuristic: plateau at f ≈ 27.82). Every
  beam search finished, but only width 40 beat the PB (widths 1/10/20: 27.82). Beam width 10 run twice gave
  identical inputs and step counts (deterministic).
- **Repeated runs** (`results/repeats.md`): local search from the PB, 5 seeds × 1,500 evaluations (≈13 min each):
  **27.28 ± 0.14 s** (mean ± std), best **27.10**, worst 27.43; all five beat gold.

Plots: `results/learning_curve.png`, `results/local_seed3*_w*_27280.png`, `results/comparison.png`, `results/repeats.png`.

## Video

Set the game to 1920x1080 in its Display Settings first. With several game instances, focus each window once (`Bridge.focus()`) before recording, or the car never leaves the start.

```powershell
# record each camera (1 = close chase, 2 = chase, 3 = bumper); the run is stepped through the plugin
.venv\Scripts\python -m tmsearch.record results\<run>.json --cams 2 1 3 --label ai --speed 0.35
.venv\Scripts\python -m tmsearch.record data\replays\MIS_B01-Race.Replay.gbx --cams 2 --label pb
.venv\Scripts\python -m tmsearch.record data\replays\downloaded\wr_oli10_25230.Replay.gbx --cams 2 --label wrh
.venv\Scripts\python -m tmsearch.record results\local_seed41_wr1-start_25210.json --cams 2 --label wrai
# cut the demo
.venv\Scripts\python -m tmsearch.video --ai-trajectory results\<run>.trajectory.csv --chart results\<run>.png --wr-result results\local_seed41_wr1-start_25210.json --wr-stats results\wr_stats.json --out video\B01_main.mp4
```

Outputs in `video/`: `B01_AI_vs_PB_and_WR.mp4` (final, 1080p60, Claude Design motion intro + demo, 2 min 16 s), `B01_main.mp4` (demo only), `B01_deck_720p.mp4` (13 MB copy embedded in the deck), `motion_intro.mp4` (the intro alone), `raw/` (per-camera clips).

## Layout

| Path | What it is |
|---|---|
| `plugin/SearchBridge.as` | TMInterface plugin: save states, rewind, step physics, report car state, stream frames over TCP (port `sb_port`, default 8477) |
| `tmsearch/bridge.py` | Python client for the plugin |
| `tmsearch/replay.py` | Replay parsing (keyboard and analog inputs, ghost samples, checkpoint and medal times) |
| `tmsearch/reproduce.py` | Re-drives a replay and writes a reference trajectory |
| `tmsearch/reference.py` | Reference trajectory: projection, progress, ghost time |
| `tmsearch/search.py` | Beam search and verification |
| `tmsearch/local.py` | Local search (hill climbing), time warp, parallel workers with a shared best |
| `tmsearch/astar.py` | A* and weighted A* (distance or PB heuristic), memory-capped open list |
| `tmsearch/compare.py` | Algorithm comparison and repeated-run statistics (mean ± std), charts and summary |
| `scripts/overnight.sh` | The overnight experiment batch (one game instance) |
| `tests/` | Offline unit tests and game tests (determinism, saved runs) |
| `report/make_report.py` | Builds the report draft from the results |
| `tmsearch/progress.py` | Learning curve and tweak list across all stages |
| `tmsearch/analyze.py` | Re-runs a result, plots it against the reference, writes its trajectory CSV |
| `tmsearch/play.py` | Plays a result at normal speed with rendering on |
| `tmsearch/record.py` | Records a run per camera as 100 fps video (one frame per physics tick) |
| `tmsearch/video.py` | Cuts the 1080p demo video |

## Fair play

Every run produced here is tool-assisted and stays offline. The plugin blocks the finish while it is in
control (and parks the car at the start when the search disconnects), so a searched run is never
registered by the game as a record. Never upload these runs or play online with TMInterface loaded.
The world-record replay belongs to oli10 (TMNF-X); it is used here only as a starting point for research.

## Videos and large files

Files over GitHub's size limit are attached to the [`media-v1` release](../../releases/tag/media-v1) instead of the repository:
the final video (`B01_AI_vs_PB_and_WR.mp4`), the demo cut (`B01_main.mp4`), the deck copy (`B01_deck_720p.mp4`),
the motion intro (`motion_intro.mp4`), the raw per-camera clips from `video/raw/` (`ai_cam1/2/3` = the AI's 27.28 run
from the close chase, chase and bumper cameras; `pb_cam2` = my PB; `wrh_cam2` = oli10's 25.23 WR; `wrai_cam2` = the AI's
25.21), and the Claude Design deck exports (`B01_deck_pixel_perfect.pptx`, `B01_deck_editable.pptx`,
`B01_deck_project_source.zip`). The raw clips' frame metadata (`video/raw/*.json`) is in the repository.
