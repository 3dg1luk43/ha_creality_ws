DOMAIN = "ha_creality_ws"

CONF_HOST = "host"
CONF_NAME = "name"
CONF_DISCOVERY_SCAN_CIDR = "scan_cidr"
CONF_POWER_SWITCH = "power_switch"
CONF_POWER_SWITCH_ENABLED = "power_switch_enabled"
CONF_CAMERA_MODE = "camera_mode"
CONF_GO2RTC_URL = "go2rtc_url"
CONF_GO2RTC_PORT = "go2rtc_port"
CONF_CUSTOM_CAMERA_URL = "custom_camera_url"

DEFAULT_NAME = "Creality Printer (WS)"

WS_PORT = 9999
MJPEG_PORT = 8080
HTTP_PORT = 80

WS_URL_TEMPLATE = "ws://{host}:" + str(WS_PORT)
# Subprotocol advertised by the printer's own web UI on the WebSocket handshake.
# Mirroring it (Sec-WebSocket-Protocol: wsslicer) keeps us byte-compatible with
# the official client. Per RFC 6455 the server ignores it if unsupported.
WS_SUBPROTOCOL = "wsslicer"
MJPEG_URL_TEMPLATE = "http://{host}:" + str(MJPEG_PORT) + "/?action=stream"

# WebRTC signaling endpoint (K2 family, and newer K1C firmwares)
WEBRTC_PORT = 8000
WEBRTC_CALL_ROOT_PATH = "/call"
WEBRTC_CALL_PATH = "/call/webrtc_local"
WEBRTC_CALL_ROOT_URL_TEMPLATE = "http://{host}:" + str(WEBRTC_PORT) + WEBRTC_CALL_ROOT_PATH
WEBRTC_URL_TEMPLATE = "http://{host}:" + str(WEBRTC_PORT) + WEBRTC_CALL_PATH

# Camera modes
CAM_MODE_AUTO = "auto"
CAM_MODE_MJPEG = "mjpeg"
CAM_MODE_WEBRTC = "webrtc"
CAM_MODE_WEBRTC_DIRECT = "webrtc_direct"
CAM_MODE_CUSTOM = "custom"

MFR = "Creality"
MODEL = "K"

# ---- Health / reconnect / keepalive ----
STALE_AFTER_SECS = 15
RETRY_MIN_BACKOFF = 1.0
RETRY_MAX_BACKOFF = 300.0
RETRY_BACKOFF_MULTIPLIER = 1.8
HEARTBEAT_SECS = 10.0
PROBE_ON_SILENCE_SECS = 10.0

# go2rtc defaults
DEFAULT_GO2RTC_URL = "localhost"
DEFAULT_GO2RTC_PORT = 11984

# go2rtc RTSP endpoint, used for HA's classic stream pipeline (HLS,
# camera.record, camera.play_stream, casting). HA's own managed go2rtc binary
# listens for RTSP on 127.0.0.1:18554 while its REST API is on 11984 (see
# homeassistant/components/go2rtc/server.py); a stand-alone go2rtc defaults to
# 8554. Users with a non-default RTSP port can override it in the options flow.
CONF_GO2RTC_RTSP_PORT = "go2rtc_rtsp_port"
# Custom-camera URL schemes that go2rtc ingests rather than Home Assistant
# fetching directly. A Custom source using one of these ends up on the same
# go2rtc camera as CAM_MODE_WEBRTC, so it needs the same settings.
GO2RTC_SOURCE_SCHEMES = ("rtsp", "rtmp", "srt")

HA_MANAGED_GO2RTC_RTSP_PORT = 18554
DEFAULT_GO2RTC_RTSP_PORT = 8554

# Telemetry fields that gate entity creation and can only arrive once the printer
# is actually reachable. Platform setup does not wait for the printer (an offline
# printer must not block the config entry), so an entity depending on one of
# these would otherwise never be created until the next restart that happens to
# race the right way. The first appearance of any of them fires a discovery pass.
# targetBoxTemp is here because number.py gates the chamber control on it: a
# printer that reports a chamber target but never a maximum (K2 Base) would
# otherwise never fire a discovery pass, and the control would stay absent until
# a restart happened to race the right way -- the very defect this list exists
# to prevent.
# Must stay in step with every gate that reads these from coord.data:
# number.py promotes chamber *control* on targetBoxTemp/maxBoxTemp, and sensor.py
# promotes the chamber *sensor* on boxTemp/targetBoxTemp/maxBoxTemp. A field that
# gates an entity but does not appear here can never trigger the pass that would
# create it.
LATE_DISCOVERY_FIELDS: tuple[str, ...] = (
    "boxsInfo",
    "boxTemp",
    "maxBoxTemp",
    "targetBoxTemp",
)

# Notifications
CONF_NOTIFY_DEVICE = "notify_device"
CONF_NOTIFY_COMPLETED = "notify_completed"
CONF_NOTIFY_ERROR = "notify_error"
CONF_NOTIFY_MINUTES_TO_END = "notify_minutes_to_end"
CONF_MINUTES_TO_END_VALUE = "minutes_to_end_value"

# Grace window after a (re)start during which the printer's current state is only
# captured as a baseline, never notified about. The printer keeps reporting the
# last job's file name and 100% progress indefinitely, so without this every HA
# restart fired a "print completed" notification (issue #112).
NOTIFY_PRIME_GRACE_SECS = 10.0

