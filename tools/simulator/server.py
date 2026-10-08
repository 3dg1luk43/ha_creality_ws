"""The running simulator: listeners, sessions, the clock, faults, control API.

Ports, as on a real printer:

* 9999  WebSocket telemetry and commands
* 8000  WebRTC signalling (`POST /call/webrtc_local`) on WebRTC models
* 8080  mjpg-streamer on MJPEG models
* 80    the web UI and the print preview (`/downloads/original/...`)
* 7125  Moonraker, on the K2 Base only

and one that is not: the control port (default 8099), which serves the web UI
at `/ui` and the JSON API, and stays up while the simulated printer is
switched off. `/ui` and the old `/test/*` endpoints are also served on :8000
for compatibility while the printer is on.

Listeners other than 9999 and the two HTTP ports are optional: a port that
cannot be bound (80 without root, say) is logged and left out.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http
import json
import logging
import math
import time
from email.utils import formatdate
from pathlib import Path
from typing import Any

from aiohttp import web

from .clock import Clock
from .frames import FrameStream
from .png import preview_png
from .printer import PrinterState
from .profiles import PROFILES
from .protocol import MessageLog, Protocol

LOGGER = logging.getLogger("simulator")
UI_DIR = Path(__file__).resolve().parent / "ui"
WEB_SERVER_HEADER = "httpd/1.24.3.15"
TICK_SECONDS = 0.2

# What a booting printer sends blank until the value next changes (#121).
BOOT_BLANK_FIELDS = ["nozzleTemp", "bedTemp0", "boxTemp", "targetNozzleTemp", "targetBedTemp0"]


def _seconds(name: str, value: Any) -> float:
    """A non-negative, finite number of seconds; booleans are not numbers here.

    `float("inf")` and `float("nan")` parse, and either one in /api/state is
    invalid JSON that the control UI cannot read (R77 review)."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite number, not negative")
    return number


class Settings:
    def __init__(self, frames: str = "delta"):
        self.frames = frames
        self.printer_heartbeat = False  # the old simulator sent its own
        self.first_frame_delay = 0.0
        self.silent = False  # accept connections, send nothing
        self.reject_sets = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Session:
    """One WebSocket client."""

    def __init__(self, sim: "Simulator", conn: Any):
        self.sim = sim
        self.conn = conn
        addr = getattr(conn, "remote_address", None) or ("?", 0)
        self.peer = f"{addr[0]}:{addr[1]}"
        self.connected_at = time.time()
        self.stream = FrameStream(sim.settings.frames)
        self.protocol = Protocol(sim.state, sim.log)

    def info(self) -> dict[str, Any]:
        return {
            "peer": self.peer,
            "connected_at": self.connected_at,
            "frames_sent": self.stream.frames_sent,
            "mode": self.stream.mode,
        }

    async def send(self, obj: Any) -> None:
        text = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
        await self.conn.send(text)
        if not (isinstance(obj, dict) and obj.keys() <= {"nozzleTemp", "bedTemp0", "boxTemp"}):
            # Temperature-only deltas are most of the traffic; the log keeps the rest.
            self.sim.log.add("out", self.peer, obj)

    async def run(self) -> None:
        sender = None
        try:
            if self.sim.settings.first_frame_delay:
                await asyncio.sleep(self.sim.settings.first_frame_delay)
            if not self.sim.settings.silent:
                await self.send(self.stream.first(self.sim.state.snapshot()))
            sender = asyncio.create_task(self._sender())
            async for raw in self.conn:
                if self.sim.settings.silent:
                    continue
                self.protocol.reject_sets = self.sim.settings.reject_sets
                for reply in self.protocol.handle(raw, self.stream, self.peer):
                    await self.send(reply)
        except Exception as exc:  # pylint: disable=broad-except
            if type(exc).__name__ not in ("ConnectionClosedOK", "ConnectionClosedError", "ConnectionClosed"):
                LOGGER.warning("session %s ended: %r", self.peer, exc)
        finally:
            if sender:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender

    async def _sender(self) -> None:
        heartbeat_at = time.monotonic()
        while True:
            self.stream.mode = self.sim.settings.frames
            await asyncio.sleep(2.0 if self.stream.mode == "full" else 1.0)
            if self.sim.settings.silent:
                continue
            frame = self.stream.next(self.sim.state.snapshot())
            if frame:
                await self.send(frame)
            if self.sim.settings.printer_heartbeat and time.monotonic() - heartbeat_at >= 10:
                heartbeat_at = time.monotonic()
                await self.send({"ModeCode": "heart_beat"})


