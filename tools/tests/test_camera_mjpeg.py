"""The MJPEG camera against a real HTTP server (R38), and the direct WebRTC candidate filter (R39).

It had no tests. Snapshots opened the whole stream for one frame, a cancelled
snapshot kept running because CancelledError was swallowed, a dark printer was
dialled on every dashboard refresh, and the live proxy opened the stream with
no timeout. These run the camera's own code against a local aiohttp server that
plays mjpg-streamer.
"""

import asyncio
import base64
import json
import sys
from types import SimpleNamespace

import pytest

from unittest.mock import MagicMock  # noqa: E402

from conftest import install_stub_attr, install_stub_module, restore_stubs  # noqa: E402

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

# The same go2rtc_client stand-in the other camera tests install. Whichever
# of them imports camera.py first fixes what it holds for the whole session.
if "go2rtc_client" not in sys.modules:
    install_stub_module(__name__, "go2rtc_client", MagicMock())
    _go2rtc_exceptions = MagicMock()

    class Go2RtcClientError(Exception):
        pass

    _go2rtc_exceptions.Go2RtcClientError = Go2RtcClientError
    install_stub_module(__name__, "go2rtc_client.exceptions", _go2rtc_exceptions)

if not hasattr(sys.modules["homeassistant.components"], "camera"):
    _camera_component = MagicMock()

    class _Camera:
        def __init__(self):
            pass

    _camera_component.Camera = _Camera
    install_stub_module(__name__, "homeassistant.components.camera", _camera_component)
    install_stub_attr(__name__, sys.modules["homeassistant.components"], "camera", _camera_component)

from custom_components.ha_creality_ws.camera import CrealityMjpegCamera  # noqa: E402


def teardown_module(_module):
    restore_stubs(__name__)

camera_mod = sys.modules[CrealityMjpegCamera.__module__]

JPEG = b"\xff\xd8" + b"\x00" * 64 + b"\xff\xd9"


@pytest.fixture(autouse=True)
def _loop():
    # Cleared afterwards: closing a loop does not uninstall it, and a closed
    # loop left installed broke whatever later called asyncio.get_event_loop().
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_the_camera_module_uses_the_real_aiohttp():
    """A stubbed aiohttp would make every test below pass for nothing."""
    assert isinstance(camera_mod.ClientError, type)
    assert camera_mod.ClientTimeout is aiohttp.ClientTimeout


class Printer:
    """mjpg-streamer: ?action=stream and, unless told otherwise, ?action=snapshot."""

    def __init__(self, *, snapshot=True, stall=False):
        self.hits: list[str] = []
        self.snapshot = snapshot
        self.stall = stall

    async def handle(self, request):
        action = request.query.get("action", "stream")
        self.hits.append(action)
        if action == "snapshot":
            if not self.snapshot:
                return web.Response(status=404)
            return web.Response(body=JPEG, content_type="image/jpeg")
        resp = web.StreamResponse(
            headers={"Content-Type": "multipart/x-mixed-replace;boundary=frame"}
        )
        await resp.prepare(request)
        if self.stall:
            await asyncio.sleep(3600)
        for _ in range(50):
            await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n")
            await asyncio.sleep(0.01)
        return resp


