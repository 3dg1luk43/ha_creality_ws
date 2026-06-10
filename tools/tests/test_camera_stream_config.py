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