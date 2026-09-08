"""The pure notification layer.

Everything here runs against `notification_rules.py`, which imports nothing from
Home Assistant on purpose -- so these tests need no stubs, no coordinator and no
event loop. Anything that needs `hass` is covered in test_notification_dispatch.
"""

import json
import re
from pathlib import Path

import pytest

from custom_components.ha_creality_ws.const import (
    CLEAR_NOTIFICATION_MARKER,
    NOTIFY_LIVE_MAX_PUSHES_PER_JOB,
    NOTIFY_LIVE_MILESTONE_STEP,
    NOTIFY_LIVE_MIN_INTERVAL_SECS,
    NOTIFY_LIVE_TRANSITION_FLOOR_SECS,
)
from custom_components.ha_creality_ws.notification_rules import (
    ALERT_ERROR,
    ALERT_RUNOUT,
    PHASE_PAUSED,
    PHASE_PRINTING,
    PHASE_START,
    EVENT_COMPLETED,
    EVENT_SOON,
    EVENT_STOPPED,
    LiveCardState,
    LiveSnapshot,
    NotifyLinks,
    NotifyVisuals,
    PushReason,
    build_alert_payload,
    build_clear_payload,
    build_live_payload,
    build_event_payload,
    coerce_targets,
    compute_when,
    display_filename,
    format_duration,
    format_filament_length,
    is_mobile_target,
    is_new_job_cycle,
    sanitize_tag,
)

TAG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The real shipped strings, not a fixture copy: a template renamed in
# strings.json without updating the code should fail here.
STRINGS = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/strings.json"
    ).read_text(encoding="utf-8")
)["common"]  # notification strings; see test_translations.py for why "common"

CHANNEL = STRINGS["channel_live"]
_LABELS = {
    "pause": STRINGS["action_pause"],
    "resume": STRINGS["action_resume"],
    "stop": STRINGS["action_stop"],
}


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, []),
        # Legacy single device migrates.
        ({"notify_device": "notify.mobile_app_a"}, ["notify.mobile_app_a"]),
        ({"notify_device": "   "}, []),
        ({"notify_device": None}, []),
        # A deliberately emptied list must STAY empty. Using truthiness instead
        # of presence here would resurrect the legacy device on every reload.
        ({"notify_device": "notify.mobile_app_a", "notify_targets": []}, []),
        # The new key wins outright.
        (
            {"notify_device": "notify.old", "notify_targets": ["notify.new"]},
            ["notify.new"],
        ),
        # A hand-edited .storage holding a bare string must not be iterated as
        # characters -- each one would silently fail to route.
        ({"notify_targets": "notify.mobile_app_a"}, ["notify.mobile_app_a"]),
        # Dedupe and strip, order preserved.
        (
            {"notify_targets": ["notify.b", " notify.a ", "notify.b", ""]},
            ["notify.b", "notify.a"],
        ),
        # Junk types are dropped, not crashed on.
        ({"notify_targets": [None, 42, "notify.a"]}, ["notify.a"]),
        ({"notify_targets": 42}, []),
    ],
)
def test_coerce_targets(options, expected):
    assert coerce_targets(options) == expected


@pytest.mark.parametrize(
    "target,expected",
    [
        ("notify.mobile_app_pixel", True),
        ("notify.mobile_app_seans_iphone", True),
        ("notify.signal_messenger", False),
        ("notify.persistent_notification", False),
        ("mobile_app_pixel", True),
        ("", False),
        (None, False),
    ],
)
def test_is_mobile_target(target, expected):
    assert is_mobile_target(target) is expected


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,expected",
    [
        # K1C firmware reports the whole path; this is the bug being fixed.
        ("/usr/data/printer_data/gcodes/3DBenchy.gcode", "3DBenchy.gcode"),
        ("3DBenchy.gcode", "3DBenchy.gcode"),
        ("C:\\prints\\part.gcode", "part.gcode"),
        ("/a/b/", "b"),
        # A path that basenames to nothing falls back rather than returning "".
        ("/", "/"),
        ("", ""),
        (None, ""),
        (42, ""),
    ],
)
def test_display_filename(raw, expected):
    assert display_filename(raw) == expected


