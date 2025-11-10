from __future__ import annotations
import logging
from typing import Any, Callable
from .utils import parse_position as _parse_position, safe_float as _safe_float

from homeassistant.components.sensor import (  # type: ignore[import]
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from .entity import KEntity
from datetime import timedelta
from homeassistant.helpers.event import async_track_time_interval  # type: ignore[import]
from .const import DOMAIN
from .utils import ModelDetection

_LOGGER = logging.getLogger(__name__)



# Unit compatibility across HA versions
try:
    from homeassistant.const import ( #type: ignore[import]
        UnitOfTemperature as UTemp,
        UnitOfLength as ULen,
        PERCENTAGE as U_PERCENT,
        UnitOfTime as UTime,
    )
    U_C = UTemp.CELSIUS
    U_MM = ULen.MILLIMETERS
    U_CM = ULen.CENTIMETERS
    U_S = UTime.SECONDS
except Exception:  # older cores fallback (keep compat with older HA constants)
    from homeassistant.const import ( #type: ignore[import]
        TEMP_CELSIUS as U_C,
        LENGTH_MILLIMETERS as U_MM,
        LENGTH_CENTIMETERS as U_CM,
        PERCENTAGE as U_PERCENT,
        TIME_SECONDS as U_S,
    )

# (imports duplicated above; keep only one set)


# ----------------- helpers -----------------

def _attr_dict(*pairs: tuple[str, Any]) -> dict[str, Any]:
    return {k: v for (k, v) in pairs if v is not None}

# position parsing moved to utils.parse_position


# ----------------- dynamic “simple field” sensors -----------------

# Use special fields for computed/fallback values:
#   "__pos_x__", "__pos_y__", "__pos_z__" from curPosition
#   "__progress__" -> printProgress or dProgress
SPECS: list[dict[str, Any]] = [
    # Temperatures
    {
        "uid": "bed_temperature",
        "name": "Bed Temperature",
        "field": "bedTemp0",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": U_C,
        "attrs": lambda d: _attr_dict(
            ("target", d.get("targetBedTemp0")),
            ("max", d.get("maxBedTemp")),
        ),
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "uid": "box_temperature",
        "name": "Box Temperature",
        "field": "boxTemp",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": U_C,
        "attrs": lambda d: _attr_dict(
            ("target", d.get("targetBoxTemp")),
            ("max", d.get("maxBoxTemp")),
        ),
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "uid": "nozzle_temperature",
        "name": "Nozzle Temperature",
        "field": "nozzleTemp",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": U_C,
        "attrs": lambda d: _attr_dict(
            ("target", d.get("targetNozzleTemp")),
            ("max", d.get("maxNozzleTemp")),
        ),
        "state_class": SensorStateClass.MEASUREMENT,
    },

    # Print progress (with fallback)
    {
        "uid": "print_progress",
        "name": "Print Progress",
        "field": "__progress__",
        "device_class": None,
        "unit": U_PERCENT,
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },

    # Layers
    {
        "uid": "total_layers",
        "name": "Total Layers",
        "field": "TotalLayer",
        "device_class": None,
        "unit": None,
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "uid": "current_layer",
        "name": "Working Layer",
        "field": "layer",
        "device_class": None,
        "unit": None,
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },

    # Positions (computed from curPosition)
    {
        "uid": "position_x",
        "name": "Position X",
        "field": "__pos_x__",
        "device_class": None,
        "unit": "mm",
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "uid": "position_y",
        "name": "Position Y",
        "field": "__pos_y__",
        "device_class": None,
        "unit": "mm",
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "uid": "position_z",
        "name": "Position Z",
        "field": "__pos_z__",
        "device_class": None,
        "unit": "mm",
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },

    # Speed/flow (current)
    {
        "uid": "feedrate_pct",
        "name": "Print Speed %",
        "field": "curFeedratePct",
        "device_class": None,
        "unit": "%",
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "uid": "flowrate_pct",
        "name": "Flow Rate %",
        "field": "curFlowratePct",
        "device_class": None,
        "unit": "%",
        "attrs": lambda d: {},
        "state_class": SensorStateClass.MEASUREMENT,
    },

    # System summary
    {
        "uid": "system",
        "name": "System",
        "field": "model",
        "device_class": None,
        "unit": None,
        "attrs": lambda d: _attr_dict(
            ("hostname", d.get("hostname")),
            ("modelVersion", d.get("modelVersion")),
        ),
        "state_class": None,
    },
]


class KSimpleFieldSensor(KEntity, SensorEntity):
    """Generic sensor bound to one telemetry field or a special computed field."""

    def __init__(self, coordinator, spec: dict[str, Any]):
        super().__init__(coordinator, spec["name"], spec["uid"])
        self._field: str = spec["field"]
        self._attr_device_class = spec.get("device_class")
        self._attr_native_unit_of_measurement = spec.get("unit")
        self._attr_state_class = spec.get("state_class")
        self._get_attrs: Callable[[dict[str, Any]], dict[str, Any]] = spec.get("attrs") or (lambda d: {})

    def _zero_value(self):
        """Return appropriate zero value for offline/off state."""
        if self._field in ("TotalLayer", "layer"):
            return 0
        if self._attr_native_unit_of_measurement is None and self._field not in ("__pos_x__", "__pos_y__", "__pos_z__", "__progress__"):
            return None
        return 0

    @property
    def native_value(self):
        # System entity (model) uses cached data from config entry, never live data
        if self._field == "model":
            cached_info = self._get_cached_device_info()
            if cached_info and cached_info.get("model"):
                return cached_info.get("model")
            # Fallback to current data if no cached model
            d = self.coordinator.data
            return d.get(self._field) if d else None
        
        d = self.coordinator.data
        
        # System entity should not be zeroed when printer is off - it shows cached info
        if self._should_zero() and self._field != "model":
            return self._zero_value()

        # Position parsing (computed from curPosition string)
        if self._field in ("__pos_x__", "__pos_y__", "__pos_z__"):
            x, y, z = _parse_position(d)
            return {"__pos_x__": x, "__pos_y__": y, "__pos_z__": z}[self._field]

        # Print progress
        if self._field == "__progress__":
            return d.get("printProgress") or d.get("dProgress")

        return d.get(self._field)

    @property
    def extra_state_attributes(self):
        # System entity (model) uses cached data from config entry, never live data
        if self._field == "model":
            cached_info = self._get_cached_device_info()
            if cached_info:
                d = {}
                if cached_info.get("hostname"):
                    d["hostname"] = cached_info.get("hostname")
                if cached_info.get("modelVersion"):
                    d["modelVersion"] = cached_info.get("modelVersion")
                return d
        
        return self._get_attrs(self.coordinator.data)

class PrintStatusSensor(KEntity, SensorEntity):
    _attr_name = "Print Status"
    _attr_icon = "mdi:printer-3d"

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "print_status")

    @property
    def native_value(self) -> str | None:
        # HIGHEST PRIORITY: Check the power switch first.
        if self.coordinator.power_is_off():
            return "off"

        # SECOND PRIORITY: Check for a lost WebSocket connection.
        if not self.coordinator.available:
            return "unknown"

        # If we get here, the printer is ON and CONNECTED.
        # Now, determine the operational state.
        d = self.coordinator.data or {}

        if d.get("err", {}).get("errcode", 0) != 0:
            return "error"

        if 1 <= d.get("withSelfTest", 0) <= 99:
            return "self-testing"

        st = d.get("state")
        fname = d.get("printFileName") or ""
        progress = d.get("printProgress") or d.get("dProgress")

        # Ensure progress is a number before comparing
        try:
            progress = int(progress) if progress is not None else -1
        except (ValueError, TypeError):
            progress = -1

        if fname:
            if progress >= 100:
                return "completed"
            # THIS IS THE LINE THAT WAS BROKEN
            if st == 5 or self.coordinator.paused_flag():
                return "paused"
            if st == 4:
                return "stopped"
            if st == 1:
                return "printing"
            if st == 0:
                return "processing"

        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        attrs = {
            "file": d.get("printFileName") or "",
            "progress": d.get("printProgress") or d.get("dProgress"),
            "job_time_s": d.get("printJobTime"),
            "left_time_s": d.get("printLeftTime"),
            "used_material_mm": d.get("usedMaterialLength"),
            "real_time_flow_mm3_s": _safe_float(d.get("realTimeFlow")),
            "paused_flag": self.coordinator.paused_flag(),
            "state_raw": d.get("state"),
            "err": d.get("err"),
        }
        err_code = d.get("err", {}).get("errcode", 0)
        if err_code != 0:
            attrs["error_code"] = err_code
            # The error message mapping function is not yet implemented, so it remains commented out.
            # attrs["error_message"] = self._map_error_code_to_message(err_code)
        
        return attrs


