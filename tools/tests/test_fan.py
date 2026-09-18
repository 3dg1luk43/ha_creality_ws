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

from custom_components.ha_creality_ws.fan import (  # noqa: E402
    _KFanEntity,
    async_setup_entry,
)


def teardown_module(_module):
    restore_stubs(__name__)


# (uid, telemetry field, M106 channel). This is the *expectation*;
# test_the_platform_wires_up_exactly_these_fans drives the real
# `async_setup_entry` and checks production against it, so a wrong field or
# channel there cannot pass by virtue of the tests using this copy.
FAN_SPECS = [
    ("model_fan", "modelFanPct", 0),
    ("case_fan", "caseFanPct", 1),
    ("side_fan", "auxiliaryFanPct", 2),
]


def test_the_platform_wires_up_exactly_these_fans():
    """Ties FAN_SPECS to what `async_setup_entry` actually creates.

    Every other test in this file builds `_KFanEntity` from the local copy, so
    without this a production field or channel could change and they would all
    still pass against the stale expectation.
    """
    coord = SimpleNamespace(client=ClientStub(), data={}, available=True,
                            config_entry=None, last_update_success=True)
    entry = SimpleNamespace(entry_id="e1")
    hass = SimpleNamespace(data={"ha_creality_ws": {"e1": coord}})
    added: list = []
    asyncio.run(async_setup_entry(hass, entry, added.extend))

    actual = [(e._attr_unique_id.split("-", 1)[1], e._read_field, e._channel)
              for e in added]
    assert actual == FAN_SPECS, (
        f"the platform no longer matches FAN_SPECS: {actual}"
    )


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


# Optional third-party imports the simulator makes at module scope. Anything
# outside this set is a defect in the simulator, not a missing extra.
_SIMULATOR_OPTIONAL_DEPS = frozenset({"aiortc", "av", "websockets", "aiohttp"})


def _test_server_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2] / "tools" / "creality_printer_test_server.py"
    ).read_text()


def test_the_simulators_m106_pattern_ignores_unrelated_m_codes():
    """`_M106_RE` compiled from source, so this runs without the simulator's deps.

    Every group after the opcode is optional, so an unanchored pattern matched
    `M1061 S30` and `M1069` with no P and no S -- and `handle_gcode` then did
    `set_fan_pct(0, 0.0)` and added channel 0 to `_manual_fans`, pinning the model
    fan off for the rest of the run while reporting the command as handled.
    """
    import re

    source = _test_server_source()
    match = re.search(r"_M106_RE = re\.compile\(\s*r\"(?P<pat>[^\"]+)\"", source)
    assert match, "could not find _M106_RE in the simulator source"
    pattern = re.compile(match.group("pat"), re.IGNORECASE)

    for cmd, p, s_val in (
        ("M106 P1 S255", "1", "255"),
        ("M106 P0 S0", "0", "0"),
        ("m106 p2 s128", "2", "128"),
    ):
        m = pattern.match(cmd)
        assert m, f"{cmd} must still be handled"
        assert (m.group("p"), m.group("s")) == (p, s_val), cmd

    for cmd in ("M1061 S30", "M1069", "M106X"):
        assert not pattern.match(cmd), (
            f"{cmd} is not a fan command; matching it sets channel 0 to 0%"
        )


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
    # The parser is built by a module-level function, so the default is a value
    # rather than a string in the file.
    assert "H264PassthroughTrack" in source
    assert "keyint=" in source, "libx264's 250-frame default is too long for HLS"

    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "tools" / "creality_printer_test_server.py"
    name = "_sim_for_args"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: a @dataclass in the module resolves its own
    # __module__ through sys.modules, and fails with an opaque AttributeError
    # otherwise.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:  # pragma: no cover - aiortc/av absent in CI
        # Only a genuinely absent optional dependency is a skip. Catching every
        # exception meant a SyntaxError or a NameError in the simulator itself
        # reported as "not importable here" and the test passed as skipped.
        if (exc.name or "").split(".")[0] not in _SIMULATOR_OPTIONAL_DEPS:
            raise
        pytest.skip(f"simulator dependency missing: {exc.name}")
    finally:
        sys.modules.pop(name, None)

    # The module imported, so a missing parser is a regression in the simulator,
    # not an environment it cannot run in. Skipping here quietly dropped the
    # prefer_codec assertion below.
    assert hasattr(module, "build_argparser"), (
        "the simulator no longer exposes build_argparser; the codec default is "
        "unasserted until this is restored or the test is rewritten"
    )
    parser = module.build_argparser()
    defaults = parser.parse_args([])
    assert defaults.prefer_codec == "h264", (
        "aiortc answers VP8 first, which HA's HLS pipeline cannot package"
    )


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
