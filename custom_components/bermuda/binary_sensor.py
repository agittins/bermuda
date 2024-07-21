"""Binary sensor platform for Bermuda BLE Trilateration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import BINARY_SENSOR
from .const import BINARY_SENSOR_DEVICE_CLASS
from .const import DEFAULT_NAME
from .const import DOMAIN  # ChatGPT: Uncommented to ensure DOMAIN is available
from .entity import BermudaEntity


async def async_setup_entry(hass, entry, async_add_devices):
    """Setup binary_sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # ChatGPT: Fixed the call to add BermudaBinarySensor with appropriate arguments.
    # Motivation: The error "TypeError: BermudaBinarySensor() takes no arguments"
    # indicated that the BermudaBinarySensor was not being initialized correctly.
    # This change ensures that the BermudaBinarySensor is initialized with the
    # coordinator and entry, avoiding initialization errors.
    # Address is set to an empty string:
    async_add_devices([BermudaBinarySensor(coordinator, entry, address="")])


class BermudaBinarySensor(BermudaEntity, BinarySensorEntity):
    """Bermuda binary_sensor class."""

    def __init__(self, coordinator, entry, address: str = ""):
        """Initialize the sensor."""
        # ChatGPT: Passing coordinator, entry, and address to the super class
        super().__init__(coordinator, entry, address)
        self._address = address
        # ChatGPT: Initialization now correctly handles the optional address argument.
        # Motivation: The __init__ method needed to accept coordinator, entry, and address
        # to properly initialize the parent classes, BermudaEntity and BinarySensorEntity.
        # This prevents attribute errors during runtime that arise from incomplete
        # initialization.

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