@pytest.mark.parametrize(
    "secs,expected",
    [
        (15132, "4h 12m"),
        (14520, "4h 02m"),
        (720, "12m"),
        (45, "45s"),
        (0, "0s"),
        (-1, ""),
        (None, ""),
        ("nope", ""),
    ],
)
def test_format_duration(secs, expected):
    assert format_duration(secs, STRINGS) == expected


def test_format_duration_survives_a_broken_translation():
    """One mistranslated template must cost a line of text, not the whole
    notification."""
    assert format_duration(720, {"duration_minutes": "{minutes_typo}m"}) == ""
    assert format_duration(720, {}) == ""


@pytest.mark.parametrize(
    "mm,expected",
    [(4800, "4.8 m"), (12400, "12.4 m"), (0, ""), (-5, ""), (None, ""), ("x", "")],
)
def test_format_filament_length(mm, expected):
    assert format_filament_length(mm, STRINGS["filament_length"]) == expected


def test_format_filament_length_survives_a_missing_template():
    assert format_filament_length(4800, None) == ""


def test_sanitize_tag_survives_a_dotted_host():
    """The printer host is an IP, and dots are illegal in a tag.

    A host-derived tag would be rejected outright, which is why the coordinator
    bases tags on entry_id -- but the helper must still be safe if one arrives.
    """
    assert TAG_RE.match(sanitize_tag("ha_creality_ws_1.2.3.4_job"))
    assert sanitize_tag("ha_creality_ws_1.2.3.4_job") == "ha_creality_ws_1_2_3_4_job"


def test_sanitize_tag_bounds_and_fallback():
    assert len(sanitize_tag("x" * 200)) == 64
    assert TAG_RE.match(sanitize_tag(""))
    assert TAG_RE.match(sanitize_tag(None))
    assert TAG_RE.match(sanitize_tag("...."))


# --------------------------------------------------------------------------- #
# Job-cycle detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prog,job_restarted,notified,expected",
    [
        # Nothing to re-arm yet.
        (50, False, False, False),
        # A real drop clear of the jitter band.
        (0, False, True, True),
        (90, False, True, True),
        # Inside the jitter band: the printer rounds to 100 early, reports 99,
        # then finishes. Re-arming here sent completion twice per print.
        (99, False, True, False),
        (91, False, True, False),
        # ...unless the job clock also restarted, which is unambiguous.
        (99, True, True, True),
        # Still at 100: not a new cycle.
        (100, True, True, False),
    ],
)
def test_is_new_job_cycle(prog, job_restarted, notified, expected):
    assert is_new_job_cycle(prog, job_restarted, notified) is expected


# --------------------------------------------------------------------------- #
# Live card state machine
# --------------------------------------------------------------------------- #


def _snap(state="printing", progress=10, **kw):
    kw.setdefault("job_active", state in {"printing", "paused", "processing"})
    return LiveSnapshot(activity_state=state, progress=progress, **kw)


def test_no_push_when_no_job_is_active():
    state = LiveCardState()
    assert state.decide(snap=_snap("idle", 0), now_mono=0.0, now_epoch=1_700_000_000.0) is None


def test_first_frame_of_a_job_starts_the_card():
    state = LiveCardState()
    assert state.decide(snap=_snap(), now_mono=0.0, now_epoch=1_700_000_000.0) is PushReason.START


def test_a_restart_mid_print_restarts_the_card_rather_than_leaving_it_frozen():
    """Priming clears card_active without notifying (issue #112).

    The stale-completion state the printer reports forever is progress >= 100,
    which is not job_active, so it cannot start a card. A genuinely running job
    can, and that push is the resync.
    """
    state = LiveCardState()
    state.reset_for_new_job(progress=42)
    assert state.card_active is False
    assert (
        state.decide(snap=_snap(progress=42), now_mono=100.0, now_epoch=1_700_000_000.0)
        is PushReason.START
    )


