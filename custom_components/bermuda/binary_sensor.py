"""Create per-area occupancy binary_sensor entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_AREAS_CHANGED
from .entity import BermudaGlobalEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load per-area occupancy binary_sensor entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_areas: list[str] = []  # list of area_ids we've already created entities for

    @callback
    def create_area_entities() -> None:
        """Create occupancy entities for any newly in-use areas."""
        entities = []
        for area_id in coordinator.areas_in_use:
            if area_id not in created_areas:
                entities.append(BermudaAreaOccupancyBinarySensor(coordinator, entry, area_id))
                created_areas.append(area_id)
        if entities:
            async_add_devices(entities, False)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_AREAS_CHANGED, create_area_entities))

    # Areas already known (eg after a reload) still need creating.
    create_area_entities()


class BermudaAreaOccupancyBinarySensor(BermudaGlobalEntity, BinarySensorEntity):
    """Occupancy for a single area, on if any occupancy-flagged device is in it."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
        area_id: str,
    ) -> None:
        super().__init__(coordinator, config_entry)
        self.area_id = area_id

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"BERMUDA_GLOBAL_OCCUPANCY_{self.area_id}"

    @property
    def name(self):
        """Return the area name, refreshed each access so renames are reflected."""
        return f"{self.coordinator.resolve_area_name(self.area_id) or 'Unknown area'} occupancy"

    @property
    def is_on(self) -> bool:
        """Return true if any occupancy-flagged device is currently in this area."""
        return any(
            dev.create_occupancy and dev.area_id == self.area_id for dev in self.coordinator.devices.values()
        )
