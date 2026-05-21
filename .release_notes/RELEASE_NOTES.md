# Changelog

All notable changes to HA Creality WS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


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
