"""The simulator's printer model, frames and protocol, on a clock tests control.

The simulator is what the test box checks the integration against, so where it
differs from a real printer the box proves the wrong thing. These pin the
behaviours real printers have that the integration was built around (see
docs/internal/reference/07-simulator.md). No aiortc, av or numpy needed: the
core is plain Python, so this runs in CI.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simulator.clock import ManualClock  # noqa: E402
from simulator.frames import FrameStream, format_frame  # noqa: E402
from simulator.printer import Phase, PrinterState, SimOptions  # noqa: E402
from simulator.protocol import MessageLog, Protocol  # noqa: E402


def _printer(model="k1c", seconds=100, **kw):
    clock = ManualClock()
    state = PrinterState(
        model,
        simulate_print=False,
        sim=SimOptions(total_print_seconds=seconds, self_test_seconds=5, **kw),
        targets={"nozzle": 220, "bed": 60},
        deterministic=True,
        clock=clock,
    )
    return state, clock


def _run(state, clock, seconds, step=0.2):
    seen = []
    for _ in range(int(seconds / step)):
        clock.advance(step)
        state.tick()
        v = state.values()
        sig = (state.phase, v["state"], v["printProgress"], v["withSelfTest"])
        if not seen or seen[-1] != sig:
            seen.append(sig)
    return seen


# --------------------------------------------------------------------------- #
# Print lifecycle
# --------------------------------------------------------------------------- #


def test_a_print_ends_100_then_99_then_completed_with_the_file_kept():
    state, clock = _printer(seconds=100)
    state.start_print("benchy.gcode")
    seen = _run(state, clock, 130)
    progresses = [p for _ph, _st, p, _w in seen]
    assert progresses[-3:] == [100, 99, 100]
    phase, code, progress, _ = seen[-1]
    assert (phase, code, progress) == (Phase.COMPLETED, 0, 100)
    v = state.values()
    assert v["printFileName"] == "/usr/data/printer_data/gcodes/benchy.gcode"
    assert v["printLeftTime"] == 0
    assert (v["targetNozzleTemp"], v["targetBedTemp0"]) == (0, 0)


def test_progress_reads_100_while_time_is_still_left():
    state, clock = _printer(seconds=100)
    state.start_print()
    _run(state, clock, 95)
    v = state.values()
    assert v["printProgress"] == 100 and v["printLeftTime"] > 0 and v["state"] == 1


def test_a_k2_self_tests_after_the_first_printing_frame():
    """#124: one printing frame, then withSelfTest 1..99, still state 1."""
    state, clock = _printer("k2plus")
    state.start_print()
    first = state.values()
    assert (first["state"], first["withSelfTest"]) == (1, 0)
    seen = _run(state, clock, 3)
    during = [s for s in seen if s[0] == Phase.SELF_TEST]
    assert during and all(code == 1 and 1 <= w <= 99 for _p, code, _pr, w in during)
    _run(state, clock, 5)
    assert state.values()["withSelfTest"] == 100


def test_a_self_test_can_report_state_2_instead():
    state, clock = _printer("k2plus")
    state.self_test_style = "state2"
    state.start_print()
    _run(state, clock, 1)
    assert state.values()["state"] == 2


def test_a_k1_does_not_self_test_and_reads_100_while_printing():
    state, clock = _printer("k1c")
    state.start_print()
    _run(state, clock, 2)
    assert state.phase == Phase.PRINTING and state.values()["withSelfTest"] == 100


@pytest.mark.parametrize(
    ("style", "code", "progress", "file_kept"),
    [("state4", 4, 40, True), ("state0", 0, 0, True), ("clear", 0, 0, False)],
)
def test_the_three_shapes_of_a_stop(style, code, progress, file_kept):
    """The old simulator restarted the print from 0 on a stop."""
    state, clock = _printer(seconds=100)
    state.start_print()
    _run(state, clock, 40.4)
    assert state.values()["printProgress"] == 40
    state.stop(style)
    _run(state, clock, 20)  # past the state-7 tail of a state4 stop
    v = state.values()
    assert (v["state"], v["printProgress"], bool(v["printFileName"])) == (code, progress, file_kept)
    assert v["targetNozzleTemp"] == 0
    assert state.phase == Phase.STOPPED


def test_a_cancel_during_self_test_goes_the_way_the_real_k1c_did():
    """R29's capture: state 7 from the cancel, self-test 100 about halfway,
    then state 4, with the printer busy (deviceState 1) until then."""
    state, clock = _printer(seconds=100)
    state.sim.self_test_seconds = 60
    state.start_print(self_test=True)
    _run(state, clock, 2)
    assert 1 <= state.values()["withSelfTest"] <= 99
    state.stop()
    seen = _run(state, clock, 20)
    states = [(sig[1], sig[3]) for sig in seen]
    first_7 = next(i for i, (code, _st) in enumerate(states) if code == 7)
    assert states[first_7][1] < 100
    assert (7, 100) in states
    assert states[-1] == (4, 100)
    assert state.values()["deviceState"] == 0


