"""Adds config flow for Bermuda BLE Trilateration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    NAME,
)
from .options_flow import BermudaOptionsFlowHandler

_GITHUB_URL = "https://github.com/foXaCe/bermuda"

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.config_entries import ConfigFlowResult


# from homeassistant import data_entry_flow

# from homeassistant.helpers.aiohttp_client import async_create_clientsession


class BermudaFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for bermuda."""

    VERSION = 1
    # CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        """Initialize."""
        self._errors = {}

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
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        return self.async_show_form(step_id="user", description_placeholders={"name": NAME, "github_url": _GITHUB_URL})

    async def async_step_user(self, user_input=None):
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
    def async_get_options_flow(config_entry):  # noqa: ARG004
        return BermudaOptionsFlowHandler()

    # async def _show_config_form(self, user_input):  # pylint: disable=unused-argument
    #     """Show the configuration form to edit location data."""
    #     return self.async_show_form(
    #         step_id="user",
    #         data_schema=vol.Schema(
    #             {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
    #         ),
    #         errors=self._errors,
    #     )
