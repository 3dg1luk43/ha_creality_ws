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
    NOTIFY_END_CONFIRM_SECS,
    NOTIFY_LIVE_MAX_PUSHES_PER_JOB,
    NOTIFY_LIVE_MILESTONE_STEP,
    NOTIFY_LIVE_MIN_INTERVAL_SECS,
    NOTIFY_LIVE_TRANSITION_FLOOR_SECS,
)
from custom_components.ha_creality_ws.notification_rules import (
    ALERT_ERROR,
    ALERT_RUNOUT,
    TEMPLATE_FIELDS,
    PHASE_PAUSED,
    PHASE_PRINTING,
    PHASE_START,
    EVENT_COMPLETED,
    EVENT_SOON,
    EVENT_STOPPED,
    JobEndWatch,
    JobEvent,
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
    render_user_template,
    sanitize_tag,
    template_unknown_fields,
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
        # Not the companion app: only the full `mobile_app_` prefix counts, or
        # these get companion-only `data` and reject the whole call.
        ("notify.mobile_application_relay", False),
        ("notify.mobile_apps", False),
        ("notify.mobile_app", False),
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
    "prog,job_restarted,expected",
    [
        # A real drop clear of the jitter band.
        (0, False, True),
        (90, False, True),
        # Inside the jitter band: the printer rounds to 100 early, reports 99,
        # then finishes. Re-arming here sent completion twice per print.
        (99, False, False),
        (91, False, False),
        # ...unless the job clock also restarted, which is unambiguous.
        (99, True, True),
        # Still at 100: the first frame of a new job usually still carries the
        # old one's 100, and re-arming there re-announces the completion.
        (100, True, False),
    ],
)
def test_is_new_job_cycle_after_a_completion(prog, job_restarted, expected):
    assert (
        is_new_job_cycle(prog, job_restarted, ended_at_completion=True) is expected
    )


def test_nothing_re_arms_before_the_job_has_ended():
    """Otherwise every mid-print frame below the jitter band looks like a new job."""
    for prog in (0, 50, 90, 99):
        assert (
            is_new_job_cycle(prog, False, ended_at_completion=False, ended_early=False)
            is False
        )
        assert (
            is_new_job_cycle(prog, True, ended_at_completion=False, ended_early=False)
            is False
        )


@pytest.mark.parametrize(
    "prog,job_restarted,expected",
    [
        # The job clock restarting is the only signal available after an early
        # stop -- a print stopped at 30% never leaves the jitter band, so a
        # progress test would re-arm on the very next frame and announce the
        # stop again.
        (2, True, True),
        (30, False, False),
        (0, False, False),
        (90, False, False),
    ],
)
def test_is_new_job_cycle_after_an_early_stop(prog, job_restarted, expected):
    assert (
        is_new_job_cycle(
            prog, job_restarted, ended_at_completion=False, ended_early=True
        )
        is expected
    )


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


def test_a_pause_is_pushed_with_no_delay_at_all():
    """Pausing is a deliberate act performed at the printer or in the app, and
    the card is where the user looks to see it took effect. This used to sit
    behind a five-second floor meant to absorb flapping telemetry; that made a
    real pause feel broken, and the per-job cap below is the better place to
    bound a printer that genuinely flaps.
    """
    state = LiveCardState()
    snap = _snap(progress=42)
    state.record_push(
        reason=PushReason.START, snap=snap, now_mono=0.0, now_epoch=1_700_000_000.0, when=None
    )
    assert NOTIFY_LIVE_TRANSITION_FLOOR_SECS == 0.0
    # Same instant as the push that started the card.
    assert (
        state.decide(snap=_snap("paused", 42), now_mono=0.0, now_epoch=1_700_000_000.0)
        is PushReason.TRANSITION
    )


