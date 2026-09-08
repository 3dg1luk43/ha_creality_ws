"""`KEntity`'s reads of the onboarding cache stored on the config entry.

These had no coverage at all, which is how they came to be refactored blind.
They matter because they are what keeps a device named and its temperature
limits known while the printer is unreachable -- the whole point of caching
them on the entry during onboarding.
"""

from types import SimpleNamespace

import pytest

from conftest import fake_config_entry
from custom_components.ha_creality_ws.entity import KEntity

CACHED = {
    "_device_info_cached": True,
    "_cached_model": "K1C",
    "_cached_hostname": "K1C-5316",
    "_cached_model_version": "CR-K1C_V1.2.3.4",
    "_cached_max_bed_temp": 100,
    "_cached_max_nozzle_temp": 300,
    "_cached_max_chamber_temp": 60,
}

LIVE = {"maxBedTemp": 11, "maxNozzleTemp": 22, "maxBoxTemp": 33}


def _entity(*, entry=None, data=None, available=True, power_off=False):
    coordinator = SimpleNamespace(
        client=SimpleNamespace(_host="1.2.3.4"),
        config_entry=entry,
        data=dict(data or {}),
        available=available,
        power_is_off=lambda: power_off,
    )
    return KEntity(coordinator, unique_id="probe")


# --------------------------------------------------------------------------- #
# Device info
# --------------------------------------------------------------------------- #


def test_cached_device_info_is_read_from_the_entry():
    ent = _entity(entry=fake_config_entry(data=CACHED))
    assert ent._get_cached_device_info() == {
        "model": "K1C",
        "hostname": "K1C-5316",
        "modelVersion": "CR-K1C_V1.2.3.4",
    }


def test_no_cache_means_no_cached_device_info():
    """An entry that never completed onboarding must not report half a device."""
    assert _entity(entry=fake_config_entry(data={}))._get_cached_device_info() is None


def test_no_entry_at_all_is_survivable():
    assert _entity(entry=None)._get_cached_device_info() is None


def test_device_info_prefers_the_cache_over_live_telemetry():
    """This is what names the device while the printer is off."""
    ent = _entity(entry=fake_config_entry(data=CACHED), data={"model": "K2", "hostname": "other"})
    info = ent.device_info
    assert info["model"] == "K1C"
    assert info["name"] == "K1C-5316"


def test_device_info_falls_back_to_telemetry_without_a_cache():
    ent = _entity(entry=None, data={"model": "K2 Plus", "hostname": "K2P-1"})
    info = ent.device_info
    assert info["model"] == "K2 Plus"
    assert info["name"] == "K2P-1"


def test_device_info_names_the_device_when_the_printer_gave_no_hostname():
    info = _entity(entry=None, data={"model": "K1 SE"}).device_info
    assert info["name"] == "K1 SE (Creality)"


# --------------------------------------------------------------------------- #
# Max temperatures
# --------------------------------------------------------------------------- #


def test_cached_max_temps_win_over_live_values():
    ent = _entity(entry=fake_config_entry(data=CACHED), data=LIVE)
    assert ent._get_cached_max_temps() == {
        "max_bed_temp": 100,
        "max_nozzle_temp": 300,
        "max_box_temp": 60,
    }


def test_a_pre_rename_entry_still_yields_a_chamber_limit():
    """Entries cached before the box -> chamber rename only have the old key, and
    dropping the fallback would silently remove chamber control for them."""
    legacy = {k: v for k, v in CACHED.items() if k != "_cached_max_chamber_temp"}
    legacy["_cached_max_box_temp"] = 55
    ent = _entity(entry=fake_config_entry(data=legacy))
    assert ent._get_cached_max_temps()["max_box_temp"] == 55


def test_max_temps_fall_back_to_live_telemetry_without_a_cache():
    ent = _entity(entry=fake_config_entry(data={}), data=LIVE)
    assert ent._get_cached_max_temps() == {
        "max_bed_temp": 11,
        "max_nozzle_temp": 22,
        "max_box_temp": 33,
    }


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "available,power_off,expected",
    [(True, False, True), (False, False, False), (True, True, False)],
)
def test_availability_folds_in_the_power_switch(available, power_off, expected):
    ent = _entity(entry=None, available=available, power_off=power_off)
    assert ent.available is expected
    assert ent._should_zero() is (not expected)


def test_a_unique_id_is_mandatory():
    """Two entities sharing a unique id silently collide in the registry."""
    with pytest.raises(ValueError):
        KEntity(SimpleNamespace(client=SimpleNamespace(_host="1.2.3.4")), unique_id="")


def test_the_unique_id_is_namespaced_by_host():
    ent = _entity(entry=None)
    assert ent._attr_unique_id == "1.2.3.4-probe"