def test_a_stale_completed_job_does_not_start_a_card():
    state = LiveCardState()
    state.reset_for_new_job(progress=100)
    assert (
        state.decide(
            snap=_snap("completed", 100, job_active=False),
            now_mono=0.0,
            now_epoch=1_700_000_000.0,
        )
        is None
    )


def test_progress_within_a_milestone_bucket_is_silent():
    state = LiveCardState()
    snap = _snap(progress=10)
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=1_700_000_000.0, when=None
    )
    later = NOTIFY_LIVE_MIN_INTERVAL_SECS + 1
    # Same bucket -> nothing, even well past the interval floor.
    assert (
        state.decide(
            snap=_snap(progress=10 + NOTIFY_LIVE_MILESTONE_STEP - 1),
            now_mono=later,
            now_epoch=1_700_000_000.0,
        )
        is None
    )


def test_crossing_a_milestone_pushes_once_the_interval_has_passed():
    state = LiveCardState()
    snap = _snap(progress=10)
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=1_700_000_000.0, when=None
    )
    crossed = _snap(progress=10 + NOTIFY_LIVE_MILESTONE_STEP)
    # Too soon.
    assert state.decide(snap=crossed, now_mono=1.0, now_epoch=1_700_000_000.0) is None
    # Interval elapsed.
    assert (
        state.decide(
            snap=crossed, now_mono=NOTIFY_LIVE_MIN_INTERVAL_SECS + 1, now_epoch=1_700_000_000.0
        )
        is PushReason.MILESTONE
    )


def test_end_of_print_jitter_cannot_produce_a_second_push():
    """The recorded 99 -> 100 -> 99 -> 99 -> 100 sequence from b2ac2ea.

    The milestone latch is monotonic, so this is jitter-proof by construction
    rather than by a throttle layered on top.
    """
    state = LiveCardState()
    now = 0.0
    pushes = []
    for progress in (99, 100, 99, 99, 100):
        now += NOTIFY_LIVE_MIN_INTERVAL_SECS + 1
        snap = _snap(progress=progress)
        reason = state.decide(snap=snap, now_mono=now, now_epoch=1_700_000_000.0)
        if reason is not None:
            pushes.append((reason, progress))
            state.record_push(
                reason=reason, snap=snap, now_mono=now, now_epoch=1_700_000_000.0, when=None
            )
    # The START, then the single 95->100 bucket crossing. Never a second one.
    assert pushes == [(PushReason.START, 99), (PushReason.MILESTONE, 100)]


def test_a_state_change_bypasses_the_interval_floor():
    state = LiveCardState()
    snap = _snap(progress=42)
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=1_700_000_000.0, when=None
    )
    paused = _snap("paused", 42)
    # Well inside the 30s floor, but pause has to show up at once.
    assert (
        state.decide(
            snap=paused, now_mono=NOTIFY_LIVE_TRANSITION_FLOOR_SECS + 0.1, now_epoch=1_700_000_000.0
        )
        is PushReason.TRANSITION
    )


def test_a_flapping_state_is_still_floored():
    state = LiveCardState()
    snap = _snap(progress=42)
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=1_700_000_000.0, when=None
    )
    assert (
        state.decide(snap=_snap("paused", 42), now_mono=0.5, now_epoch=1_700_000_000.0) is None
    )


def test_an_expired_chronometer_is_replaced_exactly_once():
    """A jumpy printLeftTime can freeze the on-device timer at 0:00.

    Replacing it needs one push; repeating that every frame is what iOS drops.
    """
    state = LiveCardState()
    snap = _snap(progress=42)
    epoch = 1_700_000_000.0
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=epoch, when=int(epoch + 60)
    )
    past = NOTIFY_LIVE_MIN_INTERVAL_SECS + 1
    assert (
        state.decide(snap=snap, now_mono=past, now_epoch=epoch + 120) is PushReason.OVERRUN
    )
    state.record_push(
        reason=PushReason.OVERRUN, snap=snap, now_mono=past, now_epoch=epoch + 120, when=None
    )
    assert state.decide(snap=snap, now_mono=past * 2, now_epoch=epoch + 240) is None


