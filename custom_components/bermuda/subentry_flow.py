"""
Config subentry flow for per-scanner RSSI calibration offsets.

Each tracked scanner can carry an RSSI offset (dB) to compensate for radio
differences between proxies. These used to live in a single options dict; they
are now managed one-per-scanner as config subentries, which gives each scanner
its own add/edit/remove entry in the UI.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_RSSI_OFFSET, CONF_SCANNER, SUBENTRY_TYPE_CALIBRATION


def _offset_selector() -> NumberSelector:
    """A bounded RSSI offset picker (dB)."""
    return NumberSelector(
        NumberSelectorConfig(min=-30, max=30, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="dB")
    )


class BermudaCalibrationSubentryFlow(ConfigSubentryFlow):
    """Manage a single scanner's RSSI offset as a config subentry."""

    async def async_step_user(self, user_input=None) -> SubentryFlowResult:
        """Add an RSSI offset for a scanner that does not have one yet."""
        entry = self._get_entry()
        coordinator = entry.runtime_data.coordinator

        # Scanners already carrying a calibration subentry are excluded.
        used = {
            se.data.get(CONF_SCANNER)
            for se in entry.subentries.values()
            if se.subentry_type == SUBENTRY_TYPE_CALIBRATION
        }
        options = [
            SelectOptionDict(value=scanner.address, label=scanner.name or scanner.address)
            for scanner in coordinator.get_scanners
            if scanner.address not in used
        ]
        if not options:
            return self.async_abort(reason="no_scanners")

        if user_input is not None:
            scanner = user_input[CONF_SCANNER]
            label = next((opt["label"] for opt in options if opt["value"] == scanner), scanner)
            return self.async_create_entry(
                title=label,
                data={CONF_SCANNER: scanner, CONF_RSSI_OFFSET: user_input[CONF_RSSI_OFFSET]},
                unique_id=scanner,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SCANNER): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_RSSI_OFFSET, default=0.0): _offset_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_reconfigure(self, user_input=None) -> SubentryFlowResult:
        """Edit an existing scanner's RSSI offset."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                data_updates={CONF_RSSI_OFFSET: user_input[CONF_RSSI_OFFSET]},
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_RSSI_OFFSET, default=subentry.data.get(CONF_RSSI_OFFSET, 0.0)): _offset_selector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            description_placeholders={"scanner": subentry.title},
        )
