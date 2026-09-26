# Changelog

All notable changes to HA Creality WS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.9.8] - 2026-09-26
> [List of issues (0.9.8)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.8)

> **Requires Home Assistant 2026.7.0.** The live print card needs the companion
> app's Live Activity support, added in that release. HACS will not offer this
> version to older cores, and a manual install fails setup with an explicit
> message. Stay on 0.9.7 if you cannot update.

### Added

- **Live print notifications for Android and iOS.** One self-updating card per printer: countdown to finish, progress bar, current layer, G-code preview as the icon. It ends itself when the print does, replaced by a completion notification carrying a camera snapshot.
  - The countdown runs on the phone, so it costs no pushes. State changes push immediately; the rest is rate limited (5 minute refresh, 1% of progress, 30 second floor) because iOS drops Live Activities that update too often.
  - **Multiple notify targets.** Companion-app targets get the full card; every other platform gets title and message only.
  - **Optional Pause / Resume / Stop buttons**, off by default. Stop requires device authentication.
  - **Tapping** opens the camera feed on Android. Set a dashboard path to change that, which is also the only way an iOS tap can deep-link.
- **Write your own notification text.** *Configure → Notifications → Custom text* overrides any of the six notifications; clear a field for the built-in wording.
  - Placeholders: `{device}`, `{filename}`, `{progress}`, `{layer}`, `{total_layers}`, `{eta}`, `{elapsed}`, `{filament}`, `{nozzle}`, `{bed}`, `{state}`, `{error_code}`, `{error_key}`, `{minutes}`. Each field lists the ones it accepts and rejects the rest when you submit. Values come from the frame that triggered the notification; nothing is estimated.
  - `[...]` marks an optional section that disappears whole while a value inside it is unknown, so `{filename}[ -- {eta} left]` loses the tail rather than leaving a dangling separator. Double them (`[[`, `]]`) for a literal bracket. `0` counts as a value.
  - The title is always the printer's name: Android fixes a live card's title when it starts.
- **Bus events** `ha_creality_ws_print_started`, `_print_finished`, `_print_error` and `_print_stopped`, fired whether or not a notify target is configured. They carry `entry_id`, `host`, `device_name`, `filename`, `progress`, `layer`, `total_layers`, `left_seconds` and `err_code`. This is the supported way to write your own text in a language other than your server's.
- **A cancelled or aborted print now says so**, naming the file and the percentage it stopped at, however the stop was made: Home Assistant, the Creality app, or the printer's own screen. Shares the *Notify when a print ends* toggle.
- **Notification text is translated**, Spanish included. It follows the **server** language (Settings → System → General), not each user's, because an integration is never told which user a notification is for. Use the bus events for per-user text.
- **`snapshot_supported` attribute** on camera entities, saying whether a still image is possible at all.
- **Edit filament from the CFS card.** Each slot tile gains an edit dialog for material type, name, vendor, colour, temperature range and pressure advance. It writes, then asks the printer to re-report, so the tile only changes once the write lands.
  - Names the box and slot it will write to, and says so when it inferred them from the card layout.
  - Disabled while the printer is busy, and for cards whose entities span more than one printer.
  - **Multi-colour spools** show their colour read-only; the other fields still save.
  - **Colour presets**: Creality's palette plus your own, kept in your browser.
