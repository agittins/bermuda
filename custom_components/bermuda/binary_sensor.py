"""Binary sensor platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import BINARY_SENSOR, BINARY_SENSOR_DEVICE_CLASS, DEFAULT_NAME
from .entity import BermudaEntity

# from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup binary_sensor platform."""
    # coordinator = hass.data[DOMAIN][entry.entry_id]
    # AJG async_add_devices([BermudaBinarySensor(coordinator, entry)])


class BermudaBinarySensor(BermudaEntity, BinarySensorEntity):
    """bermuda binary_sensor class."""

    @property
    def name(self):
        """Return the name of the binary_sensor."""
        return f"{DEFAULT_NAME}_{BINARY_SENSOR}"

    @property
    def device_class(self):
        """Return the class of this binary_sensor."""
        return BINARY_SENSOR_DEVICE_CLASS

    @property
    def is_on(self):
        """Return true if the binary_sensor is on."""
        # return self.coordinator.data.get("title", "") == "foo"
        return True
