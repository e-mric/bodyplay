"""`bodyctl` command line. Run `uv run bodyctl --help`."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from bodyctl import __version__

Handler = Callable[[argparse.Namespace], int]


def _todo(milestone: str) -> Handler:
    def run(args: argparse.Namespace) -> int:
        print(f"bodyctl {args.command}: not implemented yet (planned in {milestone})", file=sys.stderr)
        return 2

    return run


# --- implemented commands ---------------------------------------------------------


def cmd_profile_check(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from bodyctl.config.profile import load_profile

    try:
        prof = load_profile(args.profile)
    except ValidationError as e:
        print(f"{args.profile}: invalid\n{e}", file=sys.stderr)
        return 1
    modes = ", ".join(f"{k}{'+' + '+'.join(m.also) if m.also else ''}" for k, m in prof.modes.items())
    print(f"{args.profile}: ok — profile '{prof.profile}', {len(prof.ports)} ports, modes: {modes}")
    return 0


def cmd_synth(args: argparse.Namespace) -> int:
    from bodyctl.tools import synth

    if args.scenario:
        out = Path(args.out)
        paths = []
        for name in args.scenario:
            if name not in synth.SCENARIOS:
                print(f"unknown scenario {name!r}; choose from {sorted(synth.SCENARIOS)}")
                return 1
            sig = synth.build(name, noisy=args.noisy, seed=args.seed)
            suffix = f"_noisy{args.seed}" if args.noisy else ""
            paths.append(
                synth.write_trace(sig, out / f"synth_{name}{suffix}.bptrace", name, args.noisy, args.seed)
            )
    else:
        paths = synth.generate_all(args.out, noisy_seeds=args.noisy_seeds)
    for p in paths:
        print(p)
    return 0


def cmd_trace_info(args: argparse.Namespace) -> int:
    import json

    from bodyctl.protocol import Kind
    from bodyctl.trace import Trace

    tr = Trace.read(args.trace)
    print(json.dumps(tr.header, indent=2, sort_keys=True))
    kinds = Counter()
    nodes = Counter()
    for _, p in tr.frames():
        kinds[Kind(p.kind).name] += 1
        nodes[p.node_id] += 1
    t = [r.t_rx_us for r in tr.records]
    span = (max(t) - min(t)) / 1e6 if t else 0.0
    print(
        f"records: {len(tr.records)} over {span:.2f} s; frames by kind: {dict(kinds)}; by node: {dict(nodes)}"
    )
    labels = Counter(lb.event for lb in tr.labels())
    if labels:
        print(f"labels: {dict(labels)}")
    return 0


# --- parser -------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bodyctl",
        description="Bodyplay PC tools: play GTA on a PS5 with your body.",
    )
    p.add_argument("--version", action="version", version=f"bodyctl {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>", required=True)

    def add(name: str, help_: str, handler: Handler) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(func=handler)
        return sp

    sp = add("webcam", "Tier 0: webcam pose -> gestures -> virtual gamepad", _todo("M2"))
    sp.add_argument("--profile", default="config/profiles/gta5.yaml")
    sp.add_argument("--backend", choices=["vgamepad", "uinput", "dry-run"], default=None)

    sp = add("device", "Talk to a node, dongle or hub over USB serial", _todo("M3"))
    dsub = sp.add_subparsers(dest="device_command", metavar="<action>", required=True)
    push = dsub.add_parser("push-profile", help="Compile a profile and push it to the device")
    push.add_argument("--port", required=True, help="COM5 on Windows, /dev/cu.usbmodem* on macOS")
    push.add_argument("profile")
    info = dsub.add_parser("info", help="Show the device's HELLO and link stats")
    info.add_argument("--port", required=True)

    sp = add("dashboard", "Live view: skeleton or IMU, events, cadence, confidence", _todo("M2"))
    sp.add_argument("--port", help="device serial port (omit for the webcam tier)")

    sp = add("record", "Record a session to a .bptrace file", _todo("M3"))
    sp.add_argument("--port", required=True)
    sp.add_argument("-o", "--out", default="data/sessions")

    sp = add("replay", "Replay a .bptrace through the gesture library", _todo("M3"))
    sp.add_argument("trace")

    sp = add("latency-video", "Measure tell -> LED -> screen latency from a 240 fps clip", _todo("M1"))
    sp.add_argument("video")

    sp = add("synth", "Write synthetic IMU traces with ground-truth labels", cmd_synth)
    sp.add_argument("-o", "--out", default="data/synth")
    sp.add_argument("--scenario", action="append", help="one scenario (repeatable); default all")
    sp.add_argument("--noisy", action="store_true", help="with --scenario: add noise and tilt")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--noisy-seeds", type=int, default=1, help="noisy variants per scenario")

    sp = add("trace", "Inspect a .bptrace file", cmd_trace_info)
    sp.add_argument("trace")

    sp = add("profile", "Validate or compile a profile YAML", cmd_profile_check)
    psub = sp.add_subparsers(dest="profile_command", metavar="<action>", required=True)
    chk = psub.add_parser("check", help="Validate against the schema and hard rules")
    chk.add_argument("profile")
    chk.set_defaults(func=cmd_profile_check)
    cmp_ = psub.add_parser("compile", help="Compile to the device binary format")
    cmp_.add_argument("profile")
    cmp_.add_argument("-o", "--out", required=True)
    cmp_.set_defaults(func=_todo("M3"))

    add("launch-chiaki", "Start chiaki-ng with the SDL mapping for the node's HID gamepad", _todo("M3"))
    sp = add("hil", "Hardware-in-the-loop: toggle each port and confirm on the PS5", _todo("M4"))
    sp.add_argument("--port", required=True)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
