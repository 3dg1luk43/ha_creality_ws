from __future__ import annotations

import re
from typing import Any, ClassVar, Optional

__all__ = [
    "coerce_numbers",
    "parse_model_version",
    "parse_position",
    "safe_float",
    "extract_host_from_zeroconf",
    "normalize_color_hex",
    "format_filament_label",
    "build_spool_key",
    "derive_print_state",
    "BUSY_PRINT_STATES",
    "build_modify_material_payload",
    "normalize_material_color",
]


def coerce_numbers(d: dict[str, Any]) -> dict[str, Any]:
    """Convert numeric strings in a dict to numbers where safe."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, str):
            try:
                out[k] = float(v) if "." in v else int(v)
                continue
            except Exception:
                pass
        out[k] = v
    return out


def parse_model_version(s: str | None) -> tuple[str | None, str | None]:
    """Extract HW/SW versions from a semi-structured string (Creality format)."""
    if not s or not isinstance(s, str):
        return (None, None)

    parts: dict[str, Optional[str]] = {}
    for seg in s.split(";"):
        seg = seg.strip()
        if not seg or ":" not in seg:
            continue
        k, v = seg.split(":", 1)
        parts[k.strip().lower()] = (v.strip() or None)

    # Try printer versions first, then DWIN versions as fallback
    hw = parts.get("printer hw ver")
    sw = parts.get("printer sw ver")
    
    # If printer versions are empty or just whitespace, use DWIN versions (prefixed with "DWIN")
    if not hw or hw.strip() == "":
        hw = parts.get("dwin hw ver")
        if hw:
            hw = f"DWIN {hw}"
    
    if not sw or sw.strip() == "":
        sw = parts.get("dwin sw ver")
        if sw:
            sw = f"DWIN {sw}"
    
    return (hw, sw)


_POS_RE = re.compile(r"X:(?P<X>-?\d+(?:\.\d+)?)\s+Y:(?P<Y>-?\d+(?:\.\d+)?)\s+Z:(?P<Z>-?\d+(?:\.\d+)?)")


def parse_position(d: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    raw = d.get("curPosition")
    if not isinstance(raw, str):
        return (None, None, None)
    m = _POS_RE.search(raw)
    if not m:
        return (None, None, None)
    try:
        return (float(m.group("X")), float(m.group("Y")), float(m.group("Z")))
    except Exception:
        return (None, None, None)


def safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_host_from_zeroconf(info: Any) -> Optional[str]:
    """Compatibility wrapper for extract_info_from_zeroconf returning only host."""
    host, _ = extract_info_from_zeroconf(info)
    return host

def extract_info_from_zeroconf(info: Any) -> tuple[Optional[str], Optional[str]]:
    """Extract host/IP and optional MAC from zeroconf discovery info.
    
    Returns:
        (host, mac) tuple. MAC is normalized to uppercase AA:BB:CC... or None.
    """
    host: str | None = None
    mac: str | None = None
    
    if isinstance(info, dict):
        # Extract Host
        h_raw = info.get("host")
        if h_raw:
            host = str(h_raw)
        else:
            addrs_raw = info.get("addresses") or info.get("ip_addresses") or info.get("ip_address")
            if isinstance(addrs_raw, (list, tuple)) and addrs_raw:
                # Prefer IPv4 addresses when present (no ':' in string)
                v4 = next((a for a in addrs_raw if ":" not in str(a)), None)
                host = str(v4 or addrs_raw[0])
            elif isinstance(addrs_raw, str):
                host = addrs_raw
            if not host:
                hn = info.get("hostname")
                if isinstance(hn, str):
                    host = hn.strip(".")

        # Extract MAC from properties
        props = info.get("properties", {})
        if props:
            # Look for common MAC keys
            for k in ("mac", "device_mac", "serial"):
                val = props.get(k)
                if val:
                    # Basic MAC validation/normalization could go here
                    if ":" in str(val) or len(str(val)) >= 12:
                        mac = str(val).upper()
                        break
        
        # Fallback: Extract MAC from hostname if structured like "K1-AABBCC" ?
        # Not reliable enough without more knowledge.
        return (host, mac)

    # Object style (HA ZeroconfServiceInfo)
    try:
        addrs: list[str] = []
        if hasattr(info, "ip_addresses") and info.ip_addresses:
            addrs = [str(a) for a in info.ip_addresses]
        elif hasattr(info, "addresses") and info.addresses:
            addrs = [str(a) for a in info.addresses]
        
        if addrs:
            v4 = next((a for a in addrs if ":" not in a), None)
            host = v4 or addrs[0]
        elif getattr(info, "host", None):
            host = str(info.host)
        elif getattr(info, "hostname", None):
            host = str(info.hostname).rstrip(".")
            
        # Extract MAC from properties
        if hasattr(info, "properties") and info.properties:
            # Check for MAC in properties
            # Note: HA zeroconf properties are usually bytes, needing decode
            props = info.properties
            for k in (b"mac", b"device_mac", b"serial"):
                val = props.get(k)
                if val:
                    try:
                        mac_str = val.decode("utf-8")
                        mac = mac_str.upper()
                        break
                    except Exception:
                        pass
        
    except Exception:
        pass
        
    return (host, mac)

class ModelDetection:
    """Detect printer model and capabilities from telemetry data.

    Looks at both "model" (friendly) and "modelVersion" (board code like F012).
    Provides capability flags and a resolved model name if possible.
    """
    
    # Klipper output_pin name per model for LED brightness (SET_PIN dimming).
    # Keys are ModelDetection boolean-attribute names (evaluated in order); the
    # value is the Klipper output_pin name. Extend this to enable dimming for
    # more models, e.g. add "is_k2_base": "LED" once its PWM support is confirmed.
    LED_PIN_BY_MODEL: ClassVar[dict[str, str]] = {
        "is_k2_pro": "LED",
        "is_k2_plus": "LED",
    }

    def __init__(self, coord_data):
        d = coord_data or {}
        self.model = d.get("model") or ""
        self.model_l = str(self.model).lower()
        self.model_version = d.get("modelVersion") or ""
        self.model_ver_u = str(self.model_version).upper()
        
        # Check for explicit WebRTC support flag (present in 2025 models)
        self.supports_webrtc = bool(d.get("webrtcSupport") == 1)
        
        # Individual printer model detection
        # Detect specific K1 variants first so the base detector can exclude them
        # K1 SE - "K1 SE"
        self.is_k1_se = "k1 se" in self.model_l

        # K1 Max - "CR-K1 Max"
        self.is_k1_max = "cr-k1 max" in self.model_l

        # K1C - "K1C"
        self.is_k1c = "k1c" in self.model_l

        # K1 Base - "CR-K1" or an exact "k1" model. Exclude SE/C/Max variants.
        # Avoid matching substrings that would incorrectly mark variants as base.
        self.is_k1_base = (
            ("cr-k1" in self.model_l or self.model_l.strip() == "k1")
            and not (self.is_k1_se or self.is_k1_max or self.is_k1c)
        )
        
        # K2 Base/Pro/Plus via board codes (appear in modelVersion)
        self.is_k2_base = ("F021" in self.model) or ("F021" in self.model_ver_u)
        self.is_k2_pro = ("F012" in self.model) or ("F012" in self.model_ver_u)
        self.is_k2_plus = ("F008" in self.model) or ("F008" in self.model_ver_u)
        
        # Ender-3 V3 KE - "F005"
        self.is_ender_v3_ke = (
            ("F005" in self.model) or ("F005" in self.model_ver_u) or
            ("ender-3 v3 ke" in self.model_l)
        )
        
        # Ender-3 V3 Plus - "F002"
        self.is_ender_v3_plus = (
            ("F002" in self.model) or ("F002" in self.model_ver_u) or
            ("ender-3 v3 plus" in self.model_l)
        )
        
        # Ender-3 V3 - "F001"
        # Must be exactly "ender-3 v3" (not KE, Plus, or SE)
        # Check that it's not one of the variants first
        is_not_variant = not (
            self.is_ender_v3_ke or 
            self.is_ender_v3_plus
        )
        self.is_ender_v3 = (
            is_not_variant and (
                ("F001" in self.model) or ("F001" in self.model_ver_u) or
                ("ender-3 v3" in self.model_l)
            )
        )
        
        # Creality Hi - "F018"
        self.is_creality_hi = (
            ("F018" in self.model) or ("F018" in self.model_ver_u) or
            ("hi" in self.model_l)
        )
        
        # Family groupings
        # K1 Family
        self.is_k1_family = (
            self.is_k1_base or
            self.is_k1_se or
            self.is_k1_max or
            self.is_k1c or
            "k1" in self.model_l
        )
        
        # K2 Family
        self.is_k2_family = (
            self.is_k2_base or
            self.is_k2_pro or
            self.is_k2_plus or
            "k2" in self.model_l
        )
        
        # Ender-3 V3 Family
        self.is_ender_v3_family = (
            self.is_ender_v3_ke or
            self.is_ender_v3_plus or
            self.is_ender_v3 or
            ("ender" in self.model_l and "v3" in self.model_l)
        )
        
        # Feature detection
        # Chamber temperature control is only available on K2 family (Base/Pro/Plus)
        self.has_chamber_control = self.is_k2_family
        # Back-compat alias
        self.has_box_control = self.has_chamber_control

        # Chamber temperature sensor is present on K1 family (except K1 SE) and K2 family.
        # Not present on Ender V3 family, K1 SE, or Creality Hi.
        self.has_chamber_sensor = (
            (self.is_k1_base or self.is_k1c or self.is_k1_max) or self.is_k2_family
        ) and not self.is_ender_v3_family and not self.is_k1_se
        # Back-compat alias
        self.has_box_sensor = self.has_chamber_sensor

        # Light is present on most models except K1 SE and Ender V3 family
        self.has_light = not (self.is_k1_se or self.is_ender_v3_family)

        self.led_pin: str | None = next(
            (pin for attr, pin in self.LED_PIN_BY_MODEL.items() if getattr(self, attr, False)),
            None,
        )
        self.has_brightness_control = self.led_pin is not None

    # ---- Resolved/canonical model name helpers ----
    def canonical_model(self) -> str | None:
        """Return a canonical model name if derivable from codes.

        When the friendly model is missing, use modelVersion codes.
        """
        # K2 family by codes
        if self.is_k2_pro:
            return "K2 Pro"
        if self.is_k2_plus:
            return "K2 Plus"
        if self.is_k2_base:
            return "K2"
        # Ender 3 V3 family by codes
        if self.is_ender_v3_ke:
            return "Ender 3 V3 KE"
        if self.is_ender_v3_plus:
            return "Ender 3 V3 Plus"
        if self.is_ender_v3:
            return "Ender 3 V3"
        # Creality Hi
        if self.is_creality_hi:
            return "Creality Hi"
        return None

    def resolved_model(self) -> str:
        """Best-effort model string for device_info caching/UI.

        Prefer the printer-provided friendly "model", falling back to canonical
        mapping from codes, and lastly a generic label.
        """
        if self.model:
            return str(self.model)
        can = self.canonical_model()
        if can:
            return can
        return "K by Creality"


# ---------- CFS filament helpers ----------

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def _normalize_color_token(token: str) -> str:
    """Normalise one colour token to ``#rrggbb``, or return it unchanged."""
    raw = token.strip()
    if not raw:
        return token
    body = raw[1:] if raw.startswith("#") else raw
    if not _HEX_RE.match(body):
        return token
    if len(body) == 3:
        return f"#{body.lower()}"
    if len(body) >= 6:
        # Creality pads the colour with a leading character, so the *last* six
        # hex digits are the real RRGGBB value.
        return f"#{body[-6:].lower()}"
    return token


