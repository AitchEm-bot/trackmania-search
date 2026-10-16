"""Tests against the real game. They need a game instance with the plugin and B01-Race loaded, and
are skipped unless TM_PORT is set (the plugin port, e.g. 8477):

    $env:TM_PORT = 8477; .venv\\Scripts\\python -m unittest tests.test_game -v
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from tmsearch.analyze import LABELS
from tmsearch.bridge import Bridge
from tmsearch.search import verify

ROOT = Path(__file__).resolve().parents[1]
PORT = os.environ.get("TM_PORT")


def inputs(path: Path):
    return [(n, LABELS[label]) for n, label in json.loads(path.read_text())["inputs"]]


@unittest.skipUnless(PORT, "set TM_PORT to run against a game instance")
class GameDeterminism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = Bridge(port=int(PORT), timeout=120)
        cls.bridge.set_speed(100)
        cls.bridge.execute("set draw_game false")

    @classmethod
    def tearDownClass(cls):
        cls.bridge.execute("set draw_game true")
        cls.bridge.set_speed(1)
        cls.bridge.close()

    def test_saved_runs_replay_to_their_times(self):
        for name, expected in (("b40_s5_full_27660.json", 27660), ("local_seed31_w1b-start_27280.json", 27280),
                               ("local_seed41_wr1-start_25210.json", 25210),
                               ("repeats/local_seed2_rep_27100.json", 27100)):
            with self.subTest(run=name):
                self.assertEqual(verify(self.bridge, inputs(ROOT / "results" / name)), expected)

    def test_same_inputs_twice_same_result(self):
        run = inputs(ROOT / "results/local_seed31_w1b-start_27280.json")
        self.assertEqual(verify(self.bridge, run), verify(self.bridge, run))


if __name__ == "__main__":
    unittest.main()
