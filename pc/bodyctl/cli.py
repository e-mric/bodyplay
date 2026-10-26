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


def cmd_profile_compile(args: argparse.Namespace) -> int:
    from bodyctl.config.compile import c_header, compile_profile
    from bodyctl.config.profile import load_profile

    data = compile_profile(load_profile(args.profile))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.c_header:
        out.write_text(c_header(data, source=Path(args.profile).as_posix()), encoding="utf-8", newline="\n")
    else:
        out.write_bytes(data)
    print(f"{out}: {len(data)} bytes")
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


def _link_args(p: argparse.ArgumentParser, required: bool = True) -> None:
    g = p.add_mutually_exclusive_group(required=required)
    g.add_argument("--port", help="COM5 on Windows, /dev/cu.usbmodem* on macOS")
    g.add_argument("--sim", metavar="TRACE", help="simulated node playing a .bptrace (no hardware)")


def _open(args: argparse.Namespace):
    from bodyctl.output.device import open_link

    return open_link(args.port, args.sim)


def cmd_device(args: argparse.Namespace) -> int:
    from bodyctl.output.device import DeviceError
    from bodyctl.protocol import Command, Mode

    link = _open(args)
    try:
        action = args.device_command
        if action == "push-profile":
            from bodyctl.config.compile import compile_profile
            from bodyctl.config.profile import load_profile

            prof = load_profile(args.profile)
            data = compile_profile(prof)
            link.push_profile(data, lambda i, n: print(f"  chunk {i}/{n}"))
            print(
                f"pushed {args.profile} ({len(data)} bytes, profile '{prof.profile}'); stored on the device"
            )
        elif action == "info":
            h = link.hello()
            print(f"node {h.node_id}, protocol v{h.value[0]}, firmware {h.payload.decode(errors='replace')}")
        elif action == "stats":
            print(link.stats())
        elif action == "stats-reset":
            link.command(Command.STATS_RESET)
        elif action == "calibrate":
            link.command(Command.CALIBRATE)
            print("calibrating: stand still, facing the TV, for 2 s")
        elif action == "marker":
            link.command(Command.MARKER)
        elif action == "mode":
            try:
                m = Mode[args.mode.upper()]
            except KeyError:
                print(f"unknown mode {args.mode!r}; one of {[x.name.lower() for x in Mode]}", file=sys.stderr)
                return 1
            link.set_mode(m)
            print(f"mode {m.name.lower()}")
    except DeviceError as e:
        print(f"device: {e}", file=sys.stderr)
        return 1
    finally:
        link.close()
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    import time

    from bodyctl.tools.record import Recorder, session_path

    link = _open(args)
    path = session_path(args.out, args.tag)
    try:
        hello = link.hello()
        header = {
            "port": args.port or f"sim:{args.sim}",
            "node": {"id": hello.node_id, "fw": hello.payload.decode(errors="replace")},
            "tag": args.tag,
        }
        rec = Recorder(link, path, header)
        rec.start()
        print(f"recording to {path} (Ctrl+C to stop)")
        t0 = time.monotonic()
        try:
            while args.seconds is None or time.monotonic() - t0 < args.seconds:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        rec.stop()
        print(f"{rec.frames} frames, {len(rec.events)} events -> {path}")
    finally:
        link.close()
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    if not args.port and not args.sim:
        print(
            "On Tier 0 the dashboard is built into `bodyctl webcam`; give --port or --sim.", file=sys.stderr
        )
        return 2
    from bodyctl.tools.device_dashboard import run_device_dashboard

    return run_device_dashboard(_open(args), args.out)


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


def cmd_chiaki(args: argparse.Namespace) -> int:
    from bodyctl.output import sdl_mapping as sdl

    vid = args.vid if args.vid is not None else sdl.DEFAULT_VID
    pid = args.pid if args.pid is not None else sdl.DEFAULT_PID
    if args.print:
        print(sdl.mapping(vid=vid, pid=pid))
        return 0
    exe = sdl.find_chiaki(args.exe)
    if exe is None or not exe.exists():
        print("chiaki-ng not found; pass --exe path\\to\\chiaki.exe", file=sys.stderr)
        return 1
    sdl.launch_chiaki(exe, vid, pid)
    print(f"started {exe} with the Bodyplay SDL mapping")
    return 0


