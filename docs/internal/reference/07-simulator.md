# 07 - The printer simulator: what it does, what it lacks

Audit of `tools/creality_printer_test_server.py` (2026-10-03), done before rebuilding it (R77).

## 0. Status after the rebuild (2026-10-03)

The simulator is now the `tools/simulator/` package. `creality_printer_test_server.py` is an entry shim that keeps the old flags and names. Of the gaps below, these are done:

- **Wire format.** Delta frames, numbers as strings on the K1 family, `ok` to client heartbeats, real `get` replies (`reqPrintObjects`, `reqProbedMatrix`, both listing formats or none), and every key in a `set` applied.
- **Print lifecycle.** `withSelfTest` at print start, 100 then 99 then completed with the file kept, the optional reset to 0, a pause that freezes progress, three stop styles, reprints, runout with a pause, a mid-print `state 0` swap, homing with `deviceState` 7, and `err.key`.
- **Ports.** MJPEG on `:8080` with the mjpg-streamer headers and `?action=snapshot` (R75 closed), the preview on `:80`, Moonraker on `:7125` parsing queries the way Moonraker does (it exposed R41's query bug), and `webrtcSupport` on `k1c-1.3.5`.
- **Faults.** Power off and on, a boot with blanks, clean and abnormal drops, a silent printer, ignored commands, a late first frame.
- **CFS.** Runtime attach, detach, add and remove; consumption; the echo mode; per-model presence.
- **Engine.** One clock independent of clients (the handler leak and per-connection ticking are gone), a message log, a control UI at `:8099/ui/`, a runtime model switch, and a pre-rendered video loop with no runtime encoding.

Still open:

- G-code listings over 1 MiB, and multi-material listing entries.
- The K1C 2025 RTP payload-type quirk.
- WebRTC through go2rtc on the test box (R78).
- `reqProbedMatrix` values are synthetic.
- No captured K2, Hi or CFS frames exist, so those profiles follow the issue reports. Paths: SIM = the simulator, INT = `custom_components/ha_creality_ws/`, TB = `tools/testbox/`, CAP = `tools/test_files/` (gitignored real-printer captures; never copied into tracked files).

## 1. What it implements today

**Process.** One asyncio process. It runs a WebSocket server on `--ws-port` (9999), which offers no subprotocol and answers non-WS HTTP with 405 (SIM:1802-1823), and an aiohttp server on `--http-port` (8000). Nothing listens on :80, :443, :8080 or :7125.

**CLI flags:**
- Model and print: `--model`, `--simulate-print`, `--print-seconds`, `--layers`, `--objects`, `--self-test-seconds`.
- Video: `--width/--height/--fps`, `--low-power`, `--no-audio`, `--video-source`, `--ffmpeg-bin`, `--prefer-codec`.
- Targets: `--target-nozzle/bed/box`.
- Motion: `--max-x/y/z`.
- Other: `--debug`, `--deterministic`, `--cfs-variant`.

**Models** (`MODEL_CONFIGS`, SIM:87-105):

| Key | Model string |
|---|---|
| k1c | K1C |
| k1 | CR-K1 |
| k1max | CR-K1 Max |
| k1se | K1 SE |
| k2 | F021 |
| k2pro | F012 |
| k2plus | F008 |
| e3v3 | F001 |
| e3v3ke | F005 |
| e3v3plus | F002 |
| crealityhi | F018 |

Each model only sets `box_sensor`, `box_control`, `light` and `camera`. Several values are the same for every model:
- `hostname` is `creality-<key>`.
- `modelVersion` is `Printer HW Ver: <name>; Printer SW Ver: test-1`.
- CFS is always present (`cfsConnect: 1`).
- The limits are always 300/120, plus 80 for the box when `box_sensor` is set.

**Transport.**
- On connect it sends one full snapshot.
- Per connection it ticks at 0.2 s, sends a heartbeat every 10 s and a full snapshot every 2 s.
- Every `get` is answered and every handled `set` is answered with a snapshot.
- It never sends deltas.

**Fields streamed** (SIM:974-1047):
- Identity: `model`, `hostname`, `modelVersion`.
- Temperatures (floats): `nozzleTemp`, `bedTemp0`, the targets, the limits.
- Position and state: `curPosition`, `deviceState`, `state` (0/1/2/5), `err:{errcode}` with no `key`.
- Job: `objects_list`, `curObjectIndex`, `printFileName` (bare name), `printProgress`/`dProgress`, `printJobTime`, `printLeftTime`.
- Material and layers: `usedMaterialLength`, `realTimeFlow`, `layer`, `TotalLayer`.
- Speed and flow: `feedratePct`, `flowratePct`, `curFeedratePct`, `curFlowratePct`.
- Fans: `modelFanPct`, `caseFanPct`, `auxiliaryFanPct`.
- Other: `materialStatus`, `cfsConnect`, and conditionally `boxTemp`, `maxBoxTemp`, `targetBoxTemp`, `lightSw`.

`/test/set` overrides are merged last.

**`get` handling.**
- `boxsInfo` returns the CFS.
- `reqGcodeFile` returns `retGcodeFileInfo2` (3 entries, the 1.3.5.22 shape).
- Everything else returns a full snapshot.

**`set` handling.** An if/elif chain, so only the first matching key is handled:
- `pause`
- `stop` (defective, see below)
- `nozzleTempControl`
- `bedTempControl` (the `num` part is ignored)
- `boxTempControl`/`targetBoxTemp`
- `lightSw`
- `autohome` (`deviceState` 7 for 0 s)
- `setFeedratePct`/`setFlowratePct`
- `gcodeCmd`: only `M106 P<ch> S<v>`; `SET_PIN` is accepted silently.
- `materialStatus` (not a real command)
- `modifyMaterial`: merges the writable keys and raises `ValueError` on a bad box or slot.

**Print simulation.**
- It self-tests once, at process start, as `state=2`, with no `withSelfTest`.
- Progress follows the wall clock. On finish it stays at `state=0` and 100% forever, with the targets still hot.
- Pause does not freeze anything.
- **A stop restarts the print at once**: the next tick sees `_print_start_ts=None` and starts over.
- The only way to raise an error or a runout is `/test/set`.

**CFS.**
- Box 0 is `type:1`, the external box, with one slot.
- Box 1 is `type:0` with 4 Hyper PLA slots, 7-character colours and a slot with no temperatures.
- `--cfs-variant edge` adds a 6-character colour, a vendorless slot, a multi-colour slot and rfid values.
- Nothing changes over time: `percent` and `selected` stay fixed.

**Cameras.**
- `POST /call/webrtc_local` works for WebRTC models (aiortc). It uses H.264 passthrough when ffmpeg is present; **the test-box image has no ffmpeg**.
- `GET /call/webrtc_local` returns 405 for every model.
- MJPEG is served at `:8000/stream.mjpeg`. The header boundary does not match the body, and there is no snapshot endpoint.

**Control API** (on :8000):
- `POST /test/set` merges top-level overrides into the snapshot only; `null` removes a key.
- `POST /test/reset` clears the overrides only.
- `POST /test/cfs` replaces one existing box's slots.
- `GET /test/state` returns the current snapshot.
- There is no UI.

**Defects in the simulator itself.**
- `gather(rx, tx)` never returns, because `tx` swallows `ConnectionClosed`. Every reconnect leaves another 5 Hz ticker running, so state converges faster and faster.
- With no client connected, no time passes.
- A bad `set` value closes the connection.
- `contextlib` is only imported under `__main__`.
- There is no ffmpeg in the box image.

## 2. What the integration needs that is missing or wrong

### 2A. Wire protocol
1. **Delta frames.** A real printer sends one full frame, then only the keys that changed. The HAR shows `{nozzleTemp,bedTemp0}` deltas and objects frames about once a second. The cumulative-merge traps (#121: a blank stays until that key is re-sent) cannot be reproduced while every frame is full.
2. **Numbers as strings.** The K1C sends `"bedTemp0":"31.030000"`, and the same for `nozzleTemp`, `pressureAdvance`, `realTimeFlow`, `realTimeSpeed` and `smoothTime`. `coerce_numbers` is never exercised.
3. **Heartbeat direction.** The client sends `{"ModeCode":"heart_beat","msg":<ISO>}` every 6 s and the printer answers `ok`. The simulator does it the other way round.
4. **`get` replies.** `reqPrintObjects` should return only `{current_object, excluded_objects, objects}`, and `reqProbedMatrix` should return `{probedMatrix:{num,val:[{x,y,z strings}]}}`.
5. **Large frames.** File listings over 1 MiB (R15) are never sent.
6. **Subprotocol.** The client asks for `wsslicer`. Worth an option to reject an unknown one.

### 2B. Fields that are missing or wrong

| Field | Integration use | Today | Real |
|---|---|---|---|
| `withSelfTest` | 1..99 means self-testing and outranks everything else | absent; the simulator uses `state=2`, which derives to idle | 0 idle, 1..99 during the test, 100 after it |
| `current_object`, `objects`, `excluded_objects` | object sensors | `objects_list`/`curObjectIndex` | string, a JSON **string**, `"[ ]"` |
| `err.key` | error text | absent | `{"errcode":0,"key":0}`; stale codes 500/116 (#125) |
| `webrtcSupport` | camera decision and live rebuild (#46) | absent | `1` on K1C/K1 Max 1.3.5.22 and K1C 2025 |
| `deviceState` | 7 = homing | 0 | 1 while printing, 7 while homing for seconds |
| `printFileName` | path match against the listing | bare name | `/usr/data/printer_data/gcodes/<file>` |
| `modelVersion` | DWIN fallback | `Printer HW Ver:` form | `printer hw ver:;printer sw ver:;DWIN hw ver:CR4CU220812S11;DWIN sw ver:1.3.5.22;` |
| `maxBoxTemp` (K1) | chamber max sensor | 80 | absent on K1C 1.3.3.x |
| `targetBoxTemp` | chamber control promotion | never on K1 | sent by some K1C firmware (R71); `0` on the K2 Base WS |
| `TotalLayer` | layer sensors | 120, even when idle | 0 idle, and 0 mid-print on some K1C firmware (#24) |
| targets after the end | number entities | stay hot | 0 after stop or finish |
| limits | sliders | 300/120 for every model | e.g. K1C 300/100 or 320/115 |
| `pause`/`paused`/`isPaused`, `printTimeLeft` | fallbacks | absent | firmware-dependent |
| `realTimeSpeed`, `powerLoss`, `materialDetect`, `enableSelfTest`, `printStartTime`, `printId`, `autohome`, `bedTemp1/2`, `fan*`, `video`, `tfCard`, `upgradeStatus`, `curZOffset`, `pressureAdvance` | not read today (R69, #43) | absent | present on a real K1C |

### 2C. Print lifecycle
1. **Self-test at every print start** (#124). It shows as `withSelfTest` 1..99 after the first printing frame.
2. **Stop variants:**
   - `state=4` with the file and progress kept and the targets at 0;
   - `state=0` with the file kept and progress reset;
   - the file cleared.
3. **Finish:**
   - progress rounds up to 100 with minutes still left, reports 99 once more, then finishes;
   - the file stays selected at 100%;
   - later, progress resets to 0 while the file is still selected (R10).
4. **Pause** freezes progress and the time left.
5. **Reprint of the same file at runtime**: `printJobTime` goes backwards.
6. **Errors:** sticky error codes, and an error that appears mid-print and then clears.
7. **Runout:** `materialStatus=1` together with a pause, then recovery.
8. **A mid-print `state 0` stretch over 15 s** (CFS swap, R29).
9. **Homing:** `deviceState=7` for several seconds (Home button wait; pause queueing).

### 2D. Commands
- `autohome` should take time and report an `autohome` status.
- `SET_PIN` should be logged and recorded, so tests can assert it.
- `stop` needs fixing (2C.2).
- A `pause` sent while homing or self-testing should be ignored.
- The `modifyMaterial` echo should be switchable between 6 and 7 characters (#113).
- Rejected commands and drops in the middle of a command should be possible (`send_set_retry`, R32).

### 2E. CFS
- Presence per model: a K1C has no CFS by default; K1/K2/Hi with a CFS (#50, #66, #99).
- External box only (R20).
- Boxes added or removed at runtime (R21), and `cfsConnect` toggling.
- Boxes with no id or a non-integer id.
- `percent` consumption and `selected` switching during a print; slot `state` changes.
- Box temperature and humidity that move.

### 2F. G-code metadata
- The legacy `retGcodeFileInfo` from 1.3.3.46: `{totalNum, fileInfo:"dir:name:size:..."}`.
- No reply at all.
- Listings over 1 MiB.
- Multi-material entries (comma lists, #122/R70).

### 2G. Cameras
1. **MJPEG** on `:8080/?action=stream` and `?action=snapshot`, with `MJPG-Streamer/0.2` and `boundary=boundarydonotcross` (R75).
2. **K1C 1.3.5.22 / K1C 2025:**
   - `webrtcSupport:1`;
   - :8080 refused;
   - WebRTC on :8000;
   - the K1C 2025 RTP quirk: the answer lists payload types 0/96, but 98 is sent.
3. **No camera** for K1 SE and Ender.
4. **Hi on WebRTC** (#60).
5. **`GET /call/webrtc_local`** should give 404 on MJPEG models.
6. **ffmpeg** in the image, for H.264 passthrough, HLS and record.

### 2H. HTTP :80/:443
- `/downloads/original/current_print_image.png`, answering HEAD and GET with an ETag and Last-Modified.
- `/downloads/humbnail/<name>.png` (Creality's spelling).
- A `/` page for the diagnostics crawl.
- Without these, `preview_reason` is always `fetch_failed` (R27).

### 2I. Moonraker (K2 Base)
- `GET :7125/printer/objects/query?objects=temperature_fan%20chamber_fan` should return `{"result":{"status":{"temperature_fan chamber_fan":{"target":N}}}}`.
- The `k2` model should also send `targetBoxTemp: 0` on WS. Without both, R41 cannot be verified.

### 2J. Identity
- The K1 reports `"K1"`.
- The K2 family gets chamber control.
- Identity can arrive piecemeal across frames, which the K2 Base latch (R41) depends on.

### 2K. Connection faults
- Refuse connections.
- Accept, then stay silent (the integration probes at 10 s, goes unavailable at 15 s and closes at 30 s).
- Clean or abnormal drops.
- Boot with blank values (#121).
- A slow first frame.
- 90 s of silence (live-card clear).

### 2L. Real data available (local, gitignored, CAP)
- `ws_diagnostic_dumps/192.168.0.90.har`: K1C 1.3.3.46, 144 WS messages, the :80 web UI, the preview image, `:8080` headers.
- `k1c_idle.log`, `k1c_printing.log`, `k1c_print_2.log`: K1C 1.3.3.x telemetry.
- `k1c_2025_new_printer.log`: K1C 2025 with `webrtcSupport:1` and `powerLoss`.

There are no K2, Hi, CFS or self-test captures.

## 3. Constraints for the rebuild

**docker-compose (TB:27-45).**
- The command is `--model ${PRINTER_MODEL:-k1c} --simulate-print --print-seconds ${PRINT_SECONDS:-900} --self-test-seconds 5 --low-power --no-audio`.
- `tools/` is mounted read-only, and `h264_timing.py` is a sibling module.
- IPs are 172.31.77.10 and 172.31.78.10.
- Only `127.0.0.1:8323 -> 8000` is published.

**Callers.**
- `hactl.py`: `printer-set` posts to `/test/set`, `printer-reset` posts to `/test/reset`, and `rest()` expects JSON.
- `smoke.py` needs:
  - the `creality_k1c` entity prefix (from hostname `creality-k1c`);
  - 50 or more entities;
  - blanks via `/test/set` that read `unknown` within 20 s and recover after `/test/reset`;
  - `PRINT_SECONDS=120` with a force-recreate, which prints to the end by itself within about 125 s;
  - the same printer on the second IP.
- `card_check.mjs` needs the k1c defaults: light, chamber sensor, CFS box 1 with 4 slots plus temperature and humidity, and the external box.
- `testbox_tools` zeroconf uses `creality-k1c.local.`.

**Tests that pin the source.**
- `test_cfs_simulator.py` checks source strings (`"cfsConnect"`, `MATERIAL_WRITABLE_KEYS`, `_make_video_track`, the `modifyMaterial` error texts). It also starts the server on two free ports with `--deterministic --video-source synthetic`, so new listeners must be optional or tolerate a failed bind. And it imports `PrinterState(...)` with `.modify_material` and `.set_cfs_materials`.
- `test_fan.py` checks `_M106_RE`, `handle_gcode("M106")`, the fan field names, `H264PassthroughTrack`, `keyint=`, and `build_argparser().parse_args([]).prefer_codec == "h264"`.

**Documentation** that describes the current flags: `tools/README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `webrtc_test_server.sh`.

## 4. Priorities
1. Fix stop, and add the stop and finish variants, a pause that freezes, and runtime print start and reprint.
2. `withSelfTest` self-test at every print start.
3. Delta mode and string numbers (full frames stay the default, or cleared overrides are re-sent, so smoke's blanks check keeps passing).
4. Connection faults, after fixing the handler leak and ticking state independently of clients.
5. Cameras:
   - MJPEG on :8080 with a snapshot endpoint;
   - a K1C 1.3.5.22 variant;
   - no-camera models;
   - ffmpeg in the image.
6. Preview HTTP on :80.
7. Real object fields, `err.key`, `deviceState` 1 and 7, a full-path `printFileName`.
8. CFS scenarios at runtime (presence, external only, add and remove, consumption, runout).
9. Sticky and mid-print errors, and the >15 s `state 0` stretch.
10. Per-model fidelity (DWIN `modelVersion`, real limits, `"K1"`, K2 Base `targetBoxTemp:0` plus Moonraker, a WebRTC Hi, `SET_PIN` recorded).
11. G-code listing variants.
12. A web control UI over all of the above, keeping the `/test/*` JSON API, the k1c defaults, the CLI flags and the pinned strings.
