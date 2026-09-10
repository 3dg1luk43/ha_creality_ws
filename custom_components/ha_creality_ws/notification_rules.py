"""Pure notification decisions and payload construction.

This module deliberately imports nothing from Home Assistant. ``tools/tests``
stubs out the whole ``homeassistant.*`` tree and CI installs only pytest and
voluptuous, so keeping every decision here means it can be tested with no mocks
at all. Anything that needs ``hass`` -- service calls, the entity registry,
state lookups -- lives in ``coordinator.py``.

Two house rules worth knowing before editing:

* ``tools/tests/test_code_hygiene.py`` rejects ``https://`` literals anywhere in
  this package, so companion-app documentation is cited from README.md rather
  than from here.
* ``const.py`` is exec'd standalone by ``tools/tests/test_manifest_and_specs.py``
  and must stay import-free, which is why the option-coercion helpers live in
  this module instead of alongside the keys they read.
* **No user-visible text lives here.** Every label, status word and message
  template comes from ``strings.json`` and is passed in already resolved, so
  this module only decides structure. Colours and mdi slugs stay, being neither
  language nor prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from typing import Any

from .const import (
    CLEAR_NOTIFICATION_MARKER,
    CONF_NOTIFY_DEVICE,
    CONF_NOTIFY_TARGETS,
    NOTIFY_COLOR_DONE,
    NOTIFY_COLOR_ERROR,
    NOTIFY_COLOR_PAUSED,
    NOTIFY_COLOR_PRINTING,
    NOTIFY_LIVE_IOS_EXPIRY_SECS,
    NOTIFY_LIVE_MAX_PUSHES_PER_JOB,
    NOTIFY_LIVE_MILESTONE_STEP,
    NOTIFY_LIVE_INTERVAL_SECS,
    NOTIFY_LIVE_MIN_INTERVAL_SECS,
    NOTIFY_LIVE_TRANSITION_FLOOR_SECS,
    NOTIFY_REARM_PROGRESS_MAX,
)

# Tags may only contain these characters, and are capped at 64. The printer host
# is an IP address, so a host-derived tag would be rejected outright -- hence
# sanitize_tag() and the entry_id-based base in the coordinator.
_TAG_ILLEGAL = re.compile(r"[^A-Za-z0-9_-]+")
_TAG_MAX_LEN = 64

# Phases a live card can be pushed in. "start" is also what a mid-print Home
# Assistant restart re-issues, so the card resyncs instead of sitting frozen.
PHASE_START = "start"
PHASE_PRINTING = "printing"
PHASE_PAUSED = "paused"


class PushReason(str, Enum):
    """Why a live push is being sent. A ``str`` enum so test failures read well."""

    START = "start"
    TRANSITION = "transition"
    MILESTONE = "milestone"
    REFRESH = "refresh"
    OVERRUN = "overrun"


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #


def coerce_targets(options: Mapping[str, Any]) -> list[str]:
    """Resolve the configured notify targets, migrating the legacy single one.

    Reads ``notify_device`` (a single service name) only when ``notify_targets``
    is *absent*. Presence rather than truthiness is load-bearing: a user who
    deliberately empties the list must keep it empty, or the legacy device would
    be resurrected on every reload. The legacy key is never written or deleted,
    so rolling back to an earlier release keeps working.
    """
    raw = options.get(CONF_NOTIFY_TARGETS)
    if raw is None:
        legacy = options.get(CONF_NOTIFY_DEVICE)
        raw = [legacy] if isinstance(legacy, str) and legacy.strip() else []
    elif isinstance(raw, str):
        # Defends against a hand-edited .storage: iterating a str would yield
        # characters, and each one would silently fail to route.
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        target = item.strip()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def is_mobile_target(target: str | None) -> bool:
    """Whether a notify target is a companion-app service.

    Only these may receive a ``data`` payload. Strict-schema platforms reject
    unknown keys and fail the whole call, ``notify.send_message`` has no ``data``
    field at all, and the relative ``/api/...`` URLs only authenticate from the
    companion app -- three independent reasons for the same gate.
    """
    if not target:
        return False
    service = target.split(".", 1)[1] if "." in target else target
    return service.startswith("mobile_app")


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #


def display_filename(raw: Any) -> str:
    """Basename of a printer-reported print file.

    K1C firmware reports a full path (``/usr/data/printer_data/gcodes/x.gcode``)
    and nothing else in the integration basenames it, so the notification bodies
    used to interpolate the whole thing. The extension is kept: users identify
    files by it. Sensor attributes deliberately keep publishing the raw value,
    because user templates depend on it.
    """
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    tail = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].strip()
    return tail or text


def format_duration(secs: Any, templates: Mapping[str, str]) -> str:
    """Human duration from the caller's templates. Empty when unknown.

    Numbers are pre-formatted into strings before substitution so a translator
    cannot break a format spec (``{minutes:02d}``) by reordering placeholders.
    """
    try:
        total = int(float(secs))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return _fill(
            templates.get("duration_hours_minutes"),
            hours=str(hours),
            minutes=f"{minutes:02d}",
        )
    if minutes:
        return _fill(templates.get("duration_minutes"), minutes=str(minutes))
    return _fill(templates.get("duration_seconds"), seconds=str(seconds))


def format_filament_length(mm: Any, template: str | None) -> str:
    """Filament used, rendered by the caller's template. Empty when unknown."""
    try:
        value = float(mm)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return _fill(template, metres=f"{value / 1000.0:.1f}")


