"""Behaviour tests for Bermuda config flow and options flow.

These exercise the *real* flow API (via the conftest fixtures) so we cover
``custom_components/bermuda/config_flow.py`` end-to-end rather than poking at
internals. They are intentionally complementary to ``test_config_flow.py``:
here we focus on the user-step abort path, the options init menu, navigation
into each editable sub-step, and the select-devices step (form + submit +
filter-only refresh).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
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
    CONF_EXCLUDE_DEVICES,
    CONF_IRK,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_SMOOTHING_SAMPLES,
    CONF_TRACK_CATEGORIES,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    NAME,
)
from custom_components.bermuda.options_flow import BermudaOptionsFlowHandler
from custom_components.bermuda.options_text import _DESCRIPTION_TEXTS

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
    """The options init step is a menu listing the editable sub-steps."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    menu = set(result["menu_options"])
    # Per-scanner calibration moved to subentries, so it is no longer a menu item.
    assert {"scan", "globalopts", "selectdevices", "area_entities"} <= menu
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
    # A single searchable devices selector replaces the old per-category selectors.
    assert "devices" in schema_keys


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

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"devices": [addr.upper()]}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_bermuda_entry.options.get(CONF_DEVICES) == [addr.upper()]


async def test_options_selectdevices_persists_categories_and_excludes(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """The selectdevices step also persists category tracking and the exclusion list."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    _inject_device(coordinator, "AA:BB:CC:DD:EE:05")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"devices": [], "track_categories": ["ibeacon", "named"], "exclude": ["AA:BB:CC:DD:EE:05"]},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_bermuda_entry.options.get(CONF_TRACK_CATEGORIES) == ["ibeacon", "named"]
    assert setup_bermuda_entry.options.get(CONF_EXCLUDE_DEVICES) == ["AA:BB:CC:DD:EE:05"]


def _devices_selector_values(result) -> set[str]:
    """Return the option values offered by the selectdevices 'devices' selector."""
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k.schema) == "devices")
    return {opt["value"] for opt in schema[key].config["options"]}


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
    assert "AA:BB:CC:DD:EE:04" in _devices_selector_values(result)


# --------------------------------------------------------------------------- #
# Options flow: scan (simple "add nearby, not-yet-tracked devices")
# --------------------------------------------------------------------------- #


def _scan_selector_values(result) -> set[str]:
    """Return the option values offered by the scan step's 'add' selector."""
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k.schema) == "add")
    return {opt["value"] for opt in schema[key].config["options"]}


async def test_options_scan_lists_only_untracked_devices(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """The scan step hides devices already associated (create_sensor) but shows fresh ones."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    tracked = _inject_device(coordinator, "AA:BB:CC:DD:EE:11")
    tracked.create_sensor = True  # already produces an entity == already associated
    fresh = _inject_device(coordinator, "AA:BB:CC:DD:EE:12")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"next_step_id": "scan"})
    assert result["step_id"] == "scan"
    offered = _scan_selector_values(result)
    assert fresh.address.upper() in offered
    assert tracked.address.upper() not in offered


async def test_options_scan_hides_devices_already_in_tracked_list(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """A device already in CONF_DEVICES is not offered again by the scan step."""
    tracked_addr = "AA:BB:CC:DD:EE:15"
    # Set the tracked list first; this reloads the entry (new coordinator).
    hass.config_entries.async_update_entry(
        setup_bermuda_entry, options={**dict(setup_bermuda_entry.options), CONF_DEVICES: [tracked_addr]}
    )
    await hass.async_block_till_done()
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    _inject_device(coordinator, tracked_addr)
    fresh = _inject_device(coordinator, "AA:BB:CC:DD:EE:16")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"next_step_id": "scan"})
    offered = _scan_selector_values(result)
    assert fresh.address.upper() in offered
    assert tracked_addr not in offered


async def test_options_scan_appends_without_dropping_existing(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Submitting the scan step adds the ticked devices to the existing tracked list."""
    existing = "AA:BB:CC:DD:EE:20"
    # Pre-track a device first; this reloads the entry (new coordinator).
    hass.config_entries.async_update_entry(
        setup_bermuda_entry, options={**dict(setup_bermuda_entry.options), CONF_DEVICES: [existing]}
    )
    await hass.async_block_till_done()
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    new = _inject_device(coordinator, "AA:BB:CC:DD:EE:21")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"next_step_id": "scan"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"add": [new.address.upper()]}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup_bermuda_entry.options.get(CONF_DEVICES) == [existing, new.address.upper()]


