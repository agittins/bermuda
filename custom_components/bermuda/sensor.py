"""Sensor platform for Bermuda BLE Trilateration."""  # fmt: skip
from collections.abc import Mapping
from typing import Any

from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import UnitOfLength
from homeassistant.core import callback
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BermudaDataUpdateCoordinator
from .const import DOMAIN
from .const import SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

# from .const import DEFAULT_NAME
# from .const import ICON
# from .const import SENSOR


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # We define a callback and attatch it to an (event?) listener,
    # which the co-ordinator calls each time it finds
    # a (new) device to track.
    # FIXME: how do we seed the list of scanners, and ensure each device gets scanner
    # range entities set up when that list changes?

    @callback
    def device_new(address: str, scanners: [str]) -> None:
        """Create entities for newly-found device

        Make sure you have a full list of scanners ready before calling this.
        """
        entities = []
        entities.append(BermudaSensor(coordinator, entry, address))
        entities.append(BermudaSensorRange(coordinator, entry, address))
        for scanner in scanners:
            entities.append(
                BermudaSensorScannerRange(coordinator, entry, address, scanner)
            )
        # We set update before add to False because we are being
        # call(back(ed)) from the update, so causing it to call another would be... bad.
        async_add_devices(entities, False)
        coordinator.sensor_created(address)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaSensor(BermudaEntity):
    """bermuda Sensor class."""

    @property
    def unique_id(self):
        """ "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user"""
        return self._device.unique_id

    @property
    def has_entity_name(self) -> bool:
        """Indicate that our name() method only returns the entity's name,
        so that HA should prepend the device name for the user."""
        return True

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Area"

    @property
    def state(self):
        """Return the state of the sensor."""
        # return self.coordinator.data.get("body")
        return self._device.area_name

    # @property
    # def icon(self):
    #    """Return the icon of the sensor."""
    #    return ICON

    @property
    def device_class(self):
        """Return de device class of the sensor."""
        # There isn't one for "Area Names" so we'll arbitrarily define our own.
        return "bermuda__custom_device_class"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        return {
            "last_seen": self.coordinator.dt_mono_to_datetime(self._device.last_seen),
            "area_id": self._device.area_id,
            "area_name": self._device.area_name,
            "area_distance": self._device.area_distance,
            "area_rssi": self._device.area_rssi,
            "area_scanner": self._device.area_scanner,
        }


class BermudaSensorRange(BermudaSensor):
    """Extra sensor for range-to-closest-area

    Note it extends the other sensor, so we only need to set unique_id, name and value
    """

    @property
    def unique_id(self):
        """ "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user"""
        return self._device.unique_id + "_range"

    @property
    def name(self):
        return "Distance"

    @property
    def native_value(self):
        """Define the native value of the measurement."""
        distance = self._device.area_distance
        if distance is not None:
            return round(distance, 3)
        return None

    @property
    def state(self):
        """Return the user-facing state of the sensor"""
        distance = self._device.area_distance
        if distance is not None:
            return round(distance, 3)
        return None

    @property
    def device_class(self):
        return SensorDeviceClass.DISTANCE

    @property
    def unit_of_measurement(self):
        """Results are in Metres"""
        return UnitOfLength.METERS

    @property
    def native_unit_of_measurement(self):
        """Results are in metres"""
        return UnitOfLength.METERS

    @property
    def state_class(self):
        """Measurement should result in graphed results"""
        return SensorStateClass.MEASUREMENT


class BermudaSensorScannerRange(BermudaSensorRange):
    """Create sensors for range to each scanner. Extends closest-range class."""

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry,
        address: str,
        scanner_address: str,
    ):
        super().__init__(coordinator, config_entry, address)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._device = coordinator.devices[address]
        self._scanner = coordinator.devices[scanner_address]

    @property
    def unique_id(self):
        return self._device.unique_id + "_" + self._scanner.address + "_range"

    @property
    def name(self):
        return "Distance to " + self._scanner.name

    @property
    def native_value(self):
        """Expose distance to given scanner.

        Don't break if that scanner's never heard of us!"""
        devscanner = self._device.scanners.get(self._scanner.address, {})
        distance = getattr(devscanner, "rssi_distance", None)
        if distance is not None:
            return round(distance, 3)
        return None

    @property
    def state(self):
        """Expose distance to given scanner.

        Don't break if that scanner's never heard of us!"""
        devscanner = self._device.scanners.get(self._scanner.address, {})
        distance = getattr(devscanner, "rssi_distance", None)
        if distance is not None:
            return round(distance, 3)
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """We need to reimplement this, since the attributes need to be scanner-specific."""
        devscanner = self._device.scanners.get(self._scanner.address, {})
        if hasattr(devscanner, "source"):
            return {
                "last_seen": self.coordinator.dt_mono_to_datetime(devscanner.stamp),
                "area_id": self._scanner.area_id,
                "area_name": self._scanner.area_name,
                "area_rssi": devscanner.rssi,
                "area_scanner_mac": self._scanner.address,
                "area_scanner_name": self._scanner.name,
            }
        else:
            return None