def _fill(template: str | None, **values: str) -> str:
    """Substitute into a translated template, tolerating a broken one.

    A translation with a renamed or malformed placeholder must not take a
    notification down with it, so a failure yields an empty segment that the
    caller drops.
    """
    if not template:
        return ""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return ""


def sanitize_tag(raw: Any) -> str:
    """Coerce anything into a legal notification tag."""
    cleaned = _TAG_ILLEGAL.sub("_", str(raw or "")).strip("_")
    return (cleaned or "ha_creality_ws")[:_TAG_MAX_LEN]


def stringify_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
    """Coerce a companion-app ``data`` dict to what the push relay accepts.

    Android delivery goes out as an FCM data message, whose payload is a
    map<string, string>. A native ``int`` or ``bool`` at the top level makes the
    relay reject the **whole push** with "data must only contain string values",
    so a single `live_update: True` silently costs every Android notification --
    which is exactly what it did: every live-card push to a Galaxy S24 failed
    while the log said only "Error sending notification to S24".

    Nesting is exempt, verified against a real device: ``actions`` (a list of
    dicts, bools inside it and all) and ``push`` go through untouched. Only the
    top level is rewritten.

    ``None`` drops the key rather than sending the string "None", which the
    companion app would treat as a value.
    """
    out: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if value is None:
            continue
        if isinstance(value, bool):
            # Lowercase: the companion app compares against "true"/"false".
            out[key] = "true" if value else "false"
        elif isinstance(value, (int, float)):
            out[key] = str(value)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# Job-cycle detection
# --------------------------------------------------------------------------- #


def is_new_job_cycle(
    prog_val: int,
    job_restarted: bool,
    *,
    ended_at_completion: bool,
    ended_early: bool = False,
) -> bool:
    """Whether a genuinely new job has begun since the last one ended.

    The single home for this predicate; the terminal re-arm and the live-card
    reset both call it. They used to be separate expressions of the same idea,
    which is exactly the drift the comment in ``_prime_notification_state``
    warns about.

    Nothing counts until an ending has been observed, or every mid-print frame
    would look like a fresh cycle. After that there are two signals, and they
    are not interchangeable:

    * The **job clock restarting** is unambiguous and works for any ending. It
      is the only signal available for a job that was stopped part-way, which is
      why ``ended_early`` requires it.
    * A **progress drop** only means anything for a job that ended *at* 100%.
      The printer rounds progress up to 100 a second before a job ends, reports
      99 once more, and only then finishes -- so the drop has to clear that
      jitter band, or the completion notification fires twice for every print.
      Applying the same test to a job stopped at 30% would re-arm on the very
      next frame, because 30 never leaves the band.
    """
    if not (ended_at_completion or ended_early):
        return False
    if prog_val >= 100:
        # Telemetry lags: the frame that first shows a new job usually still
        # carries the previous one's 100. Re-arming here would announce that
        # completion a second time, immediately.
        return False
    if job_restarted:
        return True
    return ended_at_completion and prog_val <= NOTIFY_REARM_PROGRESS_MAX


