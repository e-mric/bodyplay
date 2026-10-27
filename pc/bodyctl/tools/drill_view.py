"""Drill window (look from theme.py): outlined title and teal subtitle across the
top, a sunset-and-palms corner, the camera in a viewfinder on the left (only a thin
glowing skeleton on the body, readouts in the corners), a glass side panel with the
live data on the right, and tracking meters underneath.

Everything that never changes is drawn once per style and cached; each frame only
adds the camera, text and meters. Drawn at DISPLAY_W x DISPLAY_H and scaled by the
resizable window, so it reads from across the room.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np

from bodyctl.gestures import webcam as W
from bodyctl.tools import theme as T

DISPLAY_W, DISPLAY_H = 1440, 810
TITLE_XY = (34, 10)
SUN = (1130, 0, 1440, 150)
CAM = (40, 126, 932, 770)
PANEL = (962, 126, 1408, 660)
METERS = (962, 680, 1222, 790)
SIGNAL = (1234, 680, 1408, 790)
PROGRESS = (40, 782, 932, 790)
# Painted backdrop (dark area behind the camera, sunsets on the right). Without it the
# sun corner, palms and panel art are drawn instead.
BACKGROUND = Path(__file__).resolve().parent.parent / "assets" / "drill_background.jpg"
# Panel glass opacity: see-through over the backdrop's sunset during setup, denser
# while drilling so the live data stays readable.
GLASS_ALPHA = {"setup": 120, "drill": 200}
# Drawn panel art (no backdrop): a tall sunset glow behind the short setup checklist,
# a low one while drilling.
PANEL_ART = {"setup": (300, 0.86, 0.5), "drill": (118, 0.72, 0.32)}  # height, horizon, sun

TITLE_FILL: T.RGB = (255, 222, 234)
TITLE_SHADOW: T.RGB = (150, 52, 150)
FRAME_LINE: T.RGB = (206, 196, 255)

# Style -> (title colour, camera frame glow). action = do the move now.
STYLES: dict[str, tuple[T.RGB, T.RGB]] = {
    "action": (T.GOLD, T.ORANGE),
    "idle": (TITLE_FILL, T.PLUM),
    "setup": (TITLE_FILL, T.PINK),
    "ready": (TITLE_FILL, T.MINT),
}

# BlazePose skeleton. Bone colours fade between joint colours: pink at the shoulders
# and hips, violet at elbows and knees, cyan at the hands and feet.
EDGES = [
    (11, 12), (11, 13), (13, 15), (12, 14), (14, 16), (11, 23), (12, 24), (23, 24),
    (23, 25), (25, 27), (24, 26), (26, 28), (0, 11), (0, 12),
]  # fmt: skip
_VIOLET: T.RGB = (160, 110, 255)
JOINTS: dict[int, T.RGB] = {
    W.NOSE: T.LAVENDER,
    W.L_SHOULDER: T.MAGENTA, W.R_SHOULDER: T.MAGENTA,
    W.L_ELBOW: _VIOLET, W.R_ELBOW: _VIOLET,
    W.L_WRIST: T.CYAN, W.R_WRIST: T.CYAN,
    W.L_HIP: T.MAGENTA, W.R_HIP: T.MAGENTA,
    W.L_KNEE: _VIOLET, W.R_KNEE: _VIOLET,
    W.L_ANKLE: T.CYAN, W.R_ANKLE: T.CYAN,
}  # fmt: skip

METER_PARTS = {
    "HEAD": (W.NOSE,),
    "SHLD": (W.L_SHOULDER, W.R_SHOULDER),
    "ELBW": (W.L_ELBOW, W.R_ELBOW),
    "WRST": (W.L_WRIST, W.R_WRIST),
    "HIPS": (W.L_HIP, W.R_HIP),
    "KNEE": (W.L_KNEE, W.R_KNEE),
    "FEET": (W.L_ANKLE, W.R_ANKLE),
}
REQUIRED = ("HEAD", "SHLD", "HIPS", "KNEE")


@dataclass
class Panel:
    header: str  # e.g. "REP 3 / 6" or "CAMERA SETUP"
    checks: list[tuple[str, bool]] | None = None  # setup guidance
    state: object | None = None  # BodyState while drilling
    events: list[tuple[float, str, bool]] | None = None  # (age s, name, expected), newest first
    tally: dict | None = None  # event -> EventScore, live


@dataclass
class Hud:
    fps: float = 0.0
    size: tuple[int, int] = (640, 480)
    recording: bool = False
    elapsed_s: float = 0.0
    frames: int = 0
    parts: dict[str, float] = field(default_factory=dict)  # visibility per METER_PARTS key
    quality: Sequence[float] = ()  # recent frames, oldest first, 0..1


def part_visibility(pose) -> dict[str, float]:
    if pose is None:
        return dict.fromkeys(METER_PARTS, 0.0)
    return {k: float(min(pose.img[i, 2] for i in idx)) for k, idx in METER_PARTS.items()}


def frame_quality(parts: dict[str, float]) -> float:
    return min((parts.get(k, 0.0) for k in REQUIRED), default=0.0)


def _bgr(layer) -> np.ndarray:
    return np.ascontiguousarray(np.asarray(layer.convert("RGB"))[:, :, ::-1])


# --- static layer -----------------------------------------------------------------------


def _gradient_outline(layer, box, radius: int, stops, width: int, glow: int, horizontal: bool = True) -> None:
    """Rounded outline filled with a gradient, plus a blurred halo of the same colours."""
    from PIL import Image, ImageDraw, ImageFilter

    x0, y0, x1, y1 = box
    m = glow * 2
    colour = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    colour.paste(T.pil_gradient(x1 - x0 + 2 * m, y1 - y0 + 2 * m, list(stops), horizontal), (x0 - m, y0 - m))
    halo = Image.new("L", layer.size, 0)
    ImageDraw.Draw(halo).rounded_rectangle(box, radius, outline=200, width=width + 6)
    glow_img = colour.copy()
    glow_img.putalpha(halo.filter(ImageFilter.GaussianBlur(glow)))
    layer.alpha_composite(glow_img)
    line = Image.new("L", layer.size, 0)
    ImageDraw.Draw(line).rounded_rectangle(box, radius, outline=255, width=width)
    colour.putalpha(line)
    layer.alpha_composite(colour)


def _vertical_label(text: str, size: int, color: T.RGB):
    from PIL import Image, ImageDraw

    f = T.font("body", size)
    w = int(f.getlength(text)) + 4
    img = Image.new("RGBA", (w, size + 6), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2, 0), text, font=f, fill=T.rgba(color))
    return img.rotate(90, expand=True)


def _fade_edges(img, left: int = 0, bottom: int = 0):
    """Multiply the alpha of an RGBA image by ramps on its left and bottom edges."""
    from PIL import Image

    a = np.asarray(img.getchannel("A")).astype(float)
    h, w = a.shape
    if left:
        a[:, :left] *= np.linspace(0, 1, left)[None, :]
    if bottom:
        a[h - bottom :, :] *= np.linspace(1, 0, bottom)[:, None]
    img.putalpha(Image.fromarray(a.astype(np.uint8)))
    return img


def _panel_art(layer, box, height: int, radius: int = 20, horizon: float = 0.78, sun: float = 0.45) -> None:
    """Sunset glow and palms along the bottom of a rounded box, clipped to its corners."""
    from PIL import Image

    x0, y0, x1, y1 = box
    art = T.sunset_scene(x1 - x0, height, horizon=horizon, sun=sun)
    corners = T.round_mask((x1 - x0, y1 - y0), radius).crop((0, y1 - y0 - height, x1 - x0, y1 - y0))
    alpha = np.minimum(np.asarray(art.getchannel("A")), np.asarray(corners))
    art.putalpha(Image.fromarray(alpha))
    layer.alpha_composite(art, (x0, y1 - height))


def _background():
    from PIL import Image, ImageDraw

    if BACKGROUND.exists():
        img = Image.open(BACKGROUND).convert("RGBA")
        if img.size != (DISPLAY_W, DISPLAY_H):  # cover, centred
            k = max(DISPLAY_W / img.width, DISPLAY_H / img.height)
            img = img.resize((round(img.width * k), round(img.height * k)), Image.Resampling.LANCZOS)
            x0, y0 = (img.width - DISPLAY_W) // 2, (img.height - DISPLAY_H) // 2
            img = img.crop((x0, y0, x0 + DISPLAY_W, y0 + DISPLAY_H))
        _frame_decor(ImageDraw.Draw(img))
        return img
    layer = T.pil_gradient(DISPLAY_W, DISPLAY_H, [(40, 20, 70), (58, 26, 92), (44, 20, 74)]).convert("RGBA")
    tex = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tex)
    for x in range(-DISPLAY_H, DISPLAY_W, 16):  # faint diagonal weave
        d.line((x, DISPLAY_H, x + DISPLAY_H, 0), fill=(255, 255, 255, 6))
    silhouette = T.rgba((70, 30, 100), 210)
    T.palm(d, 4, DISPLAY_H + 20, 360, 0.2, silhouette)
    T.palm(d, DISPLAY_W - 6, DISPLAY_H - 120, 420, -0.18, silhouette)
    T.palm(d, DISPLAY_W - 60, 330, 260, 0.15, silhouette)
    layer.alpha_composite(tex)
    # Sunset corner: sun, palms and horizon, fading into the background.
    x0, y0, x1, y1 = SUN
    scene = _fade_edges(T.sunset_scene(x1 - x0, y1 - y0, horizon=0.72), left=120, bottom=34)
    layer.alpha_composite(scene, (x0, y0))
    _frame_decor(ImageDraw.Draw(layer))
    return layer


def _frame_decor(d) -> None:
    T.crosshair(d, (DISPLAY_W - 26, 22), 8, T.rgba(T.PINK))
    d.rounded_rectangle(
        (6, 6, DISPLAY_W - 7, DISPLAY_H - 7), 14, outline=T.rgba((150, 110, 210), 180), width=2
    )


@cache
def _static(style: str) -> np.ndarray:
    from PIL import Image, ImageDraw

    layer = _background()
    # Camera: thin pale frame with a soft glow in the style's colour, brackets outside.
    d = ImageDraw.Draw(layer)
    d.rectangle(CAM, fill=T.rgba((8, 6, 18)))
    _gradient_outline(layer, CAM, 2, (STYLES[style][1], STYLES[style][1]), 2, 9)
    d = ImageDraw.Draw(layer)
    d.rectangle(CAM, outline=T.rgba(FRAME_LINE), width=2)
    x0, y0, x1, y1 = CAM
    T.corner_brackets(d, (x0 - 10, y0 - 10, x1 + 10, y1 + 10), 34, T.rgba(T.CREAM), 3)
    # Glass panel: translucent, cyan neon edge, sunset along the bottom.
    kind = "setup" if style in ("setup", "ready") else "drill"
    painted = BACKGROUND.exists()
    glass = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    alpha = GLASS_ALPHA[kind] if painted else 214
    ImageDraw.Draw(glass).rounded_rectangle(PANEL, 18, fill=T.rgba((26, 14, 52), alpha))
    layer.alpha_composite(glass)
    if not painted:
        height, horizon, sun = PANEL_ART[kind]
        _panel_art(layer, PANEL, height, 18, horizon, sun)
    _gradient_outline(layer, PANEL, 18, (T.CYAN, T.TEAL, (120, 150, 255)), 3, 10, horizontal=False)
    d = ImageDraw.Draw(layer)
    for cx in (PANEL[0] + 30, PANEL[2] - 30):
        T.crosshair(d, (cx, PANEL[3] - 30), 6, T.rgba(T.CYAN, 200), 1)
    # Meter boxes with orange / pink outlines.
    for box, stops in ((METERS, (T.ORANGE, T.GOLD)), (SIGNAL, (T.ORANGE, T.PINK))):
        d.rectangle(box, fill=T.rgba((24, 12, 40)))
        _gradient_outline(layer, box, 3, stops, 2, 5)
    d = ImageDraw.Draw(layer)
    d.text(
        ((METERS[0] + METERS[2]) // 2, METERS[3] - 7), "TRACKING", font=T.font("body", 12),
        fill=T.rgba(T.ORANGE), anchor="ms",
    )  # fmt: skip
    lbl = _vertical_label("SIGNAL", 12, T.ORANGE)
    layer.alpha_composite(lbl, (SIGNAL[0] + 7, (SIGNAL[1] + SIGNAL[3]) // 2 - lbl.height // 2))
    d.line(
        (SIGNAL[0] + 26, SIGNAL[1] + 8, SIGNAL[0] + 26, SIGNAL[3] - 8), fill=T.rgba(T.ORANGE, 160), width=1
    )
    d.rounded_rectangle(PROGRESS, 4, fill=T.rgba((20, 10, 36)))
    return _bgr(layer)


# --- per frame --------------------------------------------------------------------------


def render(
    frame, pose, title, sub, style, progress, panel: Panel, hud: Hud, skeleton: bool = True
) -> np.ndarray:
    """One drill frame. style: action, idle, setup or ready."""
    import cv2

    out = _static(style).copy()
    x0, y0, x1, y1 = CAM
    inset = 2
    aw, ah = x1 - x0 - 2 * inset, y1 - y0 - 2 * inset
    fh, fw = frame.shape[:2]
    k = min(aw / fw, ah / fh)
    img = cv2.resize(frame, (int(fw * k), int(fh * k)), interpolation=cv2.INTER_LINEAR)
    if skeleton and pose is not None:
        pts = pose.img.copy()
        pts[:, :2] *= k
        T.neon_skeleton(img, pts, EDGES, JOINTS)
    img = cv2.flip(img, 1)  # mirror, so your left is on the left
    h, w = img.shape[:2]
    vx, vy = x0 + inset + (aw - w) // 2, y0 + inset + (ah - h) // 2
    out[vy : vy + h, vx : vx + w] = img
    if progress is not None:
        px0, py0, px1, py1 = PROGRESS
        n = int((px1 - px0) * min(1.0, max(0.0, progress)))
        if n > 0:
            out[py0:py1, px0 : px0 + n] = T.gradient(px1 - px0, py1 - py0, [T.PINK, T.ORANGE])[:, :n]
    c = T.Canvas(out)
    head = T.fitted_text_image(
        title, 60, SUN[0] - TITLE_XY[0] - 10, stops=(STYLES[style][0],), shadow=TITLE_SHADOW
    )
    c.paste(head, TITLE_XY)
    _subtitle(c, sub, (TITLE_XY[0] + 4, 88), SUN[0] - TITLE_XY[0] - 30)
    _viewfinder(c, (vx, vy, vx + w, vy + h), hud)
    _panel(c, panel)
    _meters(c, hud)
    return c.bgr()


def _subtitle(c: T.Canvas, sub: str, xy: tuple[int, int], max_width: int) -> None:
    """Teal, except warnings (OUT OF VIEW) in pink."""
    parts = [p for p in sub.split("   ") if p]
    text = "   ".join(parts)
    size = T.fit_size(text, "body", 21, max_width)
    x, y = xy
    for i, part in enumerate(parts):
        piece = ("   " if i else "") + part
        c.draw.text(
            (x, y), piece, font=T.font("body", size), fill=T.PINK if "OUT OF VIEW" in part else T.CYAN
        )
        x += T.text_width(piece, "body", size)


def _fps_bars(d, x: int, y: int, level: float) -> None:
    """Four rising bars lit by frame rate (all at 30 fps); orange when the feed is slow."""
    color = T.CREAM if level >= 0.8 else T.ORANGE
    for k in range(4):
        h = 4 + 3 * k
        lit = level >= (k + 0.5) / 4
        d.rectangle((x + k * 6, y + 13 - h, x + k * 6 + 3, y + 13), fill=color if lit else (90, 70, 120))


def _viewfinder(c: T.Canvas, box, hud: Hud) -> None:
    d = c.draw
    x0, y0, x1, y1 = box
    mono = T.font("body", 17)
    if hud.recording:
        d.ellipse((x0 + 22, y0 + 23, x0 + 34, y0 + 35), fill=(255, 60, 70))
        d.text((x0 + 42, y0 + 19), f"REC  {hud.size[1]}p", font=mono, fill=T.CREAM)
    else:
        d.ellipse((x0 + 22, y0 + 23, x0 + 34, y0 + 35), outline=T.CREAM, width=2)
        d.text((x0 + 42, y0 + 19), f"STANDBY  {hud.size[1]}p", font=mono, fill=T.CREAM)
    fps = f"{hud.fps:.0f} FPS"
    d.text((x1 - 22, y0 + 19), fps, font=mono, fill=T.CREAM, anchor="ra")
    _fps_bars(d, x1 - 32 - T.text_width(fps, "body", 17) - 22, y0 + 22, min(1.0, hud.fps / 30))
    cx = (x0 + x1) // 2
    d.polygon([(cx - 6, y0 + 12), (cx + 6, y0 + 12), (cx, y0 + 19)], fill=T.CREAM)
    small = T.font("body", 15)
    d.text((x0 + 22, y1 - 34), f"FRAME {hud.frames:05d}", font=small, fill=T.CREAM)
    m, s = divmod(int(hud.elapsed_s), 60)
    d.text((x1 - 70, y1 - 34), f"{m:02d}:{s:02d}", font=small, fill=T.CREAM, anchor="ra")
    for i in range(3):  # dot grid
        for j in range(2):
            d.rectangle((x1 - 56 + i * 6, y1 - 30 + j * 6, x1 - 53 + i * 6, y1 - 27 + j * 6), fill=T.CREAM)
    fx0, fy0, fx1, fy1 = x1 - 84, y1 - 92, x1 - 44, y1 - 58  # focus box
    for px, py, dx, dy in ((fx0, fy0, 1, 1), (fx1, fy0, -1, 1), (fx0, fy1, 1, -1), (fx1, fy1, -1, -1)):
        d.line([(px, py + dy * 8), (px, py), (px + dx * 8, py)], fill=T.CREAM, width=2)


def _mark(d, cx: float, cy: float, ok: bool) -> None:
    if ok:
        d.line([(cx - 7, cy), (cx - 2, cy + 6), (cx + 8, cy - 6)], fill=T.MINT, width=3)
    else:
        d.line((cx - 6, cy - 6, cx + 6, cy + 6), fill=T.PINK, width=3)
        d.line((cx - 6, cy + 6, cx + 6, cy - 6), fill=T.PINK, width=3)


def _row(c: T.Canvas, x: int, y: int, w: int, text: str, ok: bool, h: int = 34, size: int = 16) -> int:
    """Dark pill with a thin outline (cyan when good, pink when not) and a check mark."""
    d = c.draw
    tx = x + 38
    size = T.fit_size(text, "body", size, w - 52)
    pill_w = min(w, tx - x + T.text_width(text, "body", size) + 18)
    d.rounded_rectangle(
        (x, y, x + pill_w, y + h), h // 2, fill=(22, 14, 44), outline=T.CYAN if ok else T.PINK, width=2
    )
    _mark(d, x + 20, y + h / 2, ok)
    d.text((tx, y + h // 2), text, font=T.font("body", size), fill=T.CREAM, anchor="lm")
    return y + h


def _section(c: T.Canvas, x: int, y: int, w: int, text: str) -> int:
    c.draw.text((x, y), text, font=T.font("body", 15), fill=T.PINK)
    tw = T.text_width(text, "body", 15)
    c.draw.line((x + tw + 10, y + 10, x + w, y + 10), fill=(110, 60, 150), width=1)
    return y + 25


def _bar(c: T.Canvas, x: int, y: int, w: int, label: str, v: float, signed: bool = False) -> int:
    d = c.draw
    d.text((x, y), label, font=T.font("body", 17), fill=T.LAVENDER)
    bx0, bx1, top, bottom = x + 150, x + w, y + 3, y + 21
    d.rounded_rectangle((bx0, top, bx1, bottom), 9, fill=(14, 10, 30), outline=(110, 60, 150), width=2)
    v = max(-1.0, min(1.0, v)) if signed else max(0.0, min(1.0, v))
    if signed:
        mid = (bx0 + bx1) // 2
        end = int(mid + v * (bx1 - mid - 3))
        if abs(end - mid) > 2:
            d.rounded_rectangle((min(mid, end), top + 3, max(mid, end), bottom - 3), 6, fill=T.CYAN)
        d.line((mid, top - 2, mid, bottom + 2), fill=T.CREAM, width=2)
    elif v > 0:
        d.rounded_rectangle(
            (bx0 + 3, top + 3, int(bx0 + 3 + v * (bx1 - bx0 - 6)), bottom - 3), 6, fill=T.ORANGE
        )
    return bottom + 6


def _panel(c: T.Canvas, p: Panel) -> None:
    x0, y0, x1, _ = PANEL
    x, y, w = x0 + 22, y0 + 14, x1 - x0 - 44
    head = T.fitted_text_image(p.header.upper(), 40, w, stops=(T.GOLD, T.ORANGE, T.PINK), italic=0.2)
    c.paste(head, (x, y))
    y += head.height + 10
    if p.checks is not None:
        for text, ok in p.checks:
            y = _row(c, x, y, w, text.upper(), ok) + 9
    st = p.state
    if st is not None:
        y = _section(c, x, y, w, "BODY")
        d = c.draw
        f = T.font("body", 19)
        tracked = "TRACKED" if st.visible else "NOT FOUND"
        d.text((x, y), tracked, font=f, fill=T.MINT if st.visible else T.PINK)
        ax = x + T.text_width(tracked, "body", 19) + 14
        d.text(
            (ax, y), "AIMING" if st.aiming else "not aiming", font=f, fill=T.MINT if st.aiming else T.LAVENDER
        )
        d.text((x + w, y), f"{st.cadence_hz:.1f} steps/s" + ("  SPRINT" if st.sprinting else ""),
               font=f, fill=T.CREAM, anchor="ra")  # fmt: skip
        y += 30
        y = _bar(c, x, y, w, "RUN", st.run_stick)
        y = _bar(c, x, y, w, f"LEAN {st.lean_deg:+.0f}°", st.lean_stick, signed=True) + 2
    if p.events is not None:
        y = _section(c, x, y, w, "DETECTED")
        if not p.events:
            c.draw.text((x, y), "nothing yet", font=T.font("body", 17), fill=T.LAVENDER)
            y += 30
        for age, name, ok in p.events[:3]:
            y = _row(c, x, y, w, f"{name.replace('_', ' ').upper()}   {age:.1f}s ago", ok, h=30, size=15) + 5
    if p.tally:
        y = _section(c, x, y + 2, w, "SCORE")
        for name, sc in list(p.tally.items())[:3]:
            hits = f"{sc.hits}/{sc.expected}" if sc.expected else "-"
            line = f"{name.replace('_', ' '):<10}{hits:>6}  false {sc.false}"
            c.draw.text((x, y), line, font=T.font("mono", 18), fill=T.CREAM)
            y += 24


def _meters(c: T.Canvas, hud: Hud) -> None:
    d = c.draw
    x0, y0, x1, y1 = METERS
    n = len(METER_PARTS)
    slot = (x1 - x0 - 20) / n
    base, top = y1 - 36, y0 + 10
    tiny = T.font("body", 10)
    for i, name in enumerate(METER_PARTS):
        v = hud.parts.get(name, 0.0)
        bx = x0 + 10 + i * slot
        d.rectangle((bx + 3, top, bx + slot - 3, base), fill=(44, 24, 60))
        if v > 0.02:
            col = T.GOLD if v >= 0.8 else T.ORANGE if v >= 0.5 else T.PINK
            d.rectangle((bx + 3, base - int(v * (base - top)), bx + slot - 3, base), fill=col)
        d.text(
            (bx + slot / 2, base + 3),
            name,
            font=tiny,
            fill=T.CREAM if name in REQUIRED else T.LAVENDER,
            anchor="ma",
        )
    x0, y0, x1, y1 = SIGNAL
    cols, rows = 10, 6
    gx0, gy0 = x0 + 34, y0 + 9
    cell = min((x1 - 8 - gx0) / cols, (y1 - 9 - gy0) / rows)
    q = list(hud.quality)[-cols * rows :]
    q = [None] * (cols * rows - len(q)) + q
    for k, v in enumerate(q):
        r, ci = divmod(k, cols)
        cx, cy = gx0 + ci * cell, gy0 + r * cell
        col = (
            (44, 24, 60)
            if v is None
            else T.GOLD
            if v >= 0.8
            else T.ORANGE
            if v >= 0.5
            else T.MAGENTA
            if v > 0
            else T.PINK
        )
        d.rectangle((cx + 1.5, cy + 1.5, cx + cell - 1.5, cy + cell - 1.5), fill=col)


# --- report -----------------------------------------------------------------------------


def report_image(report: str, complete: bool) -> np.ndarray:
    from PIL import Image, ImageDraw

    layer = _background()
    box = (CAM[0], CAM[1], PANEL[2], METERS[3])
    glass = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(glass).rounded_rectangle(box, 18, fill=T.rgba((26, 14, 52), 205))
    layer.alpha_composite(glass)
    if not BACKGROUND.exists():
        _panel_art(layer, box, 240, 18)
    _gradient_outline(layer, box, 18, (T.CYAN, T.TEAL, (120, 150, 255)), 3, 10, horizontal=False)
    c = T.Canvas(_bgr(layer))
    title = "DRILL COMPLETE" if complete else "DRILL STOPPED"
    head = T.fitted_text_image(title, 64, SUN[0] - 60, stops=(T.GOLD, T.ORANGE, T.PINK), italic=0.2)
    c.paste(head, (TITLE_XY[0], 14))
    y = box[1] + 30
    for i, line in enumerate(report.splitlines()):
        size = T.fit_size(line, "mono", 28, box[2] - box[0] - 80)
        c.draw.text((box[0] + 40, y), line, font=T.font("mono", size), fill=T.PINK if i == 0 else T.CREAM)
        y += 42
    c.draw.text((box[0] + 40, box[3] - 44), "any key to close", font=T.font("body", 20), fill=T.LAVENDER)
    return c.bgr()


# --- window -----------------------------------------------------------------------------


def open_window(window: str) -> None:
    """Resizable, keeps its shape, and opens at ~90 % of the screen."""
    import cv2

    cv2.namedWindow(window, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    sw, sh = screen_size()
    k = min(0.92 * sw / DISPLAY_W, 0.85 * sh / DISPLAY_H)
    cv2.resizeWindow(window, int(DISPLAY_W * k), int(DISPLAY_H * k))


def screen_size() -> tuple[int, int]:
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            pass
    return 1600, 900


def toggle_fullscreen(window: str) -> None:
    import cv2

    full = cv2.getWindowProperty(window, cv2.WND_PROP_FULLSCREEN) == cv2.WINDOW_FULLSCREEN
    cv2.setWindowProperty(
        window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL if full else cv2.WINDOW_FULLSCREEN
    )


def show(window: str, img: np.ndarray, wait_ms: int = 1) -> int:
    import cv2

    cv2.imshow(window, img)
    return cv2.waitKey(wait_ms) & 0xFF
