"""Create device_tracker entities for Bermuda devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

# The top-level module is the canonical import path (the config_entry alias is
# deprecated for removal in 2027.6), but it re-exports BaseScannerEntity without
# listing it in __all__, so mypy needs the attr-defined escape hatch.
from homeassistant.components.device_tracker import BaseScannerEntity  # type: ignore[attr-defined]
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.const import STATE_HOME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
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
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        Make sure you have a full list of scanners ready before calling this.
        """
        if address not in created_devices:
            # update_before_add=False because we are being call(back(ed)) from
            # the update, so causing it to call another would be... bad.
            async_add_devices([BermudaDeviceTracker(coordinator, entry, address)], update_before_add=False)
            created_devices.append(address)
        # tell the co-ord we've done it.
        coordinator.device_tracker_created(address)

    # Connect device_new to a signal so the coordinator can call it
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaDeviceTracker(BermudaEntity, BaseScannerEntity):
    """
    A trackable Bermuda Device.

    BaseScannerEntity (HA 2026.6+) is the base HA recommends for BLE beacon
    trackers: the default state stays home/not_home (identical to the previous
    BaseTrackerEntity behaviour), and users gain the in_zones attribute plus
    the ability to associate the tracker with any zone, not just home.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_translation_key = "bermuda_tracker"
    _attr_source_type = SourceType.BLUETOOTH_LE

    # unique_id: inherited from BermudaEntity (the bare device address).

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Return extra state attributes for this device."""
        _scannername = self._device.area_advert.name if self._device.area_advert is not None else None
        return {"scanner": _scannername, "area": self._device.area_name}

    @property
    def is_connected(self) -> bool:
        """Whether the device has been seen recently (drives home/not_home)."""
        return self._device.zone == STATE_HOME

    @property
    def icon(self) -> str:
        """Return device icon."""
        return "mdi:bluetooth-connect" if self._device.zone == STATE_HOME else "mdi:bluetooth-off"