def _milestone_of(progress: Any) -> int:
    """Which milestone bucket a progress value falls in, or -1 when unknown."""
    try:
        value = int(progress)
    except (TypeError, ValueError):
        return -1
    if value < 0:
        return -1
    return value // NOTIFY_LIVE_MILESTONE_STEP


# --------------------------------------------------------------------------- #
# Live card state machine
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """The subset of telemetry the live card reacts to."""

    activity_state: str
    job_active: bool
    progress: int
    filename: str = ""
    layer: int | None = None
    total_layers: int | None = None
    seconds_left: float | None = None


@dataclass(slots=True)
class LiveCardState:
    """Bookkeeping for one printer's live card. Reset wholesale per job.

    A test asserts this dataclass equals a fresh instance after
    ``reset_for_new_job``, so a field added here without a matching reset fails
    loudly rather than leaking across jobs.
    """

    card_active: bool = False
    job_finished: bool = False
    milestone: int = -1
    last_state: str | None = None
    last_push_mono: float | None = None
    last_when: int | None = None
    last_progress: int | None = None
    pushes_this_job: int = 0
    started_epoch: float | None = None
    overrun_pushed: bool = False

    def reset_for_new_job(self, *, progress: Any = None) -> None:
        """Baseline for a new job without notifying about it.

        Milestone is baselined off the progress we can actually see rather than
        -1, so the START push is not immediately followed by a redundant
        milestone push.
        """
        self.card_active = False
        self.job_finished = False
        self.milestone = _milestone_of(progress)
        self.last_state = None
        self.last_push_mono = None
        self.last_when = None
        self.last_progress = None
        self.pushes_this_job = 0
        self.started_epoch = None
        self.overrun_pushed = False

    def is_expired(self, now_epoch: float) -> bool:
        """Whether iOS has hard-expired the activity (Apple's 8h ceiling)."""
        if self.started_epoch is None:
            return False
        return (now_epoch - self.started_epoch) >= NOTIFY_LIVE_IOS_EXPIRY_SECS

    def decide(
        self, *, snap: LiveSnapshot, now_mono: float, now_epoch: float
    ) -> PushReason | None:
        """Whether to push, and why. ``None`` means stay quiet."""
        if self.job_finished:
            # End-of-print jitter flips the derived state completed -> printing
            # -> completed, and without this latch each flip would tear the card
            # down and stand a new one up seconds later -- two iOS
            # push-to-start slots spent showing the same print twice. Only a
            # genuinely new job (reset_for_new_job) clears it.
            return None
        if not snap.job_active:
            return None
        if self.pushes_this_job >= NOTIFY_LIVE_MAX_PUSHES_PER_JOB:
            return None
        if not self.card_active:
            # Also the mid-print restart resync: priming clears card_active
            # without notifying, so the first frame of an already-running job
            # starts the card rather than leaving a frozen one on the phone.
            return PushReason.START

        since = None if self.last_push_mono is None else now_mono - self.last_push_mono

        if snap.activity_state != self.last_state:
            # Pause/resume must be visible at once, so it bypasses the interval
            # floor -- but not entirely, or a flapping state would spam.
            if since is None or since >= NOTIFY_LIVE_TRANSITION_FLOOR_SECS:
                return PushReason.TRANSITION
            return None

        if since is not None and since < NOTIFY_LIVE_MIN_INTERVAL_SECS:
            return None

        if _milestone_of(snap.progress) > self.milestone:
            # Progress has moved a lot in little time, so refresh early rather
            # than showing a percentage the user can see is behind.
            return PushReason.MILESTONE

        if since is not None and since >= NOTIFY_LIVE_INTERVAL_SECS:
            # The ordinary cadence. Without it the card only ever moved on a 5%
            # boundary, which on a long print left the percentage and the
            # remaining estimate visibly stale for twenty minutes at a time.
            #
            # Only when the card would actually read differently, though. A
            # paused print holds its progress and has no countdown, so
            # refreshing it repaints identical pixels -- and the relay allows
            # only 500 pushes per device per day, so an idle repaint every five
            # minutes is a real cost for no information.
            if snap.progress != self.last_progress or self.last_when is not None:
                return PushReason.REFRESH

        # The chronometer counts down on-device for free, but a jumpy
        # printLeftTime can leave it frozen at 0:00 while progress stays inside
        # one milestone bucket. Replace it once, then go quiet again.
        if (
            not self.overrun_pushed
            and self.last_when is not None
            and now_epoch >= self.last_when
        ):
            return PushReason.OVERRUN

        return None

    def record_push(
        self,
        *,
        reason: PushReason,
        snap: LiveSnapshot,
        now_mono: float,
        now_epoch: float,
        when: int | None,
    ) -> None:
        """Fold a sent push back into the state."""
        if not self.card_active:
            self.card_active = True
            self.started_epoch = now_epoch
        self.last_push_mono = now_mono
        self.last_state = snap.activity_state
        self.pushes_this_job += 1
        # Assigned even when None. A push that carries no deadline (a pause, or
        # an estimate that ran out) has *stopped* the chronometer, so keeping the
        # old deadline would make decide() see it expire and fire a second,
        # pointless overrun push -- once per later push, forever.
        self.last_when = when
        self.last_progress = snap.progress
        self.overrun_pushed = reason is PushReason.OVERRUN
        milestone = _milestone_of(snap.progress)
        if milestone > self.milestone:
            self.milestone = milestone

    def finish(self) -> None:
        """The job this card was tracking has ended.

        Deliberately distinct from ``clear``: a printer that merely went quiet
        should get its card back if telemetry resumes, but a finished job must
        not, or the end-of-print progress jitter would resurrect it.
        """
        self.clear()
        self.job_finished = True

    def clear(self) -> None:
        """Mark the card gone without disturbing the per-job milestone latch."""
        self.card_active = False
        self.last_state = None
        self.last_push_mono = None
        self.last_when = None
        self.started_epoch = None
        self.overrun_pushed = False


