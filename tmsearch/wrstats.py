"""Summary of the world-record experiment from the WR workers' progress files.

Prints JSON: when the record was first beaten (tries across all workers, minutes), the totals, and the
final time. Workers are aligned on wall-clock time using their recorded start times.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def evals_at(progress: list[dict], t: float) -> int:
    """Evaluations a worker had done by time t (linear between its 30 s samples)."""
    prev = {"t_s": 0.0, "evals": 0}
    for p in progress:
        if p["t_s"] >= t:
            span = max(p["t_s"] - prev["t_s"], 1e-9)
            return int(prev["evals"] + (p["evals"] - prev["evals"]) * (t - prev["t_s"]) / span)
        prev = p
    return prev["evals"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", type=Path, nargs="*", default=sorted(Path("results").glob("progress_wr*_seed*.json")))
    ap.add_argument("--record", type=int, default=25230)
    args = ap.parse_args()

    runs = [json.loads(p.read_text()) for p in args.files]
    t0 = min(r["started_at"] for r in runs)
    first = None
    for r in runs:
        off = r["started_at"] - t0
        for tw in r["tweaks"]:
            if tw["finish_after_ms"] < args.record and (first is None or off + tw["t_s"] < first[0]):
                first = (off + tw["t_s"], r["worker"], tw)
    out = {
        "record_ms": args.record,
        "best_ms": min(r["best_ms"] for r in runs),
        "evals_total": sum(r["evaluations"] for r in runs),
        "accepted_total": sum(r["accepted"] for r in runs),
        "minutes": max(r["started_at"] - t0 + (r["progress"][-1]["t_s"] if r["progress"] else 0) for r in runs) / 60,
        "workers": len(runs),
    }
    if first:
        t_first, worker, tw = first
        out.update({
            "first_beat_min": t_first / 60,
            "first_beat_worker": worker,
            "first_beat_change": tw["change"],
            "first_beat_ms": tw["finish_after_ms"],
            "first_beat_evals": sum(evals_at(r["progress"], t_first - (r["started_at"] - t0)) for r in runs),
        })
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
