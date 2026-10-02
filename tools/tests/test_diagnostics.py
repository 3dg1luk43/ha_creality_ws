"""Diagnostics: what the download and the dump action carry, and what they hide.

The dump action used to log everything unredacted at WARNING and return
nothing, while every doc told reporters to copy its "response". The camera's
entity attributes carry an access token, which went into public issues with it.
"""

import json
from types import SimpleNamespace

import pytest

import custom_components.ha_creality_ws.diagnostics as diag
from custom_components.ha_creality_ws.notification_rules import LiveCardState
from homeassistant.components.diagnostics import REDACTED


class FakeClient:
    host = "192.168.0.90"
    is_connected = True
    reconnect_count = 2
    msg_count = 40
    last_error = None
    uptime_start = 0.0

    def get_url(self):
        return "ws://192.168.0.90:9999"

    def has_connected_once(self):
        return True

    def is_task_running(self):
        return True


def _coordinator():
    return SimpleNamespace(
        client=FakeClient(),
        data={"model": "K1C", "hostname": "K1C-C627", "webrtcSupport": 1, "nozzleTemp": 22.4},
        available=True,
        power_is_off=lambda: False,
        paused_flag=lambda: False,
        pending_pause=lambda: False,
        pending_resume=lambda: False,
        camera_type_in_use="webrtc",
        _notify_targets=["notify.mobile_app_lukas_iphone"],
        _target_platform=lambda target: ("iOS", "Apple"),
        _http_urls_accessed={"http://192.168.0.90/downloads/original/current_print_image.png"},
        # The real one: a slots dataclass, which vars() cannot read (it 500'd
        # the download on a real Home Assistant).
        _live_card=LiveCardState(card_active=True, pushes_this_job=3),
    )


@pytest.fixture
def setup(monkeypatch):
    entry = SimpleNamespace(
        entry_id="e1",
        title="Creality Printer (WS) (192.168.0.90)",
        version=3,
        data={"host": "192.168.0.90", "_cached_hostname": "K1C-C627", "_cached_camera_type": "webrtc"},
        options={"notify_targets": ["notify.mobile_app_lukas_iphone"], "camera_mode": "auto"},
    )
    camera_state = SimpleNamespace(
        state="idle",
        # The attributes a real WebRTC camera publishes (checked on the test box
        # against a K1C): every one of these carries the address or the name.
        attributes={
            "access_token": "secret-token",
            "entity_picture": "/api/camera_proxy/camera.k1c?token=secret-token",
            "friendly_name": "K1C-C627 Printer Camera",
            "upstream_signaling_url": "http://192.168.0.90:8000/call/webrtc_local",
            "stream_source": "rtsp://127.0.0.1:18554/creality_k2_192_168_0_90",
            "go2rtc_stream_name": "creality_k2_192_168_0_90",
            "source_url": "http://192.168.0.90/downloads/original/current_print_image.png",
        },
    )
    reg_entries = [SimpleNamespace(entity_id="camera.k1c", unique_id="192.168.0.90-camera", disabled_by=None)]
    monkeypatch.setattr(diag, "er", SimpleNamespace(
        async_get=lambda hass: None,
        async_entries_for_config_entry=lambda reg, entry_id: reg_entries,
    ))
    coord = _coordinator()
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda eid: camera_state),
        data={"ha_creality_ws": {"e1": coord}},
        config_entries=SimpleNamespace(async_get_entry=lambda eid: entry if eid == "e1" else None),
    )
    return hass, entry


def _flatten(obj):
    return json.dumps(obj)


def test_the_download_hides_addresses_names_and_tokens(setup):
    import asyncio

    hass, entry = setup
    out = asyncio.run(diag.async_get_config_entry_diagnostics(hass, entry))
    text = _flatten(out)
    for secret in ("192.168.0.90", "K1C-C627", "secret-token", "lukas_iphone"):
        assert secret not in text, f"{secret!r} leaked into the download"
    assert out["entry"]["host"] == REDACTED
    assert out["telemetry"]["hostname"] == REDACTED


def test_the_download_still_carries_what_triage_needs(setup):
    import asyncio

    hass, entry = setup
    out = asyncio.run(diag.async_get_config_entry_diagnostics(hass, entry))
    assert out["telemetry"]["model"] == "K1C"
    assert out["telemetry"]["webrtcSupport"] == 1
    assert out["printer"]["camera_type_detected"] == "webrtc"
    assert out["printer"]["model_detection"]["is_k1c"] is True
    assert out["notifications"]["target_platforms"] == [{"os_name": "iOS", "manufacturer": "Apple"}]
    assert out["entities"][0]["state"] == "idle"
    assert out["notifications"]["live_card"]["pushes_this_job"] == 3
    assert out["connection"]["connected"] is True


def test_the_dump_is_one_json_document_under_printers(setup, monkeypatch):
    """The bug form asks for "the printers section of the response"."""
    import asyncio

    hass, _ = setup

    async def _no_crawl(hass, host):
        return []

    monkeypatch.setattr(diag, "async_crawl_web_ui", _no_crawl)
    out = asyncio.run(diag.async_collect(hass, "0.9.9"))
    assert list(out["printers"]) == ["e1"]
    json.dumps(out)  # serialisable as it stands
    # Unredacted until the caller redacts: include_sensitive_data is honoured.
    assert out["printers"]["e1"]["entry"]["host"] == "192.168.0.90"
