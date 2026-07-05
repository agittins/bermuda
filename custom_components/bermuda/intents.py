"""
Intents for Bermuda, so micro-locations are reachable by voice and by MCP.

Home Assistant's MCP Server exposes the Assist LLM API, whose tools are the
registered intents. Registering these makes "where are my keys" and "remember
the keys are on the key hook" work from Assist *and* from any MCP client,
without the user needing to touch Bermuda's options menu.

Each handler is a thin wrapper over the coordinator helpers that also back the
services, so behaviour stays consistent between the two surfaces.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import BermudaDataUpdateCoordinator

INTENT_WHERE_IS = "BermudaWhereIs"
INTENT_CALIBRATE = "BermudaCalibrateLocation"
INTENT_LIST = "BermudaListLocations"


def _get_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator | None:
    """Return the (single) Bermuda coordinator, if it is set up."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = getattr(entry, "runtime_data", None)
        if data is not None:
            return cast("BermudaDataUpdateCoordinator", data.coordinator)
    return None


class _BermudaIntentHandler(intent.IntentHandler):
    """Shared helper for Bermuda intent handlers."""

    def _require_coordinator(self, hass: HomeAssistant) -> BermudaDataUpdateCoordinator:
        coordinator = _get_coordinator(hass)
        if coordinator is None:
            msg = "Bermuda is not set up yet."
            raise intent.IntentHandleError(msg)
        return coordinator


class WhereIsIntentHandler(_BermudaIntentHandler):
    """Answer 'where is my <device>'."""

    intent_type = INTENT_WHERE_IS
    description = "Find where a tracked Bluetooth device or item currently is."
    slot_schema = {vol.Required("name"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        coordinator = self._require_coordinator(intent_obj.hass)
        slots = self.async_validate_slots(intent_obj.slots)
        name = slots["name"]["value"]
        response = intent_obj.create_response()

        device = coordinator._resolve_device(name)  # noqa: SLF001 - internal helper, same package
        if device is None:
            response.async_set_speech(f"I don't know a device called {name}.")
            return response

        info = coordinator._device_location(device)  # noqa: SLF001
        spot = info.get("micro_location")
        area = info.get("area_name")
        if spot:
            response.async_set_speech(f"{device.name} is at the {spot}.")
        elif area:
            response.async_set_speech(f"{device.name} is in the {area}.")
        else:
            response.async_set_speech(f"I can't currently locate {device.name}.")
        return response


class CalibrateLocationIntentHandler(_BermudaIntentHandler):
    """Handle 'remember that <device> is at <location>'."""

    intent_type = INTENT_CALIBRATE
    description = "Calibrate a device's current spot as a named micro-location, eg 'Key hook'."
    slot_schema = {vol.Required("device"): cv.string, vol.Required("location"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        coordinator = self._require_coordinator(intent_obj.hass)
        slots = self.async_validate_slots(intent_obj.slots)
        device_name = slots["device"]["value"]
        location = slots["location"]["value"]
        response = intent_obj.create_response()

        device = coordinator._resolve_device(device_name)  # noqa: SLF001
        if device is None:
            response.async_set_speech(f"I don't know a device called {device_name}.")
            return response

        try:
            summary = coordinator.calibrate_location(device, location)
        except ServiceValidationError as err:
            response.async_set_speech(str(err))
            return response

        speech = f"Okay, I've saved {device.name}'s spot as {location}, using {summary['scanner_count']} proxies."
        if "warning" in summary:
            speech += " Note: " + summary["warning"]
        response.async_set_speech(speech)
        return response


class ListLocationsIntentHandler(_BermudaIntentHandler):
    """Handle 'list my saved spots' (optionally for one device)."""

    intent_type = INTENT_LIST
    description = "List the saved micro-locations, optionally for one device."
    slot_schema = {vol.Optional("name"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        coordinator = self._require_coordinator(intent_obj.hass)
        slots = self.async_validate_slots(intent_obj.slots)
        response = intent_obj.create_response()

        device = None
        if (name_slot := slots.get("name")) is not None and name_slot.get("value"):
            device = coordinator._resolve_device(name_slot["value"])  # noqa: SLF001
        address = device.address if device is not None else None

        names = [fingerprint.name for fingerprint in coordinator.fingerprints.list(address)]
        if not names:
            response.async_set_speech("There are no saved micro-locations yet.")
        else:
            response.async_set_speech("Saved spots: " + ", ".join(names) + ".")
        return response


@callback
def async_register_intents(hass: HomeAssistant) -> None:
    """Register Bermuda's intents with Home Assistant (idempotent)."""
    intent.async_register(hass, WhereIsIntentHandler())
    intent.async_register(hass, CalibrateLocationIntentHandler())
    intent.async_register(hass, ListLocationsIntentHandler())