def test_a_state_that_has_not_changed_does_not_retrigger():
    """What stops an immediate transition push becoming a push per frame: the
    derived state has to actually differ from the one last sent."""
    state = LiveCardState()
    snap = _snap("paused", 42)
    state.record_push(
        reason=PushReason.TRANSITION, snap=snap, now_mono=0.0,
        now_epoch=1_700_000_000.0, when=None,
    )
    for tick in range(1, 30):
        assert (
            state.decide(snap=snap, now_mono=tick * 0.1, now_epoch=1_700_000_000.0)
            is None
        )


def test_pathological_flapping_is_bounded_by_the_per_job_cap():
    """With no transition floor the cap is the only thing standing between a
    printer that flaps every frame and the relay's daily push budget."""
    state = LiveCardState()
    pushes = 0
    for tick in range(NOTIFY_LIVE_MAX_PUSHES_PER_JOB * 2):
        snap = _snap("paused" if tick % 2 else "printing", 42)
        reason = state.decide(
            snap=snap, now_mono=tick * 0.05, now_epoch=1_700_000_000.0
        )
        if reason is None:
            continue
        pushes += 1
        state.record_push(
            reason=reason, snap=snap, now_mono=tick * 0.05,
            now_epoch=1_700_000_000.0, when=None,
        )
    assert pushes == NOTIFY_LIVE_MAX_PUSHES_PER_JOB


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
    "last_progress": 42,
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


# --------------------------------------------------------------------------- #
# A lifecycle banner is a confirmation, not a last refresh of the card
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", [EVENT_COMPLETED, EVENT_STOPPED, EVENT_SOON])
def test_a_lifecycle_banner_never_carries_a_progress_bar(kind):
    """The completion banner used to ship `progress: 100`, so the notification
    telling you a print had *finished* still rendered a progress bar, full, with
    nothing left to track. It lands on the live card's own tag, which made the
    end of the print read as one more refresh of the card."""
    data = build_event_payload(
        tag="ha_creality_ws_abc123_live",
        title="K1C",
        message="3DBenchy.gcode finished in 4h 12m",
        kind=kind,
        channel=STRINGS["channel_finished"],
    )["data"]
    for key in ("progress", "progress_max", "progress_indeterminate",
                "chronometer", "when", "live_update"):
        assert key not in data, key


def test_the_banner_keeps_what_a_confirmation_is_actually_for():
    """Dropping the bar must not quietly drop the rest: the text, a sound, the
    tick and the picture of the bed are the whole notification."""
    payload = build_event_payload(
        tag="ha_creality_ws_abc123_live",
        title="K1C",
        message="3DBenchy.gcode finished in 4h 12m",
        kind=EVENT_COMPLETED,
        channel=STRINGS["channel_finished"],
        visuals=NotifyVisuals(snapshot_url="/api/camera_proxy/camera.k1c_cam"),
        ends_activity=True,
    )
    data = payload["data"]
    assert payload["message"] == "3DBenchy.gcode finished in 4h 12m"
    assert data["notification_icon"] == "mdi:check-circle"
    assert data["image"] == "/api/camera_proxy/camera.k1c_cam"
    assert data["importance"] == "high"
    assert "alert_once" not in data
    # Still ends the iOS Live Activity the card started.
    assert data["activity"] == "end"


# --------------------------------------------------------------------------- #
# User text templates
# --------------------------------------------------------------------------- #

VALUES = {
    "device": "K1C",
    "filename": "3DBenchy.gcode",
    "progress": "42",
    "layer": "120",
    "total_layers": "300",
    "eta": "1h 04m",
    "elapsed": "42m",
    "filament": "1.2 m",
    "nozzle": "220",
    "bed": "60",
    "state": "printing",
    "error_code": "",
    "error_key": "",
    "minutes": "",
}


def test_a_template_renders_the_values_it_names():
    assert render_user_template(
        "{device}: {filename} at {progress}%", VALUES
    ) == "K1C: 3DBenchy.gcode at 42%"


