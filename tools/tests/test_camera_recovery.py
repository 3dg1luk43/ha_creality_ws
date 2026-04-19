import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()
if "go2rtc_client" not in sys.modules:
    sys.modules["go2rtc_client"] = MagicMock()
    sys.modules["go2rtc_client.exceptions"] = MagicMock()

# Ensure exceptions module has the class
class Go2RtcClientError(Exception):
    pass
if isinstance(sys.modules["go2rtc_client.exceptions"].Go2RtcClientError, MagicMock):
    sys.modules["go2rtc_client.exceptions"].Go2RtcClientError = Go2RtcClientError

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

@pytest.mark.asyncio
async def test_webrtc_recovery_on_error():
    # Setup
    mock_go2rtc_client = MagicMock()
    mock_go2rtc_client.webrtc = MagicMock()
    mock_go2rtc_client.streams = MagicMock()
    mock_go2rtc_client.streams.delete = AsyncMock()
    
    mock_go2rtc_client.webrtc.forward_whep_sdp_offer = AsyncMock(
        side_effect=Go2RtcClientError("500 Internal Server Error")
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
    await camera.async_handle_async_webrtc_offer("sdp", "session_id", send_message)
    
    # Verify
    # 1. Error sent
    send_message.assert_called()
    msg = send_message.call_args[0][0].as_dict()
    assert msg["type"] == "error"
    
    # 2. Recovery: streams.delete called
    mock_go2rtc_client.streams.delete.assert_called_once_with("test_stream")
    
    # 3. Stream name reset
    assert camera._stream_name is None
