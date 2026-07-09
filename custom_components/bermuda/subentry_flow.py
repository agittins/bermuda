"""
Config subentry flow for per-scanner RSSI calibration offsets.

Each tracked scanner can carry an RSSI offset (dB) to compensate for radio
differences between proxies. These used to live in a single options dict; they
are now managed one-per-scanner as config subentries, which gives each scanner
its own add/edit/remove entry in the UI.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_ADDRESS,
    CONF_DEVTRACK_TIMEOUT,
    CONF_REF_POWER,
    CONF_RSSI_OFFSET,
    CONF_SCANNER,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_REF_POWER,
    OPT_MAX_DEVTRACK_TIMEOUT,
    OPT_MIN_DEVTRACK_TIMEOUT,
    OPT_REF_POWER_MAX,
    OPT_REF_POWER_MIN,
    OPT_RSSI_OFFSET_MAX,
    OPT_RSSI_OFFSET_MIN,
    SUBENTRY_TYPE_CALIBRATION,
    SUBENTRY_TYPE_DEVICE,
)


def _offset_selector() -> NumberSelector:
    """A bounded RSSI offset picker (dB)."""
    return NumberSelector(
        NumberSelectorConfig(
            min=OPT_RSSI_OFFSET_MIN,
            max=OPT_RSSI_OFFSET_MAX,
            step=0.1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="dB",
        )
    )


class BermudaCalibrationSubentryFlow(ConfigSubentryFlow):
    """Manage a single scanner's RSSI offset as a config subentry."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
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

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
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


def _ref_power_selector() -> NumberSelector:
    """A bounded reference-power (rssi@1m) picker (dBm)."""
    return NumberSelector(
        NumberSelectorConfig(
            min=OPT_REF_POWER_MIN,
            max=OPT_REF_POWER_MAX,
            step=0.1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="dBm",
        )
    )


def _timeout_selector() -> NumberSelector:
    """A per-device away timeout picker (seconds)."""
    return NumberSelector(
        NumberSelectorConfig(
            min=OPT_MIN_DEVTRACK_TIMEOUT,
            max=OPT_MAX_DEVTRACK_TIMEOUT,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


class BermudaDeviceSubentryFlow(ConfigSubentryFlow):
    """Per-device enrollment: name + reference power + away timeout as a config subentry."""

    @staticmethod
    def _fields(
        name: str = "", ref_power: float = DEFAULT_REF_POWER, timeout: int = DEFAULT_DEVTRACK_TIMEOUT
    ) -> dict[vol.Marker, Any]:
        return {
            vol.Optional(CONF_NAME, default=name): TextSelector(),
            vol.Required(CONF_REF_POWER, default=ref_power): _ref_power_selector(),
            vol.Required(CONF_DEVTRACK_TIMEOUT, default=timeout): _timeout_selector(),
        }

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Enrol a discovered device that is not yet enrolled."""
        entry = self._get_entry()
        coordinator = entry.runtime_data.coordinator

        used = {
            se.data.get(CONF_ADDRESS) for se in entry.subentries.values() if se.subentry_type == SUBENTRY_TYPE_DEVICE
        }
        options = sorted(
            (
                SelectOptionDict(value=dev.address.upper(), label=f"{dev.name} ({dev.address.upper()})")
                for dev in coordinator.devices.values()
                if not dev.is_scanner and dev.address.upper() not in used
            ),
            key=lambda opt: opt["label"],
        )
        if not options:
            return self.async_abort(reason="no_devices")

        if user_input is not None:
            addr = user_input[CONF_ADDRESS]
            title = user_input.get(CONF_NAME) or next((opt["label"] for opt in options if opt["value"] == addr), addr)
            return self.async_create_entry(
                title=title,
                data={
                    CONF_ADDRESS: addr,
                    CONF_NAME: user_input.get(CONF_NAME, ""),
                    CONF_REF_POWER: user_input[CONF_REF_POWER],
                    CONF_DEVTRACK_TIMEOUT: user_input[CONF_DEVTRACK_TIMEOUT],
                },
                unique_id=addr,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
                **self._fields(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=data_schema)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Edit an enrolled device's name, reference power and timeout."""
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input.get(CONF_NAME) or subentry.title,
                data_updates={
                    CONF_NAME: user_input.get(CONF_NAME, ""),
                    CONF_REF_POWER: user_input[CONF_REF_POWER],
                    CONF_DEVTRACK_TIMEOUT: user_input[CONF_DEVTRACK_TIMEOUT],
                },
            )

        data = subentry.data
        data_schema = vol.Schema(
            self._fields(
                name=data.get(CONF_NAME, ""),
                ref_power=data.get(CONF_REF_POWER, DEFAULT_REF_POWER),
                timeout=data.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT),
            )
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            description_placeholders={"device": subentry.title},
        )
