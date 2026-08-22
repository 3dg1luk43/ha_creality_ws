# Product Definition - Creality WebSocket Integration

## Initial Concept
Creality WebSocket Integration for Home Assistant

## Project Status
The project is a mature, production-ready Home Assistant integration that has been in active development and maintenance for several months.

## Target Audience
- Home Assistant power users with Creality 3D printers.
- 3D printing enthusiasts looking for local, cloud-free monitoring.
- Developers wanting to extend Creality printer capabilities via HA.

## Value Proposition
- **Mature & Stable:** Proven native, low-latency control and telemetry via direct WebSocket connection.
- **Privacy-Centric:** Established 100% local operation with zero cloud dependencies.
- **Rich Ecosystem:** A comprehensive suite of sensors, controls, and bundled frontend cards designed for the Creality ecosystem.

## Implemented Core Features
- **Robust WebSocket Client:** Advanced push-updates with adaptive backoff, power-state awareness, and model-specific telemetry handling.
- **Deep Telemetry & Control:** Comprehensive coverage of temperatures, progress, layers, and printer states across K1, K2, and Ender 3 V3 families.
- **Advanced Camera Integration:** Native MJPEG and WebRTC support (including HA 2025.11+ native go2rtc integration).
- **Rich Frontend:** Standalone, style-customizable Lovelace cards (`k-printer-card`, `k-cfs-card`) with visual editors.
- **Creality Filament System (CFS):** Full slot-level telemetry and visual management.
- **Smart Power Integration:** Sophisticated switch-binding for accurate device lifecycle management.
