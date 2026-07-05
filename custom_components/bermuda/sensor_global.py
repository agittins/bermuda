"""Global (hub-wide) sensor entities for Bermuda (split from sensor.py)."""

from __future__ import annotations

from typing import Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor.const import SensorStateClass
from homeassistant.const import (
    EntityCategory,
)

from .const import NEARBY_MAX_AGE, NEARBY_MAX_DEVICES
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
    def unique_id(self) -> str:
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
    def unique_id(self) -> str:
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
    def unique_id(self) -> str:
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
    def unique_id(self) -> str:
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return "BERMUDA_GLOBAL_VISIBLE_DEVICE_COUNT"

    @property
    def native_value(self) -> int:
        """Gets the amount of devices that are active."""
        return self._cached_ratelimit(self.coordinator.count_active_devices()) or 0


class BermudaNearbyDevices(BermudaGlobalSensor):
    """
    Live "what BLE is around me" discovery sensor, akin to a phone scanner app.

    Its state is the count of devices heard within the last NEARBY_MAX_AGE
    seconds, and its ``devices`` attribute is the list of those devices ranked
    by signal strength (address, name, manufacturer, category, rssi, distance,
    nearest scanner/area, whether already tracked and how long since last seen).

    Disabled by default: it rebuilds every update cycle, so it is a deliberate,
    opt-in tool for hunting new devices rather than something to record forever.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "nearby_devices"
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        """Stable id for the entity registry."""
        return "BERMUDA_GLOBAL_NEARBY_DEVICES"

    def _nearby(self) -> list[dict[str, Any]]:
        """Build the signal-ranked list of recently-heard (non-scanner) devices."""
        cutoff = monotonic_time_coarse() - NEARBY_MAX_AGE
        rows: list[dict[str, Any]] = []
        for device in self.coordinator.devices.values():
            if device.is_scanner or device.last_seen <= cutoff:
                continue
            rows.append(
                {
                    "address": device.address.upper(),
                    "name": device.name,
                    "manufacturer": device.manufacturer,
                    "tracker": device.tracker_type,
                    "category": device.category,
                    "rssi": round(device.area_rssi) if device.area_rssi is not None else None,
                    "distance": round(device.area_distance, 1) if device.area_distance is not None else None,
                    "area": device.area_name,
                    "scanner": device.area_advert.name if device.area_advert is not None else None,
                    "tracked": bool(device.create_sensor),
                    "age": round(monotonic_time_coarse() - device.last_seen, 1),
                }
            )
        # Strongest signal first; devices with no rssi yet sink to the bottom.
        rows.sort(key=lambda r: r["rssi"] if r["rssi"] is not None else -9999, reverse=True)
        return rows

    @property
    def native_value(self) -> int:
        """Number of distinct devices heard within NEARBY_MAX_AGE seconds."""
        cutoff = monotonic_time_coarse() - NEARBY_MAX_AGE
        return sum(1 for d in self.coordinator.devices.values() if not d.is_scanner and d.last_seen > cutoff)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the ranked nearby-device list (capped) for dashboards/automations."""
        return {"devices": self._nearby()[:NEARBY_MAX_DEVICES]}
