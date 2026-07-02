"""Per-device sensor entities for Bermuda (split from sensor.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import RestoreSensor, SensorEntity
from homeassistant.components.sensor.const import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
    EntityCategory,
    UnitOfLength,
    UnitOfTemperature,
)

from .const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    ICON_MICROLOCATION,
)
from .entity import BermudaEntity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.helpers.typing import StateType

    from . import BermudaConfigEntry
    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator


class BermudaSensor(BermudaEntity, SensorEntity):
    """bermuda Sensor class."""

    _attr_has_entity_name = True
    _attr_translation_key = "area"

    @property
    def unique_id(self) -> str | None:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return self._device.unique_id

    @property
    def native_value(self) -> StateType:
        """
        Return the state of the sensor.

        Typed as the broad ``StateType`` (rather than just ``str``) because the
        numeric subclasses below (rssi/range/in100 sensors) override this with
        ``float | None``; overrides must be return-type-compatible with this base.
        """
        # Return not_home when device is not detected, for consistency with device_tracker
        if self._device.area_name is None:
            return STATE_NOT_HOME
        return self._device.area_name

    @property
    def icon(self) -> str:
        """Provide a custom icon for the Area sensor."""
        return self._device.area_icon

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Declare if entity should be automatically enabled on adding."""
        return self._attr_translation_key in ("area", "distance", "floor")

    # No device_class on the text sensors (area/floor/scanner/...): the former
    # custom "bermuda__custom_device_class" had no state translation behind it and
    # kept these state changes out of Home Assistant's logbook/history. Numeric
    # subclasses (range/rssi) still set their own real device_class.

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
            current_mac = STATE_UNAVAILABLE
            _best_stamp: float = 0
            for source_ad in self._device.adverts.values():
                if source_ad.stamp > _best_stamp:  # It's a valid ad
                    current_mac = source_ad.device_address
                    _best_stamp = source_ad.stamp

        # Limit how many attributes we list - prefer new sensors instead
        # since oft-changing attribs cause more db writes than sensors
        # "last_seen": self.coordinator.dt_mono_to_datetime(self._device.last_seen),
        attribs: dict[str, Any] = {}
        if self._attr_translation_key in ("area", "floor"):
            attribs["area_id"] = self._device.area_id
            attribs["area_name"] = self._device.area_name
            attribs["floor_id"] = self._device.floor_id
            attribs["floor_name"] = self._device.floor_name
            attribs["floor_level"] = self._device.floor_level
            # Surface the finer-grained spot here too, so dashboards/automations
            # can read it without enabling the dedicated micro-location sensor.
            attribs["micro_location"] = self._device.micro_location_name
            attribs["micro_location_confidence"] = self._device.micro_location_confidence
        attribs["current_mac"] = current_mac

        return attribs


class BermudaSensorFloor(BermudaSensor):
    """Sensor for the Floor of the current Area."""

    _attr_translation_key = "floor"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_floor"

    @property
    def icon(self) -> str:
        """Provide a custom icon for the Floor sensor."""
        return self._device.floor_icon

    @property
    def native_value(self) -> str:
        # Don't use area_scanner.name because it comes from the advert
        # entry. Instead refer to the BermudaDevice, which takes trouble
        # to use user-given names etc.
        # Return not_home when device is not detected, for consistency with device_tracker
        if self._device.floor_name is None:
            return STATE_NOT_HOME
        return self._device.floor_name


class BermudaSensorScanner(BermudaSensor):
    """Sensor for name of nearest detected scanner."""

    _attr_translation_key = "nearest_scanner"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_scanner"

    @property
    def native_value(self) -> str:
        # Don't use area_scanner.name because it comes from the advert
        # entry. Instead refer to the BermudaDevice, which takes trouble
        # to use user-given names etc.
        # Return not_home when device is not detected, for consistency with device_tracker
        if self._device.area_advert is not None:
            scanner_device = self.coordinator.devices.get(self._device.area_advert.scanner_address)
            if scanner_device is not None:
                return scanner_device.name
        return STATE_NOT_HOME

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Add the nearest scanner's HA entity_id (when known) to the base attributes."""
        attribs = dict(super().extra_state_attributes or {})
        if self._device.area_advert is not None:
            scanner_device = self.coordinator.devices.get(self._device.area_advert.scanner_address)
            if scanner_device is not None and scanner_device.scanner_entity_id is not None:
                attribs["scanner_entity_id"] = scanner_device.scanner_entity_id
        return attribs


