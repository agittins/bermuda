"""
Custom integration to integrate Bermuda BLE Trilateration with Home Assistant.

For more details about this integration, please refer to
https://github.com/agittins/bermuda
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import _LOGGER, DOMAIN, PLATFORMS, STARTUP_MESSAGE
from .coordinator import BermudaDataUpdateCoordinator
from .util import mac_norm

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

type BermudaConfigEntry = ConfigEntry[BermudaData]


@dataclass(slots=True)
class BermudaData:
    """Holds global data for Bermuda."""

    coordinator: BermudaDataUpdateCoordinator


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_DUMP_DEVICES = "dump_devices"
SERVICE_DUMP_DEVICES_SCHEMA = vol.Schema(
    {
        vol.Optional("addresses"): cv.string,
        vol.Optional("configured_devices"): cv.boolean,
        vol.Optional("redact"): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up Bermuda services."""

    async def async_dump_devices(call):
        """Return a dump of beacon advertisements by receiver."""
        loaded_entries = [
            entry for entry in hass.config_entries.async_entries(DOMAIN) if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded_entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")

        coordinator = loaded_entries[0].runtime_data.coordinator
        return await coordinator.service_dump_devices(call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_DEVICES,
        async_dump_devices,
        SERVICE_DUMP_DEVICES_SCHEMA,
        SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BermudaConfigEntry) -> bool:
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        _LOGGER.info(STARTUP_MESSAGE)
        # Mark the banner as shown so it is logged once per HA process, not on
        # every entry setup/reload (the integration itself uses runtime_data).
        hass.data[DOMAIN] = True
    coordinator = BermudaDataUpdateCoordinator(hass, entry)
    entry.runtime_data = BermudaData(coordinator)

    try:
        await coordinator.async_refresh()
    except Exception as err:
        _LOGGER.exception("Error during coordinator refresh")
        raise ConfigEntryNotReady from err
    if not coordinator.last_update_success:
        _LOGGER.debug("Coordinator last update failed, raising ConfigEntryNotReady")
        raise ConfigEntryNotReady

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: BermudaConfigEntry) -> bool:
    """Migrate previous config entries."""
    _LOGGER.debug("Migrating config from version %s.%s", config_entry.version, config_entry.minor_version)
    # No migrations are currently required; config entries remain at version 1.
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: BermudaConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Implements user-deletion of devices from device registry."""
    coordinator: BermudaDataUpdateCoordinator = config_entry.runtime_data.coordinator
    address = None
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN:
            # The identifier is normally the base device address. Some legacy
            # entries may have a trailing suffix like "_range"; use rsplit so
            # iBeacon ids in uuid_major_minor form stay intact.
            for candidate in (ident, ident.rsplit("_", 1)[0]):
                if (normalized := mac_norm(candidate)) in coordinator.devices:
                    address = normalized
                    break
            if address is not None:
                break
    if address is not None:
        try:
            coordinator.devices[mac_norm(address)].create_sensor = False
        except KeyError:
            _LOGGER.warning("Failed to locate device entry for %s", address)
        return True
    # Even if we don't know this address it probably just means it's stale or from
    # a previous version that used weirder names. Allow it.
    _LOGGER.warning(
        "Didn't find address for %s but allowing deletion to proceed.",
        device_entry.name,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BermudaConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unload_result := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        _LOGGER.debug("Unloaded platforms.")
    return unload_result


async def async_reload_entry(hass: HomeAssistant, entry: BermudaConfigEntry) -> None:
    """Reload config entry."""
    hass.config_entries.async_schedule_reload(entry.entry_id)
