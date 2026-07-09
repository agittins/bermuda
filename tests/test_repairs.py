"""Tests for the Bermuda repairs fix flows (repairs.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bermuda.const import DOMAIN, REPAIR_SCANNER_WITHOUT_AREA
from custom_components.bermuda.repairs import (
    ScannerWithoutAreaRepairFlow,
    async_create_fix_flow,
)

SCANNERLIST_TEXT = "- Kitchen proxy [aa:bb:cc:dd:ee:ff]\n"


def _bind_flow(flow: ScannerWithoutAreaRepairFlow, hass: HomeAssistant) -> None:
    """Give a directly-instantiated flow the plumbing the flow manager would set."""
    flow.hass = hass
    flow.flow_id = "test_flow_id"
    flow.handler = DOMAIN


async def test_create_fix_flow_returns_scanner_flow(hass: HomeAssistant) -> None:
    """The factory returns the scanner flow (seeded with the issue's scanner list)."""
    flow = await async_create_fix_flow(hass, REPAIR_SCANNER_WITHOUT_AREA, {"scannerlist": SCANNERLIST_TEXT})
    assert isinstance(flow, ScannerWithoutAreaRepairFlow)
    assert flow._scannerlist == SCANNERLIST_TEXT


async def test_create_fix_flow_tolerates_missing_data(hass: HomeAssistant) -> None:
    """A missing/odd issue data payload yields an empty scanner list, not a crash."""
    flow = await async_create_fix_flow(hass, REPAIR_SCANNER_WITHOUT_AREA, None)
    assert isinstance(flow, ScannerWithoutAreaRepairFlow)
    assert flow._scannerlist == ""


async def test_create_fix_flow_rejects_unknown_issue(hass: HomeAssistant) -> None:
    """An unknown issue id is a programming error and raises."""
    with pytest.raises(ValueError, match="unknown repair issue"):
        await async_create_fix_flow(hass, "not_a_real_issue", None)


async def test_flow_shows_confirm_form_with_scannerlist(hass: HomeAssistant) -> None:
    """The first step shows the confirm form, carrying the scanner list placeholder."""
    flow = ScannerWithoutAreaRepairFlow(hass, SCANNERLIST_TEXT)
    _bind_flow(flow, hass)

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"scannerlist": SCANNERLIST_TEXT}


async def test_flow_confirm_triggers_scanner_recheck(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """Submitting the confirm step forces a scanner roster re-check on the loaded entry."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    coordinator.refresh_scanners = MagicMock()

    flow = ScannerWithoutAreaRepairFlow(hass, SCANNERLIST_TEXT)
    _bind_flow(flow, hass)

    result = await flow.async_step_confirm(user_input={})

    assert result["type"] == "create_entry"
    coordinator.refresh_scanners.assert_called_once_with(force=True)


async def test_public_refresh_scanners_resets_repair_memo(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """refresh_scanners clears the area-repair memo so the issue is re-evaluated from scratch."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    coordinator._scanners_without_areas = ["stale memo"]

    coordinator.refresh_scanners(force=True)

    # The memo must have been reset (then rebuilt from the real roster, which
    # in this test env has no area-less scanners pending).
    assert coordinator._scanners_without_areas != ["stale memo"]
