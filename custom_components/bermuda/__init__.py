"""
Custom integration to integrate Bermuda BLE Trilateration with Home Assistant.

For more details about this integration, please refer to
https://github.com/agittins/bermuda
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.core import ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_registry import async_migrate_entries
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.loader import BASE_PRELOAD_PLATFORMS

from .const import _LOGGER, DOMAIN, PLATFORMS, STARTUP_MESSAGE
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


def _import_code(package: str) -> None:
    """
    Import the new code in the import executor, not the event loop.

    After an unload HA still lists "<domain>.<platform>" as loaded, so it
    would import the new modules in the event loop (and warn about it);
    once they are in sys.modules its own import is a cache hit.
    """
    module = importlib.import_module(package)
    # The new module's list, plus what HA preloads with a package (diagnostics, ...).
    for name in (*module.PLATFORMS, *BASE_PRELOAD_PLATFORMS):
        if importlib.util.find_spec(f"{package}.{name}") is not None:
            importlib.import_module(f"{package}.{name}")


def _forget_code(hass: HomeAssistant, package: str, domain: str) -> None:
    """
    Drop a package's modules so the next setup imports them from disk again.

    Home Assistant keeps its own copy of each imported integration module
    (loader.py: hass.data["components"], keyed "<domain>" and
    "<domain>.<platform>") next to Python's sys.modules; both have to go.
    """
    for cache, root in (
        (sys.modules, package),
        (hass.data.get("components", {}), domain),
        (hass.data.get("missing_platforms", {}), domain),
    ):
        for name in [n for n in cache if n == root or n.startswith(root + ".")]:
            del cache[name]
    importlib.invalidate_caches()  # or a file added since the last import may not be found


def _register_reload_code(hass: HomeAssistant) -> None:
    """Dev: bermuda.reload_code loads the Python files from disk without a restart."""

    async def _reload_code(call: ServiceCall) -> None:
        # A second call during the first one's awaits would evict the caches again and race the setup.
        if hass.data.get("bermuda_reloading"):
            msg = f"{DOMAIN} is already reloading its code"
            raise HomeAssistantError(msg)
        hass.data["bermuda_reloading"] = True
        try:
            await _reload_code_once()
        finally:
            hass.data.pop("bermuda_reloading", None)

    async def _reload_code_once() -> None:
        # Disabled and ignored entries are never set up, so they are not ours to reload.
        entries = hass.config_entries.async_entries(DOMAIN, include_ignore=False, include_disabled=False)
        for entry in entries:
            if not await hass.config_entries.async_unload(entry.entry_id):
                msg = f"{DOMAIN} did not unload; restart Home Assistant instead"
                raise HomeAssistantError(msg)
        _forget_code(hass, __name__, DOMAIN)
        try:
            # Bermuda has no async_setup: importing is the check for a syntax error.
            await hass.async_add_import_executor_job(_import_code, __name__)
        except Exception as err:
            # This handler stays registered, so the action works again once the code is fixed.
            msg = f"{DOMAIN} is unloaded: the new code did not load ({err}). Fix it and call {DOMAIN}.reload_code again"
            raise HomeAssistantError(msg) from err
        failed = [e.title for e in entries if not await hass.config_entries.async_setup(e.entry_id)]
        if failed:
            msg = f"{DOMAIN} did not set up again for {failed}; see the log"
            raise HomeAssistantError(msg)

    async_register_admin_service(hass, DOMAIN, "reload_code", _reload_code)


async def async_setup_entry(hass: HomeAssistant, entry: BermudaConfigEntry) -> bool:
    """Set up this integration using UI."""
    _register_reload_code(hass)
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
        try:
            if domain == DOMAIN:
                # the identifier should be the base device address, and
                # may have "_range" or some other per-sensor suffix.
                # The address might be a mac address, IRK or iBeacon uuid
                address = ident.split("_")[0]
        except KeyError:
            pass
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
