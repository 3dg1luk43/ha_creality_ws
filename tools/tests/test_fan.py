"""Fan control entities.

Issue #114 asked for fan *control*, not just monitoring. The `fan` platform
already provides it; these tests pin down the two halves that make it work --
reading the right telemetry field and emitting the right M106 channel.
"""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import install_stub_module, restore_stubs

# fan.py needs homeassistant.components.fan, which the shared conftest omits.
if "homeassistant.components.fan" not in sys.modules:
    fan_mod = MagicMock()

    class _FanEntity:
        pass

    class _FanEntityFeature:
        SET_SPEED = 1
        TURN_ON = 2
        TURN_OFF = 4

    fan_mod.FanEntity = _FanEntity
    fan_mod.FanEntityFeature = _FanEntityFeature
    fan_mod.ATTR_PERCENTAGE = "percentage"
    install_stub_module(__name__, "homeassistant.components.fan", fan_mod)

from custom_components.ha_creality_ws.fan import _KFanEntity  # noqa: E402


def teardown_module(_module):
    restore_stubs(__name__)


# (uid, telemetry field, M106 channel) as wired up in fan.async_setup_entry.
FAN_SPECS = [
    ("model_fan", "modelFanPct", 0),
    ("case_fan", "caseFanPct", 1),
    ("side_fan", "auxiliaryFanPct", 2),
]


class ClientStub:
    def __init__(self):
        self._host = "1.2.3.4"
        self.sent = []

    async def send_set_retry(self, **params):
        self.sent.append(params)


def _fan(read_field, uid, channel, data=None):
    coord = SimpleNamespace(
        client=ClientStub(),
        data=data or {},
        available=True,
        power_is_off=lambda: False,
    )
    return _KFanEntity(
        coord, name=uid, read_field=read_field, uid=uid, channel=channel
    )


@pytest.mark.parametrize(("uid", "field", "channel"), FAN_SPECS)
def test_fan_reads_the_printer_telemetry_field(uid, field, channel):
    fan = _fan(field, uid, channel, data={field: 65})
    assert fan.percentage == 65
    assert fan.is_on is True


@pytest.mark.parametrize(("uid", "field", "channel"), FAN_SPECS)
def test_fan_set_percentage_emits_the_right_m106_channel(uid, field, channel):
    fan = _fan(field, uid, channel)
    asyncio.run(fan.async_set_percentage(100))
    assert fan.coordinator.client.sent == [{"gcodeCmd": f"M106 P{channel} S255"}]


def test_fan_scales_percentage_to_the_0_255_range():
    fan = _fan("modelFanPct", "model_fan", 0)
    asyncio.run(fan.async_set_percentage(50))
    assert fan.coordinator.client.sent[-1] == {"gcodeCmd": "M106 P0 S128"}


def test_fan_turn_off_sends_s0():
    fan = _fan("caseFanPct", "case_fan", 1, data={"caseFanPct": 80})
    asyncio.run(fan.async_turn_off())
    assert fan.coordinator.client.sent[-1] == {"gcodeCmd": "M106 P1 S0"}


def test_fan_turn_on_without_a_percentage_goes_to_full():
    fan = _fan("auxiliaryFanPct", "side_fan", 2, data={"auxiliaryFanPct": 0})
    asyncio.run(fan.async_turn_on())
    assert fan.coordinator.client.sent[-1] == {"gcodeCmd": "M106 P2 S255"}


def test_fan_turn_on_honours_an_explicit_percentage():
    fan = _fan("modelFanPct", "model_fan", 0)
    asyncio.run(fan.async_turn_on(percentage=25))
    assert fan.coordinator.client.sent[-1] == {"gcodeCmd": "M106 P0 S64"}


def test_fan_is_off_and_zero_when_the_printer_is_unreachable():
    fan = _fan("modelFanPct", "model_fan", 0, data={"modelFanPct": 90})
    fan.coordinator.available = False
    assert fan.percentage == 0
    assert fan.is_on is False


