"""Time-layered beam search over game states, run inside the real game physics.

Every search step holds one action for STEP ticks, so all nodes in a layer share the same race time.
Within a layer, nodes are ranked by how far ahead of the reference (PB) ghost they are, projected a
short time ahead using their speed along the track. The reference run itself is replayed alongside
the beam, so the search always contains a path at least as good as the PB and can branch off it at
any point.

Pruning (all relative to the reference trajectory):
  * off-line:   farther than max_offset metres from the reference line
  * pace:       more than max_behind_ms behind the reference ghost
  * checkpoint: passed a checkpoint's position on the reference without registering it
Near-duplicate states (same position cell, heading bin and speed bin) are merged, keeping the best.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from .bridge import Action, Bridge, Node
from .reference import Projection, Reference
from .replay import read_replay
from .reproduce import input_timeline

TICK_MS = 10

ACTIONS = {
    "G": Action(up=True),
    "GL": Action(up=True, left=True),
    "GR": Action(up=True, right=True),
    "-": Action(),
    "L": Action(left=True),
    "R": Action(right=True),
    "BL": Action(down=True, left=True),
    "BR": Action(down=True, right=True),
}


@dataclass
class Config:
    beam: int = 40
    step_ticks: int = 5
    actions: tuple[str, ...] = tuple(ACTIONS)
    lookahead_s: float = 0.3
    max_offset: float = 12.0
    max_behind_ms: float = 600.0
    cp_margin: float = 12.0
    cell_m: float = 1.0
    heading_bin_deg: float = 6.0
    speed_bin: float = 1.5
    wall_penalty_ms: float = 15.0
    seed: int = 218


@dataclass(eq=False)
class SNode:
    node: Node
    parent: "SNode | None"
    segments: list[tuple[int, Action]]   # (ticks, action) applied to get here from the parent
    proj: Projection
    score: float
    is_ref: bool = False
    walls: int = 0

    @property
    def t(self) -> int:
        return self.node.race_time


@dataclass
class Result:
    finish_time: int
    inputs: list[tuple[int, Action]]      # (ticks, action) from race start
    stats: dict = field(default_factory=dict)


class BeamSearch:
    def __init__(self, bridge: Bridge, ref: Reference, ref_timeline: list[Action], cfg: Config):
        self.bridge = bridge
        self.ref = ref
        self.ref_timeline = ref_timeline
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.expansions = 0
        self.pruned = {"offset": 0, "pace": 0, "checkpoint": 0, "duplicate": 0, "beam": 0}

    # -- evaluation --------------------------------------------------------------------------
    def evaluate(self, node: Node, hint: int) -> tuple[Projection, float]:
        proj = self.ref.project(node.pos, node.vel, hint)
        look = self.cfg.lookahead_s
        s_look = proj.s + max(proj.along, 0.0) * look
        t_ref_look = float(_interp(s_look, self.ref.s, self.ref.t))
        score = t_ref_look - (node.race_time + look * 1000.0)   # ms ahead of the ghost (projected)
        return proj, score

    def prune_reason(self, sn: SNode) -> str | None:
        if sn.proj.offset > self.cfg.max_offset:
            return "offset"
        if sn.proj.t_ref - sn.t < -self.cfg.max_behind_ms:
            return "pace"
        for k, s_cp in enumerate(self.ref.cp_s[:-1], start=1):   # last entry is the finish
            if sn.proj.s > s_cp + self.cfg.cp_margin and sn.node.cp_count < k:
                return "checkpoint"
        return None

    def bucket(self, sn: SNode) -> tuple:
        x, y, z = sn.node.pos
        c = self.cfg.cell_m
        return (
            round(x / c), round(y / c), round(z / c),
            round(math.degrees(sn.node.yaw) / self.cfg.heading_bin_deg),
            round(sn.node.speed / self.cfg.speed_bin),
        )

    def make(self, node: Node, parent: SNode | None, segments, is_ref=False) -> SNode:
        hint = parent.proj.idx if parent else 0
        proj, score = self.evaluate(node, hint)
        walls = (parent.walls if parent else 0) + int(node.wall)
        score -= self.cfg.wall_penalty_ms * int(node.wall)
        return SNode(node, parent, segments, proj, score, is_ref, walls)

    # -- search ------------------------------------------------------------------------------
    def expand_ref(self, parent: SNode, layer_index: int) -> SNode:
        """Advance the reference run by one layer using its own per-tick inputs."""
        start = layer_index * self.cfg.step_ticks
        ticks = [self.ref_timeline[min(start + i, len(self.ref_timeline) - 1)] for i in range(self.cfg.step_ticks)]
        segments: list[tuple[int, Action]] = []
        for a in ticks:
            if segments and segments[-1][1] == a:
                segments[-1] = (segments[-1][0] + 1, a)
            else:
                segments.append((1, a))
        sid = parent.node.sid
        node = None
        intermediate = []
        for n, a in segments:
            node = self.bridge.expand(sid, a, n)
            self.expansions += 1
            if node.finished:
                break
            if sid != parent.node.sid:
                intermediate.append(sid)
            sid = node.sid
        self.bridge.free(intermediate)
        return self.make(node, parent, segments, is_ref=True)

    def run(self, max_layers: int | None = None, log_every: int = 20) -> Result | None:
        cfg = self.cfg
        root_node = self.bridge.wait_start(restart=True)
        self.root_sid = root_node.sid   # kept alive so results can be verified without restarting the race
        root = self.make(root_node, None, [], is_ref=True)
        layer = [root]
        max_layers = max_layers or (self.ref.race_time // (cfg.step_ticks * TICK_MS) + 40)
        t0 = time.time()
        best_ahead_log = []

        for li in range(max_layers):
            children: list[SNode] = []
            finished: list[SNode] = []
            to_free: list[int] = []
            for parent in layer:
                if parent.is_ref:
                    ref_child = self.expand_ref(parent, li)
                    (finished if ref_child.node.finished else children).append(ref_child)
                for name in cfg.actions:
                    a = ACTIONS[name]
                    node = self.bridge.expand(parent.node.sid, a, cfg.step_ticks)
                    self.expansions += 1
                    sn = self.make(node, parent, [(cfg.step_ticks, a)])
                    if node.finished:
                        finished.append(sn)
                        continue
                    reason = self.prune_reason(sn)
                    if reason:
                        self.pruned[reason] += 1
                        to_free.append(node.sid)
                    else:
                        children.append(sn)
                if parent.node.sid != self.root_sid:
                    to_free.append(parent.node.sid)

            if finished:
                best = min(finished, key=lambda s: (s.node.finish_time, not s.is_ref))
                self.bridge.free(to_free + [c.node.sid for c in children] + [f.node.sid for f in finished])
                return Result(best.node.finish_time, self.inputs_of(best), self.stats(t0, li + 1, best_ahead_log))

            # Selection: best score first, merge near-duplicates, keep the reference path.
            children.sort(key=lambda s: s.score, reverse=True)
            seen: set = set()
            keep: list[SNode] = []
            for sn in children:
                if sn.is_ref:
                    keep.append(sn)
                    continue
                key = self.bucket(sn)
                if key in seen or len(keep) >= cfg.beam + 1:
                    self.pruned["duplicate" if key in seen else "beam"] += 1
                    to_free.append(sn.node.sid)
                    continue
                seen.add(key)
                keep.append(sn)
            self.bridge.free(to_free)
            layer = keep
            if not layer:
                return None

            lead = max(layer, key=lambda s: s.score)
            ref_now = next((s for s in layer if s.is_ref), None)
            max_ahead = max(s.proj.t_ref - s.t for s in layer)
            best_ahead_log.append((lead.t, lead.proj.t_ref - lead.t, max_ahead))
            if li % log_every == 0:
                el = time.time() - t0
                print(
                    f"t={lead.t / 1000:6.2f}s  beam={len(layer):3d}  top-ranked ahead of PB by {lead.proj.t_ref - lead.t:+7.1f} ms"
                    f" (most ahead {max_ahead:+7.1f} ms)"
                    f"  (s={lead.proj.s:6.1f} m, {lead.node.display_speed} km/h, cp {lead.node.cp_count})"
                    f"  ref in beam: {'yes' if ref_now else 'no'}  {self.expansions / max(el, 1e-9):5.1f} exp/s",
                    flush=True,
                )
        return None

    def inputs_of(self, sn: SNode) -> list[tuple[int, Action]]:
        chain = []
        while sn is not None and sn.parent is not None:
            chain.append(sn.segments)
            sn = sn.parent
        out: list[tuple[int, Action]] = []
        for segs in reversed(chain):
            for n, a in segs:
                if out and out[-1][1] == a:
                    out[-1] = (out[-1][0] + n, a)
                else:
                    out.append((n, a))
        return out

    def stats(self, t0: float, layers: int, ahead_log) -> dict:
        return {
            "seconds": round(time.time() - t0, 1),
            "layers": layers,
            "expansions": self.expansions,
            "pruned": dict(self.pruned),
            "config": vars(self.cfg) | {"actions": list(self.cfg.actions)},
            "lead_ahead_ms": ahead_log,
        }


def _interp(x, xs, ys):
    import numpy as np
    return np.interp(x, xs, ys)


# -- input export ---------------------------------------------------------------------------------
def to_tmi_script(inputs: list[tuple[int, Action]], offset_ms: int) -> str:
    """TMInterface script. An action applied from race time T corresponds to an input event at T + offset."""
    lines = []
    held = {"up": False, "down": False, "left": False, "right": False}
    t = 0
    for n, a in inputs:
        want = {"up": a.up, "down": a.down, "left": a.left, "right": a.right}
        for key in ("up", "down", "left", "right"):
            if want[key] != held[key]:
                lines.append(f"{(t + offset_ms) / 1000:.2f} {'press' if want[key] else 'rel'} {key}")
                held[key] = want[key]
        t += n * TICK_MS
    return "\n".join(lines) + "\n"


def verify(bridge: Bridge, inputs: list[tuple[int, Action]], root_sid: int | None = None) -> int:
    """Replay an input sequence from the race start and return the finish time (-1 if none).

    With root_sid (a kept race-start state) the race is not restarted."""
    sid = root_sid if root_sid is not None else bridge.wait_start(restart=True).sid
    used = []
    finish = -1
    for n, a in inputs:
        node = bridge.expand(sid, a, n)
        if sid != root_sid:
            used.append(sid)
        sid = node.sid
        if node.finished:
            finish = node.finish_time
            break
    bridge.free(used + [sid])
    return finish


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--replay", type=Path, default=Path("data/replays/MIS_B01-Race.Replay.gbx"))
    ap.add_argument("--beam", type=int, default=Config.beam)
    ap.add_argument("--step", type=int, default=Config.step_ticks, help="ticks (10 ms) per search step")
    ap.add_argument("--max-offset", type=float, default=Config.max_offset)
    ap.add_argument("--max-behind", type=float, default=Config.max_behind_ms)
    ap.add_argument("--lookahead", type=float, default=Config.lookahead_s)
    ap.add_argument("--actions", default=",".join(ACTIONS))
    ap.add_argument("--speed", type=float, default=100.0, help="game speed while searching")
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    ref = Reference(args.reference)
    info = read_replay(args.replay)
    timeline = input_timeline(info.events, info.race_time // TICK_MS + 50, ref.offset_ms)
    cfg = Config(
        beam=args.beam, step_ticks=args.step, max_offset=args.max_offset, max_behind_ms=args.max_behind,
        lookahead_s=args.lookahead, actions=tuple(args.actions.split(",")),
    )
    medals = ref.medals or {}
    print(f"reference {ref.race_time} ms, gold {medals.get('gold')} ms, author {medals.get('author')} ms")
    print(f"config: {cfg}")

    bridge = Bridge(timeout=180)
    bridge.set_speed(args.speed)
    bridge.execute("set draw_game false")   # rendering is the main cost per step
    args.out.mkdir(parents=True, exist_ok=True)
    search = BeamSearch(bridge, ref, timeline, cfg)
    result = None
    check = -1
    try:
        result = search.run()
        if result is not None:
            # Save before verifying, so a problem during verification can't lose the result.
            stem = f"b{cfg.beam}_s{cfg.step_ticks}" + (f"_{args.tag}" if args.tag else "") + f"_{result.finish_time}"
            record = {
                "finish_time": result.finish_time,
                "verified_finish_time": None,
                "reference_time": ref.race_time,
                "medals": medals,
                "inputs": [(n, a.label()) for n, a in result.inputs],
                "stats": result.stats,
            }
            (args.out / f"{stem}.json").write_text(json.dumps(record, indent=1))
            (args.out / f"{stem}.txt").write_text(to_tmi_script(result.inputs, ref.offset_ms))
            check = verify(bridge, result.inputs, search.root_sid)
            record["verified_finish_time"] = check
            (args.out / f"{stem}.json").write_text(json.dumps(record, indent=1))
    finally:
        bridge.execute("set draw_game true")
        bridge.set_speed(1)
        bridge.close()
    if result is None:
        raise SystemExit("search ended without finishing")

    gold = medals.get("gold")
    print(f"\nfinish {result.finish_time / 1000:.2f} s (verified replay: {check / 1000:.2f} s)"
          f"  PB {ref.race_time / 1000:.2f} s  gold {gold / 1000 if gold else float('nan'):.2f} s")
    print(f"saved {args.out / stem}.json and .txt (TMInterface script)")


if __name__ == "__main__":
    main()
