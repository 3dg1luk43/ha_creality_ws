# ha_creality_ws protocol and core runtime - Engineering Reference

**Scope:** the WebSocket protocol client, the push coordinator, entry setup/unload/reload, the shared entity base, the telemetry helpers, constants and the manifest. Notification composition (`notification_rules.py`) and the platforms are only described where the core hands off to them.
**Branch / version:** `0.9.9`, `manifest.json` version `0.9.9`, minimum core `2026.7` (`const.py:18`, `hacs.json`).
**Transport:** one plain `ws://<host>:9999` WebSocket per printer, subprotocol `wsslicer`, JSON frames, app-level heartbeat. No auth, no TLS. `iot_class: local_push`.

All anchors are `file:line` against `custom_components/ha_creality_ws/` unless a path says otherwise.

---

## 0. Module map

| File | Lines | Role |
|---|---|---|
| `ws_client.py` | 535 | `KClient`: connect/backoff loop, frame parsing, cumulative state, heartbeat watchdog, periodic GETs, the only sender. |
| `coordinator.py` | 2279 | `KCoordinator(DataUpdateCoordinator)`: push-only (no `_async_update_data`), frame handler, late discovery, pause/resume queue, power-switch gating, availability, G-code metadata fetch, Moonraker fallback, the whole notification runtime (latches, live card, dispatch). |
| `__init__.py` | 1112 | `async_setup_entry` (version gate, migrations, capability cache, listeners, services), options listener, unload, entry removal, device removal, three services. |
| `entity.py` | 130 | `KEntity(CoordinatorEntity)`: unique-id scheme, availability, zeroing helper, cached device info / max temps. |
| `utils.py` | 716 | `coerce_numbers`, `safe_float`, model-version/position parsing, zeroconf extraction, `ModelDetection`, `derive_print_state` / `derive_activity_state`, CFS write payload builder, core-version compare. |
| `const.py` | 321 | Ports, URL templates, timing constants, late-discovery field list, notification option keys and limits, bus event names, Moonraker constants. |
| `manifest.json` | 27 | Dependencies, requirements, zeroconf matchers, version. |
| `frontend.py` | 370 | Static paths for the Lovelace cards and assets, Lovelace resource upsert/migration. Called from setup. |
| `config_flow.py` (partial) | 811 | Only `async_step_user`, `async_step_zeroconf` and the options-flow host edit are covered here. |

### 0.1 Symbol inventory

**ws_client.py**

| Line | Symbol | Notes |
|---|---|---|
| 33-39 | `GET_REQPRINTERPARA_SEC=5.0`, `GET_PRINT_OBJECTS_SEC=2.0`, `GET_BOXS_INFO_SEC=300.0`, `STABLE_CONNECT_SECS=10.0` | module constants |
| 46 | `class KClient` | |
| 49 | `__init__(host, on_message)` | `_url` is a lambda that resolves the host on every call (L52) |
| 86 | `is_connected` | `_ws is not None and _ws_ready.is_set()` |
| 103 / 109 | `start` / `stop` | |
| 131 | `_is_benign_close` | |
| 153 / 160 | `wait_first_connect` / `wait_connected` | sticky `_connected_once` vs live `_ws_ready` |
| 168 | `reconnect` | sets `_force_connect`, stop + start |
| 176 | `_resolve_host` | `socket.gethostbyname` (blocking) |
| 182 | `_loop` | the connect/receive/backoff loop |
| 380 | `_mark_ready` | first valid frame sets readiness |
| 392 | `_heartbeat` | silence probe + dead-connection close |
| 441 | `_periodic_gets` | ReqPrinterPara / reqPrintObjects / boxsInfo ticker |
| 498 / 502 | `request_boxs_info` / `request_gcode_file_info` | one-shot GETs |
| 509 | `send_set_retry` | the only `method: set` sender |
| 525 | `_send_json` | serialized by `_send_lock` |
| 533 | `last_rx_monotonic` | feeds availability |

**coordinator.py** (core runtime part; notification internals listed in section 5.4)

| Line | Symbol |
|---|---|
| 106 | `_warn_on_unsendable` (module fn) |
| 142 / 144 | `class KCoordinator` / `__init__` |
| 267 | `_load_options` |
| 308 | `power_is_off` |
| 327 / 336 / 347 | `async_start` / `ensure_connected` / `async_stop` |
| 355 | `wait_for_fields` |
| 381 | `async_handle_power_change` |
| 409 / 414 / 421 | `_notify_listeners_threadsafe` / `check_stale` / `available` |
| 426-439 | paused flag + pending accessors |
| 442 / 446 / 468 | `_is_busy_homing` / `_job_state` / `_recompute_paused_from_telemetry` |
| 477 / 508 / 533 | `request_pause` / `request_resume` / `_flush_pending` |
| 569 | `merge_telemetry` (late discovery signal) |
| 597-689 | G-code metadata: `_match_gcode_entry`, `_invalidate_gcode_info`, `_absorb_gcode_file_listing`, `_maybe_request_gcode_info` |
| 691 | `_handle_message` (frame entry point) |
| 1626 | `async_stop_print` |
| 1638 | `notifier_tick` |
| 1880 / 1901 | `notifications_only_change` / `apply_notification_options` |
| 2250 | `_poll_moonraker_extras` |

**__init__.py**

| Line | Symbol |
|---|---|
| 84 | `PLATFORMS = [sensor, camera, button, number, fan, light, image]` |
| 88 | `_get_integration_version` (reads manifest.json in the executor) |
| 102 | `_migrate_go2rtc_settings` |
| 163 | `_core_version` |
| 181 | `async_setup_entry` |
| 508 | `_coordinators_for_devices` |
| 543 | `_register_custom_services` (`request_cfs_info`, `set_cfs_material`) |
| 754 | `_register_diagnostic_service` (`diagnostic_dump`) |
| 971 | `options_update_listener` |
| 1011 | `async_remove_entry` |
| 1056 | `async_unload_entry` |
| 1073 | `async_remove_config_entry_device` |

