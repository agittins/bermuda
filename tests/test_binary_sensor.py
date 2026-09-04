"""Test Bermuda BLE Trilateration area occupancy binary_sensor."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.bermuda.const import DOMAIN


def _occupancy_entity_id(hass: HomeAssistant, area_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"BERMUDA_GLOBAL_OCCUPANCY_{area_id}")


async def test_area_occupancy_created_off_by_default(hass: HomeAssistant, bypass_get_data, setup_bermuda_entry):
    """An area with a scanner should get an occupancy binary_sensor, defaulting off."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    area = ar.async_get(hass).async_get_or_create("Kitchen")

    scanner = coordinator._get_or_create_device("11:22:33:44:55:66")
    scanner.area_id = area.id
    coordinator.scanner_list_add(scanner)
    coordinator._refresh_areas_in_use()
    await hass.async_block_till_done()

    entity_id = _occupancy_entity_id(hass, area.id)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "off"


async def test_area_occupancy_tracks_flagged_devices_without_removal(
    hass: HomeAssistant, bypass_get_data, setup_bermuda_entry
):
    """Occupancy should turn on/off with a flagged device, without the entity being removed."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    area = ar.async_get(hass).async_get_or_create("Kitchen")

    scanner = coordinator._get_or_create_device("11:22:33:44:55:66")
    scanner.area_id = area.id
    coordinator.scanner_list_add(scanner)
    coordinator._refresh_areas_in_use()
    await hass.async_block_till_done()

    entity_id = _occupancy_entity_id(hass, area.id)

    tracked = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    tracked.create_sensor = True
    tracked.create_occupancy = True
    tracked.area_id = area.id
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "on"

    tracked.area_id = None
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    # Entity stays registered once created, it just reports off.
    assert _occupancy_entity_id(hass, area.id) == entity_id
    assert hass.states.get(entity_id).state == "off"
