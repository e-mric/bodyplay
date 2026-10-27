"""Bodyplay's on-screen look: a Vice City sunset palette (deep purple, hot pink,
orange, teal) with heavy gradient display type, neon outlines, palms and a striped
retro sun. Colours and mood only; no game logos, fonts or artwork.

Drawing primitives only (layouts live with the tools that use them). Text and
shapes go through Pillow so real bold fonts, gradients and glows are possible;
images handed back to OpenCV are BGR arrays.
"""

from __future__ import annotations

import math
import sys
from functools import cache, lru_cache
from pathlib import Path

import numpy as np

RGB = tuple[int, int, int]

NAVY: RGB = (16, 12, 34)
NIGHT: RGB = (30, 12, 52)
DEEP: RGB = (44, 20, 76)
PLUM: RGB = (88, 32, 118)
PINK: RGB = (255, 56, 150)
MAGENTA: RGB = (232, 70, 220)
CORAL: RGB = (255, 104, 112)
ORANGE: RGB = (255, 150, 60)
GOLD: RGB = (255, 210, 96)
TEAL: RGB = (52, 214, 210)
CYAN: RGB = (90, 228, 255)
MINT: RGB = (90, 236, 140)
LAVENDER: RGB = (200, 172, 255)
CREAM: RGB = (255, 244, 232)
SILHOUETTE: RGB = (36, 14, 60)

_FONT_FILES = {
    "display": {
        "win32": ["ariblk.ttf", "seguibl.ttf", "impact.ttf"],
        "darwin": ["Arial Black.ttf", "Impact.ttf"],
        "linux": ["DejaVuSans-Bold.ttf"],
    },
    "body": {
        "win32": ["segoeuib.ttf", "arialbd.ttf"],
        "darwin": ["Arial Bold.ttf", "Helvetica.ttc"],
        "linux": ["DejaVuSans-Bold.ttf"],
    },
    "mono": {
        "win32": ["consolab.ttf", "consola.ttf", "cour.ttf"],
        "darwin": ["Menlo.ttc", "Courier New.ttf"],
        "linux": ["DejaVuSansMono-Bold.ttf", "DejaVuSansMono.ttf"],
    },
}
_FONT_DIRS = {
    "win32": [Path("C:/Windows/Fonts")],
    "darwin": [
        Path("/System/Library/Fonts/Supplemental"),
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
    ],
    "linux": [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/TTF")],
}


def bgr(c: RGB) -> tuple[int, int, int]:
    return (c[2], c[1], c[0])


def rgba(c: RGB, a: int = 255) -> tuple[int, int, int, int]:
    return (c[0], c[1], c[2], a)


@cache
def font(role: str, size: int):
    from PIL import ImageFont

    plat = sys.platform if sys.platform in _FONT_DIRS else "linux"
    for name in _FONT_FILES[role][plat]:
        for d in _FONT_DIRS[plat]:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size)


def text_width(text: str, role: str, size: int) -> int:
    return int(font(role, size).getlength(text))


def fit_size(text: str, role: str, size: int, max_width: int | None) -> int:
    while max_width and size > 9 and text_width(text, role, size) > max_width:
        size = int(size * 0.93)
    return size


# --- gradients ----------------------------------------------------------------------


def gradient(w: int, h: int, stops: list[RGB], horizontal: bool = True) -> np.ndarray:
    """BGR image blending through the colour stops (cached and read-only: copy to edit)."""
    return _gradient(w, h, tuple(stops), horizontal)


@cache
def _gradient(w: int, h: int, stops: tuple[RGB, ...], horizontal: bool) -> np.ndarray:
    n = w if horizontal else h
    xs = np.linspace(0, 1, n)
    pos = np.linspace(0, 1, len(stops))
    line = np.stack([np.interp(xs, pos, [bgr(c)[k] for c in stops]) for k in range(3)], axis=1)
    img = np.repeat(line[None], h, axis=0) if horizontal else np.repeat(line[:, None], w, axis=1)
    img = np.ascontiguousarray(img.astype(np.uint8))
    img.flags.writeable = False
    return img


def pil_gradient(w: int, h: int, stops: list[RGB], horizontal: bool = False):
    from PIL import Image

    return Image.fromarray(np.ascontiguousarray(gradient(w, h, stops, horizontal)[:, :, ::-1]))


# --- text -----------------------------------------------------------------------------


