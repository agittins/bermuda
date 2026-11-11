"""Test Bermuda BLE Trilateration config flow."""

from __future__ import annotations

import re
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant import config_entries
from homeassistant import data_entry_flow
from homeassistant.core import HomeAssistant

# from homeassistant.core import HomeAssistant  # noqa: F401
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.config_flow import BermudaOptionsFlowHandler
from custom_components.bermuda.const import CONF_DEVICES
from custom_components.bermuda.const import CONF_SAVE_AND_CLOSE
from custom_components.bermuda.const import CONF_SCANNER_INFO
from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.const import NAME
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

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


def _fake_scanners() -> list[SimpleNamespace]:
    """
    Scanners deliberately listed in neither name nor distance order.

    hist_rssi is what each scanner has recorded for the device being calibrated:
    hist_rssi[0] is the most recent reading, and None means the scanner has not
    seen the device at all.
    """
    now = monotonic_time_coarse()
    return [
        SimpleNamespace(address="aa:bb:cc:dd:ee:03", name="Scanner C", last_seen=now, hist_rssi=[-70, -50]),
        SimpleNamespace(address="aa:bb:cc:dd:ee:01", name="scanner a", last_seen=now, hist_rssi=[-60]),
        SimpleNamespace(address="aa:bb:cc:dd:ee:04", name="Scanner D", last_seen=now, hist_rssi=[-50, -90]),
        SimpleNamespace(address="aa:bb:cc:dd:ee:05", name="Scanner E", last_seen=now, hist_rssi=[]),
        SimpleNamespace(address="aa:bb:cc:dd:ee:02", name="Scanner B", last_seen=now, hist_rssi=None),
    ]


def _fake_coordinator(scanners: list[SimpleNamespace]) -> SimpleNamespace:
    """A minimal stand-in coordinator that still uses the real scanner-summary methods."""
    coordinator = SimpleNamespace(
        devices={scanner.address: scanner for scanner in scanners},
        # Lists rather than sets so that the "unsorted" order is deterministic.
        scanner_list=[scanner.address for scanner in scanners],
        get_scanners=scanners,
    )
    for method in ("get_active_scanner_summary", "count_active_scanners", "count_active_devices"):
        setattr(coordinator, method, MethodType(getattr(BermudaDataUpdateCoordinator, method), coordinator))
    return coordinator


def test_active_scanner_summary_sorted_by_name():
    """The scanner summary (used for the status table) is sorted by name, see #758."""
    coordinator = _fake_coordinator(_fake_scanners())
    names = [scanner["name"] for scanner in coordinator.get_active_scanner_summary()]
    assert names == ["scanner a", "Scanner B", "Scanner C", "Scanner D", "Scanner E"]


async def test_options_flow_calibration2_scanner_sorting(hass: HomeAssistant):
    """
    Scanners are shown in a predictable order in the options flow, see #758.

    - the scanner status table on the options menu is sorted by name
    - the rssi offset edit list is sorted by name
    - the calibration results table is sorted by most recent distance, nearest first
    """
    scanners = _fake_scanners()
    coordinator = _fake_coordinator(scanners)
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test", title=NAME)
    config_entry.add_to_hass(hass)
    config_entry.runtime_data = SimpleNamespace(coordinator=coordinator)

    flow = BermudaOptionsFlowHandler(config_entry)
    flow.hass = hass
    flow.handler = config_entry.entry_id

    # Options menu: scanner status table sorted by name
    result = await flow.async_step_init()
    assert result.get("type") == FlowResultType.MENU
    status_names = re.findall(r"^\| (.+?)\| \[", result["description_placeholders"]["status"], re.MULTILINE)
    assert status_names == ["scanner a", "Scanner B", "Scanner C", "Scanner D", "Scanner E"]

    # Calibration 2: the editable offsets are sorted by name
    result = await flow.async_step_calibration2_scanners()
    assert result.get("type") == FlowResultType.FORM
    scanner_info_default = next(
        key.default() for key in result["data_schema"].schema if key.schema == CONF_SCANNER_INFO
    )
    assert list(scanner_info_default) == ["scanner a", "Scanner B", "Scanner C", "Scanner D", "Scanner E"]

    # Submit (without saving) to get the results table, sorted by most recent distance.
    device = SimpleNamespace(
        get_scanner=lambda address: (
            None if (hist := coordinator.devices[address].hist_rssi) is None else SimpleNamespace(hist_rssi=hist)
        )
    )
    with patch.object(BermudaOptionsFlowHandler, "_get_bermuda_device_from_registry", return_value=device):
        result = await flow.async_step_calibration2_scanners(
            user_input={
                CONF_DEVICES: "some_device_registry_id",
                CONF_SCANNER_INFO: scanner_info_default,
                CONF_SAVE_AND_CLOSE: False,
            }
        )
    assert result.get("type") == FlowResultType.FORM
    results_rows = re.findall(r"^\|([^|]+)\|", result["description_placeholders"]["suffix"], re.MULTILINE)
    # Header row first, then nearest to furthest. Scanner B has not seen the device so is not shown,
    # and Scanner E has no history so sorts last.
    assert results_rows == [" Scanner ", "---", "Scanner D", "scanner a", "Scanner C", "Scanner E"]
