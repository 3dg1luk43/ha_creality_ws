# Development Tools

This directory contains tools for developing and deploying the Creality WebSocket integration.

## Unified Creality Printer Test Server

One test server to emulate telemetry and video behavior of Creality K-series and Ender models. It merges the previous separate WS and WebRTC/MJPEG servers.

File: `tools/creality_printer_test_server.py`

Features
- WebSocket telemetry on `ws://<host>:9999`
- HTTP endpoints on `http://<host>:8000`:
	- WebRTC signaling: `POST /call/webrtc_local` (K2 family models)
	- MJPEG stream: `GET /stream.mjpeg` (K1/Ender/Hi models)
- Default video: 1080p at 30 fps (overridable)
- Expanded states: self-testing -> printing/paused/idle
- Configurable print duration, layers, objects
- Random XYZ movement while printing
- Temperature targets with ±0.1–0.2°C oscillation
	- Defaults: nozzle 250°C, bed 70°C, box 50°C (override with --target-* flags)
- Dynamic working layer and current object index
- Randomized case/model/side fan values (bridge spikes)
- Model-based capabilities: box temp sensor/control, light, camera type

Dependencies
- Required: `aiohttp`, `aiortc`, `av`, `numpy`, `websockets`
- Optional for MJPEG: `Pillow`

Run without parameters for a comprehensive help guide:

```bash
python3 tools/creality_printer_test_server.py
```

Common examples

```bash
# K2 Plus with WebRTC camera, 10-minute print, default 1080p30
python3 tools/creality_printer_test_server.py --model k2plus --simulate-print --print-seconds 600

# K1 with MJPEG camera, 720p25, targets set
python3 tools/creality_printer_test_server.py --model k1 --simulate-print --width 1280 --height 720 --fps 25 \
	--target-nozzle 210 --target-bed 60

# K2 Pro, set box temp, 8 objects, 160 layers, larger volume
python3 tools/creality_printer_test_server.py --model k2pro --simulate-print --target-box 40 --objects 8 --layers 160 \
	--max-x 300 --max-y 300 --max-z 300
```

Endpoints
- WebSocket telemetry: `ws://<host>:9999`
- WebRTC signaling: `POST http://<host>:8000/call/webrtc_local` (K2 family)
- MJPEG stream: `GET http://<host>:8000/stream.mjpeg` (others)

Notes
- Camera mode is selected automatically based on model.
- Temperature and fans are simulated realistically for UI testing.
- If MJPEG fails, install Pillow.

## deploy_to_ha.sh

Deployment script that syncs code from the development repository to production Home Assistant.

### Usage

```bash
# Dry run - see what would happen
./tools/deploy_to_ha.sh

# Full deployment with backup and restart
./tools/deploy_to_ha.sh --run

# Deploy without creating backup
./tools/deploy_to_ha.sh --run --no-backup

# Deploy without restarting Home Assistant
./tools/deploy_to_ha.sh --run --no-restart

# Deploy only the Lovelace card (k_printer_card.js)
./tools/deploy_to_ha.sh --run --card

# Deploy only the card without backup or restart
./tools/deploy_to_ha.sh --run --card --no-backup --no-restart
```

### What it does

**Full deployment mode (default):**
1. **Creates timestamped backup** of production code in `/root/ha_creality_ws/backups/`
2. **Syncs code** from development repo to production Home Assistant
3. **Removes cache files** (`__pycache__`, `*.pyc`, `*.pyo`) from production
4. **Restarts Home Assistant** via API call

**Card-only mode (`--card`):**
1. **Creates backup** of only the `k_printer_card.js` file
2. **Syncs only the card file** from development to production
3. **Restarts Home Assistant** via API call (optional)

### Configuration

- **Repository**: `/root/ha_creality_ws` (development)
- **Production**: `/root/ha_config/custom_components/ha_creality_ws` (SMB mount)
- **Backups**: `/root/ha_creality_ws/backups/` (timestamped)
- **API**: Home Assistant restart via authenticated API call

### Safety Features

- **Dry-run by default**: Use `--run` to actually perform changes
- **Automatic backups**: Creates timestamped backups before changes
- **Validation**: Checks that source and destination directories exist
- **Error handling**: Stops on errors and provides clear messages
- **Cache cleanup**: Removes Python cache files to prevent issues

### Examples

```bash
# Quick development cycle
./tools/deploy_to_ha.sh --run

# Deploy without backup (faster, but no rollback)
./tools/deploy_to_ha.sh --run --no-backup

# Deploy without restart (manual restart later)
./tools/deploy_to_ha.sh --run --no-restart
```
