"""Diagnostics support for WLED."""

from __future__ import annotations

from typing import Any

from homeassistant.components.bluetooth.api import _get_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.core import ServiceCall

from .const import DOMAIN
from .coordinator import BermudaDataUpdateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    # We can call this with our own config_entry because the diags step doesn't
    # actually use it.

    bt_manager = _get_manager(hass)
    bt_diags = await bt_manager.async_diagnostics()

    # Param structure for service call
    call = ServiceCall(DOMAIN, "dump_devices", {"redact": True})

    data: dict[str, Any] = {
        "active_devices": f"{coordinator.count_active_devices()}/{len(coordinator.devices)}",
        "active_scanners": f"{coordinator.count_active_scanners()}/{len(coordinator.scanner_list)}",
        "devices": await coordinator.service_dump_devices(call),
        "bt_manager": coordinator.redact_data(bt_diags),
    }
    return data
