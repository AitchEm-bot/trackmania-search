"""A* and weighted A* over game states, with the beam search's actions, step length and pruning.

    f(n) = g(n) + w * h(n)

  g  race time at the node (ms); for a node that crossed the line, its exact finish time.
  h  --heuristic distance (default): remaining length of the reference line / v_max (default
     1000 km/h, the game's speed cap; the fastest run on this map peaks at 449 km/h). The car cannot
     cover the rest of the track faster, so h never overestimates (admissible), and one step lowers
     it by at most that step's duration (consistent).
     --heuristic pb: the time my PB still needed from the node's progress along its line. Much better
     informed, but NOT admissible: the PB is not optimal, so it overestimates.
  w = 1 is A*; w > 1 is weighted A*: greedier and cheaper, but with an admissible h the run it
  returns can be up to w times slower than the best one.

The goal test is on pop: the search stops when it pops a finished node (h = 0, so f = finish time).
Duplicates: a node is dropped when another node in the same (position cell, heading bin, speed bin)
is there no later, at least as far along the track and at least as fast along it - it can do anything
this one can, sooner.
(Speed along the track, not total speed: at the start the car bounces, and braking rolls it backwards.)
Memory: every open node holds a saved game state inside the plugin (a 32-bit game), so the open list
is capped; on overflow the nodes with the worst f are dropped (memory-bounded A*, SMA*-style without
backed-up values). Unlike the beam search, the reference run is NOT injected as a path.
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import time
from pathlib import Path

from .bridge import Bridge
from .reference import Reference
from .search import ACTIONS, TICK_MS, BeamSearch, Config, SNode, to_tmi_script, verify


def dominates(a: SNode, b: SNode) -> bool:
    """a is in b's cell no later, at least as far along the track and at least as fast along it."""
    return a.t <= b.t and a.proj.s >= b.proj.s and a.v_along >= b.v_along


