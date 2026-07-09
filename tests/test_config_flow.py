"""Test Bermuda BLE Trilateration config flow."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

# from homeassistant.core import HomeAssistant  # noqa: F401
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.const import NAME

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

    # Enter some fake data into the form. globalopts groups its fields into
    # collapsible sections, so the input must be nested accordingly.
    flat = dict(MOCK_OPTIONS_GLOBALS)
    nested = {
        "distance_model": {k: flat[k] for k in ("ref_power", "attenuation", "max_area_radius") if k in flat},
        "tracking": {k: flat[k] for k in ("devtracker_nothome_timeout", "update_interval") if k in flat},
        "smoothing": {k: flat[k] for k in ("smoothing_samples", "max_velocity") if k in flat},
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=nested)

    # Verify that the flow finishes
    assert result.get("type") == FlowResultType.CREATE_ENTRY
    assert result.get("title") == NAME

    # Verify that the options were updated
    assert setup_bermuda_entry.options == MOCK_OPTIONS_GLOBALS


async def test_bluetooth_discovery_aborts_without_reload_when_configured(
    hass: HomeAssistant, setup_bermuda_entry
) -> None:
    """
    Regression anchor for the HA 2026.6 reload deprecation.

    Bermuda combines an entry update listener with config-flow unique-id
    matching; that combination is only legal because the flow passes
    reload_on_update=False. If someone re-enables reload-on-update, this
    discovery must not schedule a reload of the existing entry (double-reload
    race, hard error from HA 2026.12).
    """
    from unittest.mock import MagicMock, patch

    from habluetooth import BluetoothServiceInfoBleak
    from bleak.backends.device import BLEDevice

    service_info = BluetoothServiceInfoBleak(
        name="test",
        address="aa:bb:cc:dd:ee:ff",
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=BLEDevice("aa:bb:cc:dd:ee:ff", "test", None),
        advertisement=None,
        connectable=False,
        time=0.0,
        tx_power=None,
    )

    with patch.object(hass.config_entries, "async_schedule_reload", MagicMock()) as mock_reload:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_BLUETOOTH},
            data=service_info,
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] in ("single_instance_allowed", "already_configured")
    mock_reload.assert_not_called()