- **`ha_creality_ws.set_cfs_material` service** for writing slot metadata from automations. Only supplied fields change; omit one to keep the printer's value.
- **New CFS slot attributes**: `box_id`, `slot_id`, `min_temp`, `max_temp`, `pressure`. Not every printer reports the temperatures, so treat `null` as unknown rather than zero.
- **`spool_key` attribute on every CFS slot** (closes #117 part 2). The printer's `rfid` is a material id, not a tag serial, so spools of the same material shared it and external trackers such as spoolman-sync could not tell them apart. `spool_key` combines it with the normalised colour. It is derived, not new telemetry: two genuinely identical spools still collide, and raw `rfid` is passed through unchanged.
- **`color_hex_raw` attribute**: the printer's original colour string, alongside the corrected `color_hex`.
- **Third card display mode, `box`**: a photo of the CFS unit with a spool overlay per bay. Needs a mapped four-slot box, and falls back to the full view.
- **Optional go2rtc RTSP port** under *Configure → Camera*, for a go2rtc on a non-default RTSP port. `0` keeps auto-detection.
- **Fan entities documented** (#114). `fan.*_model_fan` / `*_case_fan` / `*_side_fan` have always supported on/off and speed; the README now covers them, with a chamber-too-hot automation example.

### Changed

- **⚠️ `sensor.<printer>_system` has been removed.** It was a byte-identical duplicate of `sensor.<printer>_model` and is removed from the registry on upgrade. **Point any dashboard or automation at `sensor.<printer>_model`.**
- **⚠️ The card's `compact_view` option became `view_mode`** (`full` | `compact` | `box`). Dashboards migrate automatically; no action needed.
- **The *Notification Device* setting became *Notification targets*** and accepts several. Existing configs migrate on load, and the old value stays on disk so a downgrade works. The live card is **opt-in**.
- **The *Notify when Completed* option is now *Notify when a print ends*** and covers stopped prints too. If you had it on, you will now also hear about cancellations.
- **Each options page saves when you submit it.** The old *Save and apply* menu item is gone, so closing the dialog normally no longer discards everything. *Done* and the window close button both just close.
- **Changing only notification settings no longer reloads the integration**, so the connection stays up and no entity blinks while you tune text. Every other page still reloads.
- **Notification messages name the file, not its full path.** Sensor attributes still publish the raw value.
- **The built-in notification wording is shorter and leads with the printer**: `Print "3DBenchy.gcode" completed successfully!` became `K1C finished.` *Custom text* reproduces the old phrasing exactly if you preferred it.
- **The camera-mode dropdown and the camera and power-switch help text are translatable**; they were hardcoded English.
- **Notifications are no longer awaited inside the WebSocket receive loop**, which could stall the connection and flicker every entity unavailable.
- **The card only re-renders when something it displays changed**, instead of on every state update.
- **Printer status is derived in one place**, so the card and the service cannot disagree about whether the printer is busy.
- **The bundled CFS image is 17 kB instead of 509 kB** (WebP). `www/` was 143 kB, so a PNG would have quadrupled every install.

### Fixed

- **Late-discovered entities were scheduled from the wrong thread.** A CFS unit or chamber appearing after setup created entities from an executor thread via the non-thread-safe `hass.loop.call_soon`, racing the event loop.
- **A printer reporting a bare error code took every print-status attribute down with it.** Some firmware sends `err` as a number rather than `{errcode: N}`; the attribute builder called `.get()` on it and lost `state_raw`, `left_time_s` and the rest to an `AttributeError`.
- **Pause was sent to printers that had already stopped**, because the coordinator counted any job with a file name and a progress value as printing. Pause and resume now use the same state derivation as everything else.
- **A queued pause or resume outlived its job** and fired as the *next* print started. Queued requests are dropped when the job ends.
- **Stopping a print and re-running the same file left notifications dead**: the new-job check required a *completion*, which a stopped print never produces, so the second run got nothing.
- **The G-code preview vanished while a print was paused**, and stayed hidden for the rest of a print once the printer set an error code it never cleared.
- **The diagnostic service was re-registered on every printer setup**, and dumps always reported the Home Assistant version as `unknown`. Dumps now carry the real version and a UTC timestamp.
- **The legacy light switch and fan percentage controls could never be created**, because the migration removing them ran before platform setup. The dead platform is gone; the cleanup migration stays.
- **The camera advertised a `CameraEntityFeature.ON_DEMAND` flag that does not exist.** It declares `STREAM`, which is what native WebRTC needs.
- **Every field in the printer card's Style Editor was labelled with its raw config key.** The label function sat behind `if (form.computeLabel)`, which `ha-form` never satisfies, so the translated labels were never used.
- **Saving a colour in the Style Editor changed colours you had not touched.** The picker discarded the alpha on the way in and hardcoded `0.9` on the way out, so Save rewrote every colour to 90% opacity: the stop button's `.95` and the light-off grey's `.35` both became `.90`. Three-digit hex like `#abc` also came back invalid. Both directions now keep the alpha and understand shorthand.
- **The printer card had branches for `resuming` and `pausing`**, states the integration never reports, so a colour and two icon choices were dead.
- **A failure to serve the dashboard cards was logged at debug level**, so the cards 404'd with nothing in the log. It is a warning now.
- **A notify target that could not be routed failed silently.** The field accepts free text, so a typo simply did nothing; it now logs a warning.
- **Notification state stopped tracking when no target was configured**, so enabling notifications mid-print could fire a spurious "completed".
- **The camera snapshot is skipped where it cannot work**, rather than rendering an empty grey box. Applies to K2 direct-WebRTC cameras and to the G-code preview when the printer has none.
- **Camera stream API (HLS, recording, casting) failed with `TypeError: 'str' object is not callable`** (closes #116, thanks @Raymondvb1985). A synchronous `stream_source` property returning the go2rtc stream *name* shadowed core's `async def stream_source()`. It is now an async method returning an RTSP URL, with the port detected automatically (`18554` built-in, `8554` stand-alone) and overridable. Frontend WebRTC playback is unchanged.
- **Wrong CFS spool colour: the leading pad character was kept** (closes #113 and #117 part 1, thanks @raf802). Creality tags store seven hex characters, one of padding then `RRGGBB`, so `0ffffff` was read as `#0fffff`. The last six digits are used now and normalised to lowercase; non-hex values such as `N/A` are left alone. The CFS card already compensated, so its rendering is unchanged.
- **`Generic Generic PLA` in the filament label** (closes #115). The vendor is only prepended when the name does not already start with it.
  - **⚠️ A vendor the printer never reported is no longer invented.** A slot reporting only `PETG` was labelled `Generic PETG`; it now falls back to the material name, then the type. **Update any template matching the old value.** An empty slot reports `Unknown` rather than `'Generic '`.
- **"Print completed" notification on every Home Assistant restart** (closes #112, thanks @chairstacker). The printer reports the finished job indefinitely, so a fresh coordinator read it as new. The first frame carrying print state is now recorded silently, and the same guard covers the error, runout and minutes-to-end notifications.
- **Minutes-to-end notification never fired**: it read `printTimeLeft`, but the printer streams `printLeftTime`.
- **Chamber-target control missing after a restart while the printer was off.** `number.<printer>_chamber_target` was only created if `maxBoxTemp` had already arrived, and nothing recreated it later. It now uses the capability cached during onboarding, and late-arriving telemetry fires a discovery signal so capabilities create their entities without a restart.
- **Dynamic CFS discovery relied on a swallowed error**: `async_add_entities` is a synchronous callback, so awaiting it raised `TypeError` every time, discarded because nothing held the future.
- **Completion notification only ever arrived once per file name**, so reprinting the same file never notified again. It re-arms below 90% progress, or on a job-clock restart for a print that never got near the end. (Re-arming on any dip below 100% would fire twice per print: the printer rounds up to 100 a second early, then reports 99 once more.)

### Internal

- The coordinator holds its `ConfigEntry` rather than the entry id, removing five `async_get_entry` round trips.
- The options flow no longer receives the config entry Home Assistant already gives it.
- `typing.Optional`/`List`/`Dict` replaced with `X | None`, `list`, `dict`; `Callable` and friends from `collections.abc`.
- `KEntity`'s reads of the onboarding cache are covered by tests; they had none.
- Removed the backwards-compatibility code the 2026.7 minimum makes unreachable: six `try/except ImportError` blocks, the pre-2024.7 static-path API, the pre-dataclass Lovelace resources access, and positional-argument handling in `fan.turn_on`. Two import-time shims stay so an ancient core still gets the friendly message.
- Deleted code nothing called: six functions, a write-only parameter, four constants, a no-op `if`, and a test asserting `"logistics" not in manifest.json`.
- Modernised deprecated APIs: `FlowResult` → `ConfigFlowResult`, `AddEntitiesCallback` → `AddConfigEntryEntitiesCallback`, `dt_util.utcnow()`, `get_running_loop()`.
- The test suite no longer depends on collection order: shared stubs moved to `conftest.py`, so every module runs on its own.
- `LATE_DISCOVERY_FIELDS` in `const.py` lists the telemetry fields that gate entity creation; the coordinator fires one discovery signal the first time each appears.
- New shared CFS helpers in `utils.py` (`normalize_color_hex`, `format_filament_label`, `build_spool_key`) replacing duplicated inline logic across three sensors.
- Regression tests for all of the above.

### Notes

- The live card needs **iOS 17.2+** or **Android 16+**. Older phones still get a notification that replaces itself in place, without the timer and progress bar.
- **iOS ends any Live Activity after 8 hours**, an Apple limit no app can extend. Past that the card continues as an ordinary notification with the remaining time in the text, and the completion notification still arrives. Android has no such limit.
- The G-code preview does **not** appear in an iOS Live Activity; that layout has no image slot. It shows on Android and on the plain notifications on both platforms.

### Notes for anyone with CFS hardware

Creality does not document the `modifyMaterial` command. The payload shape comes from @buzato's testing against real hardware and is verified here against the bundled simulator, but two details are unconfirmed: the printer streams colours as seven hex characters yet appears to accept six on write, and the `rfid` field name is inferred from telemetry. Every write logs the outgoing payload and what the printer reports back. **If a material edit does something unexpected, please open an issue with that part of your debug log.**

### Test server (`tools/creality_printer_test_server.py`)

Dev tooling only. Several fidelity gaps made the simulator disagree with real hardware.

- **Fan telemetry used names the integration never reads** (`caseFan` rather than `caseFanPct` and so on), so fan entities looked stuck at 0. It now emits the real names, honours `M106 P<ch> S<0-255>` over `gcodeCmd`, and leaves a manually driven fan alone.
- **Video was answered as VP8**, which HA's `stream` component cannot package into HLS, so the playlist blocked forever. It answers H.264 first now (`--prefer-codec`).
- **Keyframes were up to 25 s apart**, inherited from libx264's 250-frame default, long after HA's stream worker gives up. Video is pre-encoded with a 1 s GOP (`--video-source auto`).
- **Healthy WebRTC sessions were killed after 60 s** by an unconditional sleep-then-close. Teardown follows the connection state.
- **`--deterministic`** removes temperature, fan and position randomness so telemetry is reproducible. Progress fields stay time-derived.
- **`--cfs-variant edge`** adds the awkward payloads: a six-character colour, a vendorless slot, a multi-colour spool, shared `rfid` values, and an empty external slot.
- **Test-control endpoints** (`POST /test/set`, `/test/reset`, `/test/cfs`, `GET /test/state`) pin any telemetry field, so notification scenarios can be driven in seconds. Real printers have no such endpoints.
- Log lines carry timestamps, and the offer/answer SDP is dumped under `--debug`.

## [0.9.7] - 2026-07-28
> [List of issues (0.9.7)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.7)

### Added
- **Localization / i18n support** (#104, thanks @oscfdezdz / Óscar Fernández Díaz):
  - Entities, services, and both Lovelace cards (printer + CFS) are now fully translatable via `strings.json` / `translations/*.json` and bundled `www/i18n/*.json`, shipping with English and Spanish.
  - Entity classes use `_attr_translation_key` instead of hardcoded names; the cards resolve strings through `hass.localize` with a bundled JSON fallback and automatic language fallback while backend translations load.
- **Dimmable chamber light** (#108, closes #102, thanks @RobertJansen1):
  - The chamber LED is now exposed as a brightness-capable light on printers with confirmed PWM support (**K2 Pro**, **K2 Plus**), matching the 0–100% slider in the printer's local UI.
  - Brightness capability is model-detected (`LED_PIN_BY_MODEL`); every other printer keeps the plain on/off light. Because the firmware only reports on/off, the dim level is remembered locally, and the capability migrates onto existing config entries -- even while the printer is offline -- from cached model info.
- **Power-off confirmation on the printer card** (#110, closes #101):
  - The card's power button now asks for confirmation before turning the printer **off** (never when turning it on), with a stronger warning while a print is in progress so an accidental tap can't kill a running job.

### Changed
- **⚠️ Breaking -- `filament_status` now reports lowercase slugs** (#104):
  - As part of state translation, the **Filament Status** sensor now reports `normal` / `runout` instead of `Normal` / `Filament Runout`. Any automation or template matching the old capitalized values must be updated -- the displayed (translated) label is unchanged.
- **Print Tuning range raised to 200%** (#107, thanks @RobertJansen1):
  - The **Print Tuning %** slider now goes up to 200% (was 100%), matching the feedrate/flowrate hardware limit so the control is actually usable.
- **Printer-card display refinements** (#110, closes #101, #103):
  - The layer readout shows `-/-` (consistent with the temperature pills) instead of `?/?` when the printer is off or idle.
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
  - `boxsInfo` (the CFS payload) is only sent by the printer in response to an explicit poll, never in the regular telemetry stream. The 0.9.6 rework of the periodic-GET loop started its poll timers at the current time instead of zero, which pushed the **first** CFS poll a full 5 minutes (`GET_BOXS_INFO_SEC`) past connect. Because every reconnect restarts that timer, any printer that dropped and reconnected more often than every 5 minutes never got polled at all -- so the CFS entities were never discovered and sat unavailable.
  - The first poll of each periodic GET (printer params, print objects, and CFS box info) now fires immediately once the connection is ready, restoring the pre-0.9.6 behavior. The 5-minute steady-state cadence for CFS is unchanged, and the 0.9.6 "availability requires real data" gate still applies.


## [0.9.6] - 2026-06-10
> [List of issues (0.9.6)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.6)

### Added
- **Direct WebRTC camera mode** (idea recycled from #95, thanks @erus71an):
  - New opt-in **"WebRTC direct, no go2rtc"** camera mode that signals the printer's WebRTC endpoint directly from Home Assistant (browser-style base64-JSON SDP on `:8000/call/webrtc_local`), bypassing go2rtc entirely.
  - Available as an alternative for any WebRTC-capable printer -- try it if the default go2rtc path doesn't work on your firmware (e.g. newer K1C). The K2-family go2rtc path is unchanged and remains the default.
  - The frontend offer is reduced to video-only, wrapped/unwrapped as the printer expects, and the answer's media-line order is rebuilt to match Home Assistant's original offer.
- **Custom camera URL mode** (#92, thanks @OptimusGREEN):
  - New **"Custom camera URL"** mode to point the camera at any external stream -- `http(s)` MJPEG/snapshot URLs are served directly, and `rtsp://` (and similar) streams are ingested through go2rtc.
  - Useful for printers without a built-in camera, or to substitute an existing IP camera.
- **Spanish translations** (`es.json`, thanks @ofdezdz / Óscar Fernández Díaz):
  - Adds a full Spanish translation of the integration's config and options UI, extended to cover the reorganized options menu and the new custom-camera-URL field.

### Changed
- **Options flow reorganized into a menu**:
  - The printer Options dialog is now a menu with **Camera**, **Notifications**, **Power switch**, and **Connection & performance** sub-pages.
  - Each sub-page loads fresh, so mode-dependent fields are always correct -- previously a single combined page couldn't re-render when the camera mode changed, leaving stale/irrelevant fields (e.g. go2rtc fields showing while "Custom URL" was selected).
  - Submitting a sub-page stages its changes and returns you to the menu (the back arrow returns without staging); **Save and apply** writes everything at once, so the printer reloads only once no matter how many sections you edit.
- **Camera options refresh**:
  - The camera-mode dropdown now lists Auto, MJPEG, WebRTC (go2rtc), WebRTC direct, and Custom URL, with clearer labels and help text.
  - The custom-URL field appears when the custom mode is selected, with validation for a complete URL.
  - Auto-detection now probes both `/call` and `/call/webrtc_local` WebRTC signaling endpoints.
- **WebSocket handshake parity**:
  - The client now advertises the printer web UI's `wsslicer` subprotocol on the WebSocket handshake. Servers that don't use it simply ignore it (RFC 6455), so this is compatible with existing printers.

### Fixed
- **WebSocket availability now requires real data**:
  - The printer is reported available/connected only after the first valid telemetry frame, not on the bare TCP/WebSocket handshake -- preventing brief "available" flashes against a socket that never streams data.
  - Reconnect backoff is reset only after a connection survives at least 10 seconds, so rapid connect/drop flapping keeps backing off instead of masquerading as healthy reconnects.
  - The heartbeat watchdog measures silence from the connect time until the first frame, avoiding a premature "connection dead" verdict right after connecting.

### Internal
- **#88 diagnostics**: added debug logging of the negotiated WebRTC video codec/profile on stream start, to help diagnose the brief pre-keyframe visual artifact reported on some K2 cameras (no behavior change).


## [0.9.5] - 2026-05-22
> [List of issues (0.9.5)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.5)

### Fixed
- **K2 WebRTC Camera Instability after 0.9.4** (#88 follow-up):
  - Fixed an `'Stream' object has no attribute 'get'` error that fired on every snapshot and WebRTC offer when the go2rtc stream already existed.
  - The 0.9.4 "verify existing source matches" check incorrectly treated `streams.list()` entries as dicts; the `go2rtc-client` library actually returns `Stream` dataclasses with a `producers: list[Producer]` field. The misuse raised on every call, was swallowed by the generic exception handler, and forced a delete-and-recreate of the active stream on every snapshot / offer -- producing the flicker, repeated 30s loads, and intermittent unavailability reported on 0.9.4.
  - The verification now inspects `Stream.producers[*].url`, so the upgrade-path self-healing (recreate a leftover stream that points at a wrong source) is preserved without breaking the steady state.

### Changed
- **Printer Card Sizing for Wrapped Telemetry** (#91, thanks @Ahmed-max):
  - The printer Lovelace card now reports a larger card size when its telemetry pills naturally wrap onto a second line, so Home Assistant reserves the right amount of vertical space and the next dashboard tile no longer overlaps the wrapped row.
  - Added a debounced `ResizeObserver` on the telemetry area; observers and timers are cleaned up on disconnect. Wrapping behavior itself is unchanged.

### Internal
- **Shutdown Robustness**:
  - WebSocket client shutdown now reliably ignores expected `asyncio.CancelledError` during teardown so it no longer surfaces as a spurious error in logs.

### Testing
- Added regression tests covering the `Stream` dataclass shape returned by `go2rtc_client.streams.list()` -- both "existing stream with correct source is left alone" and "existing stream with stale source is recreated".
- Stabilized the static pytest suite by reading source files as UTF-8 and added layout tests for telemetry-driven card sizing.


## [0.9.4] - 2026-05-21
> [List of issues (0.9.4)](https://github.com/3dg1luk43/ha_creality_ws/issues?q=is%3Aissue+milestone%3Av0.9.4)

### Fixed
- **K2 WebRTC Camera Regression** (#87, #88):
  - Restored the `#format=creality` go2rtc source fragment for K2 family WebRTC cameras, which selects go2rtc's built-in Creality JSON-wrapped SDP client.
  - In 0.9.3 the fragment was dropped on the assumption it was no longer needed, but the K2/K2 Pro/K2 Combo signaling endpoint at `:8000/call/webrtc_local` does not speak standard WHEP -- it replies with `{}` to raw SDP offers, which made go2rtc fail with `sdp: syntax error at pos 1: "}"` and caused the camera entity to become unavailable.
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
