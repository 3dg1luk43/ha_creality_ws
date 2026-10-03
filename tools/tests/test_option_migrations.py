"""The option migrations that run at every setup.

They run before the platforms, every time, so anything they rewrite is
rewritten for good, whatever the user set.
"""

from types import SimpleNamespace

from test_entry_removal import _load_package_init

integration = _load_package_init()


def _migrate(options, data=None):
    entry = SimpleNamespace(options=dict(options), data=dict(data or {"host": "1.2.3.4"}))
    updates = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda e, **kw: (updates.append(kw), setattr(e, "options", kw.get("options", e.options)))
        )
    )
    integration._migrate_go2rtc_settings(hass, entry)
    return entry.options, updates


def test_a_stand_alone_go2rtc_on_the_default_pair_is_kept():
    """R18. A Core install has no bundled go2rtc, so its user runs their own,
    often on localhost:11984. The migration stripped that pair at every setup,
    before the camera read it, and the camera then failed with "go2rtc
    component not loaded". The camera itself tells HA's own go2rtc apart."""
    options, _ = _migrate({
        "power_switch_enabled": False,
        "go2rtc_url": "localhost",
        "go2rtc_port": 11984,
    })
    assert options["go2rtc_url"] == "localhost"
    assert options["go2rtc_port"] == 11984


def test_settings_stored_in_entry_data_are_still_moved_to_options():
    options, updates = _migrate(
        {"power_switch_enabled": False},
        data={"host": "1.2.3.4", "go2rtc_url": "10.0.0.9", "go2rtc_port": "1984"},
    )
    assert options["go2rtc_url"] == "10.0.0.9"
    assert options["go2rtc_port"] == 1984
    assert updates


def test_nothing_to_migrate_writes_nothing():
    _, updates = _migrate({"power_switch_enabled": False, "go2rtc_url": "10.0.0.9", "go2rtc_port": 1984})
    assert updates == []