There is no `async_setup`, no `diagnostics.py`, no repairs, and no `runtime_data`; the coordinator lives in `hass.data[DOMAIN][entry_id]` (`__init__.py:412`) and every platform reads it from there (e.g. `number.py:24`, `camera.py:1474`).

---

## 1. Wire protocol

### 1.1 Transport

- URL: `WS_URL_TEMPLATE = "ws://{host}:9999"` (`const.py`). Since R16 the connect loop resolves the host with `loop.getaddrinfo(family=AF_INET)` (still preferring IPv4, falling back to the host as given) and `get_url()` returns the unresolved URL. Before, `socket.gethostbyname` ran on the event loop at every connect attempt and in `get_url()`, so a `.local` name could stall Home Assistant for seconds per retry. `test_no_blocking_calls.py` now flags `socket.gethostbyname` / `socket.getaddrinfo(`.
- Handshake: `websockets.connect(url, ping_interval=None, subprotocols=["wsslicer"])` (`ws_client.py:223-227`). Library keepalive pings are disabled on purpose; liveness is app-level (section 2.4). `max_size=WS_MAX_MESSAGE_BYTES` (16 MiB) since R15: the G-code listing reply is ~150 KiB per 200 files, and the library's 1 MiB default closed the connection with 1009 on a printer holding more than ~1300 files. Everything else is the library default (`open_timeout=10`, `close_timeout=10` with websockets 15).
- `manifest.json:12-15` declares `websockets>=10.4` and `go2rtc-client>=0.1.0` (the latter is for `camera.py`).

### 1.2 Inbound frames (`ws_client.py:242-288`)

Per message, in order:

1. `bytes` are decoded as UTF-8 with `errors="ignore"` (`:244-247`).
2. The literal text `ok` is dropped (`:250-251`).
3. Non-JSON is dropped silently (`:254-258`).
4. `{"ModeCode": "heart_beat"}` marks the link ready/alive (`_mark_ready`) and is answered with the literal text `ok` (not JSON) (`:261-268`). Heartbeats never reach the coordinator.
5. Any other JSON object: `_mark_ready`, then `coerce_numbers(payload)` (top-level only, `utils.py:23-34`), merged into the client's cumulative `_state` (`:272-273`), `msg_count += 1`, and a **shallow copy of the whole cumulative state** is handed to `on_message` (`:275`, `:284`). The coordinator therefore never sees deltas; every frame it receives carries every key ever seen on this client instance.
6. `retGcodeFileInfo2` is popped from `_state` after the copy (`:282`), so the ~150 KiB listing rides on exactly one frame.
7. Non-dict JSON (arrays, scalars) is logged at debug and dropped (`:287-288`).

`on_message` is **awaited inline** in the receive loop (`:284`). Anything slow in `KCoordinator._handle_message` delays the next `recv()`, the heartbeat reply, and therefore `last_rx`. The coordinator's notification code is written around this (fire-and-forget dispatch, `coordinator.py:2117-2132`).

`coerce_numbers` rules (`utils.py:23-34`): a `str` value becomes `float` if it contains `.` and parses, else `int` if it parses, else it is kept. Consequences: `""` stays `""`, `"nan"`/`"inf"` stay strings, `"1.0"` becomes `1.0`, `"007"` becomes `7`, nested dicts (`err`, `boxsInfo`) are untouched.

### 1.3 Outbound messages

All are compact JSON (`separators=(",", ":")`, `ws_client.py:530`) sent through `_send_json` under `_send_lock`.

