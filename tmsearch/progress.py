"""Learning curve and tweak list across all search stages, for the report and the video.

Stages: (1) beam search from the PB, (2) local search from the beam result, (3) parallel local search
(several game instances sharing their best run). Writes results/learning_curve.png and
results/tweaks.csv / results/tweaks.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Trackmania 2020 palette (from trackmania.com), shared with the deck and the video.
SURFACE = "#071A2C"
TEXT = "#F1FFF6"
MUTED = "#8DA2B5"
GRID = "#1A3550"
SERIES = "#6EFAA0"     # single series: best time (TM mint)
REF = "#8DA2B5"
GOLD = "#E2BA12"


def stage_beam(path: Path, pb: int) -> tuple[list[tuple[float, int]], float]:
    res = json.loads(path.read_text())
    secs = res["stats"]["seconds"]
    return [(0.0, pb), (secs, res["finish_time"])], secs


def stage_local_from_log(json_path: Path, log_path: Path) -> tuple[list[tuple[float, int]], list[dict], float]:
    res = json.loads(json_path.read_text())
    budget = res["stats"]["budget_s"]
    start = int(json.loads(Path(res["stats"]["start"]).read_text())["finish_time"]) if res["stats"]["start"] != "PB" else None
    points = [(0.0, start)] if start else []
    tweaks = []
    prev = start
    pat = re.compile(r"\[\s*(\d+)s\] improvement at tick (\d+): finish (\d+) ms")
    for line in log_path.read_text().splitlines():
        m = pat.search(line)
        if not m:
            continue
        t, tick, fin = float(m.group(1)), int(m.group(2)), int(m.group(3))
        points.append((t, fin))
        if prev is not None and fin < prev:
            tweaks.append({"stage": "local search", "t_s": t, "race_time_s": tick / 100,
                           "change": f"input change at {tick / 100:.2f} s", "gain_ms": prev - fin, "finish_ms": fin})
        prev = fin
    return points, tweaks, budget


def stage_parallel(progress_files: list[Path]) -> tuple[list[tuple[float, int]], list[dict], float, int]:
    """Workers may have been restarted; place every worker on one wall-clock timeline."""
    runs = []
    for p in progress_files:
        d = json.loads(p.read_text())
        last_t = d["progress"][-1]["t_s"] if d["progress"] else 0.0
        start = d.get("started_at") or (p.stat().st_mtime - last_t)
        runs.append((start, last_t, d))
    t0 = min(r[0] for r in runs)
    events = []
    tweaks = []
    duration = 0.0
    for start, last_t, d in runs:
        off = start - t0
        duration = max(duration, off + last_t)
        if d["progress"]:
            events.append((off, d["progress"][0]["best_ms"]))
        for tw in d["tweaks"]:
            events.append((off + tw["t_s"], tw["finish_after_ms"]))
            if tw["gain_ms"] > 0:
                tweaks.append({"stage": f"parallel ({d['worker']})", "t_s": off + tw["t_s"], "race_time_s": tw["race_time_s"],
                               "change": tw["change"], "gain_ms": tw["gain_ms"], "finish_ms": tw["finish_after_ms"],
                               "kind": tw["kind"], "evals": tw["evals"]})
    n_games = len({d["worker"].split("-")[0][:2] for _, _, d in runs})
    events.sort()
    points = []
    best = None
    for t, f in events:
        if best is None or f < best:
            best = f
            points.append((t, f))
    return points, sorted(tweaks, key=lambda x: x["t_s"]), duration, n_games


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--beam", type=Path, default=Path("results/b40_s5_full_27660.json"))
    ap.add_argument("--local", type=Path, default=Path("results/local_seed218_27420.json"))
    ap.add_argument("--local-log", type=Path, default=Path("logs/local_from_27660.log"))
    ap.add_argument("--parallel", type=Path, nargs="*",
                    default=sorted(p for p in Path("results").glob("progress_w*_seed*.json") if not p.name.startswith("progress_wr")))
    ap.add_argument("--out", type=Path, default=Path("results"))
    args = ap.parse_args()

    ref = json.loads(args.reference.read_text())
    pb, medals = ref["race_time"], ref["medals"]

    beam_pts, beam_s = stage_beam(args.beam, pb)
    local_pts, local_tweaks, local_s = stage_local_from_log(args.local, args.local_log)
    par_pts, par_tweaks, par_s, n_workers = stage_parallel(args.parallel) if args.parallel else ([], [], 0.0, 0)

    # Cumulative wall-clock minutes; each stage starts where the previous one ended.
    curve: list[tuple[float, float]] = []
    offset = 0.0
    bounds = []
    for name, pts, dur in (("Beam search", beam_pts, beam_s), ("Local search", local_pts, local_s),
                           (f"Parallel local search ({n_workers} games)", par_pts, par_s)):
        if not pts:
            continue
        bounds.append((offset / 60, name))
        for t, f in pts:
            curve.append(((offset + t) / 60, f / 1000))
        offset += dur
        curve.append((offset / 60, pts[-1][1] / 1000))
    final = curve[-1][1]

    fig, ax = plt.subplots(figsize=(12, 6.2), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    xs = [c[0] for c in curve]
    ys = [c[1] for c in curve]
    ax.step(xs, ys, where="post", color=SERIES, lw=3, zorder=3)
    ax.plot([xs[-1]], [ys[-1]], "o", ms=9, color=SERIES, mec=SURFACE, mew=2, zorder=4)
    ax.annotate(f"{final:.2f} s", (xs[-1], ys[-1]), xytext=(8, -4), textcoords="offset points",
                color=TEXT, fontsize=12, fontweight="bold", va="top")
    lo_y = min(ys) - 0.06
    for value, label, va, col, ls in ((pb / 1000, "My PB", "bottom", REF, (0, (5, 4))),
                                      (medals["gold"] / 1000, "Gold", "top", GOLD, "-")):
        ax.axhline(value, color=col, lw=1.2, ls=ls, zorder=1)
        ax.text(xs[-1] * 1.035, value, f"{label} {value:.2f}", color=MUTED, fontsize=10, va=va, ha="right")
    for x, name in bounds:
        ax.axvline(x, color=GRID, lw=1, zorder=0)
        ax.text(x + 0.4, lo_y + 0.008, name, color=MUTED, fontsize=10, va="bottom")
    ax.set_xlim(0, xs[-1] * 1.04)
    ax.set_ylim(lo_y, max(ys) + 0.03)
    ax.set_xlabel("search time (minutes)", color=MUTED, fontsize=11)
    ax.set_ylabel("best finish time (s)", color=MUTED, fontsize=11)
    ax.set_title("How the search improved the run", color=TEXT, fontsize=15, loc="left", pad=14)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(axis="y", color=GRID, lw=1)
    for s in ax.spines.values():
        s.set_visible(False)
    fig.tight_layout()
    args.out.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out / "learning_curve.png", facecolor=SURFACE)

    tweaks = local_tweaks + par_tweaks
    with open(args.out / "tweaks.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stage", "t_s", "race_time_s", "change", "gain_ms", "finish_ms", "kind", "evals"])
        w.writeheader()
        for tw in tweaks:
            w.writerow({k: tw.get(k, "") for k in w.fieldnames})
    lines = ["| Stage | Found after | Where in the race | Change | Saved | New time |", "|---|---|---|---|---|---|"]
    for tw in tweaks:
        lines.append(f"| {tw['stage']} | {tw['t_s'] / 60:.1f} min | {tw['race_time_s']:.2f} s | {tw['change']} |"
                     f" {tw['gain_ms']} ms | {tw['finish_ms'] / 1000:.2f} s |")
    (args.out / "tweaks.md").write_text("\n".join(lines) + "\n")
    print(f"learning curve: {pb / 1000:.2f} -> {final:.2f} s over {offset / 60:.0f} min; {len(tweaks)} time-saving tweaks")


if __name__ == "__main__":
    main()