class BermudaSensorRssi(BermudaSensor):
    """Sensor for RSSI of closest scanner."""

    _attr_translation_key = "nearest_rssi"

    @property
    def unique_id(self) -> str:
        """Return unique id for the entity."""
        return f"{self._device.unique_id}_rssi"

    @property
    def native_value(self) -> float | None:
        return self._cached_ratelimit(self._device.area_rssi, fast_falling=False, fast_rising=True)

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.SIGNAL_STRENGTH

    @property
    def native_unit_of_measurement(self) -> str:
        return SIGNAL_STRENGTH_DECIBELS_MILLIWATT

    @property
    def state_class(self) -> SensorStateClass:
        """These are graphable measurements."""
        return SensorStateClass.MEASUREMENT


class BermudaSensorRange(BermudaSensor):
    """Extra sensor for range-to-closest-area."""

    _attr_translation_key = "distance"

    @property
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"{self._device.unique_id}_range"

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        distance = self._device.area_distance
        if distance is not None:
            return self._cached_ratelimit(round(distance, 1))
        return None

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.DISTANCE

    @property
    def native_unit_of_measurement(self) -> str:
        """Results are in metres."""
        return UnitOfLength.METERS

    @property
    def state_class(self) -> SensorStateClass:
        """Measurement should result in graphed results."""
        return SensorStateClass.MEASUREMENT


class BermudaSensorScannerRange(BermudaSensorRange):
    """Create sensors for range to each scanner. Extends closest-range class."""

    _attr_translation_key = "distance_to_scanner"

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
        address: str,
        scanner_address: str,
    ) -> None:
        super().__init__(coordinator, config_entry, address)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._device = coordinator.devices[address]
        _scanner = coordinator.devices.get(scanner_address)
        if _scanner is None:
            msg = f"Scanner device {scanner_address} not found in coordinator.devices"
            raise KeyError(msg)
        # Declared non-Optional (unlike the dict .get() above): the raise above
        # guarantees this is always set for the lifetime of the entity, and every
        # property below relies on that.
        self._scanner: BermudaDevice = _scanner

    @property
    def available(self) -> bool:
        """Unavailable once the parent scanner (proxy) drops out of the roster."""
        return super().available and self._scanner.address in self.coordinator.scanner_list

    @property
    def unique_id(self) -> str:
        # Retaining legacy wifi mac for unique_id
        return f"{self._device.unique_id}_{self._scanner.address_wifi_mac or self._scanner.address}_range"

    @property  # type: ignore[misc]
    def translation_placeholders(self) -> dict[str, str]:
        """
        Return translation placeholders for dynamic entity name.

        HA's ``Entity.translation_placeholders`` is a ``@final @cached_property``
        (integrations are meant to set ``_attr_translation_placeholders`` instead).
        This override is intentional: unlike an attribute set once at construction,
        it recomputes ``scanner_name`` from ``self._scanner.name`` on every access,
        so a later scanner rename is reflected immediately. `@final` isn't enforced
        by CPython, so this works; the ignore only silences mypy's (correct) note
        that HA does not want subclasses overriding this.
        """
        return {"scanner_name": self._scanner.name}

    @property
    def native_value(self) -> float | None:
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
        if devscanner is not None:
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

    _attr_translation_key = "unfiltered_distance_to_scanner"

    @property
    def unique_id(self) -> str:
        # Using address_wifi_mac as a legacy action, because esphome changed from
        # sending WIFI MAC to BLE MAC as its source address, in ESPHome 2025.3.0
        #
        return f"{self._device.unique_id}_{self._scanner.address_wifi_mac or self._scanner.address}_range_raw"

    @property
    def native_value(self) -> float | None:
        """
        Expose distance to given scanner.

        Don't break if that scanner's never heard of us!
        """
        devscanner = self._device.get_scanner(self._scanner.address)
        distance = devscanner.rssi_distance_raw if devscanner is not None else None
        if distance is not None:
            return round(distance, 3)
        return None


