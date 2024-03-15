"""BermudaEntity class"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import area_registry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION
from .const import BEACON_IBEACON_DEVICE
from .const import DOMAIN

if TYPE_CHECKING:
    from . import BermudaDataUpdateCoordinator

    # from . import BermudaDevice


class BermudaEntity(CoordinatorEntity):
    """Co-ordinator for Bermuda data.

    Gathers the device infor for receivers and transmitters, calculates
    distances etc.
    """

    def __init__(
        self, coordinator: BermudaDataUpdateCoordinator, config_entry, address: str
    ):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._device = coordinator.devices[address]
        self.area_reg = area_registry.async_get(coordinator.hass)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the co-ordinator

        (we don't need to implement this, but if we want to do anything special we can)
        """
        self.async_write_ha_state()

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._device.unique_id

    @property
    def device_info(self):
        """Implementing this creates an entry in the device registry."""

        # Match up our entity with any existing device entries.
        # For scanners we use ethernet MAC, which looks like they are
        # normally stored lowercased, otherwise we use our btmac, which
        # seem to be stored uppercased.
        if self._device.is_scanner:
            connection = {(dr.CONNECTION_NETWORK_MAC, self._device.address.lower())}
        elif self._device.beacon_type == BEACON_IBEACON_DEVICE:
            # ibeacon doesn't (yet) actually set a connection, but
            # this "matches" what it stores for identifier.
            connection = {("ibeacon", self._device.address.lower())}
        else:
            connection = {(dr.CONNECTION_BLUETOOTH, self._device.address.upper())}

        return {
            "identifiers": {(DOMAIN, self._device.unique_id)},
            "connections": connection,
            "name": self._device.prefname,
            # TODO: Could use this to indicate tracker type (IRK, iBeacon etc).
        }

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return {
            "attribution": ATTRIBUTION,
            "id": str(self.coordinator.data.get("id")),
            "integration": DOMAIN,
        }
