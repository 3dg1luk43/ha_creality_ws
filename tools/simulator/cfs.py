"""The Creality Filament System: boxes, slots, the material write and its echo.

Box ids follow what printers report: the external spool holder is a `type: 1`
box (id 0 here), and each CFS unit is a `type: 0` box numbered from 1 with four
slots and its own temperature and humidity. Colours are written the way the
printer streams them: seven characters, a pad digit then RRGGBB.
"""
from __future__ import annotations

import copy
import random
from collections.abc import Mapping
from typing import Any

# Keys the integration sends in a modifyMaterial payload, minus the two that
# address the slot. `rfid` is included because the integration can pass an
# existing tag id straight back through.
MATERIAL_WRITABLE_KEYS = (
    "type",
    "name",
    "vendor",
    "color",
    "minTemp",
    "maxTemp",
    "pressure",
    "rfid",
)

_DEFAULT_COLOURS = ("#0000000", "#0ffffff", "#0ffa800", "#0ff97e1")


def external_box() -> dict[str, Any]:
    return {
        "id": 0,
        "state": 0,
        "type": 1,
        "materials": [
            {
                "id": 0,
                "vendor": "Generic",
                "type": "PLA",
                "color": "#01b04ae",
                "name": "Generic PLA",
                "minTemp": 190,
                "maxTemp": 240,
                "selected": 0,
                "percent": 100,
                "state": 1,
            }
        ],
    }


def cfs_box(box_id: int, *, selected_slot: int | None = None) -> dict[str, Any]:
    materials = []
    for slot, colour in enumerate(_DEFAULT_COLOURS):
        material = {
            "id": slot,
            "vendor": "Creality",
            "type": "PLA",
            "name": "Hyper PLA",
            "color": colour,
            "minTemp": 190,
            "maxTemp": 240,
            "pressure": 0.04,
            "percent": (95, 80, 100, 75)[slot],
            "state": 1,
            "selected": 1 if slot == selected_slot else 0,
        }
        if slot == 3:
            # No temps or pressure: real slots often omit them, and the edit
            # dialog has to cope with a partial prefill.
            for key in ("minTemp", "maxTemp", "pressure"):
                material.pop(key)
        materials.append(material)
    return {"id": box_id, "state": 1, "type": 0, "temp": 28.0, "humidity": 40.0, "materials": materials}


def edge_boxes() -> list[dict[str, Any]]:
    """The awkward shapes that broke parsing in the past, kept covered: a
    six-character colour, a slot with no vendor, a multi-colour spool, rfid
    values and an empty external slot."""
    box = cfs_box(1, selected_slot=0)
    box["materials"] = [
        {"id": 0, "vendor": "Creality", "type": "PLA", "name": "Hyper PLA", "color": "#0000000",
         "rfid": "001001", "percent": 95, "state": 1, "selected": 1},
        {"id": 1, "vendor": "Creality", "type": "PLA", "name": "Hyper PLA", "color": "#0ffffff",
         "rfid": "001001", "percent": 80, "state": 1, "selected": 0},
        # already-correct six-character colour, and no vendor reported
        {"id": 2, "type": "PETG", "name": "PETG", "color": "#1b04ae", "percent": 50, "state": 1,
         "selected": 0},
        # multi-colour spool
        {"id": 3, "vendor": "Generic", "type": "PLA", "name": "Generic PLA Silk",
         "color": "#0ffa800,#0ff97e1", "percent": 30, "state": 1, "selected": 0},
    ]
    external = external_box()
    external["materials"] = [
        {"id": 0, "type": "", "name": "", "color": "", "percent": 0, "state": 0, "selected": 0}
    ]
    return [external, box]


