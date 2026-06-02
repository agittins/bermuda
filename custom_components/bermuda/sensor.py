"""Sensor platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    _LOGGER,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
)
from .sensor_entities import (
    BermudaSensor,
    BermudaSensorAreaLastSeen,
    BermudaSensorAreaSwitchReason,
    BermudaSensorFloor,
    BermudaSensorRange,
    BermudaSensorRssi,
    BermudaSensorScanner,
    BermudaSensorScannerRange,
    BermudaSensorScannerRangeRaw,
)
from .sensor_global import (
    BermudaActiveProxyCount,
    BermudaGlobalSensor,
    BermudaTotalDeviceCount,
    BermudaTotalProxyCount,
    BermudaVisibleDeviceCount,
)

PARALLEL_UPDATES = 0

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


__all__ = [
    "BermudaActiveProxyCount",
    "BermudaGlobalSensor",
    "BermudaSensor",
    "BermudaSensorAreaLastSeen",
    "BermudaSensorAreaSwitchReason",
    "BermudaSensorFloor",
    "BermudaSensorRange",
    "BermudaSensorRssi",
    "BermudaSensorScanner",
    "BermudaSensorScannerRange",
    "BermudaSensorScannerRangeRaw",
    "BermudaTotalDeviceCount",
    "BermudaTotalProxyCount",
    "BermudaVisibleDeviceCount",
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices: list[str] = []  # list of already-created devices
    created_scanners: dict[str, list[str]] = {}  # list of scanner:address for created entities

    @callback
    def device_new(address: str) -> None:
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        """
        # if len(scanners) == 0:
        #     # Bail out until we get called with some scanners to work with!
        #     return
        # for scanner in scanners:
        #     if (
        #         coordinator.devices[scanner]._is_remote_scanner is None  # usb/HCI scanner's are fine.
        #         or (
        #             coordinator.devices[scanner]._is_remote_scanner  # usb/HCI scanner's are fine.
        #             and coordinator.devices[scanner].address_wifi_mac is None
        #         )
        #     ):
        #         # This scanner doesn't have a wifi mac yet, bail out
        #         # until they are all filled out.
        #         return

        if address not in created_devices:
            entities = []
            entities.append(BermudaSensor(coordinator, entry, address))
            if coordinator.have_floors:
                entities.append(BermudaSensorFloor(coordinator, entry, address))
            entities.append(BermudaSensorRange(coordinator, entry, address))
            entities.append(BermudaSensorScanner(coordinator, entry, address))
            entities.append(BermudaSensorRssi(coordinator, entry, address))
            entities.append(BermudaSensorAreaLastSeen(coordinator, entry, address))
            entities.append(BermudaSensorAreaSwitchReason(coordinator, entry, address))

            # _LOGGER.debug("Sensor received new_device signal for %s", address)
            # We set update before add to False because we are being
            # call(back(ed)) from the update, so causing it to call another would be... bad.
            async_add_entities(entities, False)
            created_devices.append(address)
        else:
            # We've already created this one.
            # _LOGGER.debug("Ignoring duplicate creation request for %s", address)
            pass
        # Get the per-scanner entities set up to match
        create_scanner_entities()
        # tell the co-ord we've done it.
        coordinator.sensor_created(address)

    def create_scanner_entities():
        # These are per-proxy entities on each device, and scanners may come and
        # go over time. So we need to maintain our matrix of which ones we have already
        # spun-up so we don't duplicate any.

        for scanner in coordinator.get_scanners:
            if (
                scanner.is_remote_scanner is None  # usb/HCI scanner's are fine.
                or (scanner.is_remote_scanner and scanner.address_wifi_mac is None)
            ):
                # This scanner doesn't have a wifi mac yet, bail out
                # until they are all filled out.
                return

        entities = []
        for scanner in coordinator.scanner_list:
            for address in created_devices:
                if address not in created_scanners.get(scanner, []):
                    _LOGGER.debug(
                        "Creating Scanner %s entities for %s",
                        scanner,
                        address,
                    )
                    entities.append(BermudaSensorScannerRange(coordinator, entry, address, scanner))
                    entities.append(BermudaSensorScannerRangeRaw(coordinator, entry, address, scanner))
                    created_entry = created_scanners.setdefault(scanner, [])
                    created_entry.append(address)
        # _LOGGER.debug("Sensor received new_device signal for %s", address)
        # We set update before add to False because we are being
        # call(back(ed)) from the update, so causing it to call another would be... bad.
        async_add_entities(entities, False)

    @callback
    def scanners_changed() -> None:
        """Callback for event from coordinator advising that the roster of scanners has changed."""
        create_scanner_entities()

    # Connect device_new to a signal so the coordinator can call it
    _LOGGER.debug("Registering device_new and scanners_changed callbacks")
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_SCANNERS_CHANGED, scanners_changed))

    # Create Global Bermuda entities
    async_add_entities(
        (
            BermudaTotalProxyCount(coordinator, entry),
            BermudaActiveProxyCount(coordinator, entry),
            BermudaTotalDeviceCount(coordinator, entry),
            BermudaVisibleDeviceCount(coordinator, entry),
        )
    )