@lru_cache(maxsize=512)
def text_image(
    text: str,
    size: int,
    stops: tuple[RGB, ...] = (CREAM, PINK),
    role: str = "display",
    outline: RGB | None = NAVY,
    shadow: RGB | None = PLUM,
    italic: float = 0.0,
):
    """RGBA image of `text`: vertical gradient fill, dark outline, offset shadow and an
    optional italic slant. Cached, so titles that repeat every frame cost nothing."""
    from PIL import Image, ImageDraw

    f = font(role, size)
    sw = max(2, size // 11) if outline else 0
    so = max(2, size // 13) if shadow else 0
    left, top, right, bottom = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox(
        (0, 0), text, font=f, stroke_width=sw
    )
    w, h = right - left + so + 2, bottom - top + so + 2
    o = (1 - left, 1 - top)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shadow:
        d.text(
            (o[0] + so, o[1] + so), text, font=f, fill=rgba(shadow), stroke_width=sw, stroke_fill=rgba(shadow)
        )
    if outline:
        d.text(o, text, font=f, fill=rgba(outline), stroke_width=sw, stroke_fill=rgba(outline))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text(o, text, font=f, fill=255)
    img.paste(pil_gradient(w, h, list(stops)), (0, 0), mask)
    if italic:
        extra = int(italic * h)
        img = img.transform(
            (w + extra, h), Image.Transform.AFFINE, (1, italic, -extra, 0, 1, 0), Image.Resampling.BICUBIC
        )
    return img


def fitted_text_image(text: str, size: int, max_width: int, **kw):
    while size > 10:
        img = text_image(text, size, **kw)
        if img.width <= max_width:
            return img
        size = int(size * 0.93)
    return text_image(text, size, **kw)


# --- shapes ---------------------------------------------------------------------------


def glow_outline(layer, box, radius: int, color: RGB, width: int = 3, glow: int = 10) -> None:
    """Neon rounded outline with a soft halo, composited onto an RGBA layer."""
    from PIL import Image, ImageDraw, ImageFilter

    halo = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).rounded_rectangle(box, radius, outline=rgba(color, 200), width=width + 4)
    halo = halo.filter(ImageFilter.GaussianBlur(glow))
    layer.alpha_composite(halo)
    ImageDraw.Draw(layer).rounded_rectangle(box, radius, outline=rgba(color), width=width)


def corner_brackets(draw, box, length: int, color, width: int = 3) -> None:
    x0, y0, x1, y1 = box
    for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        draw.line([(cx, cy + dy * length), (cx, cy), (cx + dx * length, cy)], fill=color, width=width)


def crosshair(draw, center, r: int, color, width: int = 2) -> None:
    x, y = center
    draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=width)
    draw.line((x - r - 4, y, x + r + 4, y), fill=color, width=width)
    draw.line((x, y - r - 4, x, y + r + 4), fill=color, width=width)


def palm(draw, x: float, y: float, h: float, lean: float, color) -> None:
    """Palm tree silhouette: curved tapering trunk and drooping fronds, base at (x, y)."""
    n = 14
    left, right = [], []
    for i in range(n + 1):
        t = i / n
        cx, cy = x + lean * h * t * t, y - h * t
        half = h * (0.04 - 0.018 * t)
        left.append((cx - half, cy))
        right.append((cx + half, cy))
    draw.polygon(left + right[::-1], fill=color)
    tx, ty = x + lean * h, y - h
    for ang, length, droop in (
        (8, 0.55, 0.9), (35, 0.5, 0.7), (70, 0.38, 0.5), (110, 0.38, 0.5),
        (145, 0.5, 0.7), (172, 0.55, 0.9), (-25, 0.42, 0.9), (205, 0.42, 0.9),
    ):  # fmt: skip
        a, L = math.radians(ang), length * h
        up, down = [], []
        for i in range(13):
            t = i / 12
            px = tx + math.cos(a) * L * t
            py = ty - math.sin(a) * L * t + droop * L * t * t
            w = L * 0.11 * math.sin(math.pi * min(1.0, t * 1.15))
            up.append((px, py - w))
            down.append((px, py + w))
        draw.polygon(up + down[::-1], fill=color)
    draw.ellipse((tx - h * 0.03, ty - h * 0.02, tx + h * 0.03, ty + h * 0.04), fill=color)


