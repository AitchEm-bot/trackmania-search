# AI.md: a guide to this repo for AI agents

This file is written for an AI assistant (Claude, ChatGPT, Copilot, Cursor, etc.) that has been
asked to explain, review or extend this repository. Read this first, then `README.md`.
If you are a human, paste this file into your AI agent along with the question you have.

## 1. What this project is, in one paragraph

A CSCI218 (AI) group project. The goal: beat a human personal best (PB, 27.82 s) on the
TrackMania Nations Forever map **B01-Race** using classical AI search, not machine learning.
The search uses **the game itself as the simulator**. A TMInterface plugin
(`plugin/SearchBridge.as`) runs inside the game, saves and restores physics states, and steps
the physics forward on command. Python code (`tmsearch/`) talks to it over TCP and runs beam
search, local search (hill climbing) and A* / weighted A* over those states. The best run found is
**27.28 s** (beats the gold medal, 27.80). Starting from the human world record replay (oli10,
25.23 s), local search reached **25.21 s**. All runs are tool-assisted and are kept offline.

## 2. Mental model (how the pieces fit)

```
 TrackMania Nations Forever (TMInterface 2.2.1 loaded via ModLoader)
   └── plugin/SearchBridge.as   TCP server on port sb_port (default 8477)
          save state ─ rewind ─ apply inputs for N ticks ─ report car state ─ capture frames
                          ▲ binary protocol (little-endian int32 + struct)
                          │
 tmsearch/bridge.py   Bridge client: expand(parent_sid, action, ticks) -> Node
                          │
     ┌────────────────────┼─────────────────────────┬───────────────────────┐
 replay.py +          search.py               local.py                astar.py
 reproduce.py         beam search             hill climbing over      A* / weighted A*
 (PB replay ->        (time-layered,          the full input          (subclasses
  reference.json)      8 key actions,          sequence, parallel      BeamSearch, reuses
       │               50 ms steps)            workers share best      its actions/pruning)
       ▼                    │                        │                       │
 reference.py  <── all searches measure progress against the PB line ────────┘
                            │
                     results/*.json  (inputs + verified finish time)
                            │
   analyze.py / progress.py / compare.py / wrstats.py  -> charts, tables, CSVs
   play.py / record.py / video.py                      -> watching and video
   report/make_report.py                               -> report .docx
```

Key ideas to explain to a reader:

- **Physics tick = 10 ms.** Everything is measured in ticks or milliseconds of race time.
- **State = a save-state id (`sid`) held inside the plugin.** Python never holds physics state. It
  holds ids and must `free()` them, because the game is 32-bit and memory is limited (~9 KB per state).
- **The reference (PB) trajectory** (`data/reference.json`, produced by `reproduce.py`) gives every
  search a sense of "progress along the track", "ahead or behind the ghost", and "off the line".
  `Reference.project()` maps a car position to arc length, lateral offset and ghost time.
- **Determinism:** the same inputs always give the same finish time. So a result is just a list of
  inputs, and `search.verify()` re-runs it from the race start to confirm the time.
- **Finish guard:** the plugin blocks the finish from being registered as a record while Python is
  in control. This is why runs can't leak to leaderboards. Don't remove it.

## 3. File-by-file map

### Core code: `tmsearch/` (Python package, run modules with `python -m tmsearch.<name>`)

