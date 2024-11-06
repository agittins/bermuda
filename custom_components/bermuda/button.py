"""Button platform for Bermuda."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_DEVICE_NEW
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.entity_registry import RegistryEntry

    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Neato button from config entry."""
    coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    created_entities = []  # list of devices we've already created entities for

    @callback
    def device_new(address: str, scanners: list[str]) -> None:
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        Make sure you have a full list of scanners ready before calling this.
        """
        if address not in created_entities:
            entities = []
            entities.append(BermudaCalibrateButton(coordinator, entry, address, hass.config_entries.flow))
            # We set update before add to False because we are being
            # call(back(ed)) from the update, so causing it to call another would be... bad.
            async_add_entities(entities, False)
            created_entities.append(address)
        else:
            # _LOGGER.debug(
            #     "Ignoring create request for existing dev_tracker %s", address
            # )
            pass
        # tell the co-ord we've done it.
        coordinator.button_created(address)

    # Connect device_new to a signal so the coordinator can call it
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))


class BermudaCalibrateButton(BermudaEntity, ButtonEntity):
    """Button for launching calibration flows."""

    # _attr_translation_key = "dismiss_alert"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: RegistryEntry,
        address: str,
        hass,
    ) -> None:
        """Initialize a calibration button entity."""
        super().__init__(coordinator, entry, address)
        self._attr_unique_id = f"{self._device.unique_id}_calibrate"
        self.hass = hass

    async def async_press(self) -> None:
        """
        Start a calibration flow.

        Launch a calibration flow relevant to the device type.
        """
        await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": "globalopts"
                # data=foo
            },
        )
