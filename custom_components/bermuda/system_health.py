"""
System health for Bermuda.

Surfaces a few at-a-glance counts (proxies, devices) on Home Assistant's
Settings -> System -> Repairs/System health page, so users can sanity-check that
Bermuda is seeing scanners and devices without trawling the diagnostics dump.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import CONF_DEVICES, DOMAIN

if TYPE_CHECKING:
    from homeassistant.components import system_health

    from . import BermudaConfigEntry


@callback
def async_register(
    hass: HomeAssistant,
    register: system_health.SystemHealthRegistration,
) -> None:
    """Register Bermuda's system health callbacks."""
    register.async_register_info(_system_health_info)


async def _system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Return diagnostic counts for the (single) Bermuda config entry."""
    entries: list[BermudaConfigEntry] = [
        entry for entry in hass.config_entries.async_entries(DOMAIN) if entry.state is ConfigEntryState.LOADED
    ]
    if not entries:
        return {"status": "not loaded"}

    coordinator = entries[0].runtime_data.coordinator
    return {
        "total_proxies": len(coordinator.scanner_list),
        "active_proxies": coordinator.count_active_scanners(),
        "total_devices": len(coordinator.devices),
        "visible_devices": coordinator.count_active_devices(),
        "configured_devices": len(coordinator.options.get(CONF_DEVICES, [])),
    }