class UsedMaterialLengthSensor(KEntity, SensorEntity):
    _attr_name = "Used Material Length"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = U_CM
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "used_material_length")

    @property
    def native_value(self) -> float | None:
        if self._should_zero():
            return 0.0
        v = self.coordinator.data.get("usedMaterialLength")
        try:
            mm = float(v)
            return round(mm / 10.0, 2)
        except (TypeError, ValueError):
            return None

class PrintJobTimeSensor(KEntity, SensorEntity):
    _attr_name = "Print Job Time"
    _attr_icon = "mdi:timer-play"
    _attr_native_unit_of_measurement = U_S
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "print_job_time")

    @property
    def native_value(self) -> int | None:
        if self._should_zero():
            return 0
        v = self.coordinator.data.get("printJobTime")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

class PrintLeftTimeSensor(KEntity, SensorEntity):
    _attr_name = "Print Time Left"
    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = U_S
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "print_left_time")

    @property
    def native_value(self) -> int | None:
        if self._should_zero():
            return 0
        v = self.coordinator.data.get("printLeftTime")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

class RealTimeFlowSensor(KEntity, SensorEntity):
    _attr_name = "Real-Time Flow"
    _attr_icon = "mdi:cube-send"
    _attr_native_unit_of_measurement = "mm³/s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "real_time_flow")

    @property
    def native_value(self) -> float | None:
        if self._should_zero():
            return 0.0
        return _safe_float(self.coordinator.data.get("realTimeFlow"))