def normalize_color_hex(value: Any) -> Any:
    """Normalise a CFS filament colour to ``#rrggbb``.

    Creality RFID tags store the colour as seven hex characters -- one padding
    character followed by the real ``RRGGBB`` -- and the printer streams that
    verbatim (e.g. ``#0ffffff``). Reading the first six characters yields the
    wrong colour, so the last six are kept instead (issues #113, #117).

    Anything that is not a recognisable hex colour is returned untouched, so
    sentinels ("N/A"), named colours and unexpected formats survive intact.
    Comma/semicolon separated lists are normalised element-wise for spools that
    report more than one colour.
    """
    if not isinstance(value, str):
        return value
    for sep in (",", ";"):
        if sep in value:
            return sep.join(_normalize_color_token(part) for part in value.split(sep))
    return _normalize_color_token(value)


def format_filament_label(vendor: Any, name: Any, material_type: Any = None) -> str:
    """Build the human-readable filament label for a CFS slot.

    The printer often repeats the vendor inside the material name (vendor
    ``Generic`` with name ``Generic PLA``), which naively joining the two turned
    into "Generic Generic PLA" (issue #115). An absent vendor is left out rather
    than replaced with a guess.
    """
    vendor_txt = str(vendor).strip() if vendor not in (None, "") else ""
    name_txt = str(name).strip() if name not in (None, "") else ""
    if not name_txt:
        name_txt = str(material_type).strip() if material_type not in (None, "") else ""
    if not name_txt:
        name_txt = "Unknown"
    if not vendor_txt:
        return name_txt
    if name_txt.casefold().startswith(vendor_txt.casefold()):
        return name_txt
    return f"{vendor_txt} {name_txt}"


