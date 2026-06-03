"""Tests for pure coordinator helpers and the AreaTests diagnostic dataclass."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.trilateration import AreaTests


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
