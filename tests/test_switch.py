"""Test Bermuda BLE Trilateration occupancy switch."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import mock_restore_cache
from homeassistant.core import State

from custom_components.bermuda.const import DOMAIN, SIGNAL_DEVICE_NEW


def _occupancy_entity_id(hass: HomeAssistant, unique_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("switch", DOMAIN, unique_id)


async def test_occupancy_switch_created_and_defaults_off(hass: HomeAssistant, bypass_get_data, setup_bermuda_entry):
    """A tracked device should get an occupancy switch, defaulting off."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    device.create_sensor = True

    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    entity_id = _occupancy_entity_id(hass, f"{device.unique_id}_occupancy")
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "off"
    assert device.create_occupancy is False


async def test_occupancy_switch_turn_on_off_sets_device_flag(hass: HomeAssistant, bypass_get_data, setup_bermuda_entry):
    """Toggling the switch should mutate the underlying BermudaDevice flag."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    device.create_sensor = True

    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    entity_id = _occupancy_entity_id(hass, f"{device.unique_id}_occupancy")

    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    assert device.create_occupancy is True
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    assert device.create_occupancy is False
    assert hass.states.get(entity_id).state == "off"


async def test_occupancy_switch_restores_state_on_startup(hass: HomeAssistant, bypass_get_data, setup_bermuda_entry):
    """A previously-on switch should restore create_occupancy=True without user interaction."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    device = coordinator._get_or_create_device("aa:bb:cc:dd:ee:ff")
    device.create_sensor = True

    registry_entry = er.async_get(hass).async_get_or_create("switch", DOMAIN, f"{device.unique_id}_occupancy")
    mock_restore_cache(hass, [State(registry_entry.entity_id, "on")])

    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    assert device.create_occupancy is True
