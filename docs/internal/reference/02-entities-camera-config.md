# Creality WS Entity, Camera, Config-Flow and Services Reference

**Scope:** entity platforms, camera (MJPEG, go2rtc WebRTC bridge, direct WebRTC, custom URL), config flow (user, zeroconf), options flow, service actions, translations.
**Files read:** `sensor.py`, `number.py`, `light.py`, `fan.py`, `button.py`, `image.py`, `camera.py`, `config_flow.py`, `services.yaml`, `strings.json`, `translations/en.json`, `translations/es.json`, the service handlers in `__init__.py`, plus `entity.py`, `const.py`, and the parts of `coordinator.py`, `utils.py` and `ws_client.py` the entities read from.
**Path convention:** a bare `file.py:N` is `custom_components/ha_creality_ws/file.py`, line `N`, on branch `0.9.9` (version `0.9.9` in `manifest.json`). Several files are CRLF; line numbers are the same either way.

> Written 2026-10-02 against commit `da0295a`. Line anchors drift; the symbol names next to them are the stable part.

---

## 1. Shared entity base (`entity.py`)

Every entity inherits `KEntity(CoordinatorEntity)` (`entity.py:9`).

| Aspect | Rule | Anchor |
|---|---|---|
| Naming | `_attr_has_entity_name = True`; a `translation_key` (argument or class attribute) wins, the `name` argument is only used when there is no key. Every shipped entity has a key, so the `name` arguments are never used. | `entity.py:12`, `entity.py:18-22` |
| unique_id | `f"{coordinator.client._host}-{suffix}"`. `_host` is `entry.data["host"]` exactly as typed (an IP in practice). Empty suffix raises `ValueError`. | `entity.py:15-23`, `__init__.py:213`, `coordinator.py:157` |
| Availability | Unavailable if `coordinator.power_is_off()` or if no frame arrived in `STALE_AFTER_SECS` (15 s). | `entity.py:26-32`, `coordinator.py:421-423`, `const.py:48` |
| Power-off rule | `power_is_off()` is False while the WebSocket is connected; otherwise True when the configured switch is `off`/`unavailable`/`unknown` or missing. | `coordinator.py:308-325` |
| Zeroing helper | `_should_zero()` = not available or power off. Because it is the exact negation of `available`, the zero branches only matter on entities that override `available` to True (see 1.1). | `entity.py:35-41` |
| Device | `identifiers={(DOMAIN, host)}`, `manufacturer="Creality"`, `model`/`name`/`hw_version`/`sw_version` from the onboarding cache (`_cached_model`, `_cached_hostname`, `_cached_model_version`), falling back to live `model`/`hostname`/`modelVersion`. Name is the hostname, else `"<model> (Creality)"`. `configuration_url=http://<host>/`. | `entity.py:98-130` |
| Max-temp cache | `_get_cached_max_temps()` reads `_cached_max_bed_temp`, `_cached_max_nozzle_temp`, `_cached_max_chamber_temp` (legacy `_cached_max_box_temp`), falling back per field to live `maxBedTemp`/`maxNozzleTemp`/`maxBoxTemp`. | `entity.py:57-96` |

### 1.1 Entities that override availability to "always"

| Entity | Anchor |
|---|---|
| `sensor` `model_info` (field `model`) | `sensor.py:247-252` |
| `sensor` `max_nozzle_temp` / `max_bed_temp` / `max_box_temp` (`KMaxTempSensor`) | `sensor.py:1240-1243` |
| `button` `reconnect_ws` | `button.py:103-106` |
| `image` `current_print_preview` | `image.py:86-88` |

### 1.2 Data path the entities read

`ws_client` merges each JSON frame into a cumulative `_state` (after `coerce_numbers`, which turns numeric strings into numbers and leaves anything else, including `""`, untouched) and hands a full copy to the coordinator (`ws_client.py:270-285`, `utils.py:23-34`). The coordinator `merge_telemetry`s it into `coordinator.data` (`coordinator.py:569-594`) and calls `async_update_listeners()` (`coordinator.py:745-746`), throttled only while printing when `polling_rate > 0` (`coordinator.py:740-743`). `_state` is never cleared for the life of the client (`ws_client.py:55`).

---

## 2. Onboarding cache in `entry.data`

Written by `async_setup_entry` and read by the platform gates. Not user-editable.

| Key | Written | Meaning / gate |
|---|---|---|
| `host` | config flow | Printer address; also the unique_id root |
| `_last_ip` | `__init__.py:252-255` | Re-cache trigger when host changes |
| `_device_info_cached`, `_cached_version` | `__init__.py:316-317`, `375-376` | Cache present; re-cache on version change (`__init__.py:245-249`) |
| `_cached_model`, `_cached_hostname`, `_cached_model_version` | `__init__.py:318-320` | Device info |
| `_cached_has_light` | `__init__.py:321`, promoted by live `lightSw` (`:337-338`) | Light platform gate |
| `_cached_has_brightness_control`, `_cached_led_pin` | `__init__.py:322-323`, offline fallback `:391-405` | Light dimming (K2 Pro / K2 Plus pin `LED`, `utils.py:202-205`) |
| `_cached_has_chamber_sensor` (+ legacy `_cached_has_box_sensor`) | `__init__.py:325,327`, promoted by `boxTemp`/`maxBoxTemp` (`:334-336`) | Chamber sensor gate |
| `_cached_has_chamber_control` (+ legacy `_cached_has_box_control`) | `__init__.py:326,328`, promoted by `targetBoxTemp` (`:331-333`) | Chamber target number gate |
| `_cached_cfs_detected` | `__init__.py:341` | Diagnostics only; also forces a re-cache when `cfsConnect == 1` (`:275-276`) |
| `_cached_max_bed_temp`, `_cached_max_nozzle_temp`, `_cached_max_chamber_temp` (+ legacy `_cached_max_box_temp`) | `__init__.py:344-348` | Number maxima, max-temp sensors |
| `_cached_camera_type` | `__init__.py:351-359`; offline first setup writes `"mjpeg"` (`:390`) | Camera "auto" path (section 9.2). Written once, never re-detected. |
| `_cached_mac` | zeroconf only (`config_flow.py:173`) | MAC-based IP update in zeroconf (section 10.2) |

