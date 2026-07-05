"""Adds config flow for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    NAME,
    SUBENTRY_TYPE_CALIBRATION,
    SUBENTRY_TYPE_DEVICE,
)
from .options_flow import BermudaOptionsFlowHandler
from .subentry_flow import BermudaCalibrationSubentryFlow, BermudaDeviceSubentryFlow

_GITHUB_URL = "https://github.com/foXaCe/bermuda"

if TYPE_CHECKING:
    from typing import Any

    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.config_entries import ConfigFlowResult


class BermudaFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for bermuda."""

    VERSION = 2  # v2: per-scanner RSSI offsets moved from options into subentries

    def __init__(self) -> None:
        """Initialize."""
        self._errors: dict[str, str] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """
        Support automatic initiation of setup through bluetooth discovery.
        (we still show a confirmation form to the user, though)
        This is triggered by discovery matchers set in manifest.json,
        and since we track any BLE advert, we're being a little cheeky by listing any.
        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        # Create a unique ID so that we don't get multiple discoveries appearing.
        # reload_on_update=False: reloading is owned by the entry's update listener,
        # combining both is deprecated since HA 2026.6 (error from 2026.12).
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(reload_on_update=False)

        return self.async_show_form(step_id="user", description_placeholders={"name": NAME, "github_url": _GITHUB_URL})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """
        Handle a flow initialized by the user.

        We don't need any config for base setup, so we just activate
        (but only for one instance)
        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            # create the integration!
            return self.async_create_entry(title=NAME, data={"source": "user"}, description=NAME)

        return self.async_show_form(step_id="user", description_placeholders={"name": NAME, "github_url": _GITHUB_URL})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:  # noqa: ARG004
        return BermudaOptionsFlowHandler()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: config_entries.ConfigEntry,  # noqa: ARG003
    ) -> dict[str, type[config_entries.ConfigSubentryFlow]]:
        """Per-scanner calibration and per-device enrolment are managed as config subentries."""
        return {
            SUBENTRY_TYPE_CALIBRATION: BermudaCalibrationSubentryFlow,
            SUBENTRY_TYPE_DEVICE: BermudaDeviceSubentryFlow,
        }
