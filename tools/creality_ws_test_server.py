#!/usr/bin/env python3
"""
Creality-style WebSocket test server for local development.

Listens on ws://0.0.0.0:9999 and emits realistic telemetry data for supported printer models.
Supports all printer models from the README with accurate feature sets and behaviors.

Usage:
  python3 tools/creality_ws_test_server.py [--host 0.0.0.0] [--port 9999] [--model MODEL] [--simulate-print]

Models:
    k1c        - K1C (box temp sensor only, light, MJPEG camera)
    k1         - K1 (box temp sensor only, no control; light; MJPEG camera)
    k1max      - K1 Max (box temp sensor only, no control; light; MJPEG camera)
    k1se       - K1 SE (no box temp, no light, optional MJPEG camera)
    k2         - K2 (box temp sensor only, light, WebRTC camera)
    k2pro      - K2 Pro (box temp sensor & control, light, WebRTC camera)
    k2plus     - K2 Plus (box temp sensor & control, light, WebRTC camera)
    e3v3       - Ender 3 V3 (no box temp, no light, optional MJPEG camera)
    e3v3ke     - Ender 3 V3 KE (no box temp, no light, optional MJPEG camera)
    e3v3plus   - Ender 3 V3 Plus (no box temp, no light, optional MJPEG camera)
    crealityhi - Creality Hi (no box temp, light, MJPEG camera)

This pairs with `tools/creality_webrtc_test_server_local.py` for WebRTC signaling on :8000.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import signal
import time
from typing import Any, Dict

import websockets

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("creality_ws_test_server")


class PrinterState:
    def __init__(self, model: str = "k2plus", simulate_print: bool = False) -> None:
        self.model = model
        self.simulate_print = simulate_print
        self._t0 = time.monotonic()
        self._progress = 0
        self._paused = False
        self._light_on = False
        self._box_temp_target = 0.0
        
        # Temperature targets and current values
        self._nozzle_temp = 25.0
        self._nozzle_temp_target = 0.0
        self._bed_temp = 25.0
        self._bed_temp_target = 0.0
        self._box_temp = 26.0
        
        # Position and movement
        self._position = [0.0, 0.0, 0.0]  # X, Y, Z
        self._device_state = 0  # 0=idle, 7=homing, etc.
        
        # Print job details
        self._print_file = "demo.gcode"
        self._print_job_time = 0
        self._print_left_time = 600
        self._used_material_length = 0.0
        self._real_time_flow = 0.0
        
        # Layer information
        self._current_layer = 0
        self._total_layers = 100
        
        # Control parameters
        self._feedrate_pct = 100.0
        self._flowrate_pct = 100.0
        
        # Error state
        self._error_code = 0
        
        # Model-specific configurations
        # name maps to the integration's expected "model" field content for detection
        # - K1 family uses human-readable names that include "CR-K1" or exact variants
        # - K2/Ender/Hi use F-codes for precise detection
        self._model_configs = {
            "k1c": {"name": "K1C", "box_sensor": True, "box_control": False, "light": True, "camera": "mjpeg"},
            "k1": {"name": "CR-K1", "box_sensor": True, "box_control": False, "light": True, "camera": "mjpeg"},
            "k1max": {"name": "CR-K1 Max", "box_sensor": True, "box_control": False, "light": True, "camera": "mjpeg"},
            "k1se": {"name": "K1 SE", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
            "k2": {"name": "F021", "box_sensor": True, "box_control": False, "light": True, "camera": "webrtc"},
            "k2pro": {"name": "F012", "box_sensor": True, "box_control": True, "light": True, "camera": "webrtc"},
            "k2plus": {"name": "F008", "box_sensor": True, "box_control": True, "light": True, "camera": "webrtc"},
            "e3v3": {"name": "F001", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
            "e3v3ke": {"name": "F005", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
            "e3v3plus": {"name": "F002", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
            "crealityhi": {"name": "F018", "box_sensor": False, "box_control": False, "light": True, "camera": "mjpeg"},
        }
        
        self._config = self._model_configs.get(model, self._model_configs["k2plus"])

    def snapshot(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            # System summary fields used by sensors
            "model": self._config["name"],
            "hostname": f"creality-{self.model}",
            "modelVersion": f"Printer HW Ver: {self._config['name']}; Printer SW Ver: test-1",
            
            # Temperature readings and targets
            "nozzleTemp": self._nozzle_temp,
            "bedTemp0": self._bed_temp,
            "targetNozzleTemp": self._nozzle_temp_target,
            "targetBedTemp0": self._bed_temp_target,
            "maxNozzleTemp": 300.0,
            "maxBedTemp": 120.0,
            
            # Position and movement
            # Position needs to be a string like "X:.. Y:.. Z:.." for integration parsing
            "curPosition": f"X:{self._position[0]:.2f} Y:{self._position[1]:.2f} Z:{self._position[2]:.2f}",
            "deviceState": self._device_state,
            
            # Status and error
            "state": 5 if self._paused else (1 if self.simulate_print and self._progress < 100 else 0),
            "err": {"errcode": self._error_code},
            
            # Objects and file info
            "objects_list": [],
            "printFileName": self._print_file if self.simulate_print else "",
            
            # Print progress and timing
            "printProgress": self._progress if self.simulate_print else 0,
            "dProgress": self._progress if self.simulate_print else 0,  # Alternative progress field
            "printJobTime": int(time.monotonic() - self._t0) if self.simulate_print else 0,
            "printLeftTime": max(0, self._print_left_time - int(time.monotonic() - self._t0)) if self.simulate_print else 0,
            
            # Material and flow
            "usedMaterialLength": self._used_material_length,
            "realTimeFlow": self._real_time_flow,
            
            # Layer information
            "layer": self._current_layer,
            "TotalLayer": self._total_layers,
            
            # Control parameters
            "feedratePct": self._feedrate_pct,   # legacy/external reference
            "flowratePct": self._flowrate_pct,   # legacy/external reference
            "curFeedratePct": self._feedrate_pct,
            "curFlowratePct": self._flowrate_pct,
        }
        
        # Add box temperature if sensor present; include target only when control is supported
        if self._config.get("box_sensor", False):
            d.update({
                "boxTemp": self._box_temp,
                "maxBoxTemp": 80.0,
            })
            if self._config.get("box_control", False):
                d["targetBoxTemp"] = self._box_temp_target
        
        # Add light control if supported (telemetry key expected by integration is "lightSw")
        if self._config["light"]:
            d["lightSw"] = 1 if self._light_on else 0
            
        return d

    def tick(self):
        if self.simulate_print and not self._paused and self._progress < 100:
            # increment every second by ~1%
            self._progress = min(100, self._progress + 1)
            # Update related values
            self._used_material_length = self._progress * 10
            self._current_layer = int(self._progress / 100 * self._total_layers)
            self._real_time_flow = 0.5 + (self._progress / 100) * 0.5  # Simulate flow rate

    def set_pause(self, paused: bool) -> None:
        self._paused = paused
    
    def set_light(self, on: bool) -> None:
        if self._config["light"]:
            self._light_on = on
    
    def set_box_temp(self, temp: float) -> None:
        if self._config["box_control"]:
            self._box_temp_target = temp
    
    def set_nozzle_temp(self, temp: float) -> None:
        self._nozzle_temp_target = temp
    
    def set_bed_temp(self, temp: float) -> None:
        self._bed_temp_target = temp
    
    def set_feedrate(self, pct: float) -> None:
        self._feedrate_pct = pct
    
    def set_flowrate(self, pct: float) -> None:
        self._flowrate_pct = pct
    
    def set_stop(self) -> None:
        self._paused = False
        self._progress = 0
        self._current_layer = 0
        self._used_material_length = 0.0
        self._real_time_flow = 0.0
        self._device_state = 0
    
    def set_autohome(self, axes: str) -> None:
        self._device_state = 7  # Homing state
        # Simulate homing by resetting position
        if "X" in axes and "Y" in axes:
            self._position[0] = 0.0
            self._position[1] = 0.0
        if "Z" in axes:
            self._position[2] = 0.0
        # Reset device state after a short delay (simulated)
        self._device_state = 0
    
    def set_gcode_cmd(self, cmd: str) -> None:
        # Simulate G-code command processing
        pass


async def handle_conn(ws: Any, state: PrinterState):
    # websockets>=12 provides only the connection; path is on ws.path
    path = getattr(ws, "path", "/")
    LOGGER.info("🔌 WebSocket client connected from %s (path=%s)", ws.remote_address, path)
    LOGGER.info("🖨️  Printer: %s | Camera: %s | Box Control: %s | Light: %s", 
                state._config["name"], 
                state._config["camera"].upper(),
                "Yes" if state._config["box_control"] else "No",
                "Yes" if state._config["light"] else "No")

    async def rx_loop():
        async for raw in ws:
            try:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "ignore")
                if raw == "ok":
                    # heartbeat ack; ignore
                    continue
                msg = json.loads(raw)
            except Exception:
                continue

            # Handle different message types
            if isinstance(msg, dict) and msg.get("method") == "get":
                # Re-send a telemetry snapshot
                LOGGER.info("📡 GET request received - sending telemetry snapshot")
                await safe_send(ws, state.snapshot())
                
            elif isinstance(msg, dict) and msg.get("method") == "set":
                # Handle all control commands with detailed logging
                params = msg.get("params", {})
                handled = False
                
                # Print control commands
                if "pause" in params:
                    paused = bool(int(params.get("pause") or 0))
                    state.set_pause(paused)
                    action = "⏸️  PAUSED" if paused else "▶️  RESUMED"
                    LOGGER.info(f"🖨️  Print {action} - Progress: {state._progress}%")
                    handled = True
                    
                elif "stop" in params:
                    state.set_stop()
                    LOGGER.info("🛑 Print STOPPED - All progress reset")
                    handled = True
                
                # Temperature controls
                elif "nozzleTempControl" in params:
                    temp = float(params.get("nozzleTempControl") or 0)
                    state.set_nozzle_temp(temp)
                    LOGGER.info(f"🌡️  Nozzle temperature target set to {temp}°C")
                    handled = True
                    
                elif "bedTempControl" in params:
                    bed_control = params.get("bedTempControl", {})
                    if isinstance(bed_control, dict):
                        temp = float(bed_control.get("val", 0))
                        bed_num = bed_control.get("num", 0)
                        state.set_bed_temp(temp)
                        LOGGER.info(f"🛏️  Bed {bed_num} temperature target set to {temp}°C")
                    else:
                        temp = float(bed_control)
                        state.set_bed_temp(temp)
                        LOGGER.info(f"🛏️  Bed temperature target set to {temp}°C")
                    handled = True
                    
                elif "boxTempControl" in params or "targetBoxTemp" in params:
                    temp = float(params.get("boxTempControl") or params.get("targetBoxTemp") or 0)
                    state.set_box_temp(temp)
                    LOGGER.info(f"📦 Box temperature target set to {temp}°C")
                    handled = True
                
                # Light control (accept both legacy 'light' and expected 'lightSw')
                elif "lightSw" in params or "light" in params:
                    light_on = bool(int(params.get("lightSw") if params.get("lightSw") is not None else params.get("light") or 0))
                    state.set_light(light_on)
                    status = "💡 ON" if light_on else "💡 OFF"
                    LOGGER.info(f"Light {status}")
                    handled = True
                
                # Movement controls
                elif "autohome" in params:
                    axes = params.get("autohome", "")
                    state.set_autohome(axes)
                    LOGGER.info(f"🏠 Auto-homing axes: {axes}")
                    handled = True
                
                # Speed controls
                elif "setFeedratePct" in params:
                    pct = float(params.get("setFeedratePct") or 100)
                    state.set_feedrate(pct)
                    LOGGER.info(f"⚡ Feed rate set to {pct}%")
                    handled = True
                    
                elif "setFlowratePct" in params:
                    pct = float(params.get("setFlowratePct") or 100)
                    state.set_flowrate(pct)
                    LOGGER.info(f"💧 Flow rate set to {pct}%")
                    handled = True
                
                # G-code commands
                elif "gcodeCmd" in params:
                    cmd = params.get("gcodeCmd", "")
                    state.set_gcode_cmd(cmd)
                    LOGGER.info(f"📝 G-code command: {cmd}")
                    handled = True
                
                # Generic switch controls (for any other switch-like parameters)
                else:
                    for key, value in params.items():
                        if key not in ["pause", "stop", "light", "autohome", "gcodeCmd"]:
                            LOGGER.info(f"🔧 Control parameter: {key} = {value}")
                            handled = True
                
                if handled:
                    # Send immediate snapshot update
                    await safe_send(ws, state.snapshot())
                else:
                    LOGGER.info(f"❓ Unknown SET command: {params}")
                    
            else:
                LOGGER.info(f"📨 Received message: {msg}")

    async def tx_loop():
        # On connect, send an initial snapshot quickly
        LOGGER.info("📤 Sending initial telemetry snapshot")
        await safe_send(ws, state.snapshot())
        hb_t = 0.0
        last_snap = 0.0
        last_status_log = 0.0
        while True:
            await asyncio.sleep(0.2)
            state.tick()
            now = time.monotonic()
            
            # heartbeat every 10s
            if now - hb_t >= 10.0:
                await safe_send(ws, {"ModeCode": "heart_beat"})
                hb_t = now
                
            # telemetry every 2s
            if now - last_snap >= 2.0:
                await safe_send(ws, state.snapshot())
                last_snap = now
                
            # Status logging every 30s (less frequent to avoid spam)
            if now - last_status_log >= 30.0:
                if state.simulate_print and state._progress > 0:
                    status = "PAUSED" if state._paused else "PRINTING"
                    LOGGER.info("📊 Status: %s | Progress: %d%% | Layer: %d/%d | Nozzle: %.1f°C | Bed: %.1f°C", 
                               status, state._progress, state._current_layer, state._total_layers,
                               state._nozzle_temp, state._bed_temp)
                last_status_log = now

    try:
        await asyncio.gather(rx_loop(), tx_loop())
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        LOGGER.info("🔌 WebSocket client disconnected from %s", ws.remote_address)


async def safe_send(ws: Any, obj: Any):
    try:
        await ws.send(json.dumps(obj, separators=(",", ":")))
    except Exception:
        pass


async def main_async(host: str, port: int, model: str, simulate_print: bool):
    state = PrinterState(model=model, simulate_print=simulate_print)
    # Suppress noisy handshake errors from raw TCP probes
    try:
        import logging as _logging
        _logging.getLogger("websockets.server").setLevel(_logging.WARNING)
    except Exception:
        pass
    server = await websockets.serve(lambda ws: handle_conn(ws, state), host, port, ping_interval=None)
    LOGGER.info("🚀 Creality WebSocket Test Server Started")
    LOGGER.info("📍 Listening on: ws://%s:%d", host, port)
    LOGGER.info("🖨️  Model: %s", state._config["name"])
    LOGGER.info("📷 Camera: %s", state._config["camera"].upper())
    LOGGER.info("📦 Box Control: %s", "Yes" if state._config["box_control"] else "No")
    LOGGER.info("💡 Light: %s", "Yes" if state._config["light"] else "No")
    LOGGER.info("🖨️  Print Simulation: %s", "Yes" if simulate_print else "No")
    LOGGER.info("=" * 60)
    LOGGER.info("Ready for Home Assistant integration connections!")
    LOGGER.info("=" * 60)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    server.close()
    await server.wait_closed()


def main():
    parser = argparse.ArgumentParser(description="Creality WS test server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--model", default="k2plus", 
                       choices=["k1c", "k1", "k1max", "k1se", "k2", "k2pro", "k2plus", 
                               "e3v3", "e3v3ke", "e3v3plus", "crealityhi"],
                       help="Printer model to emulate")
    parser.add_argument("--simulate-print", action="store_true")
    args = parser.parse_args()

    asyncio.run(main_async(args.host, args.port, args.model, args.simulate_print))


if __name__ == "__main__":
    main()
