"""Reference trajectory (from a recorded run) used to measure progress and prune the search."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Projection:
    idx: int          # index of the reference segment start
    s: float          # arc length along the reference (m)
    offset: float     # distance from the reference line (m)
    t_ref: float      # time the reference run reached this progress (ms)
    along: float      # velocity component along the reference direction (m/s)


class Reference:
    def __init__(self, path: str | Path):
        data = json.loads(Path(path).read_text())
        trace = np.asarray(data["trace"], dtype=np.float64)
        self.race_time = int(data["race_time"])
        self.cp_times = list(data["cp_times"])
        self.medals = data.get("medals")
        self.offset_ms = int(data.get("offset_ms", 0))
        self.t = trace[:, 0]
        self.p = trace[:, 1:4]
        self.cp = trace[:, 7].astype(int)
        seg = np.diff(self.p, axis=0)
        seglen = np.linalg.norm(seg, axis=1)
        # Collapse zero-length segments (car standing still at the start) so directions are defined.
        seglen = np.maximum(seglen, 1e-6)
        self.seg = seg
        self.seglen = seglen
        self.dir = seg / seglen[:, None]
        self.s = np.concatenate([[0.0], np.cumsum(seglen)])
        self.length = float(self.s[-1])
        # Progress at which each checkpoint count was first reached on the reference.
        self.cp_s = []
        for k in range(1, int(self.cp.max()) + 1):
            i = int(np.argmax(self.cp >= k))
            self.cp_s.append(float(self.s[i]))

    def project(self, pos, vel, hint: int, back: int = 40, ahead: int = 250) -> Projection:
        """Project a position onto the reference, searching segments near `hint` (avoids jumping to
        another part of the track where it crosses itself)."""
        lo = max(0, hint - back)
        hi = min(len(self.seg), hint + ahead)
        p = np.asarray(pos)
        a = self.p[lo:hi]
        d = self.dir[lo:hi]
        L = self.seglen[lo:hi]
        u = np.clip(np.einsum("ij,ij->i", p - a, d), 0.0, L)
        closest = a + d * u[:, None]
        dist = np.linalg.norm(closest - p, axis=1)
        j = int(np.argmin(dist))
        i = lo + j
        s = float(self.s[i] + u[j])
        frac = u[j] / L[j]
        t_ref = float(self.t[i] + frac * (self.t[i + 1] - self.t[i]))
        along = float(np.dot(np.asarray(vel), self.dir[i]))
        return Projection(idx=i, s=s, offset=float(dist[j]), t_ref=t_ref, along=along)