class AStar(BeamSearch):
    def __init__(self, bridge: Bridge, ref: Reference, cfg: Config, weight: float = 1.0,
                 v_max_kmh: float = 1000.0, max_open: int = 20000, budget: int = 100000,
                 heuristic: str = "distance"):
        super().__init__(bridge, ref, [], cfg)
        self.heuristic = heuristic
        self.weight = weight
        self.v_max = v_max_kmh / 3.6          # m/s
        self.max_open = max_open
        self.budget = budget                  # game expansions (plugin steps)
        self.pops = 0
        self.dropped = 0
        self.max_open_seen = 0
        self.open_live = 0
        self.progress: list[dict] = []
        # Track direction per reference segment; the zero-length ones (car standing at the start) take
        # the next real segment's direction, so speed along the track is defined from the first tick.
        d = ref.dir.copy()
        nxt = None
        for i in range(len(d) - 1, -1, -1):
            if ref.seglen[i] > 1e-3:
                nxt = d[i]
            elif nxt is not None:
                d[i] = nxt
        self.track_dir = d

    def make(self, node, parent, segments, is_ref=False) -> SNode:
        sn = super().make(node, parent, segments, is_ref)
        d = self.track_dir[sn.proj.idx]
        sn.v_along = node.vel[0] * d[0] + node.vel[1] * d[1] + node.vel[2] * d[2]
        return sn

    def h(self, sn: SNode) -> float:
        if self.heuristic == "pb":
            return max(self.ref.race_time - sn.proj.t_ref, 0.0)
        return max(self.ref.length - sn.proj.s, 0.0) / self.v_max * 1000.0

    def f(self, sn: SNode) -> float:
        if sn.node.finished:
            return float(sn.node.finish_time)
        return sn.t + self.weight * self.h(sn)

    def _push(self, heap, counter, sn: SNode) -> None:
        sn.status = "open"
        self.open_live += 1
        self.max_open_seen = max(self.max_open_seen, self.open_live)
        # ties: deeper (later race time) first
        heapq.heappush(heap, (self.f(sn), -sn.t, next(counter), sn))

    def _kill(self, sn: SNode, to_free: list[int]) -> None:
        if getattr(sn, "status", None) == "open":
            self.open_live -= 1
        sn.status = "dead"
        if sn.node.sid != self.root_sid and not sn.node.finished:
            to_free.append(sn.node.sid)

    def _trim(self, heap: list, to_free: list[int]) -> list:
        live = sorted(e for e in heap if e[3].status == "open")
        for e in live[self.max_open:]:
            self._kill(e[3], to_free)
            self.dropped += 1
        keep = live[: self.max_open]
        heapq.heapify(keep)
        return keep

    def run(self, log_every_s: float = 30.0):
        cfg = self.cfg
        root_node = self.bridge.wait_start(restart=True)
        self.root_sid = root_node.sid
        root = self.make(root_node, None, [])
        counter = itertools.count()
        heap: list = []
        self._push(heap, counter, root)
        # Per (cell, heading, speed bin): the nodes no other node beats on both race time and speed along the track.
        best: dict[tuple, list[SNode]] = {self.bucket(root): [root]}
        lead = root                       # furthest progress so far (for runs that do not finish)
        t0 = time.time()
        last_log = t0
        goal = None
        while heap and self.expansions < self.budget:
            _, _, _, sn = heapq.heappop(heap)
            if sn.status != "open":
                continue
            self.open_live -= 1
            if sn.node.finished:
                goal = sn
                break
            sn.status = "closed"
            self.pops += 1
            to_free: list[int] = []
            for name in cfg.actions:
                a = ACTIONS[name]
                node = self.bridge.expand(sn.node.sid, a, cfg.step_ticks)
                self.expansions += 1
                child = self.make(node, sn, [(cfg.step_ticks, a)])
                if node.finished:
                    to_free.append(node.sid)          # its inputs live in the parent chain
                    self._push(heap, counter, child)
                    continue
                reason = self.prune_reason(child)
                if reason:
                    self.pruned[reason] += 1
                    to_free.append(node.sid)
                    continue
                same = best.setdefault(self.bucket(child), [])
                if any(dominates(p, child) for p in same):
                    self.pruned["duplicate"] += 1
                    to_free.append(node.sid)
                    continue
                keep = []
                for p in same:
                    if dominates(child, p):
                        self.pruned["duplicate"] += 1
                        if p.status == "open":
                            self._kill(p, to_free)
                    else:
                        keep.append(p)
                same[:] = keep + [child]
                self._push(heap, counter, child)
                if child.proj.s > lead.proj.s:
                    lead = child
            if sn.node.sid != self.root_sid:
                to_free.append(sn.node.sid)       # its children carry their own states
            if self.open_live > self.max_open * 1.2:
                heap = self._trim(heap, to_free)
            self.bridge.free(to_free)

            now = time.time()
            if now - last_log > log_every_s:
                last_log = now
                el = now - t0
                top = heap[0][0] if heap else float("nan")
                self.progress.append({
                    "t_s": round(el, 1), "expansions": self.expansions, "pops": self.pops,
                    "open": self.open_live, "best_f_ms": round(top, 1), "max_progress_m": round(lead.proj.s, 1),
                    "popped_race_time_ms": sn.t,
                })
                print(f"[{el:6.0f}s] exp {self.expansions} ({self.expansions / el:5.1f}/s)  pops {self.pops}"
                      f"  open {self.open_live}  min f {top / 1000:6.2f}s  popped t={sn.t / 1000:5.2f}s"
                      f"  furthest {lead.proj.s:6.1f}/{self.ref.length:.0f} m"
                      f" ({lead.proj.t_ref - lead.t:+.0f} ms vs PB ghost there)", flush=True)

        # release every state still held by the search
        rest = [e[3].node.sid for e in heap if e[3].status == "open" and not e[3].node.finished
                and e[3].node.sid != self.root_sid]
        for i in range(0, len(rest), 2000):
            self.bridge.free(rest[i:i + 2000])
        stats = {
            "seconds": round(time.time() - t0, 1), "expansions": self.expansions, "pops": self.pops,
            "pruned": dict(self.pruned), "dropped_for_memory": self.dropped, "max_open": self.max_open_seen,
            "weight": self.weight, "heuristic": self.heuristic, "v_max_kmh": round(self.v_max * 3.6, 1), "open_cap": self.max_open,
            "budget_expansions": self.budget, "config": vars(self.cfg) | {"actions": list(self.cfg.actions)},
            "max_progress_m": round(lead.proj.s, 1), "track_length_m": round(self.ref.length, 1),
            "furthest_race_time_ms": lead.t, "furthest_vs_pb_ghost_ms": round(lead.proj.t_ref - lead.t, 1),
            "progress": self.progress,
        }
        if goal is None:
            return None, stats
        return goal, stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--weight", type=float, default=1.0)
    ap.add_argument("--budget", type=int, default=100000, help="game expansions before giving up")
    ap.add_argument("--max-open", type=int, default=20000)
    ap.add_argument("--vmax", type=float, default=1000.0, help="km/h used by the distance heuristic")
    ap.add_argument("--heuristic", choices=("distance", "pb"), default="distance")
    ap.add_argument("--step", type=int, default=Config.step_ticks)
    ap.add_argument("--port", type=int, default=8477)
    ap.add_argument("--out", type=Path, default=Path("results/compare"))
    args = ap.parse_args()

    ref = Reference(args.reference)
    cfg = Config(step_ticks=args.step)
    bridge = Bridge(port=args.port, timeout=180)
    bridge.set_speed(100)
    bridge.execute("set draw_game false")
    args.out.mkdir(parents=True, exist_ok=True)
    search = AStar(bridge, ref, cfg, args.weight, args.vmax, args.max_open, args.budget, args.heuristic)
    name = ("astar" if args.weight == 1 else f"wastar{args.weight:g}") + ("_pb" if args.heuristic == "pb" else "")
    hdesc = "PB time remaining" if args.heuristic == "pb" else f"remaining/{args.vmax:g} km/h"
    print(f"{name}: f = g + {args.weight:g} * {hdesc}, budget {args.budget} expansions,"
          f" open cap {args.max_open}", flush=True)
    try:
        goal, stats = search.run()
        record = {"method": name, "weight": args.weight, "heuristic": args.heuristic, "finish_time": None, "verified_finish_time": None,
                  "reference_time": ref.race_time, "medals": ref.medals, "inputs": [], "stats": stats}
        stem = f"{name}_dnf"
        if goal is not None:
            inputs = search.inputs_of(goal)
            record["finish_time"] = goal.node.finish_time
            record["inputs"] = [(n, a.label()) for n, a in inputs]
            stem = f"{name}_{goal.node.finish_time}"
            (args.out / f"{stem}.json").write_text(json.dumps(record, indent=1))
            (args.out / f"{stem}.txt").write_text(to_tmi_script(inputs, ref.offset_ms))
            record["verified_finish_time"] = verify(bridge, inputs, search.root_sid)
        (args.out / f"{stem}.json").write_text(json.dumps(record, indent=1))
    finally:
        bridge.execute("set draw_game true")
        bridge.set_speed(1)
        bridge.close()
    s = record["stats"]
    if record["finish_time"] is None:
        print(f"\n{name}: no finish within {s['expansions']} expansions ({s['seconds']:.0f} s); furthest"
              f" {s['max_progress_m']:.0f}/{s['track_length_m']:.0f} m")
    else:
        print(f"\n{name}: finish {record['finish_time'] / 1000:.2f} s (verified {record['verified_finish_time'] / 1000:.2f} s)"
              f" after {s['expansions']} expansions, {s['seconds']:.0f} s")
    print(f"saved {args.out / stem}.json")


if __name__ == "__main__":
    main()
