"""Adds config flow for Bermuda BLE Trilateration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.bluetooth import MONOTONIC_TIME
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import selector

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

from .bermuda_device import BermudaDevice
from .const import ADDR_TYPE_IBEACON
from .const import ADDR_TYPE_PRIVATE_BLE_DEVICE
from .const import BDADDR_TYPE_PRIVATE_RESOLVABLE
from .const import CONF_ATTENUATION
from .const import CONF_DEVICES
from .const import CONF_DEVTRACK_TIMEOUT
from .const import CONF_MAX_RADIUS
from .const import CONF_MAX_VELOCITY
from .const import CONF_REF_POWER
from .const import CONF_SMOOTHING_SAMPLES
from .const import CONF_UPDATE_INTERVAL
from .const import DEFAULT_ATTENUATION
from .const import DEFAULT_DEVTRACK_TIMEOUT
from .const import DEFAULT_MAX_RADIUS
from .const import DEFAULT_MAX_VELOCITY
from .const import DEFAULT_REF_POWER
from .const import DEFAULT_SMOOTHING_SAMPLES
from .const import DEFAULT_UPDATE_INTERVAL
from .const import DOMAIN
from .const import NAME

# from homeassistant import data_entry_flow

# from homeassistant.helpers.aiohttp_client import async_create_clientsession


class BermudaFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for bermuda."""

    VERSION = 1
    # CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize."""
        self._errors = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Support automatic initiation of setup through bluetooth discovery.
        (we still show a confirmation form to the user, though)
        This is triggered by discovery matchers set in manifest.json,
        and since we track any BLE advert, we're being a little cheeky by listing any.
        """
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        # Create a unique ID so that we don't get multiple discoveries appearing.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        return self.async_show_form(
            step_id="user", description_placeholders={"name": NAME}
        )

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user.

        We don't need any config for base setup, so we just activate
        (but only for one instance)
        """

        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            # create the integration!
            return self.async_create_entry(
                title=NAME, data={"source": "user"}, description=NAME
            )

        return self.async_show_form(
            step_id="user", description_placeholders={"name": NAME}
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return BermudaOptionsFlowHandler(config_entry)

    # async def _show_config_form(self, user_input):  # pylint: disable=unused-argument
    #     """Show the configuration form to edit location data."""
    #     return self.async_show_form(
    #         step_id="user",
    #         data_schema=vol.Schema(
    #             {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
    #         ),
    #         errors=self._errors,
    #     )


class BermudaOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler for bermuda."""

    def __init__(self, config_entry: ConfigEntry):
        """Initialize HACS options flow."""
        super().__init__()
        self.config_entry = config_entry
        self.options = dict(config_entry.options)
        self.coordinator: BermudaDataUpdateCoordinator
        self.devices: dict[str, BermudaDevice]

    async def async_step_init(self, user_input=None):  # pylint: disable=unused-argument
        """Manage the options."""

        self.coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id]
        self.devices = self.coordinator.devices

        messages = {}
        active_devices = self.coordinator.count_active_devices()
        active_scanners = self.coordinator.count_active_scanners()
        messages["device_count"] = f"{active_devices} active of {len(self.devices)}"
        messages["scanner_count"] = (
            f"{active_scanners} active of {len(self.coordinator.scanner_list)}"
        )
        if len(self.coordinator.scanner_list) == 0:
            messages["status"] = (
                "You need to configure some bluetooth scanners before Bermuda will have anything to work with. Any one of esphome bluetooth_proxy, Shelly bluetooth proxy or local bluetooth adaptor should get you started."
            )
        elif active_devices == 0:
            messages["status"] = (
                "No bluetooth devices are actively being reported from your scanners. You will need to solve this before Bermuda can be of much help."
            )
        else:
            messages["status"] = "Life looks good."

        # return await self.async_step_globalopts()
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "globalopts": "Global Options",
                "selectdevices": "Select Devices",
            },
            description_placeholders=messages,
        )

    async def async_step_globalopts(self, user_input=None):
        """Handle global options flow"""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        data_schema = {
            vol.Required(
                CONF_MAX_RADIUS,
                default=self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS),
            ): vol.Coerce(float),
            vol.Required(
                CONF_MAX_VELOCITY,
                default=self.options.get(CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY),
            ): vol.Coerce(float),
            vol.Required(
                CONF_DEVTRACK_TIMEOUT,
                default=self.options.get(
                    CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT
                ),
            ): vol.Coerce(int),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=self.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): vol.Coerce(float),
            vol.Required(
                CONF_SMOOTHING_SAMPLES,
                default=self.options.get(
                    CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES
                ),
            ): vol.Coerce(int),
            vol.Required(
                CONF_ATTENUATION,
                default=self.options.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
            ): vol.Coerce(float),
            vol.Required(
                CONF_REF_POWER,
                default=self.options.get(CONF_REF_POWER, DEFAULT_REF_POWER),
            ): vol.Coerce(float),
        }

        return self.async_show_form(
            step_id="globalopts", data_schema=vol.Schema(data_schema)
        )

    async def async_step_selectdevices(self, user_input=None):
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        # Grab the co-ordinator's device list so we can build a selector from it.
        self.devices = self.hass.data[DOMAIN][self.config_entry.entry_id].devices

        # Where we store the options before building the selector
        options_list = []
        options_metadevices = []  # These will be first in the list
        options_otherdevices = []  # These will be last.
        options_randoms = []  # Random MAC addresses - very last!

        for address, device in self.devices.items():
            # Iterate through all the discovered devices to build the options list

            name = device.prefname or device.name or ""

            if device.is_scanner:
                # We don't "track" scanner devices, per se
                continue
            if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                # Private BLE Devices get configured automagically, skip
                continue
            if device.address_type == ADDR_TYPE_IBEACON:
                # This is an iBeacon meta-device
                if len(device.beacon_sources) > 0:
                    source_mac = f"[{device.beacon_sources[0].upper()}]"
                else:
                    source_mac = ""

                options_metadevices.append(
                    {
                        "value": device.address.upper(),
                        "label": f"iBeacon: {device.address.upper()} {source_mac} {name if device.address.upper() != name.upper() else ""}",
                    }
                )
                continue

            if device.address_type == BDADDR_TYPE_PRIVATE_RESOLVABLE:
                # This is a random MAC, we should tag it as such

                if device.last_seen < MONOTONIC_TIME() - (60 * 60 * 2):  # two hours
                    # A random MAC we haven't seen for a while is not much use, skip
                    continue

                options_randoms.append(
                    {
                        "value": device.address.upper(),
                        "label": f"[{device.address.upper()}] {name} (Random MAC)",
                    }
                )
                continue

            # Default, unremarkable devices, just pop them in the list.
            options_otherdevices.append(
                {
                    "value": device.address.upper(),
                    "label": f"[{device.address.upper()}] {name}",
                }
            )

        # build the final list with "preferred" devices first.
        options_metadevices.sort(key=lambda item: item["label"])
        options_otherdevices.sort(key=lambda item: item["label"])
        options_randoms.sort(key=lambda item: item["label"])
        options_list.extend(options_metadevices)
        options_list.extend(options_otherdevices)
        options_list.extend(options_randoms)

        for address in self.options.get(CONF_DEVICES, []):
            # Now check for any configured devices that weren't discovered, and add them
            if not next(
                (item for item in options_list if item["value"] == address.upper()),
                False,
            ):
                options_list.append(
                    {"value": address.upper(), "label": f"[{address}] (saved)"}
                )

        data_schema = {
            vol.Optional(
                CONF_DEVICES,
                default=self.options.get(CONF_DEVICES, []),
            ): selector(
                {
                    "select": {
                        "options": options_list,
                        "multiple": True,
                    }
                }
            ),
        }

        return self.async_show_form(
            step_id="selectdevices", data_schema=vol.Schema(data_schema)
        )

    async def _update_options(self):
        """Update config entry options."""
        return self.async_create_entry(title=NAME, data=self.options)
