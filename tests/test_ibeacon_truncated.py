"""A truncated iBeacon payload must not register a bogus beacon."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bermuda.bermuda_device import BermudaDevice


def _device():
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.get_manufacturer_from_id.return_value = (None, False)
    return BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=coordinator), coordinator


def test_truncated_ibeacon_is_not_registered():
    device, coordinator = _device()
    advert = SimpleNamespace(service_uuids=[], manufacturer_data=[{0x004C: b"\x02\x15" + b"\x01" * 10}])
    device.process_manufacturer_data(advert)
    coordinator.register_ibeacon_source.assert_not_called()
    assert device.beacon_unique_id is None


def test_full_ibeacon_is_registered():
    device, coordinator = _device()
    payload = b"\x02\x15" + bytes(range(16)) + b"\x00\x01\x00\x02" + b"\xc5"
    device.process_manufacturer_data(SimpleNamespace(service_uuids=[], manufacturer_data=[{0x004C: payload}]))
    coordinator.register_ibeacon_source.assert_called_once_with(device)
    assert device.beacon_unique_id == f"{bytes(range(16)).hex()}_1_2"