# --------------------------------------------------------------------------- #
# Payload construction
# --------------------------------------------------------------------------- #

# Lifecycle and alert flavours. Kept as constants so a typo is an ImportError
# rather than a silently wrong icon.
EVENT_COMPLETED = "completed"
EVENT_STOPPED = "stopped"
EVENT_SOON = "soon"
ALERT_ERROR = "error"
ALERT_RUNOUT = "runout"

# Icon and colour per lifecycle flavour.
_EVENT_STYLE = {
    EVENT_COMPLETED: ("mdi:check-circle", NOTIFY_COLOR_DONE),
    EVENT_STOPPED: ("mdi:stop-circle", NOTIFY_COLOR_PAUSED),
    EVENT_SOON: ("mdi:clock-fast", NOTIFY_COLOR_PRINTING),
}


@dataclass(frozen=True, slots=True)
class NotifyVisuals:
    """Resolved, already-gated media URLs.

    Both are relative ``/api/...`` paths, which the companion app fetches with
    its own credentials -- no token and no signed path needed. The caller is
    responsible for the gating: ``preview_url`` must be ``None`` unless the image
    entity reports ``preview_reason == "ok"``, and ``snapshot_url`` unless the
    camera reports ``snapshot_supported``. Both entities otherwise serve a 1x1
    placeholder, which renders as an empty grey box.
    """

    preview_url: str | None = None
    snapshot_url: str | None = None