async def test_options_scan_refresh_re_renders_without_saving(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Submitting with 'refresh' ticked re-renders the scan form and saves nothing."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    fresh = _inject_device(coordinator, "AA:BB:CC:DD:EE:30")

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={"next_step_id": "scan"})
    # Refresh: even with a device ticked, nothing is saved and we stay on the form.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"add": [fresh.address.upper()], "refresh": True}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "scan"
    assert fresh.address.upper() in _scan_selector_values(result)
    assert setup_bermuda_entry.options.get(CONF_DEVICES, []) == []


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


# --------------------------------------------------------------------------- #
# BermudaOptionsFlowHandler._get_options_translation                          #
# --------------------------------------------------------------------------- #


def _flow_for(hass: HomeAssistant, entry: MockConfigEntry) -> BermudaOptionsFlowHandler:
    """Build a flow handler wired to a real hass/config_entry, bypassing the flow manager."""
    flow = BermudaOptionsFlowHandler()
    flow.hass = hass
    flow.handler = entry.entry_id
    return flow


async def test_get_options_translation_kwargs_without_placeholder_is_unchanged(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Passing kwargs to a description_text with no placeholders leaves it unchanged."""
    flow = _flow_for(hass, setup_bermuda_entry)
    plain = await flow._get_options_translation("description_text.scanner_table_title")  # noqa: SLF001
    formatted = await flow._get_options_translation(  # noqa: SLF001
        "description_text.scanner_table_title", unused="whatever"
    )
    assert formatted == plain == "Status of scanners:"


async def test_get_options_translation_formats_matching_placeholder(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
):
    """A kwarg matching a ``{placeholder}`` in the text is substituted in."""
    monkeypatch.setitem(_DESCRIPTION_TEXTS["en"], "greeting_test", "Hello {name}!")
    flow = _flow_for(hass, setup_bermuda_entry)
    text = await flow._get_options_translation("description_text.greeting_test", name="World")  # noqa: SLF001
    assert text == "Hello World!"


async def test_get_options_translation_suppresses_format_errors(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry, monkeypatch: pytest.MonkeyPatch
):
    """A kwarg that doesn't satisfy the text's placeholder raises internally but is suppressed."""
    monkeypatch.setitem(_DESCRIPTION_TEXTS["en"], "greeting_test", "Hello {missing}!")
    flow = _flow_for(hass, setup_bermuda_entry)
    text = await flow._get_options_translation("description_text.greeting_test", name="World")  # noqa: SLF001
    # KeyError from the unmatched placeholder is swallowed, leaving the text untouched.
    assert text == "Hello {missing}!"


# --------------------------------------------------------------------------- #
# Options flow: enrol_private                                                 #
# --------------------------------------------------------------------------- #


async def test_options_enrol_private_shows_form_with_expected_schema(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """The enrol_private step (first render) shows the IRK/name form."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "enrol_private"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "enrol_private"
    schema = result["data_schema"].schema
    schema_keys = {str(k.schema): k for k in schema}
    assert {CONF_IRK, CONF_NAME} <= set(schema_keys)
    assert schema_keys[CONF_IRK].__class__.__name__ == "Required"
    assert schema_keys[CONF_NAME].__class__.__name__ == "Optional"


async def test_options_enrol_private_success_creates_entry(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """A successful enrolment refreshes the coordinator and persists options."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "enrol_private"}
    )
    assert result["step_id"] == "enrol_private"

    with patch(
        "custom_components.bermuda.options_flow.async_enrol_private_device",
        AsyncMock(return_value=""),
    ) as mock_enrol:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_IRK: "0123456789abcdef0123456789abcdef", CONF_NAME: "My Phone"},
        )
    mock_enrol.assert_awaited_once()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_options_enrol_private_irk_error_reshows_form(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """An invalid-IRK error re-shows the form with the error keyed under CONF_IRK."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "enrol_private"}
    )

    with patch(
        "custom_components.bermuda.options_flow.async_enrol_private_device",
        AsyncMock(return_value="irk_not_valid"),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_IRK: "not-a-valid-irk", CONF_NAME: ""},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "enrol_private"
    assert result["errors"] == {CONF_IRK: "irk_not_valid"}


async def test_options_enrol_private_bluetooth_unavailable_error_is_base(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """A bluetooth-not-available error is keyed under 'base' instead of CONF_IRK."""
    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "enrol_private"}
    )

    with patch(
        "custom_components.bermuda.options_flow.async_enrol_private_device",
        AsyncMock(return_value="bluetooth_not_available"),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_IRK: "0123456789abcdef0123456789abcdef", CONF_NAME: ""},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "enrol_private"
    assert result["errors"] == {"base": "bluetooth_not_available"}
