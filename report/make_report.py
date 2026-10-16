"""Builds the group report draft (report/CSCI218_B01_report_DRAFT.docx) from the results on disk.

Follows the structure in Section 4.1 of the project guidelines. Numbers are read from results/ and data/
wherever a file holds them, so re-running this after new experiments updates the draft.

    .venv\\Scripts\\python report\\make_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.shared import Cm, Pt, RGBColor  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "report"
ASSETS = ROOT / "deck/claude_design/assets"


def load(rel: str) -> dict | None:
    p = ROOT / rel
    return json.loads(p.read_text()) if p.exists() else None


ref = load("data/reference.json")
PB, MEDALS = ref["race_time"], ref["medals"]
B40 = load("results/b40_s5_full_27660.json")
L218 = load("results/local_seed218_27420.json")
WR = load("results/wr_stats.json")
EXP = load("results/experiments_summary.json")


def s(ms: float) -> str:
    return f"{ms / 1000:.2f} s"


# -- figures ---------------------------------------------------------------------------------------
def architecture_figure(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 3.4), dpi=200)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 34)
    ax.axis("off")
    boxes = [
        (2, 8, 24, 18, "TrackMania Nations Forever\n+ TMInterface 2.2.1\n(physics, 10 ms ticks,\nsave / rewind states)"),
        (36, 8, 24, 18, "SearchBridge plugin\n(AngelScript, in the game)\nexpand · free · wait_start\nrecord · finish guard"),
        (70, 20, 28, 12, "tmsearch (Python)\nbeam · A* / weighted A*\nlocal search · parallel workers"),
        (70, 2, 28, 12, "results/*.json (inputs)\nverify · analyze · record\nvideo · compare · report"),
    ]
    for x, y, w, h, text in boxes:
        ax.add_patch(plt.Rectangle((x, y), w, h, fc="#EAF7EF", ec="#0B3256", lw=1.4))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=8.5, color="#071A2C")
    arrow = dict(arrowstyle="<->", color="#0B3256", lw=1.4)
    ax.annotate("", xy=(36, 17), xytext=(26, 17), arrowprops=arrow)
    ax.text(31, 19, "in-process", ha="center", fontsize=7.5, color="#40566B")
    ax.annotate("", xy=(70, 26), xytext=(60, 20), arrowprops=arrow)
    ax.text(64, 25.5, "TCP\n127.0.0.1", ha="center", fontsize=7.5, color="#40566B")
    ax.annotate("", xy=(84, 14), xytext=(84, 20), arrowprops=dict(arrowstyle="->", color="#0B3256", lw=1.4))
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)


# -- document helpers ------------------------------------------------------------------------------
doc = Document()
st = doc.styles["Normal"]
st.font.name = "Calibri"
st.font.size = Pt(11)
for sec in doc.sections:
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
fig_no = [0]
tab_no = [0]


def h(text: str, level: int = 1) -> None:
    doc.add_heading(text, level=level)


def p(text: str, bold_lead: str | None = None) -> None:
    para = doc.add_paragraph()
    if bold_lead:
        para.add_run(bold_lead).bold = True
    para.add_run(text)


def bullets(items: list[str]) -> None:
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def caption(prefix: str, counter: list[int], text: str) -> None:
    counter[0] += 1
    para = doc.add_paragraph()
    r = para.add_run(f"{prefix} {counter[0]}. {text}")
    r.italic = True
    r.font.size = Pt(9.5)
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER


def figure(path: Path, text: str, width_cm: float = 15.5) -> None:
    if not path.exists():
        p(f"[Figure missing: {path.relative_to(ROOT)}]")
        return
    doc.add_picture(str(path), width=Cm(width_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption("Figure", fig_no, text)


def table(header: list[str], rows: list[list[str]], text: str) -> None:
    caption("Table", tab_no, text)
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    for cell, value in zip(t.rows[0].cells, header):
        cell.text = ""
        cell.paragraphs[0].add_run(value).bold = True
    for row in rows:
        cells = t.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = str(value)
    for row in t.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.font.size = Pt(9.5)
    doc.add_paragraph()


def code(text: str) -> None:
    para = doc.add_paragraph()
    run = para.add_run(text.strip("\n"))
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    run.font.color.rgb = RGBColor(0x07, 0x1A, 0x2C)
    para.paragraph_format.left_indent = Cm(0.5)


# -- content ---------------------------------------------------------------------------------------
def build() -> None:
    OUT.mkdir(exist_ok=True)
    arch = OUT / "fig_architecture.png"
    architecture_figure(arch)

    title = doc.add_heading("Can a Search Algorithm Beat My TrackMania PB?", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for line in ("Searching inside real game physics on TrackMania Nations Forever, map B01-Race",
                 "CSCI218 Foundations of Artificial Intelligence, Group Project, Autumn 2026, UOWD",
                 "[Team members and student IDs]", "[Lab group]   ·   [Date]"):
        para = doc.add_paragraph(line)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note = doc.add_paragraph()
    r = note.add_run("DRAFT generated from the project's result files. Check every [bracketed] item, rewrite in "
                     "the team's own words, and follow the subject descriptor's rules on the use of AI tools.")
    r.italic = True
    r.font.color.rgb = RGBColor(0xB0, 0x30, 0x30)
    doc.add_page_break()

    # a) Executive summary
    h("1. Executive summary")
    wr_line = (f" Starting instead from the human world record (25.23 s by oli10), which our pipeline re-drives exactly, "
               f"local search found a {s(WR['best_ms'])} run after {WR['evals_total']:,} candidate runs "
               f"({WR['minutes']:.0f} minutes on four game instances).") if WR else ""
    p(f"We built an AI agent that searches for a faster way to drive a TrackMania Nations Forever track than a "
      f"team member's personal best (PB). The game itself is the simulator: a plugin we wrote for TMInterface saves "
      f"and restores the full car state, steps the physics in 10 ms ticks and reports the result to Python over TCP, "
      f"so every candidate run is evaluated with the real game physics. On the map B01-Race the PB is {s(PB)}, "
      f"0.02 s short of the gold medal ({s(MEDALS['gold'])}). A time-layered beam search guided by the PB's own "
      f"trajectory reached {s(B40['finish_time'])} in 20 minutes; stochastic local search (hill climbing over the key "
      f"presses) then reached {s(L218['finish_time'])}, and four parallel local-search workers reached 27.28 s, "
      f"0.54 s faster than the PB and faster than gold. Every time reported was verified by re-running the inputs "
      f"from the start line in the game." + wr_line)
    if EXP:
        rep = EXP.get("repeats") or {}
        txt = ("We compared beam search (widths 1-40) with A* and weighted A* under two heuristics on the same "
               "budget of game steps: no A* variant reached the finish, every beam search did, and only the widest "
               "beam improved on the PB.")
        if rep:
            txt += (f" Repeated runs of local search from the PB ({rep['n']} seeds) gave "
                    f"{rep['mean_s']:.3f} ± {rep['std_s']:.3f} s (mean ± standard deviation).")
        p(txt)

    # b) Motivation
    h("2. Motivation")
    p("Racing games are a clean testbed for search: the goal is unambiguous (cross the line sooner), the dynamics "
      "are complex (momentum, grip, drifting, jumps) and a skilled human gives a strong, measurable baseline. "
      "TrackMania Nations Forever adds two properties that make search practical. Its physics is deterministic, "
      "so the same inputs from the same state always produce exactly the same run, and the TMInterface tool can "
      "save and restore the complete simulation state. Together they turn a video game into a perfect, rewindable "
      "simulator: a search node is a saved game state and expanding it means holding some keys for a few ticks.")
    p("The question is simple and measurable: can an AI that only presses the same four keys as the player find "
      "a faster run than the player's PB on the same map? We chose search rather than reinforcement learning "
      "because the game runs at roughly 120-150 simulated steps per second per instance: an RL agent would need "
      "millions of episodes, while search can exploit determinism and save states directly, and its decisions can "
      "be inspected and explained input by input.")
    p("", bold_lead="Objectives:")
    bullets([f"O1. Beat the PB ({s(PB)}) and the gold medal ({s(MEDALS['gold'])}) on B01-Race with runs that are "
             "verified in the game.",
             "O2. Compare search algorithms covered in the subject (beam search, A*, weighted A*, local search) on "
             "the same problem and budget, against the human PB and a greedy baseline.",
             "O3. Report repeated runs with mean and standard deviation for the stochastic method.",
             "O4. Explain what the AI changed, in terms a driver understands."])

    # c) Technical requirements
    h("3. Technical requirements")
    table(["Item", "What we used"], [
        ["Game", "TrackMania Nations Forever (Steam), patched to 2.11.26"],
        ["Tool-assisted interface", "TMInterface 2.2.1, loaded through TrackMania ModLoader 1.0.1"],
        ["Our plugin", "plugin/SearchBridge.as (AngelScript, runs inside TMInterface)"],
        ["Search code", "Python 3.14, package tmsearch (numpy, matplotlib; pygbx + lzallright for replay files)"],
        ["Hardware", "AMD Ryzen 7 7735HS (8 cores / 16 threads), 64 GB RAM, Windows 11"],
        ["Throughput", "~150 game steps (expansions) per second per game instance with rendering off; "
                       "~120/s while another CPU-heavy job was running (overnight experiments)"],
        ["Map and data", "B01-Race (official map, extracted from the PB replay); the PB replay; oli10's world-record "
                         "replay from TMNF-Exchange (track 2282)"],
    ], "Software, hardware and data.")
    p("Constraints: TMInterface runs only offline, so every run here is tool-assisted and is never uploaded. Our "
      "plugin blocks the finish while it is in control and parks the car on the start line when the search "
      "disconnects, so the game never registers a searched run as a record.")

    # d) Main constructs
    h("4. Main constructs")
    h("4.1 The problem as a search problem", 2)
    table(["Element", "Definition"], [
        ["State", "The complete simulation state at one physics tick (a TMInterface save state, about 9 KB). "
                  "Python sees race time, position, velocity, orientation, checkpoint count and wall contact."],
        ["Actions", "8 key combinations: throttle, throttle+left, throttle+right, nothing, left, right, "
                    "brake+left, brake+right, each held for 5 ticks (50 ms). Analog steering for the WR replay."],
        ["Transition", "Rewind the game to the parent's state, hold the action, step the real physics."],
        ["Goal", "Crossing the finish line (after both checkpoints)."],
        ["Cost", "Race time; the finish time is exact to 10 ms."],
        ["Size", "A 27.8 s run is ~556 decisions with 8 choices: 8^556 ≈ 10^502 action sequences."],
    ], "State space formulation.")
    h("4.2 Telemetry and the reference line", 2)
    p(f"We parse the PB replay (key events, checkpoint and medal times) and re-drive it tick by tick through the "
      f"plugin. It finishes at exactly {PB / 1000:.3f} s with matching checkpoint times, which validates our timing "
      f"(a replay event at time t acts from race time t - 10 ms). The recorded positions form the reference line. "
      f"Projecting a car position onto it gives progress s (metres along the line), the distance from the line, and "
      f"the time the PB needed to reach that progress, so 'ms ahead of the PB ghost' can be measured at any point.")
    h("4.3 Pruning and duplicate detection", 2)
    bullets(["Off-line: more than 12 m from the reference line.",
             "Pace: more than 600 ms behind the PB ghost at the same progress.",
             "Checkpoint: passed a checkpoint's position without registering it.",
             "Near-duplicates: states in the same 1 m cell, 6° heading bin and 1.5 m/s speed bin are merged "
             "(this cell-plus-continuous-state idea is the core of Hybrid A* used in vehicle path planning)."])
    h("4.4 Algorithms", 2)
    p("All nodes in a layer share the same race time. Each node is expanded with all 8 actions; children are "
      "ranked by how far ahead of the PB ghost they are, projected 0.3 s ahead using their speed along the track, "
      "with a small penalty for wall contact, and the best B are kept. The PB's own run is carried along in every "
      "layer as a safety net, so the result can never be slower than the PB. Complexity: O(B · 8) expansions per "
      "50 ms layer, about 556 · 8 · B game steps per run.", bold_lead="Beam search through time. ")
    p("Best-first search with f(n) = g(n) + w · h(n), where g is the race time so far. We tested two heuristics. "
      "(1) Distance: remaining reference length divided by 1000 km/h, the game's speed cap. The car cannot cover the "
      "rest of the track faster, so h never overestimates (admissible) and is consistent; a unit test checks this "
      "against both the PB and the world-record runs. (2) PB time left: the time the PB still needed from the node's "
      "progress. It is far better informed but not admissible, since the PB is not optimal (the world record needs "
      "more than a second less on parts of the track). w = 1 gives A*; w > 1 trades optimality for speed. A node is a "
      "duplicate if another node in its cell is no later, at least as far along and at least as fast along the "
      "track. Each open node holds a game state, so the open list is capped at 20,000 nodes (memory-bounded A*).",
      bold_lead="A* and weighted A*. ")
    p("The run is a list of per-tick inputs. A move changes it locally: shift a key press or release by 10-40 ms, "
      "tap or release a key briefly, lift off the throttle for 50-400 ms, coast instead of braking, or (with "
      "analog steering) steer a few percent more or less, or earlier or later, over a short window. The candidate "
      "is simulated from a cached state just before the change to the finish and accepted only if it finishes "
      "earlier (or equally early but further past the line one tick before). Candidates more than 150 ms behind "
      "the current best are abandoned early. Time warp: after an early change, the rest of the run is shifted in "
      "time so later inputs still line up with the track. Parallel workers on separate game instances share their "
      "best run through a file, each focusing on one section of the track.", bold_lead="Local search (hill climbing). ")

    # e) Implementation
    h("5. Implementation")
    figure(arch, "System architecture. The search runs in Python; the game is the simulator.")
    p("The plugin (565 lines) keeps saved states in an array and serves commands while the game is paused inside "
      "its per-tick callback, which keeps Python and the physics in lock-step. An expansion rewinds to the parent "
      "state, applies the inputs and steps the game:")
    code("""