class Cfs:
    """The boxes and what the printer says about them."""

    def __init__(self, *, attached: bool, variant: str = "default", deterministic: bool = False):
        self.deterministic = deterministic
        # `echo_colour`: "as_sent" stores the write verbatim; "padded" stores it
        # the way the stream reports colours (#113 asks which one is real).
        self.echo_colour = "as_sent"
        self.boxes: list[dict[str, Any]] = []
        self.reset(attached=attached, variant=variant)

    def reset(self, *, attached: bool, variant: str = "default") -> None:
        if variant == "edge":
            self.boxes = edge_boxes()
        elif attached:
            self.boxes = [external_box(), cfs_box(1, selected_slot=0)]
        else:
            self.boxes = [external_box()]

    # ------------------------------------------------------------- queries
    @property
    def attached(self) -> bool:
        """A real CFS unit is attached (a type 0 box), not just the external
        spool holder. This is what `cfsConnect` reports."""
        return any(box.get("type") == 0 for box in self.boxes)

    def tick(self) -> None:
        """The boxes' climate drifts; reading it (`info`) changes nothing."""
        if self.deterministic:
            return
        for box in self.boxes:
            if box.get("type") == 0:
                box["temp"] = round(28.0 + random.uniform(-0.8, 0.8), 1)
                box["humidity"] = round(40.0 + random.uniform(-1.5, 1.5), 1)

    def info(self) -> dict[str, Any]:
        """The `boxsInfo` reply."""
        same = [
            ["001001", str(m.get("color") or "")[1:], [{"boxId": box["id"], "materialId": m["id"]}], m.get("type")]
            for box in self.boxes if box.get("type") == 0
            for m in box.get("materials", [])[:2]
            if isinstance(m, Mapping) and "id" in m
        ]
        return {"boxsInfo": {"same_material": same, "materialBoxs": copy.deepcopy(self.boxes)}}

    def selected(self) -> tuple[int, int] | None:
        for box in self.boxes:
            for m in box.get("materials", []):
                if isinstance(m, Mapping) and m.get("selected"):
                    return box.get("id"), m.get("id")
        return None

    # ------------------------------------------------------------- the write
    def modify_material(self, payload: Any) -> dict[str, Any]:
        """Apply a ``modifyMaterial`` write to the stored slot.

        Merges rather than replaces, which is what makes the "absent key keeps
        the printer's value" contract observable -- in particular that a write
        which omits `rfid` leaves the tag association intact.

        Raises ValueError for a box or slot that does not exist, so a wrong
        `boxId` fails loudly in testing instead of silently doing nothing.
        """
        # A non-dict reaches here because the caller does `params.get(...) or
        # {}`, which passes a list, a string or a number straight through.
        if not isinstance(payload, Mapping):
            raise ValueError(f"modifyMaterial needs an object, got {payload!r}")
        try:
            box_id = int(payload.get("boxId"))
            slot_id = int(payload.get("id"))
        except (TypeError, ValueError):
            raise ValueError(
                f"modifyMaterial needs integer boxId and id, got {payload!r}"
            ) from None

        box = next((b for b in self.boxes if b.get("id") == box_id), None)
        if box is None:
            known = [b.get("id") for b in self.boxes]
            raise ValueError(f"no such box {box_id} (have {known})")

        materials = box.get("materials", [])
        # `POST /test/cfs` stores whatever list it is handed, so a non-dict
        # entry would raise AttributeError rather than read as a missing slot.
        if not all(isinstance(m, Mapping) for m in materials):
            raise ValueError(f"box {box_id} holds a non-object material entry")
        slot = next((m for m in materials if m.get("id") == slot_id), None)
        if slot is None:
            known = [m.get("id") for m in materials]
            raise ValueError(f"no such slot {slot_id} in box {box_id} (have {known})")

        for key in MATERIAL_WRITABLE_KEYS:
            if key in payload:
                slot[key] = payload[key]
        if "color" in payload and self.echo_colour == "padded":
            colour = str(payload["color"])
            if len(colour) == 7 and colour.startswith("#"):
                slot["color"] = "#0" + colour[1:]
        return slot

    # ------------------------------------------------------------- scenarios
    def set_materials(self, box_id: int, materials: list) -> bool:
        """Replace a box's slot list (POST /test/cfs)."""
        for box in self.boxes:
            if box.get("id") == box_id:
                box["materials"] = materials
                return True
        return False

    def attach(self) -> None:
        if not self.attached:
            self.boxes.append(cfs_box(1, selected_slot=None))

    def detach(self) -> None:
        self.boxes = [b for b in self.boxes if b.get("type") != 0]

    def add_box(self) -> int:
        """Chain another CFS unit on (R21: a box that appears later)."""
        ids = [b.get("id") for b in self.boxes if b.get("type") == 0 and isinstance(b.get("id"), int)]
        new_id = (max(ids) + 1) if ids else 1
        self.boxes.append(cfs_box(new_id))
        return new_id

    def remove_box(self, box_id: int) -> bool:
        before = len(self.boxes)
        self.boxes = [b for b in self.boxes if not (b.get("type") == 0 and b.get("id") == box_id)]
        return len(self.boxes) != before

    def select(self, box_id: int, slot_id: int) -> bool:
        found = False
        for box in self.boxes:
            for m in box.get("materials", []):
                if not isinstance(m, Mapping):
                    continue
                hit = box.get("id") == box_id and m.get("id") == slot_id
                m["selected"] = 1 if hit else 0
                found = found or hit
        return found

    def set_percent(self, box_id: int, slot_id: int, percent: int) -> bool:
        for box in self.boxes:
            if box.get("id") != box_id:
                continue
            for m in box.get("materials", []):
                if isinstance(m, Mapping) and m.get("id") == slot_id:
                    m["percent"] = max(0, min(100, int(percent)))
                    return True
        return False

    def consume(self, amount: float) -> None:
        """Use filament from the selected slot while printing."""
        sel = self.selected()
        if sel is None:
            return
        for box in self.boxes:
            if box.get("id") != sel[0]:
                continue
            for m in box.get("materials", []):
                if isinstance(m, Mapping) and m.get("id") == sel[1]:
                    current = float(m.get("percent") or 0)
                    m["percent"] = max(0, int(round(current - amount)))