class BermudaSensorAreaSwitchReason(BermudaSensor):
    """Sensor for area switch reason."""

    _attr_translation_key = "area_switch_diagnostic"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_area_switch_reason"

    @property
    def native_value(self) -> str | None:
        """Return the concise reason for the last area switch (full dump is an attribute)."""
        if self._device.diag_area_switch_reason is not None:
            return self._device.diag_area_switch_reason[:255]
        return None

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Expose the full area-switch diagnostic dump alongside the concise state."""
        attribs = dict(super().extra_state_attributes or {})
        if self._device.diag_area_switch is not None:
            attribs["diagnostic"] = self._device.diag_area_switch
        return attribs


class BermudaSensorAreaLastSeen(BermudaSensor, RestoreSensor):
    """Sensor for name of last seen area."""

    _attr_translation_key = "area_last_seen"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_area_last_seen"

    @property
    def icon(self) -> str:
        """Provide a custom icon for the Area Last Seen sensor."""
        return self._device.area_last_seen_icon

    @property
    def native_value(self) -> str | None:
        return self._device.area_last_seen

    async def async_added_to_hass(self) -> None:
        """Restore last saved value before adding to HASS."""
        await super().async_added_to_hass()
        if (
            sensor_data := await self.async_get_last_sensor_data()
        ) is not None and sensor_data.native_value is not None:
            # Guard against a restored None becoming the literal string "None".
            self._attr_native_value = str(sensor_data.native_value)
            self._device.area_last_seen = str(sensor_data.native_value)


class BermudaSensorMicroLocation(BermudaSensor):
    """Sensor for the device's current micro-location (a named spot, eg 'Key hook')."""

    _attr_translation_key = "micro_location"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_micro_location"

    @property
    def native_value(self) -> str | None:
        """The name of the matched spot, or None when not at a known spot."""
        return self._device.micro_location_name

    @property
    def icon(self) -> str:
        return ICON_MICROLOCATION

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Enabled by default — it's the headline feature for tracked items."""
        return True

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Expose match confidence and context for automations/MCP."""
        return {
            "micro_location_id": self._device.micro_location_id,
            "confidence": self._device.micro_location_confidence,
            "area_name": self._device.area_name,
            "last_seen": self._device.micro_location_last_seen,
        }


class BermudaSensorIn100Vcc(BermudaSensor):
    """InPlay IN100 / DFRobot Fermion supply voltage (VCC). Only created for detected IN100 devices."""

    _attr_translation_key = "in100_vcc"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_in100_vcc"

    @property
    def native_value(self) -> float | None:
        return self._device.in100_vcc

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.VOLTAGE

    @property
    def native_unit_of_measurement(self) -> str:
        return "V"

    @property
    def state_class(self) -> SensorStateClass:
        return SensorStateClass.MEASUREMENT

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Only ever created for detected IN100 devices, so enable by default."""
        return True


class BermudaSensorIn100Temperature(BermudaSensor):
    """InPlay IN100 / DFRobot Fermion temperature. Only created for detected IN100 devices."""

    _attr_translation_key = "in100_temperature"

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_in100_temperature"

    @property
    def native_value(self) -> float | None:
        return self._device.in100_temp_c

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS

    @property
    def state_class(self) -> SensorStateClass:
        return SensorStateClass.MEASUREMENT

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Only ever created for detected IN100 devices, so enable by default."""
        return True


class BermudaSensorIn100AdcVoltage(BermudaSensor):
    """InPlay IN100 / DFRobot Fermion ADC voltage. Only created for detected IN100 devices."""

    _attr_translation_key = "in100_adc_voltage"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.unique_id}_in100_adc_voltage"

    @property
    def native_value(self) -> float | None:
        return self._device.in100_adc_voltage

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.VOLTAGE

    @property
    def native_unit_of_measurement(self) -> str:
        return "V"

    @property
    def state_class(self) -> SensorStateClass:
        return SensorStateClass.MEASUREMENT

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Only ever created for detected IN100 devices, so enable by default."""
        return True