def test_text_around_the_placeholders_survives_untouched():
    """There is no escaping rule: anything that is not a known placeholder,
    including a lone brace, is literal text."""
    assert render_user_template("Done! {device} }{ ok", VALUES) == "Done! K1C }{ ok"


def test_an_optional_segment_is_dropped_when_its_value_is_unknown():
    """The reason the syntax exists: this printer reports no estimate in the
    first minute of a print, and "3DBenchy.gcode -- left" reads as a bug."""
    values = {**VALUES, "eta": ""}
    assert render_user_template(
        "{filename}[ -- {eta} left]", values
    ) == "3DBenchy.gcode"
    assert render_user_template(
        "{filename}[ -- {eta} left]", VALUES
    ) == "3DBenchy.gcode -- 1h 04m left"


def test_an_optional_segment_needs_every_value_it_names():
    """Partially filled is the case an optional segment exists to avoid: "layer
    120/" is worse than no layer count at all."""
    values = {**VALUES, "total_layers": ""}
    assert render_user_template("[layer {layer}/{total_layers}]", values) == ""


def test_a_zero_is_a_value_and_keeps_its_segment():
    """0% is something the printer said; unknown is not. An optional segment
    that vanished at 0 would leave a card blank for the first minute."""
    values = {**VALUES, "progress": "0"}
    assert render_user_template("[{progress}% done]", values) == "0% done"


def test_a_bracketed_segment_with_no_placeholder_is_literal():
    """Someone writing [PRINTER] in their own text means the brackets."""
    assert render_user_template("[PRINTER] {device}", VALUES) == "[PRINTER] K1C"


def test_the_gap_an_emptied_placeholder_leaves_is_closed_up():
    """Unbracketed placeholders are still allowed to go missing, and two spaces
    in the middle of a notification look like a rendering fault."""
    values = {**VALUES, "eta": ""}
    assert render_user_template("{filename} {eta} left", values) == (
        "3DBenchy.gcode left"
    )


def test_an_unknown_placeholder_refuses_the_whole_template():
    """It has to fall back rather than render the typo: `{filament_grams}` is a
    value this printer does not stream, and delivering the literal text would
    tell the user their template works."""
    assert template_unknown_fields(
        "{filament_grams} used", TEMPLATE_FIELDS["completed"]
    ) == ["filament_grams"]
    assert render_user_template("{filament_grams} used", VALUES) == ""


@pytest.mark.parametrize("name", sorted(TEMPLATE_FIELDS))
def test_a_known_placeholder_is_not_reported_as_unknown(name):
    every = " ".join(f"{{{field}}}" for field in TEMPLATE_FIELDS[name])
    assert template_unknown_fields(every, TEMPLATE_FIELDS[name]) == []


def test_each_unknown_placeholder_is_reported_once():
    """The options-flow error names them, and a template repeating one typo
    should not list it twice."""
    assert template_unknown_fields(
        "{nope} {nope} {nah}", TEMPLATE_FIELDS["live"]
    ) == ["nope", "nah"]


def test_a_placeholder_is_only_offered_where_it_can_be_filled():
    """The whole reason the sets differ. `{minutes}` is the point of the
    finishing-soon reminder and is nothing at all on a live card, where it would
    have rendered as empty text and read as a bug in the user's template."""
    assert template_unknown_fields("{minutes}", TEMPLATE_FIELDS["finishing_soon"]) == []
    assert template_unknown_fields("{minutes}", TEMPLATE_FIELDS["live"]) == ["minutes"]

    assert template_unknown_fields("{error_code}", TEMPLATE_FIELDS["error"]) == []
    assert template_unknown_fields("{error_code}", TEMPLATE_FIELDS["live"]) == [
        "error_code"
    ]

    # A stop is only visible once the printer has reset the job, so the numbers
    # that reset are not offered for it.
    for gone in ("eta", "elapsed", "layer", "filament", "state"):
        assert template_unknown_fields(
            f"{{{gone}}}", TEMPLATE_FIELDS["stopped"]
        ) == [gone], gone
    # What survives: the two the watch remembers, and the temperatures.
    assert template_unknown_fields(
        "{filename} {progress} {nozzle} {bed} {device}", TEMPLATE_FIELDS["stopped"]
    ) == []


