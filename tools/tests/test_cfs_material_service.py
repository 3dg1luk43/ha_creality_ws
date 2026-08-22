"""The set_cfs_material handler, actually executed.

utils.build_modify_material_payload and utils.derive_print_state are unit tested
elsewhere; this drives the service handler itself, which is the part that talks
to the printer. Home Assistant is not installed, so the modules it imports are
stubbed narrowly -- named attributes rather than blanket MagicMocks, so a rename
in the integration shows up as a failure here instead of silently passing.

Skipped when voluptuous is unavailable: a stubbed schema would make the
validation assertions meaningless. CI installs it (see .github/workflows/
tests.yml), so in practice this only skips in a local venv without it.
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]

requires_voluptuous = pytest.mark.skipif(
    importlib.util.find_spec("voluptuous") is None,
    reason="voluptuous is not installed (expected in CI)",
)


_ABSENT = object()


class _ModuleState:
    """Records every sys.modules and attribute change so it can be undone.

    The stubs below are process-wide. Without a restore, whichever test module
    pytest happens to collect after this one inherits them, which makes the whole
    suite order-dependent.
    """

    def __init__(self) -> None:
        self._modules: dict[str, object] = {}
        self._attrs: list[tuple[object, str, object]] = []

    def note_module(self, name: str) -> None:
        self._modules.setdefault(name, sys.modules.get(name, _ABSENT))

    def note_attr(self, obj: object, attr: str) -> None:
        self._attrs.append((obj, attr, getattr(obj, attr, _ABSENT)))

    def set_attr(self, obj: object, attr: str, value: object) -> None:
        self.note_attr(obj, attr)
        setattr(obj, attr, value)

    def restore(self) -> None:
        for obj, attr, old in reversed(self._attrs):
            if old is _ABSENT:
                try:
                    delattr(obj, attr)
                except AttributeError:
                    pass
            else:
                setattr(obj, attr, old)
        for name, old in self._modules.items():
            if old is _ABSENT:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _stub(state: "_ModuleState", name: str, **attrs) -> types.ModuleType:
    state.note_module(name)
    existing = sys.modules.get(name)
    module = existing or types.ModuleType(name)
    for key, value in attrs.items():
        # An existing module is mutated in place, so its attributes have to be
        # remembered individually; a fresh one is dropped wholesale.
        if existing is not None:
            state.set_attr(module, key, value)
        else:
            setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_integration():
    """Execute custom_components/ha_creality_ws/__init__.py under stubs.

    Returns the loaded module, the notification sink and the error type, plus a
    callable that undoes every global change made here.
    """
    import homeassistant.core as core  # provided by conftest

    state = _ModuleState()

    class ServiceCall:
        def __init__(self, data=None):
            self.data = data or {}

    state.set_attr(core, "ServiceCall", ServiceCall)

    class HomeAssistantError(Exception):
        pass

    class ServiceValidationError(HomeAssistantError):
        pass

    _stub(state, "homeassistant.config_entries", ConfigEntry=object, OperationNotAllowed=Exception)
    _stub(
        state,
        "homeassistant.exceptions",
        ConfigEntryNotReady=Exception,
        HomeAssistantError=HomeAssistantError,
        ServiceValidationError=ServiceValidationError,
    )
    _stub(
        state,
        "homeassistant.helpers.event",
        async_track_time_interval=lambda *a, **k: None,
        async_track_state_change_event=lambda *a, **k: None,
    )
    _stub(state, "homeassistant.helpers.typing", ConfigType=dict)
    # homeassistant.helpers is a plain module in conftest, so submodule imports
    # need a __path__ before they will resolve at all.
    helpers_mod = sys.modules["homeassistant.helpers"]
    if not hasattr(helpers_mod, "__path__"):
        state.set_attr(helpers_mod, "__path__", [])
    _stub(
        state,
        "homeassistant.helpers.dispatcher",
        async_dispatcher_send=lambda *a, **k: None,
        async_dispatcher_connect=lambda *a, **k: None,
    )
    _stub(
        state,
        "homeassistant.const",
        CONF_HOST="host",
        CONF_PORT="port",
        EVENT_HOMEASSISTANT_STOP="homeassistant_stop",
        Platform=MagicMock(),
    )
    # config_validation: only cv.string is used by the schema.
    _stub(state, "homeassistant.helpers.config_validation", string=str)
    _stub(state, "homeassistant.helpers.device_registry", async_get=MagicMock())
    _stub(state, "homeassistant.helpers.entity_registry", async_get=MagicMock())
    _stub(state, "homeassistant.helpers.aiohttp_client", async_get_clientsession=MagicMock())

    notifications = []
    _stub(
        state,
        "homeassistant.components.persistent_notification",
        async_create=lambda hass, **kw: notifications.append(kw),
    )

    helpers = sys.modules["homeassistant.helpers"]
    for attr in ("config_validation", "device_registry", "entity_registry"):
        state.set_attr(helpers, attr, sys.modules[f"homeassistant.helpers.{attr}"])

    # Loaded under its real package name: __init__.py uses relative imports
    # (`from .const import ...`), which only resolve if the module is registered
    # as custom_components.ha_creality_ws. conftest puts a bare namespace module
    # there; this replaces it with the executed one, keeping __path__ so other
    # test modules can still import the submodules.
    name = "custom_components.ha_creality_ws"
    pkg_dir = ROOT / "custom_components" / "ha_creality_ws"
    spec = importlib.util.spec_from_file_location(
        name, pkg_dir / "__init__.py", submodule_search_locations=[str(pkg_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    state.note_module(name)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, notifications, ServiceValidationError, state


@pytest.fixture(scope="module")
def integration():
    if importlib.util.find_spec("voluptuous") is None:
        pytest.skip("voluptuous is not installed")
    module, notifications, error, state = _load_integration()
    # The service filters hass.data entries with isinstance(coord, KCoordinator).
    # Point that name at the stand-in rather than building a real coordinator,
    # which would drag in the whole config-entry lifecycle.
    state.set_attr(module, "KCoordinator", FakeCoordinator)
    try:
        yield module, notifications, error
    finally:
        state.restore()


class FakeClient:
    def __init__(self, host="1.2.3.4", fail=False):
        self.host = host
        self.sent = []
        self.boxs_info_requests = 0
        self._fail = fail

    async def send_set_retry(self, **params):
        if self._fail:
            raise RuntimeError("printer link not available")
        self.sent.append(params)

    async def request_boxs_info(self):
        self.boxs_info_requests += 1


class FakeCoordinator:
    """Stands in for KCoordinator: only what the service touches."""

    def __init__(self, host="1.2.3.4", state=None, fail=False):
        self.client = FakeClient(host, fail=fail)
        self.data = state if state is not None else {}
        self.available = True

    def power_is_off(self):
        return False

    def paused_flag(self):
        return False


# Coroutines the service scheduled via hass.async_create_task. Closed after each
# test so an unawaited readback does not warn.
_SCHEDULED: list = []


@pytest.fixture(autouse=True)
def _close_scheduled_tasks():
    yield
    for coro in _SCHEDULED:
        coro.close()
    _SCHEDULED.clear()


def _make_hass(integration, coordinators, devices=None):
    """A hass stub carrying the pieces the service reads."""
    module, _notifications, _err = integration
    domain = module.DOMAIN

    services = {}

    class Services:
        def has_service(self, _domain, name):
            return name in services

        def async_register(self, _domain, name, handler, schema=None):
            services[name] = (handler, schema)

    tasks = _SCHEDULED

    class Hass:
        def __init__(self):
            self.data = {domain: dict(coordinators)}
            self.services = Services()

        def async_create_task(self, coro):
            tasks.append(coro)
            return MagicMock()

    hass = Hass()

    # device_registry.async_get(hass).async_get(device_id) -> device
    registry = MagicMock()
    registry.async_get.side_effect = lambda dev_id: (devices or {}).get(dev_id)
    module.dr.async_get = MagicMock(return_value=registry)

    return hass, services, tasks


def _register(integration, hass):
    module, _, _ = integration
    asyncio.run(module._register_custom_services(hass))


def _device(*entry_ids):
    dev = MagicMock()
    dev.config_entries = set(entry_ids)
    return dev


IDLE = {"printFileName": "", "state": 0}
PRINTING = {"printFileName": "a.gcode", "state": 1, "printProgress": 42}


# --------------------------------------------------------------------------- #
# Device targeting
# --------------------------------------------------------------------------- #


@requires_voluptuous
def test_a_device_resolves_to_its_own_coordinator(integration):
    module, _, _ = integration
    a, b = FakeCoordinator("printer-a"), FakeCoordinator("printer-b")
    hass, _, _ = _make_hass(
        integration, {"entry_a": a, "entry_b": b}, {"dev_a": _device("entry_a")}
    )
    with_patch = module._coordinators_for_devices(hass, ["dev_a"])
    assert [c.client.host for c in with_patch] == ["printer-a"]


@requires_voluptuous
def test_no_device_targets_every_printer(integration):
    """request_cfs_info relies on this to mean "refresh everything"."""
    module, _, _ = integration
    hass, _, _ = _make_hass(
        integration, {"a": FakeCoordinator("printer-a"), "b": FakeCoordinator("printer-b")}
    )
    assert len(module._coordinators_for_devices(hass, None)) == 2


@requires_voluptuous
def test_an_unknown_device_matches_nothing(integration):
    """It must not silently fall back to targeting everything."""
    module, _, _ = integration
    hass, _, _ = _make_hass(integration, {"a": FakeCoordinator()}, {})
    assert module._coordinators_for_devices(hass, ["nope"]) == []


# --------------------------------------------------------------------------- #
# The handler
# --------------------------------------------------------------------------- #


def _call_service(integration, hass, services, data):
    module, _, _ = integration
    handler, schema = services["set_cfs_material"]
    validated = schema(data) if schema else data
    ServiceCall = sys.modules["homeassistant.core"].ServiceCall
    asyncio.run(handler(ServiceCall(validated)))


@requires_voluptuous
def test_a_valid_call_sends_modify_material(integration):
    coord = FakeCoordinator(state=IDLE)
    hass, services, _ = _make_hass(
        integration, {"entry": coord}, {"dev": _device("entry")}
    )
    _register(integration, hass)
    _call_service(integration, hass, services, {
        "device_id": ["dev"], "box_id": 1, "slot_id": 2, "type": "PETG",
        "name": "Hyper PETG", "vendor": "Creality", "color": "#ff00aa",
        "min_temp": 230, "max_temp": 260, "pressure": 0.03,
    })
    assert len(coord.client.sent) == 1
    payload = coord.client.sent[0]["modifyMaterial"]
    assert payload["boxId"] == 1 and payload["id"] == 2
    assert payload["type"] == "PETG" and payload["color"] == "#ff00aa"
    assert payload["minTemp"] == 230.0 and payload["pressure"] == 0.03
    # Not supplied, so it must not appear -- sending "" would wipe a real tag.
    assert "rfid" not in payload


@requires_voluptuous
def test_a_supplied_rfid_is_passed_through(integration):
    coord = FakeCoordinator(state=IDLE)
    hass, services, _ = _make_hass(integration, {"e": coord}, {"d": _device("e")})
    _register(integration, hass)
    _call_service(integration, hass, services, {
        "device_id": ["d"], "box_id": 1, "slot_id": 0, "type": "PLA", "rfid": "001001",
    })
    assert coord.client.sent[0]["modifyMaterial"]["rfid"] == "001001"


@requires_voluptuous
def test_writing_is_refused_while_printing(integration):
    module, _, ServiceValidationError = integration
    coord = FakeCoordinator(state=PRINTING)
    hass, services, _ = _make_hass(integration, {"e": coord}, {"d": _device("e")})
    _register(integration, hass)
    with pytest.raises(ServiceValidationError, match="printing"):
        _call_service(integration, hass, services, {
            "device_id": ["d"], "box_id": 1, "slot_id": 0, "type": "PLA",
        })
    assert coord.client.sent == [], "nothing may reach the printer"


@requires_voluptuous
def test_a_busy_second_printer_blocks_before_the_first_is_written(integration):
    """The guard runs over every target before any write, so a refusal cannot
    leave one printer modified and another not."""
    module, _, ServiceValidationError = integration
    idle, busy = FakeCoordinator("idle-one", IDLE), FakeCoordinator("busy-one", PRINTING)
    hass, services, _ = _make_hass(
        integration,
        {"e1": idle, "e2": busy},
        {"d1": _device("e1"), "d2": _device("e2")},
    )
    _register(integration, hass)
    with pytest.raises(ServiceValidationError):
        _call_service(integration, hass, services, {
            "device_id": ["d1", "d2"], "box_id": 1, "slot_id": 0, "type": "PLA",
        })
    assert idle.client.sent == [], "the idle printer must not have been written"


@requires_voluptuous
def test_an_empty_device_list_is_refused_not_treated_as_all_printers(integration):
    """`device_id: []` passes the schema, and a falsy target means "everything".

    That is what request_cfs_info wants, but for a write service it would push
    this payload to every configured printer.
    """
    _module, _notifications, error = integration
    a = FakeCoordinator("printer-a", IDLE)
    b = FakeCoordinator("printer-b", IDLE)
    hass, services, _ = _make_hass(
        integration, {"e1": a, "e2": b}, {"d1": _device("e1"), "d2": _device("e2")}
    )
    _register(integration, hass)
    with pytest.raises(error):
        _call_service(integration, hass, services, {
            "device_id": [], "box_id": 1, "slot_id": 0, "type": "PLA",
        })
    assert a.client.sent == [], "no printer may be written"
    assert b.client.sent == []


@requires_voluptuous
def test_an_unresolvable_device_raises(integration):
    module, _, ServiceValidationError = integration
    hass, services, _ = _make_hass(integration, {"e": FakeCoordinator(state=IDLE)}, {})
    _register(integration, hass)
    with pytest.raises(ServiceValidationError, match="No Creality printer"):
        _call_service(integration, hass, services, {
            "device_id": ["ghost"], "box_id": 1, "slot_id": 0, "type": "PLA",
        })


@requires_voluptuous
def test_a_bad_colour_raises_before_anything_is_sent(integration):
    module, _, ServiceValidationError = integration
    coord = FakeCoordinator(state=IDLE)
    hass, services, _ = _make_hass(integration, {"e": coord}, {"d": _device("e")})
    _register(integration, hass)
    with pytest.raises(ServiceValidationError):
        _call_service(integration, hass, services, {
            "device_id": ["d"], "box_id": 1, "slot_id": 0, "type": "PLA",
            "color": "not-a-colour",
        })
    assert coord.client.sent == []


@requires_voluptuous
def test_an_inverted_temperature_range_raises(integration):
    module, _, ServiceValidationError = integration
    coord = FakeCoordinator(state=IDLE)
    hass, services, _ = _make_hass(integration, {"e": coord}, {"d": _device("e")})
    _register(integration, hass)
    with pytest.raises(ServiceValidationError, match="max_temp"):
        _call_service(integration, hass, services, {
            "device_id": ["d"], "box_id": 1, "slot_id": 0, "type": "PLA",
            "min_temp": 240, "max_temp": 200,
        })
    assert coord.client.sent == []


@requires_voluptuous
def test_a_printer_failure_notifies_and_does_not_raise(integration):
    """One unreachable printer must not abort the whole service call."""
    _module, notifications, _ = integration
    notifications.clear()
    good = FakeCoordinator("reachable", IDLE)
    bad = FakeCoordinator("unreachable", IDLE, fail=True)
    hass, services, _ = _make_hass(
        integration, {"e1": bad, "e2": good}, {"d1": _device("e1"), "d2": _device("e2")}
    )
    _register(integration, hass)
    _call_service(integration, hass, services, {
        "device_id": ["d1", "d2"], "box_id": 1, "slot_id": 0, "type": "PLA",
    })
    assert good.client.sent, "the reachable printer must still be written"
    titles = [n.get("title") for n in notifications]
    assert any("Failed" in str(t) for t in titles), titles
    assert any("Updated" in str(t) for t in titles), titles


@requires_voluptuous
def test_each_printer_gets_its_own_notification_id(integration):
    """device_id accepts a list; a shared id left only the last result visible."""
    _module, notifications, _ = integration
    notifications.clear()
    good = FakeCoordinator("reachable", IDLE)
    bad = FakeCoordinator("unreachable", IDLE, fail=True)
    hass, services, _ = _make_hass(
        integration, {"e1": bad, "e2": good}, {"d1": _device("e1"), "d2": _device("e2")}
    )
    _register(integration, hass)
    _call_service(integration, hass, services, {
        "device_id": ["d1", "d2"], "box_id": 1, "slot_id": 0, "type": "PLA",
    })
    ids = [n.get("notification_id") for n in notifications]
    assert len(ids) == len(set(ids)), f"notification ids collide: {ids}"
    assert "cfs_material_error_unreachable" in ids, ids
    assert "cfs_material_update_reachable" in ids, ids


@requires_voluptuous
def test_a_successful_write_schedules_the_echo_readback(integration):
    """The echo log is how the colour-format question gets settled from a user's
    debug log, so it must actually be scheduled."""
    coord = FakeCoordinator(state=IDLE)
    hass, services, tasks = _make_hass(integration, {"e": coord}, {"d": _device("e")})
    _register(integration, hass)
    _call_service(integration, hass, services, {
        "device_id": ["d"], "box_id": 1, "slot_id": 0, "type": "PLA",
    })
    assert tasks, "no readback task was scheduled"


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


@requires_voluptuous
def test_the_schema_rejects_out_of_range_and_missing_fields(integration):
    import voluptuous as vol

    hass, services, _ = _make_hass(integration, {"e": FakeCoordinator(state=IDLE)})
    _register(integration, hass)
    _handler, schema = services["set_cfs_material"]

    base = {"device_id": ["d"], "box_id": 1, "slot_id": 0, "type": "PLA"}
    schema(base)  # the minimum accepted call

    for bad in (
        {**base, "box_id": 9},          # only 0-4 exist
        {**base, "slot_id": 7},         # only 0-3 exist
        {**base, "min_temp": 40},       # below the selector's floor
        {**base, "pressure": 5},        # pressure advance is 0-1
        {k: v for k, v in base.items() if k != "type"},       # type is required
        {k: v for k, v in base.items() if k != "device_id"},  # device is required
    ):
        with pytest.raises(vol.Invalid):
            schema(bad)
