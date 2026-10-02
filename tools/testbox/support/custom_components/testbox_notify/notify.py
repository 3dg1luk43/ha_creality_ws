"""A legacy notify platform that records every payload it is handed.

Ported from ha_washdata's test box. Here it backs `notify.plain_testbox`, the
non-mobile target: the integration must send it title and message only, and
the real `NOTIFY_SERVICE_SCHEMA` applies when the service is called, so a
payload Home Assistant would reject fails here instead of in production.

Companion-app targets are real `mobile_app` registrations instead (see
testbox_tools), so their wire format is what core actually sends.

Every call is appended to /config/notify_capture.jsonl as one JSON object per
line: {"ts", "service", "message", "title", "data", "target"}.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.notify import BaseNotificationService
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

CAPTURE_PATH = "/config/notify_capture.jsonl"


async def async_get_service(
    hass: HomeAssistant,
    config: ConfigType,
    discovery_info: DiscoveryInfoType | None = None,
) -> BaseNotificationService:
    """Set up the capture notification service."""
    return CaptureNotificationService(config.get("name", "testbox"))


class CaptureNotificationService(BaseNotificationService):
    """Append every delivered notification to a JSONL file."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    def send_message(self, message: str = "", **kwargs: Any) -> None:
        """Record the message and every extra the caller passed."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "service": self._service_name,
            "message": message,
            "title": kwargs.get("title"),
            "data": kwargs.get("data"),
            "target": kwargs.get("target"),
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        try:
            # Line-buffered append: the assertions read this file while HA is
            # still running, so a partially flushed record would be a flake.
            with open(CAPTURE_PATH, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            _LOGGER.exception("testbox_notify: could not write %s", CAPTURE_PATH)
        _LOGGER.info("testbox_notify captured: %s", line)
