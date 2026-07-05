"""Create device_tracker entities for Bermuda devices."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.components.device_tracker import BaseScannerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BermudaConfigEntry
from .const import SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

if TYPE_CHECKING:
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Device Tracker entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices = []  # list of devices we've already created entities for

    @callback
    def device_new(address: str) -> None:
        """Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        Make sure you have a full list of scanners ready before calling this.
        """
        if address not in created_devices:
            entities = []
            entities.append(BermudaDeviceTracker(coordinator, entry, address))
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
        coordinator.device_tracker_created(address)

    # Connect device_new to a signal so the coordinator can call it
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))

    # Now we must tell the co-ord to do initial refresh, so that it will call our callback.
    # await coordinator.async_config_entry_first_refresh()


class BermudaDeviceTracker(BermudaEntity, BaseScannerEntity):
    """A trackable Bermuda Device."""

    # We switched from BaseTrackerEntity to BaseScannerEntity for in_zone changes
    # and also because Tracker now seems more reliant on the lat/long
    # being present in order to report state correctly).

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Bermuda Tracker"
    _attr_source_type = SourceType.BLUETOOTH_LE

    @property
    def unique_id(self):
        """Uniquely identify this sensor."""
        return self._device.unique_id

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return extra state attributes for this device."""
        _scannername = self._device.area_advert.name if self._device.area_advert is not None else None
        return {"scanner": _scannername, "area": self._device.area_name}

    @property
    def is_connected(self):
        """Give boolean result for home/not_home."""
        if self._device.zone is None:
            return None
        # if anything other than not home, we are connected.
        return self._device.zone != STATE_NOT_HOME

    @property
    def icon(self) -> str:
        """Return device icon."""
        return "mdi:bluetooth-connect" if self._device.zone == STATE_HOME else "mdi:bluetooth-off"
