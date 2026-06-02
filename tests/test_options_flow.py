"""Behaviour tests for Bermuda config flow and options flow.

These exercise the *real* flow API (via the conftest fixtures) so we cover
``custom_components/bermuda/config_flow.py`` end-to-end rather than poking at
internals. They are intentionally complementary to ``test_config_flow.py``:
here we focus on the user-step abort path, the options init menu, navigation
into each editable sub-step, and the select-devices step (form + submit +
filter-only refresh).
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import (
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    BDADDR_TYPE_OTHER,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_MAX_RADIUS,
    CONF_REF_POWER,
    DOMAIN,
    NAME,
)

from .const import MOCK_CONFIG, MOCK_OPTIONS_GLOBALS


# --------------------------------------------------------------------------- #
# BermudaFlowHandler.async_step_user
# --------------------------------------------------------------------------- #


async def test_user_step_shows_form_with_placeholders(hass: HomeAssistant):
    """The initial user step renders a form carrying name/github placeholders."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    placeholders = result.get("description_placeholders") or {}
    assert placeholders.get("name") == NAME
    assert placeholders.get("github_url", "").startswith("https://github.com/")


async def test_user_step_creates_entry(hass: HomeAssistant):
    """Submitting the user step creates the integration entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=MOCK_CONFIG)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME
    # async_step_user hardcodes data={"source": "user"} regardless of input.
    assert result["data"] == {"source": "user"}


async def test_user_step_single_instance_allowed_abort(hass: HomeAssistant):
    """A second user-initiated flow aborts because only one instance is allowed."""
    # First flow: create the one allowed entry.
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    first = await hass.config_entries.flow.async_configure(first["flow_id"], user_input=MOCK_CONFIG)
    assert first["type"] == FlowResultType.CREATE_ENTRY

    # Second flow: should abort immediately at async_step_user.
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert second["type"] == FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


# --------------------------------------------------------------------------- #
# Options flow: init menu + navigation
# --------------------------------------------------------------------------- #


async def test_options_init_shows_menu_with_all_steps(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """The options init step is a menu listing all four editable sub-steps."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    menu = set(result["menu_options"])
    assert {
        "globalopts",
        "selectdevices",
        "calibration1_global",
        "calibration2_scanners",
    } <= menu
    # The init step builds status text from coordinator counts.
    placeholders = result.get("description_placeholders") or {}
    assert "status" in placeholders


async def test_options_navigate_to_globalopts_form(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """Choosing 'globalopts' from the menu renders the global options form."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "globalopts"
    # The schema must expose at least the headline global options.
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    assert CONF_MAX_RADIUS in schema_keys
    assert CONF_REF_POWER in schema_keys


async def test_options_globalopts_writes_options_and_coordinator(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Submitting globalopts persists options on the entry and the coordinator sees them."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )
    assert result["step_id"] == "globalopts"

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=dict(MOCK_OPTIONS_GLOBALS))
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME

    await hass.async_block_till_done()

    # The options on the entry now equal what we submitted.
    assert setup_bermuda_entry.options == MOCK_OPTIONS_GLOBALS
    # And the live coordinator (runtime_data) sees the same config entry options.
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    assert coordinator.config_entry.options[CONF_REF_POWER] == MOCK_OPTIONS_GLOBALS[CONF_REF_POWER]
    assert coordinator.config_entry.options[CONF_ATTENUATION] == MOCK_OPTIONS_GLOBALS[CONF_ATTENUATION]


# --------------------------------------------------------------------------- #
# Options flow: selectdevices
# --------------------------------------------------------------------------- #


def _inject_device(coordinator, address: str, *, address_type=BDADDR_TYPE_OTHER):
    """Create a device entry inside the coordinator and force its address_type.

    We bypass discovery and just populate ``coordinator.devices`` directly via
    the coordinator's own factory, then override the few attributes the
    selectdevices step reads, so the device shows up as a selectable option.
    """
    device = coordinator._get_or_create_device(address)
    device.address_type = address_type
    # A freshly-created device already reports is_scanner == False (read-only
    # property backed by _is_scanner), which is what selectdevices needs.
    return device


async def test_options_selectdevices_shows_form_with_injected_device(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """The selectdevices step renders a form and surfaces a discovered device."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    addr = "AA:BB:CC:DD:EE:01"
    _inject_device(coordinator, addr)

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "selectdevices"
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    # The injected standard device makes the "standard_devices" selector appear,
    # alongside the always-present search field.
    assert "device_filter" in schema_keys
    assert "standard_devices" in schema_keys


async def test_options_selectdevices_submit_writes_devices(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """Submitting a device selection persists CONF_DEVICES (uppercased)."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    addr = "AA:BB:CC:DD:EE:02"
    _inject_device(coordinator, addr)

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    assert result["step_id"] == "selectdevices"

    # Provide the selection. device_filter must equal _last_device_filter ("")
    # so the handler treats this as a real submission, not a filter change.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"device_filter": "", "standard_devices": [addr.upper()]},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_bermuda_entry.options.get(CONF_DEVICES) == [addr.upper()]


async def test_options_selectdevices_filter_only_refreshes_form(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Changing only the filter re-shows the form rather than creating an entry."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    _inject_device(coordinator, "AA:BB:CC:DD:EE:03")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    assert result["step_id"] == "selectdevices"

    # A non-empty filter differs from the initial _last_device_filter (""),
    # so the handler should store it and re-render the form.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"device_filter": "nonexistentfilter", "standard_devices": []},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "selectdevices"


async def test_options_selectdevices_random_mac_recent_appears(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """A recently-seen random-resolvable MAC shows up as a selectable option."""
    from bluetooth_data_tools import monotonic_time_coarse

    coordinator = setup_bermuda_entry.runtime_data.coordinator
    dev = _inject_device(coordinator, "AA:BB:CC:DD:EE:04", address_type=BDADDR_TYPE_RANDOM_RESOLVABLE)
    # Mark as just-seen so it isn't pruned by the two-hour staleness check.
    dev.last_seen = monotonic_time_coarse()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    assert result["step_id"] == "selectdevices"
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    assert "random_devices" in schema_keys
