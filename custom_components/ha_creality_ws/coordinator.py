"""Coordinator for Creality 3D printers."""
from __future__ import annotations
import logging
import asyncio
import json
import time
from typing import Any, Iterable
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator  # type: ignore[import]
from homeassistant.helpers.aiohttp_client import async_get_clientsession  # type: ignore[import]
from homeassistant.helpers.dispatcher import async_dispatcher_send  # type: ignore[import]
from homeassistant.helpers import entity_registry as er  # type: ignore[import]
from homeassistant.helpers.translation import async_get_translations  # type: ignore[import]
from .ws_client import KClient
from .utils import BUSY_PRINT_STATES, ModelDetection, derive_print_state, safe_float
from .notification_rules import (
    ALERT_ERROR,
    ALERT_RUNOUT,
    EVENT_COMPLETED,
    EVENT_SOON,
    PHASE_PAUSED,
    PHASE_PRINTING,
    PHASE_START,
    LiveCardState,
    LiveSnapshot,
    NotifyLinks,
    NotifyVisuals,
    PushReason,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_STOP,
    action_ids,
    build_actions,
    build_alert_payload,
    format_duration,
    format_filament_length,
    build_clear_payload,
    build_event_payload,
    build_live_payload,
    coerce_targets,
    compute_when,
    display_filename,
    is_mobile_target,
    is_new_job_cycle,
    sanitize_tag,
)
from .const import (
    DOMAIN,
    STALE_AFTER_SECS,
    CLEAR_NOTIFICATION_MARKER,
    CONF_NOTIFY_ACTIONS,
    CONF_NOTIFY_CAMERA_SNAPSHOT,
    CONF_NOTIFY_LIVE,
    CONF_NOTIFY_PREVIEW_IMAGE,
    CONF_NOTIFY_TAP_PATH,
    CONF_NOTIFY_COMPLETED,
    CONF_NOTIFY_ERROR,
    CONF_NOTIFY_MINUTES_TO_END,
    CONF_MINUTES_TO_END_VALUE,
    LATE_DISCOVERY_FIELDS,
    BUS_EVENT_PRINT_ERROR,
    BUS_EVENT_PRINT_FINISHED,
    BUS_EVENT_PRINT_STARTED,
    NOTIFY_BODY_SEPARATOR,
    NOTIFY_CHANNEL_KEY_ALERT,
    NOTIFY_CHANNEL_KEY_DONE,
    NOTIFY_CHANNEL_KEY_LIVE,
    NOTIFY_LIVE_STALE_CLEAR_SECS,
    NOTIFY_PRIME_GRACE_SECS,
    PREVIEW_REASONS_UNUSABLE,
    CONF_POLLING_RATE,
    DEFAULT_POLLING_RATE,
    MR_PORT,
    MR_POLL_INTERVAL,
    MR_POLL_TIMEOUT,
    MR_QUERY_PARAMS,
)

_LOGGER = logging.getLogger(__name__)


class KCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage connection and data for the printer."""
    def __init__(
        self, hass, host: str, power_switch: str | None = None, config_entry_id: str | None = None
    ):
        super().__init__(hass, _LOGGER, name=f"{DOMAIN}@{host}", update_interval=None)
        self.client = KClient(host, self._handle_message)
        self.data: dict[str, Any] = {}
        self._paused_flag = False
        self._last_avail = False
        self._power_switch_entity: str | None = (power_switch or "").strip() or None
        self._pending_pause = False
        self._pending_resume = False
        self._last_power_off: bool = False
        self._config_entry_id: str | None = config_entry_id  # Will be set after entry is created
        
        # Notification & Performance
        # _notify_device is the pre-multi-target option, kept as a derived
        # value for one release so nothing reading it breaks silently.
        self._notify_targets: list[str] = []
        self._notify_device = None
        self._notify_live = False
        self._notify_actions = False
        self._notify_preview_image = True
        self._notify_camera_snapshot = True
        self._notify_tap_path = ""
        self._live_card = LiveCardState()
        # Notification text is translated at runtime; None until loaded.
        self._notify_strings: dict[str, str] | None = None
        # (platform, unique suffix) -> entity id. Populated lazily: the
        # platforms are forwarded after this coordinator exists, so nothing
        # can be resolved here.
        self._entity_id_cache: dict[tuple[str, str], str] = {}
        self._notify_completed = False
        self._notify_error = False
        self._notify_minutes_to_end = False
        self._minutes_to_end_value = 5
        self._polling_rate = DEFAULT_POLLING_RATE
        self._last_update_ts = 0.0
        
        # Notification state tracking
        self._last_print_state = None
        self._last_filename = None
        self._notified_completed = False
        self._notified_minutes_to_end = False
        self._last_error_code = 0
        self._last_mr_poll = 0.0
        # Seconds the current job has been running, as last reported. Only ever
        # goes backwards when the printer starts a new job, which is what tells a
        # reprint of the same file apart from end-of-print progress jitter.
        self._last_job_time: float | None = None

        # Notifications are suppressed until the printer's current state has been
        # captured as a baseline, so a job that already finished before Home
        # Assistant started does not fire a fresh "completed" notification on
        # every restart (issue #112).
        self._notify_primed = False
        self._notify_prime_deadline: float | None = None
        
        # Extended status tracking
        self._notified_filament_runout = False
        self._notified_started = False
        
        # Caches
        self._is_k2_base: bool | None = None

        if self._config_entry_id:
            self._load_options()

        # Only enable power detection if a switch is configured
        if self._power_switch_entity:
            self.client._check_power_status = self.power_is_off
            self._last_power_off = self.power_is_off()
            _LOGGER.debug("Power switch configured: %s (initial state: %s)", 
                         self._power_switch_entity, "OFF" if self._last_power_off else "ON")
        else:
            _LOGGER.debug("No power switch configured; connection will retry continuously")

    def _load_options(self):
        if not self._config_entry_id:
            return
        entry = self.hass.config_entries.async_get_entry(self._config_entry_id)
        if not entry:
            return
            
        options = entry.options
        self._notify_targets = coerce_targets(options)
        self._notify_device = self._notify_targets[0] if self._notify_targets else None
        self._notify_live = bool(options.get(CONF_NOTIFY_LIVE, False))
        self._notify_actions = bool(options.get(CONF_NOTIFY_ACTIONS, False))
        self._notify_preview_image = bool(options.get(CONF_NOTIFY_PREVIEW_IMAGE, True))
        self._notify_camera_snapshot = bool(options.get(CONF_NOTIFY_CAMERA_SNAPSHOT, True))
        self._notify_tap_path = str(options.get(CONF_NOTIFY_TAP_PATH) or "").strip()
        # A rename or a disabled entity invalidates these, and an options
        # change is the one moment we know we are being re-read.
        self._entity_id_cache.clear()
        self._notify_strings = None
        self._notify_completed = options.get(CONF_NOTIFY_COMPLETED, False)
        self._notify_error = options.get(CONF_NOTIFY_ERROR, False)
        self._notify_minutes_to_end = options.get(CONF_NOTIFY_MINUTES_TO_END, False)
        self._minutes_to_end_value = options.get(CONF_MINUTES_TO_END_VALUE, 5)
        self._polling_rate = options.get(CONF_POLLING_RATE, DEFAULT_POLLING_RATE)
        
        # Pass polling rate to client if relevant, or handle here
        _LOGGER.debug(
            "Loaded options: Polling Rate=%ss, Notify targets=%d",
            self._polling_rate,
            len(self._notify_targets),
        )

    def set_power_switch(self, entity_id: str | None) -> None:
        """Accept updates from options; make it thread-safe to notify."""
        old_entity = self._power_switch_entity
        self._power_switch_entity = (entity_id or "").strip() or None
        
        # Enable or disable power detection based on new config
        if self._power_switch_entity and not old_entity:
            # Power switch was just configured
            # pylint: disable=protected-access
            self.client._check_power_status = self.power_is_off
            self._last_power_off = self.power_is_off()
            _LOGGER.info("Power switch enabled: %s", self._power_switch_entity)
        elif not self._power_switch_entity and old_entity:
            # Power switch was just removed
            # pylint: disable=protected-access
            self.client._check_power_status = None
            self._last_power_off = False
            _LOGGER.info("Power switch disabled; connection will retry continuously")
        
        self._notify_listeners_threadsafe()
        
    def power_is_off(self) -> bool:
        """Check if the power switch is off."""
        # If we are actively connected via WebSocket, trust the connection over the switch state.
        # This allows manual "Reconnect" to work even if the switch entity is lagging or wrong.
        if self.client.is_connected:
            return False

        eid = self._power_switch_entity
        if not eid:
            return False
        st = self.hass.states.get(eid)
        if not st:
            _LOGGER.debug("Power switch entity %s not found (assume OFF)", eid)
            return True # FAIL-SAFE: Assume OFF if switch entity isn't ready
        is_off = str(st.state).lower() in ("off", "unavailable", "unknown")
        if is_off:
            _LOGGER.debug("Power switch %s is %s -> skipping connection", eid, st.state)
        return is_off

    async def async_start(self) -> None:
        """Start the WebSocket connection."""
        if self.power_is_off():
            _LOGGER.info("Power switch is OFF; deferring WS connect")
            self._last_power_off = True
            return
        self._last_power_off = False
        await self.client.start()
        
    async def ensure_connected(self) -> bool:
        """Ensure WebSocket connection is active, restart if needed."""
        if self.power_is_off():
            return False
        # pylint: disable=protected-access
        if not self.client._task or self.client._task.done():
            _LOGGER.info("WebSocket connection lost, restarting...")
            await self.client.start()
            return await self.client.wait_first_connect(timeout=10.0)
        return True
        
    async def async_stop(self) -> None:
        """Stop the WebSocket connection."""
        await self.client.stop()
        
    async def wait_first_connect(self, timeout: float = 5.0) -> bool:
        """Wait for the first successful connection."""
        return await self.client.wait_first_connect(timeout=timeout)
    
    async def wait_for_fields(self, fields: Iterable[str], timeout: float = 6.0) -> bool:
        """Wait until all given telemetry fields appear in self.data or timeout.

        Args:
            fields: Iterable of keys expected to be present in telemetry dict.
            timeout: Max seconds to wait.

        Returns:
            True if all fields were observed before timeout, False otherwise.
        """
        try:
            end = self.hass.loop.time() + max(0.0, float(timeout))
            needed = {str(f) for f in fields}
            # Fast path check
            if needed.issubset((self.data or {}).keys()):
                return True
            # Poll lightly; on_message updates self.data frequently when streaming starts
            while self.hass.loop.time() < end:
                if needed.issubset((self.data or {}).keys()):
                    return True
                await asyncio.sleep(0.2)
        except Exception:
            # Never raise from a helper wait; just indicate timeout/False.
            pass
        return False
        
    async def async_handle_power_change(self) -> None:
        """Start/stop WS client when the power switch toggles."""
        # Only handle power changes if a switch is configured
        if not self._power_switch_entity:
            _LOGGER.debug("Power change handler called but no switch configured; ignoring")
            return
        
        now_off = self.power_is_off()
        was_off = getattr(self, "_last_power_off", False)
        
        if now_off and not was_off:
            _LOGGER.info("Power OFF detected; stopping WebSocket client")
            await self.client.stop()
            self._last_power_off = True
        elif not now_off and was_off:
            _LOGGER.info("Power ON detected; starting WebSocket client")
            # Ensure any stale task is stopped first
            # pylint: disable=protected-access
            if self.client._task and not self.client._task.done():
                _LOGGER.debug("Stopping existing task before restart")
                await self.client.stop()
                # Give it a moment to fully stop
                await asyncio.sleep(0.1)
            await self.client.start()
            self._last_power_off = False
        
        self.async_update_listeners()
        
    def _notify_listeners_threadsafe(self) -> None:
        """Always execute listener updates on HA's event loop."""
        # Pass the callable itself (no parens); the loop invokes it safely.
        self.hass.loop.call_soon_threadsafe(self.async_update_listeners)

    def check_stale(self) -> None:
        """Called by periodic timer; may run off the event loop."""
        now_avail = self.available
        if now_avail != getattr(self, "_last_avail", None):
            self._last_avail = now_avail
            self._notify_listeners_threadsafe()

    @property
    def available(self) -> bool:
        return (self.hass.loop.time() - self.client.last_rx_monotonic()) < STALE_AFTER_SECS

    # -------- Pause state management --------
    def mark_paused(self, paused: bool) -> None:
        """Update paused state from telemetry."""
        if self._paused_flag != bool(paused):
            self._paused_flag = bool(paused)
            self.async_update_listeners()

    def paused_flag(self) -> bool:
        return self._paused_flag

    def pending_pause(self) -> bool:
        return bool(self._pending_pause)

    def pending_resume(self) -> bool:
        return bool(self._pending_resume)

    # -------- State helpers --------
    def _is_busy_homing(self) -> bool:
        """Check if printer is homing."""
        return (self.data or {}).get("deviceState") == 7

    def _has_active_job(self) -> bool:
        """Check if a print job is active."""
        d = self.data or {}
        fname = (d.get("printFileName") or "").strip()
        prog = d.get("printProgress", d.get("dProgress"))
        return bool(fname) and prog is not None

    def _is_printing(self) -> bool:
        """Check if printer is actively printing (has job, not paused, not homing)."""
        return self._has_active_job() and not self._paused_flag and not self._is_busy_homing()

    def _recompute_paused_from_telemetry(self) -> None:
        """Update paused state from telemetry data."""
        d = self.data or {}
        st = d.get("state")
        # State 5 is paused; also check explicit pause fields
        telem_paused = (st == 5) or bool(d.get("pause") == 1 or d.get("paused") or d.get("isPaused"))
        self.mark_paused(telem_paused)

    # -------- Queued actions --------
    async def request_pause(self) -> None:
        """Pause now if printable; otherwise queue until printable."""
        if self._is_printing():
            try:
                await self.client.send_set_retry(pause=1)
                _LOGGER.debug("Pause sent immediately")
            except Exception as exc:
                self._pending_pause = True
                _LOGGER.warning("Pause send failed; queued. Error: %s", exc)
        else:
            self._pending_pause = True
            _LOGGER.debug("Pause queued (not in printable state)")

    async def request_resume(self) -> None:
        """Resume now if telemetry shows paused; otherwise queue until paused shows up."""
        if self._paused_flag:
            try:
                await self.client.send_set_retry(pause=0)
                _LOGGER.debug("Resume sent immediately")
            except Exception as exc:
                self._pending_resume = True
                _LOGGER.warning("Resume send failed; queued. Error: %s", exc)
        else:
            self._pending_resume = True
            _LOGGER.debug("Resume queued (not in paused state)")

    async def _flush_pending(self) -> None:
        """Attempt to execute any queued actions when state allows (called on every telemetry frame)."""
        if self._pending_pause and self._is_printing():
            try:
                await self.client.send_set_retry(pause=1)
                self._pending_pause = False
                _LOGGER.debug("Queued pause executed")
            except Exception as exc:
                _LOGGER.warning("Queued pause failed; will retry. Error: %s", exc)

        if self._pending_resume and self._paused_flag:
            try:
                await self.client.send_set_retry(pause=0)
                self._pending_resume = False
                _LOGGER.debug("Queued resume executed")
            except Exception as exc:
                _LOGGER.warning("Queued resume failed; will retry. Error: %s", exc)

    def merge_telemetry(self, payload: dict[str, Any]) -> None:
        """Merge telemetry into self.data, firing the discovery signal once.

        Entities gated on a capability field can only be created once the printer
        has actually reported it, which may be long after the platforms were set
        up. The first appearance of any LATE_DISCOVERY_FIELDS entry fires one
        signal so every platform can re-check.

        Every writer of a gating field has to come through here. Writing straight
        into self.data both skips the signal and consumes the one-shot, because
        the field is then no longer "newly seen" when a later frame carries it --
        which is what made the chamber control unreachable on a K2 Base, whose
        targetBoxTemp only ever arrives via the Moonraker fallback.
        """
        newly_seen = [f for f in LATE_DISCOVERY_FIELDS if f not in self.data and f in payload]

        self.data.update(payload)

        if newly_seen:
            _LOGGER.info(
                "Telemetry reported %s for the first time; triggering dynamic discovery",
                ", ".join(newly_seen),
            )
            if "boxsInfo" in newly_seen:
                _LOGGER.debug("CFS Raw Data: %s", json.dumps(payload.get("boxsInfo"), default=str))
            async_dispatcher_send(self.hass, f"{DOMAIN}_new_entities_{self._config_entry_id}")

    async def _handle_message(self, payload: dict[str, Any]) -> None:
        """Handle incoming WebSocket telemetry data."""
        # Suppress broken targetBoxTemp:0 from K2 Base port 9999.
        # Only decide once a frame actually identifies the printer: the board code
        # lives in model/modelVersion, which arrive across several frames (see the
        # wait_for_fields call in async_setup_entry). Latching False off a first
        # frame that carried neither left a real K2 Base without the suppression
        # and without the Moonraker poll -- the only source of its targetBoxTemp --
        # so the chamber target snapped back to 0 after every set.
        if self._is_k2_base is None and (
            payload.get("model") or payload.get("modelVersion")
        ):
            self._is_k2_base = ModelDetection(payload).is_k2_base
             
        if (payload.get("targetBoxTemp") == 0) and self._is_k2_base:
            payload.pop("targetBoxTemp")

        self.merge_telemetry(payload)

        self._recompute_paused_from_telemetry()
        
        # Try queued actions if state allows
        try:
            await self._flush_pending()
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("flush_pending failed")

        # --- Notifications ---
        await self._check_notifications(payload)

        # --- Moonraker Fallback (K2 Base) ---
        if self._is_k2_base:
            now = self.hass.loop.time()
            if (now - getattr(self, "_last_mr_poll", 0) > MR_POLL_INTERVAL):
                self._last_mr_poll = now
                self.hass.async_create_task(self._poll_moonraker_extras())
        
        # --- Conditional Throttling (printing only) ---
        # Always update immediately when NOT printing; throttle entity updates only when printing
        now = self.hass.loop.time()
        if self._polling_rate > 0 and self._is_printing():
            if (now - self._last_update_ts) < self._polling_rate:
                return  # Skip listener update to reduce CPU usage while printing
        
        self._last_update_ts = now
        self.async_update_listeners()

    @staticmethod
    def _print_seconds_left(d: dict[str, Any]) -> float | None:
        """Seconds remaining in the current print, or None.

        The printer streams this as `printLeftTime`; `printTimeLeft` is only kept
        as a fallback because the notification code used to look for that name
        exclusively, which meant the minutes-to-end notification never fired.
        """
        for key in ("printLeftTime", "printTimeLeft"):
            value = safe_float(d.get(key))
            if value is not None:
                return value
        return None

    def _prime_notification_state(self, d: dict[str, Any]) -> None:
        """Record the printer's state at startup without notifying about it.

        The printer keeps reporting the last job's file name and 100% progress
        long after it finished, so a freshly started coordinator would treat that
        stale state as a brand new completion (issue #112). Marking the
        already-true conditions as "already notified" makes the first real
        transition the first notification.
        """
        self._notify_primed = True
        self._last_filename = d.get("printFileName")

        # Same explicit None check as _check_notifications: `or` would baseline a
        # genuine 0% frame off a stale dProgress of 100 and mark the job already
        # complete. The re-arm clears that a frame later, but the two paths must
        # not disagree about the same telemetry.
        progress = d.get("printProgress")
        if progress is None:
            progress = d.get("dProgress")
        try:
            prog_val = int(progress) if progress is not None else 0
        except (ValueError, TypeError):
            prog_val = 0
        self._notified_completed = prog_val >= 100

        try:
            self._last_error_code = int((d.get("err") or {}).get("errcode", 0))
        except (AttributeError, ValueError, TypeError):
            self._last_error_code = 0

        try:
            self._notified_filament_runout = int(d.get("materialStatus") or 0) == 1
        except (ValueError, TypeError):
            self._notified_filament_runout = False

        left_s = self._print_seconds_left(d)
        self._notified_minutes_to_end = bool(
            left_s is not None and 0 < (left_s / 60.0) <= self._minutes_to_end_value
        )

        self._last_job_time = safe_float(d.get("printJobTime"))

        # Adopting a job that was already running is not the same event as one
        # beginning, so the started latch is closed rather than armed.
        self._notified_started = True

        # The card is baselined too, or a restart would push a live activity for
        # a print that finished last week -- issue #112 reincarnated as a push.
        # Milestone comes off the progress we can actually see so the first real
        # push is a start, not a redundant milestone.
        self._live_card.reset_for_new_job(progress=prog_val)

        _LOGGER.debug(
            "Notification baseline captured: file=%s progress=%s completed=%s "
            "err=%s runout=%s near_end=%s",
            self._last_filename,
            prog_val,
            self._notified_completed,
            self._last_error_code,
            self._notified_filament_runout,
            self._notified_minutes_to_end,
        )

    async def _check_notifications(self, _payload: dict[str, Any]) -> None:
        """Check logic for sending notifications."""
        d = self.data or {}
        fname = d.get("printFileName")
        # Explicit None check, matching derive_print_state: `or` would treat a
        # genuine 0% as missing and fall back to dProgress, which still holds the
        # finished job's 100 on the first frame of a reprint. The completion flag
        # would then never re-arm and the next finish would go unnotified.
        progress = d.get("printProgress")
        if progress is None:
            progress = d.get("dProgress")

        # Baseline the current state before anything can be notified. Telemetry
        # arrives incrementally, so wait for a frame that carries both the file
        # name and the progress, but never longer than the grace window (an idle
        # printer may report neither).
        if not self._notify_primed:
            now = self.hass.loop.time()
            if self._notify_prime_deadline is None:
                self._notify_prime_deadline = now + NOTIFY_PRIME_GRACE_SECS
            if (fname and progress is not None) or now >= self._notify_prime_deadline:
                self._prime_notification_state(d)
            return

        # Not an early return any more. The bus events below are the supported
        # way to build your own notification text, in your own language, and they
        # have to work for someone who configured no notify target at all. Only
        # *delivery* is gated. Keeping the state tracking unconditional also
        # fixes a latent bug: `_last_job_time` used to stop advancing without a
        # target, so enabling notifications mid-print saw a phantom restart.
        deliver = bool(self._notify_targets)
        if deliver:
            await self._async_load_notify_strings()
            # Without text there is nothing to send. The bus events below still
            # fire: they carry structured data, not prose.
            deliver = bool(self._notify_strings)

        try:
            prog_val = int(progress) if progress is not None else 0
        except (ValueError, TypeError):
            prog_val = 0

        # `printJobTime` counts up for as long as one job runs, so it only ever
        # goes backwards when the printer starts another one. That is the one
        # unambiguous new-cycle signal in this telemetry: the file name stays the
        # same on a reprint, and progress dips below 100 for reasons that have
        # nothing to do with a new job (see the completion re-arm below).
        job_time = safe_float(d.get("printJobTime"))
        job_restarted = (
            job_time is not None
            and self._last_job_time is not None
            and job_time < self._last_job_time
        )
        if job_time is not None:
            self._last_job_time = job_time

        # Check if we started a new print (filename changed)
        # Store last filename in instance to compare
        if getattr(self, "_last_filename", None) != fname:
            self._last_filename = fname
            self._notified_minutes_to_end = False
            self._notified_filament_runout = False
            self._last_error_code = 0
            # Baseline completion off the progress we can actually see, exactly
            # as _prime_notification_state does. Telemetry arrives incrementally,
            # so the frame that first carries the new file name usually still
            # carries the *previous* job's 100 -- arming here unconditionally
            # fired "completed" the instant a new job started, and then swallowed
            # the real completion.
            self._notified_completed = prog_val >= 100
            self._reset_for_new_job(prog_val)

        if not fname:
            return

        # The K1C reports a full path; the sensor attributes deliberately
        # keep publishing it raw, but a notification body should not.
        job = display_filename(fname) or fname

        # 0) Started. Deliberately not tied to the frame the file name changes
        # on: telemetry arrives incrementally, so that frame usually still holds
        # the previous job's 100%, and firing there would announce a print
        # beginning at 100%. Waiting for real progress means the event lands on
        # the first frame that actually describes the new job.
        if not self._notified_started and prog_val < 100:
            self._notified_started = True
            self._fire_print_event(BUS_EVENT_PRINT_STARTED, d, job)

        # 1) Completion

        # Progress falling back below 100% means a new job cycle started, even if
        # it reprints the same file (in which case the file name never changes and
        # the reset above never fires). Without this the completion notification
        # only ever arrives once per file name.
        #
        # It has to be a real drop, though. The printer rounds progress up to 100
        # a second before the job ends (with minutes still on the clock), reports
        # 99 once more, and only then finishes -- so re-arming on any dip below
        # 100 sent the completion notification twice for every print, once per
        # crossing. A drop that stays within the jitter band is only a new cycle
        # if the job clock restarted too.
        if is_new_job_cycle(prog_val, job_restarted, self._notified_completed):
            _LOGGER.debug("Progress back at %s%%; re-arming completion notification", prog_val)
            self._notified_completed = False
            # Same predicate, so the card and the completion notification can
            # never disagree about where one job ends and the next begins.
            self._reset_for_new_job(prog_val)

        # Before the one-shot events below: on the frame a print finishes this
        # ends the activity, and the completion banner then arrives on its own
        # tag rather than landing behind a card that is still counting down.
        if deliver:
            self._update_live_card(self._live_snapshot())

        # Detection below is deliberately *not* wrapped in the per-event
        # option checks -- only the sends are. Three reasons:
        #   * `_notified_completed` is the sole input to is_new_job_cycle(), so
        #     latching it only when completion notifications were enabled meant
        #     a same-file reprint never re-armed: the live card stayed retired
        #     for good and no further start was ever detected.
        #   * `_last_error_code` and the runout latch likewise stopped advancing,
        #     so enabling an option mid-print replayed whatever was already true.
        #   * The bus events are documented to fire regardless of notification
        #     settings; they exist precisely for the user who writes their own
        #     text instead of using ours.
        if prog_val >= 100 and not self._notified_completed:
            if self._notify_completed and deliver:
                await self._notify_event(
                    self._completion_message(d, job), kind=EVENT_COMPLETED
                )
            self._fire_print_event(BUS_EVENT_PRINT_FINISHED, d, job)
            self._notified_completed = True

        # 2) Error
        code = self._error_code(d)
        if code != 0 and code != self._last_error_code:
            key = (d.get("err") or {}).get("key", 0)
            if self._notify_error and deliver:
                await self._notify_event(
                    self._t("error", code=code, key=key, filename=job),
                    kind=ALERT_ERROR,
                )
            self._fire_print_event(BUS_EVENT_PRINT_ERROR, d, job)
        self._last_error_code = code

        # 3) Filament runout (materialStatus == 1). Shares the error toggle,
        # being an error-like state the user wants to hear about together.
        try:
            mat_status = d.get("materialStatus")
            is_runout = mat_status is not None and int(mat_status) == 1
        except (ValueError, TypeError):
            is_runout = False

        if is_runout and not self._notified_filament_runout:
            if self._notify_error and deliver:
                await self._notify_event(
                    self._t("filament_runout", filename=job), kind=ALERT_RUNOUT
                )
            self._notified_filament_runout = True
        elif not is_runout and self._notified_filament_runout:
            # Reset once the user has reloaded.
            self._notified_filament_runout = False

        # 4) Minutes to end
        # Nothing to compare against until the printer reports a remaining time.
        left_s = self._print_seconds_left(d)
        if left_s is not None:
            left_min = left_s / 60.0
            target_min = self._minutes_to_end_value

            if 0 < left_min <= target_min and not self._notified_minutes_to_end:
                if self._notify_minutes_to_end and deliver:
                    await self._notify_event(
                        self._t(
                            "finishing_soon", filename=job, minutes=int(left_min)
                        ),
                        kind=EVENT_SOON,
                    )
                self._notified_minutes_to_end = True
            elif left_min > (target_min + 2):
                # The estimate jumped back up by more than the slack; re-arm.
                self._notified_minutes_to_end = False

    def _reset_for_new_job(self, prog_val: int) -> None:
        """Re-arm the per-job notification latches for a new job.

        The order matters: any card still on a phone is dismissed *before* the
        latch that tracks it is cleared. Resetting first would drop
        `card_active`, leaving nothing that knows a Live Activity is still up --
        and `notifier_tick` would not clean it up either, because it only acts on
        a card it believes exists.
        """
        if self._live_card.card_active:
            self._clear_live_card()
        self._live_card.reset_for_new_job(progress=prog_val)
        self._notified_started = False

    def _resolve_entity_id(self, platform: str, unique_suffix: str) -> str | None:
        """Entity id of one of our own entities, via the unique id we minted.

        Not derivable from the host: `KEntity` sets `_attr_has_entity_name`, and
        users rename things, so the object id is whatever the registry says.
        Cached, but only once it resolves -- a miss stays a miss so a lookup made
        before the platform finished setting up does not stick forever.

        Registry hits include *disabled* entities, which would 404 on the proxy
        URLs, hence the state check at the call sites.
        """
        key = (platform, unique_suffix)
        cached = self._entity_id_cache.get(key)
        if cached:
            return cached
        try:
            registry = er.async_get(self.hass)
            entity_id = registry.async_get_entity_id(
                platform, DOMAIN, f"{self.client._host}-{unique_suffix}"
            )
        except Exception:  # pylint: disable=broad-except
            return None
        if entity_id:
            self._entity_id_cache[key] = entity_id
        return entity_id

    def _live_entity_state(self, platform: str, unique_suffix: str):
        """The live state of one of our entities, or None if it cannot serve."""
        entity_id = self._resolve_entity_id(platform, unique_suffix)
        if not entity_id:
            return None, None
        state = self.hass.states.get(entity_id)
        if state is None:
            # Registered but disabled or not yet added; the proxy would 404.
            return None, None
        return entity_id, state

    def _notify_media(self, *, include_snapshot: bool) -> NotifyVisuals:
        """Media URLs for a notification, gated on being real pictures.

        Both are relative `/api/...` paths; the companion app fetches them with
        its own credentials, so no token or signed path is involved. Neither is
        attached speculatively: the image entity serves a 1x1 transparent PNG
        when it has no preview, and a camera that cannot snapshot serves a 1x1
        white JPEG. Android renders either as an empty grey box.

        `preview_reason` is only set once something has actually asked the image
        entity for bytes, and Home Assistant does not do that on its own -- so
        an unset value means "not tried yet", not "no good". Excluding only the
        two known-bad reasons lets the first notification of a print carry the
        preview (the companion app's own fetch is what populates it) instead of
        waiting for a dashboard to render the entity first.
        """
        preview_url = None
        if self._notify_preview_image:
            entity_id, state = self._live_entity_state("image", "current_print_preview")
            reason = (getattr(state, "attributes", None) or {}).get("preview_reason")
            if state is not None and reason not in PREVIEW_REASONS_UNUSABLE:
                preview_url = f"/api/image_proxy/{entity_id}"

        snapshot_url = None
        if include_snapshot and self._notify_camera_snapshot:
            entity_id, state = self._live_entity_state("camera", "camera")
            if state is not None and (
                getattr(state, "attributes", None) or {}
            ).get("snapshot_supported"):
                snapshot_url = f"/api/camera_proxy/{entity_id}"

        return NotifyVisuals(preview_url=preview_url, snapshot_url=snapshot_url)

    def _notify_links(self) -> NotifyLinks:
        """Tap targets, sent as both dialects at once.

        A configured dashboard path wins on both platforms. Failing that Android
        gets `entityId:` for the camera, which opens the more-info dialog and its
        live feed with no configuration at all -- but iOS does not support that
        form, so it gets nothing rather than a broken link.
        """
        path = (self._notify_tap_path or "").strip()
        if path.startswith("/"):
            return NotifyLinks(click_action=path, tap_path=path)

        camera_id = self._resolve_entity_id("camera", "camera")
        if camera_id:
            return NotifyLinks(click_action=f"entityId:{camera_id}")

        # No camera configured for this printer: the preview at least shows what
        # is being printed.
        preview_id = self._resolve_entity_id("image", "current_print_preview")
        if preview_id:
            return NotifyLinks(click_action=f"entityId:{preview_id}")

        return NotifyLinks()

    def _live_snapshot(self) -> LiveSnapshot:
        """The subset of telemetry the live card reacts to."""
        d = self.data or {}
        print_state = derive_print_state(
            d,
            power_off=self.power_is_off(),
            available=self.available,
            paused_flag=self._paused_flag,
        )
        activity_state = self._activity_state(d, print_state)
        return LiveSnapshot(
            activity_state=activity_state,
            job_active=activity_state in BUSY_PRINT_STATES,
            progress=self._notify_progress(),
            filename=display_filename(d.get("printFileName")),
            layer=self._int_or_none(d.get("layer")),
            total_layers=self._int_or_none(d.get("TotalLayer")),
            seconds_left=self._print_seconds_left(d),
        )

    def _activity_state(self, d: dict[str, Any], print_state: str) -> str:
        """`print_state` with a *stale* error collapsed back to what the job is doing.

        `derive_print_state` reports "error" for any non-zero `err.errcode`,
        including a code the printer never clears -- which would pin the live
        card to "error" for an entire print. Re-deriving with the error blanked
        keeps that single source of truth rather than re-implementing the
        mapping here. The one-shot error notification is unaffected: it keys on
        the code *changing*, not on this.
        """
        if print_state != "error":
            return print_state
        return derive_print_state(
            dict(d, err={}),
            power_off=self.power_is_off(),
            available=self.available,
            paused_flag=self._paused_flag,
        )

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        number = safe_float(value)
        return None if number is None else int(number)

    def _live_message(self, snap: LiveSnapshot, *, include_eta: bool) -> str:
        """The card body. Every segment is dropped when its source is unknown.

        The remaining time is only spelled out when there is no chronometer to
        show it -- otherwise the on-device timer is both live and more accurate
        than a number frozen at the last push.
        """
        parts: list[str] = []
        if snap.filename:
            parts.append(snap.filename)
        if snap.activity_state == "paused":
            parts.append(
                self._t("body_paused", progress=snap.progress)
                if snap.progress is not None
                else self._t("body_paused_unknown")
            )
        elif snap.progress is not None:
            parts.append(self._t("body_progress", progress=snap.progress))
        if snap.layer is not None and snap.total_layers:
            parts.append(
                self._t(
                    "body_layer", layer=snap.layer, total_layers=snap.total_layers
                )
            )
        if include_eta:
            remaining = format_duration(snap.seconds_left, self._notify_strings or {})
            if remaining:
                parts.append(self._t("body_time_left", duration=remaining))
        return NOTIFY_BODY_SEPARATOR.join(p for p in parts if p) or self._t(
            "body_fallback"
        )

    def _update_live_card(self, snap: LiveSnapshot) -> None:
        """Start, update or end the live print card.

        Runs on every WebSocket frame, so the cheap checks come first and no
        payload is built on a frame that decides to stay quiet.
        """
        if not self._notify_live:
            return

        if not snap.job_active:
            if self._live_card.card_active:
                # Only reaching 100% retires the card. The jitter this latch
                # defends against happens there and nowhere else, whereas a
                # stopped print -- or one transient "idle"/"off" frame -- has to
                # leave the card able to come back. A stopped job never sets
                # `_notified_completed`, so nothing else would ever re-arm it and
                # the same file could never show a card again.
                finished = snap.progress is not None and snap.progress >= 100
                self._clear_live_card(finished=finished)
            return

        now_mono = self.hass.loop.time()
        now_epoch = time.time()
        reason = self._live_card.decide(
            snap=snap, now_mono=now_mono, now_epoch=now_epoch
        )
        if reason is None:
            return

        # Past Apple's eight-hour ceiling the activity is gone, so the live-only
        # keys are dropped and the card degrades to a plain tagged notification
        # that still replaces itself in place.
        expired = self._live_card.is_expired(now_epoch)
        paused = snap.activity_state == "paused"
        when = None if paused else compute_when(now_epoch, snap.seconds_left)
        phase = (
            PHASE_PAUSED
            if paused
            else PHASE_START
            if reason is PushReason.START
            else PHASE_PRINTING
        )
        tag_base = self._notify_tag_base()

        payload = build_live_payload(
            tag=f"{tag_base}_live",
            title=self._notify_title(),
            message=self._live_message(snap, include_eta=expired or when is None),
            phase=phase,
            progress=snap.progress,
            when=when,
            channel=self._t(NOTIFY_CHANNEL_KEY_LIVE),
            status_text=self._live_status_text(phase, paused),
            live_update=not expired,
            group=tag_base,
            # No snapshot: Android re-downloads a big picture on every push and
            # an iOS Live Activity has no image slot for it.
            visuals=self._notify_media(include_snapshot=False),
            links=self._notify_links(),
            actions=self._notify_card_actions(snap),
        )
        self._notify_dispatch(
            payload, kind=f"live:{reason.value}", mobile_only=True
        )
        self._live_card.record_push(
            reason=reason,
            snap=snap,
            now_mono=now_mono,
            now_epoch=now_epoch,
            when=when,
        )

    def _live_status_text(self, phase: str, paused: bool) -> str:
        """The short label shown when there is no chronometer to show instead."""
        if paused:
            return self._t("status_paused")
        if phase == PHASE_START:
            return self._t("status_starting")
        return self._t("status_finishing")

    def _clear_live_card(self, *, finished: bool = False) -> None:
        """End the live activity.

        A same-tag banner does not end one, so this sentinel is the only way.
        Deliberately not called on unload: `options_update_listener` reloads the
        entry on *any* options change, and clearing there would dismiss and
        re-create the card every time the user toggles something unrelated --
        visibly flickering, and burning an iOS push-to-start slot each time.
        """
        self._notify_dispatch(
            build_clear_payload(f"{self._notify_tag_base()}_live"),
            kind="live:clear",
            mobile_only=True,
        )
        if finished:
            self._live_card.finish()
        else:
            self._live_card.clear()

    def _fire_print_event(self, event: str, d: dict[str, Any], job: str) -> None:
        """Fire a language-neutral bus event describing the job.

        Deliberately independent of whether any notify target is configured:
        this is what a multi-language household uses to write its own
        notification text in an automation, since a body composed here can only
        ever follow the server's language.
        """
        try:
            self.hass.bus.async_fire(
                event,
                {
                    "entry_id": self._config_entry_id,
                    "host": self.client._host,
                    "device_name": self._notify_title(),
                    "filename": job,
                    "progress": self._notify_progress(),
                    "layer": self._int_or_none(d.get("layer")),
                    "total_layers": self._int_or_none(d.get("TotalLayer")),
                    "left_seconds": self._print_seconds_left(d),
                    "err_code": self._error_code(d),
                },
            )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Failed to fire %s", event)

    @staticmethod
    def _error_code(d: dict[str, Any]) -> int:
        try:
            return int((d.get("err") or {}).get("errcode", 0))
        except (AttributeError, ValueError, TypeError):
            return 0

    def _notify_action_ids(self) -> dict[str, str]:
        """Action ids for this printer, namespaced by config entry."""
        return action_ids(self._config_entry_id or self.client._host)

    def _notify_card_actions(self, snap: LiveSnapshot) -> list[dict[str, Any]] | None:
        """Buttons for the live card, or None when the user has not asked for them."""
        if not self._notify_actions:
            return None
        return build_actions(
            paused=snap.activity_state == "paused",
            ids=self._notify_action_ids(),
            labels={
                ACTION_PAUSE: self._t("action_pause"),
                ACTION_RESUME: self._t("action_resume"),
                ACTION_STOP: self._t("action_stop"),
            },
        )

    async def async_handle_notification_action(self, action: str) -> bool:
        """Run a live-card button. Returns whether the action was ours.

        The exact id match is the only guard needed against two printers
        cross-firing, because the ids are namespaced per config entry.

        Nothing is done to the card afterwards: the next telemetry frame moves
        the state and re-renders it, matching how the pause/resume buttons
        already behave rather than guessing optimistically.
        """
        ids = self._notify_action_ids()
        if action == ids[ACTION_PAUSE]:
            await self.request_pause()
        elif action == ids[ACTION_RESUME]:
            await self.request_resume()
        elif action == ids[ACTION_STOP]:
            await self.async_stop_print()
        else:
            return False
        _LOGGER.info("Handled live-card action %s", action)
        return True

    async def async_stop_print(self) -> None:
        """Stop the current print.

        Shared with the stop button entity so the notification action and the
        dashboard cannot drift apart. The paused flag is deliberately not forced
        here; telemetry reflects idle soon enough.
        """
        if not await self.ensure_connected():
            _LOGGER.warning("Cannot execute stop command: printer not connected")
            return
        await self.client.send_set_retry(stop=1)

    def notifier_tick(self) -> None:
        """Clear a live card the printer has stopped reporting on.

        Driven by the existing periodic check in `__init__.py` rather than a new
        timer. Every other transition is caused by an incoming frame, so without
        this a card would sit on the phone forever showing a print that silently
        stopped -- a pulled plug, a crashed printer, a dead network.
        """
        if not self._live_card.card_active:
            return
        idle = self.hass.loop.time() - self.client.last_rx_monotonic()
        if idle < NOTIFY_LIVE_STALE_CLEAR_SECS:
            return
        _LOGGER.info(
            "No printer telemetry for %.0fs; clearing the live print card", idle
        )
        self._clear_live_card()

    async def _async_load_notify_strings(self) -> None:
        """Load the translated notification strings, once per entry load.

        Notification bodies are composed here rather than rendered in the
        frontend, and an integration is never told *which user* a notification
        is for -- so the only language available is the server's. That is a
        documented limitation, not a reason to hardcode English: the strings
        still live in strings.json, and the bus events exist for anyone who
        needs per-user text. Home Assistant caches these, so the call is cheap
        after the first.
        """
        if self._notify_strings is not None:
            return
        language = getattr(getattr(self.hass, "config", None), "language", None) or "en"
        # "common" rather than a category of our own: hassfest validates
        # strings.json against a fixed set of top-level keys and rejects
        # anything else, and `common` is the only one shaped as a flat
        # slug -> string bag. It is namespaced per integration either way.
        prefix = f"component.{DOMAIN}.common."
        try:
            raw = await async_get_translations(self.hass, language, "common", {DOMAIN})
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Could not load notification strings")
            self._notify_strings = {}
            return

        self._notify_strings = {
            key[len(prefix) :]: value
            for key, value in (raw or {}).items()
            if key.startswith(prefix)
        }
        if not self._notify_strings:
            _LOGGER.error(
                "No notification strings available for language %s; "
                "notifications are disabled until this is fixed",
                language,
            )

    def _t(self, key: str, /, **values: Any) -> str:
        """Resolve one translated notification string.

        `key` is positional-only: the error template substitutes a placeholder
        literally named `key` (the printer's error key), which would otherwise
        collide with this parameter.

        A translation whose placeholders do not match returns empty rather than
        raising: callers drop empty segments, so one bad string in one language
        costs a line of text instead of the whole notification.
        """
        template = (self._notify_strings or {}).get(key)
        if not template:
            _LOGGER.debug("No notification string for %r", key)
            return ""
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            _LOGGER.warning(
                "Notification string %r does not match its placeholders; "
                "check that translation",
                key,
            )
            return ""

    def _notify_title(self) -> str:
        """Printer name, used as the notification title.

        Has to be stable for the whole life of a live card: Android requires a
        title and will not update one after the activity has started, so
        anything job-specific belongs in the body instead.

        Falls back to the host rather than a generic string. Telemetry arrives
        incrementally and priming only waits for a file name and progress, so a
        card can start before `hostname` has been reported -- and because the
        title is then frozen, a generic fallback would leave the whole job
        labelled with the generic fallback and no way to tell two machines apart.
        """
        d = self.data or {}
        return str(
            d.get("hostname")
            or d.get("model")
            or self.client._host
            or self._t("title_fallback")
        )

    def notify_options_changed(self, options: Any) -> None:
        """Dismiss a live card that the incoming options are about to disable.

        Any options change reloads the entry, and the rebuilt `LiveCardState`
        starts empty -- so this is the last moment anything knows an activity is
        still up on a phone. Only fired when the card is actually being switched
        off (or its last target removed): otherwise the reload re-adopts the
        card on the next frame, and dismissing here would make it visibly
        flicker and spend an iOS push-to-start slot for nothing.
        """
        if not self._live_card.card_active:
            return
        try:
            still_wanted = bool(options.get(CONF_NOTIFY_LIVE, False)) and bool(
                coerce_targets(options)
            )
        except Exception:  # pylint: disable=broad-except
            return
        if still_wanted:
            return
        _LOGGER.info("Live print card switched off; dismissing it")
        self._clear_live_card()

    def _notify_tag_base(self) -> str:
        """Stable tag prefix for this printer.

        Derived from the config entry id and never from the host: the host is an
        IP address and dots are illegal in a tag. It also has to survive a Home
        Assistant restart, because that is what lets an existing card be
        replaced rather than duplicated.
        """
        return sanitize_tag(f"{DOMAIN}_{self._config_entry_id or self.client._host}")

    async def _notify_event(self, message: str, *, kind: str) -> None:
        """Build and dispatch one of the one-shot lifecycle notifications.

        The single chokepoint for them, which is what
        `tools/tests/test_notifications.py` monkeypatches so the gating rules --
        the completion re-arm from b2ac2ea and the issue #112 priming window --
        can be driven without any service layer underneath.
        """
        tag_base = self._notify_tag_base()
        title = self._notify_title()
        links = self._notify_links()

        if kind in (ALERT_ERROR, ALERT_RUNOUT):
            payload = build_alert_payload(
                tag=f"{tag_base}_alert",
                title=title,
                message=message,
                kind=kind,
                channel=self._t(NOTIFY_CHANNEL_KEY_ALERT),
                group=tag_base,
                # A picture of the bed is the whole point of a failure alert you
                # read from another room.
                visuals=self._notify_media(include_snapshot=True),
                links=links,
            )
        else:
            payload = build_event_payload(
                tag=f"{tag_base}_event",
                title=title,
                message=message,
                kind=kind,
                channel=self._t(NOTIFY_CHANNEL_KEY_DONE),
                progress=self._notify_progress(),
                group=tag_base,
                visuals=self._notify_media(include_snapshot=True),
                links=links,
            )

        self._notify_dispatch(payload, kind=kind)

    def _completion_message(self, d: dict[str, Any], job: str) -> str:
        """Completion text, with elapsed time and filament used when known.

        Two whole sentences rather than one sentence plus bolted-on fragments,
        so each reads naturally in every language. Falls back to the plain form
        unless *both* numbers are available -- a half-filled detailed sentence
        would read worse than the simple one.
        """
        duration = format_duration(d.get("printJobTime"), self._notify_strings or {})
        filament = format_filament_length(
            d.get("usedMaterialLength"), (self._notify_strings or {}).get("filament_length")
        )
        if duration and filament:
            detailed = self._t(
                "completed_detailed",
                filename=job,
                duration=duration,
                filament=filament,
            )
            if detailed:
                return detailed
        return self._t("completed", filename=job)

    def _notify_progress(self) -> int | None:
        """Progress for a notification body, or None when the printer has not said."""
        d = self.data or {}
        progress = d.get("printProgress")
        if progress is None:
            progress = d.get("dProgress")
        value = safe_float(progress)
        return None if value is None else int(value)

    def _notify_dispatch(
        self,
        payload: dict[str, Any],
        *,
        kind: str = "event",
        mobile_only: bool = False,
    ) -> None:
        """Fan a payload out to every configured target without blocking.

        Deliberately not a coroutine that awaits the sends. `ws_client` awaits
        `_on_message` inline in its receive loop, and a notify call is an HTTPS
        POST to a push relay for mobile_app targets -- awaiting one there stalls
        the loop, lets `last_rx_monotonic()` go stale and flips every entity
        unavailable at STALE_AFTER_SECS. One task per target, never awaited.
        """
        targets = self._notify_targets
        if not targets:
            return

        sent_to = 0
        for target in targets:
            if mobile_only and not is_mobile_target(target):
                # Live-card keys are meaningless to anything but the companion
                # app, so a progress push has nothing to say to these.
                _LOGGER.debug("Skipping %s push for non-mobile target %s", kind, target)
                continue
            sent_to += 1
            self.hass.async_create_task(self._async_deliver_one(target, payload))

        if not sent_to:
            return
        # INFO omits the target list and the body on purpose: both end up in
        # user-submitted logs, and entity ids plus print file names are theirs,
        # not ours. DEBUG carries them for anyone debugging their own setup.
        #
        # Live-card pushes stay at DEBUG: there are ~20 per print, and they would
        # otherwise bury the handful of lines that describe something happening.
        if not kind.startswith("live"):
            _LOGGER.info(
                "Notification dispatched (%s) to %d target(s)", kind, sent_to
            )
        _LOGGER.debug(
            "Notification (%s) targets=%s body=%.200s",
            kind,
            targets,
            payload.get("message", ""),
        )

    async def _async_deliver_one(self, target: str, payload: dict[str, Any]) -> None:
        """Deliver one payload to one target, tolerating a dead target."""
        message = payload.get("message", "")
        title = payload.get("title")
        data = payload.get("data")

        try:
            state = (
                self.hass.states.get(target) if target.startswith("notify.") else None
            )
            if state is not None and getattr(state, "domain", None) == "notify":
                # A notify *entity*. `notify.send_message` is the only service
                # for these and its schema has no `data` field, so an entity
                # target can never carry a tag -- which also means the dismiss
                # sentinel would arrive as visible body text.
                if message == CLEAR_NOTIFICATION_MARKER:
                    _LOGGER.debug(
                        "Not sending the dismiss sentinel to notify entity %s: "
                        "send_message cannot carry a tag",
                        target,
                    )
                    return
                if data:
                    _LOGGER.debug(
                        "Dropping %d unsupported key(s) for notify entity %s: %s",
                        len(data),
                        target,
                        ", ".join(sorted(data)),
                    )
                entity_data: dict[str, Any] = {"entity_id": target, "message": message}
                if title:
                    entity_data["title"] = title
                await self.hass.services.async_call(
                    "notify", "send_message", entity_data
                )
                return

            if "." not in target:
                # The options flow accepts free text, and this used to fail
                # completely silently: no error, no log, no notification.
                _LOGGER.warning(
                    "Ignoring notify target %r: expected the form domain.service",
                    target,
                )
                return

            domain, service = target.split(".", 1)
            if not is_mobile_target(target):
                if message == CLEAR_NOTIFICATION_MARKER:
                    _LOGGER.debug(
                        "Not sending the dismiss sentinel to %s: it would render "
                        "as visible text",
                        target,
                    )
                    return
                # Strict-schema platforms reject unknown keys and fail the whole
                # call, and the relative /api/ media URLs only authenticate from
                # the companion app anyway.
                data = None

            service_data: dict[str, Any] = {"message": message}
            if title:
                service_data["title"] = title
            if data:
                service_data["data"] = data
            await self.hass.services.async_call(domain, service, service_data)
        except Exception:  # pylint: disable=broad-except
            # One unreachable phone must not starve the others.
            _LOGGER.exception("Failed to send notification to %s", target)

    async def _poll_moonraker_extras(self):
        """Poll Moonraker for missing telemetry fields (e.g. chamber target)."""
        # pylint: disable=protected-access
        host = self.client._host
        # Only poll if we have a host and integration is still active
        if not host or self.power_is_off():
            return
            
        url = f"http://{host}:{MR_PORT}/printer/objects/query?{MR_QUERY_PARAMS}"
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, timeout=MR_POLL_TIMEOUT) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    status = res.get("result", {}).get("status", {})
                    fan = status.get("temperature_fan chamber_fan")
                    if fan and "target" in fan:
                        target = fan["target"]
                        # Only update if it's different to avoid unnecessary listener triggers
                        if self.data.get("targetBoxTemp") != target:
                            _LOGGER.debug("Updated targetBoxTemp from Moonraker: %s", target)
                            # Via the helper, not a direct write: on a K2 Base the
                            # WS feed pops targetBoxTemp:0, so this poll is the
                            # only source of the field that gates the chamber
                            # control, and it has to fire discovery itself.
                            self.merge_telemetry({"targetBoxTemp": target})
                            self.async_update_listeners()
        except Exception as e:
            # Moonraker might be disabled or port 7125 blocked; fail silently but log debug
            _LOGGER.debug("Failed to poll Moonraker for extras: %s", e)
