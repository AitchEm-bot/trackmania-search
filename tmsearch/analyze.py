"""Re-run a search result in the game, record its trajectory and plot it against the reference (PB).

Produces a top-down map of both lines and the time gained/lost along the track.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .bridge import Action, Bridge  # noqa: E402
from .reference import Reference  # noqa: E402

class _Labels(dict):
    """Action labels (see Action.label) -> Action; any label not cached is parsed (e.g. analog 'GS+12000')."""

    def __missing__(self, label: str) -> Action:
        action = Action.from_label(label)
        self[label] = action
        return action


LABELS = _Labels()


def record(bridge: Bridge, inputs: list[tuple[int, str]]) -> np.ndarray:
    root = bridge.wait_start(restart=True)
    sid = root.sid
    rows = [(root.race_time, *root.pos, root.display_speed)]
    used = []
    for n, label in inputs:
        for _ in range(n):
            node = bridge.expand(sid, LABELS[label], 1)
            used.append(sid)
            sid = node.sid
            rows.append((node.race_time, *node.pos, node.display_speed))
            if node.finished:
                break
        if len(used) > 256:
            bridge.free(used)
            used = []
        if node.finished:
            break
    bridge.free(used + [sid])
    return np.asarray(rows)


def time_gain(ref: Reference, traj: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each point of the trajectory: progress along the reference and ms ahead of the PB ghost."""
    s_list, gain = [], []
    hint = 0
    for i in range(1, len(traj)):
        proj = ref.project(traj[i, 1:4], (0.0, 0.0, 0.0), hint)
        hint = proj.idx
        s_list.append(proj.s)
        gain.append(proj.t_ref - traj[i, 0])
    return np.asarray(s_list), np.asarray(gain)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("result", type=Path)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--port", type=int, default=8477)
    args = ap.parse_args()

    res = json.loads(args.result.read_text())
    ref = Reference(args.reference)
    bridge = Bridge(port=args.port, timeout=120)
    bridge.set_speed(100)
    try:
        traj = record(bridge, [(n, label) for n, label in res["inputs"]])
    finally:
        bridge.set_speed(1)
        bridge.close()
    out = args.out or args.result.with_suffix(".png")
    np.savetxt(out.with_suffix(".trajectory.csv"), traj, delimiter=",", header="t_ms,x,y,z,speed_kmh", comments="")

    s, gain = time_gain(ref, traj)
    medals = res.get("medals") or {}
    bg, text, muted, mint, grid = "#071A2C", "#F1FFF6", "#8DA2B5", "#6EFAA0", "#1A3550"
    plt.rcParams.update({"axes.facecolor": bg, "figure.facecolor": bg, "text.color": text, "axes.labelcolor": muted,
                         "xtick.color": muted, "ytick.color": muted, "axes.edgecolor": grid})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    ax1.plot(ref.p[:, 0], ref.p[:, 2], color=muted, lw=2, ls=(0, (5, 4)), label=f"my PB {ref.race_time / 1000:.2f} s")
    ax1.plot(traj[:, 1], traj[:, 3], color=mint, lw=2, label=f"AI {res['finish_time'] / 1000:.2f} s")
    ax1.set_aspect("equal")
    ax1.set_xlabel("x (m)")
    ax1.set_ylabel("z (m)")
    ax1.set_title("Top-down view of the two lines")
    ax1.legend()
    ax2.plot(s, gain, color=mint, lw=2)
    ax2.axhline(0, color=muted, lw=1, ls=(0, (5, 4)))
    for k, s_cp in enumerate(ref.cp_s[:-1], start=1):
        ax2.axvline(s_cp, color=grid, lw=1)
        ax2.text(s_cp, ax2.get_ylim()[1] if gain.size else 0, f" CP{k}", va="top", fontsize=9, color=muted)
    ax2.set_xlabel("progress along the PB line (m)")
    ax2.set_ylabel("time ahead of PB ghost (ms)")
    title = f"Time gained along the track (gold {medals.get('gold', 0) / 1000:.2f} s)" if medals else "Time gained along the track"
    ax2.set_title(title)
    fig.tight_layout()
    for ax in (ax1, ax2):
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.grid(color=grid, lw=1)
    ax1.legend(facecolor=bg, edgecolor=grid, labelcolor=text)
    fig.savefig(out, dpi=130, facecolor=bg)
    print(f"wrote {out} and {out.with_suffix('.trajectory.csv')}")


if __name__ == "__main__":
    main()