| File | Role | Main things inside |
|---|---|---|
| `bridge.py` | TCP client for the plugin | command/response constants (`C_*`, `S_*`, must match the plugin), `Action` (keys or analog steer, with `label()`/`from_label()`), `Node` (car state), `Bridge.expand/free/wait_start/record/screenshot` |
| `replay.py` | Reads `.Replay.gbx` files | `read_replay()` → inputs, ghost samples, checkpoint times, medals. Installs an `lzo` shim over `lzallright` before importing `pygbx` |
| `reproduce.py` | Re-drives a replay tick by tick | `input_timeline()` (event at t applies from tick t − 10 ms), `reproduce()`; writes `data/reference.json` |
| `reference.py` | Reference trajectory | `Reference`, `Projection` (idx, arc length `s`, `offset`, `t_ref`, `along`) |
| `search.py` | **Beam search** | `ACTIONS` (G, GL, GR, -, L, R, BL, BR), `Config` (beam 40, 5-tick steps, 12 m corridor, 600 ms behind limit), `SNode`, `BeamSearch`, `verify()`, `to_tmi_script()` |
| `local.py` | **Local search** | `LocalSearch`: moves = shift / tap / lift / brake→lift / analog steer nudges; cached states every N ticks; `--warp` time warp; `--shared` file for parallel workers |
| `astar.py` | **A\* / weighted A\*** | `AStar(BeamSearch)`, `h()` distance (admissible) or `pb` (not admissible), `dominates()`, memory-capped open list |
| `compare.py` | Experiment tables | builds `results/comparison.*`, `results/repeats.*`, `experiments_summary.txt` |
| `progress.py` | Learning curve | `results/learning_curve.png`, `results/tweaks.{csv,md}`; also defines the shared colour palette |
| `analyze.py` | Re-run + plot one result | top-down line vs PB, time gained along the track, `*.trajectory.csv`; `LABELS` maps label → Action |
| `wrstats.py` | World-record experiment stats | `results/wr_stats.json` |
| `play.py` | Watch a result in-game | normal speed, rendering on, chosen camera |
| `record.py` | Record video per camera | one frame per tick → 100 fps MP4 + JSON sidecar (`video/raw/*.json`) |
| `video.py` | Edit the demo video | overlays (timer, speed, gap, mini-map), slow-mo, side-by-side, end card |

### Plugin: `plugin/SearchBridge.as` (AngelScript, TMInterface 2.2.1)

- `Main()` registers `sb_port` and the `sb_listen` command, and unloads any loaded input script.
- `OnRunStep()` is the heart: it serves commands, rewinds to stored states, applies inputs
  (`ApplyInputs`) and replies with `SendNode` (position, velocity, rotation, checkpoints, wall
  contact, wheels, sliding, display speed).
- `OnCheckpointCountChanged()` blocks the finish (`guardFinish`).
- `Render()` captures frames for recording and screenshots.
- `ParkAtStart()` / `Disconnect()` make the car safe when Python drops.
- The command IDs at the top must stay in sync with `tmsearch/bridge.py`.

### Data and results

