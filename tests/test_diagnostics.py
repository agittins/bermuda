"""Tests for the diagnostics config-entry dump."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant

from custom_components.bermuda.diagnostics import async_get_config_entry_diagnostics


async def test_config_entry_diagnostics(hass: HomeAssistant):
    """Diagnostics aggregates counts, device dump and redacted manager data."""
    coordinator = MagicMock()
    coordinator.count_active_devices.return_value = 3
    coordinator.count_active_scanners.return_value = 2
    coordinator.devices = {"a": 1, "b": 2, "c": 3}
    coordinator.scanner_list = ["s1", "s2"]
    coordinator.async_get_bluetooth_manager_diagnostics = AsyncMock(return_value={"bt": "diag"})
    coordinator.service_dump_devices = AsyncMock(return_value={"dump": "data"})
    coordinator.irk_manager.async_diagnostics_no_redactions.return_value = {"irk": "data"}
    # redact_data is a passthrough for the test so we can assert the wiring.
    coordinator.redact_data = lambda data: data

    entry = MagicMock()
    entry.runtime_data.coordinator = coordinator

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["active_devices"] == "3/3"
    assert result["active_scanners"] == "2/2"
    assert result["devices"] == {"dump": "data"}
    assert result["bt_manager"] == {"bt": "diag"}
    assert result["irk_manager"] == {"irk": "data"}
    coordinator.async_get_bluetooth_manager_diagnostics.assert_awaited_once()
    coordinator.service_dump_devices.assert_awaited_once()
