"""Play a search result in the game at normal speed with rendering on (for watching / demos).

The finish is prevented by the plugin, so the run is never registered as a record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyze import LABELS
from .bridge import Bridge
from .search import verify


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("result", type=Path)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--cam", type=int, default=2, help="1 close chase, 2 chase, 3 bumper")
    args = ap.parse_args()

    res = json.loads(args.result.read_text())
    inputs = [(n, LABELS[label]) for n, label in res["inputs"]]
    bridge = Bridge(timeout=120)
    try:
        bridge.execute("set draw_game true")
        bridge.set_speed(args.speed)
        root = bridge.wait_start(restart=True)
        bridge.execute(f"cam {args.cam}")
        finish = verify(bridge, inputs, root.sid)
        print(f"finished in {finish / 1000:.2f} s")
    finally:
        bridge.set_speed(1)
        bridge.close()


if __name__ == "__main__":
    main()