# Progress ceiling for re-arming the one-shot completion notification. The
# printer rounds progress up to 100 a second before the job actually ends and
# then reports 99 once more, so "progress fell below 100" on its own does not
# mean a new job started -- treating it that way sent the completion
# notification twice for every print. Only a drop clear of that jitter, or a
# restart of the job clock, counts as a new cycle.
NOTIFY_REARM_PROGRESS_MAX = 90

# --- Multi-target delivery -------------------------------------------------- #
# CONF_NOTIFY_DEVICE above held a single service name. It is still read, and is
# never deleted, so that rolling back to an earlier release keeps a user's
# target. coerce_targets() in notification_rules.py is the only thing that
# should look at it.
CONF_NOTIFY_TARGETS = "notify_targets"
CONF_NOTIFY_LIVE = "notify_live"
CONF_NOTIFY_ACTIONS = "notify_actions"
CONF_NOTIFY_PREVIEW_IMAGE = "notify_preview_image"
CONF_NOTIFY_CAMERA_SNAPSHOT = "notify_camera_snapshot"
CONF_NOTIFY_TAP_PATH = "notify_tap_path"
CONF_NOTIFY_PREVIEW_ENTITY = "notify_preview_entity"
CONF_NOTIFY_CAMERA_ENTITY = "notify_camera_entity"

# Sentinel message that dismisses a notification (and ends a Live Activity)
# carrying the same tag. It is only meaningful to the companion app: any other
# notify platform would render it as visible body text, so it must never be
# delivered to one.
CLEAR_NOTIFICATION_MARKER = "clear_notification"

# --- Live print card -------------------------------------------------------- #
# Progress here is authoritative 0-100, not an estimate, so pushes are driven by
# a monotonic milestone latch rather than a cap derived from the expected
# duration. A print then costs at most 100/STEP progress pushes whether it runs
# twenty minutes or forty hours, and the end-of-print 99->100->99->100 jitter
# (see NOTIFY_REARM_PROGRESS_MAX) cannot produce a second push.
NOTIFY_LIVE_MILESTONE_STEP = 5
# iOS throttles frequent Live Activity updates and eventually drops them, so
# this floor applies regardless of what changed.
NOTIFY_LIVE_MIN_INTERVAL_SECS = 30.0
# Pause/resume has to show up immediately, so it bypasses the interval above --
# but not completely, or telemetry that flaps between two states would spam.
NOTIFY_LIVE_TRANSITION_FLOOR_SECS = 5.0
# Defensive only: the milestone latch already bounds pushes per job.
NOTIFY_LIVE_MAX_PUSHES_PER_JOB = 60
# Apple hard-expires a Live Activity after eight hours. Past this the live-only
# keys are dropped and the card degrades to a plain tagged notification, which
# still updates in place. Android 16 progress notifications do not expire, so
# this is an iOS-shaped limit we accept rather than work around -- restarting
# the activity would burn a push-to-start slot and show a visibly new card.
NOTIFY_LIVE_IOS_EXPIRY_SECS = 28800.0
# No telemetry for this long means the printer is gone; clear the card.
NOTIFY_LIVE_STALE_CLEAR_SECS = 90.0

# Card colours by phase.
NOTIFY_COLOR_PRINTING = "#03a9f4"
NOTIFY_COLOR_PAUSED = "#ffa726"
NOTIFY_COLOR_DONE = "#43a047"
NOTIFY_COLOR_ERROR = "#e53935"

# Android notification channel *names* are user-visible in the phone's settings,
# so they live in strings.json like every other label. Splitting the terminal
# and alert channels from the live one lets a user silence progress without
# silencing failures.
NOTIFY_CHANNEL_KEY_LIVE = "channel_live"
NOTIFY_CHANNEL_KEY_DONE = "channel_finished"
NOTIFY_CHANNEL_KEY_ALERT = "channel_alerts"

# Joins the segments of a live-card body. Punctuation rather than prose, so it
# stays here instead of in strings.json.
NOTIFY_BODY_SEPARATOR = " · "

# `preview_reason` values that mean the image entity would serve its 1x1
# placeholder. Anything else -- including an unset value, which just means
# nothing has asked the entity for bytes yet -- is worth attaching.
PREVIEW_REASONS_UNUSABLE = ("not_printing", "fetch_failed")

# --- Bus events ------------------------------------------------------------- #
# Language-neutral, and fired whether or not any notify target is configured.
# Notification bodies are composed in Python and therefore follow the *server*
# language -- an integration is never told which user a notify call is for --
# so these are the supported way to build your own text, in your own language,
# with your own conditions.
BUS_EVENT_PRINT_STARTED = "ha_creality_ws_print_started"
BUS_EVENT_PRINT_FINISHED = "ha_creality_ws_print_finished"
BUS_EVENT_PRINT_ERROR = "ha_creality_ws_print_error"

CONF_POLLING_RATE = "polling_rate"
DEFAULT_POLLING_RATE = 0  # Real-time

# Moonraker defaults
MR_PORT = 7125
MR_POLL_INTERVAL = 30
MR_POLL_TIMEOUT = 5
MR_QUERY_PARAMS = "objects=temperature_fan%20chamber_fan"
