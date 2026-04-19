import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()

# Ensure go2rtc_client and aiohttp are mocked if not already
if "go2rtc_client" not in sys.modules:
    g2_mod = MagicMock()
    sys.modules["go2rtc_client"] = g2_mod
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

def test_webrtc_offer_500_error_repro():
    import asyncio
    # Setup
    mock_go2rtc_client = MagicMock()
    mock_go2rtc_client.webrtc = MagicMock()

    # Define a real exception class for the test
    class RealGo2RtcClientError(Exception):
        pass

    # Patch the exception class in the camera module so it matches what we raise
    with patch("custom_components.ha_creality_ws.camera.Go2RtcClientError", RealGo2RtcClientError):
        mock_go2rtc_client.webrtc.forward_whep_sdp_offer = AsyncMock(
            side_effect=RealGo2RtcClientError("500 Internal Server Error")
        )

        mock_hass = MagicMock()
        mock_coordinator = MagicMock()

        with patch("custom_components.ha_creality_ws.camera._BaseCamera.__init__"):
            camera = CrealityWebRTCCamera(
                mock_coordinator,
                "http://1.2.3.4:8000/call/webrtc_local"
            )

        camera.hass = mock_hass
        camera._go2rtc_client = mock_go2rtc_client
        camera._stream_name = "test_stream"

        # Callback
        send_message = MagicMock()

        # Test
        asyncio.run(camera.async_handle_async_webrtc_offer("sdp", "session_id", send_message))

        # Verify
        send_message.assert_called()
        args, _ = send_message.call_args
        msg = args[0].as_dict()

        assert msg["type"] == "error"
        # Since we patched the exception, it should be caught by the first except block
        assert "go2rtc error" in msg["message"]