def test_a_pause_freezes_progress_and_the_time_left():
    state, clock = _printer(seconds=100)
    state.start_print()
    _run(state, clock, 30)
    assert state.pause() is True
    before = state.values()
    _run(state, clock, 20)
    after = state.values()
    assert after["state"] == 5
    assert (after["printProgress"], after["printLeftTime"]) == (before["printProgress"], before["printLeftTime"])
    assert after["printJobTime"] > before["printJobTime"]
    state.resume()
    _run(state, clock, 5)
    assert state.values()["printProgress"] > before["printProgress"]


def test_a_reprint_of_the_same_file_restarts_the_job_clock():
    state, clock = _printer(seconds=50)
    state.start_print("a.gcode")
    _run(state, clock, 70)
    assert state.phase == Phase.COMPLETED
    done = state.values()["printJobTime"]
    state.start_print("a.gcode")
    _run(state, clock, 1)
    assert state.values()["printJobTime"] < done


def test_a_finished_job_drops_to_0_later_with_the_file_kept():
    """R10: a while after the end the printer resets progress, file still set."""
    state, clock = _printer(seconds=50, finished_reset_seconds=30)
    state.start_print()
    _run(state, clock, 60)
    assert state.values()["printProgress"] == 100
    _run(state, clock, 40)
    v = state.values()
    assert v["printProgress"] == 0 and v["printFileName"] and v["state"] == 0


def test_a_runout_pauses_and_resolves():
    state, clock = _printer()
    state.start_print()
    _run(state, clock, 10)
    state.runout()
    assert state.values()["materialStatus"] == 1 and state.values()["state"] == 5
    state.resolve_runout(resume=True)
    assert state.values()["materialStatus"] == 0 and state.values()["state"] == 1


def test_a_pause_while_homing_is_ignored():
    state, clock = _printer()
    state.start_print()
    _run(state, clock, 2)
    state.set_autohome("X Y", seconds=4)
    assert state.values()["deviceState"] == 7
    assert state.pause() is False
    _run(state, clock, 5)
    assert state.values()["deviceState"] == 1


def test_a_cfs_swap_reads_state_0_mid_print():
    """R29: more than 15 s of state 0 in the middle of a print."""
    state, clock = _printer()
    state.start_print()
    _run(state, clock, 10)
    state.cfs_swap(20)
    _run(state, clock, 10)
    v = state.values()
    assert v["state"] == 0 and v["printFileName"]
    _run(state, clock, 15)
    assert state.values()["state"] == 1


# --------------------------------------------------------------------------- #
# Identity and model differences
# --------------------------------------------------------------------------- #


def test_the_k2_base_sends_a_zero_chamber_target_and_reports_it_to_moonraker():
    state, _clock = _printer("k2")
    state.set_box_temp(45)
    assert state.values()["targetBoxTemp"] == 0
    assert state.moonraker_status()["temperature_fan chamber_fan"]["target"] == 45.0


def test_k1c_firmware_1_3_5_reports_webrtc_support():
    state, _clock = _printer("k1c-1.3.5")
    assert state.values()["webrtcSupport"] == 1
    assert "webrtcSupport" not in _printer("k1c")[0].values()


def test_the_test_box_default_keeps_its_hostname():
    """sensor.creality_k1c_* on the test box comes from it."""
    assert _printer("k1c")[0].values()["hostname"] == "creality-k1c"


# --------------------------------------------------------------------------- #
# Frames
# --------------------------------------------------------------------------- #


def test_a_k1_writes_temperatures_as_six_decimal_strings():
    state, _clock = _printer("k1c")
    frame = state.snapshot()
    assert frame["nozzleTemp"] == "25.000000"
    assert isinstance(frame["targetNozzleTemp"], int)
    assert isinstance(_printer("k2plus")[0].snapshot()["nozzleTemp"], float)


def test_after_the_first_frame_only_changes_are_sent():
    state, clock = _printer()
    stream = FrameStream("delta")
    first = stream.first(state.snapshot())
    assert "model" in first and len(first) > 50
    assert stream.next(state.snapshot()) is None
    state.set_nozzle_temp(200)
    clock.advance(1)
    state.tick()
    delta = stream.next(state.snapshot())
    assert set(delta) == {"nozzleTemp", "targetNozzleTemp"}


def test_a_blank_is_sent_once_and_the_value_again_when_it_returns():
    """#121: the integration keeps a blank until that key is sent again."""
    state, _clock = _printer()
    stream = FrameStream("delta")
    stream.first(state.snapshot())
    state.apply_overrides({"bedTemp0": ""})
    assert stream.next(state.snapshot()) == {"bedTemp0": ""}
    assert stream.next(state.snapshot()) is None
    state.clear_overrides()
    assert stream.next(state.snapshot()) == {"bedTemp0": "25.000000"}


