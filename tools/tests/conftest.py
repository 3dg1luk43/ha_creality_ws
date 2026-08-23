import sys
from pathlib import Path
import types
from typing import Optional
from unittest.mock import MagicMock

# Ensure repository root is on sys.path so `custom_components` imports work
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure package import path `custom_components.ha_creality_ws` resolves for relative imports
pkg_root = ROOT / "custom_components"
if str(pkg_root) not in sys.path:
    sys.path.insert(0, str(pkg_root))

pkg_name = "ha_creality_ws"
if "custom_components.ha_creality_ws" not in sys.modules:
    full_pkg = types.ModuleType(pkg_name)
    # Mark as namespace/package so submodules import from filesystem
    full_pkg.__path__ = [str(pkg_root / pkg_name)]
    sys.modules["custom_components.ha_creality_ws"] = full_pkg

# --- MOCK HOME ASSISTANT ---
ha_mod = types.ModuleType("homeassistant")
helpers_mod = types.ModuleType("homeassistant.helpers")
uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")
core_mod = types.ModuleType("homeassistant.core")
components_mod = types.ModuleType("homeassistant.components")
helpers_entity_mod = types.ModuleType("homeassistant.helpers.entity")

# Mock HomeAssistant class and callback decorator
class HomeAssistant:
    pass
core_mod.HomeAssistant = HomeAssistant
def callback(func):
    return func
core_mod.callback = callback

setattr(ha_mod, "core", core_mod)
setattr(ha_mod, "helpers", helpers_mod)
setattr(ha_mod, "components", components_mod)

sys.modules["homeassistant"] = ha_mod
sys.modules["homeassistant.core"] = core_mod
sys.modules["homeassistant.helpers"] = helpers_mod
sys.modules["homeassistant.components"] = components_mod

# --- MOCK DataUpdateCoordinator ---
class DataUpdateCoordinator:  # type: ignore
    def __init__(self, hass, logger=None, name: Optional[str] = None, update_interval=None):
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval

    def async_update_listeners(self):
        # no-op in tests
        pass
    
    # support typing subscription DataUpdateCoordinator[T]
    def __class_getitem__(cls, item):
        return cls

class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

setattr(uc_mod, "DataUpdateCoordinator", DataUpdateCoordinator)
setattr(uc_mod, "CoordinatorEntity", CoordinatorEntity)
setattr(helpers_mod, "update_coordinator", uc_mod)
sys.modules["homeassistant.helpers.update_coordinator"] = uc_mod

# --- MOCK helpers.dispatcher ---
# Installed here rather than in each test module: four of them used to do
# `if "homeassistant.helpers.dispatcher" not in sys.modules: ... MagicMock()`,
# which made the module every later import saw depend on pytest's collection
# order. Named no-ops rather than a blanket MagicMock, so a rename in the
# integration surfaces as a failure instead of silently passing. Tests that need
# to observe a dispatch monkeypatch the name the module under test imported.
dispatcher_mod = types.ModuleType("homeassistant.helpers.dispatcher")

def async_dispatcher_send(_hass, _signal, *_args) -> None:
    return None

def async_dispatcher_connect(_hass, _signal, _target):
    """Return the unsubscribe callable HA hands back."""
    return lambda: None

dispatcher_mod.async_dispatcher_send = async_dispatcher_send
dispatcher_mod.async_dispatcher_connect = async_dispatcher_connect
sys.modules["homeassistant.helpers.dispatcher"] = dispatcher_mod
helpers_mod.dispatcher = dispatcher_mod

# --- MOCK aiohttp_client ---
aiohttp_client_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
def async_get_clientsession(hass):
    return None
setattr(aiohttp_client_mod, "async_get_clientsession", async_get_clientsession)
sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client_mod
setattr(helpers_mod, "aiohttp_client", aiohttp_client_mod)

# --- MOCK components.number + const + helpers.entity_registry ---
# Needed by number.py, which test_late_discovery drives directly. Registered here
# rather than inside that test module: these are process-wide, so a per-module
# install leaks into whatever pytest collects next.
number_mod = types.ModuleType("homeassistant.components.number")

class NumberEntity:
    pass

number_mod.NumberEntity = NumberEntity
number_mod.NumberMode = MagicMock()
number_mod.NumberDeviceClass = MagicMock()
sys.modules["homeassistant.components.number"] = number_mod
components_mod.number = number_mod

# A MagicMock rather than a real module: the integration imports a moving set of
# unit constants from here (including an older-core fallback block), and every
# `from homeassistant.const import X` has to resolve. Named attributes are pinned
# where a test asserts on the value.
const_mod = MagicMock()
const_mod.PERCENTAGE = "%"
const_mod.UnitOfTemperature.CELSIUS = "°C"
sys.modules["homeassistant.const"] = const_mod
ha_mod.const = const_mod

entity_registry_mod = types.ModuleType("homeassistant.helpers.entity_registry")
# Legacy fan numbers are only created for entities that already exist, so the
# default "nothing registered" keeps a fresh setup to the modern entities.
entity_registry_mod.async_get = MagicMock(
    return_value=MagicMock(async_get_entity_id=MagicMock(return_value=None))
)
sys.modules["homeassistant.helpers.entity_registry"] = entity_registry_mod
helpers_mod.entity_registry = entity_registry_mod

# --- MOCK helpers.entity ---
class DeviceInfo:
    def __init__(self, **kwargs):
        pass
class Entity:
    pass

helpers_entity_mod.DeviceInfo = DeviceInfo
helpers_entity_mod.Entity = Entity
setattr(helpers_mod, "entity", helpers_entity_mod)
sys.modules["homeassistant.helpers.entity"] = helpers_entity_mod

# --- MOCK custom_components.ha_creality_ws.ws_client ---
ws_client_mod = types.ModuleType("custom_components.ha_creality_ws.ws_client")
import asyncio, time, contextlib  # noqa: E401

class KClient:  # type: ignore
    def __init__(self, host: str, on_message):
        self._host = host
        self._on_message = on_message
        self._task = None
        self._last = time.monotonic()

    async def start(self):
        # Simulate having a running task
        self._task = asyncio.create_task(self._noop())

    async def _noop(self):
        await asyncio.sleep(0)

    async def stop(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(Exception):
                await self._task
        self._task = None

    async def wait_first_connect(self, timeout: float = 5.0) -> bool:
        return True

    async def send_set_retry(self, **params):  # noqa: ANN001
        # Update last rx to appear fresh
        self._last = time.monotonic()
        return None

    def last_rx_monotonic(self) -> float:
        return self._last

    @property
    def is_connected(self) -> bool:
        return False

setattr(ws_client_mod, "KClient", KClient)
sys.modules["custom_components.ha_creality_ws.ws_client"] = ws_client_mod