def _spool_key_part(value: Any) -> str:
    text = str(value).strip() if value not in (None, "") else ""
    return text.replace(" ", "-").lower()


def build_spool_key(
    *,
    rfid: Any = None,
    vendor: Any = None,
    material_type: Any = None,
    name: Any = None,
    color: Any = None,
) -> Optional[str]:
    """Derive a stable per-spool identifier for external trackers.

    The printer's ``rfid`` field is a material/filament id, so two spools of the
    same material and vendor share it even when their colours differ, which
    stops tools like spoolmansync from telling them apart (issue #117).
    Appending the normalised colour disambiguates those.

    This is a *derived* key, not a tag serial: the telemetry carries no per-tag
    serial, so two genuinely identical spools still produce the same key.
    """
    ident = _spool_key_part(rfid)
    if not ident:
        ident = "-".join(
            part
            for part in (
                _spool_key_part(vendor),
                _spool_key_part(name) or _spool_key_part(material_type),
            )
            if part
        )

    # A multi-colour spool reports several values ("#0ffa800,#0ff97e1"); join
    # them with '-' so the key stays a single flat token.
    normalized = normalize_color_hex(color)
    color_part = ""
    if isinstance(normalized, str):
        tokens = [
            token.strip().lstrip("#").lower()
            for token in re.split(r"[,;]", normalized)
            if token.strip().lstrip("#")
        ]
        if tokens and all(_HEX_RE.match(token) for token in tokens):
            color_part = "-".join(tokens)

    return "_".join(part for part in (ident, color_part) if part) or None