def test_the_circuit_breaker_bounds_pushes_per_job():
    state = LiveCardState()
    state.pushes_this_job = NOTIFY_LIVE_MAX_PUSHES_PER_JOB
    state.card_active = True
    assert state.decide(snap=_snap(), now_mono=1e6, now_epoch=1_700_000_000.0) is None


# Every field is dirtied from here, so a field added without a matching reset
# fails the test below instead of quietly leaking across jobs. Built off
# dataclasses.fields so a new field cannot be forgotten here either.
_DIRTY_LIVE_CARD_STATE = {
    "card_active": True,
    "job_finished": True,
    "milestone": 19,
    "last_state": "printing",
    "last_push_mono": 123.0,
    "last_when": 1_700_000_060,
    "pushes_this_job": 7,
    "started_epoch": 1_700_000_000.0,
    "overrun_pushed": True,
}


def test_the_dirty_fixture_covers_every_field():
    """Guards the guard: a new field with no entry above would make
    test_reset_for_new_job_clears_every_field silently stop checking it."""
    import dataclasses

    assert {f.name for f in dataclasses.fields(LiveCardState)} == set(
        _DIRTY_LIVE_CARD_STATE
    )


def test_reset_for_new_job_clears_every_field():
    state = LiveCardState()
    for name, value in _DIRTY_LIVE_CARD_STATE.items():
        setattr(state, name, value)
    assert state != LiveCardState(), "the fixture must actually dirty the state"

    state.reset_for_new_job()
    assert state == LiveCardState()


def test_reset_baselines_the_milestone_off_visible_progress():
    """Otherwise START would be followed immediately by a redundant milestone."""
    state = LiveCardState()
    state.reset_for_new_job(progress=42)
    assert state.milestone == 42 // NOTIFY_LIVE_MILESTONE_STEP


def test_clear_keeps_the_milestone_latch():
    """Clearing the card must not re-arm progress pushes for the same job."""
    state = LiveCardState()
    snap = _snap(progress=42)
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=1_700_000_000.0, when=None
    )
    milestone = state.milestone
    state.clear()
    assert state.card_active is False
    assert state.milestone == milestone


def test_expiry_tracks_apples_eight_hour_ceiling():
    state = LiveCardState()
    epoch = 1_700_000_000.0
    assert state.is_expired(epoch) is False  # never started
    state.record_push(
        reason=PushReason.START, snap=_snap(), now_mono=0.0, now_epoch=epoch, when=None
    )
    assert state.is_expired(epoch + 7 * 3600) is False
    assert state.is_expired(epoch + 9 * 3600) is True


# --------------------------------------------------------------------------- #
# when
# --------------------------------------------------------------------------- #


def test_compute_when_is_wall_clock():
    """A monotonic clock here would put the chronometer in 1970.

    The prime deadline elsewhere in the coordinator *is* monotonic, so the two
    must not be confused.
    """
    when = compute_when(1_700_000_000.0, 3600)
    assert when == 1_700_003_600
    assert when > 1_700_000_000


@pytest.mark.parametrize("left", [0, -5, None, "x"])
def test_compute_when_declines_useless_values(left):
    assert compute_when(1_700_000_000.0, left) is None


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


def _live(**kw):
    kw.setdefault("tag", "ha_creality_ws_abc123_job")
    kw.setdefault("title", "K1C")
    kw.setdefault("message", "42% - Layer 128/305")
    kw.setdefault("phase", PHASE_PRINTING)
    kw.setdefault("progress", 42)
    kw.setdefault("when", 1_700_003_600)
    kw.setdefault("channel", CHANNEL)
    return build_live_payload(**kw)


def test_live_payload_shape():
    payload = _live(group="ha_creality_ws_abc123")
    assert payload["title"] == "K1C"
    data = payload["data"]
    assert data["live_update"] is True
    assert data["tag"] == "ha_creality_ws_abc123_job"
    assert data["progress"] == 42
    assert data["progress_max"] == 100
    assert data["chronometer"] is True
    assert data["when"] == 1_700_003_600
    assert data["group"] == "ha_creality_ws_abc123"
    assert data["notification_icon"].startswith("mdi:")
    # The timer replaces critical_text, so setting both is just noise.
    assert "critical_text" not in data


