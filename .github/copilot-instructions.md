# Copilot Instructions for this repository

These instructions tell GitHub Copilot Chat how to work in this repo. Assume changes target a Home Assistant custom integration that talks to Creality printers over a local WebSocket, plus a bundled Lovelace card.

## Project overview

- Domain: `ha_creality_ws` (custom_components/ha_creality_ws)
- Purpose: Low-latency local WebSocket telemetry and control for Creality K-series and compatible printers. Bundles a dependency-free Lovelace card.
- Connectivity: Local WebSocket (default ws://<host>:9999) with push updates; no polling.
- Discovery: Zeroconf matches for names containing creality/k1/k2.
- Python target: 3.11 (ruff target-version py311).

## Repo layout quick map

- `custom_components/ha_creality_ws/__init__.py` – HA setup, wiring coordinator and platforms; caches device info and options
- `custom_components/ha_creality_ws/coordinator.py` – DataUpdateCoordinator subtype; owns `KClient`; `wait_for_fields` helper
- `custom_components/ha_creality_ws/ws_client.py` – Resilient WebSocket client, heartbeat, jittered backoff, periodic GETs
- `custom_components/ha_creality_ws/sensor.py` – Sensors (status, temps, progress, positions, etc.)
- `custom_components/ha_creality_ws/button.py` – Pause/Resume/Stop controls
- `custom_components/ha_creality_ws/switch.py` – Light switch and similar
- `custom_components/ha_creality_ws/number.py` – Number entities (speed/flow/targets; K2 box control only)
- `custom_components/ha_creality_ws/camera.py` – MJPEG (K1) and WebRTC (K2) camera implementations
- `custom_components/ha_creality_ws/image.py` – Image platform exposing current print preview (K1 family)
- `custom_components/ha_creality_ws/config_flow.py` – UI config + Options (power switch binding, camera mode, go2rtc)
- `custom_components/ha_creality_ws/entity.py` – Base entity with zeroing rules and device info
- `custom_components/ha_creality_ws/utils.py` – Helpers (numeric coercion, parsing, model detection)
- `custom_components/ha_creality_ws/services.yaml` – Custom HA services
- `custom_components/ha_creality_ws/manifest.json` – HA manifest (requirements, version, zeroconf)
- `tools/deploy_to_ha.sh` – Dev-to-HA deploy script with backup and restart

## Design anchors to preserve

- Coordinator availability model: an entity stays available and zeros when the power switch is OFF or the link is stale. Use `KEntity._should_zero()`.
- Power switch awareness: `KCoordinator.power_is_off()` drives UI zeroing and WS client start/stop.
- Pause/resume pipeline: queued actions in coordinator (`request_pause`, `request_resume`, `_flush_pending`) with non-optimistic UI.
- Status derivation: `PrintStatusSensor` maps telemetry to human-readable status. Don’t regress this mapping.
- Resilient WS client: `KClient` owns heartbeat, jittered backoff, reconnect, and periodic GETs.
- Local-first, no cloud: Never introduce cloud calls. Keep latency low and updates push-driven.
- Model-specific feature detection: conditional features by model (box temp sensor/control, light, camera type).
- Sensor zeroing when printer off: Layer sensors show 0, text sensors show "N/A", status shows "off" when power is off.
- Lovelace card behavior: chips/buttons render instantly with optimistic UI; Power chip pinned far-right and only visible when configured; Light chip visibility reacts to power state and status without reload.

## Home Assistant specifics

- Entities subclass `KEntity`; follow CoordinatorEntity pattern; no polling.
- Use `selector` in config flow options; respect existing option keys.
- For new services: declare in `services.yaml` and implement async-safe handlers in platform or `__init__.py`.
- For new simple sensors: prefer adding to `SPECS` in `sensor.py`; ensure `_should_zero()`.
- Prefer HA unit constants with compatibility fallbacks.
- Image platform: subclass `ImageEntity`; call `ImageEntity.__init__(self, hass)`; set `image_last_updated` when new bytes fetched; return placeholder bytes when content is unavailable.

## Model detection and feature management

Use `ModelDetection` which reads both `model` and `modelVersion` codes.

- Detection helpers:
  - K1 family: K1, K1C, K1 Max (not K1 SE)
  - K2 family: codes F021 (K2), F012 (K2 Pro), F008 (K2 Plus)
  - Ender 3 V3 family: F001 (V3), F002 (V3 Plus), F005 (V3 KE)
  - Creality Hi: F018
- Capabilities by model:
  - Box temperature sensor: K1 (except K1 SE), K2; not present on Creality Hi
  - Box temperature control: K2 Pro and K2 Plus only; not present on Creality Hi
  - Light: All except K1 SE and Ender 3 V3 family
  - Camera types: WebRTC (K2); MJPEG optional (K1 SE, Ender 3 V3); MJPEG default (others)
- `resolved_model()` provides a stable model name for device info caching when the friendly name is missing.

## Camera implementation

- MJPEG (K1 family):
  - Snapshot extraction from MJPEG stream; fallback tiny JPEG when unavailable
  - Live streaming via `handle_async_mjpeg_stream`
- WebRTC (K2 family):
  - Uses HA built-in go2rtc (default http://localhost:11984)
  - Auto-configure go2rtc stream and forward WebRTC offer/answer
  - Must use HA WebRTC message format: `{ "type": "answer", "answer": "...SDP..." }`
  - Snapshot via go2rtc snapshot API when available

## Image (print preview) implementation

- K1 family:
  - Expose an `image` entity named "Current Print Preview" with unique_id `<host>-current_print_preview`.
  - Fetch PNG from `http://<host>/downloads/original/current_print_image.png` using HA's `async_get_clientsession` with short timeouts.
  - Show content only for statuses: self-testing, printing, completed (derive from the same telemetry/status rules as `PrintStatusSensor`).
  - When not eligible or fetch fails, return a built-in neutral PNG placeholder; cache last successful image to avoid flashing.
- Other models:
  - Keep the entity as a placeholder; do not fetch until we confirm model-specific URLs via diagnostics.
- Diagnostics:
  - Cache all accessed HTTP URLs on the coordinator and include them in the diagnostic dump as `http_urls_accessed`.
- Entity attributes:
  - Expose `preview_reason` (ok | not_printing | unsupported_model | fetch_failed) and `source_url` to aid support.

## Startup and caching

- On setup, if power isn’t OFF, wait for first connect and briefly for `model`, `modelVersion`, `hostname` using `KCoordinator.wait_for_fields`.
- Cache device info and feature flags in `ConfigEntry.data`. Re-detect camera type only when missing.
- Heuristics: if live telemetry exposes `boxTemp/targetBoxTemp/maxBoxTemp` or `lightSw`, promote those capabilities in cache and enable entities immediately.
- Cache of accessed HTTP URLs: record printer-local HTTP endpoints we hit (e.g., preview image) for diagnostics; never call cloud.

## Coding conventions

- Python 3.11 with type hints; async for I/O; no blocking
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

- Lint: ruff configured in repo
- Manual validation: run HA with the component and observe logs/telemetry
- Deployment: `tools/deploy_to_ha.sh --run` syncs to the HA test instance
 - Diagnostic samples: sample WebSocket diagnostic JSONs are stored under `tools/test_files/ws_diagnostic_dumps/` for reference when adding or validating fields

## PR checklist (for Copilot-generated changes)

- Code imports and runs under Python 3.11
- Async-safe; no blocking calls; uses HA helpers
- Entities zero out correctly when off/unavailable
- Logging not noisy; hot paths are quiet
- No breaking changes to entity IDs or options
- Model detection consistent and capabilities match spec
- Update README when user-facing behavior changes
- Expose new values via sensors: add a spec to `SPECS` or a dedicated sensor class. Ensure zeroing respects `_should_zero()` and that attributes/units are correct.
- For image/preview features: gate content by status; use placeholders when unavailable; update diagnostics with accessed URLs.
- For new controls: add a Button or Switch platform entity, call `KCoordinator.request_*` or `KClient.send_set_retry()` as appropriate.
- For options: wire through `OptionsFlowHandler` using `selector` and have the coordinator consume the option.
- For diagnostic services: Use WARNING level logging for visibility, return data in service response for UI access, use async-safe file operations.

## Do and Don’t

Do
- Keep async, typed, minimal changes.
- Reuse helpers in `utils.py` and zeroing via `KEntity`.
- Add ruff-compliant imports ordering and formatting.
- Include concise docstrings for public classes/methods.

Don’t
- Don’t block the event loop or add sleep() in sync contexts.
- Don’t add heavy dependencies or cloud calls.
- Don’t alter entity unique_id/name formats.
- Don’t remove heartbeat or periodic GET scheduling.


## Recent updates (2025-11-12)

- Lovelace card
  - Added optional Power chip (config: `power`, `show_power_button`); resolves entity across domains; optimistic toggle; pinned to far right.
  - Light chip now responds instantly to Power changes (show/hide) without reload; uses optimistic overrides.
  - Ensured Power chip styling reflects actual entity state only when state is known.
- Image platform
  - New `image.py` exposing "Current Print Preview" for K1 family.
  - Returns placeholder when not printing/unsupported/fetch fails; records `http_urls_accessed` for diagnostics.
  - Fixed ImageEntity initialization (`ImageEntity.__init__(self, hass)`) and updates `image_last_updated` on new bytes.
- Diagnostics
  - Expanded diagnostic dump with accessed HTTP URLs to help confirm model-specific paths.
- Terminology/compat
  - Continued preserving back-compat for "box" → "chamber" by keeping stable unique IDs and protocol fields while updating labels.


If in doubt, prefer small, incremental changes and point to where the feature hooks into Coordinator/Client/Entity. Keep the integration simple and local-first.