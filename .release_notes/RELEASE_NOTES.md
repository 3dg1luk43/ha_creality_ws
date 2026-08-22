# Changelog

All notable changes to HA Creality WS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.9.9] - 2026-08-22
> [List of issues (0.9.9)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.9)

Salvages the CFS material-editing work from [#75](https://github.com/3dg1luk43/ha_creality_ws/pull/75) (thanks **@buzato**), brought up to current code and reworked. The branch descends from his commits, so his authorship is intact.

### Added
- **Edit filament from the CFS card.** Each slot tile gains an edit button opening a dialog for material type, name, vendor, colour, temperature range and pressure advance. Saving writes to the printer and then asks it to re-report, so the tile only changes once the write has actually landed.
  - The dialog states which **box and slot** it will write to, and says so explicitly when it had to infer the target from the card layout rather than read it from the printer.
  - Editing is disabled while the printer is **busy**, and for cards whose entities span **more than one printer** (there would be no way to tell which machine to write to).
  - **Multi-colour spools** show their colour read-only. The printer reports two values for them and a single colour cannot represent them, so the rest of the fields save and the colour is left alone.
  - **Colour presets**: Creality's standard palette plus your own, kept in your browser rather than in the dashboard config.
- **`ha_creality_ws.set_cfs_material` service** for writing filament metadata to a slot, usable from automations as well as the card. Only the fields you supply are changed — the printer merges into the slot it already has, so leaving `rfid` empty preserves the existing tag association.
- **New CFS slot attributes**: `box_id`, `slot_id`, `min_temp`, `max_temp` and `pressure`. The first two are how the service addresses a slot; the rest are what the edit dialog prefills from. Not every printer reports the temperatures on every slot, so treat `null` as "unknown" rather than zero.
- **Third card display mode, `box`**: a photo of the CFS unit with a spool overlay per bay. Requires a mapped four-slot box and falls back to the full view otherwise, since the overlay geometry is tied to that image.

### Changed
- **The card's `compact_view` option became `view_mode`** (`full` | `compact` | `box`). Existing dashboards migrate automatically on load, and the old key is dropped the next time you edit the card. No action needed.
- **The card only re-renders when something it displays has actually changed**, instead of on every state update.
- **Printer status is derived in one place** now, shared by the status sensor and the service's "is it safe to write" check, so the card and the service cannot disagree about whether the printer is busy.
- **The bundled CFS unit image is 32 kB instead of 509 kB** (WebP). The whole `www/` directory had been 143 kB, so as a PNG this one decorative asset would have made every install over four times larger.

### Notes for anyone with CFS hardware
Creality does not document the `modifyMaterial` command. The payload shape comes from @buzato's testing against a real CFS, and is verified here against the bundled printer simulator — but two details are still unconfirmed: the printer *streams* colours as seven hex characters yet appears to accept six on write, and the `rfid` field name is inferred from telemetry rather than from a confirmed dump. Every write logs both the outgoing payload and what the printer reports back afterwards. **If a material edit does something unexpected, please open an issue with that part of your debug log** — that is what will settle these.

## [0.9.8] - 2026-08-22
> [List of issues (0.9.8)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.8)

### Added
- **`spool_key` attribute on every CFS slot** (closes #117 part 2):
  - The printer's `rfid` field is a material/filament id, not a tag serial, so two spools of the same vendor and material share it even when their colours differ — external trackers such as spoolman-sync could not tell them apart. Each slot (and the external filament) now also exposes `spool_key`, which combines that id with the normalised colour, so four slots of `Creality Hyper PLA` in different colours get four distinct keys.
  - `spool_key` is a **derived** identifier, not new telemetry: the printer streams no per-tag serial, so two genuinely identical spools still produce the same key. The raw `rfid` value is passed through unchanged.
  - Multi-colour spools collapse to a single flat token (`generic-pla-silk_ffa800-ff97e1`), and colours the printer reports as sentinels (`N/A`, empty) are left out of the key entirely.
- **`color_hex_raw` attribute on every CFS slot**: the printer's original colour string, kept alongside the corrected `color_hex` for reference and debugging.
- **Optional go2rtc RTSP port** under *Configure → Camera*: only needed if your go2rtc listens on a non-default RTSP port. `0` (the default) keeps auto-detection.
- **Fan documentation**: the `fan.*_model_fan` / `*_case_fan` / `*_side_fan` entities have always supported on/off and speed control, but were undocumented (#114). The README now covers them, including a chamber-too-hot automation example.

### Fixed
- **Camera stream API (HLS, recording, casting) failed with `TypeError: 'str' object is not callable`** (closes #116, thanks @Raymondvb1985):
  - `stream_source` was defined as a synchronous property returning the go2rtc *stream name*, which shadowed HA core's `async def stream_source()`. Every consumer of the classic stream pipeline — the `camera/stream` WebSocket command, HLS playback, `camera.record`, `camera.play_stream`, casting — does `source = await self.stream_source()`, so it tried to call a string.
  - It is now an async method returning an RTSP URL on the same go2rtc instance, which HA's `stream` component can actually ingest. The port is detected automatically (`18554` for HA's built-in go2rtc, `8554` for a stand-alone one) and can be overridden in the options flow. WebRTC playback in the frontend is unchanged; "WebRTC direct" cameras still have no HLS source, as they never register a go2rtc stream.
- **Wrong CFS spool colour: leading pad character was kept** (closes #113 and #117 part 1, thanks @raf802):
  - Creality RFID tags store the colour as *seven* hex characters — one padding character followed by the real `RRGGBB` — and the printer streams that verbatim. Reading the first six digits produced the wrong colour, e.g. `#0ffffff` reported as `#0ffffff` instead of `#ffffff`.
  - The colour sensors and the `color_hex` attribute now keep the **last** six digits and normalise to lowercase `#rrggbb`. Values that are not recognisable hex (`N/A`, named colours, empty) are left untouched, and multi-colour values are normalised element-wise. The CFS card already compensated for this, so its rendering is unchanged.
- **`Generic Generic PLA` in the filament label** (closes #115):
  - The filament sensors joined the vendor and the material name unconditionally, but the printer frequently repeats the vendor inside the name (vendor `Generic`, name `Generic PLA`). The vendor is now only prepended when the name does not already start with it.
  - **⚠️ A vendor the printer never reported is no longer invented.** When the telemetry carries no `vendor` at all, the old code substituted the literal string `Generic`, so a slot reporting only `PETG` was labelled `Generic PETG`. The label now falls back to the material name, then the material type. If you match on the old value in a template or automation, update it.
  - A completely empty slot no longer produces the malformed state `'Generic '` (with a trailing space); it reports `Unknown`, which both the CFS card and Home Assistant render as `—`.
- **"Print completed" notification on every Home Assistant restart** (closes #112, thanks @chairstacker):
  - The printer keeps reporting the finished job's file name and 100% progress indefinitely, so a freshly started coordinator read that stale state as a brand new completion and notified about it — on every restart and every config-entry reload.
  - Notifications are now baselined on startup: the first telemetry frame that carries the print state is recorded silently (with a 10 s grace window for printers that report neither field), so only a genuine transition after that notifies. The same guard applies to the error, filament-runout and minutes-to-end notifications.
- **Minutes-to-end notification never fired**: it read `printTimeLeft`, but the printer streams the remaining time as `printLeftTime`, so the value was always absent. Found while fixing #112.
- **Chamber-target control missing after a restart while the printer was off**:
  - `number.<printer>_chamber_target` was only created if the printer had already reported `maxBoxTemp` at the moment the `number` platform was set up. Platform setup deliberately does not wait for the printer (an offline printer must not block the config entry), so restarting Home Assistant while the printer was off left the entity uncreated — and nothing recreated it when the printer came back, so it sat `unavailable` until a restart that happened to win the race.
  - The control is now satisfied by the chamber capability cached during onboarding, and the coordinator additionally fires a discovery signal the first time any gating telemetry field appears, so late-arriving capabilities create their entities without a restart. Found while verifying #112/#114 against a live printer.
- **Dynamic CFS discovery relied on a swallowed error**: the late-discovery handler wrapped `async_add_entities` in a coroutine and awaited it. `async_add_entities` is a synchronous callback returning `None`, so the await raised `TypeError` every time — after the entities had been added, with the exception discarded because nothing held the resulting future. Both platforms now schedule the callback on the event loop instead, which is also what stops the entity-add task being destroyed mid-flight.
- **Completion notification only ever arrived once per file name**: the "already notified" flag was only cleared when the print file name changed, so reprinting the same file never notified again. It is now also re-armed whenever progress falls back below 100%. Found while verifying #112 against a live printer.

### Internal
- `LATE_DISCOVERY_FIELDS` in `const.py` lists the telemetry fields that gate entity creation (`boxsInfo`, `maxBoxTemp`); the coordinator fires a single discovery signal the first time each appears, replacing the CFS-only trigger. Platforms subscribe and re-check idempotently.
- New shared CFS helpers in `utils.py` — `normalize_color_hex`, `format_filament_label`, `build_spool_key` — replacing the duplicated inline logic in `KCFSSlotSensor`, `KCFSExtSlotSensor` and `KActiveFilamentSensor`, whose attribute dicts now come from one `_cfs_slot_attributes` builder.
- Regression tests added for all of the above (`test_cfs_filament.py`, `test_cfs_sensors.py`, `test_notifications.py`, `test_fan.py`, plus new `stream_source` cases in `test_camera_stream_config.py`).

### Test server (`tools/creality_printer_test_server.py`)

Several fidelity gaps made the simulator disagree with real hardware, which hid working behaviour and invented broken behaviour. All of these are dev-tooling only.

- **Fan telemetry used names the integration never reads**: it emitted `caseFan` / `modelFan` / `sideFan` where the printer sends `modelFanPct` / `caseFanPct` / `auxiliaryFanPct`, so fan entities always looked stuck at 0. It now emits the real names, honours `M106 P<ch> S<0-255>` arriving over `gcodeCmd`, and leaves a manually driven fan alone instead of overwriting it with jitter.
- **Video was answered as VP8**: aiortc's default capability order puts VP8 first, and Home Assistant's `stream` component cannot package VP8 into HLS, so the playlist blocked forever. Real K-series printers send H.264, so the simulator now answers H.264 first (`--prefer-codec`, default `h264`).
- **Keyframes were up to 25 s apart**: aiortc's H.264 encoder inherits libx264's 250-frame keyframe interval, and HA's stream worker gives up long before that. Video is now pre-encoded with a 1 s GOP and sent through as packets, bypassing aiortc's encoder (`--video-source auto`).
- **Healthy WebRTC sessions were killed after 60 s** by an unconditional sleep-then-close, which made every consumer reconnect in a loop. Teardown now follows the connection state.
- **`--deterministic`** removes all randomness (temperature oscillation, fan jitter, XYZ drift) so telemetry is reproducible — that is what makes an entity-state diff between two integration versions usable as a regression check. The print-progress fields (`printProgress`, `printJobTime`, `printLeftTime`, `layer`, `usedMaterialLength`) stay derived from elapsed wall-clock time, so they still depend on when you sample them.
- **`--cfs-variant edge`** adds the awkward CFS payloads: an already-correct six-character colour, a slot with no vendor, a multi-colour spool, shared `rfid` values across colours, and an empty external slot.
- **Test-control endpoints** (`POST /test/set`, `/test/reset`, `/test/cfs`, `GET /test/state`) pin any telemetry field on demand, so notification scenarios (completion, error, runout, minutes-to-end) can be driven in seconds instead of waiting out a simulated print. Real printers have no such endpoints.
- Log lines now carry timestamps, and the offer/answer SDP is dumped under `--debug`.


## [0.9.7] - 2026-07-28
> [List of issues (0.9.7)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.7)

### Added
- **Localization / i18n support** (#104, thanks @oscfdezdz / Óscar Fernández Díaz):
  - Entities, services, and both Lovelace cards (printer + CFS) are now fully translatable via `strings.json` / `translations/*.json` and bundled `www/i18n/*.json`, shipping with English and Spanish.
  - Entity classes use `_attr_translation_key` instead of hardcoded names; the cards resolve strings through `hass.localize` with a bundled JSON fallback and automatic language fallback while backend translations load.
- **Dimmable chamber light** (#108, closes #102, thanks @RobertJansen1):
  - The chamber LED is now exposed as a brightness-capable light on printers with confirmed PWM support (**K2 Pro**, **K2 Plus**), matching the 0–100% slider in the printer's local UI.
  - Brightness capability is model-detected (`LED_PIN_BY_MODEL`); every other printer keeps the plain on/off light. Because the firmware only reports on/off, the dim level is remembered locally, and the capability migrates onto existing config entries — even while the printer is offline — from cached model info.
- **Power-off confirmation on the printer card** (#110, closes #101):
  - The card's power button now asks for confirmation before turning the printer **off** (never when turning it on), with a stronger warning while a print is in progress so an accidental tap can't kill a running job.

### Changed
- **⚠️ Breaking — `filament_status` now reports lowercase slugs** (#104):
  - As part of state translation, the **Filament Status** sensor now reports `normal` / `runout` instead of `Normal` / `Filament Runout`. Any automation or template matching the old capitalized values must be updated — the displayed (translated) label is unchanged.
- **Print Tuning range raised to 200%** (#107, thanks @RobertJansen1):
  - The **Print Tuning %** slider now goes up to 200% (was 100%), matching the feedrate/flowrate hardware limit so the control is actually usable.
- **Printer-card display refinements** (#110, closes #101, #103):
  - The layer readout shows `—/—` (consistent with the temperature pills) instead of `?/?` when the printer is off or idle.
  - The chamber-temperature pill is hidden automatically when no chamber entity is configured (or it is absent from HA), so chamber-less printers such as the Ender 3 V3 KE no longer render a stray thermometer icon.

### Fixed
- **`Current Object` sensor crash-looping the log** (#110, closes #106):
  - `'int' object has no attribute 'strip'` was raised roughly every 5 seconds when the firmware reports `currentObject` as a non-string. The whitespace check is now guarded so only strings are stripped; all other outputs are unchanged.
- **Fluctuating fractional print-time on the card** (#110, closes #103):
  - Time-remaining rendered many-digit fractional seconds (e.g. `2:25.6789`) and reflowed the adjacent telemetry every poll. It is now floored to whole seconds, which also stabilizes the row layout.

### Internal
- **Entity/translation hardening** (#104): `KEntity` now requires a non-empty `unique_id` and honors class-level translation keys; camera translation keys moved onto the concrete camera classes.
- **LED-brightness plumbing** (#108): added the `LED_PIN_BY_MODEL` capability table and brightness-capability caching/migration, plus `ClassVar` and keyword-only-argument cleanups.


## [0.9.6.1] - 2026-06-11
> [List of issues (0.9.6.1)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.6.1)

### Fixed
- **CFS sensors stuck unavailable after upgrading to 0.9.6** (#99, regression):
  - All CFS sensors (box temp/humidity, every slot's filament/color/percent, the external slot) and the **Active Filament Slot** sensor stayed `unavailable` after a few minutes and never recovered, even across Home Assistant and printer restarts. Rolling back to 0.9.4 worked around it.
  - `boxsInfo` (the CFS payload) is only sent by the printer in response to an explicit poll, never in the regular telemetry stream. The 0.9.6 rework of the periodic-GET loop started its poll timers at the current time instead of zero, which pushed the **first** CFS poll a full 5 minutes (`GET_BOXS_INFO_SEC`) past connect. Because every reconnect restarts that timer, any printer that dropped and reconnected more often than every 5 minutes never got polled at all — so the CFS entities were never discovered and sat unavailable.
  - The first poll of each periodic GET (printer params, print objects, and CFS box info) now fires immediately once the connection is ready, restoring the pre-0.9.6 behavior. The 5-minute steady-state cadence for CFS is unchanged, and the 0.9.6 "availability requires real data" gate still applies.


## [0.9.6] - 2026-06-10
> [List of issues (0.9.6)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.6)

### Added
- **Direct WebRTC camera mode** (idea recycled from #95, thanks @erus71an):
  - New opt-in **"WebRTC direct, no go2rtc"** camera mode that signals the printer's WebRTC endpoint directly from Home Assistant (browser-style base64-JSON SDP on `:8000/call/webrtc_local`), bypassing go2rtc entirely.
  - Available as an alternative for any WebRTC-capable printer — try it if the default go2rtc path doesn't work on your firmware (e.g. newer K1C). The K2-family go2rtc path is unchanged and remains the default.
  - The frontend offer is reduced to video-only, wrapped/unwrapped as the printer expects, and the answer's media-line order is rebuilt to match Home Assistant's original offer.
- **Custom camera URL mode** (#92, thanks @OptimusGREEN):
  - New **"Custom camera URL"** mode to point the camera at any external stream — `http(s)` MJPEG/snapshot URLs are served directly, and `rtsp://` (and similar) streams are ingested through go2rtc.
  - Useful for printers without a built-in camera, or to substitute an existing IP camera.
- **Spanish translations** (`es.json`, thanks @ofdezdz / Óscar Fernández Díaz):
  - Adds a full Spanish translation of the integration's config and options UI, extended to cover the reorganized options menu and the new custom-camera-URL field.

### Changed
- **Options flow reorganized into a menu**:
  - The printer Options dialog is now a menu with **Camera**, **Notifications**, **Power switch**, and **Connection & performance** sub-pages.
  - Each sub-page loads fresh, so mode-dependent fields are always correct — previously a single combined page couldn't re-render when the camera mode changed, leaving stale/irrelevant fields (e.g. go2rtc fields showing while "Custom URL" was selected).
  - Submitting a sub-page stages its changes and returns you to the menu (the back arrow returns without staging); **Save and apply** writes everything at once, so the printer reloads only once no matter how many sections you edit.
- **Camera options refresh**:
  - The camera-mode dropdown now lists Auto, MJPEG, WebRTC (go2rtc), WebRTC direct, and Custom URL, with clearer labels and help text.
  - The custom-URL field appears when the custom mode is selected, with validation for a complete URL.
  - Auto-detection now probes both `/call` and `/call/webrtc_local` WebRTC signaling endpoints.
- **WebSocket handshake parity**:
  - The client now advertises the printer web UI's `wsslicer` subprotocol on the WebSocket handshake. Servers that don't use it simply ignore it (RFC 6455), so this is compatible with existing printers.

### Fixed
- **WebSocket availability now requires real data**:
  - The printer is reported available/connected only after the first valid telemetry frame, not on the bare TCP/WebSocket handshake — preventing brief "available" flashes against a socket that never streams data.
  - Reconnect backoff is reset only after a connection survives at least 10 seconds, so rapid connect/drop flapping keeps backing off instead of masquerading as healthy reconnects.
  - The heartbeat watchdog measures silence from the connect time until the first frame, avoiding a premature "connection dead" verdict right after connecting.

### Internal
- **#88 diagnostics**: added debug logging of the negotiated WebRTC video codec/profile on stream start, to help diagnose the brief pre-keyframe visual artifact reported on some K2 cameras (no behavior change).


## [0.9.5] - 2026-05-22
> [List of issues (0.9.5)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.5)

### Fixed
- **K2 WebRTC Camera Instability after 0.9.4** (#88 follow-up):
  - Fixed an `'Stream' object has no attribute 'get'` error that fired on every snapshot and WebRTC offer when the go2rtc stream already existed.
  - The 0.9.4 "verify existing source matches" check incorrectly treated `streams.list()` entries as dicts; the `go2rtc-client` library actually returns `Stream` dataclasses with a `producers: list[Producer]` field. The misuse raised on every call, was swallowed by the generic exception handler, and forced a delete-and-recreate of the active stream on every snapshot / offer — producing the flicker, repeated 30s loads, and intermittent unavailability reported on 0.9.4.
  - The verification now inspects `Stream.producers[*].url`, so the upgrade-path self-healing (recreate a leftover stream that points at a wrong source) is preserved without breaking the steady state.

### Changed
- **Printer Card Sizing for Wrapped Telemetry** (#91, thanks @Ahmed-max):
  - The printer Lovelace card now reports a larger card size when its telemetry pills naturally wrap onto a second line, so Home Assistant reserves the right amount of vertical space and the next dashboard tile no longer overlaps the wrapped row.
  - Added a debounced `ResizeObserver` on the telemetry area; observers and timers are cleaned up on disconnect. Wrapping behavior itself is unchanged.

### Internal
- **Shutdown Robustness**:
  - WebSocket client shutdown now reliably ignores expected `asyncio.CancelledError` during teardown so it no longer surfaces as a spurious error in logs.

### Testing
- Added regression tests covering the `Stream` dataclass shape returned by `go2rtc_client.streams.list()` — both "existing stream with correct source is left alone" and "existing stream with stale source is recreated".
- Stabilized the static pytest suite by reading source files as UTF-8 and added layout tests for telemetry-driven card sizing.


## [0.9.4] - 2026-05-21
> [List of issues (0.9.4)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.4)

### Fixed
- **K2 WebRTC Camera Regression** (#87, #88):
  - Restored the `#format=creality` go2rtc source fragment for K2 family WebRTC cameras, which selects go2rtc's built-in Creality JSON-wrapped SDP client.
  - In 0.9.3 the fragment was dropped on the assumption it was no longer needed, but the K2/K2 Pro/K2 Combo signaling endpoint at `:8000/call/webrtc_local` does not speak standard WHEP — it replies with `{}` to raw SDP offers, which made go2rtc fail with `sdp: syntax error at pos 1: "}"` and caused the camera entity to become unavailable.
  - 0.9.3 users seeing "Failed to start WebRTC stream: go2rtc error" or a permanently unavailable camera entity should be fixed by upgrading to 0.9.4 without any config changes.


## [0.9.3] - 2026-05-20
> [List of issues (0.9.3)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.3)

### Added
- **Active Filament Slot Sensor** (#80):
  - Added a new sensor that reports the currently selected CFS source as `Box X Slot Y` or `External`.
  - Exposes extra active-filament metadata when available, including filament/vendor name, color, and remaining percentage.

### Changed
- **WebRTC Stream Provisioning**:
  - Updated go2rtc stream configuration for K2/WebRTC cameras to use the standard `webrtc:` source format for improved compatibility with newer go2rtc versions.
  - Hardened stream lifecycle handling so stream names are reused more predictably across retries and recovery paths.
  - Existing go2rtc streams are now validated against the expected source and recreated when stale or mismatched.
- **Lovelace Card Styling** (#73):
  - Refined CFS card layout, spacing, and Home Assistant theme inheritance for better visual consistency.
  - Added dynamic card sizing/layout reporting for the CFS card to improve dashboard placement, especially in compact mode.

### Fixed
- **Manual Reconnect Reliability** (#81):
  - Fixed reconnect flow issues where manual reconnect attempts could fail to restore the WebSocket connection.
- **Power-Off Reconnect Noise** (#84):
  - Suppressed repeated mDNS fallback warning spam when a configured power switch reports the printer is intentionally off.
  - Cleaned up reconnect logging so fallback behavior remains visible without producing noisy or misleading warnings.
- **WebRTC Error Recovery**:
  - Improved recovery after go2rtc/WebRTC offer failures by invalidating bad streams and forcing reconfiguration on the next attempt.
  - Fixed cleanup paths so stream recreation remains possible even when deleting the old stream fails.
  - Fixed state handling during stream setup so cancellations or exceptions do not leave the camera marked as configured prematurely.
  - Improved error messages around WebRTC offer forwarding and stream management to make diagnostics more actionable.
- **go2rtc Stream Consistency**:
  - Fixed cases where an existing go2rtc stream could be reused even though it pointed at an outdated or incorrect source.
  - Existing streams are now recreated when their configured source does not match the printer's expected upstream signaling URL.
- **Printer Card Mobile Layout** (#72):
  - Fixed action chips and telemetry pills being cropped on smaller screens by allowing them to wrap correctly.
- **CFS Card Theme Compatibility** (#77):
  - Fixed spool/ring rendering on certain Home Assistant themes where the card could appear white or visually inconsistent.
  - Improved transparency and masking behavior so the CFS card better matches themed card backgrounds.
- **CFS Data Handling**:
  - Improved CFS slot/box handling and sensor registration robustness, including cleaner box ID handling and late-entity creation behavior.

### Testing
- **Regression Coverage** (#82):
  - Added focused tests for WebSocket reconnect behavior, WebRTC error recovery, stream configuration, and previously reported WebRTC failure scenarios.
  - Updated async test setup to improve reliability and remove older pytest configuration issues.


## [0.9.2] - 2026-01-27
> [List of issues (0.9.2)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.2

### Added
- **CFS Card Enhancement** (#70):
  - **Mini Mode Filament Type**: Added a new valid option to show the filament material type (e.g., PLA, ASA) in the compact "Mini Mode" view.
  - **Improved Rendering**: Enhanced the visual rendering of mini spools and improved click target areas for better usability.

## [0.9.1] - 2026-01-24
> [List of issues (0.9.1)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.1

### Added
- **Manual Reconnect Button**: Added a new `button` entity (`button.*_reconnect`) to force a WebSocket reconnection if the printer becomes unresponsive.
- **Service Targeting**: Added `device_id` selector to `request_cfs_info`, allowing users to target specific printers instead of all connected devices.
- **Service Feedback**: Added persistent notifications to `request_cfs_info` to confirm success/failure counts.

### Fixed
- **Startup Robustness**: Refactored the entire startup architecture.
  - Integration explicitly waits for `boxsInfo` (CFS) and chamber temps during setup, ensuring 100% entity coverage at booting.
  - Implemented a "hybrid" safety net: `sensor.py` retains a thread-safe dynamic loader to catch any entities that arrive late, preventing "Duplicate ID" errors.
- **Chamber Control**: Fixed missing "Chamber Target" entity for K2 Pro/Plus by auto-enabling control if the printer reports a target temperature, regardless of model detection defaults.
- **WebRTC Regression**: Fixed camera initialization failure when custom go2rtc settings were unreachable; added automatic fallback to discovery.
- **Service Stability**: Fixed crash in `request_cfs_info` when printer disconnected.

## [0.9.0] - 2026-01-23
> [List of issues (0.9.0)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.0

### Added
- **CFS Support (Creality Filament System)** (@buzato):
  - **Comprehensive Sensors**: Added sensors for each CFS box (temperature, humidity) and slot (filament type, color, percentage, active status).
  - **Native UI Card**: Introduced the **Creality CFS Card** with a built-in visual editor.
    - Renders tiles for all slots (up to 4 boxes x 4 slots) + external filament.
    - Dynamic UI: Active filament pulses, humidity color coding (Green/Orange/Red).
    - No YAML required: Fully configurable via entity mapping in the UI.
  - **New Services**: Added `request_cfs_info` (manual refresh), `cfs_load`, and `cfs_unload` for programmatic filament management.
- **Safety Features**:
  - **Confirmation Dialog**: Added a "double-check" modal for destructive actions like "Stop Print" to prevent accidental cancellations.

### Fixed
- **K2 Base Compatibility** (@PavelStoyan0v):
  - **Chamber Control**: Fixed chamber temperature control by implementing a Moonraker fallback for fetching accurate targets when the primary method fails.
  - **Data Accuracy**: Suppressed erroneous `targetBoxTemp:0` values.
  - **Threshold Removal**: Removed the hardcoded 40°C threshold for chamber heating, allowing for more flexible control.
- **go2rtc Custom Configuration**: Fixed an issue where custom go2rtc URL and Port settings were ignored.
- **Coordinator & Stability**: 
  - Refactored the central data coordinator for efficient high-frequency WebSocket updates.
  - Resolved merge conflicts and sync issues for reliable state tracking.
- **Frontend Assets**: Improved resource loading and fixed loading issues for custom card resources.

## [0.8.0] - 2026-01-05
> [List of issues (0.8.0)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.8.0

### Added
- **Diagnostics Service**: Enhanced `diagnostic_dump` service to include WebSocket connection health stats (`reconnect_count`, `msg_count`, `last_error`, `uptime`).
- **Notifications**: Added configurable notifications for print completion, errors, and time remaining (configurable via Options Flow).
- **Chamber Control for K2**: Enabled chamber temperature control for the base "K2" model.
- **Polling Rate**: New option to configure polling rate to reduce CPU usage. Throttling only applies **when the printer is actively printing**; idle/error states update immediately.
- **Translations**: Added `strings.json` and `en.json` for localization support.
- **Device Class**: Added `duration` device class to "Print Job Time" and "Print Time Left" sensors.

### Changed
- **Unavailable State**: Entities now report as `unavailable` when the printer is known to be powered off via the configured switch (static model info remains available).
- **Documentation**: Updated README to reflect K2 chamber support, K1C 2025 camera capabilities, and power switch configuration.

### Fixed
- **Connection Stability**: Slightly improved liveness detection and retry behavior.
  - Power-off check interval reduced to 10s (was 60s) for faster power-on detection.
  - Non-power-switch users utilize gradual backoff for initial failures (up to 5 attempts), transitioning to a fixed 60s retry mechanism for long-term idle detection.
  - Added application-level probes to detect and recover from stale WebSocket connections.
- **Log Noise**: Connection warnings are now limited to the first 3 failures; subsequent failures are logged as debug only to prevent spam when the printer is intentionally off.

## [0.7.1] - 2026-01-04
> [List of issues (0.7.1)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.7.1

### Added
- **Zeroconf**: Added improved Zeroconf discovery signatures for K2 and K1 series printers.

### Fixed
- Minor bug fixes and performance improvements.

## [0.7.0] - 2025-12-19
> [List of issues (0.7.0)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.7.0

### Added
- **Robust Network Management**: MAC-based discovery to automatically handle IP changes from DHCP reassignments.
- **Enhanced WebRTC Camera**: Uses official `go2rtc-client` Python library for robust stream configuration.
- **Intelligent Power-Off Detection**: Pauses connection attempts when printer power is OFF and auto-resets backoff on power return.
- **Card Customization**: New custom button targeting any entity type, with custom MDI icons for all buttons.
- **Domain Support**: Power & light controls now support `input_boolean` and `light` domains.

### Fixed
- Fixed `UnboundLocalError` in WebSocket reconnection timing logic.
- Improved `go2rtc` client error handling with descriptive messages.
- Refactored card event handling using event delegation.
- Enhanced Zeroconf flow with MAC address extraction and validation.

### Configuration Changes
- **Host/IP Update**: Host/IP is now editable from integration options.
- **Hide Chamber Temperature**: New option to toggle chamber temp pill visibility on card.
