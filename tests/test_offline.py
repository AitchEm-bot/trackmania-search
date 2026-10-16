"""Tests that do not need the game running.

    .venv\\Scripts\\python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tmsearch.astar import AStar, dominates
from tmsearch.bridge import Action
from tmsearch.local import LocalSearch, segments, with_key
from tmsearch.reference import Reference
from tmsearch.reproduce import input_timeline
from tmsearch.search import ACTIONS, BeamSearch, Config, SNode, to_tmi_script

ROOT = Path(__file__).resolve().parents[1]
PB_REPLAY = ROOT / "data/replays/MIS_B01-Race.Replay.gbx"
WR_REPLAY = ROOT / "data/replays/downloaded/wr_oli10_25230.Replay.gbx"

try:
    from tmsearch.replay import read_replay
except ImportError:   # pygbx is installed separately (see README)
    read_replay = None


class ActionLabels(unittest.TestCase):
    def test_search_actions_round_trip(self):
        for name, a in ACTIONS.items():
            self.assertEqual(a.label(), name)
            self.assertEqual(Action.from_label(name), a)

    def test_analog_round_trip(self):
        a = Action(up=True, steer=-12000)
        self.assertEqual(a.label(), "GS-12000")
        self.assertEqual(Action.from_label("GS-12000"), a)


@unittest.skipIf(read_replay is None, "pygbx not installed")
class Replays(unittest.TestCase):
    def test_pb_replay(self):
        info = read_replay(PB_REPLAY)
        self.assertEqual(info.race_time, 27820)
        self.assertEqual(info.cp_times[:2], [6200, 17550])
        self.assertEqual(info.cp_times[-1], info.race_time)
        self.assertEqual((info.medals.gold, info.medals.author), (27800, 26200))
        self.assertTrue(all(key in ("up", "down", "left", "right") for _, key, _ in info.events))

    def test_wr_replay_is_analog(self):
        info = read_replay(WR_REPLAY)
        self.assertEqual(info.race_time, 25230)
        steer = [v for _, key, v in info.events if key == "steer"]
        self.assertTrue(steer)
        self.assertTrue(all(-65536 <= v <= 65536 for v in steer))


class InputTimeline(unittest.TestCase):
    def test_event_takes_effect_offset_early(self):
        # an event at time t is held from the tick that starts at t - offset
        events = [(0, "up", True), (100, "left", True), (150, "left", False)]
        tl = input_timeline(events, 30, offset_ms=10)
        self.assertTrue(all(a.up for a in tl))
        self.assertEqual([tl[i].left for i in (8, 9, 13, 14)], [False, True, True, False])

    def test_analog_steer_held(self):
        tl = input_timeline([(0, "steer", -3000), (50, "steer", 0)], 10, offset_ms=0)
        self.assertEqual(tl[4].steer, -3000)
        self.assertEqual(tl[5].steer, 0)


class ReferenceLine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = Reference(ROOT / "data/reference.json")

    def test_projection_of_own_points(self):
        ref = self.ref
        for i in range(10, len(ref.t) - 10, 50):
            p = ref.project(ref.p[i], (0.0, 0.0, 0.0), hint=max(i - 5, 0))
            self.assertLess(p.offset, 1e-3)
            self.assertAlmostEqual(p.s, ref.s[i], delta=1e-3)
            self.assertAlmostEqual(p.t_ref, ref.t[i], delta=1e-3)

    def test_checkpoints_in_order(self):
        self.assertEqual(len(self.ref.cp_s), 3)             # two checkpoints and the finish
        self.assertEqual(self.ref.cp_s, sorted(self.ref.cp_s))
        self.assertAlmostEqual(self.ref.length, 2104, delta=5)


class Export(unittest.TestCase):
    def test_tmi_script(self):
        g, gl, idle = ACTIONS["G"], ACTIONS["GL"], ACTIONS["-"]
        script = to_tmi_script([(5, g), (3, gl), (2, idle)], offset_ms=10)
        self.assertEqual(script.splitlines(), ["0.01 press up", "0.06 press left", "0.09 rel up", "0.09 rel left"])

    def test_inputs_of_merges_equal_segments(self):
        g, gl = ACTIONS["G"], ACTIONS["GL"]
        root = SNode(None, None, [], None, 0.0)
        a = SNode(None, root, [(5, g)], None, 0.0)
        b = SNode(None, a, [(5, g)], None, 0.0)
        c = SNode(None, b, [(5, gl)], None, 0.0)
        beam = BeamSearch(None, None, [], Config())
        self.assertEqual(beam.inputs_of(c), [(10, g), (5, gl)])


class LocalSearchMoves(unittest.TestCase):
    def setUp(self):
        g, gl, b = ACTIONS["G"], ACTIONS["GL"], ACTIONS["BL"]
        self.timeline = [g] * 100 + [gl] * 40 + [b] * 10 + [g] * 150

    def test_segments_cut_at_stops(self):
        g, gl = ACTIONS["G"], ACTIONS["GL"]
        self.assertEqual(segments([g, g, g, g, gl, gl], 0, {3}), [(0, 3, g), (3, 1, g), (4, 2, gl)])

    def test_left_and_right_exclusive(self):
        a = with_key(Action(up=True, left=True), "right", True)
        self.assertTrue(a.right and not a.left and a.up)

    def test_same_seed_same_moves(self):
        runs = []
        for _ in range(2):
            ls = LocalSearch(None, None, self.timeline, seed=7)
            runs.append([ls.mutate(self.timeline, 0, 280)[0] for _ in range(50)])
        self.assertEqual(runs[0], runs[1])

    def test_changes_stay_inside_reported_window(self):
        ls = LocalSearch(None, None, self.timeline, seed=11)
        for _ in range(300):
            cand, first, last, kind = ls.mutate(self.timeline, 0, 280)
            changed = [i for i, (x, y) in enumerate(zip(cand, self.timeline)) if x != y]
            if changed:
                self.assertGreaterEqual(min(changed), first, kind)
                self.assertLessEqual(max(changed), last, kind)


class AStarHeuristic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json

        cls.ref = Reference(ROOT / "data/reference.json")
        cls.wr = np.asarray(json.loads((ROOT / "data/reference_wr.json").read_text())["trace"])
        cls.wr_time = json.loads((ROOT / "data/reference_wr.json").read_text())["race_time"]

    def _along_pb_line(self, trace, finish):
        """(projection onto my PB's line, real time left) for every 10th point of a run."""
        hint = 0
        out = []
        for row in trace[::10]:
            p = self.ref.project(row[1:4], row[4:7], hint)
            hint = p.idx
            out.append((SimpleNamespace(proj=p), finish - row[0]))
        return out

    def test_distance_heuristic_never_overestimates(self):
        a = AStar(None, self.ref, Config())
        pb = np.column_stack([self.ref.t, self.ref.p, np.zeros((len(self.ref.t), 3))])
        for trace, finish in ((pb, self.ref.race_time), (self.wr, self.wr_time)):
            for sn, left in self._along_pb_line(trace, finish):
                self.assertLessEqual(a.h(sn), left + 1e-6)

    def test_pb_heuristic_overestimates_on_the_world_record(self):
        a = AStar(None, self.ref, Config(), heuristic="pb")
        over = [a.h(sn) - left for sn, left in self._along_pb_line(self.wr, self.wr_time)]
        self.assertGreater(max(over), 1000)   # not admissible: the WR needs >1 s less than my PB somewhere

    def test_dominance(self):
        def node(t, s, v):
            return SimpleNamespace(t=t, proj=SimpleNamespace(s=s), v_along=v)
        self.assertTrue(dominates(node(100, 5.0, 20.0), node(150, 5.0, 19.0)))
        self.assertFalse(dominates(node(100, 4.0, 20.0), node(150, 5.0, 19.0)))   # behind on the track
        self.assertFalse(dominates(node(100, 5.0, 18.0), node(150, 5.0, 19.0)))   # slower


if __name__ == "__main__":
    unittest.main()
