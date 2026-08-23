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
LATE_DISCOVERY_FIELDS: tuple[str, ...] = ("boxsInfo", "maxBoxTemp", "targetBoxTemp")

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

CONF_POLLING_RATE = "polling_rate"
DEFAULT_POLLING_RATE = 0  # Real-time

# Moonraker defaults
MR_PORT = 7125
MR_POLL_INTERVAL = 30
MR_POLL_TIMEOUT = 5
MR_QUERY_PARAMS = "objects=temperature_fan%20chamber_fan"
