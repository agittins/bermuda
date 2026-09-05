"""Create Switch entities - like per-device "use for occupancy" opt-in."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from .const import SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Switch entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices = []  # list of devices we've already created entities for

    @callback
    def device_new(address: str) -> None:
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        """
        if address not in created_devices:
            entities = []
            entities.append(BermudaOccupancySwitch(coordinator, entry, address))
            async_add_devices(entities, False)
            created_devices.append(address)
        # tell the co-ord we've done it.
        coordinator.switch_created(address)

    # Connect device_new to a signal so the coordinator can call it
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaOccupancySwitch(BermudaEntity, RestoreEntity, SwitchEntity):
    """Per-device opt-in for counting toward area occupancy."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Use for occupancy"
    _attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        """Restore switch state from HA storage on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self.coordinator.devices[self.address].set_create_occupancy(last_state.state == STATE_ON)

    @property
    def is_on(self) -> bool:
        """Return true if this device counts toward area occupancy."""
        return self.coordinator.devices[self.address].create_occupancy

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on occupancy tracking for this device."""
        self.coordinator.devices[self.address].set_create_occupancy(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off occupancy tracking for this device."""
        self.coordinator.devices[self.address].set_create_occupancy(False)
        self.async_write_ha_state()

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"{self._device.unique_id}_occupancy"