class CurrentObjectSensor(KEntity, SensorEntity):
    _attr_name = "Current Object"
    _attr_icon = "mdi:cube-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "current_object")

    @property
    def native_value(self) -> str | None:
        # If printer is off or unavailable, show N/A
        if self._should_zero():
            return "N/A"
        
        d = self.coordinator.data or {}
        v = d.get("current_object") or d.get("currentObject")
        
        # If no current object and printer is not printing, show "not printing"
        if not v or v.strip() == "":
            # Check if printer is actually printing
            fname = d.get("printFileName") or ""
            if not fname:
                return "not printing"
            return None
        
        return str(v)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        return {
            "excluded_objects": d.get("excluded_objects_list", d.get("excluded_objects")),
        }


class ObjectCountSensor(KEntity, SensorEntity):
    _attr_name = "Object Count"
    _attr_icon = "mdi:format-list-numbered"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "object_count")

    @property
    def native_value(self) -> int | None:
        # If printer is off or unavailable, show 0
        if self._should_zero():
            return 0
        
        d = self.coordinator.data or {}
        
        # Check if printer is actually printing
        fname = d.get("printFileName") or ""
        if not fname:
            return 0  # "not printing" equivalent for numeric sensor
        
        # Try to get objects data from various possible fields
        objs = d.get("objects_list") or d.get("objectsList") or d.get("objects")
        
        # Handle JSON string format (from diagnostic logs)
        if isinstance(objs, str):
            try:
                import json
                parsed_objs = json.loads(objs)
                if isinstance(parsed_objs, list):
                    return len(parsed_objs)
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Handle array format
        if isinstance(objs, list):
            return len(objs)
        
        # Handle dict format with list inside
        if isinstance(objs, dict):
            lst = objs.get("list")
            if isinstance(lst, list):
                return len(lst)
        
        return None


class KPrintControlSensor(KEntity, SensorEntity):
    """Diagnostic sensor exposing control pipeline state (queued actions, paused flag, raw states)."""
    _attr_name = "Print Control"
    _attr_icon = "mdi:debug-step-over"
    _attr_state_class = None  # not a measurement

    def __init__(self, coordinator):
        super().__init__(coordinator, self._attr_name, "print_control")

    @property
    def native_value(self) -> str | None:
        # Keep state human-readable but stable: "queued" if anything is pending, else "ok".
        if self.coordinator.pending_pause() or self.coordinator.pending_resume():
            return "queued"
        return "ok" if self.coordinator.available else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        return {
            "pending_pause": self.coordinator.pending_pause(),
            "pending_resume": self.coordinator.pending_resume(),
            "paused": self.coordinator.paused_flag(),
            # raw hints (useful for debugging UI logic)
            "status_raw_state": d.get("state"),
            "status_raw_deviceState": d.get("deviceState"),
            "print_file": d.get("printFileName") or "",
            "progress": d.get("printProgress") or d.get("dProgress"),
        }

# ----------------- setup -----------------

async def async_setup_entry(hass, entry, async_add_entities):
    coord = hass.data[DOMAIN][entry.entry_id]
    ents: list[SensorEntity] = []

    # Core sensors
    ents.append(PrintStatusSensor(coord))

    # Add box temperature if supported by model. Also allow live-telemetry fallback if cache missing.
    has_box_sensor = entry.data.get("_cached_has_box_sensor", False)
    live = coord.data or {}
    if not has_box_sensor:
        # Heuristics: if boxTemp or targetBoxTemp appears, expose the sensor.
        if any(k in live for k in ("boxTemp", "targetBoxTemp", "maxBoxTemp")):
            has_box_sensor = True
    
    for spec in SPECS:
        if spec.get("uid") == "box_temperature":
            if has_box_sensor:
                ents.append(KSimpleFieldSensor(coord, spec))
        else:
            ents.append(KSimpleFieldSensor(coord, spec))

    # Additional metrics
    ents.extend([
        UsedMaterialLengthSensor(coord),
        PrintJobTimeSensor(coord),
        PrintLeftTimeSensor(coord),
        RealTimeFlowSensor(coord),
        CurrentObjectSensor(coord),
        ObjectCountSensor(coord),
        KPrintControlSensor(coord),
    ])

    async_add_entities(ents)