def retro_sun(diameter: int, stops: tuple[RGB, ...] = (GOLD, ORANGE, PINK)):
    """RGBA disc with a vertical gradient and horizontal gaps widening toward the bottom."""
    from PIL import Image, ImageDraw

    sun = pil_gradient(diameter, diameter, list(stops)).convert("RGBA")
    mask = Image.new("L", (diameter, diameter), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    y, gap = diameter * 0.5, 2.0
    while y < diameter:
        md.rectangle((0, y, diameter, y + gap), fill=0)
        y += gap + diameter * 0.07
        gap *= 1.35
    sun.putalpha(mask)
    return sun


def sunset_scene(w: int, h: int, horizon: float = 0.7, palms: bool = True, sun: float = 0.45):
    """RGBA scene: sky fading in from transparent, striped sun on the horizon, palm
    silhouettes and a neon perspective floor. `sun` = sun diameter / width."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hy = int(h * horizon)
    sky = pil_gradient(w, hy, [DEEP, PLUM, PINK, ORANGE]).convert("RGBA")
    ramp = np.clip(np.linspace(-0.2, 1.0, hy), 0, 1) ** 1.3 * 235
    sky.putalpha(Image.fromarray(np.repeat(ramp[:, None], w, axis=1).astype(np.uint8)))
    img.alpha_composite(sky)
    d = int(min(w * sun, hy * 1.2))
    sun = retro_sun(d)
    sun = sun.crop((0, 0, d, min(d, hy - (hy - d // 2) + d // 2)))
    img.alpha_composite(sun, (w // 2 - d // 2, max(0, hy - d // 2 - d // 4)))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, hy, w, h), fill=rgba(NIGHT, 245))
    for i in range(-8, 9):  # floor lines converging on the horizon
        draw.line((w / 2 + i * w * 0.03, hy, w / 2 + i * w * 0.22, h), fill=rgba(MAGENTA, 120), width=1)
    y, step = hy + 3, 3.0
    while y < h:
        draw.line((0, y, w, y), fill=rgba(MAGENTA, 110), width=1)
        y += step
        step *= 1.45
    draw.line((0, hy, w, hy), fill=rgba(PINK, 220), width=2)
    if palms:
        for x, ph, lean in (
            (w * 0.1, hy * 0.95, 0.18),
            (w * 0.2, hy * 0.7, -0.12),
            (w * 0.88, hy * 0.9, -0.2),
        ):
            palm(draw, x, hy + 4, ph, lean, rgba(SILHOUETTE))
    return img


def round_mask(size: tuple[int, int], radius: int):
    from PIL import Image, ImageDraw

    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return m


def check_icon(draw, center, size: int, ok: bool) -> None:
    x, y = center
    s = size / 2
    if ok:
        draw.line(
            [(x - s * 0.8, y), (x - s * 0.2, y + s * 0.6), (x + s * 0.9, y - s * 0.6)], fill=MINT, width=4
        )
    else:
        draw.line((x - s * 0.6, y - s * 0.6, x + s * 0.6, y + s * 0.6), fill=PINK, width=4)
        draw.line((x - s * 0.6, y + s * 0.6, x + s * 0.6, y - s * 0.6), fill=PINK, width=4)


# --- skeleton -------------------------------------------------------------------------


def neon_skeleton(
    img: np.ndarray, pts: np.ndarray, edges, joint_colors: dict[int, RGB], min_vis: float = 0.5
) -> None:
    """Thin glowing bones whose colour fades from one joint's colour to the next, with
    small pink joints, in place on BGR."""
    import cv2

    segs = []
    used: set[int] = set()
    for a, b in edges:
        if pts[a, 2] <= min_vis or pts[b, 2] <= min_vis:
            continue
        pa, pb = pts[a, :2], pts[b, :2]
        ca, cb = np.array(joint_colors[a], float), np.array(joint_colors[b], float)
        n = 6
        for k in range(n):
            p0, p1 = pa + (pb - pa) * k / n, pa + (pb - pa) * (k + 1) / n
            col = ca + (cb - ca) * (k + 0.5) / n
            segs.append(((int(p0[0]), int(p0[1])), (int(p1[0]), int(p1[1])), col))
        used |= {a, b}
    if not segs:
        return
    glow = np.zeros_like(img)
    for p0, p1, col in segs:
        cv2.line(glow, p0, p1, bgr(tuple(int(v) for v in col)), 6, cv2.LINE_AA)
    cv2.add(img, cv2.GaussianBlur(glow, (0, 0), 4), dst=img)
    for p0, p1, col in segs:
        core = tuple(int(min(255, v + 60)) for v in col)
        cv2.line(img, p0, p1, bgr(core), 2, cv2.LINE_AA)
    for i in used:
        p = (int(pts[i, 0]), int(pts[i, 1]))
        cv2.circle(img, p, 4, bgr(PINK), -1, cv2.LINE_AA)
        cv2.circle(img, p, 1, bgr(CREAM), -1, cv2.LINE_AA)


# --- canvas ---------------------------------------------------------------------------


class Canvas:
    """Pillow drawing on a BGR image: wrap, draw, then `bgr()` to get the image back."""

    def __init__(self, img: np.ndarray):
        from PIL import Image, ImageDraw

        self.image = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1]))
        self.draw = ImageDraw.Draw(self.image)

    def paste(self, rgba_img, xy: tuple[int, int]) -> None:
        self.image.paste(rgba_img, xy, rgba_img)

    def text(
        self,
        xy: tuple[int, int],
        text: str,
        size: int,
        fill: RGB = CREAM,
        role: str = "body",
        max_width: int | None = None,
        outline: bool = False,
        anchor: str = "la",
    ) -> None:
        size = fit_size(text, role, size, max_width)
        stroke = max(1, size // 10) if outline else 0
        self.draw.text(
            xy, text, font=font(role, size), fill=fill, stroke_width=stroke, stroke_fill=NAVY, anchor=anchor
        )

    def bgr(self) -> np.ndarray:
        return np.asarray(self.image)[:, :, ::-1].copy()
