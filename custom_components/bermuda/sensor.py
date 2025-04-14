"""Sensor platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import RestoreSensor, SensorEntity
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    STATE_UNAVAILABLE,
    EntityCategory,
    UnitOfLength,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    _LOGGER,
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    SIGNAL_DEVICE_NEW,
)
from .entity import BermudaEntity, BermudaGlobalEntity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices = []  # list of already-created devices

    @callback
    def device_new(address: str, scanners: list[str]) -> None:
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        Make sure you have a full list of scanners ready before calling this.
        """
        if len(scanners) == 0:
            # Bail out until we get called with some scanners to work with!
            return
        for scanner in scanners:
            if (
                coordinator.devices[scanner].is_remote_scanner is None  # usb/HCI scanner's are fine.
                or (
                    coordinator.devices[scanner].is_remote_scanner  # usb/HCI scanner's are fine.
                    and coordinator.devices[scanner].address_wifi_mac is None
                )
            ):
                # This scanner doesn't have a wifi mac yet, bail out
                # until they are all filled out.
                return

        if address not in created_devices:
            entities = []
            entities.append(BermudaSensor(coordinator, entry, address))
            entities.append(BermudaSensorRange(coordinator, entry, address))
            entities.append(BermudaSensorScanner(coordinator, entry, address))
            entities.append(BermudaSensorRssi(coordinator, entry, address))
            entities.append(BermudaSensorAreaLastSeen(coordinator, entry, address))
            entities.append(BermudaSensorAreaSwitchReason(coordinator, entry, address))

            for scanner in scanners:
                entities.append(BermudaSensorScannerRange(coordinator, entry, address, scanner))
                entities.append(BermudaSensorScannerRangeRaw(coordinator, entry, address, scanner))
            # _LOGGER.debug("Sensor received new_device signal for %s", address)
            # We set update before add to False because we are being
            # call(back(ed)) from the update, so causing it to call another would be... bad.
            async_add_entities(entities, False)
            created_devices.append(address)
        else:
            # We've already created this one.
            # _LOGGER.debug("Ignoring duplicate creation request for %s", address)
            pass
        # tell the co-ord we've done it.
        coordinator.sensor_created(address)

    # Connect device_new to a signal so the coordinator can call it
    _LOGGER.debug("Registering device_new callback")
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))
    async_add_entities(
        (
            BermudaTotalProxyCount(coordinator, entry),
            BermudaActiveProxyCount(coordinator, entry),
            BermudaTotalDeviceCount(coordinator, entry),
            BermudaVisibleDeviceCount(coordinator, entry),
        )
    )


class BermudaSensor(BermudaEntity, SensorEntity):
    """bermuda Sensor class."""

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return self._device.unique_id

    @property
    def has_entity_name(self) -> bool:
        """
        Indicate that our name() method only returns the entity's name,
        so that HA should prepend the device name for the user.
        """
        return True

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Area"

    @property
    def native_value(self):
        """Return the state of the sensor."""
        # return self.coordinator.data.get("body")
        return self._device.area_name

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Declare if entity should be automatically enabled on adding."""
        return self.name in ["Area", "Distance"]

    @property
    def device_class(self):
        """Return de device class of the sensor."""
        # There isn't one for "Area Names" so we'll arbitrarily define our own.
        return "bermuda__custom_device_class"

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Provide state_attributes for the sensor entity."""
        # By default, it's the device's MAC
        current_mac = self._device.address
        # But metadevices have source_devices
        if self._device.address_type in [
            ADDR_TYPE_IBEACON,
            ADDR_TYPE_PRIVATE_BLE_DEVICE,
        ]:
            # Check the current sources and find the latest
            current_mac: str = STATE_UNAVAILABLE
            _best_stamp = 0
            for source_ad in self._device.scanners.values():
                if source_ad.stamp > _best_stamp:  # It's a valid ad
                    current_mac = source_ad.device_address
                    _best_stamp = source_ad.stamp

        # Limit how many attributes we list - prefer new sensors instead
        # since oft-changing attribs cause more db writes than sensors
        # "last_seen": self.coordinator.dt_mono_to_datetime(self._device.last_seen),
        attribs = {}
        if self.name in ["Area"]:
            attribs["area_id"] = self._device.area_id
            attribs["area_name"] = self._device.area_name
        attribs["current_mac"] = current_mac

        return attribs


class BermudaSensorScanner(BermudaSensor):
    """Sensor for name of nearest detected scanner."""

    @property
    def unique_id(self):
        return f"{self._device.unique_id}_scanner"

    @property
    def name(self):
        return "Nearest Scanner"

    @property
    def native_value(self):
        # Don't use area_scanner.name because it comes from the advert
        # entry. Instead refer to the BermudaDevice, which takes trouble
        # to use user-given names etc.
        if self._device.area_scanner is not None:
            return self.coordinator.devices[self._device.area_scanner.scanner_address].name
        return None


class BermudaSensorRssi(BermudaSensor):
    """Sensor for RSSI of closest scanner."""

    @property
    def unique_id(self):
        """Return unique id for the entity."""
        return f"{self._device.unique_id}_rssi"

    @property
    def name(self):
        return "Nearest RSSI"

    @property
    def native_value(self):
        return self._cached_ratelimit(self._device.area_rssi, fast_falling=False, fast_rising=True)

    @property
    def device_class(self):
        return SensorDeviceClass.SIGNAL_STRENGTH

    @property
    def native_unit_of_measurement(self):
        return SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    @property
    def state_class(self):
        """These are graphable measurements."""
        return SensorStateClass.MEASUREMENT


