"""Tests for the ESPresense-style BermudaDevice.category fingerprint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
)


@pytest.fixture
def device():
    coordinator = MagicMock()
    coordinator.options = {}
    return BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=coordinator)


def test_category_ibeacon(device):
    device.address_type = ADDR_TYPE_IBEACON
    assert device.category == "ibeacon"


def test_category_irk(device):
    device.address_type = ADDR_TYPE_PRIVATE_BLE_DEVICE
    assert device.category == "irk"


def test_category_known_vendor(device):
    device.address_type = BDADDR_TYPE_OTHER
    device.manufacturer_id = 0x004C  # Apple
    assert device.category == "apple"


def test_category_named(device):
    device.address_type = BDADDR_TYPE_OTHER
    device.name_bt_local_name = "Jan's thermometer"
    assert device.category == "named"


def test_category_random(device):
    device.address_type = BDADDR_TYPE_RANDOM_RESOLVABLE
    assert device.category == "random"


def test_category_public_is_the_fallback(device):
    device.address_type = BDADDR_TYPE_OTHER
    assert device.category == "public"


def test_ibeacon_takes_precedence_over_vendor(device):
    device.address_type = ADDR_TYPE_IBEACON
    device.manufacturer_id = 0x004C  # would be "apple" if not an iBeacon
    assert device.category == "ibeacon"


def test_vendor_takes_precedence_over_named(device):
    device.address_type = BDADDR_TYPE_OTHER
    device.manufacturer_id = 0x0087  # Garmin
    device.name_bt_local_name = "Forerunner"
    assert device.category == "garmin"
