import numpy as np
import pytest
from posegen import Timeline

from bodyctl.cli import main
from bodyctl.config.profile import load_profile
from bodyctl.gestures import webcam as W
from bodyctl.tools import webcam_tune as wt
from bodyctl.tools.drill import (
    EXPECT,
    LEAN,
    Cue,
    Recording,
    RecordingBuilder,
    View,
    build_script,
    describe_yaw,
    measure_view,
    view_checks,
)

GTA5 = "config/profiles/gta5.yaml"


def gestures():
    return load_profile(GTA5).gestures


def recording(tl: Timeline, cues: list[tuple[str, float, float]], angle: str = "front") -> Recording:
    t = np.array([tl.t_us(i) for i in range(tl.n)], np.int64)
    return Recording(
        {"angle": angle},
        t,
        tl.img.astype(np.float32),
        tl.world.astype(np.float32),
        [Cue(label, int(t0 * 1e6), int(t1 * 1e6)) for label, t0, t1 in cues],
    )


def test_script_has_reps_and_known_labels():
    s = build_script(["jump", "lean"], 3)
    labels = [st.label for st in s]
    assert labels.count("jump") == 3
    assert labels.count("lean_left") + labels.count("lean_right") == 3
    neutral = {"still", "gap", "center", "shift", "ready", "stop"}
    assert all(lb in EXPECT or lb in LEAN or lb in neutral for lb in labels)
    assert labels[0] == "still" and "shift" in labels


def test_save_load_roundtrip(tmp_path):
    tl = Timeline(1.0)
    b = RecordingBuilder({"angle": "left30"})
    poses = tl.poses()
    b.cue("still", poses[0].t_us)
    for i, p in enumerate(poses):
        b.add(p.t_us, None if i == 5 else p)
    path = b.build().save(tmp_path / "d.npz")
    rec = Recording.load(path)
    assert rec.header["angle"] == "left30"
    back = rec.poses()
    assert back[5] is None and back[6] is not None
    assert back[6].world is not None
    np.testing.assert_allclose(back[6].img, poses[6].img, atol=1e-3)
    assert rec.cues[0].label == "still" and rec.cues[0].end_us == poses[-1].t_us


def test_score_counts_hits_and_false_detections():
    tl = Timeline(11.0)
    tl.jump(2.0)
    tl.jump(5.0)
    tl.punch(8.0)
    rec = recording(
        tl,
        [("still", 0, 1.9), ("jump", 1.9, 3.4), ("gap", 3.4, 4.9), ("jump", 4.9, 6.4), ("gap", 6.4, 7.9),
         ("punch_right", 7.9, 9.1), ("gap", 9.1, 11.0)],
    )  # fmt: skip
    sc = wt.score(rec, gestures())
    assert (sc.events["jump"].hits, sc.events["jump"].expected, sc.events["jump"].false) == (2, 2, 0)
    assert sc.events["punch"].hits == 1
    assert all(0 < d < 1500 for d in sc.events["jump"].delays_ms)
    text = wt.format_scores(sc)
    assert "jump" in text and "2/2" in text


def test_detection_outside_any_cue_is_false():
    tl = Timeline(6.0)
    tl.jump(3.0)
    sc = wt.score(recording(tl, [("still", 0, 6.0)]), gestures())
    assert sc.events["jump"].false == 1 and sc.events["jump"].expected == 0


def small_steps_recording() -> Recording:
    tl = Timeline(12.0)
    t, k = 2.0, 0
    while t < 6.0:  # knees lift less than the default knee_lift (0.12 torso)
        tl.knee_lift(t, 0.24, W.L_KNEE if k % 2 == 0 else W.R_KNEE, lift_torso=0.09)
        t += 0.4
        k += 1
    return recording(tl, [("still", 0, 2.0), ("run", 2.0, 6.0), ("stop", 6.0, 12.0)])


def test_search_lowers_knee_lift_for_small_steps():
    rec = small_steps_recording()
    g = gestures()
    assert wt.score(rec, g).events["run_start"].hits == 0
    best, results = wt.search([rec], g, progress=lambda s: None)
    assert [r.group for r in results] == ["run"]  # only cued moves are tuned
    assert best.webcam.knee_lift < 0.09
    sc = wt.score(rec, best)
    assert sc.events["run_start"].hits == 1 and sc.events["run_stop"].hits == 1
    assert sc.steps_per_s and sc.steps_per_s > 2


def test_lean_stats_and_suggestion():
    tl = Timeline(14.0)
    tl.lean(3.0, 6.0, -15)
    tl.lean(9.0, 12.0, 15)
    rec = recording(
        tl,
        [("still", 0, 3.0), ("lean_left", 3.0, 6.0), ("center", 6.0, 9.0), ("lean_right", 9.0, 12.0),
         ("center", 12.0, 14.0)],
    )  # fmt: skip
    g = gestures()
    st = wt.score(rec, g).lean
    assert st is not None
    assert st.hold_deg == pytest.approx(15, abs=0.5)
    assert st.neutral_deg < 0.5 and st.neutral_ok == 1.0
    sug = wt.suggest_lean(st, g.lean)
    assert sug["lean.deadzone_deg"] == 1.0
    assert sug["lean.full_deg"] == 12.0


