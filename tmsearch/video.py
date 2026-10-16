"""Cut the demo video (1920x1080, 60 fps) from recorded clips (see record.py).

Sections: title cards, the full AI run with a live data overlay (timer, speed, gap to the PB ghost,
mini-map), the highlights in slow motion from other cameras, a side-by-side of PB vs AI, and an end
card with the result and the time-gain chart.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .reference import Reference

W, H = 1920, 1080
FPS = 60
FONT_DIR = Path("C:/Windows/Fonts")
# Trackmania 2020 palette (from trackmania.com), shared with the deck and the charts.
BG = (7, 26, 44)              # ink #071A2C
NAVY = (11, 50, 86)           # TM navy #0B3256
ACCENT = (110, 250, 160)      # TM mint #6EFAA0: the AI
GHOST = (241, 255, 246)       # my PB ghost
GOLD = (226, 186, 18)
AUTHOR = (110, 250, 160)
AHEAD = (58, 160, 255)        # blue split when ahead (#3AA0FF)
BEHIND = (255, 90, 90)
MUTED = (141, 162, 181)
WHITE = (241, 255, 246)

_fonts: dict = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _fonts:
        names = ("segoeuib.ttf", "arialbd.ttf") if bold else ("bahnschrift.ttf", "arial.ttf")
        for name in names:
            p = FONT_DIR / name
            if p.exists():
                _fonts[key] = ImageFont.truetype(str(p), size)
                break
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def fmt_time(ms: float) -> str:
    ms = max(0, int(round(ms)))
    return f"{ms // 60000:01d}:{(ms // 1000) % 60:02d}.{(ms % 1000) // 10:02d}"


# -- inputs -----------------------------------------------------------------------------------------
class Clip:
    """A recorded clip: 100 fps video, one frame per physics tick, plus a JSON sidecar."""

    def __init__(self, path: Path):
        self.path = path
        self.meta = json.loads(path.with_suffix(".json").read_text())
        self.times = self.meta["times"]
        self.speeds = self.meta["speeds"]
        self.finish = self.meta["finish_time"]

    def index(self, t: float) -> int:
        return int(min(max(round((t - self.times[0]) / 10), 0), len(self.times) - 1))

    def speed(self, t: float) -> int:
        return self.speeds[self.index(t)]


class Reader:
    """Sequential frame access by race time (times must not go backwards)."""

    def __init__(self, clip: Clip, t_start: float):
        self.clip = clip
        self.pos = clip.index(t_start)
        self.gen = imageio_ffmpeg.read_frames(str(clip.path), input_params=["-ss", f"{self.pos / 100:.3f}"])
        meta = next(self.gen)
        self.w, self.h = meta["size"]
        self.frame = None
        self.frame = self._next()

    def _next(self):
        raw = next(self.gen, None)
        if raw is None:
            return self.frame
        return np.frombuffer(raw, dtype=np.uint8).reshape(self.h, self.w, 3)

    def at(self, t: float) -> np.ndarray:
        target = self.clip.index(t)
        while self.pos < target:
            self.frame = self._next()
            self.pos += 1
        return self.frame

    def close(self):
        self.gen.close()


@dataclass
class Telemetry:
    """Per-tick AI position + gap to the PB ghost, and the PB trajectory for the mini-map."""

    ref: Reference
    ai_t: np.ndarray
    ai_xz: np.ndarray
    ai_ahead: np.ndarray

    @classmethod
    def load(cls, ref: Reference, ai_csv: Path) -> "Telemetry":
        d = np.genfromtxt(ai_csv, delimiter=",", names=True)
        hint = 0
        ahead = []
        for row in d:
            proj = ref.project((row["x"], row["y"], row["z"]), (0.0, 0.0, 0.0), hint)
            hint = proj.idx
            ahead.append(proj.t_ref - row["t_ms"])
        return cls(ref, d["t_ms"], np.stack([d["x"], d["z"]], axis=1), np.asarray(ahead))

    def ai_at(self, t: float):
        i = int(np.clip(np.searchsorted(self.ai_t, t), 0, len(self.ai_t) - 1))
        return self.ai_xz[i], self.ai_ahead[i]

    def pb_at(self, t: float):
        i = int(np.clip(np.searchsorted(self.ref.t, t), 0, len(self.ref.t) - 1))
        return self.ref.p[i, [0, 2]]


# -- drawing helpers --------------------------------------------------------------------------------
def fit(frame: np.ndarray, w: int, h: int) -> Image.Image:
    img = Image.fromarray(frame)
    return img if img.size == (w, h) else img.resize((w, h), Image.LANCZOS)


def fade(t: float, start: float, end: float, ramp: float = 350) -> float:
    if t < start or t > end:
        return 0.0
    return float(min(1.0, (t - start) / ramp, (end - t) / ramp))


def caption(img: Image.Image, text: str, sub: str | None, alpha: float, x: int = 60, y: int = 880):
    if alpha <= 0:
        return
    d = ImageDraw.Draw(img, "RGBA")
    f1, f2 = font(50, bold=True), font(32)
    bw = int(max(d.textlength(text, font=f1), d.textlength(sub, font=f2) if sub else 0)) + 60
    bh = 90 + (50 if sub else 0)
    d.rectangle([x, y - 14, x + bw, y - 14 + bh], fill=(10, 10, 14, int(205 * alpha)))
    d.rectangle([x, y - 14, x + 9, y - 14 + bh], fill=ACCENT + (int(255 * alpha),))
    d.text((x + 32, y), text, font=f1, fill=(255, 255, 255, int(255 * alpha)))
    if sub:
        d.text((x + 32, y + 66), sub, font=f2, fill=MUTED + (int(255 * alpha),))


def banner(img: Image.Image, text: str):
    d = ImageDraw.Draw(img, "RGBA")
    f = font(32, bold=True)
    w = int(d.textlength(text, font=f)) + 60
    d.rectangle([0, 40, w, 100], fill=(10, 10, 14, 185))
    d.rectangle([0, 40, 8, 100], fill=ACCENT + (255,))
    d.text((30, 50), text, font=f, fill=WHITE)


class MiniMap:
    def __init__(self, ref: Reference, box: tuple[int, int, int, int]):
        xz = ref.p[:, [0, 2]]
        lo, hi = xz.min(axis=0), xz.max(axis=0)
        x0, y0, x1, y1 = box
        scale = min((x1 - x0 - 40) / (hi[0] - lo[0]), (y1 - y0 - 40) / (hi[1] - lo[1]))
        self.lo, self.scale = lo, scale
        self.off = (x0 + (x1 - x0 - (hi[0] - lo[0]) * scale) / 2, y0 + (y1 - y0 - (hi[1] - lo[1]) * scale) / 2)
        self.line = [self.px(p) for p in xz[::5]]

    def px(self, p) -> tuple[float, float]:
        return (self.off[0] + (p[0] - self.lo[0]) * self.scale, self.off[1] + (p[1] - self.lo[1]) * self.scale)

    def draw(self, d: ImageDraw.ImageDraw, ai, pb):
        d.line(self.line, fill=(70, 76, 92, 255), width=9, joint="curve")
        d.line(self.line, fill=(120, 128, 150, 255), width=3, joint="curve")
        for p, col, r in ((pb, GHOST, 8), (ai, ACCENT, 10)):
            x, y = self.px(p)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,), outline=(0, 0, 0, 255), width=3)


PANEL_X = 1450


def panel(img: Image.Image, t: float, speed: int, ahead_ms: float, cam_label: str, mm: MiniMap, ai_xz, pb_xz):
    d = ImageDraw.Draw(img, "RGBA")
    x0 = PANEL_X
    d.rectangle([x0, 0, W, H], fill=BG + (200,))
    d.rectangle([x0, 0, x0 + 6, H], fill=ACCENT + (255,))
    x = x0 + 40
    d.text((x, 34), "AI SEARCH AGENT", font=font(30, bold=True), fill=ACCENT)
    d.text((x, 74), cam_label, font=font(24), fill=MUTED)
    d.text((x, 128), "RACE TIME", font=font(22), fill=MUTED)
    d.text((x, 152), fmt_time(t), font=font(80), fill=WHITE)
    d.text((x, 268), "SPEED", font=font(22), fill=MUTED)
    d.text((x, 292), f"{speed:3d}", font=font(80), fill=WHITE)
    d.text((x + 175, 330), "km/h", font=font(32), fill=MUTED)
    bar = int(390 * min(speed, 450) / 450)
    d.rectangle([x, 398, x + 390, 410], fill=(40, 44, 56, 255))
    d.rectangle([x, 398, x + bar, 410], fill=ACCENT + (255,))
    d.text((x, 440), "GAP TO MY PB GHOST", font=font(22), fill=MUTED)
    split = -ahead_ms / 1000.0          # TrackMania convention: negative = ahead
    d.text((x, 466), f"{split:+.3f} s", font=font(66), fill=AHEAD if split <= 0 else BEHIND)
    mm.draw(d, ai_xz, pb_xz)
    d.text((x, 1030), "green: AI     white: my PB ghost", font=font(22), fill=MUTED)


# -- editor -----------------------------------------------------------------------------------------
class Editor:
    def __init__(self, out: Path, clips: dict[str, Clip], tele: Telemetry, info: dict):
        self.writer = imageio_ffmpeg.write_frames(
            str(out), (W, H), fps=FPS, codec="libx264", pix_fmt_in="rgb24",
            output_params=["-crf", "18", "-preset", "medium", "-movflags", "+faststart"], macro_block_size=1,
        )
        self.writer.send(None)
        self.clips = clips
        self.tele = tele
        self.info = info
        self.mm = MiniMap(tele.ref, (PANEL_X + 30, 570, W - 30, 1010))
        self.frames = 0

    def emit(self, img: Image.Image, fade_alpha: float = 1.0):
        arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
        if fade_alpha < 1.0:
            arr = (arr.astype(np.float32) * max(fade_alpha, 0.0)).astype(np.uint8)
        self.writer.send(np.ascontiguousarray(arr))
        self.frames += 1

    def card(self, seconds: float, lines: list[tuple[str, int, tuple, bool]], background: Image.Image | None = None):
        n = int(seconds * FPS)
        base = background.copy() if background is not None else Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(base)
        total = sum(size + 28 for _, size, _, _ in lines)
        y = (H - total) / 2
        for text, size, col, bold in lines:
            f = font(size, bold)
            d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=col)
            y += size + 28
        for k in range(n):
            self.emit(base, min(1.0, k / (0.4 * FPS), (n - 1 - k) / (0.4 * FPS)))

    def blurred_still(self, clip: str, t: float) -> Image.Image:
        r = Reader(self.clips[clip], t)
        img = fit(r.at(t), W, H)
        r.close()
        img = img.filter(ImageFilter.GaussianBlur(14))
        return Image.blend(Image.new("RGB", (W, H), BG), img, 0.4)

    def run_with_panel(self, clip: str, cam_label: str, t0: float, t1: float, rate: float,
                       captions=(), banner_text: str | None = None):
        c = self.clips[clip]
        r = Reader(c, t0)
        n = int((t1 - t0) / rate / 1000 * FPS)
        for k in range(n):
            t = t0 + k * rate * 1000 / FPS
            img = fit(r.at(t), W, H).copy()
            ai_xz, ahead = self.tele.ai_at(t)
            shown_t = min(t, c.finish) if c.finish > 0 else t
            panel(img, shown_t, c.speed(t), ahead, cam_label, self.mm, ai_xz, self.tele.pb_at(t))
            if banner_text:
                banner(img, banner_text)
            for (cs, ce, text, sub) in captions:
                caption(img, text, sub, fade(t, cs, ce))
            if c.finish > 0 and t >= c.finish:
                self._finish_flash(img, c.finish, t)
            self.emit(img, min(1.0, (k + 1) / (0.25 * FPS), (n - k) / (0.25 * FPS)))
        r.close()

    def _finish_flash(self, img: Image.Image, finish: int, t: float):
        d = ImageDraw.Draw(img, "RGBA")
        a = int(min(1.0, (t - finish) / 250) * 225)
        cx = PANEL_X / 2
        d.rectangle([cx - 420, 400, cx + 420, 590], fill=(10, 10, 14, a))
        d.text((cx - 380, 412), "FINISH", font=font(36, bold=True), fill=MUTED + (a,))
        f = font(100, bold=True)
        text = fmt_time(finish)
        d.text((cx - d.textlength(text, font=f) / 2, 450), text, font=f, fill=AHEAD + (a,))

    def side_by_side(self, left: str, right: str, t0: float, t1: float, rate: float, title: str,
                     labels: tuple[str, str] = ("MY PB  (human)", "AI SEARCH")):
        cl, cr = self.clips[left], self.clips[right]
        rl, rr = Reader(cl, t0), Reader(cr, t0)
        n = int((t1 - t0) / rate / 1000 * FPS)
        fw, fh = 948, 533
        for k in range(n):
            t = t0 + k * rate * 1000 / FPS
            img = Image.new("RGB", (W, H), BG)
            img.paste(fit(rl.at(t), fw, fh), (8, 150))
            img.paste(fit(rr.at(t), fw, fh), (W - fw - 8, 150))
            d = ImageDraw.Draw(img)
            d.text((40, 40), title, font=font(42, bold=True), fill=WHITE)
            d.text((W - 380, 52), f"race time {fmt_time(t)}", font=font(32), fill=MUTED)
            for x, c, lab, col in ((40, cl, labels[0], GHOST), (W - fw + 32, cr, labels[1], ACCENT)):
                d.rectangle([x - 32, 700, x - 32 + fw, 707], fill=col)
                d.text((x, 730), lab, font=font(40, bold=True), fill=col)
                d.text((x, 790), f"{c.speed(t):3d}", font=font(76), fill=WHITE)
                d.text((x + 170, 826), "km/h", font=font(32), fill=MUTED)
                if c.finish > 0 and t >= c.finish:
                    d.text((x + 500, 790), fmt_time(c.finish), font=font(76), fill=AHEAD)
            self.emit(img, min(1.0, (k + 1) / (0.25 * FPS), (n - k) / (0.25 * FPS)))
        rl.close()
        rr.close()

    def image_card(self, image: Path, title: str, caption: str, seconds: float):
        n = int(seconds * FPS)
        im = Image.open(image).convert("RGB")
        w = 1480
        im = im.resize((w, int(im.height * w / im.width)), Image.LANCZOS)
        base = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(base)
        d.text((160, 70), title, font=font(54, bold=True), fill=WHITE)
        base.paste(im, ((W - w) // 2, 170))
        d.text((160, 170 + im.height + 30), caption, font=font(32), fill=MUTED)
        for k in range(n):
            self.emit(base, min(1.0, k / (0.4 * FPS), (n - 1 - k) / (0.4 * FPS)))

    def tweak_card(self, tweaks: list[dict], seconds_each: float = 1.6):
        """Tweaks appear one by one: what the search changed and what it saved."""
        f_title, f_row, f_gain = font(54, bold=True), font(34), font(34, bold=True)
        rows = tweaks[:8]
        per = int(seconds_each * FPS)
        total = per * len(rows) + int(2.5 * FPS)
        for k in range(total):
            img = Image.new("RGB", (W, H), BG)
            d = ImageDraw.Draw(img)
            d.text((160, 90), "What the search changed", font=f_title, fill=WHITE)
            d.line([(160, 170), (W - 160, 170)], fill=NAVY, width=2)
            shown = min(len(rows), k // per + 1)
            for i, tw in enumerate(rows[:shown]):
                a = min(1.0, (k - i * per) / (0.3 * FPS))
                y = 220 + i * 92
                col = tuple(int(c * a + b * (1 - a)) for c, b in zip(ACCENT, BG))
                txt = tuple(int(c * a + b * (1 - a)) for c, b in zip(WHITE, BG))
                d.text((160, y), f"-{tw['gain_ms']} ms", font=f_gain, fill=col)
                d.text((400, y), tw["change"], font=f_row, fill=txt)
                d.text((W - 420, y), f"found after {tw['t_s'] / 60:.0f} min", font=font(28), fill=tuple(
                    int(c * a + b * (1 - a)) for c, b in zip(MUTED, BG)))
            self.emit(img, min(1.0, k / (0.4 * FPS), (total - 1 - k) / (0.4 * FPS)))

    def end_card(self, chart: Path, seconds: float):
        n = int(seconds * FPS)
        ch = Image.open(chart).convert("RGB")
        cw = 1080
        ch = ch.resize((cw, int(ch.height * cw / ch.width)), Image.LANCZOS)
        info = self.info
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.text((70, 70), "RESULT", font=font(34, bold=True), fill=ACCENT)
        d.text((70, 120), f"AI  {fmt_time(info['ai'])}", font=font(96, bold=True), fill=WHITE)
        d.text((70, 250), f"My PB  {fmt_time(info['pb'])}", font=font(52), fill=GHOST)
        d.text((70, 320), f"Gold  {fmt_time(info['gold'])}", font=font(52), fill=GOLD)
        d.text((70, 390), f"Author  {fmt_time(info['author'])}", font=font(52), fill=MUTED)
        if info.get("wr"):
            d.text((70, 440), f"World record (oli10)  {fmt_time(info['wr'])}", font=font(40), fill=MUTED)
        gain = (info["pb"] - info["ai"]) / 1000
        d.text((70, 510), f"-{gain:.2f} s vs my PB", font=font(64, bold=True), fill=ACCENT)
        if info.get("ai_wr"):
            d.text((70, 590), f"AI from the WR line: {fmt_time(info['ai_wr'])}", font=font(40, bold=True), fill=ACCENT)
        y = 680
        for line in info["method"]:
            d.text((70, y), line, font=font(28), fill=MUTED)
            y += 42
        img.paste(ch, (W - cw - 40, (H - ch.height) // 2))
        for k in range(n):
            self.emit(img, min(1.0, k / (0.5 * FPS), (n - 1 - k) / (0.5 * FPS)))

    def close(self):
        self.writer.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=Path("video/raw"))
    ap.add_argument("--ai", default="ai", help="label prefix of the AI clips (ai_cam1.mp4 ...)")
    ap.add_argument("--pb", default="pb", help="label prefix of the PB clips")
    ap.add_argument("--ai-trajectory", type=Path, required=True)
    ap.add_argument("--chart", type=Path, required=True)
    ap.add_argument("--reference", type=Path, default=Path("data/reference.json"))
    ap.add_argument("--corners-start", type=float, default=20300, help="race time of the first highlight (ms)")
    ap.add_argument("--drop-start", type=float, default=24800, help="race time of the final-drop highlight (ms)")
    ap.add_argument("--main-cam", type=int, default=2, help="camera for the full run and the side-by-side")
    ap.add_argument("--corners-cam", type=int, default=1)
    ap.add_argument("--drop-cam", type=int, default=3)
    ap.add_argument("--out", type=Path, default=Path("video/B01_AI_vs_PB.mp4"))
    ap.add_argument("--learning-curve", type=Path, default=Path("results/learning_curve.png"))
    ap.add_argument("--tweaks", type=Path, default=Path("results/tweaks.csv"))
    ap.add_argument("--wr-result", type=Path, default=None, help="AI result that started from the world record")
    ap.add_argument("--wr-stats", type=Path, default=None, help="JSON with first_beat_evals, first_beat_min, evals_total, minutes")
    ap.add_argument("--wr-human", default="wrh", help="clip label prefix of the human world record")
    ap.add_argument("--wr-ai", default="wrai", help="clip label prefix of the AI run on the world-record line")
    args = ap.parse_args()

    clips = {p.stem: Clip(p) for p in sorted(args.raw.glob("*.mp4"))}
    print("clips:", {k: (len(c.times), c.finish) for k, c in clips.items()})
    ref = Reference(args.reference)
    tele = Telemetry.load(ref, args.ai_trajectory)
    main_key = f"{args.ai}_cam{args.main_cam}"
    ai = clips[main_key]
    medals = ref.medals or {}
    info = {
        "ai": ai.finish, "pb": ref.race_time, "gold": medals.get("gold", 0), "author": medals.get("author", 0),
        "method": [
            "Beam search + local search over keyboard inputs,",
            "run inside the real game physics (TMInterface save states).",
            "Reference line and pruning from my own PB telemetry.",
            "Tool-assisted run: never uploaded to leaderboards.",
        ],
        "wr": 25230,
    }
    if args.wr_result and args.wr_result.exists():
        info["ai_wr"] = json.loads(args.wr_result.read_text())["finish_time"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    ed = Editor(args.out, clips, tele, info)
    cs, ds = args.corners_start, args.drop_start
    fin = ai.finish
    cam2 = f"{args.ai}_cam{args.corners_cam}" if f"{args.ai}_cam{args.corners_cam}" in clips else None
    cam3 = f"{args.ai}_cam{args.drop_cam}" if f"{args.ai}_cam{args.drop_cam}" in clips else None
    pb_key = f"{args.pb}_cam{args.main_cam}" if f"{args.pb}_cam{args.main_cam}" in clips else None
    pb = clips.get(pb_key) if pb_key else None

    def speeds(t: float) -> str:
        return f"AI {ai.speed(t)} km/h  ·  me {pb.speed(t)} km/h" if pb else f"AI {ai.speed(t)} km/h"

    def gap(t: float) -> str:
        ahead = tele.ai_at(t)[1] / 1000
        return f"{abs(ahead):.2f} s {'ahead of' if ahead >= 0 else 'behind'} my PB ghost"

    still = ed.blurred_still(main_key, 3000)
    ed.card(3.5, [("Can a search algorithm beat my Trackmania PB?", 72, WHITE, True),
                  ("B01-Race  ·  TrackMania Nations Forever  ·  CSCI218 Foundations of AI", 36, MUTED, False)], still)
    ed.card(3.0, [("The goal", 40, ACCENT, True),
                  (f"Beat my PB {fmt_time(info['pb'])} and the gold medal {fmt_time(info['gold'])}", 60, WHITE, True),
                  ("with an AI that drives the real game physics", 36, MUTED, False)], still)
    ed.card(4.0, [("Beam search beat gold in 20 minutes: 0:27.66", 56, WHITE, True),
                  ("Too easy. We watched it drive, gave feedback on the corners and the braking,", 34, MUTED, False),
                  ("and pushed it further with local search on four games at once.", 34, MUTED, False)], still)

    ed.run_with_panel(main_key, f"CAMERA {args.main_cam}  ·  full run", 0, fin + 1200, 1.0, captions=[
        (300, 2800, "The final AI run, my PB ghost alongside", "Every input found by search in the real game physics"),
        (9000, 11500, "First 20 seconds: my line was hard to beat", "Thousands of tweaks tried here; none were faster"),
        (cs, cs + 2500, "After checkpoint 2 the AI pulls away", gap(cs + 4000)),
        (ds + 300, ds + 2300, "The final drop", "where most of the time comes from"),
    ], banner_text="FULL RUN  ·  real time")

    ed.card(2.2, [("Where does the time come from?", 66, WHITE, True),
                  ("1. the last corners      2. the final drop", 40, MUTED, False)])
    if cam2:
        ed.run_with_panel(cam2, f"CAMERA {cam2[-1]}  ·  slow motion", cs, ds, 0.5, captions=[
            (cs + 300, cs + 2000, "Tighter lines through the last corners", speeds(cs + 700)),
            (cs + 2300, ds - 200, "Small gains add up", gap(ds - 200)),
        ], banner_text="HIGHLIGHT 1  ·  last corners  ·  0.5x")
    drop_cam = cam3 or cam2 or main_key
    ed.run_with_panel(drop_cam, f"CAMERA {drop_cam[-1]}  ·  slow motion", ds, min(fin + 300, ds + 3200), 0.35, captions=[
        (ds + 400, ds + 1100, "Off the throttle before the drop", "the AI lifts and brakes briefly"),
        (ds + 1150, ds + 1750, "Softer landing", speeds(ds + 1200)),
        (ds + 1800, ds + 2700, "Carries speed through the curve", speeds(ds + 2200)),
    ], banner_text="HIGHLIGHT 2  ·  final drop  ·  0.35x")
    if pb is not None:
        ed.side_by_side(pb_key, main_key, ds, max(fin, pb.finish) + 900, 1.0,
                        "Side by side: my PB vs the AI (same moment in the race)")
        ed.side_by_side(pb_key, main_key, ds + 700, ds + 2700, 0.4,
                        "Again at 0.4x: the landing and the curve after it")
    if args.learning_curve.exists():
        ed.image_card(args.learning_curve, "How the AI learned",
                      "Best finish time over 86 minutes of search: beam search, local search, then 4 games in parallel", 6.0)
    if args.tweaks.exists():
        import csv
        rows = [r for r in csv.DictReader(open(args.tweaks)) if r["stage"].startswith("parallel") and r["change"]]
        rows = sorted(rows, key=lambda r: -int(r["gain_ms"]))[:7]
        for r in rows:
            r["gain_ms"] = int(r["gain_ms"])
            r["t_s"] = float(r["t_s"])
        if rows:
            ed.tweak_card(rows)
    if info.get("ai_wr"):
        wr_stats = json.loads(args.wr_stats.read_text()) if args.wr_stats and args.wr_stats.exists() else {}
        ed.card(4.5, [("More ambitious: the world record", 66, WHITE, True),
                      (f"{fmt_time(info['wr'])} by oli10, a human run (TMNF-X, 2024)", 44, MUTED, False),
                      ("We loaded his replay into our pipeline: 0:25.230, reproduced exactly", 40, ACCENT, False)])
        wrh = f"{args.wr_human}_cam{args.main_cam}"
        wra = f"{args.wr_ai}_cam{args.main_cam}"
        if wrh in clips and wra in clips:
            wf = max(clips[wrh].finish, clips[wra].finish)
            ed.side_by_side(wrh, wra, wf - 7000, wf + 1000, 1.0, "The world record vs the AI refining his line",
                            labels=("WORLD RECORD  ·  oli10", "AI ON HIS LINE"))
            ed.side_by_side(wrh, wra, wf - 1800, wf + 600, 0.25, "The last metres at 0.25x",
                            labels=("WORLD RECORD  ·  oli10", "AI ON HIS LINE"))
        lines = [(f"AI  {fmt_time(info['ai_wr'])}", 96, ACCENT, True),
                 (f"{(info['wr'] - info['ai_wr']) / 1000:.2f} s under the human world record", 48, WHITE, True)]
        if wr_stats:
            lines.append((f"first beaten after {wr_stats['first_beat_evals']:,} tries ({wr_stats['first_beat_min']:.0f} min);"
                          f" {wr_stats['evals_total']:,} tries in {wr_stats['minutes']:.0f} min on 4 games", 34, MUTED, False))
        lines.append(("Tool-assisted, offline: not a record. His line, machine precision.", 34, MUTED, False))
        ed.card(5.0, lines)
    ed.end_card(args.chart, 7.0)
    ed.close()
    print(f"wrote {args.out} ({ed.frames} frames, {ed.frames / FPS:.1f} s)")


if __name__ == "__main__":
    main()
