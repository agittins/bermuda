"""Bermuda Repairs Handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components.bluetooth.api import _get_manager
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow

from .const import REPAIR_ID_ADVERTS

if TYPE_CHECKING:
    from homeassistant import data_entry_flow
    from homeassistant.core import HomeAssistant


class IssueAdvertsRepairFlow(RepairsFlow):
    """Handler for an issue fixing flow."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the repair."""
        super().__init__()
        self.hass = hass
        self.manager = _get_manager(self.hass)

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        """Handle the first step of a fix flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        """Handle the confirm step of a fix flow."""
        count = len(self.manager._all_history)  # noqa: SLF001

        if user_input is None:
            # Seed the form values
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders={"count": str(count)},
            )

        # User has submitted the repair, let's go!
        deleteme = []
        for devkey, device in self.manager._all_history.items():  # noqa: SLF001
            if device.source not in self.manager._connectable_scanners | self.manager._non_connectable_scanners:  # noqa: SLF001
                deleteme.append(devkey)
        for key in deleteme:
            del self.manager._all_history[key]  # noqa: SLF001

        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create flow."""
    if issue_id == REPAIR_ID_ADVERTS:
        return IssueAdvertsRepairFlow(hass)
    return ConfirmRepairFlow()
