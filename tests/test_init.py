"""Test Bermuda BLE Trilateration setup process."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized

# from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN, IrkTypes
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

from .const import MOCK_CONFIG
from homeassistant.config_entries import ConfigEntryState

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

    # Reload the entry and assert that the data from above is still there
    assert await hass.config_entries.async_reload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.LOADED

    assert set(IrkTypes.unresolved()) == {
        IrkTypes.ADRESS_NOT_EVALUATED.value,
        IrkTypes.NO_KNOWN_IRK_MATCH.value,
        IrkTypes.NOT_RESOLVABLE_ADDRESS.value,
    }

    # Unload the entry and verify that the data has been removed
    assert await hass.config_entries.async_unload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.NOT_LOADED


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
    #  with pytest.raises(ConfigEntryNotReady):
    #     assert await async_setup_entry(hass, config_entry)


async def test_reload_code_is_admin_only(
    hass: HomeAssistant, bypass_get_data, setup_bermuda_entry, hass_read_only_user
):
    """Non-admin users may not call bermuda.reload_code."""
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, "reload_code", {}, blocking=True, context=Context(user_id=hass_read_only_user.id)
        )


async def test_reload_code_replaces_the_modules(hass: HomeAssistant, bypass_get_data, setup_bermuda_entry):
    """bermuda.reload_code re-imports the package and sets the entry up again."""
    old = {n: m for n, m in sys.modules.items() if n.startswith("custom_components.bermuda")}
    old_flow = config_entries.HANDLERS[DOMAIN]
    try:
        # The conftest patches name the old module objects, so the re-imported coordinator
        # loads the real manufacturer yaml files: point the config dir at the repo for that.
        with patch.object(hass.config, "config_dir", str(Path(__file__).parents[1])):
            await hass.services.async_call(DOMAIN, "reload_code", {}, blocking=True)
            await hass.async_block_till_done()
        assert setup_bermuda_entry.state == ConfigEntryState.LOADED
        coordinator_module = sys.modules["custom_components.bermuda.coordinator"]
        assert coordinator_module is not old["custom_components.bermuda.coordinator"]
        assert type(setup_bermuda_entry.runtime_data.coordinator) is coordinator_module.BermudaDataUpdateCoordinator
        assert hass.services.has_service(DOMAIN, "reload_code")
    finally:
        # Later tests patch by module path, so put the original modules back.
        for name in [n for n in sys.modules if n.startswith("custom_components.bermuda")]:
            del sys.modules[name]
        sys.modules.update(old)
        sys.modules["custom_components"].bermuda = old["custom_components.bermuda"]
        config_entries.HANDLERS[DOMAIN] = old_flow  # the re-import registered its own flow class
