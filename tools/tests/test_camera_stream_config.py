import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()

# Mock go2rtc_client if needed
if "go2rtc_client" not in sys.modules:
    sys.modules["go2rtc_client"] = MagicMock()
    sys.modules["go2rtc_client.exceptions"] = MagicMock()

# Mock homeassistant.components.camera
if "homeassistant.components" in sys.modules:
    components_mod = sys.modules["homeassistant.components"]
    if not hasattr(components_mod, "camera"):
        cam_mod = MagicMock()
        class MockCamera:
            def __init__(self):
                pass
        cam_mod.Camera = MockCamera
        cam_mod.CameraEntityFeature = MagicMock()
        sys.modules["homeassistant.components.camera"] = cam_mod
        components_mod.camera = cam_mod
else:
    # Fallback
    mock_ha = MagicMock()
    sys.modules["homeassistant"] = mock_ha
    sys.modules["homeassistant.core"] = MagicMock()
    sys.modules["homeassistant.components"] = MagicMock()
    cam_mod = MagicMock()
    class MockCamera:
        def __init__(self):
            pass
    cam_mod.Camera = MockCamera
    cam_mod.CameraEntityFeature = MagicMock()
    sys.modules["homeassistant.components.camera"] = cam_mod
    sys.modules["homeassistant.components"].camera = cam_mod

from custom_components.ha_creality_ws.camera import CrealityWebRTCCamera

