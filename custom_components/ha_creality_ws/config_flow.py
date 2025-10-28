from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional
from .utils import extract_host_from_zeroconf as util_extract_host_from_zeroconf
import voluptuous as vol
from homeassistant import config_entries #type: ignore[import]
from homeassistant.data_entry_flow import FlowResult #type: ignore[import]
from homeassistant.helpers import config_validation as cv, selector #type: ignore[import]
from homeassistant.helpers.aiohttp_client import async_get_clientsession #type: ignore[import]
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_NAME,
    DEFAULT_NAME,
    WS_PORT,
    MJPEG_URL_TEMPLATE,
    WEBRTC_URL_TEMPLATE,
    CONF_POWER_SWITCH,
    CONF_CAMERA_MODE,
    CAM_MODE_AUTO,
    CAM_MODE_MJPEG,
    CAM_MODE_WEBRTC,
    CONF_GO2RTC_URL,
    CONF_GO2RTC_PORT,
    DEFAULT_GO2RTC_URL,
    DEFAULT_GO2RTC_PORT,
)
from .utils import ModelDetection

_LOGGER = logging.getLogger(__name__)

async def _probe_tcp(host: str, port: int, timeout: float = 2.5) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _probe_webrtc_signaling(hass, url: str, timeout: float = 1.5) -> bool:
    """Probe the Creality WebRTC signaling endpoint.
    
    Returns:
        bool: True if WebRTC signaling is available, False otherwise
    """
    session = async_get_clientsession(hass)
    try:
        async with session.head(url, timeout=timeout) as resp:
            if resp.status in (200, 204, 405):
                return True
    except Exception:
        pass
    try:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status in (200, 204, 405):
                return True
    except Exception:
        return False
    return False


def _extract_host_from_zeroconf(info: Any) -> Optional[str]:
    # Use shared helper for testability
    return util_extract_host_from_zeroconf(info)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 3

    @staticmethod
    @config_entries.HANDLERS.register("options")
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()

            if not await _probe_tcp(host, WS_PORT):
                errors["base"] = "cannot_connect"
            else:
                title = user_input.get(CONF_NAME) or f"{DEFAULT_NAME} ({host})"
                return self.async_create_entry(title=title, data={CONF_HOST: host})

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        })
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": DEFAULT_NAME}
        )

    async def async_step_zeroconf(self, discovery_info: Any) -> FlowResult:
        host = _extract_host_from_zeroconf(discovery_info)
        if not host:
            return self.async_abort(reason="cannot_connect")

        if not await _probe_tcp(host, WS_PORT):
            return self.async_abort(reason="not_K")

        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()

        title = f"{DEFAULT_NAME} ({host})"
        return self.async_create_entry(title=title, data={CONF_HOST: host})


