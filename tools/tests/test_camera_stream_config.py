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

@pytest.mark.asyncio
async def test_ensure_stream_configured_modern():
    # Test that it adds stream WITHOUT format=creality
    
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
    # Simulate client initialization check passing
    with patch.object(camera, '_initialize_go2rtc_client', new_callable=AsyncMock) as mock_init:
        mock_init.return_value = True
        
        await camera._ensure_stream_configured()
    
    # Verify add was called 
    mock_go2rtc_client.streams.add.assert_called_once()
    call_args = mock_go2rtc_client.streams.add.call_args
    assert "sources" in call_args.kwargs
    source = call_args.kwargs["sources"]
    print(f"DEBUG: source={source}")
    assert "#format=creality" not in source
    assert source == "webrtc:http://1.2.3.4:8000/call/webrtc_local"