| Path | Contents |
|---|---|
| `maps/B01-Race.Challenge.Gbx` | The map (extracted from a replay) |
| `data/replays/MIS_B01-Race.Replay.gbx` | The team's PB replay (27.82). `corrupted_autosave_2312…` is a kept backup, see the story |
| `data/replays/downloaded/wr_oli10_25230.Replay.gbx` | oli10's world record (third-party, analog gamepad) |
| `data/reference.json`, `reference_wr.json` | Reference trajectories: `trace_fields` = t,x,y,z,vx,vy,vz,cp |
| `results/*.json` | A run: `finish_time`, `verified_finish_time`, `reference_time`, `medals`, `inputs` = list of `[ticks, label]` segments |
| `results/*.txt` | TMInterface script export (doesn't reproduce runs exactly, so use `play.py`) |
| `results/*.trajectory.csv` | t_ms, x, y, z, speed_kmh per tick |
| `results/progress_*.json` | Local-search worker progress samples (every 30 s) |
| `results/shared_best.json`, `shared_wr.json` | The best run shared between parallel workers |
| `results/compare/`, `results/repeats/` | Overnight experiment outputs (algorithm comparison, 5-seed repeats) |
| `results/*.md`, `*.png`, `*.csv` | Human-readable tables and charts |

The file-name pattern `local_seed31_w1b-start_27280.json` means: local search, random seed 31,
worker tag `w1b-start`, finish time 27,280 ms. The pattern `b40_s5_full_27660` means beam 40,
5-tick steps, full track, 27.66 s.

### Everything else

| Path | Contents |
|---|---|
| `tests/test_offline.py` | 17 unit tests, no game needed (labels, replays, timelines, projection, moves, A* heuristic admissibility, dominance) |
| `tests/test_game.py` | Needs a running game (`TM_PORT`); checks saved runs replay to their times and determinism |
| `scripts/overnight.sh` | The overnight batch: beam widths, A* variants, 5 repeats; resumable via `.done` markers |
| `report/make_report.py` | Generates `report/CSCI218_B01_report_DRAFT.docx` from `results/` |
| `deck/claude_design/` | Presentation: PDF, offline HTML, prompts, speaker notes |
| `video/` | Stills and raw-clip metadata. The MP4s themselves are in the GitHub release `media-v1` |
| `project_journey.txt` | The story of the project, in order: what was tried, what failed, the timeline |
| `proposal_answers.txt` | Answers to the project proposal questions |
| `CSCI218_Project_Guidelines_Grading_Criteria_AUT26.docx` | The course brief and grading criteria |

## 4. The results an explanation should mention

| Run | Time |
|---|---|
| World record, oli10 (human) | 25.23 |
| AI starting from the WR (local search, 4 workers, 35 min) | **25.21** |
| Author medal | 26.20 |
| AI from the PB: beam → local → parallel local (86 min) | **27.28** |
| Local search from the PB, 5 seeds × 1,500 evals | 27.28 ± 0.14 (best 27.10) |
| Beam search, width 40 | 27.66 |
| Gold medal | 27.80 |
| PB (baseline) | 27.82 |

Findings worth explaining:

- Beam search is myopic. Its early leads vanished, and its gain came from the final drop.
- Most local-search gains are throttle timing in the last ~6 s.
- A\*/weighted A\* never got past 12% of the track on a 180,000-step budget. This is a good
  teaching example of why A\* struggles when the state space is continuous and states are expensive.
- Only beam width 40 beat the PB. Widths 1, 10 and 20 all tied it.

## 5. How to run things

See `README.md` for the full commands. In short (Windows, PowerShell):

```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt; .venv\Scripts\pip install --no-deps pygbx==0.3
.venv\Scripts\python -m unittest discover -s tests -v     # works without the game
```

Everything else needs TrackMania Nations Forever with ModLoader + TMInterface 2.2.1, the plugin
copied into `Documents\TMInterface\Plugins\`, and the map loaded.

## 6. Rules for an agent working on this repo

1. **Never upload, submit or play online with any run produced here.** They're tool-assisted.
   Don't weaken the plugin's finish guard.
2. **Don't overwrite the PB replay or the game's Autosaves.** Back them up before any experiment.
3. Keep the plugin's command IDs and node struct (`_NODE = "<ii9fiiBBBi"`) in sync with `bridge.py`.
4. Free plugin state ids you no longer need. The game is 32-bit.
5. Don't minimise the game window during a search (the game loop pauses). Focus each new
   instance once (`Bridge.focus()`).
6. Large media lives in the GitHub release, not the repo (100 MB file limit). Don't commit MP4s.
7. The code style is plain, typed Python 3.12+, with dataclasses and argparse CLIs. Each module's
   docstring explains it. Read the docstring before changing a module.

## 7. Good first answers for common questions

- *"How does the AI drive?"* It doesn't learn. It searches over sequences of key presses. It tries
  them in the real game physics through save and restore, and keeps what finishes faster.
- *"Where is the search algorithm?"* Beam: `tmsearch/search.py` `BeamSearch.run`. Local:
  `tmsearch/local.py` `LocalSearch.run`. A\*: `tmsearch/astar.py` `AStar.run`.
- *"How is 'better' measured during the search?"* By `Reference.project()`: how far ahead of the PB
  ghost a car is at the same race time. The final judge is the verified finish time.
- *"What happened over the project?"* See `project_journey.txt`.
