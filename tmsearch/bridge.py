"""TCP client for the SearchBridge TMInterface plugin (plugin/SearchBridge.as)."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

C_PING = 1
C_SET_SPEED = 2
C_WAIT_START = 3
C_EXPAND = 4
C_FREE = 5
C_EXEC = 6
C_GET_BLOCKS = 7
C_DETACH = 8
C_GAME_STATE = 9
C_GET_VAR = 10
C_SCREENSHOT = 11
C_FOCUS = 12
C_RECORD = 13
C_RELEASE = 14

S_PONG = 100
S_NODE = 101
S_BLOCKS = 102
S_OK = 103
S_STATE = 104
S_TEXT = 105
S_FRAME = 106
S_TICK_FRAME = 107
S_FINISH = 108

GAME_STATES = {
    0: "None", 1: "GameNetMenus", 2: "GameNetRoundPrepare", 4: "GameNetRoundPlay", 8: "GameNetRoundExit",
    16: "StartUp", 32: "Menus", 64: "Quit", 128: "LocalInit", 256: "LocalEditor", 512: "LocalRace",
    1024: "LocalRaceEndDialog", 2048: "LocalReplayEditor", 4096: "LocalReplay", 8192: "LocalEnd",
    16384: "NetSync", 32768: "NetPlaying", 65536: "NetExitRound",
}

_NODE = struct.Struct("<ii9fiiBBBi")  # after the message type


@dataclass(frozen=True)
class Action:
    """Inputs held for one search step. steer is analog in [-65536, 65536]; 0 means use left/right keys."""

    up: bool = False
    down: bool = False
    left: bool = False
    right: bool = False
    steer: int = 0

    def label(self) -> str:
        if self.steer:
            return f"{'G' if self.up else ''}{'B' if self.down else ''}S{self.steer:+d}"
        keys = ("G" if self.up else "") + ("B" if self.down else "") + ("L" if self.left else "") + ("R" if self.right else "")
        return keys or "-"

    @staticmethod
    def from_label(label: str) -> "Action":
        """Inverse of label(): 'GL', 'BR', '-', or analog 'GS+12000' / 'S-4000'."""
        if "S" in label:
            keys, value = label.split("S", 1)
            return Action(up="G" in keys, down="B" in keys, steer=int(value))
        if label == "-":
            return Action()
        return Action(up="G" in label, down="B" in label, left="L" in label, right="R" in label)


@dataclass
class Node:
    """Car state reported by the plugin after a step. `sid` is the plugin-side save state id."""

    sid: int
    race_time: int
    pos: tuple[float, float, float]
    vel: tuple[float, float, float]
    yaw: float
    pitch: float
    roll: float
    cp_count: int
    finish_time: int
    wall: bool
    wheels_on_ground: int
    sliding: bool
    display_speed: int

    @property
    def speed(self) -> float:
        vx, vy, vz = self.vel
        return (vx * vx + vy * vy + vz * vz) ** 0.5

    @property
    def finished(self) -> bool:
        return self.finish_time >= 0


@dataclass
class Block:
    name: str
    x: int
    y: int
    z: int
    dir: int
    waypoint: int  # 0 start, 1 finish, 2 checkpoint, 3 none, 4 start/finish


class Bridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 8477, timeout: float = 30.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # While recording, frames streamed during an expansion are passed here as (race_time, speed, bgra).
        self.frame_sink = None
        self.finish_events: list[int] = []

    def close(self) -> None:
        try:
            self._send(struct.pack("<i", C_DETACH))
            self._expect(S_OK)
        except OSError:
            pass
        self.sock.close()

    # -- low level -------------------------------------------------------------------------
    def _send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def _recv(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("plugin closed the connection")
            buf += chunk
        return bytes(buf)

    def _recv_int(self) -> int:
        return struct.unpack("<i", self._recv(4))[0]

    def _expect(self, msg: int) -> None:
        got = self._recv_int()
        if got != msg:
            raise ConnectionError(f"expected message {msg}, got {got}")

    def _recv_node(self) -> Node:
        while True:
            msg = self._recv_int()
            if msg == S_NODE:
                break
            if msg == S_TICK_FRAME and self.frame_sink is not None:
                _, t, speed, frame = self._read_frame_payload()
                self.frame_sink(t, speed, frame)
            elif msg == S_FINISH:
                self.finish_events.append(self._recv_int())
            elif msg == S_TICK_FRAME:
                self._skip_event(msg)
            else:
                raise ConnectionError(f"expected message {S_NODE}, got {msg}")
        v = _NODE.unpack(self._recv(_NODE.size))
        return Node(
            sid=v[0], race_time=v[1], pos=(v[2], v[3], v[4]), vel=(v[5], v[6], v[7]),
            yaw=v[8], pitch=v[9], roll=v[10], cp_count=v[11], finish_time=v[12],
            wall=bool(v[13]), wheels_on_ground=v[14], sliding=bool(v[15]), display_speed=v[16],
        )

    # -- commands --------------------------------------------------------------------------
    def ping(self) -> None:
        self._send(struct.pack("<i", C_PING))
        self._expect(S_PONG)

    def set_speed(self, speed: float) -> None:
        self._send(struct.pack("<if", C_SET_SPEED, speed))
        self._expect(S_OK)

    def execute(self, command: str) -> None:
        data = command.encode("utf-8")
        self._send(struct.pack("<ii", C_EXEC, len(data)) + data)
        self._expect(S_OK)

    def focus(self) -> None:
        self._send(struct.pack("<i", C_FOCUS))
        self._expect(S_OK)

    def screenshot(self, width: int = 640, height: int = 480):
        """Game frame as an (h, w, 4) uint8 array in the byte order the game uses (BGRA)."""
        import numpy as np

        self._send(struct.pack("<iii", C_SCREENSHOT, width, height))
        self._expect(S_FRAME)
        w, h, n = struct.unpack("<iii", self._recv(12))
        return np.frombuffer(self._recv(n), dtype=np.uint8).reshape(h, w, 4)

    def get_var(self, name: str) -> str:
        data = name.encode("utf-8")
        self._send(struct.pack("<ii", C_GET_VAR, len(data)) + data)
        self._expect(S_TEXT)
        return self._recv(self._recv_int()).decode("utf-8", "replace")

    def game_state(self) -> tuple[str, int, bool]:
        """(game state name, race time ms, in race)."""
        self._send(struct.pack("<i", C_GAME_STATE))
        self._expect(S_STATE)
        state, race_time, in_race = struct.unpack("<iiB", self._recv(9))
        return GAME_STATES.get(state, str(state)), race_time, bool(in_race)

    def blocks(self) -> list[Block]:
        self._send(struct.pack("<i", C_GET_BLOCKS))
        self._expect(S_BLOCKS)
        out = []
        for _ in range(self._recv_int()):
            name = self._recv(self._recv_int()).decode("utf-8", "replace")
            x, y, z, d, w = struct.unpack("<5i", self._recv(20))
            out.append(Block(name, x, y, z, d, w))
        return out

    def wait_start(self, restart: bool = True, keep_script: bool = False) -> Node:
        """Restart the race (optionally) and return the root node at race time 0.

        Any loaded TMInterface input script is unloaded unless keep_script is set."""
        flag = 2 if (restart and keep_script) else int(restart)
        self._send(struct.pack("<iB", C_WAIT_START, flag))
        return self._recv_node()

    # -- recording -------------------------------------------------------------------------
    def record(self, on: bool, width: int = 640, height: int = 480) -> None:
        """Start/stop streaming one frame per race tick. While on, use next_event() to read them."""
        self._send(struct.pack("<iiii", C_RECORD, int(on), width, height))
        if on:
            self._expect(S_OK)
            return
        while True:   # frames may still be in flight before the acknowledgement
            msg = self._recv_int()
            if msg == S_OK:
                return
            self._skip_event(msg)

    def release(self) -> None:
        """Stop holding the game in lock-step; the race then runs on its own."""
        self._send(struct.pack("<i", C_RELEASE))
        self._expect(S_OK)

    def next_event(self):
        """Next streamed event: ('frame', race_time, display_speed, bgra array) or ('finish', race_time)."""
        import numpy as np

        msg = self._recv_int()
        if msg == S_TICK_FRAME:
            return self._read_frame_payload()
        if msg == S_FINISH:
            return ("finish", self._recv_int())
        raise ConnectionError(f"unexpected message {msg} while recording")

    def _read_frame_payload(self):
        import numpy as np

        t, speed, w, h, n = struct.unpack("<5i", self._recv(20))
        return ("frame", t, speed, np.frombuffer(self._recv(n), dtype=np.uint8).reshape(h, w, 4))

    def _skip_event(self, msg: int) -> None:
        if msg == S_TICK_FRAME:
            _t, _s, _w, _h, n = struct.unpack("<5i", self._recv(20))
            self._recv(n)
        elif msg == S_FINISH:
            self._recv_int()
        else:
            raise ConnectionError(f"unexpected message {msg}")

    def expand(self, parent: int, action: Action, ticks: int) -> Node:
        """Rewind to `parent`, hold `action` for `ticks` physics ticks (10 ms each), return the new node."""
        self._send(struct.pack(
            "<iiiBBBBi", C_EXPAND, parent, ticks,
            action.up, action.down, action.left, action.right, action.steer,
        ))
        return self._recv_node()

    def free(self, ids: list[int]) -> None:
        if not ids:
            return
        self._send(struct.pack(f"<ii{len(ids)}i", C_FREE, len(ids), *ids))
        self._expect(S_OK)
