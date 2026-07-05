"""Tests for pure coordinator helpers and the AreaTests diagnostic dataclass."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.trilateration import AreaTests

# Captured at import time -- ie. before the autouse `skip_yaml_data_load` fixture in
# conftest.py patches BermudaDataUpdateCoordinator.async_load_manufacturer_ids for every
# single test -- so tests that want to exercise the *real* method body can call this
# directly instead of the (module-wide, autouse-mocked) bound method.
_real_async_load_manufacturer_ids = BermudaDataUpdateCoordinator.async_load_manufacturer_ids


@pytest.fixture
def coordinator():
    """Bare coordinator (no __init__) for testing standalone helpers."""
    return object.__new__(BermudaDataUpdateCoordinator)


def test_count_active_devices(coordinator):
    """Only devices seen within the last 10s count as active."""
    now = monotonic_time_coarse()
    coordinator.devices = {
        "fresh": SimpleNamespace(last_seen=now),
        "recent": SimpleNamespace(last_seen=now - 5),
        "stale": SimpleNamespace(last_seen=now - 100),
    }
    assert coordinator.count_active_devices() == 2


def test_get_active_scanner_summary(coordinator):
    """The summary exposes name/address/area and a computed age per scanner."""
    now = monotonic_time_coarse()
    coordinator._scanners = [
        SimpleNamespace(name="Lounge", address="aa", area_name="Lounge", last_seen=now),
        SimpleNamespace(name="Attic", address="bb", area_name="Attic", last_seen=now - 30),
    ]
    summary = coordinator.get_active_scanner_summary()
    assert len(summary) == 2
    assert {s["name"] for s in summary} == {"Lounge", "Attic"}
    assert all("last_stamp_age" in s and s["last_stamp_age"] >= 0 for s in summary)


def test_count_active_scanners_respects_max_age(coordinator):
    """count_active_scanners filters the summary by recency."""
    now = monotonic_time_coarse()
    coordinator._scanners = [
        SimpleNamespace(name="Lounge", address="aa", area_name="Lounge", last_seen=now),
        SimpleNamespace(name="Attic", address="bb", area_name="Attic", last_seen=now - 30),
    ]
    assert coordinator.count_active_scanners() == 1  # default max_age 10
    assert coordinator.count_active_scanners(max_age=60) == 2


def test_get_device_normalises_and_misses(coordinator):
    """_get_device normalises the MAC and returns None when absent."""
    coordinator.devices = {"aa:bb:cc:dd:ee:ff": "the-device"}
    assert coordinator._get_device("AA:BB:CC:DD:EE:FF") == "the-device"
    assert coordinator._get_device("AABBCCDDEEFF") == "the-device"
    assert coordinator._get_device("99:99:99:99:99:99") is None


def test_init_floors_true_when_any_area_has_floor(coordinator):
    """init_floors returns True as soon as any configured area has a floor_id."""
    coordinator.ar = MagicMock()
    coordinator.ar.async_list_areas.return_value = [
        SimpleNamespace(floor_id=None),
        SimpleNamespace(floor_id="f1"),
    ]
    assert coordinator.init_floors() is True


def test_init_floors_false_when_no_area_has_floor(coordinator):
    """init_floors returns False when no configured area has a floor_id set."""
    coordinator.ar = MagicMock()
    coordinator.ar.async_list_areas.return_value = [
        SimpleNamespace(floor_id=None),
        SimpleNamespace(floor_id=None),
    ]
    assert coordinator.init_floors() is False


async def test_async_get_bluetooth_manager_diagnostics_success(coordinator):
    """A successful manager lookup returns the manager's diagnostics dict verbatim."""
    coordinator.hass = MagicMock()
    manager = MagicMock()
    manager.async_diagnostics = AsyncMock(return_value={"scanners": ["a", "b"]})
    with patch("homeassistant.components.bluetooth.api._get_manager", return_value=manager):
        result = await coordinator.async_get_bluetooth_manager_diagnostics()
    assert result == {"scanners": ["a", "b"]}


async def test_async_get_bluetooth_manager_diagnostics_failure_returns_error_dict(coordinator):
    """A failure resolving/using the manager degrades to an {'error': ...} dict, not a raise."""
    coordinator.hass = MagicMock()
    with patch("homeassistant.components.bluetooth.api._get_manager", side_effect=RuntimeError("kaboom")):
        result = await coordinator.async_get_bluetooth_manager_diagnostics()
    assert "error" in result
    assert "kaboom" in result["error"]


async def test_async_load_manufacturer_ids_sets_uuids_and_clears_flag(coordinator):
    """A successful load populates member/company uuid maps and clears the waiting flag."""
    coordinator.hass = MagicMock()
    coordinator._waitingfor_load_manufacturer_ids = True
    with patch(
        "custom_components.bermuda.coordinator.load_manufacturer_ids",
        new=AsyncMock(return_value=({"member": 1}, {"company": 2})),
    ):
        await _real_async_load_manufacturer_ids(coordinator)
    assert coordinator.member_uuids == {"member": 1}
    assert coordinator.company_uuids == {"company": 2}
    assert coordinator._waitingfor_load_manufacturer_ids is False


async def test_async_load_manufacturer_ids_clears_flag_even_on_error(coordinator):
    """The waiting flag is cleared via the finally block even if the load raises."""
    coordinator.hass = MagicMock()
    coordinator._waitingfor_load_manufacturer_ids = True
    with (
        patch(
            "custom_components.bermuda.coordinator.load_manufacturer_ids",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await _real_async_load_manufacturer_ids(coordinator)
    assert coordinator._waitingfor_load_manufacturer_ids is False


def test_areatests_sensortext_format():
    """AreaTests.sensortext renders a stable pipe-delimited diagnostic string."""
    tests = AreaTests()
    tests.device = "Phone"
    tests.areas = ("Kitchen", "Lounge")
    tests.pcnt_diff = 0.42
    tests.distance = (3.0, 2.0)
    tests.reason = "WIN by not losing!"

    text = tests.sensortext()

    assert "device|Phone" in text
    assert "areas|KitchenLounge" in text
    assert "pcnt_diff|0.420" in text
    assert "distance|3.00|2.00|" in text
    assert "reason|WIN by not losing!" in text
    assert len(text) <= 255  # capped at DIAG_TEXT_MAX_LENGTH
