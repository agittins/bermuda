"""Global (hub-wide) sensor entities for Bermuda (split from sensor.py)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import SensorStateClass
from homeassistant.const import (
    EntityCategory,
)

from .entity import BermudaGlobalEntity


class BermudaGlobalSensor(BermudaGlobalEntity, SensorEntity):
    """bermuda Global Sensor class."""

    _attr_has_entity_name = True
    # No device_class: the former custom "bermuda__custom_device_class" had no state
    # translation behind it (these are plain numeric counts).


class BermudaTotalProxyCount(BermudaGlobalSensor):
    """Counts the total number of proxies we have access to."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "total_proxy_count"

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


class BermudaActiveProxyCount(BermudaGlobalSensor):
    """Counts the number of proxies that are active."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "active_proxy_count"

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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes with area breakdown."""
        scanners = self.coordinator.get_active_scanner_summary()

        # Group scanners by area
        areas: dict[str, int] = {}
        total_active = 0
        max_age = 10  # seconds

        for scanner in scanners:
            area_name = scanner.get("area_name")
            last_stamp_age = scanner.get("last_stamp_age", float("inf"))

            if last_stamp_age <= max_age:
                total_active += 1
                if area_name and area_name != "null":
                    areas[area_name] = areas.get(area_name, 0) + 1

        return {
            "areas": areas,
            "total_active": total_active,
        }


class BermudaTotalDeviceCount(BermudaGlobalSensor):
    """Counts the total number of devices we can see."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "total_device_count"

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


class BermudaVisibleDeviceCount(BermudaGlobalSensor):
    """Counts the number of devices that are active."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "visible_device_count"

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