case C_EXPAND: {
    int parent = client.ReadInt32();
    int ticks = client.ReadInt32();
    inUp = client.ReadUint8() != 0;   inDown = client.ReadUint8() != 0;
    inLeft = client.ReadUint8() != 0; inRight = client.ReadUint8() != 0;
    inSteer = client.ReadInt32();
    sm.RewindToState(states[parent], false);
    sm.PreventSimulationFinish();      // a searched run is never registered as a finish
    ApplyInputs(sm);
    stepsLeft = ticks;
    mode = MODE_STEPPING;              // the node is sent back when stepsLeft reaches 0
    return true;
}""")
    p("In Python, A* differs from the beam search only in how nodes are ordered and kept:")
    code("""
def h(self, sn):
    if self.heuristic == "pb":                       # PB's time left (not admissible)
        return max(self.ref.race_time - sn.proj.t_ref, 0.0)
    return max(self.ref.length - sn.proj.s, 0.0) / self.v_max * 1000.0   # admissible

def f(self, sn):
    if sn.node.finished:
        return float(sn.node.finish_time)
    return sn.t + self.weight * self.h(sn)""")
    p("The local search accepts a candidate only on a strictly better (or equal-time but further) finish:")
    code("""
finish, s_tie, used = self.evaluate(cand, first, last)
better = finish > 0 and (finish < self.best_finish or
                         (finish == self.best_finish and s_tie > self.best_tiebreak + 1e-3))
