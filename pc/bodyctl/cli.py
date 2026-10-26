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

    if args.fixtures:
        paths = synth.write_fixtures()
    elif args.scenario:
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


def cmd_webcam(args: argparse.Namespace) -> int:
    from bodyctl.config.profile import load_profile
    from bodyctl.output.gamepad import Gamepad, open_backend
    from bodyctl.output.marker import open_marker
    from bodyctl.tier0 import run_webcam

    prof = load_profile(args.profile)
    w, _, h = args.size.partition("x")
    backend = open_backend(args.backend, verbose=args.verbose)
    gamepad = Gamepad(backend, prof.press.hold_ms, prof.press.release_ms, open_marker(args.marker_port))
    print(f"profile {prof.profile}, gamepad backend {type(backend).__name__}")
    return run_webcam(
        prof, gamepad, args.camera, (int(w), int(h)), args.fps, args.model, show=not args.no_dashboard
    )


def cmd_dashboard(args: argparse.Namespace) -> int:
    if not args.port:
        print("On Tier 0 the dashboard is built into `bodyctl webcam`.", file=sys.stderr)
        return 2
    return _todo("M3")(args)


def cmd_replay(args: argparse.Namespace) -> int:
    from bodyctl.config.compile import BELT_PARAM_IDS, flatten_gestures
    from bodyctl.config.profile import load_profile
    from bodyctl.tools import replay

    params = flatten_gestures(load_profile(args.profile))
    for kv in args.set:
        name, _, value = kv.partition("=")
        if name not in BELT_PARAM_IDS:
            print(f"unknown parameter {name!r}; known: {', '.join(BELT_PARAM_IDS)}", file=sys.stderr)
            return 1
        params[name] = float(value)
    for trace in args.trace:
        replay.main_print(Path(trace), params, args.forward, args.calib_ms, args.events)
    return 0


def cmd_latency_video(args: argparse.Namespace) -> int:
    from bodyctl.tools import latency_video as lv

    video = Path(args.video)
    if not video.exists():
        print(f"no such file: {video}", file=sys.stderr)
        return 1
    saved = lv.rois_path(video)
    rois: dict[str, lv.Roi] = {}
    if args.rois_from:
        rois = lv.load_rois(lv.rois_path(Path(args.rois_from)))
    elif saved.exists() and not args.select:
        rois = lv.load_rois(saved)
    for spec in args.roi:
        name, _, box = spec.partition("=")
        if name not in lv.ROI_NAMES:
            print(f"ROI name must be one of {lv.ROI_NAMES}, got {name!r}", file=sys.stderr)
            return 1
        rois[name] = lv.Roi.parse(box)
    if not rois or args.select:
        rois = lv.select_rois_interactive(video, lv.ROI_NAMES)
    if "led" not in rois and "tell" not in rois:
        print("need at least a led or tell ROI", file=sys.stderr)
        return 1
    lv.save_rois(saved, rois)
    settings = lv.Settings(
        k_sigma=args.k, tell_lead_max_ms=args.lead_max_ms, screen_lag_max_ms=args.lag_max_ms
    )
    trials, summary, csv_path = lv.run(video, rois, settings, args.out, args.fps)
    missing = sum(1 for t in trials if None in (t.tell_frame, t.screen_frame) and "screen" in rois)
    print(f"{len(trials)} trials -> {csv_path}" + (f" ({missing} incomplete)" if missing else ""))
    for col, st in summary.items():
        print(
            f"  {col:<18} median {st['median']:7.1f}  p90 {st['p90']:7.1f}  p99 {st['p99']:7.1f}  "
            f"[{st['min']:.1f}..{st['max']:.1f}] ms  n={st['n']}"
        )
    return 0 if trials else 1


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

    sp = add("webcam", "Tier 0: webcam pose -> gestures -> virtual gamepad", cmd_webcam)
    sp.add_argument("--profile", default="config/profiles/gta5.yaml")
    sp.add_argument(
        "--backend",
        choices=["vgamepad", "dry-run"],
        default=None,
        help="virtual pad (default: vgamepad on Windows, dry-run elsewhere)",
    )
    sp.add_argument("--camera", type=int, default=0, help="camera index")
    sp.add_argument("--size", default="640x480", help="capture size WxH")
    sp.add_argument("--fps", type=int, default=60, help="requested camera frame rate")
    sp.add_argument("--model", choices=["lite", "full", "heavy"], default="lite", help="pose model")
    sp.add_argument("--marker-port", help="serial port of the marker LED board (firmware env marker)")
    sp.add_argument("--no-dashboard", action="store_true", help="print events instead of a window")
    sp.add_argument("--verbose", action="store_true", help="dry-run: print every gamepad report")

    sp = add("device", "Talk to a node, dongle or hub over USB serial", _todo("M3"))
    dsub = sp.add_subparsers(dest="device_command", metavar="<action>", required=True)
    push = dsub.add_parser("push-profile", help="Compile a profile and push it to the device")
    push.add_argument("--port", required=True, help="COM5 on Windows, /dev/cu.usbmodem* on macOS")
    push.add_argument("profile")
    info = dsub.add_parser("info", help="Show the device's HELLO and link stats")
    info.add_argument("--port", required=True)

    sp = add("dashboard", "Live view: skeleton or IMU, events, cadence, confidence", cmd_dashboard)
    sp.add_argument("--port", help="device serial port (omit for the webcam tier)")

    sp = add("record", "Record a session to a .bptrace file", _todo("M3"))
    sp.add_argument("--port", required=True)
    sp.add_argument("-o", "--out", default="data/sessions")

    sp = add("replay", "Replay a .bptrace through the firmware gesture library and score it", cmd_replay)
    sp.add_argument("trace", nargs="+")
    sp.add_argument("--profile", default="config/profiles/gta5.yaml", help="gesture thresholds to use")
    sp.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override one threshold")
    sp.add_argument("--forward", default="+x", help="board axis pointing forward when worn")
    sp.add_argument("--calib-ms", type=int, default=800, help="standing calibration at the start")
    sp.add_argument("--events", action="store_true", help="list every event")

    sp = add("latency-video", "Measure tell -> LED -> screen latency from a 240 fps clip", cmd_latency_video)
    sp.add_argument("video")
    sp.add_argument(
        "--roi",
        action="append",
        default=[],
        metavar="NAME=x,y,w,h",
        help="tell, led or screen ROI in pixels (repeatable); omit to draw them on the first frame",
    )
    sp.add_argument("--rois-from", help="reuse ROIs saved next to another clip (same framing)")
    sp.add_argument("--select", action="store_true", help="draw ROIs even if saved ones exist")
    sp.add_argument("--fps", type=float, help="override the clip's frame rate (slow-mo exports)")
    sp.add_argument("--k", type=float, default=6.0, help="onset threshold in baseline std devs")
    sp.add_argument("--lead-max-ms", type=float, default=600.0, help="longest tell -> LED to search")
    sp.add_argument("--lag-max-ms", type=float, default=500.0, help="longest LED -> screen to search")
    sp.add_argument("-o", "--out", help="output folder (default: next to the clip)")

    sp = add("synth", "Write synthetic IMU traces with ground-truth labels", cmd_synth)
    sp.add_argument("-o", "--out", default="data/synth")
    sp.add_argument("--scenario", action="append", help="one scenario (repeatable); default all")
    sp.add_argument("--noisy", action="store_true", help="with --scenario: add noise and tilt")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--noisy-seeds", type=int, default=1, help="noisy variants per scenario")
    sp.add_argument(
        "--fixtures", action="store_true", help="regenerate firmware/test/fixtures (native gesture tests)"
    )

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
