"""Stochastic local search (hill climbing) over a full input sequence.

The run is a per-tick list of keyboard states. A move perturbs it locally:
  * shift:        move one press/release edge of one key by a few ticks
  * tap:          toggle one key for a few ticks somewhere
  * lift:         release the throttle for 50-400 ms (lifting as a natural brake)
  * brake->lift:  replace a brake press with coasting
The candidate is simulated in the game from a cached state just before the first changed tick up to
the finish, and accepted only if it finishes earlier, or at the same time but further along the
track one tick before that finish (a finer-grained tie-breaker than the 10 ms finish time).

Time warp (--warp): a change early in the run shifts where the car is when the later inputs happen,
so those inputs no longer fit and a genuinely faster corner can still end in a slower finish. With
warp, the candidate is measured a little after the change; if it is d ticks ahead of (behind) the
current best there, the rest of the best run's inputs are replayed d ticks earlier (later), so they
line up with the track again.

Cached states of the current best run are stored in the plugin every CACHE_TICKS ticks, so a
candidate never has to be simulated from the race start.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

from .bridge import Action, Bridge
from .reference import Reference
from .replay import read_replay
from .reproduce import input_timeline
from .search import TICK_MS, to_tmi_script, verify

KEYS = ("up", "down", "left", "right")
KEY_NAMES = {"up": "throttle", "down": "brake", "left": "steer-left", "right": "steer-right"}
CHECK_TICKS = 50          # compare a candidate against the current best every 0.5 s
ABORT_BEHIND_MS = 150.0   # ...and drop it once it is this far behind
WARP_AFTER_TICKS = 60     # where to measure the lead for time warp, after the last changed tick
MAX_WARP_TICKS = 30


def segments(timeline: list[Action], start: int, stops: set[int]) -> list[tuple[int, int, Action]]:
    """Split timeline[start:] into (tick, length, action) runs, also cutting at every tick in `stops`."""
    out = []
    i = start
    n = len(timeline)
    while i < n:
        j = i + 1
        while j < n and timeline[j] == timeline[i] and j not in stops:
            j += 1
        out.append((i, j - i, timeline[i]))
        i = j
    return out


def with_key(a: Action, key: str, value: bool) -> Action:
    d = {"up": a.up, "down": a.down, "left": a.left, "right": a.right, "steer": a.steer}
    d[key] = value
    if key == "left" and value:
        d["right"] = False
    if key == "right" and value:
        d["left"] = False
    return Action(**d)


class LocalSearch:
    def __init__(self, bridge: Bridge, ref: Reference, timeline: list[Action], cache_ticks: int = 10,
                 seed: int = 218, window: tuple[int, int] | None = None, warp: bool = False):
        self.bridge = bridge
        self.ref = ref
        self.best = list(timeline)
        self.analog = any(a.steer for a in timeline)
        self.cache_ticks = cache_ticks
        self.rng = random.Random(seed)
        self.window = window                  # (from_tick, to_tick) where moves may start
        self.warp = warp
        self.cache: dict[int, int] = {}       # tick -> plugin state id (states of the current best run)
        self.cache_hint: dict[int, int] = {}  # tick -> reference index (projection hint)
        self.best_finish = -1
        self.best_tiebreak = -1.0
        self.best_ahead: dict[int, float] = {}   # tick -> ms ahead of the reference ghost (current best run)
        self.aborted = 0
        self.evals = 0
        self.accepted = 0
        self.warped = 0
        self.moves: dict[str, list[int]] = {}    # move kind -> [tried, accepted]
        self.history: list[tuple[float, int]] = []
        self.tweaks: list[dict] = []       # every accepted change, with a human-readable description
        self.progress: list[dict] = []     # best time over search time (learning curve)
        self.last_desc = ""
        self.last_warp = 0
        self.on_update = None              # called after each improvement / progress sample (for saving)
        # Parallel workers (one game instance each) share their best run through a JSON file.
        self.shared_path: Path | None = None
        self.sync_every_s = 20.0
        self.worker = ""
        self.adoptions: list[dict] = []
        self._rejected: set[str] = set()
        self.max_evals: int | None = None   # stop after this many evaluations (equal work across repeats)

    # -- sharing between parallel workers ----------------------------------------------------
    def _encode(self, timeline: list[Action]) -> list[list]:
        out: list[list] = []
        for a in timeline[: self.best_finish // TICK_MS + 1]:
            if out and out[-1][1] == a.label():
                out[-1][0] += 1
            else:
                out.append([1, a.label()])
        return out

    def _read_shared(self) -> dict | None:
        try:
            return json.loads(self.shared_path.read_text())
        except (OSError, ValueError):
            return None

    def publish(self) -> None:
        if self.shared_path is None:
            return
        cur = self._read_shared()
        if cur and (cur["finish_ms"] < self.best_finish or
                    (cur["finish_ms"] == self.best_finish and cur["tiebreak"] >= self.best_tiebreak)):
            return
        tmp = self.shared_path.with_suffix(f".{self.worker or 'w'}.tmp")
        tmp.write_text(json.dumps({"finish_ms": self.best_finish, "tiebreak": self.best_tiebreak,
                                   "worker": self.worker, "inputs": self._encode(self.best)}))
        os.replace(tmp, self.shared_path)

    def maybe_adopt(self, elapsed: float) -> None:
        if self.shared_path is None:
            return
        cur = self._read_shared()
        if not cur or cur.get("worker") == self.worker:
            return
        # Compare the runs themselves, not just the tie-break number (it differs slightly between instances).
        if cur["inputs"] == self._encode(self.best):
            return
        sig = json.dumps(cur["inputs"])
        if sig in self._rejected:
            return
        if cur["finish_ms"] < self.best_finish or (cur["finish_ms"] == self.best_finish and cur["tiebreak"] > self.best_tiebreak + 1e-3):
            from .analyze import LABELS
            timeline = [LABELS[label] for n, label in cur["inputs"] for _ in range(n)]
            timeline += [timeline[-1]] * 100
            before, before_tl = self.best_finish, self.best
            self.accept(timeline, 0)
            if self.best_finish < 0 or self.best_finish > before:
                # Did not reproduce here: keep our own best and never try this one again.
                print(f"  [{elapsed:6.0f}s] shared best from {cur['worker']} gave {self.best_finish} ms here; keeping ours",
                      flush=True)
                self._rejected.add(sig)
                self.accept(before_tl, 0)
                return
            self.adoptions.append({"t_s": round(elapsed, 1), "from": cur["worker"], "finish_before_ms": before,
                                   "finish_after_ms": self.best_finish})
            print(f"  [{elapsed:6.0f}s] adopted the shared best from {cur['worker']}: finish {self.best_finish} ms",
                  flush=True)

    # -- simulation ------------------------------------------------------------------------
    def _run(self, sid: int, hint: int, timeline: list[Action], start_tick: int, *, stop_tick: int | None = None,
             record_cache: bool = False, check: bool = False, limit_ms: int | None = None):
        """Simulate timeline[start_tick:] from plugin state `sid` (which is at start_tick).

        Returns (finish, s_tie, end_sid, end_hint, end_ahead). When stop_tick is reached the state there is
        kept alive and returned as end_sid (the caller frees it); otherwise end_sid is None."""
        tiebreak_tick = (self.best_finish // TICK_MS - 1) if self.best_finish > 0 else -1
        stops: set[int] = set()
        if record_cache:
            stops |= set(range(start_tick - start_tick % self.cache_ticks + self.cache_ticks, len(timeline), self.cache_ticks))
        if check:
            stops |= set(range(start_tick - start_tick % CHECK_TICKS + CHECK_TICKS, len(timeline), CHECK_TICKS))
        if tiebreak_tick > start_tick:
            stops.add(tiebreak_tick)
        if stop_tick is not None:
            stops.add(stop_tick)
        temp: list[int] = []
        s_tie = -1.0
        finish = -1
        first_sid = sid
        for tick, n, a in segments(timeline, start_tick, stops):
            node = self.bridge.expand(sid, a, n)
            end_tick = tick + n
            proj = self.ref.project(node.pos, node.vel, hint)
            hint = proj.idx
            ahead = proj.t_ref - node.race_time
            if end_tick == tiebreak_tick:
                s_tie = proj.s
            if stop_tick is not None and end_tick == stop_tick and not node.finished:
                self.bridge.free(temp)
                return finish, s_tie, node.sid, hint, ahead
            if record_cache and end_tick % self.cache_ticks == 0:
                self.cache[end_tick] = node.sid
                self.cache_hint[end_tick] = hint
                self.best_ahead[end_tick] = ahead
            else:
                temp.append(node.sid)
            if check and end_tick in self.best_ahead and ahead < self.best_ahead[end_tick] - ABORT_BEHIND_MS:
                self.aborted += 1
                break
            sid = node.sid
            if node.finished:
                finish = node.finish_time
                break
            if limit_ms is not None and node.race_time > limit_ms:
                break
        del first_sid
        self.bridge.free(temp)
        return finish, s_tie, None, hint, None

    def _base(self, tick: int) -> int:
        return tick - tick % self.cache_ticks

    def accept(self, timeline: list[Action], start_tick: int) -> None:
        """Make `timeline` the current best and refresh the state cache from start_tick on."""
        base = self._base(start_tick)
        self.bridge.free([sid for t, sid in self.cache.items() if t > base])
        self.cache = {t: sid for t, sid in self.cache.items() if t <= base}
        self.cache_hint = {t: h for t, h in self.cache_hint.items() if t <= base}
        self.best = timeline
        finish, _, _, _, _ = self._run(self.cache[base], self.cache_hint[base], timeline, base, record_cache=True)
        self.best_finish = finish
        # Tie-break value of the new best: progress one tick before its finish.
        tb = self._base(max(0, finish // TICK_MS - 60))
        _, self.best_tiebreak, _, _, _ = self._run(self.cache[tb], self.cache_hint[tb], timeline, tb)

    def evaluate(self, cand: list[Action], first: int, last: int) -> tuple[int, float, list[Action]]:
        """Finish time and tie-break of a candidate (possibly time-warped), and the timeline actually used."""
        base = self._base(first)
        w = self._base(last + WARP_AFTER_TICKS) + self.cache_ticks
        end_tick = self.best_finish // TICK_MS
        self.last_warp = 0
        if not self.warp or w not in self.best_ahead or w >= end_tick - 20:
            finish, s_tie, _, _, _ = self._run(self.cache[base], self.cache_hint[base], cand, base,
                                               check=True, limit_ms=self.best_finish)
            return finish, s_tie, cand
        finish, s_tie, sid_w, hint_w, ahead_w = self._run(self.cache[base], self.cache_hint[base], cand, base,
                                                          stop_tick=w, check=True, limit_ms=self.best_finish)
        if sid_w is None:          # aborted or finished before the warp point
            return finish, s_tie, cand
        d = int(round((ahead_w - self.best_ahead[w]) / TICK_MS))
        d = max(-MAX_WARP_TICKS, min(MAX_WARP_TICKS, d))
        if d > 0:
            tail = self.best[w + d:]
        elif d < 0:
            tail = [self.best[w]] * (-d) + self.best[w:]
        else:
            tail = self.best[w:]
        warped = cand[:w] + tail
        if len(warped) < len(self.best):
            warped += [warped[-1]] * (len(self.best) - len(warped))
        if d != 0:
            self.warped += 1
            self.last_warp = d
        finish, s_tie2, _, _, _ = self._run(sid_w, hint_w, warped, w, check=d == 0, limit_ms=self.best_finish)
        self.bridge.free([sid_w])
        return finish, max(s_tie, s_tie2), warped

    # -- moves -----------------------------------------------------------------------------
    def mutate(self, timeline: list[Action], lo: int, hi: int) -> tuple[list[Action], int, int, str]:
        """Returns (candidate, first changed tick, last changed tick, move kind)."""
        cand = list(timeline)
        n = len(cand)
        r = self.rng.random()
        if self.analog:
            if r < 0.5:
                return self._mutate_steer(cand, lo, hi, r < 0.3)
            r = self.rng.random()
        if r < 0.15:   # lift: release the throttle for 50-400 ms
            start = self.rng.randrange(lo, hi)
            length = self.rng.randint(5, 40)
            for i in range(start, min(start + length, n)):
                cand[i] = with_key(with_key(cand[i], "up", False), "down", False)
            self.last_desc = f"lift off the throttle for {length * 10} ms at {start / 100:.2f} s"
            return cand, start, start + length, "lift"
        if r < 0.25:   # brake -> lift: coast instead of braking
            brakes = [i for i in range(lo, min(hi + 50, n)) if cand[i].down]
            if brakes:
                i = self.rng.choice(brakes)
                a = i
                while a > 0 and cand[a - 1].down:
                    a -= 1
                b = i
                while b + 1 < n and cand[b + 1].down:
                    b += 1
                for k in range(a, b + 1):
                    cand[k] = with_key(with_key(cand[k], "down", False), "up", False)
                self.last_desc = f"coast instead of braking for {(b - a + 1) * 10} ms at {a / 100:.2f} s"
                return cand, a, b, "brake->lift"
        if self.analog:   # left/right keys do nothing while analog steering is held
            key = self.rng.choice(("up", "down"))
        else:
            key = self.rng.choice(KEYS[1:] if self.rng.random() < 0.85 else KEYS)
        if self.rng.random() < 0.6:
            edges = [i for i in range(max(lo, 1), min(hi, n)) if getattr(cand[i], key) != getattr(cand[i - 1], key)]
            if edges:
                e = self.rng.choice(edges)
                d = self.rng.choice((-4, -3, -2, -1, 1, 2, 3, 4))
                if d > 0:   # extend the previous state forward
                    v = getattr(cand[e - 1], key)
                    for i in range(e, min(e + d, n)):
                        cand[i] = with_key(cand[i], key, v)
                    self.last_desc = (f"{KEY_NAMES[key]} {'release' if v else 'press'} {d * 10} ms later"
                                      f" at {e / 100:.2f} s")
                    return cand, e, e + d, "shift"
                v = getattr(cand[e], key)  # pull the new state backward
                for i in range(max(e + d, 0), e):
                    cand[i] = with_key(cand[i], key, v)
                self.last_desc = (f"{KEY_NAMES[key]} {'press' if v else 'release'} {-d * 10} ms earlier"
                                  f" at {e / 100:.2f} s")
                return cand, max(e + d, 0), e, "shift"
        start = self.rng.randrange(lo, hi)
        length = self.rng.randint(1, 8)
        v = not getattr(cand[start], key)
        for i in range(start, min(start + length, n)):
            cand[i] = with_key(cand[i], key, v)
        self.last_desc = (f"{'tap' if v else 'let go of'} {KEY_NAMES[key]} for {length * 10} ms"
                          f" at {start / 100:.2f} s")
        return cand, start, start + length, "tap"

    def _mutate_steer(self, cand: list[Action], lo: int, hi: int, nudge: bool) -> tuple[list[Action], int, int, str]:
        """Analog steering moves: nudge (steer more/less over a window) or shift (move a window in time)."""
        n = len(cand)
        start = self.rng.randrange(lo, hi)
        length = self.rng.randint(3, 30)
        end = min(start + length, n)
        if nudge:
            delta = self.rng.choice((-20000, -10000, -5000, -2500, -1000, 1000, 2500, 5000, 10000, 20000))
            for i in range(start, end):
                a = cand[i]
                cand[i] = Action(up=a.up, down=a.down, steer=max(-65536, min(65536, a.steer + delta)))
            side = "right" if delta > 0 else "left"
            self.last_desc = f"steer {abs(delta) * 100 // 65536}% more to the {side} for {length * 10} ms at {start / 100:.2f} s"
            return cand, start, end, "steer-nudge"
        d = self.rng.choice((-4, -3, -2, -1, 1, 2, 3, 4))
        src = list(cand)
        for i in range(start, end):
            j = min(max(i + d, 0), n - 1)
            a = cand[i]
            cand[i] = Action(up=a.up, down=a.down, steer=src[j].steer)
        self.last_desc = (f"steering {abs(d) * 10} ms {'earlier' if d > 0 else 'later'} for {length * 10} ms"
                          f" at {start / 100:.2f} s")
        return cand, start, end, "steer-shift"

    # -- main loop -------------------------------------------------------------------------
    def run(self, budget_s: float, log_every_s: float = 30.0) -> None:
        root = self.bridge.wait_start(restart=True)
        self.cache = {0: root.sid}
        self.cache_hint = {0: 0}
        self.accept(self.best, 0)
        print(f"start: finish {self.best_finish} ms" + (f", moves start in ticks {self.window}" if self.window else "")
              + (", time warp on" if self.warp else ""), flush=True)
        t0 = time.time()
        self.started_at = t0
        last_log = t0
        last_sync = t0
        self.progress.append({"t_s": 0.0, "evals": 0, "accepted": 0, "best_ms": self.best_finish})
        self.publish()
        while time.time() - t0 < budget_s and (self.max_evals is None or self.evals < self.max_evals):
            if time.time() - last_sync > self.sync_every_s:
                last_sync = time.time()
                self.maybe_adopt(time.time() - t0)
            end_tick = self.best_finish // TICK_MS
            w_lo, w_hi = self.window or (0, end_tick - 20)
            w_hi = min(w_hi, end_tick - 20)
            if w_hi <= w_lo:
                time.sleep(1)
                continue
            lo = self.rng.randrange(w_lo, w_hi)
            cand, first, last, kind = self.mutate(self.best, lo, min(lo + 100, end_tick))
            if cand == self.best:
                continue
            self.evals += 1
            self.moves.setdefault(kind, [0, 0])[0] += 1
            finish, s_tie, used = self.evaluate(cand, first, last)
            better = finish > 0 and (finish < self.best_finish or (finish == self.best_finish and s_tie > self.best_tiebreak + 1e-3))
            if better:
                before, before_tie = self.best_finish, self.best_tiebreak
                desc, warp = self.last_desc, self.last_warp
                self.accepted += 1
                self.moves[kind][1] += 1
                self.accept(used, first)
                el = round(time.time() - t0, 1)
                self.history.append((el, self.best_finish))
                self.tweaks.append({
                    "t_s": el, "evals": self.evals, "kind": kind, "change": desc,
                    "race_time_s": round(first / 100, 2), "first_tick": first, "last_tick": last,
                    "finish_before_ms": before, "finish_after_ms": self.best_finish,
                    "gain_ms": before - self.best_finish,
                    "tiebreak_gain_m": round(self.best_tiebreak - before_tie, 3) if before == self.best_finish else None,
                    "time_warp_ticks": warp,
                })
                print(f"  [{el:6.0f}s] {desc}: finish {self.best_finish} ms"
                      + (f" (-{before - self.best_finish} ms)" if before > self.best_finish else
                         f" (+{self.best_tiebreak - before_tie:.2f} m further at the line)")
                      + (f" [rest of run shifted {warp:+d} ticks]" if warp else ""), flush=True)
                self.publish()
                if self.on_update:
                    self.on_update()
            if time.time() - last_log > log_every_s:
                last_log = time.time()
                el = time.time() - t0
                self.progress.append({"t_s": round(el, 1), "evals": self.evals, "accepted": self.accepted,
                                      "best_ms": self.best_finish})
                print(f"[{el:6.0f}s] evals {self.evals} ({self.evals / el:.1f}/s), accepted {self.accepted},"
                      f" aborted early {self.aborted}, warped {self.warped}, best {self.best_finish} ms", flush=True)
                if self.on_update:
                    self.on_update()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--replay", type=Path, default=Path("data/replays/MIS_B01-Race.Replay.gbx"))
    ap.add_argument("--start", type=Path, default=None, help="start from a search result .json instead of the PB")
    ap.add_argument("--budget", type=float, default=1200, help="seconds")
    ap.add_argument("--max-evals", type=int, default=None, help="stop after this many evaluations")
    ap.add_argument("--seed", type=int, default=218)
    ap.add_argument("--from-tick", type=int, default=None, help="only change inputs from this tick (10 ms) on")
    ap.add_argument("--to-tick", type=int, default=None, help="...up to this tick")
    ap.add_argument("--warp", action="store_true", help="time-warp the rest of the run after an early change")
    ap.add_argument("--tag", default="")
    ap.add_argument("--out", type=Path, default=Path("results"))
    ap.add_argument("--port", type=int, default=8477, help="plugin port of the game instance to use")
    ap.add_argument("--shared", type=Path, default=None, help="shared best-run file for parallel workers")
    args = ap.parse_args()

    ref = Reference(args.reference)
    if args.start:
        from .analyze import LABELS
        res = json.loads(args.start.read_text())
        timeline = [LABELS[label] for n, label in res["inputs"] for _ in range(n)]
        timeline += [timeline[-1]] * 100
    else:
        info = read_replay(args.replay)
        timeline = input_timeline(info.events, info.race_time // TICK_MS + 100, ref.offset_ms)
    window = None
    if args.from_tick is not None or args.to_tick is not None:
        window = (args.from_tick or 0, args.to_tick or len(timeline))

    bridge = Bridge(port=args.port, timeout=180)
    bridge.set_speed(100)
    bridge.execute("set draw_game false")
    args.out.mkdir(parents=True, exist_ok=True)
    progress_path = args.out / f"progress_{args.tag or 'run'}_seed{args.seed}.json"
    try:
        ls = LocalSearch(bridge, ref, timeline, seed=args.seed, window=window, warp=args.warp)
        ls.worker = f"{args.tag or 'run'}-{args.seed}"
        ls.shared_path = args.shared
        ls.max_evals = args.max_evals

        def save_progress() -> None:
            progress_path.write_text(json.dumps({
                "worker": ls.worker, "window": window, "warp": args.warp, "best_ms": ls.best_finish,
                "started_at": getattr(ls, "started_at", None),
                "evaluations": ls.evals, "accepted": ls.accepted, "moves": ls.moves,
                "progress": ls.progress, "tweaks": ls.tweaks, "adoptions": ls.adoptions,
            }, indent=1))

        ls.on_update = save_progress
        ls.run(args.budget)
        save_progress()
        inputs: list[tuple[int, Action]] = []
        for a in ls.best[: ls.best_finish // TICK_MS + 1]:
            if inputs and inputs[-1][1] == a:
                inputs[-1] = (inputs[-1][0] + 1, a)
            else:
                inputs.append((1, a))
        check = verify(bridge, inputs, ls.cache[0])
    finally:
        bridge.execute("set draw_game true")
        bridge.set_speed(1)
        bridge.close()

    medals = ref.medals or {}
    print(f"\nlocal search: {ls.best_finish / 1000:.2f} s (verified {check / 1000:.2f} s), PB {ref.race_time / 1000:.2f} s,"
          f" gold {medals.get('gold', 0) / 1000:.2f} s; {ls.evals} evaluations, {ls.accepted} accepted;"
          f" moves tried/accepted {ls.moves}")
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"local_seed{args.seed}" + (f"_{args.tag}" if args.tag else "") + f"_{ls.best_finish}"
    (args.out / f"{stem}.json").write_text(json.dumps({
        "finish_time": ls.best_finish,
        "verified_finish_time": check,
        "reference_time": ref.race_time,
        "medals": medals,
        "inputs": [(n, a.label()) for n, a in inputs],
        "stats": {"evaluations": ls.evals, "accepted": ls.accepted, "aborted": ls.aborted, "warped": ls.warped,
                  "moves": ls.moves, "history": ls.history, "budget_s": args.budget, "max_evals": args.max_evals, "seconds": round(time.time() - ls.started_at, 1), "seed": args.seed,
                  "window": window, "warp": args.warp, "start": str(args.start) if args.start else "PB",
                  "tweaks": ls.tweaks, "progress": ls.progress, "adoptions": ls.adoptions},
    }, indent=1))
    (args.out / f"{stem}.txt").write_text(to_tmi_script(inputs, ref.offset_ms))
    print(f"saved {args.out / stem}.json and .txt")


if __name__ == "__main__":
    main()
