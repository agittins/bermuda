"""Behavioural tests for BermudaNumber (the per-device ref_power number entity).

These build a real ``BermudaNumber`` against a live coordinator (via
``setup_bermuda_entry``) and a real ``BermudaDevice``, so ``set_ref_power``'s
actual logic runs and is observed. Only genuine HA-base-class boundaries
(``async_get_last_number_data``, ``async_write_ha_state``) are stubbed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.number import NumberExtraStoredData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.number import BermudaNumber


def _make_number(coordinator, entry, address: str) -> BermudaNumber:
    """Create a real BermudaNumber for a device already in coordinator.devices."""
    coordinator._get_or_create_device(address)
    return BermudaNumber(coordinator, entry, address)


async def test_async_added_to_hass_restores_ref_power(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """When restore data carries a native_value, the device's ref_power is set from it."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    addr = "aa:bb:cc:dd:ee:01"
    ent = _make_number(coordinator, setup_bermuda_entry, addr)

    restored = NumberExtraStoredData(
        native_max_value=0,
        native_min_value=-127,
        native_step=1,
        native_unit_of_measurement=None,
        native_value=-55.0,
    )
    ent.async_get_last_number_data = AsyncMock(return_value=restored)

    await ent.async_added_to_hass()

    assert ent.restored_data is restored
    assert coordinator.devices[addr].ref_power == -55.0


async def test_async_added_to_hass_no_restored_value_leaves_ref_power(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """When there is no prior restore data, ref_power is left untouched."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    addr = "aa:bb:cc:dd:ee:02"
    ent = _make_number(coordinator, setup_bermuda_entry, addr)
    device = coordinator.devices[addr]
    original_ref_power = device.ref_power

    ent.async_get_last_number_data = AsyncMock(return_value=None)

    await ent.async_added_to_hass()

    assert ent.restored_data is None
    assert device.ref_power == original_ref_power


async def test_async_set_native_value_updates_device_and_writes_state(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Setting a new value updates the real device's ref_power and writes ha state."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    addr = "aa:bb:cc:dd:ee:03"
    ent = _make_number(coordinator, setup_bermuda_entry, addr)
    ent.async_write_ha_state = MagicMock()

    await ent.async_set_native_value(-42.0)

    assert coordinator.devices[addr].ref_power == -42.0
    ent.async_write_ha_state.assert_called_once()
