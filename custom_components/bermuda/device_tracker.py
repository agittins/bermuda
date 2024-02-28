"""Create device_tracker entities for Bermuda devices"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import BaseTrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BermudaDataUpdateCoordinator
from .const import DOMAIN
from .entity import BermudaEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Device Tracker entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # We go through each "device" in the co-ordinator, and create the entities
    entities = []
    for device in coordinator.devices.values():
        if device.create_sensor:
            entities.append(BermudaDeviceTracker(coordinator, entry, device.address))
    async_add_devices(entities, True)


class BermudaDeviceTracker(BermudaEntity, BaseTrackerEntity):
    """A trackable Bermuda Device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = None

    @property
    def unique_id(self):
        """ "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user"""
        return self._device.unique_id

    @property
    def extra_state_attributes(self) -> Mapping[str, str]:
        """Return extra state attributes for this device."""
        return {"scanner": self._device.area_scanner, "area": self._device.area_name}

    @property
    def state(self) -> str:
        """Return the state of the device."""
        return self._device.zone

    @property
    def source_type(self) -> SourceType:
        """Return the source type, eg gps or router, of the device."""
        return SourceType.BLUETOOTH_LE

    @property
    def icon(self) -> str:
        """Return device icon."""
        return (
            "mdi:bluetooth-connect"
            if self._device.zone == STATE_HOME
            else "mdi:bluetooth-off"
        )
