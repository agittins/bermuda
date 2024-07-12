"""Diagnostics support for WLED."""

from __future__ import annotations

from typing import Any

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

    # Param structure for service call
    call = ServiceCall(DOMAIN, "dump_devices", {"redact": True})

    data: dict[str, Any] = {
        "active_devices": f"{coordinator.count_active_devices()}/{len(coordinator.devices)}",
        "active_scanners": f"{coordinator.count_active_scanners()}/{len(coordinator.scanner_list)}",
        "devices": await coordinator.service_dump_devices(call),
    }
    return data