def test_every_customisable_notification_has_a_field_list():
    """An option with no list is a field the options flow cannot render help
    for, and a list with no option is documentation for nothing."""
    from custom_components.ha_creality_ws.const import NOTIFY_TEMPLATE_OPTIONS

    assert set(TEMPLATE_FIELDS) == set(NOTIFY_TEMPLATE_OPTIONS)


@pytest.mark.parametrize("name", sorted(TEMPLATE_FIELDS))
def test_every_field_list_starts_from_the_printer_and_the_job(name):
    """Whatever else differs, a notification can always say which printer and
    which file it is about -- it is the first thing a user writes."""
    assert {"device", "filename"} <= set(TEMPLATE_FIELDS[name])


@pytest.mark.parametrize("template", ["", "   ", None, 42, "[{eta}]"])
def test_nothing_usable_renders_as_nothing(template):
    """Empty means "use the shipped text" to every caller. The last case is the
    one that matters: a template whose every segment turned out unknown must
    fall back, because a blank notification is worse than a generic one."""
    assert render_user_template(template, {**VALUES, "eta": ""}) == ""


# --------------------------------------------------------------------------- #
# Noticing that a print was stopped
# --------------------------------------------------------------------------- #
# Stopping a print from the printer's screen, from the Creality app or from Home
# Assistant all look the same in telemetry, and none of them looks like an
# event: `state` goes back to 0 with the file name still attached and the
# progress reset to 0, which derives as "processing" -- the same word as a
# warm-up. So the end is a transition, and this is the thing that watches for
# it.

STOP_AS_REPORTED = dict(state="processing", progress=0, filename="3DBenchy.gcode")


def _watching(progress=42, filename="3DBenchy.gcode"):
    """A watch that has seen this job printing."""
    watch = JobEndWatch()
    assert watch.observe(
        state="printing", progress=progress, filename=filename, now_mono=0.0
    ) is None
    return watch


def test_a_stop_is_announced_once_the_ambiguous_state_persists():
    """What the printer actually reports when a print is cancelled: state 0,
    progress 0, file name still there. Nothing in that frame says "stopped"."""
    watch = _watching()

    assert watch.observe(**STOP_AS_REPORTED, now_mono=1.0) is None
    assert watch.pending() is True
    assert watch.observe(
        **STOP_AS_REPORTED, now_mono=1.0 + NOTIFY_END_CONFIRM_SECS
    ) is JobEvent.ENDED_EARLY


def test_the_progress_and_name_reported_are_the_ones_from_before_the_stop():
    """The frame that reveals the stop has already lost both: the printer resets
    the progress to 0, and some firmware clears the file name. "Stopped at 0%"
    is what the telemetry alone would have said."""
    watch = _watching(progress=42)
    watch.observe(**STOP_AS_REPORTED, now_mono=1.0)
    watch.observe(**STOP_AS_REPORTED, now_mono=1.0 + NOTIFY_END_CONFIRM_SECS)

    assert watch.progress == 42
    assert watch.job_name == "3DBenchy.gcode"


def test_a_single_idle_frame_mid_print_is_not_a_stop():
    """This printer does emit one, and it must not cost the user a
    notification."""
    watch = _watching()

    assert watch.observe(**STOP_AS_REPORTED, now_mono=1.0) is None
    assert watch.observe(
        state="printing", progress=43, filename="3DBenchy.gcode", now_mono=2.0
    ) is None
    assert watch.pending() is False

    # And the clock starts again from the next one, rather than counting the
    # time the print spent running.
    assert watch.observe(
        **STOP_AS_REPORTED, now_mono=2.0 + NOTIFY_END_CONFIRM_SECS
    ) is None


