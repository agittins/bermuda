"""Image platform for Bermuda BLE Trilateration."""

from datetime import datetime
from random import randint
from typing import TYPE_CHECKING

import homeassistant.util.dt as dt_util
import svgwrite
from homeassistant import config_entries
from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .const import DOMAIN
from .entity import BermudaGlobalEntity

if TYPE_CHECKING:
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Setup sensor platform."""
    coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BermudaImage(hass, coordinator, entry, "Main floor")])


class BermudaImage(BermudaGlobalEntity, ImageEntity):
    """Image entity for Bermuda."""

    _attr_content_type = "image/svg+xml"
    image_last_updated: datetime

    def __init__(self, hass, coordinator, config_entry, floor) -> None:
        super().__init__(coordinator, config_entry)
        ImageEntity.__init__(self, hass)
        self.image_bytes = b""
        self.floor = floor
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_image_last_updated = dt_util.utcnow()

    def _handle_coordinator_update(self) -> None:
        dwg = svgwrite.Drawing(size=(500, 500))
        dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill="white"))

        coordinates = [(20, 20), (100, 100)]
        for x, y in coordinates:
            radius = randint(10, 40)  # noqa: S311

            dwg.add(dwg.circle(center=(x, y), r=2, fill="black"))

            dwg.add(dwg.circle(center=(x, y), r=radius, stroke="black", fill="none"))

        # Convert SVG drawing to bytes
        self.image_bytes = dwg.tostring().encode()
        self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return bytes of SVG image."""
        return self.image_bytes

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"BERMUDA_GLOBAL_MAP_{slugify(self.floor)}"

    @property
    def name(self):
        """Gets the name of the sensor."""
        return f"{self.floor} Map"