Capability flags come from `ModelDetection` (`utils.py:191-319`): `has_chamber_control = is_k2_family` (`:300`), `has_chamber_sensor` for K1 / K1C / K1 Max / K2 family and not K1 SE / Ender V3 (`:306-308`), `has_light = not (K1 SE or Ender V3 family)` (`:313`), `supports_webrtc = webrtcSupport == 1` (`:215`).

`async_remove_config_entry_device` refuses the printer's current device and leaves the cache alone (R35); before, it dropped every `_cached_*` key, `_cached_mac` included.

---

## 3. Platforms

`PLATFORMS = ["sensor", "camera", "button", "number", "fan", "light", "image"]` (`__init__.py:84`), forwarded at `__init__.py:493`. Every platform reads its coordinator from `hass.data[DOMAIN][entry.entry_id]` (stored at `__init__.py:412`); none uses `entry.runtime_data`. No platform declares `PARALLEL_UPDATES`. No entity uses an `EntityDescription`.

---

## 4. Sensor platform (`sensor.py`)

Unique id is always `<host>-<key>`. "Default" availability is the `KEntity` rule in section 1.

### 4.1 Telemetry-field sensors from `SPECS` (`KSimpleFieldSensor`, `sensor.py:89-218`, class `:236-303`)

`native_value` runs every SPECS value through `utils.numeric_state` (R3, #121): an int stays an int, a numeric string is converted, and a blank, non-number, NaN or infinity is None ("unknown"). Before R3 it returned the raw value, so a booting printer's `""` made Home Assistant reject the state write and the entity stayed "unavailable". Progress takes the first of `printProgress`, `dProgress` that holds a number (it used `or`, so a real 0% showed the previous job's value). The CFS box temperature/humidity and slot percent go through the same helper; `boxsInfo` is nested, so the client's top-level `coerce_numbers` never reaches it.

| key (uid) | translation_key | Source field | Device class | Unit | State class | Attributes | Gate |
|---|---|---|---|---|---|---|---|
| `bed_temperature` | `bed_temperature` | `bedTemp0` | TEMPERATURE | C | MEASUREMENT | `target`=`targetBedTemp0`, `max`=`maxBedTemp` | always (`:1152-1154`) |
| `box_temperature` | `chamber_temperature` | `boxTemp` | TEMPERATURE | C | MEASUREMENT | `target`=`targetBoxTemp`, `max`=`maxBoxTemp` | chamber gate (4.6) |
| `nozzle_temperature` | `nozzle_temperature` | `nozzleTemp` | TEMPERATURE | C | MEASUREMENT | `target`, `max` | always |
| `print_progress` | `print_progress` | `printProgress or dProgress` (`:285-286`) | none | % | MEASUREMENT | none | always |
| `total_layers` | `total_layers` | `TotalLayer` | none | none | MEASUREMENT | none | always |
| `current_layer` | `current_layer` | `layer` | none | none | MEASUREMENT | none | always |
| `position_x` / `_y` / `_z` | `position_x` / `_y` / `_z` | parsed from `curPosition` (`utils.py:68-81`) | DISTANCE | mm | MEASUREMENT | none | always; disabled by default for new installs (R36) |
| `feedrate_pct` | `feedrate_pct` | `curFeedratePct` | none | % | MEASUREMENT | none | always |
| `flowrate_pct` | `flowrate_pct` | `curFlowratePct` | none | % | MEASUREMENT | none | always |
| `model_info` | `model_info` | `_cached_model`, else live `model` (`:264-271`) | none | none | none | `hostname`, `modelVersion` (cache first) | always; always available (`sensor.py:1132-1147`) |

### 4.2 Mapped sensor (`KMappedSensor`, `sensor.py:221-233`, class `:306-335`)

| key | translation_key | Source | Values | Category | Notes |
|---|---|---|---|---|---|
| `filament_status` | `filament_status` | `materialStatus` | `0 -> normal`, `1 -> runout`, anything else or missing -> `None` | DIAGNOSTIC | icon `mdi:printer-3d-nozzle-alert`; ENUM with options from the mapping (R36) |

### 4.3 Job and status sensors (always created, `sensor.py:1119-1126`)

| key | Class (anchor) | Source | Device class / unit / state class | Notes |
|---|---|---|---|---|
| `print_status` | `PrintStatusSensor` (`:338-384`) | `utils.derive_print_state` (`utils.py:494-552`) | ENUM / none / none; options `utils.PRINT_STATES` minus `unknown`, which is reported as `None` (R36) | States `off`, `unknown`, `error`, `self-testing`, `completed`, `paused`, `stopped`, `printing`, `processing`, `idle`. Attributes `file`, `progress`, `job_time_s`, `left_time_s`, `used_material_mm`, `real_time_flow_mm3_s`, `paused_flag`, `state_raw`, `err`, plus `error_code` when non-zero (`:358-384`). |
| `used_material_length` | `UsedMaterialLengthSensor` (`:387-406`) | `usedMaterialLength` (mm) / 10 | DISTANCE / cm / MEASUREMENT | converted in code, rounded to 0.01 |
| `print_job_time` | `PrintJobTimeSensor` (`:495-513`) | `printJobTime` | DURATION / s / MEASUREMENT | `int()` |
| `print_left_time` | `PrintLeftTimeSensor` (`:515-533`) | `printLeftTime` | DURATION / s / MEASUREMENT | not cleared when idle |
| `real_time_flow` | `RealTimeFlowSensor` (`:535-549`) | `realTimeFlow` | none / `mm³/s` / MEASUREMENT | no device class on purpose (`:538`) |
| `current_object` | `CurrentObjectSensor` (`:552-585`) | `current_object` or `currentObject` | none | returns the slug `not_printing` (state translation "Not printing") when there is no file, `None` while unavailable (R33); attribute `excluded_objects` from `excluded_objects_list` or `excluded_objects` |
| `object_count` | `ObjectCountSensor` (`:588-631`) | `objects_list` / `objectsList` / `objects` | none / none / MEASUREMENT | `objects` arrives as a JSON string with polygons on a K1C and is `json.loads`-ed on every state write (`:613-619`) |
| `print_control` | `KPrintControlSensor` (`:634-662`) | coordinator pending flags | ENUM, DIAGNOSTIC (R36) | states `queued` / `ok`, `None` when unreachable; attributes `pending_pause`, `pending_resume`, `paused`, `status_raw_state`, `status_raw_deviceState`, `print_file`, `progress` |

### 4.4 Capability-limit sensors (`KMaxTempSensor`, `sensor.py:1226-1288`)

Always available, never zeroed. TEMPERATURE / C / MEASUREMENT. Value = cached max, else live (`:1245-1279`).

| key | translation_key | Gate |
|---|---|---|
| `max_nozzle_temp` | `max_nozzle_temp` | cached or live `maxNozzleTemp` non-null **at platform setup only** (`:1193-1197`) |
| `max_bed_temp` | `max_bed_temp` | cached or live `maxBedTemp` non-null at setup only (`:1194-1199`) |
| `max_box_temp` | `max_chamber_temp` | with the chamber gate, re-evaluated on discovery (`:1048-1061`) |

### 4.5 Slicer-estimate sensors (gated on `gcodeFileInfo`, `sensor.py:1066-1086`)

The coordinator reduces the printer's `retGcodeFileInfo2` listing to the running job's entry under `GCODE_INFO_KEY = "gcodeFileInfo"` (`coordinator.py:629-665`, `const.py:82-84`). The key's first appearance creates all three (late discovery).

| key | Class | Source | Device class / unit / state class | Notes |
|---|---|---|---|---|
| `expected_material_length` | `ExpectedMaterialLengthSensor` (`:408-441`) | `consumables` (mm) / 10 | DISTANCE / cm / MEASUREMENT | not zeroed (but still unavailable when the printer is, section 1); attributes `material`, `color` (`materialColors`), `slicer` (`software`), `estimated_time_s` (`timeCost`), `gcode_file` (`name`) |
| `expected_material_weight` | `ExpectedMaterialWeightSensor` (`:444-469`) | `filamentWeight` (g) | WEIGHT / g / MEASUREMENT | unknown for multi-material strings and blank weights |
| `filament_consumption` | `FilamentConsumptionSensor` (`:472-492`) | `usedMaterialLength / consumables * 100` | none / % / MEASUREMENT | uncapped |

### 4.6 Chamber gate (`add_chamber_entities`, `sensor.py:1021-1062`)

Creates `box_temperature` and (if a max exists, cached or live) `max_box_temp` when `_cached_has_chamber_sensor` (legacy `_cached_has_box_sensor`) is true, or when any of `boxTemp`, `targetBoxTemp`, `maxBoxTemp` is in live data. Re-run on every discovery signal.

### 4.7 CFS sensors (`add_cfs_entities`, `sensor.py:924-1001`)

Source: `coordinator.data["boxsInfo"]["materialBoxs"]`, requested on connect and every 300 s (`ws_client.py:35`, `ws_client.py:441-500`); service `request_cfs_info` asks on demand. Box `type == 0` is a CFS unit, `type == 1` the external spool holder. The per-box pass skips the type-1 box (it gets the `cfs_external_*` set); before R20 it skipped it only when a CFS was present, so an external-only printer got both sets for one spool. `_box_slots_registered` keeps creating the per-box set where an earlier version already registered it.

| Entity | unique_id suffix | translation_key (placeholders) | Device class / unit / state class | Value | Anchor |
|---|---|---|---|---|---|
| Box temperature | `cfs_box_{box_id}_temp` | `cfs_box_temp` (`box_id`) | TEMPERATURE / C / MEASUREMENT | box `temp` | `sensor.py:666-699`; created when `temp` is non-null (`:950-956`) |
| Box humidity | `cfs_box_{box_id}_humidity` | `cfs_box_humidity` (`box_id`) | HUMIDITY / % / MEASUREMENT | box `humidity` | same |
| Slot filament | `cfs_box_{box_id}_slot_{slot_id}_filament` | `cfs_slot_filament` (`box_id`, `slot` = slot_id + 1) | none | `format_filament_label(vendor, name, type)`; the vendor alone, or `None` (HA's own "Unknown") when name and type are empty (R33) | `sensor.py:744-803` |
| Slot color | `..._slot_{slot_id}_color` | `cfs_slot_color` | none, icon `mdi:palette` | `normalize_color_hex(color)`: last six hex digits, lowercase `#rrggbb`, list-aware (`utils.py:365-405`) | same |
| Slot remaining | `..._slot_{slot_id}_percent` | `cfs_slot_percent` | none / % / MEASUREMENT | `percent` | same |
| External filament / color / remaining | `cfs_external_filament` / `_color` / `_percent` | `cfs_ext_filament` / `cfs_ext_color` / `cfs_ext_percent` | as above | first `type == 1` box, slot matched by id else `materials[0]` | `sensor.py:806-870`, created at `:978-991` |
| Active slot | `active_filament_slot` | `active_filament_slot` | none, icon `mdi:printer-3d-nozzle` | first slot with `selected`: slug `external` or `box_{box_id}_slot_{n}`, shown through state translations for boxes 1-4 (R33; the raw state used to be the English text); attributes `filament`, `color`, `percent` | `sensor.py:873-913`, created once per printer (`:993-998`) |

Slot attributes (box slots and external slot alike, `_cfs_slot_attributes`, `sensor.py:702-741`): `vendor`, `type`, `name`, `color_hex`, `color_hex_raw`, `rfid`, `spool_key` (`utils.build_spool_key`, `utils.py:434-476`: rfid else vendor+name/type, plus normalised colour tokens), `state`, `selected`, `box_id`, `slot_id`, `min_temp`, `max_temp`, `pressure`. Attributes are empty when the printer is unavailable (`sensor.py:798-799`, `862-863`).

Slot id normalisation: the printer's `id` cast to int; negative or missing falls back to the list index (`sensor.py:961-968`).

Box loop rule (`sensor.py:936-946`): the `type == 1` box is skipped in the per-box loop **only** when a `type == 0` box is present. With no CFS unit, the external holder gets both `cfs_box_{id}_slot_0_*` and `cfs_external_*` entities.

### 4.8 Late discovery (`sensor.py:1004-1115`, `number.py:79-112`)

`merge_telemetry` sends `f"{DOMAIN}_new_entities_{entry_id}"` once per field the first time any of `LATE_DISCOVERY_FIELDS = ("boxsInfo", "boxTemp", "maxBoxTemp", "targetBoxTemp", "gcodeFileInfo")` appears (`const.py:109-115`, `coordinator.py:583-594`). The sensor callback re-runs the CFS, chamber and G-code gates (`sensor.py:1092-1106`); additions are deferred with `hass.loop.call_soon` and dropped after unload (`platform_live`, `sensor.py:1007-1017`). The signal does not fire again when an already-seen field changes shape (for example a CFS box appearing inside an existing `boxsInfo`).

---

## 5. Number platform (`number.py`)

| key | Class (anchor) | translation_key | Reads | Writes | Mode / range / unit | Gate |
|---|---|---|---|---|---|---|
| `print_tuning_pct` | `PrintTuningPercent` (`:118-154`) | `print_tuning_pct` | `curFeedratePct`, else `curFlowratePct` | **both** `setFeedratePct=v` and `setFlowratePct=v` (`:152-154`) | SLIDER, 1-200, step 1, % | always |
| `nozzle_target` | `NozzleTargetNumber` (`:158-193`) | `nozzle_target` | `targetNozzleTemp` | optimistic `data["targetNozzleTemp"]`, then `nozzleTempControl=v` | BOX, 0..max (cached/live `maxNozzleTemp`, default 300, read once in `__init__`), C, TEMPERATURE | always |
| `bed_target_0` | `BedTargetNumber` (`:196-233`) | `bed_target` | `targetBedTemp0` | optimistic, then `bedTempControl={"num": 0, "val": v}` | BOX, 0..max (default 100), C | always (index 0 only, `:30`) |
| `box_target` | `BoxTargetNumber` (`:236-286`) | `chamber_target` | `targetBoxTemp` | optimistic via `merge_telemetry`, then `boxTempControl=v` | BOX, 0..max (cached/live `maxBoxTemp`, default 60), C | `_chamber_entities` (`:43-75`): `_cached_has_chamber_control` (legacy `_cached_has_box_control`) or live `targetBoxTemp`; with cache only, also needs a max; re-run on discovery |

All writes go through `KEntity._send` (`coordinator.send_command` around `client.send_set_retry`), which raises the translated `HomeAssistantError` `printer_not_connected` if the link is not back within 6 s; the home and stop buttons raise the same when `ensure_connected` fails, and a Stop tapped on the live card logs a warning instead (R32). Temperature targets are written to `coordinator.data` only after the send succeeds. K2 Base: `targetBoxTemp: 0` is stripped from WS frames and the real target comes from a Moonraker poll on port 7125 every 30 s (`coordinator.py:700-706`, `coordinator.py:731-736`, `coordinator.py:2250-2279`, `const.py:318-321`).

---

## 6. Light, fan, button, image

### 6.1 Light (`light.py`)

| key | translation_key | Reads | Writes | Color modes | Gate |
|---|---|---|---|---|---|
| `light` | `light` | `lightSw > 0` (`:62-73`) | on: `lightSw=1`; dim: `lightSw=1` then `gcodeCmd="SET_PIN PIN=<led_pin> VALUE=<b/255>"`; off: `lightSw=0` (`:89-116`) | `ONOFF`, or `BRIGHTNESS` when `_cached_has_brightness_control` and a `_cached_led_pin` exist (`:28-32`, `:52-57`) | `_cached_has_light` (default True) or live `lightSw` at setup (`:19-26`); no late discovery |

Brightness is remembered in memory only (`:60`, `:79-87`); reported as 255 after a restart.

### 6.2 Fan (`fan.py`)

Three unconditional fans (`fan.py:14-16`), `SET_SPEED | TURN_ON | TURN_OFF` (`:24-26`).

| key | translation_key | Reads | Writes |
|---|---|---|---|
| `model_fan` | `model_fan` | `modelFanPct` | `gcodeCmd="M106 P0 S<0-255>"` |
| `case_fan` | `case_fan` | `caseFanPct` | `M106 P1 S..` |
| `side_fan` | `side_fan` | `auxiliaryFanPct` | `M106 P2 S..` |

`turn_on` with no percentage restores the current speed or goes to 100 (`fan.py:65-87`). `_attr_percentage_step` (`:27`) is not a FanEntity attribute; the 1% step comes from the default `speed_count` of 100.

### 6.3 Buttons (`button.py`)

| key | translation_key | Action | Availability |
|---|---|---|---|
| `home_all` | `home_xyz` | `ensure_connected()`, `autohome="X Y"`, wait up to 15 s while `deviceState == 7`, `autohome="Z"` (`:24-51`); not connected logs a warning and returns | default (no busy gate) |
| `pause_print` | `pause_print` | `coordinator.request_pause()` (`:57-65`) | default |
| `resume_print` | `resume_print` | `coordinator.request_resume()` (`:67-75`) | default |
| `stop_print` | `stop_print` | `coordinator.async_stop_print()` (`:77-86`) | default |
| `reconnect_ws` | `reconnect` | `client.reconnect()`; DIAGNOSTIC since R36 | always |

### 6.4 Image (`image.py`)

| key | translation_key | Content | Notes |
|---|---|---|---|
| `current_print_preview` | `current_print_preview` | `image/png` from `https://<host>/downloads/original/current_print_image.png` then `http://...` (`:112-115`), 5 s timeout, self-signed TLS accepted | Fetched only when HA asks for bytes. Gate `_status_allows_preview()` (`:61-84`): printer reachable, a file name, and `derive_activity_state` in `PREVIEW_PRINT_STATES` (`utils.py:491`). Otherwise returns the last image or a 1x1 PNG and sets `preview_reason="not_printing"`. Fetch throttle 5 s after a success (`:107-109`). Attributes `preview_reason` (`None`/`ok`/`not_printing`/`fetch_failed`) and `source_url`. `image_last_updated` changes only on a successful fetch (`:142`). |

The coordinator attaches `/api/image_proxy/<entity_id>` to notifications unless `preview_reason` is in `PREVIEW_REASONS_UNUSABLE` (`coordinator.py:1276-1280`, `const.py:301`).

---

Since R27 `_handle_coordinator_update` resets the entity when `printFileName` changes to a new job: last image, reason and source cleared, `image_last_updated` bumped, so the frontend refetches at once instead of at the next token rotation. `KCoordinator._notify_media` treats a `not_printing` reason as stale while a preview state is active (entities update after notifications, so it is always stale on a job's first frame).

## 7. Camera entity classes (`camera.py`)

Both classes use unique_id `<host>-camera` and translation_key `printer_camera` (`camera.py:132`, `:141`, `:338`, `:365`). Shared base `_BaseCamera` (`:64-116`): fallback image is the last good frame or a 1x1 white JPEG (`:75-96`); attribute `snapshot_supported` (`:98-116`).

### 7.1 `CrealityMjpegCamera` (`camera.py:119-316`)

* Still image (R38): first mjpg-streamer's `?action=snapshot` beside an `?action=stream` URL (3 s; whether it works is remembered after the first answer), else the first `FFD8..FFD9` frame cut from the stream (5 s). 1 s throttle and a lock. Skipped, returning the last frame, while `_printer_unreachable()` (switched off or coordinator unavailable). `CancelledError` propagates.
* Live: `async_aiohttp_proxy_web` (Home Assistant's helper: 10 s per read, stops at shutdown, 502/504 on connect failure) since R38; it was a hand-rolled proxy with `timeout=None` that swallowed cancellation. Checked on the test box through a custom URL pointing at the mock's `:8000/stream.mjpeg`.
* No `supported_features`, no `stream_source`.

### 7.2 `CrealityWebRTCCamera` (`camera.py:319-1422`)

`supported_features = CameraEntityFeature.STREAM` (`:409-421`). Implements native async WebRTC (`async_handle_async_webrtc_offer`, `async_on_webrtc_candidate`, `close_webrtc_session`), so the frontend is offered WebRTC.

| Constructor input | Meaning | Anchor |
|---|---|---|
| `signaling_url` | Always `WEBRTC_URL_TEMPLATE` = `http://<host>:8000/call/webrtc_local` | `camera.py:1482`, `const.py:31-35` |
| `go2rtc_url`, `go2rtc_port`, `go2rtc_rtsp_port` | From `entry.options` | `camera.py:1479-1487` |
| `direct_signaling` | True only for `webrtc_direct` | `camera.py:1495-1503` |
| `go2rtc_source` | Custom rtsp/rtmp/srt URL | `camera.py:1519-1521` |

**go2rtc client resolution** (`_initialize_go2rtc_client`, `camera.py:591-704`):

1. If `go2rtc_url` is set and it is *not* the case that HA's go2rtc is loaded and the configured pair is loopback:11984 (`_configured_go2rtc_is_has_own`, `:565-589`), build `Go2RtcRestClient(session, "http://<url>[:<port>]/")` and `validate_server_version()`. On failure, log and fall through with `_go2rtc_fell_back_from_custom = True`.
2. Else use `hass.data["go2rtc"].session` and `.url` (HA's managed instance; private data of another integration), `_go2rtc_is_ha_managed = True`.
3. Else error `go2rtc component not loaded`.

**Stream registration** (`_ensure_stream_configured` / `_configure_stream_locked`, `camera.py:780-908`), under `_stream_config_lock`:

| Item | Value |
|---|---|
| Stream name | `creality_k2_<host with dots as underscores>`; custom sources `creality_custom_<host>` (`:811-827`) |
| Source | `webrtc:http://<host>:8000/call/webrtc_local#format=creality`, or the custom URL verbatim (`:835`) |
| Reuse rule | `_same_go2rtc_source` (R17): a producer matches if its `url` equals the source exactly (the idle form), or, for a `webrtc:` source, if it is the bare connected form go2rtc 1.9 reports (`http://<ip>:8000/call/webrtc_local`, no prefix, no fragment) for the same address. Otherwise delete and re-add. The exact-only rule deleted a watched stream on every reload (reproduced on the test box with three viewers); an idle 0.9.3 stream without `#format=creality` is still replaced. `hactl.py go2rtc` shows the producers |
| Failure | `_force_recreate_stream = True` (`:897-908`) |

**RTSP for HA's stream pipeline** (`stream_source`, `camera.py:523-539`; endpoint `:460-521`): `rtsp://<host>:<port>/<stream_name>`, where port is the explicit `go2rtc_rtsp_port` override (unless the custom server failed), else `18554` for HA's managed go2rtc on loopback:11984 (`const.py:70`), else `8554` (`const.py:71`). IPv6 hosts are bracketed. `None` for direct signalling or while a recreate is pending.

**Snapshots** (`camera.py:706-774`): go2rtc `get_jpeg_snapshot(name, width, height)`, 2 s throttle, JPEG validation, fallback image on any error (logged at WARNING). Since R38 an unreachable printer (switched off or coordinator unavailable) gets the fallback without go2rtc being asked. Direct mode always returns the fallback (`:728-729`) and reports `snapshot_supported = False` (`:431-439`).

**Offer handling, go2rtc bridge** (`camera.py:937-1055`): `webrtc.forward_whep_sdp_offer(source_name, WebRTCSdpOffer(sdp))`; answer sent as `{"type": "answer", "answer": sdp}` through a local duck-typed wrapper (`_wrap_send_message`, `:912-935`) rather than HA's `WebRTCAnswer`/`WebRTCError`. A `Go2RtcClientError` deletes the stream so the next call re-creates it (`:1014-1045`). Frontend ICE candidates are only logged (`:1371-1372`).

**Offer handling, direct** (`_handle_direct_webrtc_offer`, `camera.py:1059-1147`): waits 1 s to collect trickled candidates (`:1075-1076`), reduces the offer to the video m-line with `mid=0` and folds in candidates (`:1157-1177`): the video-tagged ones, or, when none is (max-bundle), those tagged with a bundled mid. Candidates are `webrtc_models.RTCIceCandidateInit` (`sdp_mid`, `sdp_m_line_index`); read as camelCase before R39 they were never told apart and all were sent. Checked on the test box with the mock as a K2 Plus: ICE completes with 2 video-tagged candidates as it did with all 4, POSTs `base64(json({"type":"offer","sdp":...}))` with `Content-Type: plain/text`, `Origin`/`Referer` = `http://<host>:8000` (`:1084-1095`), decodes a base64 JSON or raw SDP answer (`:1338-1353`) and rebuilds the answer so its m-lines match HA's offer, rejecting non-video sections (`:1179-1203`, `:1300-1313`).

Extra attributes: `snapshot_supported`, `go2rtc_stream_name`, `go2rtc_version`, `upstream_signaling_url`, `stream_source`, and `error` when `_last_error` is set (never assigned) (`camera.py:1387-1407`).

---

## 8. Camera endpoints per model

| Model family | Detection | `_cached_camera_type` | Options "Auto" result | URL used |
|---|---|---|---|---|
| K1, K1C (classic), K1 Max, Creality Hi | `model` substring / `F018` (`utils.py:220-280`) | `mjpeg` | `mjpeg` | `http://<host>:8080/?action=stream` (`const.py:21`, `:28`) |
| K1 SE, Ender-3 V3 / KE / Plus | `model` / `F001` `F002` `F005` | `mjpeg_optional` (camera still created) | `mjpeg` | same MJPEG URL |
| K2, K2 Pro, K2 Plus | `F021` / `F012` / `F008` or `k2` in model | `webrtc` | `webrtc` | go2rtc source `webrtc:http://<host>:8000/call/webrtc_local#format=creality` |
| K1C 2025 and other `webrtcSupport == 1` firmware | `supports_webrtc` (`utils.py:215`) | `webrtc` **if** `webrtcSupport` was in the frames seen during the first caching run; never revisited | `mjpeg` (K1C check runs before any WebRTC check, `config_flow.py:326-329`) | as K2 |
| Unknown model, no telemetry | none | `mjpeg` | probe `http://<host>:8000/call` then `/call/webrtc_local`, HEAD then GET, 200/204/405 = present (`config_flow.py:70-102`) -> `webrtc`, else `mjpeg` | per result |

Ports: WebSocket 9999 (`const.py:20`), MJPEG 8080, WebRTC signalling 8000, go2rtc REST 11984 (`const.py:57`), go2rtc RTSP 18554 managed / 8554 stand-alone, Moonraker 7125.

---

## 9. Camera mode decision tree (`camera.async_setup_entry`, `camera.py:1462-1549`)

### 9.1 Forced modes (`entry.options["camera_mode"]`)

```
camera_mode
|- "webrtc"         -> CrealityWebRTCCamera(signaling, go2rtc bridge)            camera.py:1491-1494
|- "webrtc_direct"  -> CrealityWebRTCCamera(signaling, direct_signaling=True)    camera.py:1495-1504
|- "mjpeg"          -> CrealityMjpegCamera(http://<host>:8080/?action=stream)   camera.py:1505-1508
|- "custom"         -> custom_camera_url empty      -> no camera, warning       camera.py:1510-1513
|                      scheme http/https            -> CrealityMjpegCamera(url) camera.py:1515-1517
|                      any other scheme             -> go2rtc bridge, go2rtc_source=url  camera.py:1518-1521
`- unset / "auto"   -> 9.2
```

### 9.2 Cached ("auto") path

```
entry.data["_cached_camera_type"] (default "mjpeg")
|- "webrtc"          -> go2rtc bridge camera          camera.py:1528-1531
|- "mjpeg_optional"  -> MJPEG camera                  camera.py:1537-1545
`- anything else     -> MJPEG camera                  camera.py:1548-1549
```

`_cached_camera_type` comes from `utils.detect_camera_type(data, previous)` (R5, #46): K2 family or `webrtcSupport == 1` -> `webrtc`; an explicit `webrtcSupport` other than 1, or no previous value -> `mjpeg` (`mjpeg_optional` for K1 SE / Ender 3 V3); a frame without the key keeps `previous`, so piecemeal frames cannot flip a working camera; no model at all decides nothing. It is recomputed whenever the device cache refreshes and, independently, on every setup that has telemetry, before the camera platform reads it. In auto mode the camera platform records `coord.camera_type_in_use`; `KCoordinator._check_camera_type` compares it with live telemetry on every frame and, once, writes the corrected type to `entry.data`, which reloads the entry and builds the right camera (covers an HA start with the printer off and a firmware update while running). Verified on the test box both ways, and against a real K1C on firmware 1.3.5.22 (`webrtcSupport: 1`, nothing on :8080, video through go2rtc with `#format=creality`).

The options flow stores `auto` as `auto` since R5 and keeps the go2rtc fields for it. Before, choosing Auto resolved the mode (MJPEG for any K1 before `webrtcSupport` was consulted) and saved the result as a forced mode; those saved modes cannot be told apart from a deliberate choice, so affected users must re-select Auto. The old resolver and its signalling probes were removed.

---

## 10. Config flow (`config_flow.py`)

`ConfigFlow.VERSION = 3`, no `MINOR_VERSION`, no `async_migrate_entry` (`config_flow.py:103-104`). No `async_step_reconfigure`.

### 10.1 `async_step_user` (`config_flow.py:113-135`)

| Field | Type | Required | Default |
|---|---|---|---|
| `host` | `str` | yes | none (not re-filled after an error) |
| `name` | `str` | no | `"Creality Printer (WS)"` (`const.py:12`) |

Flow: `unique_id = host.strip()`, abort `already_configured` if taken (`:116-118`); TCP connect to port 9999 within 2.5 s (`:59-67`) else error `cannot_connect`; create entry `title=name or "<DEFAULT_NAME> (<host>)"`, `data={"host": host}`. `description_placeholders={"name": ...}` is passed but the description does not use it (`:134`).

### 10.2 `async_step_zeroconf` (`config_flow.py:137-173`)

Matchers: `_http._tcp.local.` and `_workstation._tcp.local.` with names `*creality*`, `*k1*`, `*k2*` (`manifest.json`). Steps:

Rewritten for R4 (#39); verified on the test box against real Home Assistant.

1. `extract_info_from_zeroconf`: routable IPv4 first, then any IPv4, then first address; MAC from properties `mac` / `device_mac` / `serial`. Home Assistant passes `decoded_properties` (str keys); the old bytes-key lookup never matched, so no MAC was ever stored. Bytes keys are still accepted.
2. No host -> abort `cannot_connect`.
3. `unique_id = host`, abort `already_configured` if taken, **before** any network probe (every mDNS re-announcement lands here).
4. Same printer at a new address: an entry whose `_cached_mac` matches the MAC, or whose `_cached_hostname` matches the mDNS hostname (`normalize_printer_hostname`: lower case, `.local.` stripped, so `K1C-C627.local.` equals telemetry `K1C-C627`). A hostname-only match whose configured address still answers on 9999 changes nothing (a second interface, or another printer with the same name). Otherwise `host` is updated in `entry.data`; the update listener reloads the entry and `_async_follow_host` moves the device and entities at setup. Abort `already_configured`. (The old MAC branch also scheduled its own reload, so it reloaded twice.)
5. TCP probe 9999 fails -> abort `not_K`.
6. `async_step_zeroconf_confirm`: `_set_confirm_only()`, `title_placeholders={"name": hostname or host}` (`config.flow_title` is `{name}`), description placeholders `name`, `host`. Only the confirmation creates the entry: `title="<DEFAULT_NAME> (<host>)"`, `data={"host"}` plus `_cached_mac` when one was found. Before R4 the entry was created silently, so every `*k1*`/`*k2*` host was added and a deleted printer came back on its next announcement.

### 10.2a Identity follows the host (`_async_follow_host`, `__init__.py`)

Runs at every setup before the platforms. Entity unique ids are still `"<host>-<key>"` and the device identifier `(DOMAIN, host)`; when this entry's device carries a different host, the device identifier and every `"<old>-"` unique id are moved to the current host, and the entry's own `unique_id` follows (unless another entry holds it). Prefix match, not a split, so hostnames with dashes work. A registry already split by the pre-R4 bug (a device and `_2` entities at the new address) is left alone and logged at INFO, because merging either way renames entities someone may have rebuilt automations on. The device lookup is done among the entry's own devices: `device_registry.async_get_device` is deprecated in 2026.9 and its replacement does not exist in older supported cores. Pinned by `tools/tests/test_host_change.py`.

### 10.3 Options flow (`OptionsFlowHandler`, `config_flow.py:264-811`)

A menu (`async_step_init`, `:347-371`) with four pages. Each page validates, folds its fields into a working copy and **writes the entry immediately** with `async_update_entry` (`_persist`, `:285-303`), then returns to the menu. The flow never ends with `async_create_entry`; closing the dialog is the exit. A host change goes out in the same update as the options.

| Page | Fields (stored in `options` unless noted) | Validation / behaviour | Anchor |
|---|---|---|---|
| `camera` | `camera_mode` (select `auto`, `mjpeg`, `webrtc`, `webrtc_direct`, `custom`, translation_key `camera_mode`); `go2rtc_url`, `go2rtc_port`, `go2rtc_rtsp_port` shown for `webrtc`, `auto`, and custom go2rtc schemes; `custom_camera_url` for `custom` | `auto` replaced by `_detect_camera_type()` (`:310-345`); custom URL needs scheme `http`/`https`/`rtsp`/`rtmp`/`srt` and a hostname, else `invalid_camera_url`; go2rtc keys default to `localhost`/11984, kept only for webrtc or a go2rtc custom source and dropped otherwise; RTSP `0` removes the key | `:373-529` |
| `notifications` | `notify_targets` (multi-select of `notify.*` services and notify entities, custom values allowed); sections `events` (`notify_live`, `notify_completed`, `notify_error`, `notify_minutes_to_end`, `minutes_to_end_value` 1-60), `extras` (`notify_actions`, `notify_preview_image`, `notify_camera_snapshot`, `notify_tap_path`), `text` (six `notify_template_*`) | templates checked against `TEMPLATE_FIELDS`, error `unknown_placeholder`; `None` never persisted; sections flattened (`:209-224`) | `:560-728` |
| `power` | `power_switch_enabled` (bool), `power_switch` (entity: `switch`, `input_boolean`, `light`) | enabled without an entity stores `None` | `:746-788` |
| `connection` | `host` (**entry.data**), `polling_rate` (0-60 s) | a changed host must answer on 9999 and not be another entry's (`_new_host_error`: `cannot_connect`, `host_in_use`, form keeps the typed value; R35); since R4 the reload moves the device, entities and `unique_id` to the new host (section 10.2a) | `:790-811` |

Changes confined to `NOTIFY_ONLY_OPTION_KEYS` (`const.py:204-219`) are applied in place; anything else reloads the entry, retried three times on `OperationNotAllowed` (`__init__.py:971-1010`).

### 10.4 Option migration on every setup (`_migrate_go2rtc_settings`, `__init__.py:102-161`)

Runs at `__init__.py:211` (and again after caching, `:368`, `:410`): back-fills `power_switch_enabled` and moves `go2rtc_url`/`go2rtc_port` from `data` to `options`. It used to also **remove** `localhost`/11984 at every setup, which defeated the camera's stand-alone go2rtc support on a Core install; removed for R18 (`test_option_migrations.py`).

---

## 11. Service actions

All registered from `async_setup_entry` (not `async_setup`), each guarded by `has_service`; none are removed on unload.

### 11.1 `diagnostic_dump` (`__init__.py:754-965`, registered `:498-499`)

| Field | Type | Default |
|---|---|---|
| `include_sensitive_data` | bool | `false` (turns redaction off since R9) |

Since R9/R30 both this action and the standard download come from `diagnostics.py`. `printer_diagnostics` builds, per printer: entry (options, cache), connection (WebSocket stats, power, `http_urls_accessed`), printer (pause flags, camera type in use and detected, `ModelDetection` flags), notifications (targets, per-target platform as a list, live card state), the full telemetry, and every entity's registry id, state and attributes. `async_get_config_entry_diagnostics` returns it through `async_redact_data(TO_REDACT)`. The action (`SupportsResponse.OPTIONAL`) collects every printer under `printers`, adds `web_ui_urls` from a crawl of the printer's web root (action only), redacts unless `include_sensitive_data`, logs the result at WARNING between the CREALITY DIAGNOSTIC DATA markers, posts the persistent notification and returns it. `TO_REDACT` covers addresses, hostnames, MAC, notify targets, titles, unique ids, access tokens and every camera/preview attribute that carries the address (checked against a real K1C's camera attributes on the test box); entity ids are kept. Before R9 the action returned nothing, logged everything unredacted including the camera's access token, and ignored `include_sensitive_data`, while every doc said to copy its response.

### 11.2 `request_cfs_info` (`__init__.py:546-572`, registered `:742-743`, no schema)

| Field | Type | Required |
|---|---|---|
| `device_id` | device selector, multiple | no (omitted = every printer) |

Calls `client.request_boxs_info()` per target (`ws_client.py:498-500`), then always posts persistent notification `cfs_request_result` naming the printers asked and the ones not reached. Never raises.

### 11.3 `set_cfs_material` (`__init__.py:574-670`, schema `:715-740`, registered `:745-751`)

| Field | Schema | services.yaml selector |
|---|---|---|
| `device_id` | required, string or list | device, multiple, required |
| `box_id` | required int >= 0 | number min 0, default 1 |
| `slot_id` | required int 0-3 | number 0-3, default 0 |
| `type` | required string | text |
| `name`, `vendor`, `rfid` | optional string | text |
| `color` | optional string, `#?[0-9a-f]{6}` (`utils.py:591`, `677-698`) | text |
| `min_temp` | optional float 150-300 | number 150-300 |
| `max_temp` | optional float 150-350, not below `min_temp` | number 150-350 |
| `pressure` | optional float 0-1 | number 0-1 step 0.01 |

Flow:

1. Empty `device_id` -> `ServiceValidationError` `cfs_material_needs_device`; no matching coordinator -> `no_printer_matched`. `_coordinators_for_devices` maps device ids to entries (`:508-540`).
2. `build_modify_material_payload` (`utils.py:594-674`): `{"boxId", "id", "type"}` plus only the supplied fields, renamed `minTemp`/`maxTemp`; colour written as lowercase `#rrggbb`. Rejections raise `MaterialValueError(key, **placeholders)`, a `ValueError` whose key (`material_*`) becomes the `ServiceValidationError` translation key (R33).
3. Busy check on **all** targets first: `derive_activity_state` in `BUSY_PRINT_STATES = {printing, paused, processing, self-testing}` -> `ServiceValidationError` `cfs_material_printer_busy` (placeholder `printer`). An unreachable printer derives `unknown` and passes.
4. Per target: `send_set_retry(modifyMaterial=payload)`. A failure is logged and posted as persistent notification `cfs_material_error_<host>`, and the remaining targets are still written; afterwards the call raises `HomeAssistantError` (`cfs_material_write_failed`, placeholder `printers`) if any target failed (R19), so the CFS card's save toast reports it. Before R19 the call returned success and the card said "Saved". Success dismisses that and posts `cfs_material_update_<host>` (`:659-669`), then schedules `_log_material_echo`, which re-requests `boxsInfo` and logs the round trip at DEBUG after 3 s (`:672-712`).

Since R33 every error is a translation key and every notification title and body comes from `common` through `_common_strings`/`_fill` in the server's language; `tools/tests/test_inline_strings.py` fails on a literal in a user-facing raise or `pn_async_create`.

---

## 12. Translation coverage

Locales: `en`, `es` (`translations/`). `strings.json`, `en.json` and `es.json` each hold 204 leaf keys with identical key sets; `strings.json` equals `en.json` value for value (checked 2026-10-02, and by `tools/tests/test_translations.py:113`, `:151`).

| Area | Keys present | Gaps |
|---|---|---|
| `config.step.user` | title, description, `host`, `name` | no `data_description` |
| `config.step.zeroconf_confirm`, `config.flow_title` | title, description (`{name}`, `{host}`) | added for R4, en + es |
| `config.error` | `cannot_connect`, `not_K`, `already_configured` | `not_K` and `already_configured` are only ever used as aborts |
| `config.abort` | `already_configured`, `cannot_connect`, `not_K` | none |
| `options.step.*` | `init` (menu), `camera`, `notifications` (sections), `power`, `connection` | `connection` has no description; selector units `sec`/`min` are literals in code (`config_flow.py:650`, `:809`) |
| `options.error` | `invalid_camera_url`, `unknown_placeholder` | none |
| `selector.camera_mode` | all five modes | none |
| `entity.*` names | every translation_key used by the seven platforms (sensor 36, button 5, number 4, fan 3, light 1, camera 1, image 1) | none |
| `entity.sensor.*.state` | `filament_status`, `print_status`, `print_control`, `current_object`, `active_filament_slot` (R33) | none left among sensor states |
| `exceptions` | `unsupported_ha_version` only | every service error in section 11 |
| `services` | all three, every field | none |
| `common` | notification and channel strings (coordinator) | outside this slice |

No `icons.json`: entity icons are `_attr_icon` literals and service actions have no icons.

---

## 13. README vs code

1. README CFS section lists `sensor.<host>_cfs_box_<box>_temp` and `..._slot_<slot>_percent`. Entity ids are generated from the device name and the translated entity name, so they read `sensor.<device>_cfs_box_<box>_temperature` and `..._slot_<n>_remaining`, with `<n>` one-based.
2. README says K1C 2025 cameras are "auto-detected via telemetry". Only the onboarding cache does that, once; the options-flow Auto choice picks MJPEG for any K1C (section 8).
3. README says the optional-camera models "gracefully handle" a missing camera. The camera entity is always created and serves a 1x1 white JPEG.
4. README does not mention that an external holder without a CFS unit produces two sets of slot sensors (section 4.7).
