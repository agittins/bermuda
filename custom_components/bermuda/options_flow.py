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
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
from homeassistant.helpers.translation import async_get_translations

from .const import (
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    CONF_AREA_ENTITIES,
    CONF_AREA_ENTITY_DISTANCE,
    CONF_AREA_ENTITY_DISTANCES,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_EXCLUDE_DEVICES,
    CONF_IRK,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_SMOOTHING_SAMPLES,
    CONF_TRACK_CATEGORIES,
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
    NAME,
    OPT_MIN_ATTENUATION,
    OPT_MIN_DEVTRACK_TIMEOUT,
    OPT_MIN_MAX_RADIUS,
    OPT_MIN_MAX_VELOCITY,
    OPT_MIN_SMOOTHING_SAMPLES,
    OPT_MIN_UPDATE_INTERVAL,
    OPT_REF_POWER_MAX,
    OPT_REF_POWER_MIN,
    TRACK_CATEGORIES,
)
from .options_text import _DESCRIPTION_TEXTS
from .private_enrol import async_enrol_private_device
from .util import mac_redact

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator


class BermudaOptionsFlowHandler(OptionsFlow):
    """Config flow options handler for bermuda."""

    def __init__(self) -> None:
        """Initialize Bermuda options flow."""
        self.coordinator: BermudaDataUpdateCoordinator
        self.devices: dict[str, BermudaDevice]
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
                "enrol_private",
                "area_entities",
            ],
            description_placeholders=messages,
        )

    async def async_step_globalopts(self, user_input=None):
        """Global options, grouped into collapsible sections for readability."""
        if user_input is not None:
            # Each section nests its fields; flatten them back into the flat options.
            for value in user_input.values():
                self.options.update(value)
            return await self._update_options()

        def _opt(key, default):
            return self.options.get(key, default)

        def _float(min_=None, max_=None):
            return vol.All(vol.Coerce(float), vol.Range(min=min_, max=max_))

        def _int(min_=None):
            return vol.All(vol.Coerce(int), vol.Range(min=min_))

        data_schema = vol.Schema(
            {
                vol.Required("distance_model"): section(
                    vol.Schema(
                        {
                            vol.Required(CONF_REF_POWER, default=_opt(CONF_REF_POWER, DEFAULT_REF_POWER)): _float(
                                OPT_REF_POWER_MIN, OPT_REF_POWER_MAX
                            ),
                            vol.Required(CONF_ATTENUATION, default=_opt(CONF_ATTENUATION, DEFAULT_ATTENUATION)): _float(
                                OPT_MIN_ATTENUATION
                            ),
                            vol.Required(CONF_MAX_RADIUS, default=_opt(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)): _float(
                                OPT_MIN_MAX_RADIUS
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required("tracking"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_DEVTRACK_TIMEOUT, default=_opt(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT)
                            ): _int(OPT_MIN_DEVTRACK_TIMEOUT),
                            vol.Required(
                                CONF_UPDATE_INTERVAL, default=_opt(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                            ): _float(OPT_MIN_UPDATE_INTERVAL),
                        }
                    ),
                    {"collapsed": True},
                ),
                vol.Required("smoothing"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_SMOOTHING_SAMPLES, default=_opt(CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES)
                            ): _int(OPT_MIN_SMOOTHING_SAMPLES),
                            vol.Required(
                                CONF_MAX_VELOCITY, default=_opt(CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY)
                            ): _float(OPT_MIN_MAX_VELOCITY),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(step_id="globalopts", data_schema=data_schema)

    async def async_step_selectdevices(self, user_input=None):
        """Choose what to track: individual devices, whole categories, and exclusions."""
        if user_input is not None:
            self.options[CONF_DEVICES] = user_input.get("devices", [])
            self.options[CONF_TRACK_CATEGORIES] = user_input.get("track_categories", [])
            self.options[CONF_EXCLUDE_DEVICES] = user_input.get("exclude", [])
            return await self._update_options()

        self.devices = self.config_entry.runtime_data.coordinator.devices
        options_list: list[SelectOptionDict] = []
        for device in self.devices.values():
            # Scanners aren't tracked; Private BLE devices configure themselves.
            if device.is_scanner or device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                continue
            # A random MAC unseen for >2h is not useful.
            if device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE and device.last_seen < monotonic_time_coarse() - (
                60 * 60 * 2
            ):
                continue
            addr = device.address.upper()
            manuf = f" · {device.manufacturer}" if device.manufacturer else ""
            rssi = f" · {device.area_rssi:.0f}dBm" if device.area_rssi is not None else ""
            label = f"[{device.category}] {addr} · {device.name}{manuf}{rssi}"
            options_list.append(SelectOptionDict(value=addr, label=label))

        options_list.sort(key=lambda opt: opt["label"])

        # Keep already-configured-but-no-longer-discovered devices selectable.
        discovered = {opt["value"] for opt in options_list}
        options_list.extend(
            SelectOptionDict(value=address.upper(), label=f"{address.upper()} (saved)")
            for address in self.options.get(CONF_DEVICES, [])
            if isinstance(address, str) and address.upper() not in discovered
        )

        device_selector = SelectSelector(SelectSelectorConfig(options=options_list, multiple=True, sort=False))
        category_selector = SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=cat, label=cat) for cat in TRACK_CATEGORIES],
                multiple=True,
                sort=False,
                translation_key="track_category",
            )
        )
        data_schema = vol.Schema(
            {
                vol.Optional(
                    "devices",
                    default=[a.upper() for a in self.options.get(CONF_DEVICES, []) if isinstance(a, str)],
                ): device_selector,
                vol.Optional(
                    "track_categories",
                    default=list(self.options.get(CONF_TRACK_CATEGORIES, [])),
                ): category_selector,
                vol.Optional(
                    "exclude",
                    default=[a.upper() for a in self.options.get(CONF_EXCLUDE_DEVICES, []) if isinstance(a, str)],
                ): device_selector,
            }
        )
        return self.async_show_form(step_id="selectdevices", data_schema=data_schema)

    async def async_step_enrol_private(self, user_input=None):
        """
        Enrol a privacy device (iPhone/Watch) by its IRK.

        Bermuda's proxies can't pair like ESPresense firmware, so we hand the IRK
        to HA's private_ble_device integration; Bermuda then tracks it on its own.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await async_enrol_private_device(self.hass, user_input[CONF_IRK], user_input.get(CONF_NAME, ""))
            if not error:
                # Nudge the coordinator so the new private device shows up promptly.
                with contextlib.suppress(Exception):
                    await self.coordinator.async_request_refresh()
                return await self._update_options()
            errors["base" if error == "bluetooth_not_available" else CONF_IRK] = error

        data_schema = vol.Schema(
            {
                vol.Required(CONF_IRK): TextSelector(),
                vol.Optional(CONF_NAME, default=""): TextSelector(),
            }
        )
        return self.async_show_form(step_id="enrol_private", data_schema=data_schema, errors=errors)

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

    async def _update_options(self):
        """Update config entry options."""
        return self.async_create_entry(title=NAME, data=self.options)
