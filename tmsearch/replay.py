"""Read TrackMania Nations Forever replay files: driver inputs, ghost telemetry, checkpoint and medal times.

pygbx needs the `lzo` module (python-lzo), which has no wheels for recent Python versions on Windows,
so we register a small stand-in backed by `lzallright` before importing pygbx.
"""

from __future__ import annotations

import struct
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path


def _install_lzo_shim() -> None:
    if "lzo" in sys.modules:
        return
    try:
        import lzo  # noqa: F401  (real python-lzo, if available)
        return
    except ImportError:
        pass
    from lzallright import LZOCompressor

    compressor = LZOCompressor()
    shim = types.ModuleType("lzo")
    shim.decompress = lambda data, header=True, buflen=None: compressor.decompress(bytes(data), output_size_hint=buflen)
    sys.modules["lzo"] = shim


_install_lzo_shim()
from pygbx import Gbx, GbxType  # noqa: E402

KEY_EVENTS = {"Accelerate": "up", "Brake": "down", "SteerLeft": "left", "SteerRight": "right"}


@dataclass
class Medals:
    bronze: int
    silver: int
    gold: int
    author: int


@dataclass
class ReplayInfo:
    race_time: int                                   # ms
    cp_times: list[int]                              # ms, last one is the finish
    events: list[tuple[int, str, bool]]              # (time ms, key in up/down/left/right, pressed)
    samples: list[tuple[float, float, float]]        # ghost positions every `sample_period` ms
    sample_period: int
    medals: Medals | None = None
    map_gbx: bytes | None = field(default=None, repr=False)


def _medals_from_map(map_gbx: bytes) -> Medals | None:
    """Read medal times from header chunk 0x03043002 of an embedded .Challenge.Gbx."""
    if not map_gbx.startswith(b"GBX"):
        return None
    off = 3 + 2 + 4 + 4  # magic, version, format flags, class id
    _user_data_size, n_chunks = struct.unpack_from("<II", map_gbx, off)
    off += 8
    entries = []
    for _ in range(n_chunks):
        cid, size = struct.unpack_from("<II", map_gbx, off)
        entries.append((cid, size & 0x7FFFFFFF))
        off += 8
    pos = off
    for cid, size in entries:
        if cid == 0x03043002:
            version = map_gbx[pos]
            if version < 3:
                return None
            bronze, silver, gold, author = struct.unpack_from("<iiii", map_gbx, pos + 1 + 4)
            return Medals(bronze, silver, gold, author)
        pos += size
    return None


def read_replay(path: str | Path) -> ReplayInfo:
    g = Gbx(str(path))
    ghost = g.get_class_by_id(GbxType.CTN_GHOST)
    if ghost is None:
        raise ValueError(f"{path}: no ghost found")

    events = []
    for e in ghost.control_entries:
        if e.event_name == "Steer":
            # pygbx splits the 32-bit event value into two uint16s; analog steering is a signed 24-bit value.
            raw = (e.enabled | (e.flags << 16)) & 0xFFFFFF
            value = raw - 0x1000000 if raw & 0x800000 else raw
            # Replays store steering with the opposite sign to TMInterface's SetInputState(Steer)
            # (verified: the flipped value re-drives the WR replay with 0.00 m error).
            events.append((e.time, "steer", max(-65536, min(65536, -value))))
            continue
        if e.event_name == "Gas":
            raise ValueError(f"{path}: analog gas is not supported yet")
        key = KEY_EVENTS.get(e.event_name)
        if key is None:
            continue
        events.append((e.time, key, bool(e.enabled)))

    map_gbx = None
    body = bytes(getattr(g, "data", b"") or b"")
    start = body.find(b"GBX")
    if start >= 0:
        map_gbx = body[start:]

    return ReplayInfo(
        race_time=ghost.race_time,
        cp_times=list(ghost.cp_times or []),
        events=events,
        samples=[tuple(r.position.as_array()) for r in ghost.records],
        sample_period=ghost.sample_period,
        medals=_medals_from_map(map_gbx) if map_gbx else None,
        map_gbx=map_gbx,
    )