if better:
    self.accept(used, first)        # becomes the new best; state cache refreshed from here""")
    figure(ASSETS / "clip1_ai_run_still.png", "The AI's run recorded in the game with our data panel: race time, "
           "speed, gap to the PB ghost and a mini-map. Frames are captured once per physics tick by the plugin.")
    p("tests/test_offline.py (17 tests, no game needed) checks replay parsing, the "
      "input timeline, the reference projection, script export, that local-search moves are reproducible with a "
      "fixed seed and only change inputs inside the reported window, that the distance heuristic never "
      "overestimates and that the PB heuristic does. tests/test_game.py replays saved runs in the game and checks "
      "their finish times and that the same inputs give the same result twice. All searches take a seed; all "
      "results are stored as input lists with their verified finish times; scripts/overnight.sh reruns the "
      "experiments.", bold_lead="Testing and reproducibility. ")

    # f) Experiments and results
    h("6. Experiments and results")
    p("Metrics: verified finish time; game steps simulated (expansions or candidate evaluations), which is "
      "independent of machine load; wall-clock time; for runs that did not finish, how far along the track they got. "
      "Baselines: the human PB (27.82 s) and greedy beam search (width 1).")
    h("6.1 Main pipeline", 2)
    table(["Stage", "Result", "vs PB", "Work"], [
        ["My PB (human baseline)", s(PB), "-", "-"],
        ["Beam search, width 40", s(B40["finish_time"]), f"{(B40['finish_time'] - PB) / 1000:+.2f} s",
         f"{B40['stats']['expansions']:,} expansions, {B40['stats']['seconds'] / 60:.0f} min"],
        ["Local search from the beam result", s(L218["finish_time"]), f"{(L218['finish_time'] - PB) / 1000:+.2f} s",
         f"{L218['stats']['evaluations']:,} candidates, {L218['stats']['accepted']} accepted, 30 min"],
        ["Parallel local search (4 game instances)", "27.28 s", "-0.54 s", "≈29,400 candidates, 129 kept"],
        ["Gold medal", s(MEDALS["gold"]), f"{(MEDALS['gold'] - PB) / 1000:+.2f} s", "-"],
    ], "From the PB to 27.28 s. All times verified by replaying the inputs from the start in the game.")
    figure(ROOT / "results/learning_curve.png", "Best verified time against search time across the three stages "
           "(27.82 → 27.28 s in 86 minutes).")
    p("Beam search followed the PB line for the first ~1,900 m; its 0.16 s came from the "
      "final drop, where a short brake and coast before landing avoids the PB's large speed loss. It was myopic: "
      "leads of 45 ms (at 5 s) and 96 ms (at 22 s) vanished within a second because ranking by short-term progress "
      "rewards carrying speed into a corner. Local search judges every change by the finish time instead. Workers "
      "restricted to the first 20 s kept nothing; almost all gains are throttle timing in the last six seconds "
      "(Table 3).", bold_lead="Where the time came from. ")
    table(["Saved", "At", "Change"], [
        ["30 ms", "21.55 s", "throttle back on 20 ms earlier"],
        ["20 ms", "25.50 s", "stay on the throttle 40 ms longer before the drop"],
        ["20 ms", "25.62 s", "stay on the throttle 40 ms longer"],
        ["10 ms", "23.85 s", "release the brake 10 ms earlier"],
        ["10 ms", "25.60 s", "a 20 ms throttle tap while coasting"],
    ], "The largest changes kept by the parallel local search (full list: results/tweaks.md).")
    figure(ROOT / "results/local_seed31_w1b-start_27280.png", "The AI's line against the PB (left) and time gained "
           "along the track (right): nothing until the last corners, then 0.54 s.")

    h("6.2 Search algorithms compared", 2)
    if EXP and EXP.get("comparison"):
        rows = []
        for r in EXP["comparison"]:
            res = s(r["finish_ms"]) if r["finished"] else f"no finish ({r['progress_pct']:.0f}% of track)"
            vs = f"{(r['finish_ms'] - PB) / 1000:+.2f} s" if r["finished"] else "-"
            rows.append([r["method"], r["heuristic"], res, vs, f"{r['expansions']:,}", f"{r['seconds'] / 60:.1f} min"])
        table(["Method", "Heuristic / ranking", "Result", "vs PB", "Game steps", "Time"], rows,
              "Algorithms on the same problem, actions and pruning. A* budget: 180,000 game steps (what beam width 40 "
              "needed). Overnight runs shared the CPU with another job, so compare game steps, not minutes.")
        figure(ROOT / "results/comparison.png", "Track covered against game steps simulated, for A* and weighted A* "
               "with both heuristics; diamonds mark where each beam search finished.")
        if EXP.get("beam10_deterministic") is not None:
            p("Determinism check: beam search with width 10 was run twice and produced "
              + ("identical inputs, finish time and expansion count." if EXP["beam10_deterministic"]
                 else "different results [investigate]."))
        p("No A* variant reached the finish within 180,000 game steps, while every beam search finished with fewer "
          "steps. Three different failure modes explain this.", bold_lead="Interpretation. ")
        bullets([
            "Admissible but weak heuristic. Dividing the remaining distance by 1000 km/h underestimates the remaining "
            "time about four-fold (the PB averages 273 km/h), so f grows along every path and A* expands states in "
            "order of race time - almost a uniform-cost, breadth-first search through time. The number of distinct "
            "states per 50 ms layer grows quickly, so plain A* was still about 3 s into a 27.8 s race when its budget "
            "ran out. Raising the weight to 5 or 20 helps only a little.",
            "Weighted A* dives. A large weight prefers deeper nodes even when they are slower. Those branches "
            "eventually fall 600 ms behind the PB ghost and are pruned, and the search then backtracks through a huge "
            "number of slow branches around the first corner (200-260 m, about 6.5 s into the race).",
            "Informed but not admissible: plateaus. With the PB's remaining time as h, f = g + h is almost exactly "
            "the PB's time for every state that keeps the PB's pace. Thousands of states tie at f ≈ 27.82 s and A* "
            "must expand all of them before it can go deeper.",
            "Beam search avoids all three by fixing the work per 50 ms layer (linear in the race length) and ranking "
            "states with the same information as the PB heuristic. The price is completeness: widths 1, 10 and 20 never "
            "found anything faster than the PB, and only width 40 kept the branch that gained 0.16 s at the final drop.",
        ])
        p("The lesson for this problem: with a 10 ms clock and a 556-step horizon, the bottleneck is not whether the "
          "heuristic is admissible but how the frontier is bounded. A*'s optimality guarantee is also moot here, "
          "because the action discretisation (8 actions held for 50 ms) and the pruning already exclude the true "
          "optimum. This is why we moved from tree search to beam search plus local search over whole runs.")
    else:
        p("[Pending: results/experiments_summary.json not found. Run scripts/overnight.sh then "
          "python -m tmsearch.compare, and rebuild this report.]")

    h("6.3 Repeated runs", 2)
    rep = (EXP or {}).get("repeats") or {}
    if rep:
        p(f"Beam search and A* are deterministic, so repeating them gives identical results. Local search is "
          f"stochastic, so we repeated it from the PB with {rep['n']} seeds and the same budget of "
          f"{rep['evals_each']:,} candidate evaluations each: {rep['mean_s']:.3f} ± {rep['std_s']:.3f} s (mean ± "
          f"standard deviation), best {rep['best_s']:.2f} s, worst {rep['worst_s']:.2f} s; {rep['beat_gold']} of "
          f"{rep['n']} runs beat gold." + (f" Evaluations until gold was first beaten: {rep['evals_to_gold_mean']:.0f} ± "
                                             f"{rep['evals_to_gold_std']:.0f}." if "evals_to_gold_mean" in rep else ""))
        p(f"The best seed finished in {rep['best_s']:.2f} s after about 13 minutes on one game - faster than the "
          f"27.28 s that the 86-minute, four-instance pipeline of Section 6.1 reached. Seeds differ by up to "
          f"{rep['worst_s'] - rep['best_s']:.2f} s on the same budget, so a single run of a stochastic search says "
          f"little: the variance between runs is as important a result as the best time. Every seed found one large "
          f"change worth 0.16-0.32 s somewhere between 17.1 and 22.3 s into the race (for example a 30 ms brake tap "
          f"at 21.73 s, or releasing steer-left 10 ms earlier at 22.28 s), plus many smaller refinements worth "
          f"10-100 ms each: of the 61 time-saving smaller changes, 36 adjusted steering, 15 the brake and 10 the "
          f"throttle.")
        rep_md = ROOT / "results/repeats.md"
        if rep_md.exists():
            lines = [ln for ln in rep_md.read_text().splitlines()[2:] if ln.startswith("|")]
            rows = [[c.strip().replace("**", "") for c in ln.strip("|").split("|")] for ln in lines]
            table(["Seed", "Finish", "vs PB", "Accepted", "Evaluations", "First beat gold"], rows,
                  "Local search from the PB, one row per seed.")
        figure(ROOT / "results/repeats.png", "Best time against evaluations for each seed, with the mean ± standard "
               "deviation of the final times.")
    else:
        p("[Pending: repeated-run results not found yet.]")

    h("6.4 The world record", 2)
    if WR:
        p(f"To test the method on a much stronger starting point we downloaded the human world record, 25.23 s by "
          f"oli10 (a normal, non tool-assisted run). It uses an analog gamepad, so we added analog steering and found "
          f"that replays store steering with the opposite sign to TMInterface. Re-driven through our pipeline it "
          f"finishes at exactly 25.230 s. Four local-search workers then refined it: the first faster run came after "
          f"{WR['first_beat_evals']:,} candidates ({WR['first_beat_min']:.1f} min) from \"{WR['first_beat_change']}\", "
          f"and after {WR['evals_total']:,} candidates ({WR['accepted_total']} accepted, {WR['minutes']:.0f} min) the "
          f"best run was {s(WR['best_ms'])}, {(WR['record_ms'] - WR['best_ms'])} ms under the record. This is the "
          f"human's line refined with machine precision; it is tool-assisted and not a record.")
    figure(ASSETS / "clip4_wr_vs_ai_still.png", "oli10's world record (left) and the AI's refinement of it (right) at "
           "the same moment of the race.", 14)

    # g) Limitations
    h("7. Limitations, difficulties and constraints")
    p("", bold_lead="Threats to validity:")
    bullets(["One map and one PB: no evidence yet that the results generalise to other tracks or surfaces.",
             "The searches are tied to a reference line (for ranking and pruning). Local search refines a line but "
             "does not discover new routes: from the PB's line the AI is still 2.05 s off the world record.",
             "Pruning thresholds (12 m, 600 ms) were set by hand; they shape what every algorithm can find.",
             "Wall-clock times depend on machine load (the overnight experiments shared the CPU), so game steps are "
             "the fair cost measure.",
             "Repeated runs used a smaller budget (1,500 evaluations) than the main local search runs."])
    p("", bold_lead="Difficulties we solved:")
    bullets(["A searched finish was once registered as a real record and overwrote the PB's autosaved replay: "
             "TMInterface's PreventSimulationFinish only works inside the checkpoint callback. We restored the replay "
             "from a backup, added a finish guard and park the car at the start whenever the search disconnects.",
             "TMInterface input scripts exported from our runs desync at every timing offset we tried, so runs are "
             "always verified and replayed through our plugin instead.",
             "A loaded input script silently overrides the plugin's inputs; the plugin now unloads scripts.",
             "Analog steering in replays has the opposite sign to TMInterface's input.",
             "A* duplicate detection first threw away almost every node at the start: in the first half-second the "
             "car is still settling on its suspension and braking rolls it backwards, so 'earlier and faster' "
             "wrongly marked real progress as a duplicate. Dominance now also requires being at least as far along "
             "the track, using speed along the track.",
             "Each open A* node holds a 9 KB game state in a 32-bit game, so the open list had to be capped.",
             "Several game instances only accept inputs after each window has been focused once."])
    p("Deviation from the proposal: we planned custom tarmac maps of increasing length and a grid-A* plus "
      "path-following baseline. We used the official map the PB was set on instead, and the human PB and greedy "
      "beam search as baselines. [Confirm wording with the team.]")

    # h) Conclusion
    h("8. Conclusion and discussion")
    p("Search inside the real game physics beat the PB by 0.54 s (27.82 → 27.28 s) and the gold medal, and, "
      "starting from the human world record, found a run 0.02 s faster than it. The decisive design choices were "
      "using the game as an exact, rewindable simulator; a reference line that turns raw positions into progress "
      "and time gained; and judging changes by the final finish time rather than short-term progress. "
      "The algorithm comparison showed why: A* and weighted A* could not bound their frontier on a 556-step horizon, "
      "while time-layered beam search could; and the repeated runs showed that local search from the PB is reliable "
      "(every seed beat gold) but has a wide spread (0.4-0.7 s gained), so a single run says little and more seeds "
      "or parallel workers pay off. Next steps: more maps; search that is not "
      "tied to one reference line (to find new routes); and using these optimised runs as training data for a "
      "learned driving policy.")

    # i) References
    h("9. References")
    for ref_text in [
        "Hart, P. E., Nilsson, N. J. and Raphael, B. (1968) 'A formal basis for the heuristic determination of minimum "
        "cost paths', IEEE Transactions on Systems Science and Cybernetics, 4(2), pp. 100-107.",
        "Pohl, I. (1970) 'Heuristic search viewed as path finding in a graph', Artificial Intelligence, 1(3-4), "
        "pp. 193-204.",
        "Lowerre, B. T. (1976) The HARPY Speech Recognition System. PhD thesis, Carnegie Mellon University.",
        "Russell, S. (1992) 'Efficient memory-bounded search methods', Proceedings of the 10th European Conference on "
        "Artificial Intelligence (ECAI-92), pp. 1-5.",
        "Russell, S. and Norvig, P. (2021) Artificial Intelligence: A Modern Approach. 4th edn. Pearson.",
        "Dolgov, D., Thrun, S., Montemerlo, M. and Diebel, J. (2008) 'Practical search techniques in path planning "
        "for autonomous driving', Proceedings of the First International Symposium on Search Techniques in Artificial "
        "Intelligence and Robotics (STAIR-08). [check]",
        "donadigo. TMInterface (tool-assisted interface for TrackMania Nations Forever), version 2.2.1. "
        "https://donadigo.com/tminterface [check URL and access date]",
        "TrackMania ModLoader (TMLoader), version 1.0.1. [add URL and access date]",
        "TrackMania Nations Forever Exchange (TMNF-X). B01-Race, track 2282; world-record replay by oli10. "
        "https://tmnf.exchange/trackshow/2282 [access date]",
        "pygbx: Python parser for GameBox files. https://github.com/donadigo/pygbx [check]",
    ]:
        doc.add_paragraph(ref_text, style="List Number")

    # j) Appendices
    doc.add_page_break()
    h("Appendix A: Git repository and team contributions")
    p("Git repository: [link]")
    table(["Student name & ID", "Main contributions (code, report sections, experiments)", "Est. share (%)", "Signature"],
          [["[ ]", "[ ]", "[ ]", ""] for _ in range(5)], "Team contribution table (template from the guidelines).")
    h("Appendix B: Industry engagement")
    p("Not applicable (no industry partner).")
    h("Appendix C: Reproducing the results")
    code(r"""
.venv\Scripts\python -m unittest discover -s tests -v              # offline tests
.venv\Scripts\python -m tmsearch.reproduce data\replays\MIS_B01-Race.Replay.gbx
.venv\Scripts\python -m tmsearch.search --beam 40
.venv\Scripts\python -m tmsearch.local --start results\b40_s5_full_27660.json --budget 1800 --seed 218
.venv\Scripts\python -m tmsearch.astar --heuristic pb --weight 2 --budget 180000
bash scripts/overnight.sh                                            # comparison + repeated runs
.venv\Scripts\python -m tmsearch.compare                             # tables, charts, summary
.venv\Scripts\python report\make_report.py                           # this report""")
    h("Appendix D: Use of AI tools")
    p("[Describe how AI tools were used in this project, as required by the subject descriptor.]")

    out = OUT / "CSCI218_B01_report_DRAFT.docx"
    doc.save(out)
    print(f"wrote {out} ({fig_no[0]} figures, {tab_no[0]} tables)")


if __name__ == "__main__":
    build()
