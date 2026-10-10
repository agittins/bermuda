"""
Custom integration to integrate Bermuda BLE Trilateration with Home Assistant.

For more details about this integration, please refer to
https://github.com/agittins/bermuda
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_registry import async_migrate_entries

from .const import _LOGGER, CONF_DEVICES, DOMAIN, PLATFORMS, STARTUP_MESSAGE
from .coordinator import BermudaDataUpdateCoordinator
from .util import mac_math_offset, mac_norm

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

type BermudaConfigEntry = ConfigEntry[BermudaData]


@dataclass
class BermudaData:
    """Holds global data for Bermuda."""

    coordinator: BermudaDataUpdateCoordinator


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: BermudaConfigEntry) -> bool:
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        _LOGGER.info(STARTUP_MESSAGE)
    coordinator = BermudaDataUpdateCoordinator(hass, entry)
    entry.runtime_data = BermudaData(coordinator)

    async def on_failure():
        _LOGGER.debug("Coordinator last update failed, rasing ConfigEntryNotReady")
        raise ConfigEntryNotReady

    try:
        await coordinator.async_refresh()
    except Exception as ex:  # noqa: BLE001
        _LOGGER.exception(ex)
        await on_failure()
    if not coordinator.last_update_success:
        await on_failure()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: BermudaConfigEntry) -> bool:
    """Migrate previous config entries."""
    _LOGGER.debug("Migrating config from version %s.%s", config_entry.version, config_entry.minor_version)
    _oldversion = f"{config_entry.version}.{config_entry.minor_version}"

    if config_entry.version == 3:  # it won't be.
        # Bogus version for now, wanted to placeholder the migrate_entries / unique_id thing.
        # If we need to manage unique_id of sensors, we probably just need
        # to manage the callback, but not worry about the hass update.
        #
        # This is lifted from the discussion at https://community.home-assistant.io/t/migrating-unique-ids/348512
        #
        # Also worth looking at https://github.com/home-assistant/core/pull/115265/files for an example
        # of migrating unique_ids from one form to another.
        #
        old_unique_id = config_entry.unique_id
        new_unique_id = mac_math_offset(old_unique_id, 3)

        @callback
        def update_unique_id(entity_entry):
            """Update unique_id of an entity."""
            return {"new_unique_id": entity_entry.unique_id.replace(old_unique_id, new_unique_id)}

        if old_unique_id != new_unique_id:
            await async_migrate_entries(hass, config_entry.entry_id, update_unique_id)
            hass.config_entries.async_update_entry(config_entry, unique_id=new_unique_id)

        return False

    if f"{config_entry.version}.{config_entry.minor_version}" != _oldversion:
        _LOGGER.info("Migrated config entry to version %s.%s", config_entry.version, config_entry.minor_version)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: BermudaConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Implements user-deletion of devices from device registry."""
    coordinator: BermudaDataUpdateCoordinator = config_entry.runtime_data.coordinator
    address = None
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN:
            # The identifier is the device's unique_id: a MAC address, an IRK,
            # or an iBeacon's uuid_major_minor - which has underscores of its
            # own, so it must be matched whole, not split.
            address = ident
    if address is not None:
        # Older versions added a per-sensor suffix ("_range") to the id. Strip
        # trailing "_part"s one at a time until something matches, so an
        # iBeacon's own underscores ("uuid_major_minor_range") survive.
        device = None
        candidate = address
        while device is None:
            device = coordinator.devices.get(mac_norm(candidate))
            if "_" not in candidate:
                break
            candidate = candidate.rsplit("_", 1)[0]
        if device is None:
            _LOGGER.warning("Failed to locate device entry for %s", address)
            return True
        device.create_sensor = False
        # calculate_data() turns create_sensor back on every cycle for any
        # address in CONF_DEVICES, so a removed device has to leave that list
        # too, or its entities come straight back.
        tracked = config_entry.options.get(CONF_DEVICES, [])
        # Compared normalised: a stored address may use "-" or "_" separators.
        target = mac_norm(device.address)
        keep = [a for a in tracked if mac_norm(a) != target]
        if len(keep) != len(tracked):
            hass.config_entries.async_update_entry(config_entry, options={**config_entry.options, CONF_DEVICES: keep})
            # The reload that follows is scheduled, not immediate: update the
            # live options too (every BermudaDevice shares this dict), or a
            # cycle in between turns create_sensor straight back on.
            coordinator.options[CONF_DEVICES] = keep
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