# --------------------------------------------------------------------------- #
# Print state
# --------------------------------------------------------------------------- #

# States in which the printer is doing something that must not be interrupted.
# The CFS card mirrors this set, and a test cross-checks the two so they cannot
# drift apart.
BUSY_PRINT_STATES = frozenset({"printing", "paused", "processing", "self-testing"})


def derive_print_state(
    data: dict[str, Any],
    *,
    power_off: bool = False,
    available: bool = True,
    paused_flag: bool = False,
) -> str:
    """Derive the printer's operational state from a telemetry snapshot.

    Extracted from ``PrintStatusSensor`` so that services can gate on the same
    notion of "busy" the user sees on the dashboard, instead of re-deriving it
    slightly differently. Keep this the only place the mapping lives.
    """
    # Highest priority: the power switch, then a lost WebSocket.
    if power_off:
        return "off"
    if not available:
        return "unknown"

    if (data.get("err") or {}).get("errcode", 0) != 0:
        return "error"

    if 1 <= (data.get("withSelfTest") or 0) <= 99:
        return "self-testing"

    state = data.get("state")
    filename = data.get("printFileName") or ""
    progress = safe_float(
        data.get("printProgress") if data.get("printProgress") is not None
        else data.get("dProgress")
    )
    progress = -1 if progress is None else int(progress)

    if filename:
        if progress >= 100:
            return "completed"
        if state == 5 or paused_flag:
            return "paused"
        if state == 4:
            return "stopped"
        if state == 1:
            return "printing"
        if state == 0:
            return "processing"

    return "idle"


