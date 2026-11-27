"""Real controller passthrough: XInput decoding, merging, and the body-only marker."""

from bodyctl.output.gamepad import DryRunBackend, Gamepad
from bodyctl.output.passthrough import PhysicalPad, from_xinput, merge


def test_xinput_decoding_with_deadzones():
    p = from_xinput(0x1000 | 0x0100, 255, 10, 32767, 0, 3000, -3000)
    assert p.buttons == {"cross", "l1"}
    assert p.left == (1.0, 0.0)
    assert p.right == (0.0, 0.0)  # inside the deadzone
    assert p.l2 == 1.0 and p.r2 == 0.0  # 10/255 is under the trigger threshold


def test_real_controller_merges_on_top_of_the_body():
    gp = Gamepad(DryRunBackend(), 30, 30)
    gp.set_stick("left_stick", 0.0, 0.8)  # body runs forward
    gp.press("square", 0)
    gp.update(0)
    gp.set_physical(PhysicalPad(buttons=frozenset({"cross"}), right=(0.5, 0.0), l2=0.7), 1000)
    r = gp.report
    assert r.buttons == {"square", "cross"} and r.left == (0.0, 0.8) and r.right == (0.5, 0.0) and r.l2 == 0.7
    gp.set_physical(PhysicalPad(left=(-1.0, 0.0)), 2000)
    assert gp.report.left == (-1.0, 0.0)  # a stick you move wins
    gp.set_physical(None, 3000)
    assert gp.report.left == (0.0, 0.8) and "cross" not in gp.report.buttons


def test_marker_follows_the_body_only():
    pulses = []

    class Marker:
        def pulse(self):
            pulses.append(1)

        def close(self):
            pass

    gp = Gamepad(DryRunBackend(), 30, 30, Marker())
    gp.set_physical(PhysicalPad(buttons=frozenset({"cross"})), 0)
    assert not pulses  # your own presses aren't body latency
    gp.press("square", 1000)
    gp.update(1000)
    assert len(pulses) == 1


def test_merge_without_a_real_controller_is_the_body():
    gp = Gamepad(DryRunBackend(), 30, 30)
    gp.update(0)
    assert merge(gp.body, None) is gp.body


def test_the_body_waits_while_the_real_controller_is_used():
    """Holding the controller (menus, walking to a gym station) can pass for a posture: a
    controller held low looks like a deadlift bar. While it's in use the body sends
    nothing; with passthrough off (Veyra reads the controller itself) it isn't merged."""
    from test_workout import GTA6, swing_set

    from bodyctl.config.profile import load_profile
    from bodyctl.tier0 import Tier0

    prof = load_profile(GTA6)
    gp = Gamepad(DryRunBackend(), 30, 30)
    gp.merge_physical = False
    app = Tier0(prof, gp, clock=lambda: 0)
    app.engine.set_mode("fitness", 0)
    poses = swing_set(3).poses()
    sent_while_used, sent_after = [], []
    for p in poses:
        app.clock = lambda p=p: p.t_us
        t = p.t_us / 1e6
        if t < 4.0:  # thumb on the stick for the first 4 s
            gp.set_physical(PhysicalPad(left=(0.0, 0.6)), p.t_us)
        elif gp.physical is not None and gp.physical.left != (0.0, 0.0):
            gp.set_physical(PhysicalPad(), p.t_us)  # let go
        app.step(p.t_us, p, t_pose_us=p.t_us)
        r = gp.report
        active = bool(r.buttons) or r.l2 > 0 or r.left != (0.0, 0.0)
        (sent_while_used if t < 3.9 + prof.tier0.controller_yield_ms / 1000 else sent_after).append(
            (t, active)
        )
    assert not [t for t, a in sent_while_used if a]  # nothing from the body, stick not merged
    assert any(a for _, a in sent_after)  # the swings reach the game once the controller is down
