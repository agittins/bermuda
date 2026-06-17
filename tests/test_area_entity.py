"""Tests for the area-entity presence override (manager + coordinator logic)."""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.area_entity import BermudaAreaEntityManager
from custom_components.bermuda.const import CONF_AREA_ENTITIES, CONF_AREA_ENTITY_DISTANCE
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _kitchen_entity(hass):
    """Create a binary_sensor in a 'Kitchen' area; return (entity_id, area_id)."""
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = er.async_get(hass).async_get_or_create("binary_sensor", "test", "motion_kitchen")
    er.async_get(hass).async_update_entity(entry.entity_id, area_id=kitchen.id)
    return entry.entity_id, kitchen.id


# ---------------------------------------------------------------------------
# BermudaAreaEntityManager
# ---------------------------------------------------------------------------


async def test_resolve_entity_area(hass):
    entity_id, area_id = _kitchen_entity(hass)
    mgr = BermudaAreaEntityManager(hass)
    assert mgr.resolve_entity_area(entity_id) == (area_id, "Kitchen")
    # Unknown entity resolves to (None, None).
    assert mgr.resolve_entity_area("binary_sensor.nope") == (None, None)


async def test_resolve_area_falls_back_to_device(hass):
    """An entity with no own area inherits its device's area."""
    kitchen = ar.async_get(hass).async_create("Kitchen")
    config_entry = MockConfigEntry(domain="test")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("test", "devA")}
    )
    dr.async_get(hass).async_update_device(device.id, area_id=kitchen.id)
    entry = er.async_get(hass).async_get_or_create("binary_sensor", "test", "child", device_id=device.id)
    mgr = BermudaAreaEntityManager(hass)
    assert mgr.resolve_entity_area(entry.entity_id) == (kitchen.id, "Kitchen")


async def test_resolve_area_none_without_area_or_device(hass):
    """An orphan entity (no area, no device) resolves to (None, None)."""
    entry = er.async_get(hass).async_get_or_create("binary_sensor", "test", "orphan")
    mgr = BermudaAreaEntityManager(hass)
    assert mgr.resolve_entity_area(entry.entity_id) == (None, None)


async def test_triggered_skips_missing_state_and_unresolvable_area(hass):
    """Entities without a state, or triggered without a resolvable area, are skipped."""
    mgr = BermudaAreaEntityManager(hass)
    # No state at all -> skipped.
    assert mgr.get_triggered_areas_with_distances(["binary_sensor.ghost"], {}, 0.1) == {}
    # Triggered but no area to resolve -> skipped.
    entry = er.async_get(hass).async_get_or_create("binary_sensor", "test", "noarea")
    hass.states.async_set(entry.entity_id, "on")
    assert mgr.get_triggered_areas_with_distances([entry.entity_id], {}, 0.1) == {}


async def test_triggered_areas_state_and_distances(hass):
    entity_id, area_id = _kitchen_entity(hass)
    mgr = BermudaAreaEntityManager(hass)

    # "on" -> triggered at the default distance.
    hass.states.async_set(entity_id, "on")
    assert mgr.get_triggered_areas_with_distances([entity_id], {}, 0.1) == {area_id: ("Kitchen", 0.1)}

    # "off" -> not triggered.
    hass.states.async_set(entity_id, "off")
    assert mgr.get_triggered_areas_with_distances([entity_id], {}, 0.1) == {}

    # per-entity distance override wins over the default.
    hass.states.async_set(entity_id, "on")
    assert mgr.get_triggered_areas_with_distances([entity_id], {entity_id: 2.5}, 0.1) == {area_id: ("Kitchen", 2.5)}


# ---------------------------------------------------------------------------
# Coordinator override logic
# ---------------------------------------------------------------------------


def _bare_coordinator(triggered):
    coord = object.__new__(BermudaDataUpdateCoordinator)
    coord.options = {CONF_AREA_ENTITIES: ["binary_sensor.kitchen"], CONF_AREA_ENTITY_DISTANCE: 0.1}
    coord.area_entity_manager = SimpleNamespace(get_triggered_areas_with_distances=lambda *_a: triggered)
    return coord


def _device(*, area_id, area_name, area_distance):
    dev = SimpleNamespace(
        name="dev", create_sensor=True, area_id=area_id, area_name=area_name, area_distance=area_distance, applied=[]
    )

    def _override(aid, dist):
        dev.applied.append((aid, dist))
        dev.area_id, dev.area_name, dev.area_distance = aid, "Kitchen", dist

    dev.apply_area_override = _override
    return dev


def test_override_when_virtually_closer_than_ble():
    coord = _bare_coordinator({"kitchen_id": ("Kitchen", 0.1)})
    dev = _device(area_id="garage_id", area_name="Garage", area_distance=3.0)  # 0.1 < 3.0
    coord.devices = {"d": dev}
    coord._apply_area_entity_overrides()
    assert dev.applied == [("kitchen_id", 0.1)]


def test_override_when_device_has_no_area():
    coord = _bare_coordinator({"kitchen_id": ("Kitchen", 0.1)})
    dev = _device(area_id=None, area_name=None, area_distance=None)  # Unknown / not placed
    coord.devices = {"d": dev}
    coord._apply_area_entity_overrides()
    assert dev.applied == [("kitchen_id", 0.1)]


def test_no_override_when_ble_is_closer():
    coord = _bare_coordinator({"kitchen_id": ("Kitchen", 0.5)})
    dev = _device(area_id="garage_id", area_name="Garage", area_distance=0.2)  # BLE 0.2 < virtual 0.5
    coord.devices = {"d": dev}
    coord._apply_area_entity_overrides()
    assert dev.applied == []


def test_no_override_when_already_in_area_and_ble_closer():
    coord = _bare_coordinator({"kitchen_id": ("Kitchen", 0.5)})
    dev = _device(area_id="kitchen_id", area_name="Kitchen", area_distance=0.3)  # already here, closer via BLE
    coord.devices = {"d": dev}
    coord._apply_area_entity_overrides()
    assert dev.applied == []


def test_no_op_when_unconfigured():
    coord = _bare_coordinator({"kitchen_id": ("Kitchen", 0.1)})
    coord.options = {}  # no CONF_AREA_ENTITIES
    dev = _device(area_id="garage_id", area_name="Garage", area_distance=3.0)
    coord.devices = {"d": dev}
    coord._apply_area_entity_overrides()
    assert dev.applied == []
