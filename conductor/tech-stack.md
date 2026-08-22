# Tech Stack - Creality WebSocket Integration

## Backend (Home Assistant Integration)
- **Language:** Python 3.x
- **Framework:** Home Assistant Core
  - **Pattern:** Data Update Coordinator (local push)
  - **Setup:** Config Flow with Zeroconf/mDNS discovery support
- **Communication:**
  - **WebSocket:** `websockets>=10.4` for real-time telemetry and control.
  - **HTTP:** Standard `aiohttp` for fetching printer-local images (e.g., print previews).
- **Video & Camera:**
  - **MJPEG:** Standard HTTP MJPEG streaming for K1 (classic) and Ender 3 V3 families.
  - **WebRTC:** `go2rtc-client>=0.1.0` for K2 family and 2025+ K1C models.
  - **Orchestration:** Native integration with Home Assistant's built-in `go2rtc` service (Home Assistant 2025.11+).

## Frontend (Lovelace Cards)
- **Language:** JavaScript (ES6+)
- **Format:** Home Assistant Frontend Custom Cards (Bundled `module` type)
- **Components:**
  - `k-printer-card.js`: Main printer control and telemetry dashboard.
  - `k-cfs-card.js`: Dedicated filament system management.
- **Features:** Visual Style Editor with native color pickers and theme persistence.

## Infrastructure & Tools
- **Dependency Management:** `manifest.json` (HACS compatible), `pyproject.toml`.
- **Testing:** `pytest` (configured in `pyproject.toml`).
- **Distribution:** HACS (Home Assistant Community Store).