def test_live_payload_alerts_only_once():
    """Live pushes must not buzz. The terminal push, which shares the tag, must."""
    assert _live()["data"]["alert_once"] is True


def test_paused_payload_stops_the_timer_explicitly():
    """Omitting `chronometer` may not clear a previously-set one, and a timer
    counting down through a pause is the worse failure."""
    data = _live(phase=PHASE_PAUSED, status_text=STRINGS["status_paused"])["data"]
    assert data["chronometer"] is False
    assert "when" not in data
    assert data["critical_text"] == "Paused"
    assert data["notification_icon"] == "mdi:pause-circle"


def test_start_payload_labels_itself_when_there_is_no_eta_yet():
    data = _live(
        phase=PHASE_START, progress=0, when=None, status_text=STRINGS["status_starting"]
    )["data"]
    assert data["critical_text"] == "Starting"
    assert data["progress"] == 0
    # Stated, not omitted: dropping the key might leave a chronometer already
    # running on the phone.
    assert data["chronometer"] is False


def test_an_expired_estimate_stops_the_timer_rather_than_freezing_it():
    """The overrun push exists to replace a chronometer stuck at 0:00, so it has
    to say so explicitly -- omitting the key could leave it stuck."""
    data = _live(
        phase=PHASE_PRINTING,
        progress=88,
        when=None,
        status_text=STRINGS["status_finishing"],
    )["data"]
    assert data["chronometer"] is False
    assert data["critical_text"] == "Finishing"


def test_unknown_progress_becomes_an_indeterminate_bar():
    data = _live(progress=None)["data"]
    assert data["progress_indeterminate"] is True
    assert "progress" not in data


def test_progress_is_clamped():
    assert _live(progress=150)["data"]["progress"] == 100
    assert _live(progress=-3)["data"]["progress"] == 0


def test_expired_activity_degrades_to_a_plain_tagged_notification():
    """Past Apple's 8h ceiling the card still replaces itself in place and still
    shows an Android progress bar; only the activity-only keys go."""
    data = _live(live_update=False)["data"]
    for key in (
        "live_update",
        "chronometer",
        "when",
        "critical_text",
        "progress_bar_color",
        "progress_bar_direction",
        "notification_icon_color",
    ):
        assert key not in data, key
    assert data["tag"] == "ha_creality_ws_abc123_job"
    assert data["progress"] == 42
    assert data["progress_max"] == 100


def test_live_payload_carries_the_preview_but_never_a_snapshot():
    """A big picture is re-downloaded on every Android push and has no slot in an
    iOS Live Activity, so it belongs on terminal pushes only."""
    visuals = NotifyVisuals(
        preview_url="/api/image_proxy/image.k1c_preview",
        snapshot_url="/api/camera_proxy/camera.k1c_cam",
    )
    data = _live(visuals=visuals)["data"]
    assert data["icon_url"] == "/api/image_proxy/image.k1c_preview"
    assert "image" not in data


def test_ungated_media_is_simply_absent():
    """The caller passes None when the image reports a placeholder reason or the
    camera has never captured a frame. Sending the 1x1 URL would render as an
    empty grey box."""
    data = _live(visuals=NotifyVisuals())["data"]
    assert "icon_url" not in data
    assert "image" not in data


def test_links_ship_both_dialects_and_omit_what_did_not_resolve():
    data = _live(
        links=NotifyLinks(click_action="entityId:camera.k1c_cam", tap_path="/lovelace/printer")
    )["data"]
    assert data["clickAction"] == "entityId:camera.k1c_cam"
    assert data["url"] == "/lovelace/printer"

    data = _live(links=NotifyLinks(click_action="entityId:camera.k1c_cam"))["data"]
    assert data["clickAction"] == "entityId:camera.k1c_cam"
    assert "url" not in data

    # Nothing resolved: never "entityId:None", never an empty url.
    data = _live(links=NotifyLinks())["data"]
    assert "clickAction" not in data
    assert "url" not in data


