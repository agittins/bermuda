"""Image platform for Bermuda BLE Trilateration."""

from datetime import datetime
from io import BytesIO
from random import randint

import homeassistant.util.dt as dt_util
from homeassistant.components.image import ImageEntity
from homeassistant.const import EntityCategory
from homeassistant.util import slugify
from PIL import Image, ImageDraw

from .entity import BermudaGlobalEntity

# async def async_setup_entry(
#     hass: HomeAssistant,
#     entry: config_entries.ConfigEntry,
#     async_add_entities: AddEntitiesCallback,
# ) -> None:
#     """Setup sensor platform."""
#     coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
#     async_add_entities([BermudaImage(hass, coordinator, entry, "Main floor")])


class BermudaImage(BermudaGlobalEntity, ImageEntity):
    """Image entity for Bermuda."""

    image_last_updated: datetime

    def __init__(self, hass, coordinator, config_entry, floor) -> None:
        super().__init__(coordinator, config_entry)
        ImageEntity.__init__(self, hass)
        self.image_bytes = b""
        self.floor = floor
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_image_last_updated = dt_util.utcnow()

    def _handle_coordinator_update(self) -> None:
        # On every coordinator run - update the image. Maybe when this is actually implemented
        self._attr_image_last_updated = dt_util.utcnow()
        image = Image.new("RGB", (500, 500), "white")
        draw = ImageDraw.Draw(image)
        coordinates = [(20, 20), (100, 100)]
        for x, y in coordinates:
            radius = randint(10, 40)  # noqa
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="black")

            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline="black")
        output = BytesIO()
        image.save(output, format="PNG")

        # Get the bytes of the SVG image
        self.image_bytes = output.getvalue()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return bytes of image."""
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