# --------------------------------------------------------------------------- #
# CFS material writes
# --------------------------------------------------------------------------- #

_MATERIAL_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def build_modify_material_payload(
    *,
    box_id: int,
    slot_id: int,
    material_type: str,
    name: str | None = None,
    vendor: str | None = None,
    color: Any = None,
    min_temp: Any = None,
    max_temp: Any = None,
    pressure: Any = None,
    rfid: Any = None,
) -> dict[str, Any]:
    """Build the ``modifyMaterial`` payload the printer expects.

    Kept free of Home Assistant so it can be unit tested directly. Raises
    ``ValueError`` on input the printer would not accept, rather than coercing it
    into something that silently writes the wrong thing.

    Only keys the caller actually supplied are included: the printer merges the
    payload into the slot it already has, so emitting a default would overwrite a
    real value with a guess. ``rfid`` matters most here -- sending ``""`` wipes
    the tag association on an RFID spool.
    """
    payload: dict[str, Any] = {
        "boxId": int(box_id),
        "id": int(slot_id),
        "type": str(material_type).strip(),
    }
    if not payload["type"]:
        raise ValueError("material type must not be empty")

    for key, value in (("name", name), ("vendor", vendor)):
        if value is not None and str(value).strip():
            payload[key] = str(value).strip()

    if color is not None:
        payload["color"] = normalize_material_color(color)

    low = safe_float(min_temp)
    high = safe_float(max_temp)
    if low is not None and high is not None and high < low:
        raise ValueError(
            f"max_temp ({high}) must not be below min_temp ({low})"
        )
    if low is not None:
        payload["minTemp"] = low
    if high is not None:
        payload["maxTemp"] = high

    advance = safe_float(pressure)
    if advance is not None:
        if not 0.0 <= advance <= 1.0:
            raise ValueError(f"pressure must be between 0 and 1, got {advance}")
        payload["pressure"] = advance

    # Pass an existing tag id straight through; never substitute a placeholder.
    if rfid is not None and str(rfid).strip():
        payload["rfid"] = str(rfid).strip()

    return payload


def normalize_material_color(value: Any) -> str:
    """Validate a colour for *writing* and return it as lowercase ``#rrggbb``.

    Deliberately stricter than :func:`normalize_color_hex`, which normalises
    whatever the printer happens to stream. A write has to be exact, so anything
    that is not a single six-digit hex colour is rejected -- including the
    comma-separated multi-colour form, which cannot be expressed as one value and
    would otherwise be silently flattened.
    """
    if isinstance(value, (list, tuple)):
        raise ValueError(
            "colour must be a '#rrggbb' string, not an RGB list; "
            "the color_rgb selector is not used for this field"
        )
    text = str(value).strip()
    if re.search(r"[,;]", text):
        raise ValueError(
            f"cannot write a multi-colour value ({text!r}) as a single colour"
        )
    if not _MATERIAL_COLOR_RE.match(text):
        raise ValueError(f"colour must be six hex digits, got {text!r}")
    return f"#{text.lstrip('#').lower()}"
