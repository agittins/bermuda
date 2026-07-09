"""Tests for the pure helper functions in helpers.py."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.bermuda.const import (
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
)
from custom_components.bermuda.helpers import is_device_selectable

CUTOFF = 1_000.0


def _device(**overrides):
    base = {
        "is_scanner": False,
        "address_type": BDADDR_TYPE_OTHER,
        "last_seen": CUTOFF + 100,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_normal_device_is_selectable():
    assert is_device_selectable(_device(), CUTOFF) is True


def test_scanner_is_not_selectable():
    assert is_device_selectable(_device(is_scanner=True), CUTOFF) is False


def test_private_ble_device_is_not_selectable():
    """Private BLE devices configure themselves and must not clutter pickers."""
    assert is_device_selectable(_device(address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE), CUTOFF) is False


def test_stale_random_mac_is_not_selectable():
    """A random (resolvable) MAC unseen since the cutoff has rotated away."""
    dev = _device(address_type=BDADDR_TYPE_RANDOM_RESOLVABLE, last_seen=CUTOFF - 1)
    assert is_device_selectable(dev, CUTOFF) is False


def test_fresh_random_mac_is_selectable():
    dev = _device(address_type=BDADDR_TYPE_RANDOM_RESOLVABLE, last_seen=CUTOFF + 1)
    assert is_device_selectable(dev, CUTOFF) is True


def test_stale_non_random_device_stays_selectable():
    """Staleness only disqualifies rotating random MACs, not stable addresses."""
    dev = _device(address_type=BDADDR_TYPE_OTHER, last_seen=CUTOFF - 1)
    assert is_device_selectable(dev, CUTOFF) is True
