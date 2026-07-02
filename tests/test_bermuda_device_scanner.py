"""
Coverage for BermudaScannerDeviceMixin (custom_components/bermuda/bermuda_device_scanner.py).

Uses the same ``make_coordinator()`` MagicMock-coordinator pattern as
tests/test_bermuda_device_extra.py to build a real ``BermudaDevice``, then
drives the scanner-mixin methods directly.

TESTS ONLY - the source under custom_components/ is never modified.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.bluetooth import BaseHaRemoteScanner, BaseHaScanner

from custom_components.bermuda.bermuda_device import BermudaDevice


def make_coordinator():
    """A MagicMock coordinator usable by BermudaDevice.__init__ (see test_bermuda_device_extra.py)."""
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.irk_manager = MagicMock()
    coordinator.get_manufacturer_from_id.return_value = (None, None)
    return coordinator


@pytest.fixture
def mock_coordinator():
    return make_coordinator()


def _fake_ha_scanner(address: str, *, remote: bool = False, seconds_ago: float = 1.0):
    """A spec'd MagicMock standing in for a BaseHaScanner / BaseHaRemoteScanner."""
    spec = BaseHaRemoteScanner if remote else BaseHaScanner
    scanner = MagicMock(spec=spec)
    scanner.source = address
    scanner.name = "FakeScanner"
    scanner.time_since_last_detection.return_value = seconds_ago
    scanner.discovered_device_timestamps = {}
    return scanner


# --------------------------------------------------------------------------- #
# async_as_scanner_nolonger
# --------------------------------------------------------------------------- #
def test_async_as_scanner_nolonger_demotes_and_notifies_coordinator(mock_coordinator):
    """Demoting a scanner clears the flags and tells the coordinator to drop it."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    ha_scanner = _fake_ha_scanner("aa:bb:cc:dd:ee:ff")
    dev.async_as_scanner_init(ha_scanner)
    assert dev.is_scanner is True

    dev.async_as_scanner_nolonger()

    assert dev.is_scanner is False
    assert dev.is_remote_scanner is False
    mock_coordinator.scanner_list_del.assert_called_once_with(dev)


# --------------------------------------------------------------------------- #
# async_as_scanner_init: same-object no-op
# --------------------------------------------------------------------------- #
def test_async_as_scanner_init_same_object_is_noop(mock_coordinator):
    """Calling init twice with the identical ha_scanner object only resolves once."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    ha_scanner = _fake_ha_scanner("aa:bb:cc:dd:ee:ff")

    dev.async_as_scanner_init(ha_scanner)
    assert mock_coordinator.dr.devices.get_entries.call_count == 1

    dev.async_as_scanner_init(ha_scanner)  # same object -> early return, no re-resolve
    assert mock_coordinator.dr.devices.get_entries.call_count == 1


# --------------------------------------------------------------------------- #
# async_as_scanner_resolve_device_entries: entity fallback to first entity
# --------------------------------------------------------------------------- #
def test_resolve_device_entries_entity_fallback_ignores_domain(mock_coordinator):
    """When no switch/light entity exists, the first entity of any domain is used."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._hascanner = SimpleNamespace(source="aa:bb:cc:dd:ee:ff", name="HAScanner")

    bt_entry = SimpleNamespace(
        id="bt-id",
        area_id="area-1",
        name="BT Auto Name",
        name_by_user=None,
        connections={("bluetooth", "AA:BB:CC:DD:EE:FF")},
    )
    mock_coordinator.dr.devices.get_entries.return_value = [bt_entry]
    mock_coordinator.er.entities.get_entries_for_device_id.return_value = [
        SimpleNamespace(domain="sensor", entity_id="sensor.foo")
    ]

    area = SimpleNamespace(name="Lounge", icon="mdi:sofa", floor_id=None)
    dev.ar = MagicMock()
    dev.ar.async_get_area.return_value = area

    dev.async_as_scanner_resolve_device_entries()

    assert dev.entry_id == "bt-id"
    assert dev.scanner_entity_id == "sensor.foo"


# --------------------------------------------------------------------------- #
# _update_area_and_floor
# --------------------------------------------------------------------------- #
def test_update_area_and_floor_valid_floor_sets_floor_fields(mock_coordinator):
    """A valid area with a valid floor_id populates floor name/icon/level."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    area = SimpleNamespace(name="Lounge", icon="mdi:sofa", floor_id="floor-1")
    dev.ar = MagicMock()
    dev.ar.async_get_area.return_value = area
    floor = SimpleNamespace(name="Ground Floor", icon="mdi:floor-plan", level=0)
    dev.fr = MagicMock()
    dev.fr.async_get_floor.return_value = floor

    dev._update_area_and_floor("area-1")

    assert dev.area_id == "area-1"
    assert dev.area_name == "Lounge"
    assert dev.floor_id == "floor-1"
    assert dev.floor_name == "Ground Floor"
    assert dev.floor_icon == "mdi:floor-plan"
    assert dev.floor_level == 0


