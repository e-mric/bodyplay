"""Press the virtual pad's L2 + R2 through a fixed sequence, no camera, to check how a
game (through Remote Play) reads trigger pressure. Each step prints what is sent.

Run it, reconnect the Remote Play client so it sees the new pad, go to the in-game
station, then press Enter in the terminal to play the sequence (again as often as you
like; Ctrl+C quits)."""

from __future__ import annotations

import time

STEP_HZ = 100


def _sequence():
    """(label, l2(t), r2(t), seconds): pressure as a function of time within the step."""
    ramp = lambda s: lambda t: min(1.0, t / s)  # noqa: E731
    fall = lambda s: lambda t: max(0.0, 1.0 - t / s)  # noqa: E731
    const = lambda v: lambda t: v  # noqa: E731
    late = lambda d: lambda t: 1.0 if t >= d else 0.0  # noqa: E731
    # By hand one trigger lands a little before the other: maybe a press of both in the
    # very same instant is what gets lost.
    yield "staggered: L2, then R2 0.15 s later, held", const(1.0), late(0.15), 2.5
    yield "rest", const(0.0), const(0.0), 1.5
    yield "staggered: R2, then L2 0.15 s later, held", late(0.15), const(1.0), 2.5
    yield "rest", const(0.0), const(0.0), 1.5
    yield "slow press: 0 -> 100% over 2 s", ramp(2.0), ramp(2.0), 2.0
    yield "hold 100%", const(1.0), const(1.0), 2.0
    yield "slow release over 1 s", fall(1.0), fall(1.0), 1.0
    yield "rest", const(0.0), const(0.0), 2.0
    for v in (0.25, 0.5, 0.75, 1.0):
        yield f"hold {v:.0%}", const(v), const(v), 2.0
    yield "rest", const(0.0), const(0.0), 2.0
    yield "quick full press, held", const(1.0), const(1.0), 2.0
    yield "rest", const(0.0), const(0.0), 2.0
    yield "L2 alone, full", const(1.0), const(0.0), 2.0
    yield "rest", const(0.0), const(0.0), 1.5
    yield "R2 alone, full", const(0.0), const(1.0), 2.0
    yield "rest", const(0.0), const(0.0), 2.0
    for i in range(3):  # the body's GTA 6 rep: down over 0.5 s, held, up over 0.5 s
        yield f"body-style rep {i + 1}: press over 0.5 s", ramp(0.5), ramp(0.5), 0.5
        yield "  held", const(1.0), const(1.0), 1.5
        yield "  release over 0.5 s", fall(0.5), fall(0.5), 0.5
        yield "  rest", const(0.0), const(0.0), 1.0


def run(backend_name: str = "vgamepad") -> None:
    from bodyctl.output.gamepad import Gamepad, open_backend
    from bodyctl.sensors.webcam_pose import now_us

    gp = Gamepad(open_backend(backend_name), 30, 30)
    slot = getattr(gp.backend, "slot", None)
    print(f"virtual pad up{'' if slot is None else f' in controller slot {slot + 1}'}")
    print("reconnect Remote Play now, go to the station, then press Enter here (Ctrl+C quits)")
    try:
        while True:
            input()
            # Veyra only forwards input while its own window has focus.
            print("  click the Remote Play window now: starting in 5 s", flush=True)
            time.sleep(5)
            for label, l2, r2, seconds in _sequence():
                print(f"  {label}", flush=True)
                t0 = time.perf_counter()
                while (t := time.perf_counter() - t0) < seconds:
                    gp.set_trigger("l2", l2(t))
                    gp.set_trigger("r2", r2(t))
                    gp.update(now_us())
                    time.sleep(1 / STEP_HZ)
            gp.set_trigger("l2", 0.0)
            gp.set_trigger("r2", 0.0)
            gp.update(now_us())
            # A plain button: if this reaches the game and the triggers don't, only the
            # triggers get lost on the way (GTA 6 at the kettlebell: circle = put it down).
            print("  tap circle (button check)", flush=True)
            gp.hold("circle", True, now_us())
            gp.update(now_us())
            time.sleep(0.2)
            gp.hold("circle", False, now_us())
            gp.update(now_us())
            time.sleep(0.1)
            gp.update(now_us())
            print("done; Enter plays it again, Ctrl+C quits")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        gp.release_all(now_us())
        gp.update(now_us())
        gp.close()