def _test_server_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2] / "tools" / "creality_printer_test_server.py"
    ).read_text()


def test_test_server_reports_the_same_fan_fields_the_integration_reads():
    """The simulator used to emit caseFan/modelFan/sideFan, which never matched."""
    source = _test_server_source()
    for _uid, field, _channel in FAN_SPECS:
        assert f'"{field}"' in source, f"test server does not report {field}"


def test_test_server_prefers_h264_for_video():
    """Guard the simulator's HLS-testability.

    aiortc answers with VP8 first by default, and Home Assistant's stream
    component cannot package VP8 into HLS -- the playlist just blocks. Real
    K-series printers send H.264, so the simulator must too, with a short
    keyframe interval (aiortc's encoder inherits libx264's 250-frame default).
    """
    source = _test_server_source()
    assert '"--prefer-codec"' in source
    assert 'default="h264"' in source
    assert "H264PassthroughTrack" in source
    assert "keyint=" in source


def _h264_timing():
    """The simulator's timestamp helper, importable without aiortc/av."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "tools" / "h264_timing.py"
    spec = importlib.util.spec_from_file_location("h264_timing", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePacket:
    """Only what assign_clip_timestamps touches: size, pts, dts."""

    def __init__(self, size):
        self.size = size
        self.pts = None
        self.dts = None


def test_h264_clip_timestamps_stay_monotonic_across_the_loop():
    """The clip loops forever, so pts must not go backwards at the seam.

    Timestamps used to be derived from `enumerate(demux(...))` while the clip
    length came from the *kept* packet count. A skipped zero-size packet -- what
    ffmpeg emits on a flush -- made the length shorter than the real span, so the
    wrap advanced _pts_offset by less than one clip and the second loop's
    timestamps overlapped the first.
    """
    timing = _h264_timing()
    step = timing.pts_step(30)

    # A zero-size packet in the middle is the case that used to desynchronise.
    kept, clip_duration = timing.assign_clip_timestamps(
        [_FakePacket(120), _FakePacket(0), _FakePacket(140), _FakePacket(90)], 30
    )

    assert len(kept) == 3, "the zero-size packet is dropped"
    assert [p.pts for p in kept] == [0, step, 2 * step], "contiguous, no gap"
    assert [p.dts for p in kept] == [p.pts for p in kept]
    # The seam invariant: the next loop's first pts is exactly one step past the
    # previous loop's last.
    assert kept[-1].pts + step == clip_duration

    # Two full loops must be strictly increasing.
    emitted = []
    offset = 0
    for _ in range(2):
        emitted.extend(offset + p.pts for p in kept)
        offset += clip_duration
    assert emitted == sorted(set(emitted)), f"pts not strictly increasing: {emitted}"


def test_the_simulator_uses_the_shared_timestamp_helper():
    """Pins the call site, since the bug was inline arithmetic."""
    source = _test_server_source()
    clip = source.split("async def _ensure_clip", 1)[1].split("\n    async def recv", 1)[0]
    assert "assign_clip_timestamps(" in clip
    assert "enumerate(container.demux" not in clip, (
        "pts must not be indexed by demuxed position; zero-size packets are skipped"
    )


def test_test_server_does_not_close_healthy_webrtc_sessions_on_a_timer():
    """A fixed sleep-then-close made every consumer reconnect in a loop.

    Asserted positively: "no `asyncio.sleep(60)`" also passes for any
    differently-spelled timer teardown, so check that the cleanup actually waits
    on the connection state instead.
    """
    source = _test_server_source()
    cleanup = source.split("async def _cleanup_pc", 1)[1].split("\n    async def ", 1)[0]
    assert 'pc.on("connectionstatechange")' in cleanup
    assert "await closed.wait()" in cleanup
    assert "asyncio.sleep" not in cleanup
