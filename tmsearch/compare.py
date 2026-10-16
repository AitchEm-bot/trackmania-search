"""Overnight experiments -> tables, charts and a plain-text summary.

  1. Search algorithms compared on one game instance: beam search (widths 1/10/20/40), A* and weighted
     A* with an admissible distance heuristic, and A*/weighted A* with my PB's remaining time.
     All of them are deterministic, so one run each (beam width 10 is run twice to show that).
  2. Repeated runs: local search from my PB with 5 seeds and the same evaluation budget -> mean +- std.

Writes results/comparison.{md,csv,png}, results/repeats.{md,png}, results/experiments_summary.txt.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .progress import GOLD, GRID, MUTED, REF, SERIES, SURFACE, TEXT  # noqa: E402

# Lightness steps of the TM mint for an ordered parameter (the weight w), plus a dash per step so the
# lines never rely on colour alone; all >= 4.5:1 on SURFACE.
MINT_STEPS = ("#E3FFEE", "#6EFAA0", "#22A15A")
DASHES = ("-", (0, (6, 3)), (0, (2, 2)))


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def beam_row(path: Path, label: str) -> dict:
    d = load(path)
    s = d["stats"]
    return {"method": label, "family": "beam", "heuristic": "PB ghost (ranking)", "param": s["config"]["beam"],
            "finished": True, "finish_ms": d["verified_finish_time"] or d["finish_time"], "expansions": s["expansions"],
            "seconds": s["seconds"], "progress_pct": 100.0, "inputs": d["inputs"], "file": str(path)}


def astar_row(path: Path) -> dict:
    d = load(path)
    s = d["stats"]
    w = d["weight"]
    h = d.get("heuristic", "distance")
    name = ("A*" if w == 1 else f"Weighted A* (w = {w:g})") + (" + PB heuristic" if h == "pb" else "")
    fin = d["verified_finish_time"] or d["finish_time"]
    pct = 100.0 if fin else 100.0 * s["max_progress_m"] / s["track_length_m"]
    return {"method": name, "family": "astar", "heuristic": "PB time left" if h == "pb" else "distance / 1000 km/h",
            "param": w, "finished": bool(fin), "finish_ms": fin, "expansions": s["expansions"], "seconds": s["seconds"],
            "progress_pct": pct, "progress": s.get("progress", []), "track_m": s["track_length_m"],
            "furthest_m": s["max_progress_m"], "behind_ms": s.get("furthest_vs_pb_ghost_ms"), "file": str(path)}


def fmt_time(ms) -> str:
    return f"{ms / 1000:.2f} s" if ms else "-"


def style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(axis="y", color=GRID, lw=1)
    for s in ax.spines.values():
        s.set_visible(False)


def comparison(rows: list[dict], pb: int, out: Path) -> list[str]:
    with open(out / "comparison.csv", "w", newline="") as fh:
        cols = ["method", "heuristic", "param", "finished", "finish_ms", "expansions", "seconds", "progress_pct", "file"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})
    lines = ["| Method | Heuristic | Result | vs my PB | Game steps (expansions) | Time |",
             "|---|---|---|---|---|---|",
             f"| My PB (human baseline) | - | {pb / 1000:.2f} s | - | - | - |"]
    for r in rows:
        if r["finished"]:
            res, vs = fmt_time(r["finish_ms"]), f"{(r['finish_ms'] - pb) / 1000:+.2f} s"
        else:
            res, vs = f"no finish ({r['progress_pct']:.0f}% of the track)", "-"
        lines.append(f"| {r['method']} | {r['heuristic']} | {res} | {vs} | {r['expansions']:,} | {r['seconds'] / 60:.1f} min |")
    (out / "comparison.md").write_text("\n".join(lines) + "\n")

    # Chart: how far along the track each A* variant got for the work it did; beam runs as finish markers.
    astars = [r for r in rows if r["family"] == "astar"]
    if not astars:
        return lines
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.6), dpi=160, sharey=True)
    fig.patch.set_facecolor(SURFACE)
    beams = [r for r in rows if r["family"] == "beam" and r["finished"] and "(run 2)" not in r["method"]]
    for ax, h, title in ((axes[0], "distance / 1000 km/h", "Distance heuristic (admissible)"),
                         (axes[1], "PB time left", "My PB's time left (not admissible)")):
        style(ax)
        group = sorted((r for r in astars if r["heuristic"] == h), key=lambda r: r["param"])
        ends = []
        for color, dash, r in zip(MINT_STEPS, DASHES, group):
            xs = [0] + [p["expansions"] / 1000 for p in r["progress"]] + [r["expansions"] / 1000]
            ys = [0] + [100 * p["max_progress_m"] / r["track_m"] for p in r["progress"]] + [r["progress_pct"]]
            ax.plot(xs, ys, color=color, lw=2, ls=dash, label=r["method"].replace(" + PB heuristic", ""))
            end = (f"w={r['param']:g}: " + (fmt_time(r["finish_ms"]) if r["finished"] else f"{r['progress_pct']:.0f}%"))
            ends.append([ys[-1], xs[-1], end])
        # end labels, nudged apart so lines that finish close together stay readable
        ends.sort()
        for i in range(1, len(ends)):
            ends[i][0] = max(ends[i][0], ends[i - 1][0] + 5.5)
        for y, x, text in ends:
            ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", color=TEXT, fontsize=10, va="center")
        for b in beams:
            ax.plot([b["expansions"] / 1000], [100], "D", ms=8, color=TEXT, mec=SURFACE, mew=1.5, zorder=4)
            ax.annotate(f"B={b['param']}\n{b['finish_ms'] / 1000:.2f}", (b["expansions"] / 1000, 100), xytext=(0, -12),
                        textcoords="offset points", ha="center", va="top", color=MUTED, fontsize=8.5)
        if beams:
            ax.plot([], [], "D", ms=8, color=TEXT, mec=SURFACE, label="Beam search: finished (B = width)")
        ax.set_title(title, color=TEXT, fontsize=13, loc="left", pad=10)
        ax.set_xlabel("game steps simulated (thousands)", color=MUTED, fontsize=11)
        leg = ax.legend(loc="center right", frameon=False, fontsize=9.5, labelcolor=TEXT)
        del leg
    axes[0].set_ylabel("track covered (%)", color=MUTED, fontsize=11)
    axes[0].set_ylim(0, 108)
    fig.suptitle("A* stalls; beam search finishes", color=TEXT, fontsize=15, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(out / "comparison.png", facecolor=SURFACE)
    plt.close(fig)
    return lines


def repeats(paths: list[Path], pb: int, gold: int, out: Path) -> tuple[list[str], dict]:
    runs = []
    for p in sorted(paths):
        d = load(p)
        s = d["stats"]
        pts = [(0, s["progress"][0]["best_ms"] if s["progress"] else pb)]
        first_gold = None
        for tw in s["tweaks"]:
            pts.append((tw["evals"], tw["finish_after_ms"]))
            if first_gold is None and tw["finish_after_ms"] < gold:
                first_gold = tw["evals"]
        pts.append((s["evaluations"], d["finish_time"]))
        runs.append({"seed": s["seed"], "finish_ms": d["verified_finish_time"] or d["finish_time"],
                     "evals": s["evaluations"], "accepted": s["accepted"], "seconds": s.get("seconds"),
                     "first_gold_evals": first_gold, "points": pts})
    if not runs:
        return [], {}
    fins = [r["finish_ms"] / 1000 for r in runs]
    stats = {"n": len(runs), "mean_s": statistics.mean(fins), "std_s": statistics.stdev(fins) if len(fins) > 1 else 0.0,
             "best_s": min(fins), "worst_s": max(fins), "median_s": statistics.median(fins),
             "mean_gain_s": pb / 1000 - statistics.mean(fins),
             "beat_gold": sum(f < gold / 1000 for f in fins), "evals_each": runs[0]["evals"]}
    golds = [r["first_gold_evals"] for r in runs if r["first_gold_evals"] is not None]
    if golds:
        stats["evals_to_gold_mean"] = statistics.mean(golds)
        stats["evals_to_gold_std"] = statistics.stdev(golds) if len(golds) > 1 else 0.0
    lines = ["| Seed | Finish | vs my PB | Accepted tweaks | Evaluations | First beat gold after |", "|---|---|---|---|---|---|"]
    for r in runs:
        g = f"{r['first_gold_evals']:,} evaluations" if r["first_gold_evals"] is not None else "never"
        lines.append(f"| {r['seed']} | {r['finish_ms'] / 1000:.2f} s | {(r['finish_ms'] - pb) / 1000:+.2f} s |"
                     f" {r['accepted']} | {r['evals']:,} | {g} |")
    lines.append(f"| **mean ± std** | **{stats['mean_s']:.3f} ± {stats['std_s']:.3f} s** |"
                 f" {-stats['mean_gain_s']:+.3f} s | | | |")
    (out / "repeats.md").write_text("\n".join(lines) + "\n")

    fig, ax = plt.subplots(figsize=(12, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    for i, r in enumerate(runs):
        xs = [p[0] for p in r["points"]]
        ys = [p[1] / 1000 for p in r["points"]]
        ax.step(xs, ys, where="post", color=SERIES, lw=1.6, alpha=0.75, label="one seed" if i == 0 else None)
        ax.plot([xs[-1]], [ys[-1]], "o", ms=8, color=SERIES, mec=SURFACE, mew=1.5, zorder=4)
    x_end = max(r["evals"] for r in runs)
    m, sd = stats["mean_s"], stats["std_s"]
    ax.errorbar([x_end * 1.03], [m], yerr=[sd], fmt="s", ms=8, color=TEXT, ecolor=TEXT, elinewidth=2, capsize=6, zorder=5,
                label="mean ± std")
    ax.annotate(f"{m:.2f} ± {sd:.2f} s", (x_end * 1.03, m), xytext=(12, 0), textcoords="offset points",
                color=TEXT, fontsize=12, fontweight="bold", va="center")
    for value, label, col, ls, va in ((pb / 1000, "My PB", REF, (0, (5, 4)), "bottom"), (gold / 1000, "Gold", GOLD, "-", "top")):
        ax.axhline(value, color=col, lw=1.5, ls=ls, zorder=1)
        ax.text(x_end * 0.6, value + (0.004 if va == "bottom" else -0.004), f"{label} {value:.2f}", color=col,
                fontsize=10, va=va, ha="left")
    ax.set_xlim(0, x_end * 1.17)
    ax.set_xlabel("evaluations (candidate runs simulated)", color=MUTED, fontsize=11)
    ax.set_ylabel("best finish time (s)", color=MUTED, fontsize=11)
    ax.set_title(f"Local search from my PB, {len(runs)} seeds, {stats['evals_each']:,} evaluations each",
                 color=TEXT, fontsize=15, loc="left", pad=14)
    ax.legend(loc="lower left", frameon=False, fontsize=10, labelcolor=TEXT)
    fig.tight_layout()
    fig.savefig(out / "repeats.png", facecolor=SURFACE)
    plt.close(fig)
    return lines, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--beam40", type=Path, default=Path("results/b40_s5_full_27660.json"))
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()
    ref = load(args.reference)
    pb, gold = ref["race_time"], ref["medals"]["gold"]
    args.out.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in sorted(Path("results/compare").glob("b*_s5_cmp*.json")):
        tag = p.name.split("_")[2]
        width = load(p)["stats"]["config"]["beam"]
        run = {"cmpA": " (run 1)", "cmpB": " (run 2)"}.get(tag, "")
        rows.append(beam_row(p, f"Beam search, width {width}" + ("  (greedy)" if width == 1 else "") + run))
    if args.beam40.exists():
        rows.append(beam_row(args.beam40, "Beam search, width 40 (main run)"))
    rows.sort(key=lambda r: (r["param"], r["method"]))
    rows += sorted((astar_row(p) for p in Path("results/compare").glob("*astar*.json")),
                   key=lambda r: (r["heuristic"] != "distance / 1000 km/h", r["param"]))
    table = comparison(rows, pb, args.out)

    det = [r for r in rows if r["method"].startswith("Beam search, width 10 (run")]
    same = len(det) == 2 and det[0]["inputs"] == det[1]["inputs"] and det[0]["finish_ms"] == det[1]["finish_ms"]

    rep_lines, st = repeats(list(Path("results/repeats").glob("local_seed*_rep_*.json")), pb, gold, args.out)

    txt = ["OVERNIGHT EXPERIMENTS - B01-Race (one game instance)", "=" * 52, "",
           f"My PB: {pb / 1000:.2f} s   Gold: {gold / 1000:.2f} s", "",
           "1. SEARCH ALGORITHMS COMPARED", "-" * 30,
           "Same actions (8 key combinations held for 50 ms), same pruning, same game.",
           "A*/weighted A*: f = g + w*h, g = race time so far.",
           "  distance heuristic: remaining track length / 1000 km/h (admissible: never overestimates)",
           "  PB heuristic: time my PB still needed from that point (better informed, not admissible)",
           "Budget for A*: 180,000 game steps (what beam search width 40 needed to finish).", ""]
    txt += [line.replace("**", "") for line in table]
    if det:
        txt += ["", "Determinism: beam width 10 run twice -> " + ("identical inputs and finish time." if same else
                                                                 "DIFFERENT results (see results/compare).")]
    txt += ["", "2. REPEATED RUNS (local search from my PB)", "-" * 42]
    if st:
        txt += [f"{st['n']} seeds x {st['evals_each']:,} evaluations each.", ""]
        txt += [line.replace("**", "") for line in rep_lines]
        txt += ["", f"Result: {st['mean_s']:.3f} +- {st['std_s']:.3f} s (mean +- std), best {st['best_s']:.2f}, worst {st['worst_s']:.2f};"
                f" {st['beat_gold']}/{st['n']} seeds beat gold; on average {st['mean_gain_s']:.2f} s faster than my PB."]
        if "evals_to_gold_mean" in st:
            txt.append(f"Evaluations until it first beat gold: {st['evals_to_gold_mean']:.0f} +- {st['evals_to_gold_std']:.0f}.")
    txt += ["", "Files: results/comparison.md/.csv/.png, results/repeats.md/.png, results/compare/, results/repeats/"]
    (args.out / "experiments_summary.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")
    (args.out / "experiments_summary.json").write_text(json.dumps(
        {"comparison": [{k: v for k, v in r.items() if k not in ("inputs", "progress")} for r in rows],
         "beam10_deterministic": same if det else None, "repeats": st}, indent=1))
    print("\n".join(txt))


if __name__ == "__main__":
    main()
