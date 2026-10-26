from dataclasses import dataclass, field

import pytest

from bodyctl.config.profile import load_profile
from bodyctl.mapping import BindingEngine
from bodyctl.output.gamepad import DryRunBackend, Gamepad, PressScheduler, Report

MS = 1000


def levels(s: PressScheduler, t0_ms: int, t1_ms: int) -> list[bool]:
    return [s.tick(t * MS) for t in range(t0_ms, t1_ms)]


def test_press_holds_30_ms_and_releases_30_ms():
    s = PressScheduler(30 * MS, 30 * MS)
    s.press(0)
    lv = levels(s, 0, 100)
    assert lv[:30] == [True] * 30
    assert lv[30:] == [False] * 70


def test_queued_presses_respect_release():
    s = PressScheduler(30 * MS, 30 * MS)
    s.press(0)
    s.press(5 * MS)  # mashed during the first press
    lv = levels(s, 0, 130)
    assert lv[0:30] == [True] * 30
    assert lv[30:60] == [False] * 30  # >= 30 ms released between presses
    assert lv[60:90] == [True] * 30
    assert lv[90:] == [False] * 40


def test_mash_cap_about_16_hz():
    s = PressScheduler(30 * MS, 30 * MS, max_queue=2)
    rises = 0
    prev = False
    for t in range(1000):
        if t % 10 == 0:
            s.press(t * MS)  # 100 Hz requests
        cur = s.tick(t * MS)
        rises += cur and not prev
        prev = cur
    assert 15 <= rises <= 17


def test_hold_min_durations():
    s = PressScheduler(30 * MS, 30 * MS)
    s.hold(True, 0)
    s.hold(False, 5 * MS)  # let go too early: still held 30 ms
    assert levels(s, 5, 30) == [True] * 25
    assert s.tick(30 * MS) is False
    s.hold(True, 40 * MS)  # re-grab 10 ms after release: waits until 60 ms
    assert s.tick(59 * MS) is False
    assert s.tick(60 * MS) is True


@dataclass
class FakeState:
    sources: dict[str, float] = field(default_factory=dict)
    conditions: dict[str, bool] = field(default_factory=dict)

    def source(self, name: str) -> float:
        return self.sources.get(name, 0.0)

    def condition(self, name: str) -> bool:
        return self.conditions.get(name, False)


@dataclass
class Ev:
    name: str


class CountingMarker:
    def __init__(self):
        self.n = 0

    def pulse(self):
        self.n += 1

    def close(self):
        pass


@pytest.fixture
def rig():
    prof = load_profile("config/profiles/gta5.yaml")
    backend = DryRunBackend()
    marker = CountingMarker()
    gp = Gamepad(backend, prof.press.hold_ms, prof.press.release_ms, marker)
    return BindingEngine(prof, gp), backend, marker


def last(backend: DryRunBackend) -> Report:
    return backend.history[-1][1]


def test_jump_presses_square_once(rig):
    eng, backend, marker = rig
    st = FakeState()
    eng.apply(st, [Ev("jump")], 0)
    assert "square" in last(backend).buttons
    assert marker.n == 1
    for t in range(1, 40):
        eng.apply(st, [], t * MS)
    assert "square" not in last(backend).buttons
    downs = [r for _, r in backend.history if "square" in r.buttons]
    assert len(downs) == 1  # one report with square down, then one with it up


def test_run_drives_left_stick_and_marks_run_start(rig):
    eng, backend, marker = rig
    st = FakeState({"run_cadence": 0.0, "lean": 0.0})
    eng.apply(st, [], 0)
    st.sources["run_cadence"] = 0.5
    eng.apply(st, [Ev("run_start"), Ev("step")], 33 * MS)
    assert last(backend).left == (0.0, 0.5)
    assert marker.n == 1  # stick left neutral: the run-start trial gets a marker flash
    st.sources["run_cadence"] = 0.8
    st.sources["lean"] = -0.3
    eng.apply(st, [Ev("step")], 66 * MS)
    assert last(backend).left == (-0.3, 0.8)
    assert marker.n == 1  # continuous changes don't flash


def test_sprint_taps_cross_on_steps_only_above_threshold(rig):
    eng, backend, _ = rig
    st = FakeState({"run_cadence": 1.0})
    eng.apply(st, [Ev("step")], 0)
    assert "cross" not in last(backend).buttons
    st.conditions["cadence_above_sprint"] = True
    eng.apply(st, [Ev("step")], 300 * MS)
    assert "cross" in last(backend).buttons


def test_aim_holds_l2_and_shoot_needs_aim(rig):
    eng, backend, _ = rig
    st = FakeState()
    eng.apply(st, [Ev("shoot")], 0)
    assert not backend.history  # shoot without aiming does nothing
    st.conditions["aiming"] = True
    eng.apply(st, [], 10 * MS)
    assert last(backend).l2 == 1.0
    eng.apply(st, [Ev("shoot")], 20 * MS)
    assert last(backend).r2 == 1.0
    st.conditions["aiming"] = False
    for t in range(21, 80):
        eng.apply(st, [], t * MS)
    assert last(backend).l2 == 0.0 and last(backend).r2 == 0.0


def test_car_mode_neutral_sticks_and_analog_throttle(rig):
    eng, backend, _ = rig
    st = FakeState({"run_cadence": 0.7, "lean": 0.5, "twist": 0.4})
    eng.apply(st, [], 0)
    assert last(backend).left == (0.5, 0.7)
    eng.set_mode("boat", 10 * MS)  # covered by the car mode
    eng.apply(st, [Ev("jump")], 20 * MS)
    r = last(backend)
    assert r.left == (0.0, 0.0) and r.right == (0.0, 0.0)  # hard rule 4
    assert r.r2 == pytest.approx(0.7)  # throttle from run cadence
    assert "square" not in r.buttons  # jump does nothing in a car


def test_vehicle_exit_gated_on_stopped(rig):
    eng, backend, _ = rig
    eng.set_mode("car", 0)
    st = FakeState()
    eng.apply(st, [Ev("enter_exit")], 0)
    assert not backend.history or "triangle" not in last(backend).buttons
    st.conditions["stopped"] = True
    eng.apply(st, [Ev("enter_exit")], 10 * MS)
    assert "triangle" in last(backend).buttons


def test_paused_releases_everything(rig):
    eng, backend, _ = rig
    st = FakeState({"run_cadence": 1.0}, {"aiming": True})
    eng.apply(st, [], 0)
    eng.set_mode("paused", 10 * MS)
    for t in range(11, 60):
        eng.apply(st, [], t * MS)
    r = last(backend)
    assert r == Report()


def test_unknown_mode_rejected(rig):
    eng, _, _ = rig
    with pytest.raises(ValueError):
        eng.set_mode("submarine", 0)
