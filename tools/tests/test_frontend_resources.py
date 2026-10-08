"""Lovelace resources left by older versions are removed, not rewritten (R28).

An older migration appended whatever followed the card's `/local/` path to the
integration URL, so "/local/ha_creality_ws/k_printer_card.js?v=1" became
"/ha_creality_ws/k_printer_card.js/?v=1?v=<new>", a 404 on every dashboard
load, and an entry with no query was skipped while its file was deleted.
Reproduced on the test box; `_init_resource` maintains the correct entry.
"""

import asyncio
import sys
import types
from types import SimpleNamespace

from conftest import install_stub_module, restore_stubs

resources_mod = types.ModuleType("homeassistant.components.lovelace.resources")


class ResourceStorageCollection:
    def __init__(self, items):
        self._items = {i["id"]: dict(i) for i in items}

    async def async_get_info(self):
        return {}

    def async_items(self):
        return list(self._items.values())

    async def async_delete_item(self, item_id):
        del self._items[item_id]

    async def async_update_item(self, item_id, data):
        self._items[item_id].update(data)


resources_mod.ResourceStorageCollection = ResourceStorageCollection
install_stub_module(__name__, "homeassistant.components.lovelace.resources", resources_mod)
if "homeassistant.components.lovelace" not in sys.modules:
    install_stub_module(__name__, "homeassistant.components.lovelace", types.ModuleType("homeassistant.components.lovelace"))

from custom_components.ha_creality_ws import frontend  # noqa: E402


def teardown_module(_module):
    restore_stubs(__name__)


CARD = "/ha_creality_ws/k_printer_card.js"
LOCAL = "/local/ha_creality_ws/k_printer_card.js"


def _run(items):
    collection = ResourceStorageCollection(items)
    hass = SimpleNamespace(data={"lovelace": SimpleNamespace(resources=collection)})
    removed = asyncio.run(frontend._migrate_local_resources(hass, LOCAL, CARD, "new"))
    return removed, sorted(i["url"] for i in collection.async_items())


def test_legacy_and_malformed_entries_are_removed():
    removed, urls = _run([
        {"id": "1", "url": f"{CARD}?v=new"},
        {"id": "2", "url": f"{LOCAL}?v=1"},
        {"id": "3", "url": LOCAL},
        {"id": "4", "url": f"{CARD}/?v=1?v=old"},
    ])
    assert removed == 3
    assert urls == [f"{CARD}?v=new"]


def test_other_resources_are_left_alone():
    removed, urls = _run([
        {"id": "1", "url": "/local/community/some-card.js"},
        {"id": "2", "url": "/local/ha_creality_ws/k_printer_card_backup.js"},
        {"id": "3", "url": f"{CARD}?v=new"},
    ])
    assert removed == 0
    assert len(urls) == 3
