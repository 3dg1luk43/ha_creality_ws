"""Test-box helpers that need to run inside Home Assistant.

Two things a REST client cannot do from outside:

* **Capture companion-app pushes.** A fake phone registered through the real
  `mobile_app` registration API gets a `push_url` pointing at
  `/api/testbox_push/<name>` below. `mobile_app` then posts exactly what it
  would post to the push relay, after its own Live Activity routing, and this
  view appends each body to /config/push_capture.jsonl. That is the wire format
  the relay and the phone see, which no unit test can observe.
* **Inject a zeroconf discovery.** `testbox_tools.zeroconf_discover` starts the
  integration's zeroconf step with a real `ZeroconfServiceInfo`, since the mock
  printer does not advertise over mDNS.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from homeassistant.helpers.typing import ConfigType

DOMAIN = "testbox_tools"
_LOGGER = logging.getLogger(__name__)

PUSH_CAPTURE_PATH = "/config/push_capture.jsonl"
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

ZEROCONF_SCHEMA = vol.Schema(
    {
        vol.Required("host"): cv.string,
        vol.Optional("hostname", default="creality-k1c.local."): cv.string,
        vol.Optional("name", default="creality-k1c._http._tcp.local."): cv.string,
        vol.Optional("type", default="_http._tcp.local."): cv.string,
        vol.Optional("port", default=80): cv.port,
        vol.Optional("properties", default={}): dict,
        vol.Optional("domain", default="ha_creality_ws"): cv.string,
    }
)


class PushCaptureView(HomeAssistantView):
    """Stand-in for the push relay: record the body, answer like the relay."""

    url = "/api/testbox_push/{name}"
    name = "api:testbox_push"
    requires_auth = False

    async def post(self, request: web.Request, name: str) -> web.Response:
        body: Any = await request.json()
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "device": name,
            "body": body,
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        hass: HomeAssistant = request.app["hass"]
        await hass.async_add_executor_job(_append, line)
        _LOGGER.info("testbox push captured for %s", name)
        return self.json({"rateLimits": {"successful": 1, "maximum": 500}}, status_code=201)


def _append(line: str) -> None:
    with open(PUSH_CAPTURE_PATH, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(PushCaptureView())

    async def zeroconf_discover(call: ServiceCall) -> ServiceResponse:
        host = call.data["host"]
        info = ZeroconfServiceInfo(
            ip_address=ip_address(host),
            ip_addresses=[ip_address(host)],
            port=call.data["port"],
            hostname=call.data["hostname"],
            type=call.data["type"],
            name=call.data["name"],
            properties=dict(call.data["properties"]),
        )
        result = await hass.config_entries.flow.async_init(
            call.data["domain"], context={"source": "zeroconf"}, data=info
        )
        _LOGGER.info("testbox zeroconf flow result: %s", result)
        return {
            key: str(result.get(key))
            for key in ("type", "reason", "step_id", "flow_id")
            if result.get(key) is not None
        }

    hass.services.async_register(
        DOMAIN,
        "zeroconf_discover",
        zeroconf_discover,
        schema=ZEROCONF_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True
