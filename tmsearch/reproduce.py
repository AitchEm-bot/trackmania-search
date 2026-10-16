"""Re-drive a recorded replay through the plugin, tick by tick, and record full-rate telemetry.

This checks that the plugin's stepping matches the game's own timing (checkpoint and finish times
must match the replay) and produces the reference trajectory used by the search.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bridge import Action, Bridge
from .replay import read_replay

TICK_MS = 10


def input_timeline(events: list[tuple[int, str, bool | int]], n_ticks: int, offset_ms: int) -> list[Action]:
    """Input held during each tick. An event at time t takes effect for the tick starting at t - offset_ms.

    Key events are (t, key, pressed); analog steering events are (t, "steer", value)."""
    held: dict = {"up": False, "down": False, "left": False, "right": False, "steer": 0}
    timeline = []
    i = 0
    ordered = sorted(events, key=lambda e: e[0])
    for tick in range(n_ticks):
        t = tick * TICK_MS + offset_ms
        while i < len(ordered) and ordered[i][0] <= t:
            _, key, value = ordered[i]
            held[key] = int(value) if key == "steer" else bool(value)
            i += 1
        timeline.append(Action(**held))
    return timeline


def reproduce(bridge: Bridge, timeline: list[Action], max_ticks: int) -> dict:
    root = bridge.wait_start(restart=True)
    cur = root
    trace = [(cur.race_time, *cur.pos, *cur.vel, cur.cp_count)]
    cp_times: list[int] = []
    last_cp = 0
    finish = -1
    to_free = []
    for tick in range(max_ticks):
        nxt = bridge.expand(cur.sid, timeline[min(tick, len(timeline) - 1)], 1)
        to_free.append(cur.sid)
        if len(to_free) >= 256:
            bridge.free(to_free)
            to_free = []
        cur = nxt
        trace.append((cur.race_time, *cur.pos, *cur.vel, cur.cp_count))
        if cur.cp_count > last_cp:
            cp_times.append(cur.race_time)
            last_cp = cur.cp_count
        if cur.finished:
            finish = cur.finish_time
            break
    bridge.free(to_free + [cur.sid])
    return {"finish": finish, "cp_times": cp_times, "trace": trace}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("replay", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--offset", type=int, nargs="*", default=[0, -10, 10])
    args = ap.parse_args()

    info = read_replay(args.replay)
    print(f"replay: {info.race_time} ms, checkpoints {info.cp_times}, {len(info.events)} key events")
    n_ticks = info.race_time // TICK_MS + 50

    bridge = Bridge(timeout=120)
    bridge.set_speed(100)
    best = None
    for offset in args.offset:
        result = reproduce(bridge, input_timeline(info.events, n_ticks, offset), n_ticks)
        print(f"offset {offset:+d} ms -> finish {result['finish']} ms, checkpoint times {result['cp_times']}")
        if result["finish"] == info.race_time:
            best = (offset, result)
            break
    bridge.set_speed(1)
    bridge.close()

    if best is None:
        raise SystemExit("could not reproduce the replay exactly")
    offset, result = best
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "source": str(args.replay),
        "offset_ms": offset,
        "race_time": info.race_time,
        "cp_times": info.cp_times,
        "medals": vars(info.medals) if info.medals else None,
        "trace_fields": ["t", "x", "y", "z", "vx", "vy", "vz", "cp"],
        "trace": result["trace"],
    }))
    print(f"reproduced exactly with offset {offset:+d} ms; wrote {args.out} ({len(result['trace'])} ticks)")


if __name__ == "__main__":
    main()