def test_actions_are_absent_unless_supplied():
    assert "actions" not in _live()["data"]
    actions = [{"action": "CREALITY_PAUSE_ABC12345", "title": "Pause"}]
    assert _live(actions=actions)["data"]["actions"] == actions


def test_terminal_payload_must_actually_alert():
    """It shares the live tag, and alert_once there would replace the card
    silently -- the "print finished" ping would never sound."""
    payload = build_event_payload(
        channel=STRINGS["channel_finished"],
        tag="ha_creality_ws_abc123_job",
        title="K1C",
        message="3DBenchy.gcode finished in 4h 12m",
        kind=EVENT_COMPLETED,
        visuals=NotifyVisuals(snapshot_url="/api/camera_proxy/camera.k1c_cam"),
    )
    data = payload["data"]
    assert "alert_once" not in data
    assert "live_update" not in data
    assert data["importance"] == "high"
    assert data["image"] == "/api/camera_proxy/camera.k1c_cam"
    assert data["notification_icon"] == "mdi:check-circle"
    assert data["push"]["interruption-level"] == "time-sensitive"


def test_stopped_payload_reports_where_it_stopped():
    data = build_event_payload(
        tag="t", title="K1C", message="stopped", kind=EVENT_STOPPED, progress=42,
        channel=STRINGS["channel_finished"],
    )["data"]
    assert data["progress"] == 42
    assert data["notification_icon"] == "mdi:stop-circle"


@pytest.mark.parametrize(
    "kind,icon",
    [(ALERT_ERROR, "mdi:alert-circle"), (ALERT_RUNOUT, "mdi:printer-3d-nozzle-alert")],
)
def test_alert_payload_uses_its_own_tag_and_never_alert_once(kind, icon):
    data = build_alert_payload(
        tag="ha_creality_ws_abc123_alert", title="K1C", message="boom", kind=kind,
        channel=STRINGS["channel_alerts"],
    )["data"]
    assert data["tag"] == "ha_creality_ws_abc123_alert"
    assert data["notification_icon"] == icon
    assert "alert_once" not in data
    assert "live_update" not in data


def test_clear_payload_is_the_sentinel_and_carries_no_title():
    payload = build_clear_payload("ha_creality_ws_abc123_job")
    assert payload["message"] == CLEAR_NOTIFICATION_MARKER
    assert payload["data"] == {"tag": "ha_creality_ws_abc123_job"}
    assert "title" not in payload


def test_every_builder_emits_a_legal_tag_even_from_a_dotted_host():
    host_tag = "ha_creality_ws_192.168.1.50_job"
    for payload in (
        _live(tag=host_tag),
        build_event_payload(tag=host_tag, title="K1C", message="m", kind=EVENT_COMPLETED,
                            channel=CHANNEL),
        build_alert_payload(tag=host_tag, title="K1C", message="m", kind=ALERT_ERROR,
                            channel=CHANNEL),
        build_clear_payload(host_tag),
    ):
        assert TAG_RE.match(payload["data"]["tag"])


@pytest.mark.parametrize(
    "kind,icon",
    [
        (EVENT_COMPLETED, "mdi:check-circle"),
        (EVENT_STOPPED, "mdi:stop-circle"),
        (EVENT_SOON, "mdi:clock-fast"),
    ],
)
def test_every_lifecycle_flavour_has_its_own_glyph(kind, icon):
    data = build_event_payload(tag="t", title="K1C", message="m", kind=kind,
                              channel=CHANNEL)["data"]
    assert data["notification_icon"] == icon
    # None of them may carry alert_once: they share the live card's tag family
    # and would replace it without a sound.
    assert "alert_once" not in data


def test_an_unknown_lifecycle_flavour_falls_back_rather_than_raising():
    data = build_event_payload(tag="t", title="K1C", message="m", kind="???",
                              channel=CHANNEL)["data"]
    assert data["notification_icon"] == "mdi:check-circle"
