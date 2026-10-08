"""Command line: the old simulator's flags, plus the new listeners and modes."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from .printer import SimOptions
from .profiles import PROFILES

LOGGER = logging.getLogger("simulator")


def build_argparser() -> argparse.ArgumentParser:
    epilog = (
        "\nModels:\n" + "".join(f"  {k:<11} {p.title}\n" for k, p in PROFILES.items()) +
        "\nPorts (a real printer's, except the control port):\n"
        "  9999 WebSocket, 8000 WebRTC signalling, 8080 MJPEG, 80 web/preview,\n"
        "  7125 Moonraker (K2 Base), 8099 control UI and API (/ui, /api/state)\n\n"
        "Examples:\n"
        "  %(prog)s --model k2plus --simulate-print --print-seconds 600\n"
        "  %(prog)s --model k1c --simulate-print --web-port 8081 --mjpeg-port 8080\n"
    )
    p = argparse.ArgumentParser(
        description="Creality printer simulator (WebSocket telemetry, cameras, web, Moonraker)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--ws-port", type=int, default=9999)
    p.add_argument("--http-port", type=int, default=8000)
    p.add_argument("--mjpeg-port", type=int, default=8080, help="mjpg-streamer on MJPEG models; 0 disables")
    p.add_argument("--web-port", type=int, default=80, help="web UI and print preview; 0 disables")
    p.add_argument("--moonraker-port", type=int, default=7125, help="Moonraker on the K2 Base; 0 disables")
    p.add_argument("--control-port", type=int, default=8099,
                   help="control UI and API, up while the printer is off; 0 disables")
    p.add_argument("--model", default="k2plus", choices=list(PROFILES.keys()), help="Printer model to emulate")
    p.add_argument("--simulate-print", action="store_true", help="Start a print at boot")
    p.add_argument("--print-seconds", type=int, default=600, help="Print duration in seconds")
    p.add_argument("--layers", type=int, default=120)
    p.add_argument("--objects", type=int, default=6)
    p.add_argument("--self-test-seconds", type=int, default=5,
                   help="Self-test length at print start, on models that self-test")
    p.add_argument("--finished-reset-seconds", type=float, default=0.0,
                   help="Reset a finished job's progress to 0 after this long (R10); 0 never")

    # telemetry shape
    p.add_argument("--frames", choices=["delta", "full"], default="delta",
                   help="'delta' (a real printer): a full frame on connect, then changed keys. "
                        "'full': every frame complete, the old simulator's way.")
    p.add_argument("--cfs", choices=["auto", "on", "off"], default="auto",
                   help="Attach a CFS: 'auto' follows the model")
    p.add_argument("--stop-style", choices=["state4", "state0", "clear"], default=None,
                   help="What a stop looks like; default follows the model")
    p.add_argument("--gcode-listing", choices=["v2", "legacy", "none"], default=None,
                   help="Reply to reqGcodeFile; default follows the model")

    # video options
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--low-power", action="store_true", help="Use 640x360 @ 10 fps for low-end hardware")
    p.add_argument("--no-audio", action="store_true")

    # temp targets for a print
    p.add_argument("--target-nozzle", type=float, default=250, help="Nozzle target while printing (°C)")
    p.add_argument("--target-bed", type=float, default=70, help="Bed target while printing (°C)")
    p.add_argument("--target-box", type=float, default=50, help="Chamber target while printing (°C, if supported)")

    # motion bounds
    p.add_argument("--max-x", type=float, default=235.0)
    p.add_argument("--max-y", type=float, default=235.0)
    p.add_argument("--max-z", type=float, default=250.0)

    p.add_argument("--debug", action="store_true")
    p.add_argument("--video-source", choices=["auto", "h264", "synthetic", "ffmpeg"], default="auto",
                   help="Video generator. 'auto' (default) sends pre-encoded H.264 with a 1s GOP when "
                        "the peer offers H.264 -- required for Home Assistant's HLS/recording pipeline "
                        "-- and falls back to synthetic frames otherwise.")
    p.add_argument("--ffmpeg-bin", default="ffmpeg", help="Path to ffmpeg binary")
    p.add_argument("--deterministic", action="store_true",
                   help="Remove randomness (temperature noise, fan jitter, motion) so telemetry is "
                        "reproducible; time-derived fields still depend on when you sample.")
    p.add_argument("--cfs-variant", choices=["default", "edge"], default="default",
                   help="'edge' adds awkward CFS payloads: a 6-char colour, a slot with no vendor, a "
                        "multi-colour spool, rfid values and an empty external slot.")
    p.add_argument("--prefer-codec", choices=["h264", "vp8", "auto"], default="h264",
                   help="Video codec to answer with first. H.264 matches real K-series printers and is "
                        "required for Home Assistant HLS; 'auto' keeps aiortc's default order.")
    return p


def prepare(args: argparse.Namespace) -> argparse.Namespace:
    """Fill in what the simulator derives from the flags."""
    if getattr(args, "low_power", False) and (args.width, args.height, args.fps) == (1920, 1080, 30):
        args.width, args.height, args.fps = 640, 360, 10
    args.sim = SimOptions(
        total_print_seconds=args.print_seconds,
        total_layers=args.layers,
        total_objects=args.objects,
        self_test_seconds=args.self_test_seconds,
        max_x=args.max_x,
        max_y=args.max_y,
        max_z=args.max_z,
        finished_reset_seconds=args.finished_reset_seconds,
    )
    args.targets = {"nozzle": args.target_nozzle, "bed": args.target_bed, "box": args.target_box}
    return args


async def main_async(args: argparse.Namespace) -> None:
    from .server import Simulator

    sim = Simulator(prepare(args))
    await sim.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    prev_handler = loop.get_exception_handler()

    def quiet(loop_: asyncio.AbstractEventLoop, context: dict) -> None:
        # aioice's STUN retries raise InvalidStateError noise after a session ends.
        text = f"{context.get('message', '')} {context.get('exception')!r}"
        if not args.debug and ("Transaction.__retry" in text or "TransactionTimeout" in text
                               or ("InvalidStateError" in text and "Transaction" in text)):
            return
        if prev_handler is not None:
            prev_handler(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(quiet)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await stop.wait()
    finally:
        await sim.stop()


def main(argv: list[str] | None = None) -> None:
    parser = build_argparser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        parser.print_help()
        return
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s:%(name)s:%(message)s", datefmt="%H:%M:%S")
    if not args.debug:
        logging.getLogger("websockets").setLevel(logging.WARNING)
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass
