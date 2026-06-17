"""
Options (and calibration) flow for Bermuda BLE Trilateration.

Split out of config_flow.py: holds the large options/calibration wizard
(BermudaOptionsFlowHandler) and its inline UI-text table. Imports are broad
and pruned by ruff.
"""

from __future__ import annotations

import contextlib
from copy import deepcopy
from typing import TYPE_CHECKING

import voluptuous as vol
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.config_entries import OptionsFlow
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    DeviceSelector,
    DeviceSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    ObjectSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.helpers.translation import async_get_translations

from .const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    CONF_AREA_ENTITIES,
    CONF_AREA_ENTITY_DISTANCE,
    CONF_AREA_ENTITY_DISTANCES,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SAVE_AND_CLOSE,
    CONF_SCANNER_INFO,
    CONF_SCANNERS,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_AREA_ENTITY_DISTANCE,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DISTANCE_INFINITE,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
    NAME,
    OPT_MIN_ATTENUATION,
    OPT_MIN_DEVTRACK_TIMEOUT,
    OPT_MIN_MAX_RADIUS,
    OPT_MIN_MAX_VELOCITY,
    OPT_MIN_SMOOTHING_SAMPLES,
    OPT_MIN_UPDATE_INTERVAL,
    OPT_REF_POWER_MAX,
    OPT_REF_POWER_MIN,
)
from .options_text import _DESCRIPTION_TEXTS
from .util import mac_redact, rssi_to_metres

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator


class BermudaOptionsFlowHandler(OptionsFlow):
    """Config flow options handler for bermuda."""

    def __init__(self) -> None:
        """Initialize Bermuda options flow."""
        self.coordinator: BermudaDataUpdateCoordinator
        self.devices: dict[str, BermudaDevice]
        self._last_ref_power = None
        self._last_device = None
        self._last_scanner = None
        self._last_attenuation = None
        self._last_scanner_info = None
        self._last_device_filter = ""
        self._translations_cache: dict[str, str] | None = None
        self._options: dict | None = None

    @property
    def options(self) -> dict:
        """Return a mutable working copy of the config entry options."""
        if self._options is None:
            self._options = deepcopy(dict(self.config_entry.options))
        return self._options

    async def _get_options_translation(self, key: str, **kwargs: str) -> str:
        """
        Get a translated string from options translations.

        Keys starting with "description_text." are resolved from the inline
        _DESCRIPTION_TEXTS dict (not part of HA's translation schema).
        All other keys are fetched via async_get_translations.
        """
        if key.startswith("description_text."):
            sub_key = key.removeprefix("description_text.")
            lang = self.hass.config.language
            texts = _DESCRIPTION_TEXTS.get(lang, _DESCRIPTION_TEXTS["en"])
            text = texts.get(sub_key, _DESCRIPTION_TEXTS["en"].get(sub_key, ""))
        else:
            if self._translations_cache is None:
                self._translations_cache = await async_get_translations(
                    self.hass, self.hass.config.language, "options", integrations=[DOMAIN]
                )
            full_key = f"component.{DOMAIN}.options.{key}"
            text = self._translations_cache.get(full_key, "")
        if kwargs:
            with contextlib.suppress(KeyError, IndexError, ValueError):
                text = text.format(**kwargs)
        return text

    async def async_step_init(self, user_input=None):  # pylint: disable=unused-argument
        """Manage the options."""
        self.coordinator = self.config_entry.runtime_data.coordinator
        self.devices = self.coordinator.devices

        messages = {}
        active_devices = self.coordinator.count_active_devices()
        active_scanners = self.coordinator.count_active_scanners()

        messages["device_counter_active"] = f"{active_devices}"
        messages["device_counter_devices"] = f"{len(self.devices)}"
        messages["scanner_counter_active"] = f"{active_scanners}"
        messages["scanner_counter_scanners"] = f"{len(self.coordinator.scanner_list)}"

        if len(self.coordinator.scanner_list) == 0:
            messages["status"] = await self._get_options_translation("error.no_scanners")
        elif active_devices == 0:
            messages["status"] = await self._get_options_translation("error.no_devices")
        else:
            messages["status"] = await self._get_options_translation("error.some_active")

        # Build a markdown table of scanners so the user can see what's up.
        t_title = await self._get_options_translation("description_text.scanner_table_title")
        t_col_scanner = await self._get_options_translation("description_text.scanner_table_col_scanner")
        t_col_address = await self._get_options_translation("description_text.scanner_table_col_address")
        t_col_last_ad = await self._get_options_translation("description_text.scanner_table_col_last_ad")
        t_seconds_ago = await self._get_options_translation("description_text.seconds_ago")
        scanner_table = f"\n\n{t_title}\n\n|{t_col_scanner}|{t_col_address}|{t_col_last_ad}|\n|---|---|---:|\n"
        # Use emoji to indicate if age is "good"
        for scanner in self.coordinator.get_active_scanner_summary():
            age = int(scanner.get("last_stamp_age", 999))
            if age < 2:
                status = '<ha-icon icon="mdi:check-circle-outline"></ha-icon>'
            elif age < 10:
                status = '<ha-icon icon="mdi:alert-outline"></ha-icon>'
            else:
                status = '<ha-icon icon="mdi:skull-crossbones"></ha-icon>'
            # Remove centre octets from mac for condensed, privatised display
            shortmac = mac_redact(scanner.get("address", "ERR"))
            scanner_table += (
                f"| {scanner.get('name', 'NAME_ERR')}| [{shortmac}]"
                f"| {status} {(scanner.get('last_stamp_age', DISTANCE_INFINITE)):.2f} {t_seconds_ago}|\n"
            )
        messages["status"] += scanner_table

        # return await self.async_step_globalopts()
        # Menu labels come from translations (options.step.init.menu_options.*),
        # so every menu entry is localised like the rest of the flow.
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "globalopts",
                "selectdevices",
                "area_entities",
                "calibration1_global",
                "calibration2_scanners",
            ],
            description_placeholders=messages,
        )

    async def async_step_globalopts(self, user_input=None):
        """Handle global options flow."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        data_schema = {
            vol.Required(
                CONF_MAX_RADIUS,
                default=self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_MIN_MAX_RADIUS)),
            vol.Required(
                CONF_MAX_VELOCITY,
                default=self.options.get(CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_MIN_MAX_VELOCITY)),
            vol.Required(
                CONF_DEVTRACK_TIMEOUT,
                default=self.options.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT),
            ): vol.All(vol.Coerce(int), vol.Range(min=OPT_MIN_DEVTRACK_TIMEOUT)),
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=self.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_MIN_UPDATE_INTERVAL)),
            vol.Required(
                CONF_SMOOTHING_SAMPLES,
                default=self.options.get(CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES),
            ): vol.All(vol.Coerce(int), vol.Range(min=OPT_MIN_SMOOTHING_SAMPLES)),
            vol.Required(
                CONF_ATTENUATION,
                default=self.options.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_MIN_ATTENUATION)),
            vol.Required(
                CONF_REF_POWER,
                default=self.options.get(CONF_REF_POWER, DEFAULT_REF_POWER),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_REF_POWER_MIN, max=OPT_REF_POWER_MAX)),
        }

        return self.async_show_form(step_id="globalopts", data_schema=vol.Schema(data_schema))

    async def async_step_selectdevices(self, user_input=None):
        """Handle a flow initialized by the user."""
        device_selector_keys = ("ibeacon_devices", "standard_devices", "random_devices")
        if user_input is not None:
            submitted_filter = user_input.get("device_filter", "").lower()
            # Check if user submitted device selections (not just filtering)
            selected_devices = []
            for selector_key in device_selector_keys:
                selected_devices.extend(user_input.get(selector_key, []))

            if submitted_filter != self._last_device_filter:
                self._last_device_filter = submitted_filter
            elif any(selector_key in user_input for selector_key in device_selector_keys):
                self.options[CONF_DEVICES] = selected_devices
                return await self._update_options()

        # Grab the co-ordinator's device list so we can build a selector from it.
        self.devices = self.config_entry.runtime_data.coordinator.devices

        # Get search text if it exists
        filter_text = self._last_device_filter

        # Where we store the options before building the selector
        options_metadevices = []  # These will be first in the list
        options_otherdevices = []  # These will be last.
        options_randoms = []  # Random MAC addresses - very last!

        for device in self.devices.values():
            # Iterate through all the discovered devices to build the options list

            name = device.name

            # Build additional info for better searchability
            manufacturer_info = f" - {device.manufacturer}" if device.manufacturer else ""
            rssi_info = f" RSSI:{device.area_rssi:.0f}dBm" if device.area_rssi is not None else ""

            if device.is_scanner:
                # We don't "track" scanner devices, per se
                continue
            if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                # Private BLE Devices get configured automagically, skip
                continue

            # Build the full label for text filtering
            full_label = f"{device.address.upper()} {name} {device.manufacturer or ''}"

            # Apply text search filter if present
            if filter_text and filter_text not in full_label.lower():
                continue

            if device.address_type == ADDR_TYPE_IBEACON:
                # This is an iBeacon meta-device
                if len(device.metadevice_sources) > 0:
                    source_mac = f"[{device.metadevice_sources[0].upper()}]"
                else:
                    source_mac = ""

                device_name = f" {name}" if device.address.upper() != name.upper() else ""

                options_metadevices.append(
                    SelectOptionDict(
                        value=device.address.upper(),
                        label=(
                            f"iBeacon: {device.address.upper()} {source_mac}{device_name}{manufacturer_info}{rssi_info}"
                        ),
                    )
                )
                continue

            if device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE:
                # This is a random MAC, we should tag it as such

                if device.last_seen < monotonic_time_coarse() - (60 * 60 * 2):  # two hours
                    # A random MAC we haven't seen for a while is not much use, skip
                    continue

                options_randoms.append(
                    SelectOptionDict(
                        value=device.address.upper(),
                        label=f"[{device.address.upper()}] {name} (Random MAC){manufacturer_info}{rssi_info}",
                    )
                )
                continue

            # Default, unremarkable devices, just pop them in the list.
            options_otherdevices.append(
                SelectOptionDict(
                    value=device.address.upper(),
                    label=f"[{device.address.upper()}] {name}{manufacturer_info}{rssi_info}",
                )
            )

        # build the final list with "preferred" devices first.
        options_metadevices.sort(key=lambda item: item["label"])
        options_otherdevices.sort(key=lambda item: item["label"])
        options_randoms.sort(key=lambda item: item["label"])

        # Apply pagination limits (50 devices per category max without filter)
        max_devices_per_category = 50
        show_pagination_warning = False

        if not filter_text:
            if len(options_metadevices) > max_devices_per_category:
                options_metadevices = options_metadevices[:max_devices_per_category]
                show_pagination_warning = True
            if len(options_otherdevices) > max_devices_per_category:
                options_otherdevices = options_otherdevices[:max_devices_per_category]
                show_pagination_warning = True
            if len(options_randoms) > max_devices_per_category:
                options_randoms = options_randoms[:max_devices_per_category]
                show_pagination_warning = True

        # Build description with device counts and filter help
        description_text = (
            await self._get_options_translation(
                "description_text.found_devices",
                ibeacon_count=str(len(options_metadevices)),
                standard_count=str(len(options_otherdevices)),
                random_count=str(len(options_randoms)),
            )
            + "\n\n"
        )

        if show_pagination_warning:
            description_text += (
                await self._get_options_translation(
                    "description_text.pagination_warning",
                    max_count=str(max_devices_per_category),
                )
                + "\n\n"
            )

        if filter_text:
            description_text += (
                await self._get_options_translation(
                    "description_text.filter_active",
                    filter_text=filter_text,
                )
                + "\n\n"
            )

        # Configured devices that are no longer being discovered must still be
        # offered (labelled "(saved)"), otherwise saving the form would silently
        # drop them from CONF_DEVICES. Add them to the standard-devices selector.
        _discovered_values = {
            opt["value"] for group in (options_metadevices, options_otherdevices, options_randoms) for opt in group
        }
        for address in self.options.get(CONF_DEVICES, []):
            # Guard against a malformed (e.g. hand-edited) config carrying a non-string
            # entry, which would crash the whole options flow on .upper().
            if isinstance(address, str) and address.upper() not in _discovered_values:
                options_otherdevices.append(SelectOptionDict(value=address.upper(), label=f"[{address}] (saved)"))

        # Build the form schema with search field
        data_schema = {
            vol.Optional(
                "device_filter",
                default=filter_text,
                description={"suggested_value": filter_text},
            ): TextSelector(TextSelectorConfig(type="search")),
        }

        # Add grouped selectors by device type
        if options_metadevices:
            data_schema[
                vol.Optional(
                    "ibeacon_devices",
                    default=[
                        d
                        for d in self.options.get(CONF_DEVICES, [])
                        if any(opt["value"] == d.upper() for opt in options_metadevices)
                    ],
                )
            ] = SelectSelector(SelectSelectorConfig(options=options_metadevices, multiple=True))

        if options_otherdevices:
            data_schema[
                vol.Optional(
                    "standard_devices",
                    default=[
                        d
                        for d in self.options.get(CONF_DEVICES, [])
                        if any(opt["value"] == d.upper() for opt in options_otherdevices)
                    ],
                )
            ] = SelectSelector(SelectSelectorConfig(options=options_otherdevices, multiple=True))

        if options_randoms:
            data_schema[
                vol.Optional(
                    "random_devices",
                    default=[
                        d
                        for d in self.options.get(CONF_DEVICES, [])
                        if any(opt["value"] == d.upper() for opt in options_randoms)
                    ],
                )
            ] = SelectSelector(SelectSelectorConfig(options=options_randoms, multiple=True))

        return self.async_show_form(
            step_id="selectdevices",
            data_schema=vol.Schema(data_schema),
            description_placeholders={"filter_help": description_text},
        )

    async def async_step_area_entities(self, user_input=None):
        """Select presence entities and the global default virtual distance."""
        if user_input is not None:
            self.options[CONF_AREA_ENTITIES] = user_input.get(CONF_AREA_ENTITIES, [])
            self.options[CONF_AREA_ENTITY_DISTANCE] = user_input.get(
                CONF_AREA_ENTITY_DISTANCE, DEFAULT_AREA_ENTITY_DISTANCE
            )
            if self.options[CONF_AREA_ENTITIES]:
                return await self.async_step_area_entities_distance()
            return await self._update_options()

        data_schema = {
            vol.Optional(
                CONF_AREA_ENTITIES,
                default=self.options.get(CONF_AREA_ENTITIES, []),
            ): EntitySelector(EntitySelectorConfig(multiple=True)),
            vol.Optional(
                CONF_AREA_ENTITY_DISTANCE,
                default=self.options.get(CONF_AREA_ENTITY_DISTANCE, DEFAULT_AREA_ENTITY_DISTANCE),
            ): NumberSelector(
                NumberSelectorConfig(min=0.01, max=999, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="m")
            ),
        }
        return self.async_show_form(step_id="area_entities", data_schema=vol.Schema(data_schema))

    async def async_step_area_entities_distance(self, user_input=None):
        """Set the per-entity virtual distance, grouped by area for readability."""
        entities = self.options.get(CONF_AREA_ENTITIES, [])
        if user_input is not None:
            distances: dict[str, float] = {}
            for entity_id in entities:
                value = user_input.get(entity_id)
                if value is not None:
                    distances[entity_id] = float(value)
            self.options[CONF_AREA_ENTITY_DISTANCES] = distances
            return await self._update_options()

        existing = self.options.get(CONF_AREA_ENTITY_DISTANCES, {})
        default_distance = self.options.get(CONF_AREA_ENTITY_DISTANCE, DEFAULT_AREA_ENTITY_DISTANCE)
        manager = self.coordinator.area_entity_manager

        # Group the entities by their resolved area, for a readable form.
        area_groups: dict[str, list[str]] = {}
        for entity_id in entities:
            _area_id, area_name = manager.resolve_entity_area(entity_id)
            area_groups.setdefault(area_name or "(no area)", []).append(entity_id)

        data_schema = {}
        for area_name in sorted(area_groups):
            for entity_id in sorted(area_groups[area_name]):
                data_schema[vol.Optional(entity_id, default=existing.get(entity_id, default_distance))] = (
                    NumberSelector(
                        NumberSelectorConfig(
                            min=0.01, max=999, step=0.1, mode=NumberSelectorMode.BOX, unit_of_measurement="m"
                        )
                    )
                )

        summary = "\n".join(
            f"**{area_name}**: " + ", ".join(e.split(".")[-1] for e in sorted(area_groups[area_name]))
            for area_name in sorted(area_groups)
        )
        return self.async_show_form(
            step_id="area_entities_distance",
            data_schema=vol.Schema(data_schema),
            description_placeholders={"area_summary": summary},
        )

    async def async_step_calibration1_global(self, user_input=None):
        # Workaround: HTML tags in translations break placeholder substitution.
        # Injecting them as placeholders avoids the parsing issue.
        _ugly_token_hack = {
            "details": "<details>",
            "details_end": "</details>",
            "summary": "<summary>",
            "summary_end": "</summary>",
        }

        if user_input is not None:
            if user_input[CONF_SAVE_AND_CLOSE]:
                # Update the running options (this propagates to coordinator etc)
                self.options.update(
                    {
                        CONF_ATTENUATION: user_input[CONF_ATTENUATION],
                        CONF_REF_POWER: user_input[CONF_REF_POWER],
                    }
                )
                # Ideally, we'd like to just save out the config entry and return to the main menu.
                # Unfortunately, doing so seems to break the chosen device (for at least 15 seconds or so)
                # until it gets re-invigorated. My guess is that the link between coordinator and the
                # sensor entity might be getting broken, but not entirely sure.
                # For now disabling the return-to-menu and instead we finish out the flow.

                # Previous block for returning to menu:
                # # Let's update the options - but we don't want to call create entry as that will close the flow.
                # # This will save out the config entry:
                # self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
                # Reset last device so that the next step doesn't think it exists.
                # self._last_device = None
                # return await self.async_step_init()

                # Current block for finishing the flow:
                return await self._update_options()

            self._last_ref_power = user_input[CONF_REF_POWER]
            self._last_attenuation = user_input[CONF_ATTENUATION]
            self._last_device = user_input[CONF_DEVICES]
            self._last_scanner = user_input[CONF_SCANNERS]

        # Use SelectSelector since scanners don't have device entries yet
        scanner_options = [
            SelectOptionDict(
                value=scanner,
                label=self.coordinator.devices[scanner].name if scanner in self.coordinator.devices else scanner,
            )
            for scanner in self.coordinator.scanner_list
        ]
        data_schema = {
            vol.Required(
                CONF_DEVICES,
                default=self._last_device if self._last_device is not None else vol.UNDEFINED,
            ): DeviceSelector(DeviceSelectorConfig(integration=DOMAIN)),
            vol.Required(
                CONF_SCANNERS,
                default=self._last_scanner if self._last_scanner is not None else vol.UNDEFINED,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=scanner_options,
                    multiple=False,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_REF_POWER,
                default=self._last_ref_power
                if self._last_ref_power is not None
                else self.options.get(CONF_REF_POWER, DEFAULT_REF_POWER),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_REF_POWER_MIN, max=OPT_REF_POWER_MAX)),
            vol.Required(
                CONF_ATTENUATION,
                default=self._last_attenuation
                if self._last_attenuation is not None
                else self.options.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
            ): vol.All(vol.Coerce(float), vol.Range(min=OPT_MIN_ATTENUATION)),
            vol.Optional(CONF_SAVE_AND_CLOSE, default=False): vol.Coerce(bool),
        }
        calibration_hint = await self._get_options_translation("description_text.calibration_submit_hint")
        if user_input is None:
            return self.async_show_form(
                step_id="calibration1_global",
                data_schema=vol.Schema(data_schema),
                description_placeholders=_ugly_token_hack | {"suffix": calibration_hint},
            )
        results_str = ""
        device = self._get_bermuda_device_from_registry(user_input[CONF_DEVICES])
        if device is not None:
            scanner = device.get_scanner(user_input[CONF_SCANNERS])
            if scanner is None:
                return self.async_show_form(
                    step_id="calibration1_global",
                    errors={"base": "err_scanner_no_record"},
                    data_schema=vol.Schema(data_schema),
                    description_placeholders=_ugly_token_hack | {"suffix": calibration_hint},
                )

            distances = [
                rssi_to_metres(historical_rssi, self._last_ref_power, self._last_attenuation)
                for historical_rssi in scanner.hist_rssi
            ]

            # Build a markdown table showing distance and rssi history for the
            # selected device / scanner combination
            t_estimate = await self._get_options_translation("description_text.calibration_row_estimate")
            t_rssi = await self._get_options_translation("description_text.calibration_row_rssi")
            results_str = f"| {device.name} |"
            # Limit the number of columns to what's available up to a max of 5.
            cols = min(5, len(distances), len(scanner.hist_rssi))
            for i in range(cols):
                results_str += f" {i} |"
            results_str += "\n|---|"
            for i in range(cols):  # noqa for unused var i
                results_str += "---:|"

            results_str += f"\n| {t_estimate} |"
            for i in range(cols):
                results_str += f" `{distances[i]:>5.2f}`|"
            results_str += f"\n| {t_rssi} |"
            for i in range(cols):
                results_str += f" `{scanner.hist_rssi[i]:>5}`|"
            results_str += "\n"

        calibration_intro = await self._get_options_translation(
            "description_text.calibration_results_intro",
            ref_power=str(self._last_ref_power),
            attenuation=str(self._last_attenuation),
        )
        return self.async_show_form(
            step_id="calibration1_global",
            data_schema=vol.Schema(data_schema),
            description_placeholders=_ugly_token_hack
            | {
                "suffix": f"{calibration_intro}\n\n{results_str}",
            },
        )

    async def async_step_calibration2_scanners(self, user_input=None):
        """
        Per-scanner calibration of rssi_offset.

        Prompts the user to select a configured device, then adjust the offset
        so that the estimated distance to each proxy is correct (typically by
        placing device at 1m from each proxy in turn).

        Distances are recalculated and displayed each time the user presses
        Submit, and they check "Save and Close" to save the config.
        """
        if user_input is not None:
            if user_input[CONF_SAVE_AND_CLOSE]:
                # Convert the name-based dict to use MAC addresses.
                # CONF_SCANNER_INFO is a free-form ObjectSelector, so a key may be
                # missing/renamed and a value may be non-numeric: default safely.
                scanner_info = user_input.get(CONF_SCANNER_INFO, {})
                rssi_offset_by_address = {}
                for address in self.coordinator.scanner_list:
                    scanner_name = self.coordinator.devices[address].name
                    try:
                        offset = int(float(scanner_info.get(scanner_name, 0)))
                    except (TypeError, ValueError):
                        offset = 0
                    # Clip to keep in sensible range, fixes #497
                    rssi_offset_by_address[address] = max(min(offset, 127), -127)

                self.options.update({CONF_RSSI_OFFSETS: rssi_offset_by_address})
                # Per previous step, returning elsewhere in the flow after updating the entry doesn't
                # seem to work, so we'll just save and close the flow.
                # # Let's update the options - but we don't want to call create entry as that will close the flow.
                # self.hass.config_entries.async_update_entry(self.config_entry, options=self.options)
                # # Reset last device so that the next step doesn't think it exists.
                # self._last_device = None
                # self._last_scanner_info = None
                # return await self.async_step_init()

                # Save the config entry and close the flow.
                return await self._update_options()

            # It's a refresh, basically...
            self._last_scanner_info = user_input[CONF_SCANNER_INFO]
            self._last_device = user_input[CONF_DEVICES]

        saved_rssi_offsets = self.options.get(CONF_RSSI_OFFSETS, {})
        rssi_offset_dict = {}

        for scanner in self.coordinator.scanner_list:
            scanner_name = self.coordinator.devices[scanner].name
            rssi_offset_dict[scanner_name] = saved_rssi_offsets.get(scanner, 0)
        data_schema = {
            vol.Required(
                CONF_DEVICES,
                default=self._last_device if self._last_device is not None else vol.UNDEFINED,
            ): DeviceSelector(DeviceSelectorConfig(integration=DOMAIN)),
            vol.Required(
                CONF_SCANNER_INFO,
                default=rssi_offset_dict if not self._last_scanner_info else self._last_scanner_info,
            ): ObjectSelector(),
            vol.Optional(CONF_SAVE_AND_CLOSE, default=False): vol.Coerce(bool),
        }
        if user_input is None:
            return self.async_show_form(
                step_id="calibration2_scanners",
                data_schema=vol.Schema(data_schema),
                description_placeholders={
                    "suffix": await self._get_options_translation("description_text.calibration_submit_hint")
                },
            )
        device = None
        if isinstance(self._last_device, str):
            device = self._get_bermuda_device_from_registry(self._last_device)
        results_str = ""
        if device is not None and isinstance(self._last_scanner_info, dict):
            results = {}
            # Gather new estimates for distances using rssi hist and the new offset.
            for scanner in self.coordinator.scanner_list:
                scanner_name = self.coordinator.devices[scanner].name
                cur_offset = self._last_scanner_info.get(scanner_name, 0)
                if (scanneradvert := device.get_scanner(scanner)) is not None:
                    results[scanner_name] = [
                        rssi_to_metres(
                            historical_rssi + cur_offset,
                            self.options.get(CONF_REF_POWER, DEFAULT_REF_POWER),
                            self.options.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
                        )
                        for historical_rssi in scanneradvert.hist_rssi
                    ]
            # Format the results for display (HA has full markdown support!)
            t_scanner = await self._get_options_translation("description_text.scanner_table_col_scanner")
            results_str = f"| {t_scanner} | 0 | 1 | 2 | 3 | 4 |\n|---|---:|---:|---:|---:|---:|"
            for scanner_name, distances in results.items():
                results_str += f"\n|{scanner_name}|"
                for i in range(5):
                    # We round to 2 places (1cm) and pad to fit nn.nn
                    try:
                        results_str += f" `{distances[i]:>6.2f}`|"
                    except IndexError:
                        results_str += "`-`|"
            results_str += "\n\n"

        return self.async_show_form(
            step_id="calibration2_scanners",
            data_schema=vol.Schema(data_schema),
            description_placeholders={"suffix": results_str},
        )

    def _get_bermuda_device_from_registry(self, registry_id: str) -> BermudaDevice | None:
        """
        Given a device registry device id, return the associated MAC address.

        Returns None if the id can not be resolved to a mac.
        """
        devreg = dr.async_get(self.hass)
        device = devreg.async_get(registry_id)
        device_address = None
        if device is not None:
            for connection in device.connections:
                if connection[0] in {
                    DOMAIN_PRIVATE_BLE_DEVICE,
                    dr.CONNECTION_BLUETOOTH,
                    "ibeacon",
                }:
                    device_address = connection[1]
                    break
            if device_address is not None:
                return self.coordinator.devices.get(device_address.lower())
        # We couldn't match the HA device id to a bermuda device mac.
        return None

    async def _update_options(self):
        """Update config entry options."""
        return self.async_create_entry(title=NAME, data=self.options)
