"""BermudaEntity class"""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import area_registry

from .const import ATTRIBUTION
from .const import DOMAIN
from .const import NAME
from .const import VERSION

if TYPE_CHECKING:
    from . import BermudaDataUpdateCoordinator
    #from . import BermudaDevice

class BermudaEntity(CoordinatorEntity):
    """Co-ordinator for Bermuda data.

    Gathers the device infor for receivers and transmitters, calculates
    distances etc.
    """
    def __init__(
            self,
            coordinator: BermudaDataUpdateCoordinator,
            config_entry,
            address: str
        ):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._device = coordinator.devices[address]
        self.ar = area_registry.async_get(coordinator.hass)
    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._device.unique_id

    @property
    def device_info(self):
        """Implementing this creates an entry in the device registry."""
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self._device.prefname,
            "model": VERSION,
            "manufacturer": NAME,
        }

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return {
            "attribution": ATTRIBUTION,
            "id": str(self.coordinator.data.get("id")),
            "integration": DOMAIN,
        }