# --------- Options Flow ---------
class OptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__()
        # Avoid deprecated `self.config_entry = config_entry`; store private reference
        self._entry = config_entry

    async def _detect_camera_type(self) -> str:
        """Detect the camera type for this printer."""
        host = self._entry.data["host"]
        
        # Get the coordinator to access printer data
        try:
            coord = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if coord and coord.data:
                # Use model detection if we have telemetry data
                printermodel = ModelDetection(coord.data)
                
                # K2 family uses WebRTC
                if printermodel.is_k2_family:
                    _LOGGER.debug("ha_creality_ws: detected K2 family printer (WebRTC)")
                    return CAM_MODE_WEBRTC
                
                # K1 family, K1 Max, K1C, Creality Hi use MJPEG
                if printermodel.is_k1_family or printermodel.is_k1_max or printermodel.is_k1c or printermodel.is_creality_hi:
                    _LOGGER.debug("ha_creality_ws: detected MJPEG camera model")
                    return CAM_MODE_MJPEG
                
                # K1 SE and Ender V3 may have optional MJPEG
                if printermodel.is_k1_se or printermodel.is_ender_v3_family:
                    _LOGGER.debug("ha_creality_ws: detected optional camera model, trying MJPEG")
                    return CAM_MODE_MJPEG
        except Exception as exc:
            _LOGGER.debug("ha_creality_ws: failed to detect camera from telemetry: %s", exc)
        
        # Fallback: probe WebRTC signaling endpoint
        webrtc_url = WEBRTC_URL_TEMPLATE.format(host=host)
        if await _probe_webrtc_signaling(self.hass, webrtc_url, timeout=2.0):
            _LOGGER.debug("ha_creality_ws: detected WebRTC via probe")
            return CAM_MODE_WEBRTC
        
        # Default to MJPEG
        _LOGGER.debug("ha_creality_ws: defaulting to MJPEG")
        return CAM_MODE_MJPEG

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            # Clean up power switch - remove if empty (text input now)
            if CONF_POWER_SWITCH in user_input:
                power_switch = user_input.get(CONF_POWER_SWITCH)
                if not power_switch or not str(power_switch).strip():
                    # Empty string - remove the key
                    user_input.pop(CONF_POWER_SWITCH, None)
                else:
                    # Valid value - keep it trimmed
                    user_input[CONF_POWER_SWITCH] = str(power_switch).strip()
            
            # If camera mode is auto, detect the actual camera type and replace it
            camera_mode = user_input.get(CONF_CAMERA_MODE)
            if camera_mode == CAM_MODE_AUTO:
                detected_type = await self._detect_camera_type()
                user_input[CONF_CAMERA_MODE] = detected_type
                _LOGGER.info("ha_creality_ws: auto mode detected camera type: %s", detected_type)
                camera_mode = detected_type
            
            # Only save go2rtc config if using WebRTC
            if camera_mode != CAM_MODE_WEBRTC:
                user_input.pop(CONF_GO2RTC_URL, None)
                user_input.pop(CONF_GO2RTC_PORT, None)
            
            return self.async_create_entry(title="Printer Configuration", data=user_input)

        # Get current values with defaults  
        current_power_switch_raw = self._entry.options.get(CONF_POWER_SWITCH)
        # Clean up any empty lists or invalid values - normalize to None or string
        current_power_switch = None
        if current_power_switch_raw:
            if isinstance(current_power_switch_raw, str) and current_power_switch_raw.strip() and "." in current_power_switch_raw:
                current_power_switch = current_power_switch_raw.strip()
            elif isinstance(current_power_switch_raw, list) and len(current_power_switch_raw) > 0:
                entity = current_power_switch_raw[0]
                if isinstance(entity, str) and entity.strip() and "." in entity:
                    current_power_switch = entity.strip()
        
        current_camera_mode = self._entry.options.get(CONF_CAMERA_MODE, CAM_MODE_AUTO)
        current_go2rtc_url = self._entry.options.get(CONF_GO2RTC_URL, DEFAULT_GO2RTC_URL)
        current_go2rtc_port = self._entry.options.get(CONF_GO2RTC_PORT, DEFAULT_GO2RTC_PORT)
        
        # Build schema - show go2rtc options if WebRTC mode is selected or if in auto mode
        show_go2rtc = current_camera_mode in (CAM_MODE_WEBRTC, CAM_MODE_AUTO)
        
        # Build schema - power switch as text input (entity_id)
        # Using text input instead of EntitySelector to allow truly optional/empty values
        schema_dict: dict[str, Any] = {
            vol.Optional(
                CONF_POWER_SWITCH,
                default=current_power_switch or "",
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                    multiline=False,
                    autocomplete="off",
                )
            ),
        }
        
        schema_dict.update({
            vol.Optional(
                CONF_CAMERA_MODE,
                default=current_camera_mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=CAM_MODE_AUTO, label="Auto (Detect)"),
                        selector.SelectOptionDict(value=CAM_MODE_MJPEG, label="MJPEG (K1 family)"),
                        selector.SelectOptionDict(value=CAM_MODE_WEBRTC, label="WebRTC (K2 family)"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        
        # Add go2rtc options if WebRTC mode is selected or in auto mode
        if show_go2rtc:
            schema_dict.update({
                vol.Optional(CONF_GO2RTC_URL, default=current_go2rtc_url): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional(CONF_GO2RTC_PORT, default=current_go2rtc_port): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
            })
        
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "power_help": "Optional power switch entity ID (e.g., switch.smart_plug_name) to enable accurate 'Off' state detection",
                "camera_help": "Camera streaming mode - Auto automatically detects based on printer model",
            }
        )