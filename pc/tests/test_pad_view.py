"""Controller picture: pressed buttons, sticks and triggers change the hotspot's pixels."""

import numpy as np
import pytest

from bodyctl.tools import pad_view as P


@pytest.mark.parametrize("kind", sorted(P.PADS))
def test_pressed_button_lights_its_hotspot_only(kind):
    width = 380
    idle = np.asarray(P.render(kind, width, frozenset(), (0, 0), (0, 0), 0, 0)).astype(int)
    lit = np.asarray(P.render(kind, width, frozenset({"cross"}), (0, 0), (0, 0), 0, 0)).astype(int)
    _, k, band = P._base(kind, width)
    x, y, r = P.PADS[kind].spots["cross"]
    cx, cy = round(x * k), round(y * k + band)
    assert np.abs(lit[cy, cx] - idle[cy, cx]).sum() > 30  # the button glows
    ox, oy, _ = P.PADS[kind].spots["dpad_left"]
    assert (
        np.abs(lit[round(oy * k + band), round(ox * k)] - idle[round(oy * k + band), round(ox * k)]).sum() < 5
    )


def test_trigger_tab_fills_and_stick_dot_moves():
    a = np.asarray(P.render("xbox", 380, frozenset(), (0, 0), (0, 0), 0, 0)).astype(int)
    b = np.asarray(P.render("xbox", 380, frozenset(), (0, 1), (0, 0), 1.0, 0)).astype(int)
    _, k, band = P._base("xbox", 380)
    assert np.abs(b[:band, : 380 // 4] - a[:band, : 380 // 4]).sum() > 1000  # LT tab filled
    x, y, r = P.PADS["xbox"].spots["left_stick"]
    top = round(y * k + band - 0.8 * r * k)
    assert np.abs(b[top, round(x * k)] - a[top, round(x * k)]).sum() > 30  # dot pushed up


def test_size_fits_height():
    for kind in P.PADS:
        w = P.width_for_height(kind, 200)
        assert P.size(kind, w)[1] <= 200