class BermudaSensorRange(BermudaSensor):
    """Extra sensor for range-to-closest-area."""

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"{self._device.unique_id}_range"

    @property
    def name(self):
        return "Distance"

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        distance = self._device.area_distance
        if distance is not None:
            return self._cached_ratelimit(round(distance, 1))
        return None

    @property
    def device_class(self):
        return SensorDeviceClass.DISTANCE

    @property
    def native_unit_of_measurement(self):
        """Results are in metres."""
        return UnitOfLength.METERS

    @property
    def state_class(self):
        """Measurement should result in graphed results."""
        return SensorStateClass.MEASUREMENT


class BermudaSensorScannerRange(BermudaSensorRange):
    """Create sensors for range to each scanner. Extends closest-range class."""

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry,
        address: str,
        scanner_address: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._device = coordinator.devices[address]
        self._scanner = coordinator.devices[scanner_address]

    @property
    def unique_id(self):
        # Retaining legacy wifi mac for unique_id
        return f"{self._device.unique_id}_{self._scanner.address_wifi_mac or self._scanner.address}_range"

    @property
    def name(self):
        return f"Distance to {self._scanner.name}"

    @property
    def native_value(self):
        """
        Expose distance to given scanner.

        Don't break if that scanner's never heard of us!
        """
        distance = None
        if (scanner := self._device.get_scanner(self._scanner.address)) is not None:
            distance = scanner.rssi_distance
        if distance is not None:
            return self._cached_ratelimit(round(distance, 3))
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """We need to reimplement this, since the attributes need to be scanner-specific."""
        devscanner = self._device.get_scanner(self._scanner.address)
        if hasattr(devscanner, "source"):
            return {
                "area_id": self._scanner.area_id,
                "area_name": self._scanner.area_name,
                "area_scanner_mac": self._scanner.address,
                "area_scanner_name": self._scanner.name,
            }
        else:
            return None


class BermudaSensorScannerRangeRaw(BermudaSensorScannerRange):
    """Provides un-filtered latest distances per-scanner."""

    @property
    def unique_id(self):
        # Using address_wifi_mac as a legacy action, because esphome changed from
        # sending WIFI MAC to BLE MAC as its source address, in ESPHome 2025.3.0
        #
        return f"{self._device.unique_id}_{self._scanner.address_wifi_mac or self._scanner.address}_range_raw"

    @property
    def name(self):
        return f"Unfiltered Distance to {self._scanner.name}"

    @property
    def native_value(self):
        """
        Expose distance to given scanner.

        Don't break if that scanner's never heard of us!
        """
        devscanner = self._device.get_scanner(self._scanner.address)
        distance = getattr(devscanner, "rssi_distance_raw", None)
        if distance is not None:
            return round(distance, 3)
        return None


class BermudaSensorAreaSwitchReason(BermudaSensor):
    """Sensor for area switch reason."""

    # _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Declare if entity should be automatically enabled on adding."""
        return False

    @property
    def name(self):
        return "Area Switch Diagnostic"

    @property
    def unique_id(self):
        return f"{self._device.unique_id}_area_switch_reason"

    @property
    def native_value(self):
        if self._device.diag_area_switch is not None:
            return self._device.diag_area_switch[:255]
        return None


class BermudaSensorAreaLastSeen(BermudaSensor, RestoreSensor):
    """Sensor for name of last seen area."""

    @property
    def unique_id(self):
        return f"{self._device.unique_id}_area_last_seen"

    @property
    def name(self):
        return "Area Last Seen"

    @property
    def native_value(self):
        return self._device.area_last_seen

    async def async_added_to_hass(self) -> None:
        """Restore last saved value before adding to HASS."""
        await super().async_added_to_hass()
        if (sensor_data := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = str(sensor_data.native_value)
            self._device.area_last_seen = str(sensor_data.native_value)


class BermudaGlobalSensor(BermudaGlobalEntity, SensorEntity):
    """bermuda Global Sensor class."""

    _attr_has_entity_name = True

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Area"

    @property
    def device_class(self):
        """Return de device class of the sensor."""
        return "bermuda__custom_device_class"


class BermudaTotalProxyCount(BermudaGlobalSensor):
    """Counts the total number of proxies we have access to."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_PROXY_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the number of proxies we have access to."""
        return self._cached_ratelimit(len(self.coordinator.scanner_list)) or 0

    @property
    def name(self):
        """Gets the name of the sensor."""
        return "Total proxy count"


class BermudaActiveProxyCount(BermudaGlobalSensor):
    """Counts the number of proxies that are active."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_ACTIVE_PROXY_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the number of proxies we have access to."""
        return self._cached_ratelimit(self.coordinator.count_active_scanners()) or 0

    @property
    def name(self):
        """Gets the name of the sensor."""
        return "Active proxy count"


class BermudaTotalDeviceCount(BermudaGlobalSensor):
    """Counts the total number of devices we can see."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_DEVICE_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the amount of devices we have seen."""
        return self._cached_ratelimit(len(self.coordinator.devices)) or 0

    @property
    def name(self):
        """Gets the name of the sensor."""
        return "Total device count"


class BermudaVisibleDeviceCount(BermudaGlobalSensor):
    """Counts the number of devices that are active."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_VISIBLE_DEVICE_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the amount of devices that are active."""
        return self._cached_ratelimit(self.coordinator.count_active_devices()) or 0

    @property
    def name(self):
        """Gets the name of the sensor."""
        return "Visible device count"
