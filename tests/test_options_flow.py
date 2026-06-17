"""Behaviour tests for Bermuda config flow and options flow.

These exercise the *real* flow API (via the conftest fixtures) so we cover
``custom_components/bermuda/config_flow.py`` end-to-end rather than poking at
internals. They are intentionally complementary to ``test_config_flow.py``:
here we focus on the user-step abort path, the options init menu, navigation
into each editable sub-step, and the select-devices step (form + submit +
filter-only refresh).
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.const import (
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    BDADDR_TYPE_OTHER,
    CONF_AREA_ENTITIES,
    CONF_AREA_ENTITY_DISTANCE,
    CONF_AREA_ENTITY_DISTANCES,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
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


def _nest_globals(flat: dict) -> dict:
    """Wrap flat global options into the collapsible-section structure the form expects."""
    groups = {
        "distance_model": (CONF_REF_POWER, CONF_ATTENUATION, CONF_MAX_RADIUS),
        "tracking": (CONF_DEVTRACK_TIMEOUT, CONF_UPDATE_INTERVAL),
        "smoothing": (CONF_SMOOTHING_SAMPLES, CONF_MAX_VELOCITY),
    }
    return {section: {k: flat[k] for k in keys if k in flat} for section, keys in groups.items()}


async def test_options_navigate_to_globalopts_form(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """Choosing 'globalopts' from the menu renders the sectioned global options form."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "globalopts"
    # The schema groups the fields into collapsible sections.
    sections = {str(k.schema) for k in result["data_schema"].schema}
    assert {"distance_model", "tracking", "smoothing"} <= sections


async def test_options_globalopts_writes_options_and_coordinator(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Submitting globalopts persists options on the entry and the coordinator sees them."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )
    assert result["step_id"] == "globalopts"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=_nest_globals(MOCK_OPTIONS_GLOBALS)
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME

    await hass.async_block_till_done()

    # The sections are flattened back, so the stored options equal the flat input.
    assert setup_bermuda_entry.options == MOCK_OPTIONS_GLOBALS
    # And the live coordinator (runtime_data) sees the same config entry options.
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    assert coordinator.config_entry.options[CONF_REF_POWER] == MOCK_OPTIONS_GLOBALS[CONF_REF_POWER]
    assert coordinator.config_entry.options[CONF_ATTENUATION] == MOCK_OPTIONS_GLOBALS[CONF_ATTENUATION]


async def test_options_globalopts_schema_rejects_out_of_range(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """The globalopts schema rejects zero/negative values and out-of-range ref_power.

    These bounds stop a user from setting attenuation=0 (division by zero in
    rssi_to_metres) or smoothing/interval/velocity<=0 (which break the smoothing
    and timing loops).
    """
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "globalopts"}
    )
    schema = result["data_schema"]

    # The valid baseline still passes validation unchanged.
    schema(_nest_globals(MOCK_OPTIONS_GLOBALS))

    # Each positive-only field rejects a zero value (in whichever section it lives).
    for key in (
        CONF_ATTENUATION,
        CONF_SMOOTHING_SAMPLES,
        CONF_MAX_VELOCITY,
        CONF_UPDATE_INTERVAL,
        CONF_MAX_RADIUS,
    ):
        bad = dict(MOCK_OPTIONS_GLOBALS)
        bad[key] = 0
        with pytest.raises(vol.Invalid):
            schema(_nest_globals(bad))

    # ref_power is a dBm value: must stay within [-127, 0].
    too_high = dict(MOCK_OPTIONS_GLOBALS)
    too_high[CONF_REF_POWER] = 5
    with pytest.raises(vol.Invalid):
        schema(_nest_globals(too_high))


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


async def test_options_area_entities_two_step_flow(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """The area-entities wizard collects entities then per-entity virtual distances."""
    kitchen = ar.async_get(hass).async_create("Kitchen")
    entry = er.async_get(hass).async_get_or_create("binary_sensor", "test", "motion_aef")
    er.async_get(hass).async_update_entity(entry.entity_id, area_id=kitchen.id)

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "area_entities"}
    )
    assert result["step_id"] == "area_entities"

    # Stage 1: pick the entity and a global default distance -> advances to stage 2.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_AREA_ENTITIES: [entry.entity_id], CONF_AREA_ENTITY_DISTANCE: 0.2},
    )
    assert result["step_id"] == "area_entities_distance"

    # Stage 2: per-entity distance -> persists everything.
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={entry.entity_id: 1.5})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_bermuda_entry.options.get(CONF_AREA_ENTITIES) == [entry.entity_id]
    assert setup_bermuda_entry.options.get(CONF_AREA_ENTITY_DISTANCE) == 0.2
    assert setup_bermuda_entry.options.get(CONF_AREA_ENTITY_DISTANCES) == {entry.entity_id: 1.5}


async def test_options_area_entities_empty_skips_distance_step(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Selecting no entities persists immediately, skipping the per-entity step."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "area_entities"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_AREA_ENTITIES: [], CONF_AREA_ENTITY_DISTANCE: 0.1}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_bermuda_entry.options.get(CONF_AREA_ENTITIES) == []
