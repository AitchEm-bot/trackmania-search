"""Record a run as video: the run is stepped through the plugin exactly like the search simulates it
(so it reproduces the verified finish time), and the plugin streams one rendered frame per physics
tick (10 ms). Output: a 100 fps MP4 plus a JSON sidecar with the race time and speed of every frame.

Note: playing the inputs as a TMInterface script does NOT reproduce the searched runs reliably, so the
recording drives the car through the plugin instead. The plugin still prevents the finish from being
registered as a record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from .analyze import LABELS
from .bridge import Action, Bridge
from .replay import read_replay
from .reproduce import input_timeline
from .search import TICK_MS


def inputs_for(source: Path, offset_ms: int = 10) -> list[tuple[int, Action]]:
    """(ticks, action) segments for a search result (.json) or a replay (.gbx)."""
    if source.suffix.lower() == ".json":
        res = json.loads(source.read_text())
        return [(n, LABELS[label]) for n, label in res["inputs"]]
    info = read_replay(source)
    out: list[tuple[int, Action]] = []
    for a in input_timeline(info.events, info.race_time // TICK_MS + 50, offset_ms):
        if out and out[-1][1] == a:
            out[-1] = (out[-1][0] + 1, a)
        else:
            out.append((1, a))
    return out


def record_run(bridge: Bridge, source: Path, cam: int, out: Path, speed: float = 0.2,
               tail_ms: int = 1500, size: tuple[int, int] = (1920, 1080)) -> dict:
    inputs = inputs_for(source)
    root = bridge.wait_start(restart=True)
    bridge.execute("set draw_game true")
    bridge.execute(f"cam {cam}")
    bridge.set_speed(speed)

    out.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(out), size, pix_fmt_in="bgra", fps=1000 // TICK_MS, codec="libx264",
        output_params=["-crf", "14", "-preset", "veryfast"], macro_block_size=1,
    )
    writer.send(None)
    rec = {"times": [], "speeds": [], "filled": 0, "last_t": None, "last_frame": None}

    def sink(t: int, spd: int, frame: np.ndarray) -> None:
        if rec["last_t"] is not None and t <= rec["last_t"]:
            return
        # Fill any skipped ticks with the previous frame so the video stays at exactly 100 fps.
        if rec["last_t"] is not None:
            for missing in range(rec["last_t"] + TICK_MS, t, TICK_MS):
                rec["filled"] += 1
                writer.send(rec["last_frame"])
                rec["times"].append(missing)
                rec["speeds"].append(rec["speeds"][-1])
        rec["last_frame"] = np.ascontiguousarray(frame)
        writer.send(rec["last_frame"])
        rec["times"].append(t)
        rec["speeds"].append(spd)
        rec["last_t"] = t

    finish = -1
    used: list[int] = []
    sid = root.sid
    try:
        bridge.frame_sink = sink
        bridge.record(True, *size)
        for n, a in inputs:
            node = bridge.expand(sid, a, n)
            used.append(sid)
            sid = node.sid
            if node.finished:
                finish = node.finish_time
                break
        # Keep driving a little past the line (the plugin prevents the finish from counting).
        node = bridge.expand(sid, Action(up=True), tail_ms // TICK_MS)
        used.append(sid)
        sid = node.sid
    finally:
        bridge.frame_sink = None
        bridge.record(False)
        writer.close()
        bridge.set_speed(1)
        bridge.free(used + [sid])

    meta = {"source": str(source), "cam": cam, "finish_time": finish, "fps": 1000 // TICK_MS,
            "times": rec["times"], "speeds": rec["speeds"], "size": list(size), "filled_ticks": rec["filled"]}
    out.with_suffix(".json").write_text(json.dumps(meta))
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="search result .json or replay .gbx")
    ap.add_argument("--cams", type=int, nargs="+", default=[2, 1, 3])
    ap.add_argument("--speed", type=float, default=0.2, help="game speed while recording (lower = no skipped ticks)")
    ap.add_argument("--size", type=int, nargs=2, default=[1920, 1080], help="capture size (width height)")
    ap.add_argument("--out", type=Path, default=Path("video/raw"))
    ap.add_argument("--label", default=None)
    ap.add_argument("--port", type=int, default=8477)
    args = ap.parse_args()

    label = args.label or args.source.stem
    bridge = Bridge(port=args.port, timeout=180)
    try:
        for cam in args.cams:
            out = args.out / f"{label}_cam{cam}.mp4"
            meta = record_run(bridge, args.source, cam, out, speed=args.speed, size=tuple(args.size))
            print(f"cam {cam}: {len(meta['times'])} frames ({meta['filled_ticks']} repeated), "
                  f"finish {meta['finish_time']} ms -> {out}", flush=True)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