def test_measure_view_and_checks():
    tl = Timeline(0.1)
    pose = tl.poses()[0]
    v = measure_view(pose, 480)
    assert v.yaw_deg == pytest.approx(0, abs=1)
    assert v.roll_deg == pytest.approx(0, abs=1)
    assert v.height_frac == pytest.approx((470 - 90) / 480)
    # Turned to the left: right shoulder closer to the camera -> camera off to the right.
    pose.world[W.L_SHOULDER, 2] = 0.1
    pose.world[W.R_SHOULDER, 2] = -0.1
    assert measure_view(pose, 480).yaw_deg == pytest.approx(29, abs=1)
    assert describe_yaw(29) == "camera 29 deg to your right"

    good = View(yaw_deg=2, roll_deg=1, height_frac=0.6, pitch_ratio=1.0)
    assert view_checks(good, [], 0.0)[1]
    assert not view_checks(good, [], 30.0)[1]  # wrong angle for right30
    assert not view_checks(View(yaw_deg=2, roll_deg=9, height_frac=0.6), [], 0.0)[1]
    assert not view_checks(good, ["knees", "feet"], 0.0)[1]
    lines, ready = view_checks(good, ["feet"], 0.0)  # feet are optional
    assert ready and lines[0] == ("head to knees in view (feet optional)", True)
    lines, ready = view_checks(View(height_frac=0.6, pitch_ratio=0.7), [], 0.0)
    assert ready and any("low" in text for text, _ in lines)  # height is only a tip


def test_set_params_and_write_profile(tmp_path):
    g = wt.set_params(gestures(), {"webcam.knee_lift": 0.08, "jump.crouch_hold_ms": 450})
    assert g.webcam.knee_lift == 0.08 and g.jump.crouch_hold_ms == 450
    with pytest.raises(KeyError):
        wt.set_params(g, {"webcam.nope": 1})
    out = wt.write_profile(wt.BASE_PROFILE, g, tmp_path / "tuned.yaml", [])
    prof = load_profile(out)
    assert prof.gestures.webcam.knee_lift == 0.08
    assert prof.gestures.jump.crouch_hold_ms == 450
    assert prof.gestures.lean == gestures().lean
    assert prof.ports == load_profile(GTA5).ports


def test_cli_tune(tmp_path, capsys):
    path = small_steps_recording().save(tmp_path / "drill.npz")
    assert main(["webcam-tune", str(path), "--profile", GTA5, "--score-only"]) == 0
    assert "run_start" in capsys.readouterr().out
    out = tmp_path / "tuned.yaml"
    assert main(["webcam-tune", str(path), "--profile", GTA5, "-o", str(out)]) == 0
    assert load_profile(out).gestures.webcam.knee_lift < 0.09
    assert "front" in capsys.readouterr().out


def test_themed_screens_render():
    from bodyctl.gestures.webcam import BodyState
    from bodyctl.tools import drill_view as V

    frame = np.full((480, 640, 3), 90, np.uint8)
    pose = Timeline(0.1).poses()[0]
    tally = {"jump": wt.EventScore(expected=3, hits=2, false=1)}
    events = [(0.4, "jump", True), (2.0, "crouch", False)]
    drilling = V.Panel("REP 2 / 6", state=BodyState(visible=True), events=events, tally=tally)
    setup = V.Panel("CAMERA SETUP", checks=[("whole body in view", True), ("come closer", False)])
    parts = V.part_visibility(pose)
    hud = V.Hud(fps=29.5, recording=True, elapsed_s=42, frames=1234, parts=parts, quality=[1.0, 0.6, 0.0])
    assert V.frame_quality(parts) > 0.9
    for style in V.STYLES:
        for panel in (drilling, setup):
            img = V.render(
                frame, pose, "JUMP!", "next: stand still   OUT OF VIEW: knees", style, 0.5, panel, hud
            )
            assert img.shape == (V.DISPLAY_H, V.DISPLAY_W, 3)
    wide = np.full((360, 640, 3), 90, np.uint8)  # 16:9 camera, letterboxed
    empty = V.Panel("GET READY", events=[])
    assert V.render(wide, None, "x" * 200, "", "idle", None, empty, V.Hud()).shape[1] == V.DISPLAY_W
    assert V.report_image("event hits\nrun_start 1/1", False).shape[1] == V.DISPLAY_W


def test_rep_header():
    from bodyctl.tools.drill import rep_header

    s = build_script(["jump"], 3)
    labels = [st.label for st in s]
    assert rep_header(s, 0) == "GET READY"
    assert rep_header(s, labels.index("jump")) == "REP 1 / 3"
    assert rep_header(s, len(s) - 1) == "ALMOST DONE"


def test_setup_shows_only_missing_parts_until_body_in_view():
    lines, ready = view_checks(View(yaw_deg=100, roll_deg=-10, height_frac=0.82), ["hips", "knees"], 0.0)
    assert not ready and lines == [("not in view: hips, knees", False)]


def test_view_from_knees_when_feet_hidden():
    pose = Timeline(0.1).poses()[0]
    pose.img[[W.L_ANKLE, W.R_ANKLE], 2] = 0.1
    v = measure_view(pose, 480)
    assert v.from_knees
    assert v.height_frac == pytest.approx((400 - 90) / 0.73 / 480)
    assert v.roll_deg == pytest.approx(0, abs=1)
    lines, ready = view_checks(View(yaw_deg=0, roll_deg=0, height_frac=0.6, from_knees=True), ["feet"], 0.0)
    assert ready and "estimated from your knees" in lines[1][0]
