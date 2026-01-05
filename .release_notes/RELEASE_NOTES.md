# Changelog

All notable changes to HA Creality WS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
