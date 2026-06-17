"""Tests for Bermuda's system_health platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda import system_health


async def test_system_health_info_when_loaded(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """When an entry is loaded, the info dict carries integer counts."""
    info = await system_health._system_health_info(hass)
    assert {
        "total_proxies",
        "active_proxies",
        "total_devices",
        "visible_devices",
        "configured_devices",
    } <= set(info)
    assert all(isinstance(v, int) for v in info.values())


async def test_system_health_info_when_not_loaded(hass: HomeAssistant):
    """With no loaded Bermuda entry, the info reports a 'not loaded' status."""
    info = await system_health._system_health_info(hass)
    assert info == {"status": "not loaded"}


def test_async_register_registers_info_callback(hass: HomeAssistant):
    """async_register wires our info callback into the system_health registration."""
    register = MagicMock()
    system_health.async_register(hass, register)
    register.async_register_info.assert_called_once_with(system_health._system_health_info)
