# Copilot Instructions for this repository

These instructions tell GitHub Copilot Chat how to work in this repo. Assume changes target a Home Assistant custom integration that talks to Creality printers over a local WebSocket, plus two bundled Lovelace cards.

The maintained references are `CLAUDE.md` (rules and traps) and `docs/internal/INTEGRATION_REFERENCE.md` with its deep-dives (how it works, open findings). Where this file and those disagree, they win.

## Project overview

- Domain: `ha_creality_ws` (custom_components/ha_creality_ws)
- Purpose: Low-latency local WebSocket telemetry and control for Creality K-series and compatible printers. Bundles a dependency-free Lovelace card.
- Connectivity: Local WebSocket (default ws://<host>:9999) with push updates; no polling.
- Discovery: Zeroconf matches for names containing creality/k1/k2.
- Python target: 3.14 (Home Assistant 2026.7, the minimum, needs it); the Tests workflow runs 3.13 and 3.14. No formatter; CI's only lint is `ruff --select F,E9`, see "Dev quick checks".

## Repo layout quick map

- `custom_components/ha_creality_ws/__init__.py` – HA setup, wiring coordinator and platforms; caches device info and options
- `custom_components/ha_creality_ws/coordinator.py` – DataUpdateCoordinator subtype; owns `KClient`; `wait_for_fields` helper
- `custom_components/ha_creality_ws/ws_client.py` – Resilient WebSocket client, heartbeat, jittered backoff, periodic GETs
- `custom_components/ha_creality_ws/sensor.py` – Sensors (status, temps, progress, positions, etc.)
- `custom_components/ha_creality_ws/button.py` – Pause/Resume/Stop controls
- `custom_components/ha_creality_ws/light.py` – Light platform (printer chamber light); there is no switch platform
- `custom_components/ha_creality_ws/number.py` – Number entities (speed/flow/targets; K2 box control only)
- `custom_components/ha_creality_ws/camera.py` – MJPEG (K1) and WebRTC (K2) camera implementations
- `custom_components/ha_creality_ws/image.py` – Image platform exposing current print preview (attempted for every model; see the Image section)
- `custom_components/ha_creality_ws/fan.py` – Fan platform (model/case/side fans)
- `custom_components/ha_creality_ws/config_flow.py` – UI config + Options (power switch binding, camera mode, go2rtc)
- `custom_components/ha_creality_ws/entity.py` – Base entity: availability and device info
- `custom_components/ha_creality_ws/utils.py` – Helpers (numeric coercion, parsing, model detection)
- `custom_components/ha_creality_ws/services.yaml` – Custom HA services
- `custom_components/ha_creality_ws/manifest.json` – HA manifest (requirements, version, zeroconf)
- `tools/test_files/deploy_to_ha.sh` – Dev-to-HA deploy script with backup and restart (gitignored; local only)

## Design anchors to preserve

- Availability: an entity is unavailable when the power switch is OFF or the link is stale (`KEntity.available`). A few stay available on purpose (model, max temperatures, Reconnect, the print preview); `KEntity._should_zero()` is what those use to show nothing.
- Power switch awareness: the switch's own state decides power edges (`KCoordinator._switch_reports_off()`) and starts and stops the WS client.
- Pause/resume pipeline: queued actions in coordinator (`request_pause`, `request_resume`, `_flush_pending`) with non-optimistic UI.
- Status derivation: `PrintStatusSensor` maps telemetry to human-readable status. Don’t regress this mapping.
- Resilient WS client: `KClient` owns heartbeat, jittered backoff, reconnect, and periodic GETs.
- Local-first, no cloud: Never introduce cloud calls. Keep latency low and updates push-driven.
- Model-specific feature detection: conditional features by model (box temp sensor/control, light, camera type).
- Lovelace card behavior: chips/buttons render instantly with optimistic UI; chip order follows the `button_order` config; the Power chip is only visible when configured; Light chip visibility reacts to power state and status without reload.

## Home Assistant specifics

- Entities subclass `KEntity`; follow CoordinatorEntity pattern; no polling.
- Use `selector` in config flow options; respect existing option keys.
- For new services: declare in `services.yaml` and implement async-safe handlers in platform or `__init__.py`.
- For new simple sensors: prefer adding to `SPECS` in `sensor.py`, with a `translation_key` and its strings in every locale.
- Prefer HA unit constants with compatibility fallbacks.
- Image platform: subclass `ImageEntity`; call `ImageEntity.__init__(self, hass)`; set `image_last_updated` when new bytes fetched; return placeholder bytes when content is unavailable.

## Model detection and feature management

Use `ModelDetection` which reads both `model` and `modelVersion` codes.

- Detection helpers:
  - K1 family: K1, K1C, K1 Max, K1 SE
  - K2 family: codes F021 (K2), F012 (K2 Pro), F008 (K2 Plus)
  - Ender 3 V3 family: F001 (V3), F002 (V3 Plus), F005 (V3 KE)
  - Creality Hi: F018
- Capabilities by model:
  - Box temperature sensor: K1 (except K1 SE), K2; not present on Creality Hi
  - Box temperature control: K2 family (K2, K2 Pro, K2 Plus); not present on Creality Hi
  - Light: All except K1 SE and Ender 3 V3 family; dimmable on K2 Pro and K2 Plus
  - Camera types (`detect_camera_type`): WebRTC for the K2 family and any printer reporting `webrtcSupport: 1` (K1C/K1 Max firmware 1.3.5.22); MJPEG optional (K1 SE, Ender 3 V3); MJPEG otherwise
- `resolved_model()` is the cached model (what the Model sensor shows); `display_model()` is the device page's name and model id ("K2 Pro", "F012").

## Camera implementation

- MJPEG (K1 family):
  - Snapshot extraction from MJPEG stream; fallback tiny JPEG when unavailable
  - Live streaming via `handle_async_mjpeg_stream`
- WebRTC (K2 family, and printers reporting `webrtcSupport: 1`):
  - Uses HA's own go2rtc through its session and URL (`hass.data["go2rtc"]`); since HA 2025.12 that is a Unix socket, not a TCP port
  - Auto-configure go2rtc stream and forward WebRTC offer/answer
  - Must use HA WebRTC message format: `{ "type": "answer", "answer": "...SDP..." }`
  - Snapshot via go2rtc snapshot API when available

## Image (print preview) implementation

- Every model, no model gate:
  - Expose an `image` entity named "Current Print Preview" with unique_id `<host>-current_print_preview`.
  - Fetch PNG from `http(s)://<host>/downloads/original/current_print_image.png` using HA's `async_get_clientsession` with short timeouts.
  - Attempt it for **all** models. Some non-K1 printers serve the same path, and gating on `ModelDetection` hid the preview from them (removed in 43c6668).
  - Show content for the statuses in `PREVIEW_PRINT_STATES` (`BUSY_PRINT_STATES` plus `completed`, i.e. printing, paused, processing, self-testing, completed), derived through `derive_activity_state` so a stale error code does not hide the preview.
  - When not eligible or fetch fails, return a built-in neutral PNG placeholder; cache last successful image to avoid flashing.
- Diagnostics:
  - Cache all accessed HTTP URLs on the coordinator and include them in the diagnostic dump as `http_urls_accessed`.
- Entity attributes:
  - Expose `preview_reason` (ok | not_printing | fetch_failed) and `source_url` to aid support.
  - The preview URL is attempted for every model, so there is no model-gated reason value: some non-K1 printers serve the same path, and gating on the model hid the preview from them.

## Startup and caching

- Setup does not wait for the printer when the cache is complete; it waits (up to 15 s, then `wait_for_fields`) only on a first setup, an integration upgrade or a moved printer.
- Cache device info and feature flags in `ConfigEntry.data`. The camera type is re-detected from telemetry at every start and rebuilt live when the printer reports the other kind; the firmware version follows telemetry.
- Heuristics: if live telemetry exposes `boxTemp/targetBoxTemp/maxBoxTemp` or `lightSw`, promote those capabilities in cache and enable entities immediately. **Except chamber *control*:** that comes only from the cached `ModelDetection.has_chamber_control` or a live `targetBoxTemp`, never from `maxBoxTemp`, which sensor-only K1-family chambers also report -- promoting on it creates a `boxTempControl` entity for a printer that cannot use one. `maxBoxTemp` promotes the chamber *sensor* only. See the note above `LATE_DISCOVERY_FIELDS` in `const.py`.
- Cache of accessed HTTP URLs: record printer-local HTTP endpoints we hit (e.g., preview image) for diagnostics; never call cloud.

## Coding conventions

- Python 3.14 with type hints; async for I/O; no blocking
- Logging: concise; DEBUG for detail, WARNING for visible diagnostics
- Keep public identifiers stable (unique_id/name formats)
- Don’t add heavy dependencies or cloud calls
- Frontend (card):
  - Keep config keys backward-compatible; labels may evolve (box→chamber) but entity IDs/keys remain stable.
  - Use entity resolution helpers to support ambiguous IDs across domains (e.g., `light/switch`).
  - Implement optimistic UI for snappy feedback on toggles; apply a short-lived override and re-render.
  - Avoid forced page reloads; react to HA state updates and local optimistic overrides.
  - Maintain consistent chip layout; pin Power chip to the far right via CSS ordering.

## Dev quick checks

- Lint: CI's `lint` job runs `ruff check --isolated --select F,E9 custom_components tools`: undefined names, unused imports, syntax errors, nothing else. There is no lint configuration (`pyproject.toml` holds only `[tool.pytest.ini_options]`; no `ruff.toml`/`.flake8`/`.pylintrc`) and no formatter. Do not describe a formatting or import-order change as needed to pass a lint check.
- Tests: `python -m pytest` (stubbed Home Assistant, `tools/tests`) and `python -m pytest tools/tests_ha` (a real one; Python 3.14, `tools/requirements-ha.txt`).
- Manual validation: run HA with the component and observe logs/telemetry
- Release: `tools/release_check.sh` is the preflight (version in `manifest.json` == top `CHANGELOG.md` heading == tag). The top heading reads `## X.Y.Z - Unreleased` until release day. Pushing a `v*` tag runs it and drafts the GitHub release from that CHANGELOG section.
- Deployment: `tools/test_files/deploy_to_ha.sh --run` syncs to the HA test instance. `tools/test_files/` is gitignored, so this script is a local maintainer helper and is not present in a clone.
 - Diagnostic samples: sample WebSocket diagnostic JSONs are stored under `tools/test_files/ws_diagnostic_dumps/` for reference when adding or validating fields

## PR checklist (for Copilot-generated changes)

- Code imports and runs under Python 3.14
- Async-safe; no blocking calls; uses HA helpers
- Entities go unavailable when the printer is off or silent
- Logging not noisy; hot paths are quiet
- No breaking changes to entity IDs or options
- Model detection consistent and capabilities match spec
- Update README when user-facing behavior changes
- Expose new values via sensors: add a spec to `SPECS` or a dedicated sensor class, with a translation key, and correct attributes/units.
- For image/preview features: gate content by status; use placeholders when unavailable; update diagnostics with accessed URLs.
- For new controls: add an entity on one of the platforms in `PLATFORMS` (sensor, camera, button, number, fan, light, image), and call `KCoordinator.request_*` or `KClient.send_set_retry()` as appropriate.
- For options: wire through `OptionsFlowHandler` using `selector` and have the coordinator consume the option.
- For diagnostic services: Use WARNING level logging for visibility, return data in service response for UI access, use async-safe file operations.

## Do and Don’t

Do
- Keep async, typed, minimal changes.
- Reuse helpers in `utils.py` and availability via `KEntity`.
- Match the surrounding file's existing import order and line length. Do not reformat untouched lines: there is no formatter and no style rule to converge on, and long signature lines are the norm across the platform modules. Keep each file's line endings (`test_line_endings.py`).
- Include concise docstrings for public classes/methods.

Don’t
- Don’t block the event loop or add sleep() in sync contexts.
- Don’t add heavy dependencies or cloud calls.
- Don’t alter entity unique_id/name formats.
- Don’t remove heartbeat or periodic GET scheduling.



If in doubt, prefer small, incremental changes and point to where the feature hooks into Coordinator/Client/Entity. Keep the integration simple and local-first.