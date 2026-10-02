"""A printer that moves to a new IP keeps its device and entities (#39).

Entity unique ids and the device identifier embed the host. Before
`_async_follow_host`, a new address recreated every entity with a `_2` id on a
second device and stranded the originals. These tests drive the function
against small fake registries; the same change is exercised end to end against
a real Home Assistant by the test box (tools/testbox/smoke.sh).
"""

from types import SimpleNamespace

from test_entry_removal import _load_package_init

from custom_components.ha_creality_ws.const import DOMAIN
from custom_components.ha_creality_ws.utils import (
    extract_info_from_zeroconf,
    normalize_printer_hostname,
)

OLD, NEW = "192.168.1.20", "192.168.1.57"

# The real package body; conftest's package of the same name is a stub.
integration = _load_package_init()


class FakeDeviceRegistry:
    def __init__(self, devices):
        self.devices = {d.id: d for d in devices}

    def async_update_device(self, device_id, *, new_identifiers):
        self.devices[device_id] = SimpleNamespace(
            id=device_id, identifiers=set(new_identifiers), config_entry_id="e1"
        )


class FakeEntityRegistry:
    def __init__(self, entries):
        self.entries = {e.entity_id: e for e in entries}

    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self.entries.values():
            if e.domain == domain and e.unique_id == unique_id:
                return e.entity_id
        return None

    def async_update_entity(self, entity_id, *, new_unique_id):
        self.entries[entity_id].unique_id = new_unique_id


def _entity(entity_id, unique_id):
    return SimpleNamespace(
        entity_id=entity_id, unique_id=unique_id, domain=entity_id.split(".")[0]
    )


def _setup(monkeypatch, devices, entities, *, unique_id=OLD, others=()):
    dev_reg = FakeDeviceRegistry(devices)
    ent_reg = FakeEntityRegistry(entities)
    monkeypatch.setattr(
        integration,
        "dr",
        SimpleNamespace(
            async_get=lambda hass: dev_reg,
            async_entries_for_config_entry=lambda reg, entry_id: list(reg.devices.values()),
        ),
    )
    monkeypatch.setattr(
        integration,
        "er",
        SimpleNamespace(
            async_get=lambda hass: ent_reg,
            async_entries_for_config_entry=lambda reg, entry_id: list(reg.entries.values()),
        ),
    )
    entry = SimpleNamespace(entry_id="e1", unique_id=unique_id)
    updates = []

    def _update_entry(e, **kw):
        updates.append(kw)
        for key, value in kw.items():
            setattr(e, key, value)

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [entry, *others],
            async_update_entry=_update_entry,
        )
    )
    return hass, entry, dev_reg, ent_reg, updates


def _device(host, device_id="d1"):
    return SimpleNamespace(id=device_id, identifiers={(DOMAIN, host)}, config_entry_id="e1")


def test_a_new_address_moves_the_device_and_every_entity(monkeypatch):
    hass, entry, dev_reg, ent_reg, updates = _setup(
        monkeypatch,
        [_device(OLD)],
        [
            _entity("sensor.k1c_nozzle_temperature", f"{OLD}-nozzle_temperature"),
            _entity("light.k1c_light", f"{OLD}-light"),
        ],
    )
    integration._async_follow_host(hass, entry, NEW)

    assert dev_reg.devices["d1"].identifiers == {(DOMAIN, NEW)}
    assert ent_reg.entries["sensor.k1c_nozzle_temperature"].unique_id == f"{NEW}-nozzle_temperature"
    assert ent_reg.entries["light.k1c_light"].unique_id == f"{NEW}-light"
    # The entry's own unique id follows, so the old address is free again.
    assert entry.unique_id == NEW


def test_an_unchanged_address_touches_nothing(monkeypatch):
    hass, entry, dev_reg, ent_reg, updates = _setup(
        monkeypatch,
        [_device(OLD)],
        [_entity("sensor.k1c_nozzle_temperature", f"{OLD}-nozzle_temperature")],
    )
    integration._async_follow_host(hass, entry, OLD)
    assert updates == []
    assert ent_reg.entries["sensor.k1c_nozzle_temperature"].unique_id == f"{OLD}-nozzle_temperature"


def test_a_registry_already_split_by_the_old_bug_is_left_alone(monkeypatch):
    """Merging either way would rename entities someone may have rebuilt
    their automations on, so neither side moves."""
    hass, entry, dev_reg, ent_reg, _ = _setup(
        monkeypatch,
        [_device(OLD, "d1"), _device(NEW, "d2")],
        [
            _entity("sensor.k1c_nozzle_temperature", f"{OLD}-nozzle_temperature"),
            _entity("sensor.k1c_nozzle_temperature_2", f"{NEW}-nozzle_temperature"),
        ],
    )
    integration._async_follow_host(hass, entry, NEW)
    assert dev_reg.devices["d1"].identifiers == {(DOMAIN, OLD)}
    assert ent_reg.entries["sensor.k1c_nozzle_temperature"].unique_id == f"{OLD}-nozzle_temperature"
    assert ent_reg.entries["sensor.k1c_nozzle_temperature_2"].unique_id == f"{NEW}-nozzle_temperature"


def test_a_hostname_with_dashes_is_matched_by_prefix_not_split(monkeypatch):
    """`k1c-printer.lan-nozzle_temperature` must not be cut at the first dash."""
    old = "k1c-printer.lan"
    hass, entry, _, ent_reg, _ = _setup(
        monkeypatch,
        [_device(old)],
        [_entity("sensor.k1c_nozzle_temperature", f"{old}-nozzle_temperature")],
        unique_id=old,
    )
    integration._async_follow_host(hass, entry, NEW)
    assert ent_reg.entries["sensor.k1c_nozzle_temperature"].unique_id == f"{NEW}-nozzle_temperature"


def test_the_entry_id_is_not_taken_from_another_entry(monkeypatch):
    other = SimpleNamespace(entry_id="e2", unique_id=NEW)
    hass, entry, _, _, _ = _setup(monkeypatch, [_device(OLD)], [], others=(other,))
    integration._async_follow_host(hass, entry, NEW)
    assert entry.unique_id == OLD


def test_mdns_and_telemetry_hostnames_compare_equal():
    assert normalize_printer_hostname("K1C-C627.local.") == "k1c-c627"
    assert normalize_printer_hostname("K1C-C627") == "k1c-c627"
    assert normalize_printer_hostname("") is None
    assert normalize_printer_hostname(None) is None


def test_a_mac_in_decoded_zeroconf_properties_is_found():
    """Home Assistant passes `decoded_properties`: str keys. The lookup used
    bytes keys, so no MAC was ever stored and the MAC path never ran."""
    info = SimpleNamespace(
        ip_addresses=["192.168.1.57"],
        hostname="K1C-C627.local.",
        properties={"mac": "aa:bb:cc:dd:ee:ff"},
    )
    host, mac = extract_info_from_zeroconf(info)
    assert host == "192.168.1.57"
    assert mac == "AA:BB:CC:DD:EE:FF"


def test_raw_bytes_properties_still_work():
    info = SimpleNamespace(ip_addresses=["10.0.0.2"], properties={b"mac": b"aa:bb:cc:dd:ee:ff"})
    assert extract_info_from_zeroconf(info)[1] == "AA:BB:CC:DD:EE:FF"
