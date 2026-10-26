"""latency-video on a generated 240 fps clip whose onset frames are known exactly.

Stands in for the M1 acceptance clip (20 hand-counted trials) until a real one
is filmed: the analyzer must land within +-1 frame of the truth.
"""

import csv

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from bodyctl.tools import latency_video as lv  # noqa: E402

FPS = 240
W, H = 160, 96
TELL = lv.Roi(10, 20, 30, 50)
LED = lv.Roi(60, 10, 20, 20)
SCREEN = lv.Roi(100, 10, 50, 70)


def make_clip(path, n_trials=20, spacing_s=2.5, seed=0, with_led=True):
    """Belt tape dips, LED flashes 20 ms, a block moves on the 'TV'. Returns truth per trial."""
    rng = np.random.default_rng(seed)
    n = int((n_trials * spacing_s + 1.5) * FPS)
    truth = []
    tape_dy = np.zeros(n)
    led_on = np.zeros(n, bool)
    block_dx = np.zeros(n)
    for k in range(n_trials):
        tell = int((1.0 + k * spacing_s) * FPS) + int(rng.integers(0, 30))
        led = tell + int(rng.integers(2, 60))  # 8 ms (Tier 2) .. 250 ms (Tier 0)
        scr = led + int(rng.integers(10, 30))  # one or two 60 fps game frames + TV
        truth.append((tell, led if with_led else None, scr))
        # Tape accelerates down (1, 3, 6, ... px), holds 0.4 s, comes back.
        for i in range(12):
            tape_dy[tell + i] = min(12, (i + 1) * (i + 2) / 2)
        tape_dy[tell + 12 : tell + 12 + int(0.4 * FPS)] = 12
        if with_led:
            led_on[led : led + 5] = True  # 20 ms at 240 fps
        for i in range(6):
            block_dx[scr + i : scr + int(0.5 * FPS)] = 3 * (i + 1)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for f in range(n):
        img = np.full((H, W), 40, np.float32)
        y = int(TELL.y + 15 + tape_dy[f])
        img[y : y + 6, TELL.x + 5 : TELL.x + 25] = 250
        if led_on[f]:
            img[LED.y + 6 : LED.y + 14, LED.x + 6 : LED.x + 14] = 255
        else:
            img[LED.y + 6 : LED.y + 14, LED.x + 6 : LED.x + 14] = 70
        img[SCREEN.y : SCREEN.y + SCREEN.h, SCREEN.x : SCREEN.x + SCREEN.w] = 90
        bx = int(SCREEN.x + 10 + block_dx[f])
        img[SCREEN.y + 20 : SCREEN.y + 40, bx : bx + 12] = 200
        img += rng.normal(0, 2.0, img.shape)
        frame = np.clip(img, 0, 255).astype(np.uint8)
        writer.write(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR))
    writer.release()
    return truth


def test_detect_onset_basic():
    sig = np.zeros(200)
    sig[120:] = 5.0
    assert lv.detect_onset(sig, (100, 200), (0, 100), k_sigma=6, floor=0.5) == 120
    assert lv.detect_onset(sig, (130, 200), (0, 100), k_sigma=6, floor=0.5) == 130
    assert lv.detect_onset(np.zeros(200), (100, 200), (0, 100), k_sigma=6, floor=0.5) is None


def test_single_frame_spike_ignored():
    sig = np.zeros(200)
    sig[110] = 9.0
    sig[150:] = 9.0
    assert lv.detect_onset(sig, (100, 200), (0, 100), k_sigma=6, hold_frames=2, floor=0.5) == 150


def test_roi_parse():
    assert lv.Roi.parse("1,2,3,4") == lv.Roi(1, 2, 3, 4)
    with pytest.raises(ValueError):
        lv.Roi.parse("1,2,0,4")


def test_clip_within_one_frame(tmp_path):
    video = tmp_path / "clip.avi"
    truth = make_clip(video)
    trials, summary, csv_path = lv.run(video, {"tell": TELL, "led": LED, "screen": SCREEN}, out_dir=tmp_path)
    assert len(trials) == len(truth)
    for t, (tell, led, scr) in zip(trials, truth, strict=True):
        assert t.led_frame is not None and abs(t.led_frame - led) <= 1
        assert t.tell_frame is not None and abs(t.tell_frame - tell) <= 1, (t, tell)
        assert t.screen_frame is not None and abs(t.screen_frame - scr) <= 1, (t, scr)
    assert summary["tell_to_led_ms"]["n"] == len(truth)
    rows = list(csv.DictReader(csv_path.open()))
    assert len(rows) == len(truth)
    assert (tmp_path / "clip_latency.png").exists()


def test_baseline_mode_without_led(tmp_path):
    video = tmp_path / "dualsense.avi"
    truth = make_clip(video, n_trials=8, with_led=False, seed=2)
    trials, _, _ = lv.run(video, {"tell": TELL, "screen": SCREEN}, out_dir=tmp_path)
    assert len(trials) == len(truth)
    for t, (tell, _, scr) in zip(trials, truth, strict=True):
        assert abs(t.tell_frame - tell) <= 1
        assert abs(t.screen_frame - scr) <= 1
        assert t.led_frame is None


def test_cli_saves_and_reuses_rois(tmp_path, capsys):
    from bodyctl.cli import main

    video = tmp_path / "tier2.avi"
    make_clip(video, n_trials=4, seed=5)
    roi_args = [
        f"--roi=tell={TELL.x},{TELL.y},{TELL.w},{TELL.h}",
        f"--roi=led={LED.x},{LED.y},{LED.w},{LED.h}",
    ]
    assert main(["latency-video", str(video), *roi_args]) == 0
    assert "4 trials" in capsys.readouterr().out
    assert lv.rois_path(video).exists()
    # Same framing, next chapter: reuse the saved ROIs without drawing them again.
    other = tmp_path / "tier3.avi"
    make_clip(other, n_trials=3, seed=6)
    assert main(["latency-video", str(other), "--rois-from", str(video)]) == 0
    assert "3 trials" in capsys.readouterr().out