def test_ensure_stream_configured_uses_creality_format():
    import asyncio
    # K2 signaling uses Creality's JSON-wrapped SDP, not raw WHEP — the
    # `#format=creality` fragment must remain on the go2rtc source. See #87/#88.

    mock_go2rtc_client = MagicMock()
    mock_go2rtc_client.streams = MagicMock()
    mock_go2rtc_client.streams.list = AsyncMock(return_value={})
    mock_go2rtc_client.streams.add = AsyncMock()

    mock_coordinator = MagicMock()

    with patch("custom_components.ha_creality_ws.camera._BaseCamera.__init__"):
        camera = CrealityWebRTCCamera(
            mock_coordinator,
            "http://1.2.3.4:8000/call/webrtc_local"
        )

    camera.hass = MagicMock()
    camera._go2rtc_client = mock_go2rtc_client

    async def run():
        with patch.object(camera, '_initialize_go2rtc_client', new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            await camera._ensure_stream_configured()

    asyncio.run(run())

    # Verify add was called
    mock_go2rtc_client.streams.add.assert_called_once()
    call_args = mock_go2rtc_client.streams.add.call_args
    assert "sources" in call_args.kwargs
    source = call_args.kwargs["sources"]
    assert source == "webrtc:http://1.2.3.4:8000/call/webrtc_local#format=creality"


def test_existing_stream_with_matching_source_is_not_recreated():
    """Regression test for issue #88 (0.9.4 follow-up).

    go2rtc_client returns dict[str, Stream] from streams.list(), where Stream
    is a dataclass with a `producers: list[Producer]` field. Treating it as
    `dict.get('sources', ...)` raises AttributeError on every call and caused
    a delete/recreate loop on every snapshot or offer. Guard against that.
    """
    import asyncio
    from types import SimpleNamespace

    expected_src = "webrtc:http://1.2.3.4:8000/call/webrtc_local#format=creality"
    existing_stream = SimpleNamespace(producers=[SimpleNamespace(url=expected_src)])

    mock_go2rtc_client = MagicMock()
    mock_go2rtc_client.streams = MagicMock()
    mock_go2rtc_client.streams.list = AsyncMock(
        return_value={"creality_k2_1_2_3_4": existing_stream}
    )
    mock_go2rtc_client.streams.add = AsyncMock()
    mock_go2rtc_client.streams.delete = AsyncMock()

    mock_coordinator = MagicMock()

    with patch("custom_components.ha_creality_ws.camera._BaseCamera.__init__"):
        camera = CrealityWebRTCCamera(
            mock_coordinator,
            "http://1.2.3.4:8000/call/webrtc_local",
        )

    camera.hass = MagicMock()
    camera._go2rtc_client = mock_go2rtc_client

    async def run():
        with patch.object(camera, "_initialize_go2rtc_client", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            await camera._ensure_stream_configured()

    asyncio.run(run())

    mock_go2rtc_client.streams.add.assert_not_called()
    mock_go2rtc_client.streams.delete.assert_not_called()
    assert camera._stream_name == "creality_k2_1_2_3_4"
    assert camera._force_recreate_stream is False


def test_existing_stream_with_wrong_source_is_recreated():
    """A stream left over from 0.9.3 (wrong source) must be replaced, not reused."""
    import asyncio
    from types import SimpleNamespace

    wrong_src = "webrtc:http://1.2.3.4:8000/call/webrtc_local"  # missing #format=creality
    existing_stream = SimpleNamespace(producers=[SimpleNamespace(url=wrong_src)])

    mock_go2rtc_client = MagicMock()
    mock_go2rtc_client.streams = MagicMock()
    mock_go2rtc_client.streams.list = AsyncMock(
        return_value={"creality_k2_1_2_3_4": existing_stream}
    )
    mock_go2rtc_client.streams.add = AsyncMock()
    mock_go2rtc_client.streams.delete = AsyncMock()

    mock_coordinator = MagicMock()

    with patch("custom_components.ha_creality_ws.camera._BaseCamera.__init__"):
        camera = CrealityWebRTCCamera(
            mock_coordinator,
            "http://1.2.3.4:8000/call/webrtc_local",
        )

    camera.hass = MagicMock()
    camera._go2rtc_client = mock_go2rtc_client

    async def run():
        with patch.object(camera, "_initialize_go2rtc_client", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            await camera._ensure_stream_configured()

    asyncio.run(run())

    mock_go2rtc_client.streams.delete.assert_called_once_with("creality_k2_1_2_3_4")
    mock_go2rtc_client.streams.add.assert_called_once()
    added = mock_go2rtc_client.streams.add.call_args.kwargs["sources"]
    assert added == "webrtc:http://1.2.3.4:8000/call/webrtc_local#format=creality"


def _camera(**kwargs):
    """Build a go2rtc camera without touching the HA entity base class."""
    mock_coordinator = MagicMock()
    with patch("custom_components.ha_creality_ws.camera._BaseCamera.__init__"):
        cam = CrealityWebRTCCamera(
            mock_coordinator,
            "http://1.2.3.4:8000/call/webrtc_local",
            **kwargs,
        )
    cam.hass = MagicMock()
    return cam


def test_stream_source_is_an_awaitable_method_not_a_property():
    """Regression test for issue #116.

    HA core defines `Camera.stream_source` as an async method and the stream
    pipeline does `source = await self.stream_source()`. Declaring it as a
    property shadowed that method, so the attribute evaluated to a plain string
    and calling it raised `TypeError: 'str' object is not callable`.
    """
    import inspect

    assert not isinstance(
        inspect.getattr_static(CrealityWebRTCCamera, "stream_source"), property
    )
    assert inspect.iscoroutinefunction(CrealityWebRTCCamera.stream_source)


def test_stream_source_returns_rtsp_url_for_ha_managed_go2rtc():
    """HA's bundled go2rtc serves RTSP on 127.0.0.1:18554, API on 11984."""
    import asyncio

    cam = _camera()
    cam._go2rtc_server_url = "http://localhost:11984/"
    cam._go2rtc_is_ha_managed = True

    async def run():
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = "creality_k2_1_2_3_4"
            return await cam.stream_source()

    assert asyncio.run(run()) == "rtsp://127.0.0.1:18554/creality_k2_1_2_3_4"


def test_stream_source_uses_default_rtsp_port_for_custom_loopback_go2rtc():
    """A stand-alone go2rtc on localhost:11984 is not HA's, so RTSP is 8554.

    The URL alone cannot tell the two apart, so the endpoint has to key off what
    initialization actually connected to; guessing 18554 here pointed HLS at a
    port nothing was listening on.
    """
    import asyncio

    cam = _camera(go2rtc_url="http://127.0.0.1:11984")
    cam._go2rtc_server_url = "http://127.0.0.1:11984/"
    cam._go2rtc_is_ha_managed = False

    async def run():
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = "creality_k2_1_2_3_4"
            return await cam.stream_source()

    assert asyncio.run(run()) == "rtsp://127.0.0.1:8554/creality_k2_1_2_3_4"


def test_stream_source_uses_go2rtc_default_port_for_external_server():
    """A stand-alone go2rtc listens for RTSP on its own host, port 8554."""
    import asyncio

    cam = _camera()
    cam._go2rtc_server_url = "http://10.0.0.5:1984/"

    async def run():
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = "creality_k2_1_2_3_4"
            return await cam.stream_source()

    assert asyncio.run(run()) == "rtsp://10.0.0.5:8554/creality_k2_1_2_3_4"


def test_stream_source_brackets_an_ipv6_go2rtc_host():
    """urlparse().hostname strips the brackets; rtsp://::1:8554/x is unparseable."""
    import asyncio

    cam = _camera(go2rtc_url="http://[::1]:11984")
    cam._go2rtc_server_url = "http://[::1]:11984/"
    cam._go2rtc_is_ha_managed = False

    async def run():
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = "creality_k2_1_2_3_4"
            return await cam.stream_source()

    assert asyncio.run(run()) == "rtsp://[::1]:8554/creality_k2_1_2_3_4"


def test_stream_source_honours_an_explicit_rtsp_port_override():
    import asyncio

    cam = _camera(go2rtc_rtsp_port=9554)
    cam._go2rtc_server_url = "http://10.0.0.5:1984/"

    async def run():
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = "creality_k2_1_2_3_4"
            return await cam.stream_source()

    assert asyncio.run(run()) == "rtsp://10.0.0.5:9554/creality_k2_1_2_3_4"


def _run_initialize(cam, *, custom_ok, ha_url="http://localhost:11984/"):
    """Drive the real _initialize_go2rtc_client() with a stubbed go2rtc client.

    `custom_ok=False` makes the custom server's version check raise, which is the
    branch that falls back to HA's go2rtc.
    """
    import asyncio
    from custom_components.ha_creality_ws import camera as camera_mod

    ha_data = MagicMock()
    ha_data.url = ha_url
    ha_data.session = MagicMock()
    cam.hass.data = {camera_mod.GO2RTC_DOMAIN: ha_data}

    made = []

    def _client(_session, url):
        client = MagicMock()
        # The custom attempt is the one carrying the configured URL.
        is_custom = cam._custom_go2rtc_url is not None and url != ha_url
        if is_custom and not custom_ok:
            client.validate_server_version = AsyncMock(side_effect=RuntimeError("refused"))
        else:
            client.validate_server_version = AsyncMock(return_value="1.9.4")
        made.append(url)
        return client

    with patch.object(camera_mod, "Go2RtcRestClient", side_effect=_client), \
            patch.object(camera_mod, "async_get_clientsession", return_value=MagicMock()), \
            patch.object(camera_mod, "GO2RTC_CLIENT_AVAILABLE", True):
        ok = asyncio.run(cam._initialize_go2rtc_client())
    return ok, made


def _stream_source(cam, name="creality_k2_1_2_3_4"):
    import asyncio

    async def run() -> "str | None":
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = name
            return await cam.stream_source()

    return asyncio.run(run())


def test_a_failed_custom_go2rtc_does_not_keep_its_rtsp_override():
    """Custom init fails, discovery falls back to HA's go2rtc.

    The override was configured for a server we could not reach, so honouring it
    aimed HLS at a port nothing is listening on. Drives the real initializer, so
    this fails if the fallback branch stops recording that it fell back.
    """
    cam = _camera(go2rtc_url="http://10.0.0.5:1984", go2rtc_rtsp_port=9554)
    ok, attempted = _run_initialize(cam, custom_ok=False)

    assert ok is True, "the fallback must still produce a working client"
    assert cam._go2rtc_fell_back_from_custom is True
    assert cam._go2rtc_is_ha_managed is True
    assert len(attempted) == 2, f"custom then HA-managed, got {attempted}"
    assert _stream_source(cam) == "rtsp://127.0.0.1:18554/creality_k2_1_2_3_4"


def test_an_ha_managed_instance_keeps_an_explicit_rtsp_override():
    """The options flow stores an explicit RTSP port for any go2rtc mode.

    Suppressing it for every HA-managed client -- rather than only after a
    fallback -- sent HLS to 18554 for a user who had deliberately configured a
    different port.
    """
    cam = _camera(go2rtc_rtsp_port=9554)
    ok, _ = _run_initialize(cam, custom_ok=True)

    assert ok is True
    assert cam._go2rtc_is_ha_managed is True
    assert cam._go2rtc_fell_back_from_custom is False, "no custom server was configured"
    # Host comes from the resolved go2rtc URL, which for HA-managed is localhost.
    assert _stream_source(cam) == "rtsp://localhost:9554/creality_k2_1_2_3_4"


def test_a_reachable_custom_go2rtc_keeps_its_override():
    """The ordinary custom case must be unaffected by the fallback handling."""
    cam = _camera(go2rtc_url="http://10.0.0.5:1984", go2rtc_rtsp_port=9554)
    ok, _ = _run_initialize(cam, custom_ok=True)

    assert ok is True
    assert cam._go2rtc_fell_back_from_custom is False
    assert cam._go2rtc_is_ha_managed is False
    assert _stream_source(cam) == "rtsp://10.0.0.5:9554/creality_k2_1_2_3_4"


def test_custom_rtsp_sources_keep_their_go2rtc_settings():
    """A Custom rtsp:// source is served by go2rtc, so it needs those settings.

    The options flow dropped CONF_GO2RTC_URL/PORT/RTSP_PORT for every mode except
    CAM_MODE_WEBRTC, but camera.async_setup_entry routes a non-http Custom URL
    through _make_go2rtc_camera(), which reads exactly those options. The result
    was a Custom RTSP camera that could not be pointed at an external go2rtc and
    silently lost its RTSP port.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "custom_components" / "ha_creality_ws"
    flow = (root / "config_flow.py").read_text()

    # The keep/drop decision must consider a go2rtc-ingested Custom URL.
    assert "custom_uses_go2rtc" in flow
    assert "if camera_mode == CAM_MODE_WEBRTC or custom_uses_go2rtc:" in flow, (
        "go2rtc settings must survive a Custom source that go2rtc ingests"
    )
    # And the fields have to be offered for that case, not just WebRTC/Auto.
    show = flow.split("show_go2rtc =", 1)[1].split("\n        show_custom_url", 1)[0]
    assert "GO2RTC_SOURCE_SCHEMES" in show

    # The schemes list is shared rather than duplicated per call site.
    const = (root / "const.py").read_text()
    assert 'GO2RTC_SOURCE_SCHEMES = ("rtsp", "rtmp", "srt")' in const
    camera = (root / "camera.py").read_text()
    assert "_make_go2rtc_camera(go2rtc_source=custom_url)" in camera, (
        "the Custom non-http path still builds a go2rtc camera"
    )


def test_stream_source_is_none_for_direct_signaling():
    """Direct-signaling cameras never register a go2rtc stream, so no HLS."""
    import asyncio

    cam = _camera(direct_signaling=True)
    assert asyncio.run(cam.stream_source()) is None


def test_stream_source_is_none_before_a_stream_exists():
    import asyncio

    cam = _camera()
    cam._go2rtc_server_url = "http://localhost:11984/"
    cam._go2rtc_is_ha_managed = True

    async def run():
        with patch.object(cam, "_ensure_stream_configured", new_callable=AsyncMock):
            cam._stream_name = None
            return await cam.stream_source()

    assert asyncio.run(run()) is None
