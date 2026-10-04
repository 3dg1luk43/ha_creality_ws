"""Which camera a printer gets: decided from telemetry, and revisited (#46).

Firmware 1.3.5.22 moved the K1C and K1 Max from mjpg-streamer on :8080 to
WebRTC on :8000 and reports `webrtcSupport: 1`. Verified on a real K1C running
it: :8080 refuses connections, :8000/call/webrtc_local answers. A camera chosen
as MJPEG for such a printer shows nothing, and both the cache (decided once,
never revisited) and options "Auto" (MJPEG for any K1) chose MJPEG.
"""

from types import SimpleNamespace

from custom_components.ha_creality_ws.coordinator import KCoordinator
from custom_components.ha_creality_ws.utils import detect_camera_type

# The real frame from that K1C, trimmed to what detection reads.
K1C_FW_1_3_5_22 = {
    "model": "K1C",
    "modelVersion": "printer hw ver:;printer sw ver:;DWIN hw ver:CR4CU220812S11;DWIN sw ver:1.3.5.22;",
    "hostname": "K1C-C627",
    "webrtcSupport": 1,
}


def test_a_webrtc_k1c_is_webrtc_whatever_was_cached():
    assert detect_camera_type(K1C_FW_1_3_5_22, "mjpeg") == "webrtc"
    assert detect_camera_type(K1C_FW_1_3_5_22, None) == "webrtc"


def test_an_older_k1c_that_says_no_is_mjpeg():
    assert detect_camera_type({"model": "K1C", "webrtcSupport": 0}, "webrtc") == "mjpeg"


def test_a_frame_without_the_flag_does_not_flip_a_working_camera():
    """Frames arrive piecemeal; the model can come before `webrtcSupport`."""
    assert detect_camera_type({"model": "K1C"}, "webrtc") == "webrtc"
    assert detect_camera_type({"model": "K1C"}, "mjpeg") == "mjpeg"


def test_with_nothing_known_the_first_guess_is_mjpeg():
    assert detect_camera_type({"model": "K1C"}, None) == "mjpeg"


def test_no_telemetry_decides_nothing():
    assert detect_camera_type({}, "webrtc") == "webrtc"
    assert detect_camera_type(None, None) is None


def test_the_k2_family_is_webrtc_without_the_flag():
    assert detect_camera_type({"model": "F008"}, "mjpeg") == "webrtc"
    assert detect_camera_type({"modelVersion": "Printer HW Ver: F012"}, None) == "webrtc"


def test_optional_camera_models_keep_their_type():
    assert detect_camera_type({"model": "K1 SE", "webrtcSupport": 0}, None) == "mjpeg_optional"


def _coordinator(cached):
    updates = []
    entry = SimpleNamespace(
        entry_id="e1",
        options={},
        data={"host": "1.2.3.4", "_cached_camera_type": cached},
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda e, **kw: updates.append(kw)
        ),
        states=SimpleNamespace(get=lambda _eid: None),
        loop=None,
    )
    coord = KCoordinator(hass, host="1.2.3.4")
    coord.config_entry = entry
    return coord, updates


def test_a_running_mjpeg_camera_is_rebuilt_once_the_printer_says_webrtc():
    """HA started with the printer switched off, so setup kept the cached
    MJPEG. The first frames that prove otherwise record the type, which
    reloads the entry and builds the WebRTC camera."""
    coord, updates = _coordinator("mjpeg")
    coord.camera_type_in_use = "mjpeg"
    coord.data = dict(K1C_FW_1_3_5_22)
    coord._check_camera_type()
    assert updates == [{"data": {"host": "1.2.3.4", "_cached_camera_type": "webrtc"}}]
    # Once: the reload builds a new coordinator.
    coord._check_camera_type()
    assert len(updates) == 1


def test_a_forced_camera_mode_is_never_second_guessed():
    coord, updates = _coordinator("mjpeg")
    coord.camera_type_in_use = None
    coord.data = dict(K1C_FW_1_3_5_22)
    coord._check_camera_type()
    assert updates == []


def test_a_correct_camera_is_left_alone():
    coord, updates = _coordinator("webrtc")
    coord.camera_type_in_use = "webrtc"
    coord.data = dict(K1C_FW_1_3_5_22)
    coord._check_camera_type()
    assert updates == []


def test_a_printer_go2rtc_got_no_video_from_stays_on_direct_webrtc():
    """#46: once an Auto camera has moved to direct WebRTC, a restart or the
    live re-check must not put it back on go2rtc."""
    frame = {"model": "K1C", "webrtcSupport": 1}
    assert detect_camera_type(frame, "webrtc_direct") == "webrtc_direct"
    assert detect_camera_type(frame, "webrtc") == "webrtc"
    assert detect_camera_type(frame, None) == "webrtc"
    # A printer that stops offering WebRTC leaves direct mode too.
    assert detect_camera_type({"model": "K1C", "webrtcSupport": 0}, "webrtc_direct") == "mjpeg"
