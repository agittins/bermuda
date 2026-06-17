"""Tests for per-scanner RSSI calibration: the subentry flow + the v1->v2 migration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda import async_migrate_entry
from custom_components.bermuda.const import (
    CONF_RSSI_OFFSET,
    CONF_RSSI_OFFSETS,
    CONF_SCANNER,
    DOMAIN,
    SUBENTRY_TYPE_CALIBRATION,
)


def _calibration_offsets(entry: MockConfigEntry) -> dict[str, float]:
    return {
        se.data[CONF_SCANNER]: se.data[CONF_RSSI_OFFSET]
        for se in entry.subentries.values()
        if se.subentry_type == SUBENTRY_TYPE_CALIBRATION
    }


# --------------------------------------------------------------------------- #
# v1 -> v2 migration
# --------------------------------------------------------------------------- #


async def test_migrate_v1_moves_offsets_to_subentries(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, version=1, options={CONF_RSSI_OFFSETS: {"AA:BB": 3.0, "CC:DD": -2.5}})
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert CONF_RSSI_OFFSETS not in entry.options
    assert _calibration_offsets(entry) == {"AA:BB": 3.0, "CC:DD": -2.5}


async def test_migrate_v1_no_offsets_just_bumps_version(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, version=1, options={})
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert _calibration_offsets(entry) == {}


# --------------------------------------------------------------------------- #
# Subentry flow
# --------------------------------------------------------------------------- #


async def test_subentry_add_creates_offset_and_coordinator_sees_it(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Adding a calibration subentry persists the offset and the coordinator mirrors it."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    scanner = coordinator._get_or_create_device("AA:BB:CC:DD:EE:F0")
    scanner.name_by_user = "Kitchen proxy"
    scanner.make_name()
    coordinator._scanners.add(scanner)
    addr = scanner.address

    result = await hass.config_entries.subentries.async_init(
        (setup_bermuda_entry.entry_id, SUBENTRY_TYPE_CALIBRATION), context={"source": "user"}
    )
    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_SCANNER: addr, CONF_RSSI_OFFSET: 4.5}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert _calibration_offsets(setup_bermuda_entry) == {addr: 4.5}
    # The entry reloads on subentry change; the fresh coordinator mirrors the offset.
    assert setup_bermuda_entry.runtime_data.coordinator.options[CONF_RSSI_OFFSETS] == {addr: 4.5}


async def test_subentry_aborts_when_no_scanners(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """With no scanners to calibrate, the add flow aborts cleanly."""
    result = await hass.config_entries.subentries.async_init(
        (setup_bermuda_entry.entry_id, SUBENTRY_TYPE_CALIBRATION), context={"source": "user"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_scanners"