def test_the_printer_saying_so_outright_needs_no_confirming():
    """`state == 4` is unambiguous, and a user who just pressed stop should not
    wait for a timer to elapse."""
    watch = _watching()

    assert watch.observe(
        state="stopped", progress=0, filename="3DBenchy.gcode", now_mono=1.0
    ) is JobEvent.ENDED_EARLY


def test_a_cleared_file_name_needs_no_confirming_either():
    """Some firmware ends a cancelled job by dropping the name instead. The job
    is gone from the printer's own point of view, and this is the last frame
    that can say anything about it -- the notification path returns early from
    the next one."""
    watch = _watching()

    assert watch.observe(
        state="idle", progress=0, filename="", now_mono=1.0
    ) is JobEvent.ENDED_EARLY


def test_a_stop_is_announced_only_once():
    watch = _watching()
    watch.observe(state="stopped", progress=0, filename="x.gcode", now_mono=1.0)

    for tick in range(2, 40):
        assert watch.observe(
            **STOP_AS_REPORTED, now_mono=float(tick)
        ) is None, tick


def test_a_lost_connection_is_not_a_stopped_print():
    """"unknown" is the WebSocket being down and "off" is the power switch.
    Neither says anything about the job, and announcing a stop off one of them
    would turn every network blip into "your print was stopped"."""
    for blind in ("unknown", "off"):
        watch = _watching()
        assert watch.observe(
            state=blind, progress=0, filename="3DBenchy.gcode", now_mono=1.0
        ) is None
        assert watch.pending() is False, blind
        # Nor does the printer coming back with the job gone count the time it
        # was away: the confirmation starts from the first frame that actually
        # described the printer.
        assert watch.observe(
            **STOP_AS_REPORTED, now_mono=1.0 + NOTIFY_END_CONFIRM_SECS
        ) is None, blind


def test_a_finished_print_is_never_a_stopped_one():
    """The completion notification owns a job that reached 100%. This is the
    frame *before* the caller's completion latch is set, so the guard cannot be
    left to the caller alone."""
    watch = _watching(progress=99)

    assert watch.observe(
        state="completed", progress=100, filename="3DBenchy.gcode", now_mono=1.0
    ) is None
    assert watch.pending() is False


def test_a_job_never_seen_printing_is_never_ended():
    """A warm-up (state 0, file named, 0%) reports the same state a cancelled
    job does, so the watch only ever arms on a job it has seen printing."""
    watch = JobEndWatch()

    for tick in range(40):
        assert watch.observe(**STOP_AS_REPORTED, now_mono=float(tick)) is None


def test_printing_again_after_an_end_is_a_new_job():
    """The signal that re-arms the once-per-print latches. The older ones both
    miss it: reprinting the same file does not change the file name, and a stop
    resets `printJobTime` to 0 so the job clock never runs backwards."""
    watch = _watching()
    watch.observe(state="stopped", progress=0, filename="3DBenchy.gcode", now_mono=1.0)

    assert watch.observe(
        state="printing", progress=1, filename="3DBenchy.gcode", now_mono=2.0
    ) is JobEvent.RESTARTED
    # And it is following the new job, not the one it just buried.
    assert watch.ended is False
    assert watch.progress == 1


def test_a_pause_is_not_an_end():
    """A paused print is still a live job -- it has a card on the phone and a
    Resume button on it."""
    watch = _watching()

    for tick in range(40):
        assert watch.observe(
            state="paused", progress=42, filename="3DBenchy.gcode", now_mono=float(tick)
        ) is None
    assert watch.pending() is False


def test_a_reset_watch_equals_a_fresh_one():
    """A field added without a matching reset leaks the previous job into the
    next one -- the same guard `LiveCardState` has."""
    watch = _watching()
    watch.observe(**STOP_AS_REPORTED, now_mono=1.0)
    watch.observe(**STOP_AS_REPORTED, now_mono=1.0 + NOTIFY_END_CONFIRM_SECS)
    watch.reset()

    assert watch == JobEndWatch()
