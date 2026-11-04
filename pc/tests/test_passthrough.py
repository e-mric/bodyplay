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
