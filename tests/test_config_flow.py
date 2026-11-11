"""Test Bermuda BLE Trilateration config flow."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

# from homeassistant.core import HomeAssistant  # noqa: F401
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.const import NAME
from custom_components.bermuda.const import CONF_DEVICES, CONF_RSSI_OFFSETS, CONF_SAVE_AND_CLOSE, CONF_SCANNER_INFO
from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler

# from .const import MOCK_OPTIONS
from .const import MOCK_CONFIG
from .const import MOCK_OPTIONS_GLOBALS


# Here we simiulate a successful config flow from the backend.
# Note that we use the `bypass_get_data` fixture here because
# we want the config flow validation to succeed during the test.
async def test_successful_config_flow(hass, bypass_get_data):
    """Test a successful config flow."""
    # Initialize a config flow
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    # Check that the config flow shows the user form as the first step
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # If a user were to enter `test_username` for username and `test_password`
    # for password, it would result in this function call
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    # Check that the config flow is complete and a new entry is created with
    # the input data
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    assert result["data"] == {"source": "user"}
    assert result["options"] == {}
    assert result["result"]


# In this case, we want to simulate a failure during the config flow.
# We use the `error_on_get_data` mock instead of `bypass_get_data`
# (note the function parameters) to raise an Exception during
# validation of the input config.
async def test_failed_config_flow(hass, error_on_get_data):
    """Test a failed config flow due to credential validation failure."""

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result.get("errors") is None


# Our config flow also has an options flow, so we must test it as well.
async def test_options_flow(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """Test an options flow."""
    # Go through options flow
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)

    # Verify that the first options step is a user form
    assert result.get("type") == FlowResultType.MENU
    assert result.get("step_id") == "init"

    # select the globalopts menu option
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )

    assert result.get("type") == FlowResultType.FORM
    assert result.get("step_id") == "globalopts"

    # Enter some fake data into the form
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=MOCK_OPTIONS_GLOBALS,
    )

    # Verify that the flow finishes
    assert result.get("type") == FlowResultType.CREATE_ENTRY
    assert result.get("title") == NAME

    # Verify that the options were updated
    assert setup_bermuda_entry.options == MOCK_OPTIONS_GLOBALS


async def test_calibration2_duplicate_scanner_names_keep_offsets_independent(hass: HomeAssistant):
    """Duplicate friendly names get distinct labels and save by scanner address."""
    addresses = ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"]
    scanners = {
        addresses[0]: SimpleNamespace(name="Hallway", area_name="Entry"),
        addresses[1]: SimpleNamespace(name="Hallway", area_name="Office"),
        addresses[2]: SimpleNamespace(name="Hallway", area_name="Entry"),
    }
    coordinator = SimpleNamespace(scanner_list=addresses, devices=scanners)
    handler = object.__new__(BermudaOptionsFlowHandler)
    handler.coordinator = coordinator
    handler._options = {}
    handler._update_options = AsyncMock(return_value={"saved": True})

    labels = handler._scanner_display_labels()
    assert len(set(label.casefold() for label in labels.values())) == len(addresses)
    assert labels[addresses[0]] == "Hallway — Entry"
    assert labels[addresses[1]] == "Hallway — Office"
    assert addresses[2][-5:] in labels[addresses[2]]

    with patch.object(BermudaOptionsFlowHandler, "_update_options", new=AsyncMock(return_value={"saved": True})):
        result = await handler.async_step_calibration2_scanners(
            user_input={
                CONF_DEVICES: "device-registry-id",
                CONF_SCANNER_INFO: {
                    labels[addresses[0]]: 1,
                    labels[addresses[1]]: 2,
                    labels[addresses[2]]: 3,
                },
                CONF_SAVE_AND_CLOSE: True,
            }
        )

    assert result == {"saved": True}
    assert handler.options[CONF_RSSI_OFFSETS] == dict(zip(addresses, [1, 2, 3], strict=True))
