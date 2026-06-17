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
    BermudaSensorIn100AdcVoltage,
    BermudaSensorIn100Temperature,
    BermudaSensorIn100Vcc,
    BermudaSensorMicroLocation,
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
    "BermudaSensorIn100AdcVoltage",
    "BermudaSensorIn100Temperature",
    "BermudaSensorIn100Vcc",
    "BermudaSensorMicroLocation",
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
        # go over time. So we maintain a matrix of which ones we have already
        # spun-up so we don't duplicate any.
        entities = []
        for scanner in coordinator.get_scanners:
            # Wait until a remote scanner reports its wifi mac before creating its
            # per-scanner entities, so the unique_id (which prefers the wifi mac)
            # stays stable and won't flip later. USB/HCI scanners are ready at once.
            # Skip just this scanner, not the whole batch, so one not-yet-ready proxy
            # can't block the distance entities for all the others.
            if scanner.is_remote_scanner is None or (scanner.is_remote_scanner and scanner.address_wifi_mac is None):
                continue
            for address in created_devices:
                if address not in created_scanners.get(scanner.address, []):
                    _LOGGER.debug("Creating Scanner %s entities for %s", scanner.address, address)
                    entities.append(BermudaSensorScannerRange(coordinator, entry, address, scanner.address))
                    entities.append(BermudaSensorScannerRangeRaw(coordinator, entry, address, scanner.address))
                    created_scanners.setdefault(scanner.address, []).append(address)
        # We set update-before-add to False because we are being called back from
        # the update loop; triggering another update here would be bad.
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
