"""
Repairs flows for Bermuda.

The scanner_without_area issue is raised by the scanner mixin whenever one or
more Bluetooth proxies have no Area assigned (Bermuda cannot place devices
without it). The fix flow doesn't assign the area itself - only the user can
decide where a proxy lives - but once they have done so, confirming the flow
forces an immediate scanner re-check instead of waiting for the next natural
refresh, so the issue clears (or reappears with the remaining proxies) right
away.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState

from .const import DOMAIN, REPAIR_SCANNER_WITHOUT_AREA

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

    from . import BermudaConfigEntry


class ScannerWithoutAreaRepairFlow(RepairsFlow):
    """Handler for the scanner_without_area issue: re-check once the user has assigned areas."""

    def __init__(self, hass: HomeAssistant, scannerlist: str) -> None:
        """Store hass (to reach the coordinator) and the offending-scanner list text."""
        self._hass = hass
        self._scannerlist = scannerlist

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the first step of a fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Wait for the user to confirm they have assigned the missing areas, then re-check."""
        if user_input is not None:
            for entry in self._hass.config_entries.async_entries(DOMAIN):
                if entry.state is ConfigEntryState.LOADED:
                    coordinator = cast("BermudaConfigEntry", entry).runtime_data.coordinator
                    # Re-inspect the scanner roster now; this re-raises the issue
                    # (with an updated proxy list) if any are still missing an area.
                    coordinator.refresh_scanners(force=True)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"scannerlist": self._scannerlist},
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a flow to fix an issue raised by this integration."""
    if issue_id == REPAIR_SCANNER_WITHOUT_AREA:
        scannerlist = str((data or {}).get("scannerlist", ""))
        return ScannerWithoutAreaRepairFlow(hass, scannerlist)
    msg = f"unknown repair issue: {issue_id}"
    raise ValueError(msg)
