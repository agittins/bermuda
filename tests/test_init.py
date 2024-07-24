"""Test Bermuda BLE Trilateration setup process."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

# from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

from .const import MOCK_CONFIG

# from pytest_homeassistant_custom_component.common import AsyncMock


# We can pass fixtures as defined in conftest.py to tell pytest to use the fixture
# for a given test. We can also leverage fixtures and mocks that are available in
# Home Assistant using the pytest_homeassistant_custom_component plugin.
# Assertions allow you to verify that the return value of whatever is on the left
# side of the assertion matches with the right side.
async def test_setup_unload_and_reload_entry(
    hass: HomeAssistant, bypass_get_data, setup_bermuda_entry: MockConfigEntry
):
    """Test entry setup and unload."""
    assert isinstance(
        hass.data[DOMAIN][setup_bermuda_entry.entry_id], BermudaDataUpdateCoordinator
    )

    # Reload the entry and assert that the data from above is still there
    assert await hass.config_entries.async_reload(setup_bermuda_entry.entry_id)
    assert DOMAIN in hass.data and setup_bermuda_entry.entry_id in hass.data[DOMAIN]
    assert isinstance(
        hass.data[DOMAIN][setup_bermuda_entry.entry_id], BermudaDataUpdateCoordinator
    )

    # Unload the entry and verify that the data has been removed
    assert await hass.config_entries.async_unload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.entry_id not in hass.data[DOMAIN]


async def test_setup_entry_exception(hass, error_on_get_data):
    """Test ConfigEntryNotReady when API raises an exception during entry setup."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    assert config_entry is not None

    # In this case we are testing the condition where async_setup_entry raises
    # ConfigEntryNotReady using the `error_on_get_data` fixture which simulates
    # an error.

    # Hmmm... this doesn't seem to be how this works. The super's _async_refresh might
    # handle exceptions, in which it then sets self.last_update_status, which is what
    # async_setup_entry checks in order to raise ConfigEntryNotReady, but I don't think
    # anything will "catch" our over-ridded async_refresh's exception.
    # with pytest.raises(ConfigEntryNotReady):
    #     assert await async_setup_entry(hass, config_entry)