@dataclass(frozen=True, slots=True)
class NotifyLinks:
    """Tap targets. Both ship together; each platform ignores the other's key.

    ``click_action`` is Android's and accepts ``entityId:<id>`` to open a
    more-info dialog. iOS's ``url`` does not support that form, so it only gets a
    value when the user configured a dashboard path.
    """

    click_action: str | None = None
    tap_path: str | None = None


def compute_when(now_epoch: float, seconds_left: Any) -> int | None:
    """Absolute epoch the print is expected to finish, for ``when``.

    Wall clock, never ``hass.loop.time()``. The prime deadline elsewhere in the
    coordinator is monotonic, and feeding one of those into ``when`` would put
    the chronometer in 1970.
    """
    try:
        remaining = float(seconds_left)
    except (TypeError, ValueError):
        return None
    if remaining <= 0:
        return None
    return int(now_epoch + remaining)


def _clamp_progress(progress: Any) -> int | None:
    try:
        value = int(progress)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, value))


def _apply_common(
    data: dict[str, Any],
    *,
    group: str | None,
    links: NotifyLinks | None,
    actions: list[dict[str, Any]] | None,
) -> None:
    if group:
        data["group"] = group
    if links is not None:
        # Never emit "entityId:None" or an empty url -- the caller passes None
        # when nothing resolved.
        if links.click_action:
            data["clickAction"] = links.click_action
        if links.tap_path:
            data["url"] = links.tap_path
    if actions:
        data["actions"] = list(actions)