def cmd_plot(args: argparse.Namespace) -> int:
    from bodyctl.config.compile import flatten_gestures
    from bodyctl.config.profile import load_profile
    from bodyctl.tools import replay
    from bodyctl.tools.plot import plot_event

    trace = Path(args.trace)
    _, dets = replay.run(trace, flatten_gestures(load_profile(args.profile)))
    fired = [d.t_us / 1e6 for d in dets if d.event == args.event]
    out = Path(args.out) if args.out else trace.with_name(f"{trace.stem}_{args.event}.png")
    print(plot_event(trace, out, fired[:1], args.event))
    return 0


def cmd_hil(args: argparse.Namespace) -> int:
    from bodyctl.config.profile import load_profile
    from bodyctl.output.device import DeviceError, open_link
    from bodyctl.tools.hil import run_hil, save_results

    prof = load_profile(args.profile)
    print("On the PS5, open a screen that shows the inputs live (the Access controller's settings under")
    print("Settings > Accessibility > Controllers, or a game you know). Hub on USB, ports plugged in.")
    print("Answer y/n for each check. Ctrl+C stops and releases everything.")

    def ask(q: str) -> bool:
        return input(f"  {q} [y/n] ").strip().lower().startswith("y")

    link = open_link(args.port)
    try:
        h = link.hello()
        if h.node_id != 10:
            print(f"that's node {h.node_id}, not the hub", file=sys.stderr)
            return 1
        results = run_hil(link, prof, ask, args.hold)
    except (DeviceError, KeyboardInterrupt) as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 1
    finally:
        link.close()
    bad = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(bad)}/{len(results)} checks ok -> {save_results(results)}")
    for r in bad:
        print(f"  FAILED {r['port']} {r['check']} ({r['expect']})")
    return 0 if not bad else 1


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

    sp = add("device", "Talk to a node, dongle or hub over USB serial", cmd_device)
    dsub = sp.add_subparsers(dest="device_command", metavar="<action>", required=True)

    def device_parser(name: str, help_: str) -> argparse.ArgumentParser:
        d = dsub.add_parser(name, help=help_, description=help_)
        _link_args(d)
        return d

    push = device_parser("push-profile", "Compile a profile and push it to the device (stored in flash)")
    push.add_argument("profile")
    device_parser("info", "Show the device's HELLO (firmware version, role)")
    device_parser("stats", "Print the device's per-stage latency histograms")
    device_parser("stats-reset", "Clear the device's latency histograms")
    device_parser("calibrate", "Redo the standing calibration (stand still facing the TV)")
    device_parser("marker", "Flash the marker LED once (camera framing check)")
    mode = device_parser("mode", "Switch the game mode (on_foot, car, heli, swim, qte, paused...)")
    mode.add_argument("mode")

    sp = add("dashboard", "Live view of a device: IMU, events, cadence, lean, pad", cmd_dashboard)
    _link_args(sp, required=False)
    sp.add_argument("-o", "--out", default="data/sessions", help="where `r` saves recordings")

    sp = add("record", "Record a device session to a .bptrace file", cmd_record)
    _link_args(sp)
    sp.add_argument("-o", "--out", default="data/sessions")
    sp.add_argument("--tag", default="session", help="file name prefix, e.g. jump_drill")
    sp.add_argument("--seconds", type=float, help="stop after this long (default: Ctrl+C)")

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

    sp = add("plot", "Graph an event in a trace: the dip, where the node fires, take-off", cmd_plot)
    sp.add_argument("trace")
    sp.add_argument("--event", default="jump", help="event to centre on (jump, run_start, crouch...)")
    sp.add_argument("-o", "--out", help="PNG path (default: next to the trace)")
    sp.add_argument("--profile", default="config/profiles/gta5.yaml")

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
    cmp_.add_argument("--c-header", action="store_true", help="write a C++ header (built-in default profile)")
    cmp_.set_defaults(func=cmd_profile_compile)

    sp = add("launch-chiaki", "Start chiaki-ng with the SDL mapping for the node's HID gamepad", cmd_chiaki)
    sp.add_argument("--exe", help="path to chiaki-ng (found automatically if installed normally)")
    sp.add_argument("--print", action="store_true", help="only print the SDL mapping string")
    sp.add_argument(
        "--vid", type=lambda v: int(v, 0), default=None, help="USB vendor id (default XIAO 0x2886)"
    )
    sp.add_argument("--pid", type=lambda v: int(v, 0), default=None, help="USB product id (default 0x0056)")
    sp = add("hil", "Bench test: the hub drives each port; confirm what the PS5 shows", cmd_hil)
    sp.add_argument("--port", required=True, help="the hub's serial port")
    sp.add_argument("--profile", default="config/profiles/gta5.yaml")
    sp.add_argument("--hold", type=float, default=1.0, help="seconds each output is held before asking")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
