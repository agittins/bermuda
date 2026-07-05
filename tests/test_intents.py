"""Tests for Bermuda's voice/Assist intents (custom_components/bermuda/intents.py).

The happy paths (successful where-is / calibrate / list) are already covered
in test_services.py's intent section; here we focus on branches that section
doesn't reach: no coordinator set up at all, the calibrate "few scanners"
warning surfaced through speech, and listing when nothing has been calibrated.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import DOMAIN
from custom_components.bermuda.intents import (
    INTENT_CALIBRATE,
    INTENT_LIST,
    INTENT_WHERE_IS,
    _get_coordinator,
    async_register_intents,
)

# --------------------------------------------------------------------------- #
# helpers (mirrors tests/test_services.py's small device-building helpers)     #
# --------------------------------------------------------------------------- #


def _mock_ad(rssi: int = -60):
    from unittest.mock import MagicMock

    ad = MagicMock()
    ad.rssi = rssi
    ad.tx_power = None
    ad.local_name = None
    ad.manufacturer_data = {}
    ad.service_data = {}
    ad.service_uuids = []
    return ad


def _make_scanner(coordinator, address: str, name: str, area_id: str) -> BermudaDevice:
    scanner = BermudaDevice(address, coordinator)
    scanner.name_by_user = name
    scanner.name = name
    scanner.area_id = area_id
    scanner.area_name = name
    coordinator.devices[scanner.address] = scanner
    return scanner


def _make_tracked_device(coordinator, address: str, name: str) -> BermudaDevice:
    device = BermudaDevice(address, coordinator)
    device.name_by_user = name
    device.name = name
    device.create_sensor = True
    coordinator.devices[device.address] = device
    return device


def _set_distance(device: BermudaDevice, scanner: BermudaDevice, distance: float, rssi: int = -60):
    advert = device.get_scanner(scanner.address)
    if advert is None:
        device.process_advertisement(scanner, _mock_ad(rssi))
        advert = device.get_scanner(scanner.address)
    advert.rssi_distance = distance
    advert.rssi = rssi
    advert.hist_distance_by_interval = [distance] * 5
    advert.area_id = scanner.area_id
    advert.area_name = scanner.area_name
    return advert


def _speech(response) -> str:
    return response.speech["plain"]["speech"]


# --------------------------------------------------------------------------- #
# _get_coordinator / _require_coordinator                                     #
# --------------------------------------------------------------------------- #


async def test_get_coordinator_returns_none_when_bermuda_not_set_up(hass: HomeAssistant):
    """With no Bermuda config entry at all, there is no coordinator to find."""
    assert _get_coordinator(hass) is None


async def test_require_coordinator_raises_via_intent_when_not_set_up(hass: HomeAssistant):
    """An intent call raises IntentHandleError when Bermuda has no live coordinator."""
    async_register_intents(hass)
    with pytest.raises(intent.IntentHandleError):
        await intent.async_handle(hass, DOMAIN, INTENT_WHERE_IS, {"name": {"value": "Keys"}})


# --------------------------------------------------------------------------- #
# CalibrateLocationIntentHandler: few-scanners warning                        #
# --------------------------------------------------------------------------- #


async def test_calibrate_intent_speech_includes_warning_for_single_scanner(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Calibrating with fewer than MICROLOC_MIN_USEFUL_SCANNERS proxies warns in speech."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    await coordinator.fingerprints.async_load()
    scanner = _make_scanner(coordinator, "11:11:11:11:11:11", "Lonely Proxy", "garage")
    fob = _make_tracked_device(coordinator, "ec:00:00:00:00:09", "Fob")
    _set_distance(fob, scanner, 2.0)

    response = await intent.async_handle(
        hass, DOMAIN, INTENT_CALIBRATE, {"device": {"value": "Fob"}, "location": {"value": "Workbench"}}
    )
    speech = _speech(response)
    assert "Workbench" in speech
    assert "Note:" in speech


# --------------------------------------------------------------------------- #
# ListLocationsIntentHandler: nothing saved yet                               #
# --------------------------------------------------------------------------- #


async def test_list_locations_intent_speech_when_nothing_saved(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Listing locations before any calibration reports there are none saved."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    await coordinator.fingerprints.async_load()

    response = await intent.async_handle(hass, DOMAIN, INTENT_LIST, {})
    assert _speech(response) == "There are no saved micro-locations yet."
