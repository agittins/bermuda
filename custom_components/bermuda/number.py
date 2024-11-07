"""Create Number entities - like per-device rssi ref_power, etc."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberExtraStoredData,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
    """Load Number entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices = []  # list of devices we've already created entities for

    @callback
    def device_new(address: str, scanners: list[str]) -> None:  # pylint: disable=unused-argument
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        Make sure you have a full list of scanners ready before calling this.
        """
        if address not in created_devices:
            entities = []
            entities.append(BermudaNumber(coordinator, entry, address))
            # We set update before add to False because we are being
            # call(back(ed)) from the update, so causing it to call another would be... bad.
            async_add_devices(entities, False)
            created_devices.append(address)
        else:
            # _LOGGER.debug(
            #     "Ignoring create request for existing dev_tracker %s", address
            # )
            pass
        # tell the co-ord we've done it.
        coordinator.number_created(address)

    # Connect device_new to a signal so the coordinator can call it
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))

    # Now we must tell the co-ord to do initial refresh, so that it will call our callback.
    # await coordinator.async_config_entry_first_refresh()


class BermudaNumber(BermudaEntity, RestoreNumber):
    """A Number entity for bermuda devices."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Calibration Ref Power at 1m. 0 for default."
    _attr_translation_key = "ref_power"
    _attr_device_class = NumberDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.CONFIG
    # _attr_entity_registry_enabled_default = False
    _attr_native_min_value = -127
    _attr_native_max_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialise the number entity."""
        self.restored_data: NumberExtraStoredData | None = None
        super().__init__(coordinator, entry, address)

    async def async_added_to_hass(self) -> None:
        """Restore values from HA storage on startup."""
        await super().async_added_to_hass()
        self.restored_data = await self.async_get_last_number_data()
        if self.restored_data is not None and self.restored_data.native_value is not None:
            self.coordinator.devices[self.address].set_ref_power(self.restored_data.native_value)

    @property
    def native_value(self) -> float | None:
        """Return value of number."""
        # if self.restored_data is not None and self.restored_data.native_value is not None:
        #     return self.restored_data.native_value
        return self.coordinator.devices[self.address].ref_power
        return 0

    async def async_set_native_value(self, value: float) -> None:
        """Set value."""
        self.coordinator.devices[self.address].set_ref_power(value)
        self.async_write_ha_state()
        # Beware that STATE_DUMP_INTERVAL for restore_state's dump_state
        # is 15 minutes, so if HA is killed instead of exiting cleanly,
        # updated values may not be restored. Tempting to schedule a dump
        # here, since updates to calib will be infrequent, but users are
        # moderately likely to restart HA after playing with them.

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"{self._device.unique_id}_ref_power"

    # @property
    # def extra_state_attributes(self) -> Mapping[str, Any]:
    #     """Return extra state attributes for this device."""
    #     return {"scanner": self._device.area_scanner, "area": self._device.area_name}

    # @property
    # def state(self) -> str:
    #     """Return the state of the device."""
    #     return self._device.zone

    # @property
    # def source_type(self) -> SourceType:
    #     """Return the source type, eg gps or router, of the device."""
    #     return SourceType.BLUETOOTH_LE

    # @property
    # def icon(self) -> str:
    #     """Return device icon."""
    #     return "mdi:bluetooth-connect" if self._device.zone == STATE_HOME else "mdi:bluetooth-off"