async def _serve(printer):
    app = web.Application()
    app.router.add_get("/", printer.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/?action=stream"


def _camera(url, session, *, off=False, available=True):
    cam = CrealityMjpegCamera.__new__(CrealityMjpegCamera)
    cam.hass = SimpleNamespace()
    cam.coordinator = SimpleNamespace(power_is_off=lambda: off, available=available)
    cam._url = url
    cam._last_frame = None
    cam._last_snapshot_ts = 0.0
    cam._snapshot_min_interval = 1.0
    cam._snapshot_lock = asyncio.Lock()
    cam._snapshot_endpoint_works = None
    return cam


def _run(scenario, printer, monkeypatch, **camera_kw):
    async def main():
        runner, url = await _serve(printer)
        session = aiohttp.ClientSession()
        monkeypatch.setattr(camera_mod, "async_get_clientsession", lambda hass: session)
        try:
            return await scenario(_camera(url, session, **camera_kw))
        finally:
            await session.close()
            await runner.cleanup()

    return asyncio.get_event_loop().run_until_complete(main())


def test_a_snapshot_asks_for_one_frame_not_the_stream(monkeypatch):
    printer = Printer()
    image = _run(lambda cam: cam.async_camera_image(), printer, monkeypatch)
    assert image == JPEG
    assert printer.hits == ["snapshot"]


def test_without_a_snapshot_endpoint_the_frame_comes_from_the_stream(monkeypatch):
    printer = Printer(snapshot=False)

    async def two_snapshots(cam):
        first = await cam.async_camera_image()
        cam._last_snapshot_ts = -10.0  # past the throttle
        second = await cam.async_camera_image()
        return first, second

    first, second = _run(two_snapshots, printer, monkeypatch)
    assert first == second == JPEG
    # The missing endpoint is asked about once, then left alone.
    assert printer.hits == ["snapshot", "stream", "stream"]


@pytest.mark.parametrize("state", [{"off": True}, {"available": False}])
def test_a_printer_that_cannot_answer_is_not_dialled(monkeypatch, state):
    printer = Printer()
    image = _run(lambda cam: cam.async_camera_image(), printer, monkeypatch, **state)
    assert printer.hits == []
    assert image.startswith(b"\xff\xd8"), "the placeholder frame, not nothing"


def test_a_cancelled_snapshot_stops(monkeypatch):
    """CancelledError was caught and turned into "no frame", so a request Home
    Assistant had given up on carried on, and its timeout never fired."""
    printer = Printer(snapshot=False, stall=True)

    async def cancel_midway(cam):
        task = asyncio.ensure_future(cam.async_camera_image())
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return True

    assert _run(cancel_midway, printer, monkeypatch) is True


def test_the_live_stream_goes_through_home_assistants_proxy(monkeypatch):
    """The hand-rolled proxy had no timeout; the helper gives up on a silent
    upstream and stops at shutdown."""
    seen = {}

    async def proxy(hass, request, web_coro, *a, **kw):
        seen["request"] = request
        resp = await web_coro
        seen["status"] = resp.status
        resp.release()
        return "proxied"

    monkeypatch.setattr(camera_mod, "async_aiohttp_proxy_web", proxy)
    printer = Printer()
    result = _run(lambda cam: cam.handle_async_mjpeg_stream("req"), printer, monkeypatch)
    assert result == "proxied"
    assert seen == {"request": "req", "status": 200}
    assert printer.hits == ["stream"]


# --------------------------------------------------------------------------- #
# Direct WebRTC: which browser ICE candidates go to the printer (R39)
# --------------------------------------------------------------------------- #

OFFER = "\r\n".join([
    "v=0",
    "o=- 1 1 IN IP4 0.0.0.0",
    "s=-",
    "t=0 0",
    "a=group:BUNDLE 0 1",
    "m=audio 9 UDP/TLS/RTP/SAVPF 111",
    "a=mid:0",
    "m=video 9 UDP/TLS/RTP/SAVPF 96",
    "a=mid:1",
    "",
])


def _webrtc_camera():
    from custom_components.ha_creality_ws.camera import CrealityWebRTCCamera

    return CrealityWebRTCCamera.__new__(CrealityWebRTCCamera)


def _candidate(port, mid, index):
    """Shaped like Home Assistant's webrtc_models.RTCIceCandidateInit."""
    return SimpleNamespace(
        candidate=f"candidate:{port} 1 udp 1 10.0.0.2 {port} typ host",
        sdp_mid=mid,
        sdp_m_line_index=index,
    )


def test_the_video_transports_candidates_go_to_the_printer():
    """The "balanced" policy gathers per m-line; the answer keeps the video's
    transport, so the audio-tagged candidates are for a transport that is
    thrown away. Read as sdpMid/sdpMLineIndex the tags were always None and
    every candidate went (R39)."""
    lines = _webrtc_camera()._candidate_lines_for_video(
        [_candidate(5000, "0", 0), _candidate(5002, "1", 1), _candidate(5004, "1", 1)], OFFER
    )
    assert lines == [
        "a=candidate:5002 1 udp 1 10.0.0.2 5002 typ host",
        "a=candidate:5004 1 udp 1 10.0.0.2 5004 typ host",
    ]


def test_max_bundle_candidates_are_all_kept():
    """Gathered once, tagged with the bundle's first m-line (the audio), and
    carrying the video: dropping them would leave the printer none."""
    lines = _webrtc_camera()._candidate_lines_for_video(
        [_candidate(5000, "0", 0), _candidate(5001, "0", 0)], OFFER
    )
    assert len(lines) == 2


def test_candidates_in_json_form_are_read_too():
    lines = _webrtc_camera()._candidate_lines_for_video(
        [
            {"candidate": "candidate:5000 1 udp 1 10.0.0.2 5000 typ host", "sdpMid": "0", "sdpMLineIndex": 0},
            {"candidate": "candidate:5002 1 udp 1 10.0.0.2 5002 typ host", "sdpMid": "1", "sdpMLineIndex": 1},
        ],
        OFFER,
    )
    assert lines == ["a=candidate:5002 1 udp 1 10.0.0.2 5002 typ host"]


# --------------------------------------------------------------------------- #
# Direct WebRTC: a browser's hidden address (R87)
# --------------------------------------------------------------------------- #

MDNS_OFFER = "\r\n".join([
    "v=0",
    "o=- 1 1 IN IP4 0.0.0.0",
    "s=-",
    "t=0 0",
    "a=group:BUNDLE 0",
    "m=video 9 UDP/TLS/RTP/SAVPF 96",
    "a=mid:0",
    "a=candidate:1 1 udp 2113937151 0f1e2d3c-aaaa.local 54321 typ host generation 0",
    "a=candidate:2 1 udp 1677729535 198.51.100.7 4044 typ srflx raddr 0.0.0.0 rport 0",
    "a=candidate:3 1 udp 2113937151 gone-away.local 54322 typ host",
    "",
])


def test_a_browsers_hidden_address_is_resolved_before_the_printer_sees_it(monkeypatch):
    """Browsers offer `<uuid>.local` instead of their address; a K1C never
    checks it, starts no ICE of its own, and the stream stays at 0 bytes."""
    asked = []

    async def resolve(hass, names):
        asked.append(names)
        return {"0f1e2d3c-aaaa.local": "192.168.0.50"}

    monkeypatch.setattr(camera_mod, "_async_resolve_mdns_hosts", resolve)
    cam = _webrtc_camera()
    cam.hass = SimpleNamespace()
    out = asyncio.run(cam._printer_reachable_candidates(MDNS_OFFER))
    assert asked == [["0f1e2d3c-aaaa.local", "gone-away.local"]]
    assert "a=candidate:1 1 udp 2113937151 192.168.0.50 54321 typ host generation 0\r\n" in out
    # A name that did not answer, and every other line, is left as it was.
    assert "a=candidate:3 1 udp 2113937151 gone-away.local 54322 typ host\r\n" in out
    assert out.replace("192.168.0.50", "0f1e2d3c-aaaa.local") == MDNS_OFFER


def test_an_offer_without_hidden_addresses_does_no_lookup(monkeypatch):
    async def resolve(hass, names):  # pragma: no cover - must not run
        raise AssertionError("looked up")

    monkeypatch.setattr(camera_mod, "_async_resolve_mdns_hosts", resolve)
    plain = MDNS_OFFER.replace("0f1e2d3c-aaaa.local", "192.168.0.50").replace("gone-away.local", "192.168.0.51")
    assert asyncio.run(_webrtc_camera()._printer_reachable_candidates(plain)) == plain


def test_the_printer_gets_the_resolved_address_in_the_offer_it_is_posted(monkeypatch):
    """Wired into the signalling the way Home Assistant drives it."""
    answer = "\r\n".join([
        "v=0", "o=- 2 2 IN IP4 0.0.0.0", "s=-", "t=0 0", "a=group:BUNDLE 0",
        "m=video 9 UDP/TLS/RTP/SAVPF 96", "a=mid:0", "a=sendonly", "",
    ])
    posted = []

    async def handle(request):
        posted.append(json.loads(base64.b64decode(await request.text()))["sdp"])
        body = base64.b64encode(json.dumps({"type": "answer", "sdp": answer}).encode()).decode()
        return web.Response(text=body)

    async def resolve(hass, names):
        return {name: "192.168.0.50" for name in names}

    async def main():
        app = web.Application()
        app.router.add_post("/call/webrtc_local", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        session = aiohttp.ClientSession()
        monkeypatch.setattr(camera_mod, "async_get_clientsession", lambda hass: session)
        monkeypatch.setattr(camera_mod, "_async_resolve_mdns_hosts", resolve)
        cam = _webrtc_camera()
        cam.hass = SimpleNamespace()
        cam._direct_sessions = {}
        cam._upstream_signaling_url = f"http://127.0.0.1:{port}/call/webrtc_local"
        sent = []
        try:
            await cam._handle_direct_webrtc_offer(MDNS_OFFER, "s1", lambda msg: sent.append(msg.as_dict()))
        finally:
            await session.close()
            await runner.cleanup()
        return sent

    sent = asyncio.run(main())
    assert sent and sent[-1]["type"] == "answer"
    assert " 192.168.0.50 54321 typ host" in posted[0]
    assert ".local" not in posted[0]


def test_the_printer_endpoint_probe_takes_any_http_answer_and_caches_it(monkeypatch):
    """The check before a go2rtc snapshot (#133): one GET, no peer connection.
    The K1C answers 200 "{}", the simulator 405; a printer whose WebRTC server
    is down refuses the connection."""

    async def main():
        async def handle(request):
            return web.Response(status=405)

        app = web.Application()
        app.router.add_get("/call/webrtc_local", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        session = aiohttp.ClientSession()
        monkeypatch.setattr(camera_mod, "async_get_clientsession", lambda hass: session)
        cam = _webrtc_camera()
        cam.hass = SimpleNamespace()
        cam._go2rtc_source = None
        cam._webrtc_probe = None
        cam._upstream_signaling_url = f"http://127.0.0.1:{port}/call/webrtc_local"
        try:
            up = await cam._printer_webrtc_answering()
            await runner.cleanup()
            cached = await cam._printer_webrtc_answering()
            cam._webrtc_probe = None
            down = await cam._printer_webrtc_answering()
            cam._go2rtc_source = "rtsp://nvr/stream"
            cam._webrtc_probe = None
            custom = await cam._printer_webrtc_answering()
        finally:
            await session.close()
        return up, cached, down, custom

    assert asyncio.run(main()) == (True, True, False, True)
