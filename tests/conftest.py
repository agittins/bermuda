"""Global fixtures for Bermuda BLE Trilateration integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.const import NAME

# from .const import MOCK_OPTIONS
from .const import MOCK_CONFIG

# from custom_components.bermuda import BermudaDataUpdateCoordinator


pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def mock_bluetooth(enable_bluetooth):
    """Auto mock bluetooth."""


# This fixture enables loading custom integrations in all tests.
# Remove to enable selective use of this fixture
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations."""
    yield


# This fixture is used to prevent HomeAssistant from
# attempting to create and dismiss persistent
# notifications. These calls would fail without this
# fixture since the persistent_notification
# integration is never loaded during a test.
@pytest.fixture(name="skip_notifications", autouse=True)
def skip_notifications_fixture():
    """Skip notification calls."""
    with patch("homeassistant.components.persistent_notification.async_create"), patch(
        "homeassistant.components.persistent_notification.async_dismiss"
    ):
        yield


# This fixture, when used, will result in calls to
# async_get_data to return None. To have the call
# return a value, we would add the `return_value=<VALUE_TO_RETURN>`
# parameter to the patch call.
@pytest.fixture(name="bypass_get_data")
def bypass_get_data_fixture():
    """Skip calls to get data from API."""
    with patch("custom_components.bermuda.BermudaDataUpdateCoordinator.async_refresh"):
        yield


# In this fixture, we are forcing calls to async_get_data to raise
# an Exception. This is useful
# for exception handling.
@pytest.fixture(name="error_on_get_data")
def error_get_data_fixture():
    """Simulate error when retrieving data from API."""
    with patch(
        "custom_components.bermuda.BermudaDataUpdateCoordinator.async_refresh",
        side_effect=Exception,
    ):
        yield


# 2024-05-18: No longer required as config_flow no longer accesses the bluetooth platform,
# instead pulling data from the dataupdatecoordinator.
# # This fixture ensures that the config flow gets service info for the anticipated address
# # to go into configured_devices
# @pytest.fixture(autouse=True)
# def mock_service_info():
#     """Simulate a discovered advertisement for config_flow"""
#     with patch("custom_components.bermuda.bluetooth.async_discovered_service_info"):
#         return SERVICE_INFOS


@pytest.fixture()
async def mock_bermuda_entry(hass: HomeAssistant):
    """This creates a mock config entry"""
    config_entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME
    )
    config_entry.add_to_hass(hass)
    await hass.async_block_till_done()
    return config_entry


@pytest.fixture()
async def setup_bermuda_entry(hass: HomeAssistant):
    """This setups a entry so that it can be used."""
    config_entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME
    )
    config_entry.add_to_hass(hass)
    await async_setup_component(hass, DOMAIN, {})
    assert DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]
    return config_entry