def test_update_area_and_floor_invalid_floor_id_resets_floor(mock_coordinator):
    """An area whose floor_id doesn't resolve logs a warning and resets floor_*."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    area = SimpleNamespace(name="Lounge", icon="mdi:sofa", floor_id="bogus-floor")
    dev.ar = MagicMock()
    dev.ar.async_get_area.return_value = area
    dev.fr = MagicMock()
    dev.fr.async_get_floor.return_value = None

    dev._update_area_and_floor("area-1")

    assert dev.area_id == "area-1"  # area itself is still valid
    assert dev.floor_id is None
    assert dev.floor_name == "Invalid Floor ID"
    assert dev.floor_level is None


def test_update_area_and_floor_invalid_area_id_clears_area_and_floor(mock_coordinator):
    """An area_id that doesn't resolve logs a warning and clears area + floor fields."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.ar = MagicMock()
    dev.ar.async_get_area.return_value = None

    dev._update_area_and_floor("bogus-area")

    assert dev.area is None
    assert dev.area_name == f"Invalid Area for {dev.name}"
    assert dev.floor is None
    assert dev.floor_id is None
    assert dev.floor_name is None


# --------------------------------------------------------------------------- #
# async_as_scanner_update
# --------------------------------------------------------------------------- #
def test_async_as_scanner_update_replacement_scanner_reinits(mock_coordinator):
    """A different ha_scanner instance triggers the 'replacement' log + re-init."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    ha_scanner1 = _fake_ha_scanner("aa:bb:cc:dd:ee:ff", seconds_ago=1.0)
    dev.async_as_scanner_init(ha_scanner1)
    assert dev._hascanner is ha_scanner1

    ha_scanner2 = _fake_ha_scanner("aa:bb:cc:dd:ee:ff", seconds_ago=2.0)
    dev.async_as_scanner_update(ha_scanner2)

    assert dev._hascanner is ha_scanner2


def test_async_as_scanner_update_stamp_backwards_logs_debug_and_keeps_last_seen(mock_coordinator):
    """A stamp that goes backwards by > 0.8s only logs; last_seen is left untouched."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    ha_scanner = _fake_ha_scanner("aa:bb:cc:dd:ee:ff", seconds_ago=0.0)

    with patch("custom_components.bermuda.bermuda_device_scanner.monotonic_time_coarse", return_value=1000.0):
        dev.async_as_scanner_init(ha_scanner)  # first update sets last_seen = 1000.0

    assert dev.last_seen == 1000.0

    # New stamp = 1000.0 - 5.0 = 995.0; that's 5s behind last_seen (> 0.8s threshold).
    ha_scanner.time_since_last_detection.return_value = 5.0
    with patch("custom_components.bermuda.bermuda_device_scanner.monotonic_time_coarse", return_value=1000.0):
        dev.async_as_scanner_update(ha_scanner)

    # The branch only logs a debug message; last_seen is not reset backwards.
    assert dev.last_seen == 1000.0


# --------------------------------------------------------------------------- #
# async_as_scanner_get_stamp
# --------------------------------------------------------------------------- #
def test_get_stamp_remote_scanner_none_stamps_returns_none(mock_coordinator):
    """A remote scanner whose stamps dict is None (never updated) returns None."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._is_remote_scanner = True
    dev.stamps = None

    assert dev.async_as_scanner_get_stamp("11:22:33:44:55:66") is None


def test_get_stamp_remote_scanner_empty_stamps_returns_none(mock_coordinator):
    """A remote scanner with an empty stamps dict returns None."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._is_remote_scanner = True
    dev.stamps = {}

    assert dev.async_as_scanner_get_stamp("11:22:33:44:55:66") is None


def test_get_stamp_remote_scanner_returns_matching_stamp(mock_coordinator):
    """A known address (case-insensitive) returns its stamp from self.stamps."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._is_remote_scanner = True
    dev.stamps = {"11:22:33:44:55:66": 123.45}

    assert dev.async_as_scanner_get_stamp("11:22:33:44:55:66") == 123.45


def test_get_stamp_remote_scanner_missing_address_returns_none(mock_coordinator):
    """An address not present in self.stamps hits the except (KeyError) path."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._is_remote_scanner = True
    dev.stamps = {"aa:aa:aa:aa:aa:aa": 1.0}

    assert dev.async_as_scanner_get_stamp("11:22:33:44:55:66") is None


def test_get_stamp_non_remote_scanner_returns_none(mock_coordinator):
    """A non-remote (e.g. BlueZ/usb) scanner always returns None without stamps."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._is_remote_scanner = False

    assert dev.async_as_scanner_get_stamp("11:22:33:44:55:66") is None


# --------------------------------------------------------------------------- #
# async_as_scanner_update: remote-scanner stamps copy
# --------------------------------------------------------------------------- #
def test_async_as_scanner_update_remote_scanner_copies_stamps(mock_coordinator):
    """A remote scanner copies discovered_device_timestamps directly."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    ha_scanner = _fake_ha_scanner("aa:bb:cc:dd:ee:ff", remote=True)
    ha_scanner.discovered_device_timestamps = {"11:22:33:44:55:66": 42.0}

    dev.async_as_scanner_init(ha_scanner)

    assert dev.is_remote_scanner is True
    assert dev.stamps == {"11:22:33:44:55:66": 42.0}