class Simulator:
    def __init__(self, args: Any, clock: Clock | None = None):
        self.args = args
        self.clock = clock or Clock()
        self.state = PrinterState(
            args.model,
            simulate_print=args.simulate_print,
            sim=args.sim,
            targets=args.targets,
            deterministic=args.deterministic,
            cfs_variant=args.cfs_variant,
            clock=self.clock,
            cfs=args.cfs,
        )
        if args.stop_style:
            self.state.stop_style = args.stop_style
        if args.gcode_listing:
            self.state.gcode_listing = args.gcode_listing
        self.log = MessageLog()
        self.settings = Settings(args.frames)
        self.sessions: set[Session] = set()
        self.powered = False
        self.listeners: dict[str, str] = {}
        self._ws_server: Any = None
        self._runners: dict[str, web.AppRunner] = {}
        self._control_runner: web.AppRunner | None = None
        self._ticker: asyncio.Task | None = None
        self._webrtc = None
        self._mjpeg = None
        self._warm: asyncio.Task | None = None
        # Lifecycle actions asked for over a printer port, run after the reply.
        self._background_actions: set[asyncio.Task] = set()

    # ================================================================ lifecycle
    async def start(self) -> None:
        self._ticker = asyncio.create_task(self._tick_loop())
        if self.args.control_port:
            self._control_runner = await self._serve("control", self._control_app(), self.args.control_port,
                                                     required=False)
        await self.power_on(first=True)
        LOGGER.info("Simulator ready: %s (%s)", self.state.profile.title, self.state.profile.key)

    async def stop(self) -> None:
        await self.power_off()
        if self._control_runner:
            await self._control_runner.cleanup()
        if self._ticker:
            self._ticker.cancel()

    async def _tick_loop(self) -> None:
        # One clock for the printer, independent of clients: the old simulator
        # ticked per connection, so every reconnect sped it up, and with no
        # client connected no time passed at all.
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                self.state.tick()
            except Exception:  # pylint: disable=broad-except
                LOGGER.exception("tick failed")

    async def power_on(self, first: bool = False, boot_blanks: float = 0.0) -> None:
        if self.powered:
            return
        if not first:
            self.state.clear_job()
            if boot_blanks:
                self.state.blank_fields(BOOT_BLANK_FIELDS, boot_blanks)
        await self._start_ws()
        try:
            await self._start_printer_http()
        except OSError:
            # Half a printer is worse than none: close the WebSocket again.
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
            self.listeners["ws"] = "off"
            raise
        self.powered = True
        if first:
            self._warm = asyncio.create_task(self._warm_camera())
        self.state._log("power on" + (f", blanks for {boot_blanks:.0f} s" if boot_blanks else ""))

    async def power_off(self) -> None:
        """The plug is pulled: connections die without a close frame, and
        nothing answers on any printer port until power_on."""
        if not self.powered:
            return
        await self.drop(clean=False)
        if self._ws_server is not None:
            self._ws_server.close(close_connections=False)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._ws_server.wait_closed(), 5)
            self._ws_server = None
        for name, runner in list(self._runners.items()):
            with contextlib.suppress(Exception):
                await runner.cleanup()
            self.listeners[name] = "off"
        self._runners.clear()
        self.listeners["ws"] = "off"
        self.powered = False
        self.state._log("power off")

    async def drop(self, clean: bool = True) -> None:
        for session in list(self.sessions):
            with contextlib.suppress(Exception):
                if clean:
                    await session.conn.close(1000, "simulator drop")
                else:
                    session.conn.transport.abort()

    # ================================================================ WebSocket
    async def _start_ws(self) -> None:
        from websockets.asyncio.server import serve

        def process_request(connection, request):
            if (request.headers.get("Upgrade") or "").lower() != "websocket":
                return connection.respond(http.HTTPStatus.METHOD_NOT_ALLOWED,
                                          "This endpoint expects a WebSocket upgrade.\n")
            return None

        async def handler(conn):
            session = Session(self, conn)
            self.sessions.add(session)
            self.state._log(f"client connected: {session.peer}")
            try:
                await session.run()
            finally:
                self.sessions.discard(session)
                self.state._log(f"client disconnected: {session.peer}")

        self._ws_server = await serve(
            handler, self.args.host, self.args.ws_port,
            ping_interval=None, process_request=process_request, max_size=None,
            server_header=WEB_SERVER_HEADER,
        )
        self.listeners["ws"] = f":{self.args.ws_port}"

    # ================================================================ HTTP
    async def _serve(self, name: str, app: web.Application, port: int, *, required: bool) -> web.AppRunner | None:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, self.args.host, port).start()
        except OSError as exc:
            await runner.cleanup()
            self.listeners[name] = f"unavailable ({exc.strerror or exc})"
            if required:
                raise
            LOGGER.warning("%s listener on :%d not started: %s", name, port, exc)
            return None
        self.listeners[name] = f":{port}"
        return runner

    async def _start_printer_http(self) -> None:
        a = self.args
        profile = self.state.profile
        runner = await self._serve("http", self._printer_app(), a.http_port, required=True)
        self._runners["http"] = runner
        if profile.camera == "mjpeg" and a.mjpeg_port:
            runner = await self._serve("mjpeg", self._mjpeg_app(), a.mjpeg_port, required=False)
            if runner:
                self._runners["mjpeg"] = runner
        if a.web_port:
            runner = await self._serve("web", self._web_app(), a.web_port, required=False)
            if runner:
                self._runners["web"] = runner
        if profile.moonraker and a.moonraker_port:
            runner = await self._serve("moonraker", self._moonraker_app(), a.moonraker_port, required=False)
            if runner:
                self._runners["moonraker"] = runner

    async def _warm_camera(self) -> None:
        camera = self.state.profile.camera
        if camera == "none":
            return
        try:
            from .cameras import warm
        except ImportError as exc:
            LOGGER.info("no camera media (%s)", exc)
            return
        a = self.args
        await warm(camera, ffmpeg_bin=a.ffmpeg_bin, video_source=a.video_source,
                   mjpeg=self._mjpeg_camera() if camera == "mjpeg" else None, **self._camera_options())

    def _camera_options(self) -> dict[str, Any]:
        a = self.args
        return {"width": a.width, "height": a.height, "fps": a.fps}

    def _webrtc_signalling(self):
        if self._webrtc is None:
            from .cameras import WebRtcSignalling

            a = self.args
            self._webrtc = WebRtcSignalling(audio=not a.no_audio, video_source=a.video_source,
                                            ffmpeg_bin=a.ffmpeg_bin, prefer_codec=a.prefer_codec,
                                            **self._camera_options())
        return self._webrtc

    def _mjpeg_camera(self):
        if self._mjpeg is None:
            from .cameras import MjpegCamera

            a = self.args
            self._mjpeg = MjpegCamera(video_source=a.video_source, ffmpeg_bin=a.ffmpeg_bin,
                                      **self._camera_options())
        return self._mjpeg

    def _printer_app(self) -> web.Application:
        app = web.Application()
        profile = self.state.profile

        async def root(_request):
            return web.Response(text=(
                f"Creality printer simulator: {profile.title}\n"
                f"Control: /ui and /api/state (also on the control port)\n"
            ))

        async def call(request):
            if profile.camera != "webrtc":
                return web.Response(status=404, text="no WebRTC camera on this model")
            try:
                signalling = self._webrtc_signalling()
            except ImportError as exc:
                return web.Response(status=503, text=f"WebRTC needs aiortc, av and numpy: {exc}")
            return await signalling.handle_call(request)

        async def probe(_request):
            if profile.camera != "webrtc":
                return web.Response(status=404, text="no WebRTC camera on this model")
            # A real printer answers GET with 405, which says "it is here".
            return web.Response(status=405, text="Method Not Allowed")

        async def legacy_mjpeg(request):
            if profile.camera != "mjpeg":
                return web.Response(status=404, text="no MJPEG camera on this model")
            return await self._mjpeg_camera().handle_legacy(request)

        app.add_routes([
            web.get("/", root),
            web.post("/call/webrtc_local", call),
            web.get("/call/webrtc_local", probe),
            web.get("/stream.mjpeg", legacy_mjpeg),
        ])
        self._add_control_routes(app, detach_lifecycle=True)
        return app

    def _mjpeg_app(self) -> web.Application:
        app = web.Application()

        async def handle(request):
            return await self._mjpeg_camera().handle(request)

        app.add_routes([web.get("/", handle)])
        return app

    def _web_app(self) -> web.Application:
        """The printer's own web server: its UI page and the print preview."""
        app = web.Application()

        def headers(etag: str | None = None, modified: float | None = None) -> dict[str, str]:
            out = {"Server": WEB_SERVER_HEADER}
            if etag:
                out["ETag"] = f'"{etag}"'
            if modified:
                out["Last-Modified"] = formatdate(modified, usegmt=True)
            return out

        async def index(_request):
            body = (
                "<!doctype html><html><head><title>Creality Print</title></head><body>"
                f"<h1>{self.state.profile.model}</h1>"
                '<a href="/downloads/original/current_print_image.png">current print</a>'
                "</body></html>"
            )
            return web.Response(text=body, content_type="text/html", headers=headers())

        async def preview(request):
            job = self.state.job
            name = job.name if job else self.state.last_job_name
            if not name:
                return web.Response(status=404, headers=headers())
            started = job.started_wall if job else 0.0
            etag = hashlib.md5(f"{name}{started}".encode()).hexdigest()[:16]
            if request.headers.get("If-None-Match", "").strip('"') == etag:
                return web.Response(status=304, headers=headers(etag, started))
            body = b"" if request.method == "HEAD" else preview_png(f"{name}{started}")
            return web.Response(body=body, content_type="image/png", headers=headers(etag, started))

        async def thumbnail(request):
            name = request.match_info["name"]
            return web.Response(body=preview_png(name, 96, 96), content_type="image/png", headers=headers())

        app.add_routes([
            web.get("/", index),
            web.route("HEAD", "/downloads/original/current_print_image.png", preview),
            web.get("/downloads/original/current_print_image.png", preview, allow_head=False),
            # Creality's own spelling.
            web.get("/downloads/humbnail/{name}", thumbnail),
        ])
        return app

    def _moonraker_app(self) -> web.Application:
        app = web.Application()

        async def query(request):
            # As Moonraker's `_object_parser` reads a GET: every query key is an
            # object name, its value an optional comma list of attributes, and
            # only `_`, `token`, `access_token` and `connection_id` are skipped.
            # `?objects=...` therefore asks for an object called "objects",
            # which Klippy does not have (R41).
            available = self.state.moonraker_status()
            status = {}
            for name, value in request.query.items():
                if name in ("_", "token", "access_token", "connection_id") or name not in available:
                    continue
                attrs = [a for a in value.split(",") if a] if value else None
                status[name] = {k: v for k, v in available[name].items() if attrs is None or k in attrs}
            return web.json_response({"result": {"eventtime": time.monotonic(), "status": status}})

        async def info(_request):
            return web.json_response({"result": {"klippy_connected": True, "klippy_state": "ready"}})

        app.add_routes([
            web.get("/printer/objects/query", query),
            web.get("/server/info", info),
        ])
        return app

    # ================================================================ control
    def _control_app(self) -> web.Application:
        app = web.Application()
        self._add_control_routes(app)

        async def root(_request):
            raise web.HTTPFound("/ui/")

        app.add_routes([web.get("/", root)])
        return app

    # Shut down the printer's own ports. Asked for over one of them, the action
    # cleans up the runner serving the request, which waits for that request,
    # until aiohttp cancels it and power_off stops halfway: still "powered",
    # ports closed (CodeRabbit on #126, reproduced on the test box).
    LIFECYCLE_ACTIONS = frozenset({"power_off", "switch_profile"})

    def _add_control_routes(self, app: web.Application, *, detach_lifecycle: bool = False) -> None:
        async def test_set(request):
            try:
                payload = await request.json()
            except Exception:  # pylint: disable=broad-except
                return web.Response(status=400, text="expected a JSON object")
            if not isinstance(payload, dict):
                return web.Response(status=400, text="expected a JSON object")
            self.state.apply_overrides(payload)
            LOGGER.info("test/set applied: %s", json.dumps(payload))
            return web.json_response({"ok": True, "overrides": self.state._overrides})

        async def test_reset(_request):
            self.state.clear_overrides()
            LOGGER.info("test/reset: overrides cleared")
            return web.json_response({"ok": True})

        async def test_cfs(request):
            try:
                payload = await request.json()
            except Exception:  # pylint: disable=broad-except
                return web.Response(status=400, text="expected a JSON object")
            if not isinstance(payload, dict):
                return web.Response(status=400, text="expected a JSON object")
            try:
                box_id = int(payload.get("box_id", 1))
            except (TypeError, ValueError):
                return web.Response(status=400, text="box_id must be an integer")
            materials = payload.get("materials")
            if not isinstance(materials, list):
                return web.Response(status=400, text="materials must be a list")
            if not self.state.set_cfs_materials(box_id, materials):
                return web.Response(status=404, text=f"no CFS box with id {box_id}")
            return web.json_response({"ok": True})

        async def test_state(_request):
            return web.json_response(self.state.snapshot())

        async def api_state(_request):
            return web.json_response(self.describe())

        async def api_log(request):
            try:
                since = int(request.query.get("since", "0"))
            except ValueError:
                since = 0
            kind = request.query.get("dir")
            items = [i for i in self.log.since(since) if not kind or i["dir"] == kind]
            return web.json_response({"seq": self.log.seq, "items": items})

        async def api_action(request):
            try:
                payload = await request.json()
            except Exception:  # pylint: disable=broad-except
                return web.json_response({"ok": False, "error": "expected a JSON object"}, status=400)
            if not isinstance(payload, dict) or "action" not in payload:
                return web.json_response({"ok": False, "error": "expected {\"action\": ...}"}, status=400)
            try:
                action = str(payload.pop("action"))
                if detach_lifecycle and action in self.LIFECYCLE_ACTIONS:
                    if action == "switch_profile" and str(payload.get("key")) not in PROFILES:
                        raise ValueError(f"unknown model {payload.get('key')!r}")
                    task = asyncio.create_task(self.act(action, payload))
                    self._background_actions.add(task)
                    task.add_done_callback(self._background_action_done)
                    return web.json_response({"ok": True, "scheduled": True})
                result = await self.act(action, payload)
            except (KeyError, TypeError, ValueError) as exc:
                return web.json_response({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception("action failed")
                return web.json_response({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)
            return web.json_response({"ok": True, **(result or {})})

        async def ui_index(_request):
            return web.FileResponse(UI_DIR / "index.html")

        app.add_routes([
            web.post("/test/set", test_set),
            web.post("/test/reset", test_reset),
            web.post("/test/cfs", test_cfs),
            web.get("/test/state", test_state),
            web.get("/api/state", api_state),
            web.get("/api/log", api_log),
            web.post("/api/action", api_action),
            web.get("/ui", ui_index),
            web.get("/ui/", ui_index),
            web.static("/ui/", UI_DIR),
        ])

    def describe(self) -> dict[str, Any]:
        return {
            "powered": self.powered,
            "telemetry": self.state.snapshot(),
            "sim": self.state.describe(),
            "cfs": self.state.cfs.info()["boxsInfo"]["materialBoxs"],
            "settings": self.settings.as_dict(),
            "listeners": self.listeners,
            "sessions": [s.info() for s in self.sessions],
            "profiles": {k: p.title for k, p in PROFILES.items()},
            "log_seq": self.log.seq,
        }

    # ================================================================ actions
    SETTINGS = {
        "frames": ("delta", "full"),
        "printer_heartbeat": bool,
        "first_frame_delay": float,
        "silent": bool,
        "reject_sets": bool,
        "stop_style": PrinterState.STOP_STYLES,
        "self_test_style": ("withSelfTest", "state2"),
        "gcode_listing": ("v2", "legacy", "none"),
        "finished_reset_seconds": float,
    }

    def _apply_settings(self, args: dict[str, Any]) -> None:
        unknown = set(args) - set(self.SETTINGS)
        if unknown:
            raise ValueError(f"unknown settings {sorted(unknown)}; known: {sorted(self.SETTINGS)}")
        # Every value is checked before any is applied, so a bad call changes nothing.
        checked: dict[str, Any] = {}
        for key, value in args.items():
            kind = self.SETTINGS[key]
            if kind is bool:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
            elif kind is float:
                value = _seconds(key, value)
            elif value not in kind:
                raise ValueError(f"{key} is one of {list(kind)}")
            checked[key] = value
        for key, value in checked.items():
            if key == "finished_reset_seconds":
                self.state.sim.finished_reset_seconds = value
            elif hasattr(self.settings, key):
                setattr(self.settings, key, value)
            else:
                setattr(self.state, key, value)

    def _background_action_done(self, task: asyncio.Task) -> None:
        self._background_actions.discard(task)
        if not task.cancelled() and task.exception() is not None:
            LOGGER.error("background action failed", exc_info=task.exception())

    async def switch_profile(self, key: str) -> None:
        """Become another printer: power off, swap the model, power on clean."""
        if key not in PROFILES:
            raise ValueError(f"unknown model {key!r}")
        await self.power_off()
        old = self.state
        self.args.model = key
        self.state = PrinterState(
            key, simulate_print=False, sim=old.sim, targets=self.args.targets,
            deterministic=old.deterministic, cfs_variant=old.cfs_variant, clock=self.clock, cfs="auto",
        )
        await self.power_on(first=True)
        self.state._log(f"switched to {PROFILES[key].title}")

    async def act(self, action: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Everything the control UI can do, by name. Unknown names raise.

        Actions that can find nothing to do say so with `applied`, rather than
        answering ok for a stop with no print running."""
        s = self.state

        def arg(key: str, default: Any = None) -> Any:
            return args.get(key, default)

        def required(key: str) -> Any:
            if args.get(key) is None:
                raise ValueError(f"{action} needs {key!r}")
            return args[key]

        if action == "start_print":
            seconds = arg("seconds")
            if seconds is not None and _seconds("seconds", seconds) < 1:
                raise ValueError("seconds must be at least 1")
            for key, top in (("layers", 10000), ("objects", 100)):
                if arg(key) is not None and not (isinstance(arg(key), int) and 1 <= arg(key) <= top):
                    raise ValueError(f"{key} must be a whole number from 1 to {top}")
            if arg("self_test") is not None and not isinstance(arg("self_test"), bool):
                raise ValueError("self_test must be true, false or left out")
            s.start_print(name=str(arg("name") or "demo.gcode"), seconds=seconds, layers=arg("layers"),
                          objects=arg("objects"), self_test=arg("self_test"))
        elif action == "pause":
            return {"applied": s.pause()}
        elif action == "resume":
            return {"applied": s.resume()}
        elif action == "stop":
            return {"applied": s.stop(arg("style"))}
        elif action == "finish_now":
            return {"applied": s.finish_now()}
        elif action == "cfs_swap":
            return {"applied": s.cfs_swap(_seconds("seconds", arg("seconds", 20)))}
        elif action == "clear_job":
            s.clear_job()
        elif action == "set_error":
            s.set_error(int(arg("code", 0)), int(arg("key", 0)))
        elif action == "clear_error":
            s.clear_error()
        elif action == "runout":
            s.runout()
        elif action == "resolve_runout":
            s.resolve_runout(bool(arg("resume", False)))
        elif action == "home":
            s.set_autohome(str(arg("axes", "XYZ")), _seconds("seconds", arg("seconds", 4)))
        elif action == "blank_fields":
            s.blank_fields(arg("fields") or BOOT_BLANK_FIELDS, _seconds("seconds", arg("seconds", 20)))
        elif action == "targets":
            temps = {key: _seconds(key, arg(key)) for key in ("nozzle", "bed", "box") if arg(key) is not None}
            applied = True
            if "nozzle" in temps:
                s.set_nozzle_temp(temps["nozzle"])
            if "bed" in temps:
                s.set_bed_temp(temps["bed"])
            if "box" in temps:
                applied = s.set_box_temp(temps["box"])
            return {"applied": applied}
        elif action == "light":
            return {"applied": s.set_light(bool(arg("on", True)))}
        elif action == "cfs_attach":
            s.cfs.attach()
        elif action == "cfs_detach":
            s.cfs.detach()
        elif action == "cfs_add_box":
            return {"box_id": s.cfs.add_box()}
        elif action == "cfs_remove_box":
            return {"applied": s.cfs.remove_box(int(required("box_id")))}
        elif action == "cfs_select":
            return {"applied": s.cfs.select(int(required("box_id")), int(required("slot_id")))}
        elif action == "cfs_percent":
            return {"applied": s.cfs.set_percent(int(required("box_id")), int(required("slot_id")),
                                                 int(required("percent")))}
        elif action == "cfs_echo":
            mode = str(arg("mode"))
            if mode not in ("as_sent", "padded"):
                raise ValueError("mode is as_sent or padded")
            s.cfs.echo_colour = mode
        elif action == "set_override":
            key = required("key")
            s.apply_overrides({str(key): arg("value")})
        elif action == "clear_overrides":
            s.clear_overrides()
        elif action == "power_off":
            await self.power_off()
        elif action == "power_on":
            await self.power_on(boot_blanks=_seconds("boot_blanks", arg("boot_blanks", 0) or 0))
        elif action == "drop":
            await self.drop(clean=bool(arg("clean", True)))
        elif action == "settings":
            self._apply_settings(args)
        elif action == "switch_profile":
            await self.switch_profile(str(required("key")))
        else:
            raise ValueError(f"unknown action {action!r}")
        return None