def build_live_payload(
    *,
    tag: str,
    title: str,
    message: str,
    phase: str,
    progress: Any,
    when: int | None,
    channel: str,
    status_text: str = "",
    live_update: bool = True,
    group: str | None = None,
    visuals: NotifyVisuals | None = None,
    links: NotifyLinks | None = None,
    actions: list[dict[str, Any]] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """A live-card push.

    ``title`` must be the printer's name and identical on every push of a job:
    it is required on Android and cannot be changed once the activity has
    started, so anything job-specific belongs in ``message``.

    ``live_update=False`` degrades to a plain tagged notification, which still
    replaces itself in place and still shows an Android progress bar. That is
    the path taken once iOS has expired the activity.
    """
    paused = phase == PHASE_PAUSED
    color = NOTIFY_COLOR_PAUSED if paused else NOTIFY_COLOR_PRINTING

    data: dict[str, Any] = {
        "tag": sanitize_tag(tag),
        "notification_icon": "mdi:pause-circle" if paused else "mdi:printer-3d-nozzle",
        "channel": channel,
        "importance": "low",
        # Progress pushes only. Terminal pushes reuse this tag, and alert_once
        # there would update the card silently -- the "finished" ping would
        # never sound. build_event_payload deliberately omits it.
        "alert_once": True,
        # Android: survives a swipe. `sticky` alone only survives a *tap*, which
        # is the distinction that made the card look dismissable. Both need a
        # tag, which is set above. The Dismiss action is the deliberate way out;
        # without it this would be a notification the user cannot get rid of.
        "persistent": True,
        "sticky": True,
    }
    if refresh:
        # iOS alerts on every push unless told otherwise, so a 5-minute refresh
        # cadence buzzes the phone for the whole print. `alert_once` above is
        # Android-only and does nothing here, so these are its iOS counterpart
        # rather than a duplicate. Deliberately *not* set on the push that
        # starts the card: that one is the "your print is now on the Lock
        # Screen" cue and should be felt once.
        data["silent"] = True
        data["push"] = {"interruption-level": "passive"}
    else:
        # Tells iOS to begin a Live Activity rather than update one.
        data["activity"] = "start"

    pct = _clamp_progress(progress)
    if pct is None:
        data["progress_indeterminate"] = True
    else:
        data["progress"] = pct
        data["progress_max"] = 100

    if live_update:
        data["live_update"] = True
        data["progress_bar_direction"] = "increasing"
        data["progress_bar_color"] = color
        data["notification_icon_color"] = color
        if when is not None and not paused:
            # Ticks on-device, so the countdown costs no further pushes.
            data["chronometer"] = True
            data["when"] = when
        else:
            # Stopped explicitly rather than by omitting the key: whether
            # dropping it clears a chronometer already running on the phone is
            # undocumented, and both cases here exist precisely to get rid of
            # one. A timer counting down through a pause is bad; a timer frozen
            # at 0:00 because the estimate ran out is the whole reason the
            # overrun push exists, and omitting the key could leave it there.
            data["chronometer"] = False
            if status_text:
                data["critical_text"] = status_text

    # No snapshot on live pushes: Android re-downloads a big picture every time
    # and an iOS Live Activity has no image slot, so it would be pure waste on
    # the hottest path.
    if visuals is not None and visuals.preview_url:
        data["icon_url"] = visuals.preview_url

    _apply_common(data, group=group, links=links, actions=actions)
    return {"title": title, "message": message, "data": data}


def build_event_payload(
    *,
    tag: str,
    title: str,
    message: str,
    kind: str,
    channel: str,
    progress: Any = 100,
    group: str | None = None,
    visuals: NotifyVisuals | None = None,
    links: NotifyLinks | None = None,
    ends_activity: bool = False,
) -> dict[str, Any]:
    """A job-lifecycle banner: completed, stopped, or finishing soon.

    Carries no ``live_update`` (it is not an activity) and, crucially, no
    ``alert_once``: a terminal banner posted on the live card's own tag would
    otherwise replace it *silently* and the "print finished" ping would never
    sound.

    ``ends_activity`` is for the terminal ones, which are posted on the live
    tag so they replace the card in place. It adds ``activity: "end"`` to close
    the iOS Live Activity, and it also clears the keys that made the card
    undismissable -- otherwise the print would be over and the user would be
    left with a notification they cannot swipe away.

    No preceding ``clear_notification`` is sent on that path: dismissing and
    then re-posting the same tag makes the card visibly flicker, and replacing
    it by tag identity achieves the same end state in one push.
    """
    icon, color = _EVENT_STYLE.get(kind, _EVENT_STYLE[EVENT_COMPLETED])
    data: dict[str, Any] = {
        "tag": sanitize_tag(tag),
        "notification_icon": icon,
        "notification_icon_color": color,
        "color": color,
        "channel": channel,
        "importance": "high",
        "push": {"interruption-level": "time-sensitive"},
    }
    if ends_activity:
        data["activity"] = "end"
        # Explicitly false rather than omitted: these are being sent to replace
        # a card that set them, and an omitted key does not undo one already
        # applied to a live notification.
        data["persistent"] = False
        data["sticky"] = False

    pct = _clamp_progress(progress)
    if pct is not None:
        data["progress"] = pct
        data["progress_max"] = 100

    if visuals is not None:
        if visuals.preview_url:
            data["icon_url"] = visuals.preview_url
        if visuals.snapshot_url:
            # What the bed actually looks like, which is the whole point of a
            # completion notification you read from another room.
            data["image"] = visuals.snapshot_url

    _apply_common(data, group=group, links=links, actions=None)
    return {"title": title, "message": message, "data": data}


def build_alert_payload(
    *,
    tag: str,
    title: str,
    message: str,
    kind: str,
    channel: str,
    group: str | None = None,
    visuals: NotifyVisuals | None = None,
    links: NotifyLinks | None = None,
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An error or filament-runout alert.

    Uses its own tag so a lifecycle update cannot overwrite it, and no
    ``alert_once`` so it always breaks through.
    """
    runout = kind == ALERT_RUNOUT
    data: dict[str, Any] = {
        "tag": sanitize_tag(tag),
        "notification_icon": (
            "mdi:printer-3d-nozzle-alert" if runout else "mdi:alert-circle"
        ),
        "notification_icon_color": NOTIFY_COLOR_ERROR,
        "color": NOTIFY_COLOR_ERROR,
        "channel": channel,
        "importance": "high",
        "push": {"interruption-level": "time-sensitive"},
    }

    if visuals is not None and visuals.snapshot_url:
        data["image"] = visuals.snapshot_url

    _apply_common(data, group=group, links=links, actions=actions)
    return {"title": title, "message": message, "data": data}


def build_clear_payload(tag: str) -> dict[str, Any]:
    """Dismiss a notification, and end a Live Activity, carrying ``tag``.

    ``title`` is omitted rather than sent as ``None``. The message is a sentinel
    the companion app interprets; every other notify platform would render it as
    visible text, which is why delivery hard-gates it on mobile targets.
    """
    return {
        "message": CLEAR_NOTIFICATION_MARKER,
        "data": {"tag": sanitize_tag(tag)},
    }


# --------------------------------------------------------------------------- #
# Live-card buttons
# --------------------------------------------------------------------------- #

ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_STOP = "stop"
# The card is posted with `persistent`, so a swipe will not remove it. This is
# the deliberate way out, and it must always be offered alongside that flag --
# an undismissable notification with no dismiss button is a trap.
ACTION_DISMISS = "dismiss"


def action_ids(entry_key: str) -> dict[str, str]:
    """Action ids for one printer, namespaced so two cannot cross-fire.

    The handler matches these exactly, which is the whole guard: a tap on one
    printer's card must never pause another's print.

    The *whole* key is used, not a prefix. Home Assistant entry ids are ULIDs
    whose leading characters encode the creation time and carry no randomness,
    so two printers added in the same second would have shared a truncated id --
    and a Stop tap would have gone to whichever one matched first.
    """
    suffix = sanitize_tag(entry_key).upper() or "CREALITY"
    return {
        ACTION_PAUSE: f"CREALITY_PAUSE_{suffix}",
        ACTION_RESUME: f"CREALITY_RESUME_{suffix}",
        ACTION_STOP: f"CREALITY_STOP_{suffix}",
        ACTION_DISMISS: f"CREALITY_DISMISS_{suffix}",
    }


def build_actions(
    *,
    paused: bool,
    ids: Mapping[str, str],
    labels: Mapping[str, str],
    controls: bool = True,
) -> list[dict[str, Any]]:
    """Buttons for a live card: the useful one, Stop, and Dismiss.

    ``labels`` supplies the translated button titles, keyed by ACTION_*.

    Stop is marked destructive and authentication-required. A mis-tap on a lock
    screen must not be able to end a fourteen-hour print.

    ``controls=False`` drops Pause/Resume and Stop, for the user who does not
    want to drive the printer from a lock screen. Dismiss survives that: the
    card is sent with ``persistent`` so a swipe cannot remove it, which makes
    this the only way to get rid of it, and a card with no way out is a trap
    rather than a feature. It hides the card without touching the print, which
    is what distinguishes it from Stop.
    """
    buttons: list[dict[str, Any]] = []
    if controls:
        primary_key = ACTION_RESUME if paused else ACTION_PAUSE
        primary_icon = (
            "sfsymbols:play.circle" if paused else "sfsymbols:pause.circle"
        )
        buttons.append(
            {
                "action": ids[primary_key],
                "title": labels.get(primary_key, ""),
                "icon": primary_icon,
            }
        )
        buttons.append(
            {
                "action": ids[ACTION_STOP],
                "title": labels.get(ACTION_STOP, ""),
                "icon": "sfsymbols:stop.circle",
                "destructive": True,
                "authenticationRequired": True,
            }
        )
    buttons.append(
        {
            "action": ids[ACTION_DISMISS],
            "title": labels.get(ACTION_DISMISS, ""),
            "icon": "sfsymbols:xmark.circle",
        }
    )
    return buttons
