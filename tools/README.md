# Development Tools

This directory contains tools for developing and deploying the Creality WebSocket integration.

## Printer simulator

A simulated Creality printer to test the integration against: the WebSocket
telemetry and commands, the cameras, the printer's web server and Moonraker,
with a control UI to drive scenarios. It follows what real printers send (field
names, types, delta frames, numbers as strings on the K1 family) and how their
firmware behaves around a print; `docs/internal/reference/07-simulator.md`
lists what it reproduces and what it does not.

Code: `tools/simulator/` (a package). `tools/creality_printer_test_server.py`
is the entry point and keeps the old command line.

```bash
python3 tools/creality_printer_test_server.py --model k1c --simulate-print   # or: cd tools && python3 -m simulator ...
```

Then open the control UI at `http://<host>:8099/ui/` (on the test box:
`http://127.0.0.1:8323/ui/`).

### Ports

| Port | What | Models |
|---|---|---|
| 9999 | WebSocket telemetry and commands | all |
| 8000 | WebRTC signalling, `POST /call/webrtc_local` | WebRTC cameras |
| 8080 | mjpg-streamer: `/?action=stream`, `/?action=snapshot` | MJPEG cameras |
| 80 | web page and print preview, `/downloads/original/current_print_image.png` | all |
| 7125 | Moonraker, `/printer/objects/query` | K2 Base |
| 8099 | control UI (`/ui/`) and API (`/api/state`, `/api/action`, `/api/log`, `/test/*`); stays up while the printer is "off" | simulator only |

Each is a flag (`--ws-port`, `--http-port`, `--mjpeg-port`, `--web-port`,
`--moonraker-port`, `--control-port`); `0` disables the optional ones, and a
port that cannot be bound (80 without root) is logged and skipped.

### Models

`--model` picks a profile (`python3 tools/creality_printer_test_server.py --help`
lists them): `k1c` (1.3.3.x, MJPEG; the test box default), `k1c-1.3.5`
(WebRTC, `webrtcSupport: 1`), `k1`, `k1max`, `k1se`, `k2` (Base: chamber
target 0 on the WebSocket, real value in Moonraker), `k2pro`, `k2plus`,
`e3v3`, `e3v3ke`, `e3v3plus`, `crealityhi`. The control UI can switch model
at runtime.

### What it does

- **Telemetry like a real printer:** one full frame on connect, then only the
  keys that changed (`--frames full` restores the old every-2-seconds full
  frame). The K1 family writes temperatures and flow as `"31.030000"`. Client
  heartbeats are answered with `ok`. `get` requests are answered with only
  what was asked: `boxsInfo`, the G-code listing, `reqPrintObjects`
  (`{current_object, excluded_objects, objects}`), `reqProbedMatrix`, or for
  `ReqPrinterPara` a full frame.
- **A print's whole life:**
  - Heating with `state 1` at 0 %, then a self-test on K2 and Hi
    (`withSelfTest` 1..99).
  - Progress reads 100 while time is still left, then 99 once more, then the
    job completes with the file still selected.
  - Optionally, progress resets to 0 later (`--finished-reset-seconds`).
  - A pause freezes progress and the time left.
  - A stop is `state4`, `state0` or `clear` (`--stop-style`).
  - Reprints of the same file.
- **Conditions and faults** (control UI or `POST /api/action`):
  - Prints: start with any file and length, pause, resume, stop, finish now, a
    mid-print CFS swap (`state 0` for a while), homing (`deviceState` 7).
  - Errors (code and key), filament runout with its pause.
  - Power: power off (connections dropped, ports refused), power on with blank
    values (#121).
  - Connections: clean or abnormal drops, a silent printer, ignored commands, a
    late first frame.
  - Field overrides.
- **CFS:**
  - Boxes attach, detach, chain on and come off.
  - Slots are selected and consumed while printing.
  - `modifyMaterial` writes merge into the slot. They are addressed by `boxId`
    and `id`, unknown targets are rejected, and an omitted `rfid` keeps the
    tag. The echo can store the colour as sent or padded like the stream
    (#113).
- **G-code listing:** `retGcodeFileInfo2` (1.3.5.22), the legacy packed
  `retGcodeFileInfo` (1.3.3.x), or no reply (`--gcode-listing`).
- **Commands:** temperatures, light, `SET_PIN` LED dimming (K2 Pro/Plus,
  recorded), fans via `M106 P<0|1|2> S<0-255>`, speed and flow, autohome. Every
  message in and out is in the control UI's log and `GET /api/log`.

### Video

The picture is a 4-second loop stored with the simulator in
`tools/simulator/media/`:
- `loop.h264`: H.264 with a keyframe every second, which Home Assistant's HLS
  pipeline and go2rtc need.
- `loop.mjpeg`: JPEG frames for the MJPEG camera.

Nothing is encoded at runtime, so low-end hosts keep up and neither ffmpeg nor
Pillow is needed. Regenerate with `cd tools && python3 -m simulator.media`
(needs ffmpeg with libx264). `--video-source synthetic|ffmpeg` renders live
instead.

### Old test-control endpoints

`POST /test/set` (force fields; `null` clears one), `POST /test/reset`,
`POST /test/cfs` (`{"box_id": 1, "materials": [...]}`) and `GET /test/state`
still work, on the control port and on :8000.

### Tests

- `tools/tests/test_simulator_core.py` covers the printer model, the frames
  and the protocol, with no third-party dependencies, so it runs in CI.
- `test_simulator_server.py` and `test_cfs_simulator.py` run the real servers;
  they skip where `websockets`/`aiohttp` (or `.venv`) are missing.

Dependencies: `aiohttp`, `websockets`; for cameras `aiortc`, `av`, `numpy`
(and `Pillow` only for live-rendered MJPEG).

## deploy_to_ha.sh (maintainer only)

`tools/test_files/deploy_to_ha.sh` is gitignored and not in the repository. It
copies the working tree into the maintainer's own Home Assistant and restarts
it: a dry run by default, `--run` to apply, `--card` for the printer card only,
`--no-backup` and `--no-restart`. To try a change in Home Assistant without it,
use the test box (`tools/testbox`, see its README).
