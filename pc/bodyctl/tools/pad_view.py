"""Controller picture for the drill and live windows: which buttons the body is pressing.

The pictures (assets/pads/*.png) were rendered once, offline (pyvista + trimesh), from
the 3D controller models in the repo's assets/ folder: the Xbox one from 40 degrees above
its face, the DualSense from the front. The models are one mesh each, so the hotspots
below were placed by hand on the renders, in pixels of the 760 px wide render. Pressed buttons
glow, sticks show their deflection, and the shoulder buttons and triggers (out of sight
from the front) are tabs above the controller; the triggers fill with how far they're
pulled.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from bodyctl.tools import theme as T

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "pads"
BAND = 0.2  # tab band above the picture, as a share of the picture's height
GLOW = T.CYAN
SHOULDERS = ("l2", "l1", "r1", "r2")  # tabs, left to right


@dataclass(frozen=True)
class Pad:
    image: str
    w: int  # picture size the hotspots were measured on, px
    h: int
    spots: dict[str, tuple[float, float, float]]  # control -> (x, y, radius) in px of w x h
    labels: dict[str, str]  # shoulder tab labels
    credit: str = ""  # shown under the picture: model, author, licence (assets/README.md)


# Report control names are PlayStation's (the Xbox backend maps cross -> A and so on).
PADS = {
    "xbox": Pad(
        "xbox.png", 760, 538,
        {
            "cross": (580, 197, 25), "circle": (630, 148, 25), "square": (527, 146, 25),
            "triangle": (579, 95, 25),
            "dpad_up": (275, 234, 21), "dpad_down": (275, 307, 21), "dpad_left": (239, 270, 21),
            "dpad_right": (311, 270, 21),
            "create": (323, 143, 15), "options": (437, 143, 15), "ps": (379, 70, 27),
            "left_stick": (181, 143, 47), "right_stick": (483, 258, 47),
        },
        {"l2": "LT", "l1": "LB", "r1": "RB", "r2": "RT"},
        credit='3D model "Xbox Inalambric Controller (White)" by Chistodrako._., CC BY 4.0',
    ),
    "dualsense": Pad(
        "dualsense.png", 760, 483,
        {
            "cross": (643, 232, 27), "circle": (708, 170, 27), "square": (584, 170, 27),
            "triangle": (648, 108, 27),
            "dpad_up": (118, 120, 24), "dpad_down": (115, 215, 24), "dpad_left": (70, 168, 24),
            "dpad_right": (160, 170, 24),
            "create": (178, 75, 13), "options": (580, 75, 13), "ps": (382, 322, 13),
            "left_stick": (255, 300, 52), "right_stick": (510, 300, 52),
        },
        {"l2": "L2", "l1": "L1", "r1": "R1", "r2": "R2"},
        credit='3D model "Playstation 5 Dualsense" by AHarmlessPotato, CC BY 4.0',
    ),
}  # fmt: skip
STICK_PRESS = {"l3": "left_stick", "r3": "right_stick"}


@cache
def _base(kind: str, width: int):
    """The controller picture scaled to `width`, under a transparent band for the tabs."""
    from PIL import Image

    pad = PADS[kind]
    pic = Image.open(ASSETS / pad.image).convert("RGBA")
    h = round(pad.h * width / pad.w)
    pic = pic.resize((width, h), Image.LANCZOS)
    band = round(BAND * h)
    out = Image.new("RGBA", (width, h + band), (0, 0, 0, 0))
    out.paste(pic, (0, band), pic)
    return out, width / pad.w, band


@cache
def _glow(kind: str, width: int, control: str):
    """A soft neon disc for one pressed control, the size of the whole picture."""
    from PIL import Image, ImageDraw, ImageFilter

    base, k, band = _base(kind, width)
    x, y, r = PADS[kind].spots[control]
    x, y, r = x * k, y * k + band, r * k
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    halo = r * 1.45
    d.ellipse((x - halo, y - halo, x + halo, y + halo), outline=(*GLOW, 220), width=max(3, round(r * 0.5)))
    layer = layer.filter(ImageFilter.GaussianBlur(max(2.0, r * 0.35)))
    d = ImageDraw.Draw(layer)
    d.ellipse((x - r, y - r, x + r, y + r), fill=(*GLOW, 70))  # tint: the button's label shows through
    d.ellipse((x - r, y - r, x + r, y + r), outline=(*T.CREAM, 255), width=max(2, round(r * 0.14)))
    return layer


def render(kind: str, width: int, buttons: frozenset[str] | set[str], left, right, l2: float, r2: float):
    """RGBA picture of the controller with `buttons` lit, the sticks at `left` / `right`
    (x, y in -1..1, y up) and the triggers at l2 / r2 (0..1)."""
    from PIL import Image, ImageDraw

    base, k, band = _base(kind, width)
    pad = PADS[kind]
    img = base.copy()
    lit = {STICK_PRESS.get(b, b) for b in buttons}
    sticks = {"left_stick": left, "right_stick": right}
    for name, (sx, sy) in sticks.items():
        if abs(sx) > 0.05 or abs(sy) > 0.05:
            lit.add(name)
    for name in lit:
        if name in pad.spots:
            img = Image.alpha_composite(img, _glow(kind, width, name))
    d = ImageDraw.Draw(img)
    for name, (sx, sy) in sticks.items():  # where each stick points
        x, y, r = pad.spots[name]
        cx, cy = x * k + sx * r * k * 0.8, y * k + band - sy * r * k * 0.8
        dot = max(3, round(r * k * 0.28))
        moved = abs(sx) > 0.05 or abs(sy) > 0.05
        d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=T.PINK if moved else (150, 140, 170))
    _tabs(d, pad, width, band, buttons, l2, r2)
    return img


def _tabs(d, pad: Pad, width: int, band: int, buttons, l2: float, r2: float) -> None:
    """Shoulder buttons and triggers as pills in the band above the controller."""
    h = max(12, round(band * 0.62))
    y0 = (band - h) // 2
    gap = width * 0.03
    tab_w = (width - 5 * gap) / 4
    size = max(9, round(h * 0.62))
    for i, name in enumerate(SHOULDERS):
        x0 = gap + i * (tab_w + gap)
        if i >= 2:
            x0 += gap  # the middle stays open, like the controller's top edge
        else:
            x0 -= gap / 2
        box = (x0, y0, x0 + tab_w, y0 + h)
        value = {"l2": l2, "r2": r2}.get(name, 1.0 if name in buttons else 0.0)
        on = value > 0.05
        d.rounded_rectangle(box, h // 2, fill=(22, 14, 44), outline=GLOW if on else (110, 60, 150), width=2)
        if on:
            fill_w = max(h, (tab_w - 4) * min(1.0, value))
            d.rounded_rectangle((x0 + 2, y0 + 2, x0 + 2 + fill_w, y0 + h - 2), (h - 4) // 2, fill=T.ORANGE)
        d.text(((box[0] + box[2]) / 2, y0 + h / 2), pad.labels[name], font=T.font("body", size),
               fill=T.CREAM, stroke_width=max(1, size // 8), stroke_fill=T.NAVY, anchor="mm")  # fmt: skip


def size(kind: str, width: int) -> tuple[int, int]:
    base, _, _ = _base(kind, width)
    return base.size


def width_for_height(kind: str, height: int) -> int:
    pad = PADS[kind]
    return int(height / ((1 + BAND) * pad.h / pad.w))