| Message | Sender | Cadence / trigger |
|---|---|---|
| `{"method":"get","params":{"ReqPrinterPara":1}}` | `_periodic_gets` `ws_client.py:468-473`; heartbeat probe `:406`, `:425` | every 5 s once ready; probe after 10 s of silence |
| `{"method":"get","params":{"reqPrintObjects":1}}` | `_periodic_gets` `:475-480` | every 2 s once ready |
| `{"method":"get","params":{"boxsInfo":1}}` | `_periodic_gets` `:483-491`; `request_boxs_info` `:498-500` | every 300 s while `cfsConnect` is unknown or 1; first poll fires right after ready (timers back-dated, `:453-455`, issue #99); also from setup (`__init__.py:296`), the `request_cfs_info` service (`:560`) and the material echo (`:683`) |
| `{"method":"get","params":{"reqGcodeFile":1}}` | `request_gcode_file_info` `:502-508` via `coordinator.py:667-689` | when `printFileName` changes; retried every 30 s, at most 3 times without a reply |
| `{"method":"set","params":{"pause":1}}` / `{"pause":0}` | `coordinator.py:494`, `:519`, `:555`, `:563` | pause/resume buttons, live-card actions, queued flush |
| `{"method":"set","params":{"stop":1}}` | `coordinator.py:1636` | stop button, live-card Stop |
| `{"method":"set","params":{"autohome":"X Y"}}` then `{"autohome":"Z"}` | `button.py:40-43` | Home button (waits up to 15 s for `deviceState != 7` between) |
| `{"method":"set","params":{"nozzleTempControl":N}}` | `number.py:193` | |
| `{"method":"set","params":{"bedTempControl":{"num":i,"val":N}}}` | `number.py:233` | |
| `{"method":"set","params":{"boxTempControl":N}}` | `number.py:286` | chamber target |
| `{"method":"set","params":{"setFeedratePct":N}}` + `{"setFlowratePct":N}` | `number.py:153-154` | one slider drives both |
| `{"method":"set","params":{"lightSw":0|1}}` | `light.py:94`, `:108`, `:116` | |
| `{"method":"set","params":{"gcodeCmd":"SET_PIN PIN=LED VALUE=0.xxxx"}}` | `light.py:110` | K2 Pro / K2 Plus dimming (`utils.py:202-205`) |
| `{"method":"set","params":{"gcodeCmd":"M106 P<ch> S<0-255>"}}` | `fan.py:63` | fans |
| `{"method":"set","params":{"modifyMaterial":{boxId,id,type,...}}}` | `__init__.py:646` | `set_cfs_material` service; payload from `utils.py:594-674` |

`send_set_retry` (`ws_client.py:509-523`): one attempt; on any exception wait up to 6 s for `_ws_ready` and try once more; raises `RuntimeError("printer link not available after 6.0s")` if the link never comes back. User commands call it through `coordinator.send_command`, which turns any failure into the translated `HomeAssistantError` `printer_not_connected` (R32). If the first failure happened on a socket that is closing but not yet torn down, `_ws_ready` is still set, so the retry runs immediately against the same dead socket and the original library exception propagates instead.

### 1.4 Telemetry keys the core consumes

| Key | Consumer | Interpretation |
|---|---|---|
| `state` | `utils.py:526-550`, `coordinator.py:473` | 0 processing (with a file), 1 printing, 4 stopped, 5 paused; any other value with a file name derives as `idle` |
| `printFileName` | `utils.py:527`, `coordinator.py:641`, `:669`, `:852` | non-empty means a job exists; a change re-arms per-job latches; full path on K1C |
| `printProgress`, fallback `dProgress` | `utils.py:528-538`, `coordinator.py:778-780`, `:857-859` | explicit `is None` fallback, never `or` (a real 0 must not fall back to the previous job's 100) |
| `withSelfTest` | `utils.py:522-524` | 1..99 means self-testing; outranks every job state |
| `err.errcode` (or bare `err`) | `utils.py:517-520`, `coordinator.py:1566-1571` | non-zero is `error` for display; `derive_activity_state` re-derives without it |
| `pause`, `paused`, `isPaused` | `coordinator.py:473` | any truthy, or `state == 5`, sets the paused flag |
| `deviceState` | `coordinator.py:444`, `button.py:49` | 7 means homing |
| `printJobTime` | `coordinator.py:896-903` | going backwards means a new job |
| `printLeftTime`, fallback `printTimeLeft` | `coordinator.py:749-760` | seconds remaining |
| `materialStatus` | `coordinator.py:1064-1084` | 1 means filament runout |
| `layer`, `TotalLayer`, `usedMaterialLength`, `nozzleTemp`, `bedTemp0` | notification payloads/templates | |
| `model`, `modelVersion` | `utils.py:207-319`, `coordinator.py:700-703` | friendly name and board-code string (`F008`, `F012`, `F021`...) |
| `hostname` | `__init__.py:312`, `coordinator.py:1830` | device name, notification title |
| `cfsConnect` | `ws_client.py:485-486`, `__init__.py:275`, `:293`, `:341` | gates the 5-minute `boxsInfo` poll |
| `boxsInfo` | `coordinator.py:592-593`, CFS sensors | late-discovery field |
| `boxTemp`, `maxBoxTemp`, `targetBoxTemp` | `__init__.py:331-336`, `number.py:43-75` | chamber capability promotion; late-discovery fields |
| `maxBedTemp`, `maxNozzleTemp` | `__init__.py:344-345` | cached slider limits |
| `lightSw` | `__init__.py:337-338` | light capability promotion |
| `webrtcSupport` | `utils.py:215` | `== 1` selects the WebRTC camera at first cache |
| `retGcodeFileInfo2` | `coordinator.py:629-665` | reduced to one entry stored as `gcodeFileInfo` |

---

## 2. Connection lifecycle (`KClient`)

### 2.1 State machine

```
STOPPED --start()--> LOOP
LOOP: [not forced && power off -> reset backoff, sleep POWER_OFF_POLL_SECS (10 s) -> LOOP]
      CONNECTING (websockets.connect, open_timeout 10 s)
        -> fail ---------------------------------------------> BACKOFF
        -> HANDSHAKEN (_ws set, heartbeat + ticker tasks started, NOT ready)
             -> first JSON frame or heart_beat -> READY (_ws_ready, _connected_once, _last_rx)
             -> close, clean or not -> [session >= STABLE_CONNECT_SECS: reset failures/backoff] -> BACKOFF
BACKOFF: sleep, interruptible by stop() -> LOOP
stop(): _stop set, child tasks cancelled, ws.close(1000), loop task cancelled -> STOPPED
```

Readiness is deliberately decoupled from the TCP/WS handshake (`ws_client.py:232-234`, `:380-390`): availability needs real data.

### 2.2 `_loop` walkthrough (`ws_client.py:182-378`)

1. Locals per loop run: `backoff = 1.0`, `connect_failures = 0`, `use_fixed_retry = not _check_power_status`, `max_backoff = 300` with a power switch else `60` (`:183-189`). These are evaluated once per `start()`; a later change of `_check_power_status` has no effect until the next start.
2. **Power check**: a forced iteration (the Reconnect button sets `_force_connect`) skips it once and connects. Every other iteration asks `_check_power_status`; while it says OFF the loop resets the backoff, waits `POWER_OFF_POLL_SECS` (10 s) and re-checks, so the first attempt after power-on is prompt. Fixed for R2: 0.9.1 (`85605c7`) had it inverted, checking only on forced iterations. `test_ws_client_reconnect.py` pins both directions.
3. Connect, start `_heartbeat` and `_periodic_gets` (`:239-240`), iterate frames (section 1.2).
4. (removed in R2: the clean-close-only reset moved into `finally`, step 6.)
5. Exception path (`:298-328`): `connect_failures += 1` regardless of how long the session lasted (`short_lived` is computed at `:299-302` but only logged). Logging: debug if the power check says OFF, debug if `_is_benign_close`, otherwise WARNING for the first three failures of this loop run, then debug. `last_error` is recorded.
6. `finally`: if this attempt connected and lasted at least `STABLE_CONNECT_SECS`, reset `connect_failures`/`backoff` however the session ended (R2); then cancel child tasks, `_ws = None`, clear `_ws_ready`.
7. Sleep (`:341-346`): without a power switch, after 5 failures a fixed 60 s; otherwise `min(backoff * (1.8 + U(0, 0.4)), max_backoff)`. Sequence from a fresh loop: about 1.8-2.2, 3.2-4.8, 5.8-10.6, ... capped at 60 or 300.
8. "mDNS fallback" block (`:348-367`): logs a WARNING ("Attempting mDNS fallback...") when the backoff is near the 300 s cap and the power check says ON, but performs no fallback. Only reachable for power-switch users.
9. Interruptible wait on `_stop` (`:369-373`), then `backoff = min(sleep_for, max_backoff)` unless in the fixed-retry regime (`:375-376`).

**Fixed (R2):** a session that ended abnormally after being healthy used to keep the backoff built up before it (up to 300 s). Now any session of at least `STABLE_CONNECT_SECS` resets it.

### 2.3 Benign closes (`ws_client.py:131-151`)

`ConnectionClosedOK`, close code 1000, messages containing `no close frame received`, `connection closed ok`, `code = 1000`, `sent 1000`, or anything while stopping. The `asyncio.CancelledError` check is dead (it is a `BaseException`, never caught by `except Exception`).

### 2.4 Heartbeat watchdog (`ws_client.py:392-439`)

- Sleeps `PROBE_ON_SILENCE_SECS` (10 s) after connect; if nothing arrived since connect, sends `ReqPrinterPara` (`:401-408`). This is also what un-sticks a printer that does not stream until asked, because `_periodic_gets` is gated on readiness.
- Then every `HEARTBEAT_SECS` (10 s): silence `> 10 s` sends a `ReqPrinterPara` probe (`:422-428`); silence `> 30 s` logs a WARNING and calls `ws.close()` (`:431-437`). Against a dead peer `close()` waits the library `close_timeout` (10 s) before aborting, so a dead link is torn down roughly 40-50 s after the last byte.
- Silence is measured from `max(_last_rx, uptime_start)` so a fresh connection is not judged by the previous one's `_last_rx`.

### 2.5 Periodic GETs (`ws_client.py:441-495`)

Initial 2 s delay; timers back-dated so each poll fires on the first ready iteration (`:453-455`, issue #99, where CFS sensors stayed unavailable because `boxsInfo` is only ever sent on request). The loop wakes every 0.2 s (`:466`, `:493`) and exits when `_ws` is gone or stop is set. Send errors are swallowed.

### 2.6 `stop()` and `reconnect()`

`stop()` (`:109-129`) cancels child tasks, awaits `ws.close(code=1000, reason="shutdown")` (can take up to 10 s on a dead peer), then cancels and awaits the loop task. It does not clear `_state`, `_last_rx`, `_connected_once` or `_force_connect`. `reconnect()` is stop + start with `_force_connect = True`.

The cumulative `_state` (`:55`) is never cleared for the lifetime of the `KClient` object, so keys the printer stops sending (for example `boxsInfo` after a CFS is removed) persist in every later frame, and so in coordinator data. A new `KClient` is only built when the coordinator is (entry setup/reload).

---

## 3. Coordinator frame path

### 3.1 Construction (`coordinator.py:144-260`)

`DataUpdateCoordinator` with `update_interval=None` and an explicit `config_entry` (`:150-156`); no `_async_update_data` is defined, so any `async_refresh`/`async_request_refresh` call reaches core's `NotImplementedError` path. `self.data` starts as `{}` (`:158`). `KClient(host, self._handle_message)` (`:157`). Options are loaded (`:250-251`, `_load_options` `:267-307`). With a configured power switch the client's private `_check_power_status` is pointed at `power_is_off` and the initial power state is sampled (`:253-258`).

### 3.2 `_handle_message` (`coordinator.py:691-746`), in order

1. **K2 Base latch** (`:700-703`): the first frame that carries `model` or `modelVersion` decides `_is_k2_base` (board code `F021`, `utils.py:236`). Latched once per coordinator.
2. On a K2 Base, `targetBoxTemp == 0` is popped from the frame (`:705-706`): port 9999 reports a broken 0 there. Only the frame copy is edited; the client's `_state` keeps the 0, so it is popped again on every frame.
3. `_absorb_gcode_file_listing` (`:708`, section 3.4).
4. `merge_telemetry(payload)` (`:710`, section 3.3).
5. `_recompute_paused_from_telemetry` (`:712`, `:468-474`), which can itself call `async_update_listeners` on a change.
6. `job_state = _job_state()` once (`:715`), `derive_activity_state` with the current power/availability/paused flag (`:446-466`).
7. `_flush_pending(job_state)` inside try/except (`:718-721`, section 3.6).
8. `await _check_notifications(payload)` inside try/except since R14. Before, an exception here aborted the rest of the frame, including the listener update, so entities froze on stale values while still available.
9. `await _maybe_request_gcode_info()` (`:729`), deliberately above the throttle.
10. Moonraker poll on a K2 Base every `MR_POLL_INTERVAL` (30 s), as an HA task (`:732-736`, section 3.5).
11. Throttle: if `polling_rate > 0` and the job is busy, skip `async_update_listeners` unless `polling_rate` seconds passed since the last update (`:740-746`). Default `polling_rate = 0` (`const.py:315`) means every frame updates every entity.

### 3.3 `merge_telemetry` and late discovery (`coordinator.py:569-594`)

`newly_seen = [f for f in LATE_DISCOVERY_FIELDS if f not in self.data and f in payload]`, then `self.data.update(payload)`, then one dispatcher signal `ha_creality_ws_new_entities_<entry_id>` if anything was newly seen. `LATE_DISCOVERY_FIELDS = (boxsInfo, boxTemp, maxBoxTemp, targetBoxTemp, gcodeFileInfo)` (`const.py:109-115`). Each field fires once per coordinator lifetime; "first appearance" is relative to `self.data`, which survives reconnects and power cycles. Every writer of a gating field must go through this helper (tests: `tools/tests/test_late_discovery.py:340-404`); the number entity's optimistic chamber write does (`number.py:283`), the nozzle/bed optimistic writes go straight into `.data` (`number.py:189`, `:229`) because those keys gate nothing.

Since R21 a `boxsInfo` whose set of boxes and slots differs from the last one (`_cfs_shape`: box id, type, slot ids; contents ignored) fires the same signal, so a CFS box that appears after the first report is discovered. Platforms dedupe by unique id.

### 3.4 Sliced G-code metadata state machine (`coordinator.py:597-689`)

- Request side, `_maybe_request_gcode_info` (`:667-689`): nothing to do without a file name or when `_gcode_info_file` already equals it. A new name resets attempts and invalidates the cached entry (`:673-676`). Otherwise give up after `GCODE_INFO_MAX_ATTEMPTS` (3) or wait `GCODE_INFO_RETRY_SECS` (30 s) between attempts (`:677-682`).
- Reply side, `_absorb_gcode_file_listing` (`:629-665`): pops `retGcodeFileInfo2` from the frame before the merge, matches by full `path`, falling back to bare `name` (`:597-616`), records the file as settled even when nothing matched, and merges only the matched entry as `gcodeFileInfo` (a late-discovery field).
- `_invalidate_gcode_info` (`:618-627`) writes `None` only when the key already exists, so the discovery one-shot is not spent on a job whose metadata never arrived.

### 3.5 Moonraker fallback (`coordinator.py:2250-2279`)

Only on a K2 Base. `GET http://<host>:7125/printer/objects/query?objects=temperature_fan%20chamber_fan` (`const.py:318-321`), 5 s timeout, HA's shared session. Reads `result.status["temperature_fan chamber_fan"].target` and, if it differs, `merge_telemetry({"targetBoxTemp": target})` + listener update. Skipped when `power_is_off()`. Failures are debug-logged only. Triggered only from the frame path, so it stops with the WebSocket.

### 3.6 Pause / resume queue (`coordinator.py:477-567`)

- `request_pause`: already paused -> ignore; printing and not homing (`deviceState != 7`) -> send now, queue on failure; any other busy state -> queue; otherwise ignore with a WARNING.
- `request_resume`: paused -> send now, queue on failure; other busy state -> queue; otherwise ignore.
- `_flush_pending` (every frame): drop both queues once the job is not busy (`:544-551`), send a queued pause when `printing` (no homing check, unlike `request_pause`), send a queued resume when `paused`.
- `BUSY_PRINT_STATES = {printing, paused, processing, self-testing}` (`utils.py:486`).

---

## 4. Availability, power switch, staleness

### 4.1 Availability

- `KCoordinator.available` (`coordinator.py:421-423`): `hass.loop.time() - client.last_rx_monotonic() < STALE_AFTER_SECS` (15 s). `_last_rx` is set with `time.monotonic()` (`ws_client.py:386`); the comparison relies on asyncio's default loop clock being `time.monotonic()`.
- `KEntity.available` (`entity.py:26-32`): unavailable when `power_is_off()`, else `coordinator.available`. Overrides: the model sensor is always available (`sensor.py:247-252`), the Reconnect button is always available (`button.py:103-106`).
- `KEntity._should_zero` (`entity.py:35-41`): `not available or power_is_off()`; platforms use it to return `None`/0 while unavailable.
- `check_stale` (`coordinator.py:414-419`) pushes a listener update only when availability flips, via `call_soon_threadsafe` (`:409-412`), a leftover from when the interval ran in an executor. It is now called from an `@callback` on the loop (`__init__.py:444-450`); the docstring "may run off the event loop" is stale.
- The interval is `max(5, STALE_AFTER_SECS // 3)` = 5 s (`__init__.py:453-456`); it also drives `notifier_tick` (`coordinator.py:1638-1654`), which clears a live card after 90 s without telemetry.

### 4.2 `power_is_off` (`coordinator.py:308-325`)

1. If the WebSocket is connected, **always False**, so a connected link outranks a switch that says off. This is availability semantics only; power *edges* use `_switch_reports_off()`, which is rules 2-4 without rule 1.
2. No switch configured: False.
3. Switch entity missing from the state machine: True for `POWER_SWITCH_MISSING_GRACE_SECS` (120 s, the plug's integration may load after this one), then False with a WARNING and a Repairs issue `missing_power_switch_<entry_id>` (`translation_key` `missing_power_switch`), withdrawn when the entity reappears (R24). `async_recheck_missing_switch`, run from the 5 s interval check while the switch is missing, applies the resulting on edge, since no state event arrives for a missing entity and a client deferred at setup is not polling. Before R24 a renamed or deleted switch kept the printer "off" forever.
4. Otherwise True for `off`, `unavailable`, `unknown`.

### 4.3 Power-switch edge handling (`coordinator.py:381-407`, wired at `__init__.py:458-469`)

`async_track_state_change_event` on the configured switch calls `async_handle_power_change`, which takes `_power_lock` and compares `_switch_reports_off()` (the raw switch, ignoring the socket) with `_last_power_off`:

- off edge: `client.stop()`, `_last_power_off = True`. While the socket is connected only a literal `off` counts: `unavailable`/`unknown` is a plug blinking, not a power cut, and is ignored (R2);
- on edge: stop a still-running task, sleep 0.1 s, `client.start()`, `_last_power_off = False`;
- always `async_update_listeners()`.

**Fixed (R2, #45 regression from 0.9.1):** the edge used to be decided from `power_is_off()`, which is False while the socket still looks connected. Cutting the plug under a running printer therefore recorded no off edge, the loop kept dialling with growing backoff, and the on edge found nothing to do, so reconnection waited up to 300 s. `test_power_switch.py` pins the edges and the serialization.

The watcher subscribes to the raw `power_switch` option (`__init__.py:217`, `:468`), not the effective one; when the switch is disabled in options the coordinator has no switch and the handler returns at `:384-386`. The handler is serialized by `_power_lock` (R2), so an off edge still waiting on `client.stop()` cannot swallow a quick on edge.

---

## 5. Print state machine

### 5.1 `derive_print_state` (`utils.py:494-552`), first match wins

| Order | Condition | State |
|---|---|---|
| 1 | `power_off` | `off` |
| 2 | `not available` | `unknown` |
| 3 | `err.errcode` (or bare `err`) non-zero | `error` |
| 4 | `withSelfTest` in 1..99 | `self-testing` |
| 5 | file name and progress >= 100 | `completed` |
| 6 | file name and (`state == 5` or paused flag) | `paused` |
| 7 | file name and `state == 4` | `stopped` |
| 8 | file name and `state == 1` | `printing` |
| 9 | file name and `state == 0` | `processing` |
| 10 | anything else (including other `state` codes with a file) | `idle` |

Non-finite progress is treated as absent (`:532-538`). `derive_activity_state` (`utils.py:555-584`) re-derives with `err={}` when the result is `error`, so it never returns `error`; it is what pause/resume (`coordinator.py:461-466`), the live card (`:1319-1324`), the CFS write guard (`__init__.py:630-640`) and the notification watch use. `PREVIEW_PRINT_STATES = BUSY | {completed}` (`utils.py:491`).

### 5.2 Job end detection

`JobEndWatch.observe` (`notification_rules.py:592-668`) arms only on `printing`/`paused` (`RUNNING_JOB_STATES`, `:533`), ignores `unknown`/`off`, holds on `self-testing` (the #124 fix in `df19ff3`, `:634-641`), defers to completion at progress >= 100, ends at once on `stopped` or a cleared file name, and otherwise confirms an ambiguous state after `NOTIFY_END_CONFIRM_SECS` (15 s). A running state after an end returns `RESTARTED`.

### 5.3 Coordinator latches (`coordinator.py:849-1140`)

Priming (`:865-871`): nothing is notified until a frame carries both a file name and progress, or `NOTIFY_PRIME_GRACE_SECS` (10 s) passes; `_prime_notification_state` (`:762-847`) records the current truth as "already notified" (issue #112). After priming every frame: job-clock restart detection (`:896-903`), the watch (`:917-946`), file-name change re-arm (`:950-962`), early return without a file (`:964-965`), started event (`:976-978`), completion re-arm via `is_new_job_cycle` (`:993-1003`, `notification_rules.py:472-509`, re-arm below `NOTIFY_REARM_PROGRESS_MAX` = 90), live card (`:1008-1009`), completion (`:1022-1028`), stopped (`:1035-1045`), error on a changed code (`:1048-1060`), runout (`:1064-1084`), alert clear (`:1091-1098`), finishing soon (`:1102-1123`), owed dismissals (`:1131-1140`). Bus events (`const.py:309-312`) fire regardless of notify targets; only delivery is gated (`:879-884`).

### 5.4 Notification internals (pointer)

`_update_live_card` `:1390`, `_clear_live_card` `:1505`, `_fire_print_event` `:1531`, `async_handle_notification_action` `:1597`, `_async_load_notify_strings` `:1656`, `_t` `:1694`, `_template_values` `:1719`, `_custom_message` `:1784`, `notify_options_changed` `:1836`, `_notify_event` `:1934`, `_replace_card_with` `:2073`, `_notify_dispatch` `:2117`, `_async_deliver_one` `:2177`. All delivery is `hass.async_create_task` per target, never awaited on the frame path.

---

## 6. Model detection and the capability cache

### 6.1 `ModelDetection` (`utils.py:191-357`)

Built from a telemetry dict (`model`, `modelVersion`, `webrtcSupport`).

- K1 variants by substring of the lower-cased `model`: `k1 se`, `cr-k1 max`, `k1c`, base `cr-k1` or exactly `k1` (`:220-233`); family is any of those or `"k1" in model` (`:274-280`).
- K2 by board code in `model` or upper-cased `modelVersion`: `F021` base, `F012` Pro, `F008` Plus (`:236-238`); family also matches `"k2" in model` (`:283-288`).
- Ender-3 V3 KE `F005`, Plus `F002`, plain `F001` (`:241-264`); Creality Hi `F018` or `"hi" in model` (`:267-270`, a broad substring).
- Capabilities: chamber control = K2 family (`:300`); chamber sensor = K1 base/C/Max or K2, not Ender, not K1 SE (`:306-308`); light = not K1 SE, not Ender (`:313`); LED dimming pin `LED` for K2 Pro/Plus (`:202-205`, `:315-319`). `box_*` names are back-compat aliases.
- `resolved_model()` prefers the friendly `model`, then a canonical name from codes, then `"K by Creality"` (`:346-357`).

### 6.2 Cache in `entry.data` (`__init__.py:239-410`)

Re-cache when any of: never cached, integration version changed, `_last_ip != host`, missing max-temp keys, missing LED keys, or live `cfsConnect == 1` without `_cached_cfs_detected` (`:245-276`).

- Online path (`:284-368`): wait first connect (10 s), wait for `model`/`modelVersion`/`hostname` (6 s), with CFS request `boxsInfo` and wait 5 s, wait 2 s for `maxBoxTemp`/`targetBoxTemp` if absent; then write `_cached_model`, `_cached_hostname`, `_cached_model_version`, light/brightness/LED pin, chamber sensor/control (+ legacy box mirrors), telemetry promotions (`targetBoxTemp` -> control, `boxTemp`/`maxBoxTemp` -> sensor, `lightSw` -> light), `_cached_cfs_detected`, max bed/nozzle/chamber temps, and `_cached_camera_type` **only if it was never set** (`:350-359`). Nothing is written if no frame arrived.
- Offline path (`:369-410`): bump `_cached_version`; on a first-ever setup write defaults (`K by Creality`, light on, no chamber, camera `mjpeg`); otherwise derive brightness keys from the cached model.

Consumers: `KEntity._get_cached_device_info` / `device_info` (`entity.py:43-55`, `:98-130`), `_get_cached_max_temps` (`entity.py:57-96`, per-field fallback to live), `number.py:49-75`, `camera.py:1525`. Because re-caching is keyed on the integration version, a printer firmware update does not refresh `_cached_model_version`, and an auto-detected camera type is never revisited.

---

## 7. Setup, reload, unload, removal

### 7.1 `async_setup_entry` (`__init__.py:181-505`)

1. Core version gate -> `ConfigEntryError` with translation key `unsupported_ha_version` (`:188-208`).
2. `_migrate_go2rtc_settings` (`:211`, `:102-161`): power-switch enabled flag, go2rtc url/port moved from data to options, removal of the 0.9.0 `localhost:11984` defaults.
3. Effective power switch = option entity only when the enabled flag is set (`:216-218`); build `KCoordinator` (`:223-225`).
4. `async_start()`, then `entry.async_on_unload(coord.async_stop)` at once, so a later failure in setup cannot leave the client running (R31). No wait here since R31: the entities come from the capability cache and late discovery; the only wait (15 s, once) is in the re-cache block, when there is something to learn. Before R31 every setup with the printer unreachable sat 15 s (measured on the test box: 15.0 s, now 0.27 s). An offline printer still always sets up and shows unavailable; `ConfigEntryNotReady` is only for `async_start` itself raising.
5. Version, `_last_ip`, capability cache (section 6.2).
6. `hass.data[DOMAIN][entry_id] = coord` (`:412`).
7. Card registration (`:417-421`, section 10), non-fatal.
8. `entry.async_on_unload` for: the options update listener (`:424`), the `mobile_app_notification_action` bus listener (`:430-437`), the 5 s interval (`:453-456`), the power-switch watcher (`:468-469`).
9. Legacy entity removal (`:472-491`): `switch.<host>-light`, three fan-percentage numbers, `sensor.<host>-system`.
10. `async_forward_entry_setups(entry, PLATFORMS)` (`:493`).
11. (Before R34: register the actions here. Since R34 `async_setup` registers all three once, before any entry is set up, with `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`; a failing entry no longer hides them.)

Since R31 `coord.async_stop` is registered with `entry.async_on_unload` right after start, so a setup that raises later (for example `request_boxs_info` hitting `RuntimeError("WebSocket not connected")` after a drop) no longer leaves the `KClient` task running.

### 7.2 Options and reload (`__init__.py:971-1010`, `coordinator.py:1880-1922`)

`options_update_listener` first asks `notifications_only_change(entry.options)`: the set of keys that differ from the options the coordinator was built from must be non-empty and inside `NOTIFY_ONLY_OPTION_KEYS` (`const.py:204-219`). Then `apply_notification_options` applies in place and resyncs the live card. Anything else (including a change to `entry.data` only, since HA fires update listeners for data changes too) calls `notify_options_changed` and reloads, retrying `OperationNotAllowed` 3 times 0.5 s apart.

Writers of `entry.data` after setup, each of which therefore reloads the entry: the options flow host field (`config_flow.py:795-802`, `:301-303`), the zeroconf MAC branch (`config_flow.py:156-162`, which also schedules its own reload, so two reloads), and `async_remove_config_entry_device` (`__init__.py:1095-1104`).

### 7.3 Unload, removal, device removal

- `async_unload_entry`: unloads the platforms first; only when that succeeds does it stop the client and pop `hass.data` (R31; before, a failed unload left entities on a stopped client). `entry.async_on_unload(coord.async_stop)` also covers a setup that fails after the client started. Static paths and Lovelace resources stay.
- `async_remove_entry` (`:1011-1053`): sends the dismiss sentinel for `_live`, `_soon`, `_alert` tags to every mobile target (the only teardown that clears phones).
- `async_remove_config_entry_device` (`:1073-1112`): strips every `_cached_*` key and `_device_info_cached` and returns True. The data write reloads the entry, which re-caches and re-creates the same device.

---

## 8. Services (`__init__.py:543-965`)

| Service | Schema | Behaviour |
|---|---|---|
| `request_cfs_info` | `device_id` optional (schema since R34: a string or a list; other keys rejected) | `boxsInfo` GET to the targeted printers (all when empty); persistent notification naming the printers asked and the ones not reached (translated `common.cfs_info_*`, R33). |
| `set_cfs_material` | `__init__.py:715-740` | requires `device_id`; validates via `build_modify_material_payload`; refuses if any target is busy per `derive_activity_state`; sends `modifyMaterial`, then logs the echo 3 s later. Errors are `ServiceValidationError` with inline English. |
| `diagnostic_dump` | `include_sensitive_data` (ignored) | builds a JSON blob per printer (options subset, cache, WS stats, telemetry, model and feature detection, CFS raw, every entity's state and attributes), crawls `https://` and `http://` of the printer root for same-host links, logs it at WARNING, posts a persistent notification. No response data, no redaction. |

Targets resolve through `_coordinators_for_devices` (`:508-540`), device registry -> config entry ids -> coordinators in `hass.data`.

---

## 9. Zeroconf discovery

- Matchers (`manifest.json:17-24`): `_http._tcp.local.` and `_workstation._tcp.local.` with names `*creality*`, `*k1*`, `*k2*`.
- `extract_info_from_zeroconf` (`utils.py:97-189`): prefers a routable IPv4, then any IPv4, then the first address, then the hostname; a MAC only from properties `mac`/`device_mac`/`serial`.
- `async_step_zeroconf`: rewritten for R4; see `02-entities-camera-config.md` section 10.2 (unique-id check before probing, MAC or hostname match moves an existing entry, a confirmation step before anything is created) and 10.2a (`_async_follow_host`).
- `async_step_user` (`config_flow.py:113-134`): unique id is the typed host string.

---

## 10. Static paths and Lovelace resources (`frontend.py`)

- `_register_static_path` (`frontend.py:60-101`): once per process via the `hass.data["ha_creality_ws_static_paths"]` set (`:21`, `:83-86`); the actual `async_register_static_paths` runs in an HA task and drops the marker on failure.
- Paths: `/ha_creality_ws/k_printer_card.js`, `/ha_creality_ws/k_cfs_card.js` (cache headers on, `?v=<sha256(manifest version + file)[:10]>` from `card_version`, `:23-57`, hashed in the executor), `/ha_creality_ws/cfs_box.webp`, and the directory `/ha_creality_ws/i18n` (cache headers off).
- Lovelace: `_init_resource` (`:104-156`) upserts one `module` resource per card in storage mode; `_migrate_local_resources` (`:159-209`) rewrites old `/local/ha_creality_ws/...` entries; `_expand_base_resource` (`:303-371`) expands a bare `/ha_creality_ws/` entry into per-card URLs. Old `/config/www/ha_creality_ws/<card>` copies are deleted (`:239-248`, on the loop).
- Runs on every entry setup and reload (`__init__.py:417-421`).

---

## 11. Threading and async model

- Everything runs on the HA event loop. The only executor work in this slice is reading `manifest.json` (`__init__.py:93-95`) and hashing the cards (`frontend.py:233`).
- Per printer: one `asyncio.create_task` loop (`ws_client.py:107`) plus two per-connection child tasks (`:239-240`), all plain asyncio tasks that HA does not track.
- `_handle_message` runs inside the receive loop, so the frame path is single-threaded and strictly ordered; nothing on it may await network I/O. Notification sends, the Moonraker poll and the material echo are `hass.async_create_task`.
- Timers: one `async_track_time_interval` (5 s); the dispatcher signal for late discovery is handled by `@callback`s that defer `async_add_entities` with `hass.loop.call_soon` (`number.py:97-104`).
- Two clocks: `time.monotonic()` in `ws_client.py`, `hass.loop.time()` in `coordinator.py`; they agree only because asyncio's default loop uses `time.monotonic()`.

---

## 12. Timers and constants

| Constant | Value | Where |
|---|---|---|
| `WS_PORT` / `WS_SUBPROTOCOL` | 9999 / `wsslicer` | `const.py:20`, `:27` |
| `MJPEG_PORT`, `WEBRTC_PORT` | 8080, 8000 | `const.py:21`, `:31` |
| `STALE_AFTER_SECS` | 15 s | `const.py:48` |
| `RETRY_MIN_BACKOFF` / `RETRY_MAX_BACKOFF` / multiplier | 1.0 s / 300 s / 1.8 (+U(0,0.4)) | `const.py:49-51`, `ws_client.py:345` |
| fixed retry without power switch | 60 s after 5 failures | `ws_client.py:188`, `:341-342` |
| `STABLE_CONNECT_SECS` | 10 s | `ws_client.py:39` |
| `HEARTBEAT_SECS` / `PROBE_ON_SILENCE_SECS` | 10 s / 10 s; dead at > 30 s silence | `const.py:52-53`, `ws_client.py:431` |
| websockets `open_timeout` / `close_timeout` / `max_size` | 10 s / 10 s (library defaults) / 16 MiB (`WS_MAX_MESSAGE_BYTES`, R15) | `ws_client.py` |
| GET cadences | ReqPrinterPara 5 s, reqPrintObjects 2 s, boxsInfo 300 s; tick 0.2 s; first after 2 s | `ws_client.py:33-35`, `:444`, `:493` |
| force-connect power-off sleep | 10 s | `ws_client.py:212` |
| `send_set_retry` reconnect wait | 6 s | `ws_client.py:509` |
| interval check | 5 s | `__init__.py:454` |
| setup waits | none normally; re-cache only: first connect 15 s, then fields 6 + 5 + 2 s (R31) | `__init__.py` re-cache block |
| `ensure_connected` wait | 10 s | `coordinator.py:344` |
| `GCODE_INFO_RETRY_SECS` / `GCODE_INFO_MAX_ATTEMPTS` | 30 s / 3 | `const.py:89-90` |
| `MR_PORT` / `MR_POLL_INTERVAL` / `MR_POLL_TIMEOUT` | 7125 / 30 s / 5 s | `const.py:318-320` |
| `NOTIFY_PRIME_GRACE_SECS` | 10 s | `const.py:128` |
| `NOTIFY_END_CONFIRM_SECS` | 15 s | `const.py:148` |
| `NOTIFY_REARM_PROGRESS_MAX` | 90 % | `const.py:136` |
| `NOTIFY_LIVE_INTERVAL_SECS` / `MIN_INTERVAL` / `STALE_CLEAR` | 300 s / 30 s / 90 s | `const.py:250`, `:253`, `:271` |
| `NOTIFY_LIVE_MAX_PUSHES_PER_JOB` / iOS expiry | 600 / 8 h | `const.py:263`, `:269` |
| options reload retry | 3 x 0.5 s | `__init__.py:995-1007` |
| `DEFAULT_POLLING_RATE` | 0 (every frame) | `const.py:315` |
| `MINIMUM_HA_VERSION` | (2026, 7) | `const.py:18` |

---

## 13. Traps for future maintainers

1. **Frames are cumulative.** `on_message` receives the client's whole merged state each time (`ws_client.py:275`). "Is this key in the payload" means "has it ever been seen on this client", not "did this frame carry it". Editing the frame (the K2 Base pop) does not edit `_state`.
2. **The frame handler is inline in the receive loop.** Awaiting network I/O in `_handle_message` stalls RX, `last_rx` goes stale and every entity flips unavailable at 15 s (`coordinator.py:2127-2131`).
3. **`power_is_off()` means "switch off and not connected".** It cannot detect the moment a plug cuts a connected printer (section 4.3).
4. **Backoff state lives in `_loop` locals** and survives across sessions that end in an exception (section 2.2). Only `stop()`/`start()` gives a clean slate.
5. **Late discovery fires once per field per coordinator.** Writing a gating field straight into `self.data` consumes the one-shot silently (`coordinator.py:577-582`).
6. **Unique ids and the device identifier are the host string** (`entity.py:23`, `:105`, `:123`; `coordinator.py:1240`). Changing the host in options mints a new id for every entity and a new device.
7. **Any `entry.data` write reloads the entry** once the update listener is registered (section 7.2).
8. **The capability cache refreshes on integration version changes, not printer changes** (section 6.2). The camera type is cached once and kept across upgrades.
9. **`derive_print_state` is the single state table.** Gate actions on `derive_activity_state` (stale errors collapsed); display uses `derive_print_state`.
10. **Progress fallback must use `is None`, not `or`** (`coordinator.py:853-859`). `sensor.py:286` still uses `or`.
11. **`coerce_numbers` keeps `""`.** A numeric sensor returning the raw value then raises in core (seen in the #121 log for `bedTemp0`/`nozzleTemp`).
12. **No `_async_update_data`.** Anything that asks the coordinator to refresh hits core's `NotImplementedError` path.
13. **Mixed line endings in the repo** (no `.gitattributes`): edit existing files without text-mode rewrites.