def test_full_mode_resends_everything():
    state, _clock = _printer()
    stream = FrameStream("full")
    stream.first(state.snapshot())
    assert len(stream.next(state.snapshot())) > 50


def test_format_leaves_other_types_alone():
    out = format_frame({"nozzleTemp": 21.5, "state": 1, "err": {"errcode": 0}}, string_numbers=True)
    assert out == {"nozzleTemp": "21.500000", "state": 1, "err": {"errcode": 0}}


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


def _proto(model="k1c"):
    state, clock = _printer(model)
    log = MessageLog()
    stream = FrameStream("delta")
    stream.first(state.snapshot())
    return Protocol(state, log), state, stream, log


def test_a_client_heartbeat_is_answered_with_ok():
    proto, _s, stream, _log = _proto()
    assert proto.handle(json.dumps({"ModeCode": "heart_beat", "msg": "x"}), stream) == ["ok"]


def test_print_objects_come_as_three_keys_with_objects_as_a_string():
    proto, state, stream, _log = _proto()
    state.start_print()
    reply = proto.handle(json.dumps({"method": "get", "params": {"reqPrintObjects": 1}}), stream)
    assert set(reply[0]) == {"current_object", "excluded_objects", "objects"}
    assert isinstance(reply[0]["objects"], str) and json.loads(reply[0]["objects"])


def test_a_bad_value_is_rejected_without_dropping_the_client():
    """The old simulator raised in the receive loop and closed the socket."""
    proto, state, stream, log = _proto()
    proto.handle(json.dumps({"method": "set", "params": {"nozzleTempControl": "abc"}}), stream)
    proto.handle(json.dumps({"method": "set", "params": {"nozzleTempControl": 200}}), stream)
    assert state.nozzle_target == 200
    assert any("rejected" in item["note"] for item in log.items)


def test_every_key_in_a_set_is_applied():
    proto, state, stream, _log = _proto()
    proto.handle(json.dumps({"method": "set", "params": {"setFeedratePct": 120, "lightSw": 0}}), stream)
    assert state.feedrate == 120 and state.light_on is False


def test_set_pin_is_recorded_on_a_model_with_a_dimmable_led():
    proto, state, stream, _log = _proto("k2plus")
    proto.handle(json.dumps({"method": "set", "params": {"gcodeCmd": "SET_PIN PIN=LED VALUE=0.5"}}), stream)
    assert state.led_value == 0.5


def test_the_listing_follows_the_firmware():
    proto, state, stream, _log = _proto("k1c")
    legacy = proto.handle(json.dumps({"method": "get", "params": {"reqGcodeFile": 1}}), stream)[0]
    packed = legacy["retGcodeFileInfo"]["fileInfo"]
    assert packed.endswith(";") and packed.split(";")[0].count(":") == 6
    state.gcode_listing = "v2"
    state.start_print("x.gcode")
    v2 = proto.handle(json.dumps({"method": "get", "params": {"reqGcodeFile": 1}}), stream)[0]
    assert any(e["path"] == state.values()["printFileName"] for e in v2["retGcodeFileInfo2"])
    state.gcode_listing = "none"
    assert proto.handle(json.dumps({"method": "get", "params": {"reqGcodeFile": 1}}), stream) == []


def test_modify_material_round_trip_and_rejections():
    proto, state, stream, _log = _proto("k2plus")
    replies = proto.handle(json.dumps({"method": "set", "params": {"modifyMaterial": {
        "boxId": 1, "id": 1, "type": "PETG", "color": "#ff00aa"}}}), stream)
    slot = next(m for b in replies[0]["boxsInfo"]["materialBoxs"] if b["id"] == 1
                for m in b["materials"] if m["id"] == 1)
    assert (slot["type"], slot["color"], slot["percent"]) == ("PETG", "#ff00aa", 80)
    with pytest.raises(ValueError, match=r"no such box 9"):
        state.modify_material({"boxId": 9, "id": 0})
    with pytest.raises(ValueError, match=r"no such slot 7 in box 1"):
        state.modify_material({"boxId": 1, "id": 7})
    with pytest.raises(ValueError, match=r"needs an object"):
        state.modify_material(["boxId"])


def test_the_echo_can_pad_the_colour_like_the_stream():
    """#113 asks which form the printer stores; both can be tested."""
    _proto_, state, _stream, _log = _proto("k2plus")
    state.cfs.echo_colour = "padded"
    assert state.modify_material({"boxId": 1, "id": 0, "color": "#ff00aa"})["color"] == "#0ff00aa"


def test_cfs_units_come_and_go():
    state, _clock = _printer("k2plus")
    assert state.values()["cfsConnect"] == 1
    assert state.cfs.add_box() == 2
    state.cfs.detach()
    assert state.values()["cfsConnect"] == 0
    assert [b["type"] for b in state.cfs.boxes] == [1]
