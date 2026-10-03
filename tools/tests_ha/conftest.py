"""Tests against a real Home Assistant (pytest-homeassistant-custom-component).

Kept apart from tools/tests on purpose: that suite stubs Home Assistant's
modules in sys.modules, which would collide with the real ones here. These
cover what the stubbed suite cannot: config entry setup, unload and reload,
the config and options flows, and the entity and device registries (R47).

Run them where Home Assistant can be installed (Python 3.14):

    pip install -r tools/requirements-ha.txt
    python -m pytest tools/tests_ha
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest

# Imported first, so `custom_components` in sys.modules is this repository's:
# Home Assistant's test config directory ships a regular `custom_components`
# package, which would otherwise win over this repo's namespace directory.
import custom_components.ha_creality_ws  # noqa: F401,E402

pytest_plugins = ["pytest_homeassistant_custom_component"]

DOMAIN = "ha_creality_ws"
HOST = "192.0.2.10"

# A K1C's first frame, in the shape a real one sends (see the simulator's
# profiles): identity, temperatures as strings, the job fields.
K1C_FRAME: dict[str, Any] = {
    "model": "K1C",
    "hostname": "K1C-TEST",
    "modelVersion": "printer hw ver:;printer sw ver:;DWIN hw ver:CR4CU220812S11;DWIN sw ver:1.3.3.46;",
    "nozzleTemp": "24.500000",
    "bedTemp0": "23.900000",
    "targetNozzleTemp": 0,
    "targetBedTemp0": 0,
    "maxNozzleTemp": 300,
    "maxBedTemp": 100,
    "boxTemp": 24,
    "state": 0,
    "deviceState": 0,
    "err": {"errcode": 0, "key": 0},
    "withSelfTest": 0,
    "printFileName": "",
    "printProgress": 0,
    "printJobTime": 0,
    "printLeftTime": 0,
    "lightSw": 1,
    "curPosition": "X:0.00 Y:0.00 Z:0.00",
    "modelFanPct": 0,
    "caseFanPct": 0,
    "auxiliaryFanPct": 0,
    "curFeedratePct": 100,
    "curFlowratePct": 100,
    "materialStatus": 0,
    "layer": 0,
    "TotalLayer": 0,
}


class FakeClient:
    """Stands in for ws_client.KClient: no socket, frames on demand.

    `online` (class-wide, set before setup) decides whether a started client
    delivers its first frame; `start_error` makes `start` raise; `overrides`
    is laid over the K1C frame, to be another printer.
    """

    instances: list["FakeClient"] = []
    online = True
    start_error: Exception | None = None
    overrides: dict[str, Any] = {}

    def __init__(self, host: str, on_message):
        self._host = host
        self._on_message = on_message
        self._task: asyncio.Task | None = None
        self._last_rx = 0.0
        self.sent: list[dict[str, Any]] = []
        self.started = 0
        self.stopped = 0
        self.frame = {**K1C_FRAME, **FakeClient.overrides}
        self.msg_count = 0
        self.reconnect_count = 0
        self.last_error = None
        self.uptime_start = None
        self.has_connected_once = False
        self.waits: list[float] = []
        FakeClient.instances.append(self)

    @property
    def host(self) -> str:
        return self._host

    @property
    def is_connected(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_task_running(self) -> bool:
        return self.is_connected

    async def start(self) -> None:
        self.started += 1
        if FakeClient.start_error is not None:
            raise FakeClient.start_error
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        if FakeClient.online:
            await self.feed(self.frame)
        await asyncio.Event().wait()

    async def feed(self, frame: dict[str, Any]) -> None:
        self._last_rx = time.monotonic()
        self.msg_count += 1
        self.has_connected_once = True
        await self._on_message(dict(frame))

    async def stop(self) -> None:
        self.stopped += 1
        if self._task:
            self._task.cancel()
            self._task = None

    async def reconnect(self) -> None:
        self.reconnect_count += 1

    async def wait_first_connect(self, timeout: float = 5.0) -> bool:
        self.waits.append(timeout)
        return self.has_connected_once

    def last_rx_monotonic(self) -> float:
        return self._last_rx

    async def send_set_retry(self, **params: Any) -> None:
        self.sent.append(params)

    async def request_boxs_info(self) -> None:
        return None

    async def request_gcode_file_info(self) -> None:
        return None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_printer():
    """The integration talks to FakeClient, and the config flow's reachability
    probe says yes."""
    FakeClient.instances.clear()
    FakeClient.online = True
    FakeClient.start_error = None
    FakeClient.overrides = {}
    with (
        patch("custom_components.ha_creality_ws.coordinator.KClient", FakeClient),
        patch("custom_components.ha_creality_ws.config_flow._probe_tcp", return_value=True),
    ):
        yield FakeClient


def entry_data(**extra: Any) -> dict[str, Any]:
    """An entry's data as a first setup against a K1C leaves it, so a setup
    with it has nothing left to learn from the printer."""
    import json
    from pathlib import Path

    manifest = Path(__file__).parents[2] / "custom_components" / DOMAIN / "manifest.json"
    data: dict[str, Any] = {
        "host": HOST,
        "_device_info_cached": True,
        "_cached_version": json.loads(manifest.read_text(encoding="utf-8"))["version"],
        "_last_ip": HOST,
        "_cached_model": "K1C",
        "_cached_hostname": "K1C-TEST",
        "_cached_max_bed_temp": 100,
        "_cached_max_nozzle_temp": 300,
        "_cached_has_light": True,
        "_cached_has_brightness_control": False,
        "_cached_led_pin": None,
    }
    data.update(extra)
    return data
