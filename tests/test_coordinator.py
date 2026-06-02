"""Tests for Bermuda coordinator edge cases."""

from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bermuda.const import ADDR_TYPE_IBEACON, CONF_DEVICES
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def test_dump_devices_includes_private_ble_current_addresses() -> None:
    """Test configured-device dumps include Private BLE source addresses."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.options = {CONF_DEVICES: []}
    coordinator.pb_state_sources = {"device_tracker.phone": "aa:bb:cc:dd:ee:ff"}
    coordinator.devices = {
        "aa:bb:cc:dd:ee:ff": SimpleNamespace(to_dict=lambda: {"name": "phone"}),
        "11:22:33:44:55:66": SimpleNamespace(to_dict=lambda: {"name": "other"}),
    }

    service_call = coordinator.service_dump_devices(SimpleNamespace(data={"configured_devices": True, "redact": False}))
    try:
        service_call.send(None)
    except StopIteration as exc:
        result = exc.value
    else:
        raise AssertionError("service_dump_devices unexpectedly awaited")

    assert result == {"aa:bb:cc:dd:ee:ff": {"name": "phone"}}


def test_prune_devices_keeps_metadevices() -> None:
    """Test pruning does not delete iBeacon/IRK metadevice entries."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.stamp_last_prune = 0
    coordinator.stamp_redactions_expiry = None
    coordinator.redactions = {}
    coordinator.irk_manager = MagicMock()

    metadevice_address = "00112233445566778899aabbccddeeff_1_2"
    metadevice = SimpleNamespace(
        address=metadevice_address,
        metadevice_sources=[],
        last_seen=-999999999,
        create_sensor=False,
        is_scanner=False,
        address_type=ADDR_TYPE_IBEACON,
        adverts={},
    )
    coordinator.devices = {metadevice_address: metadevice}
    coordinator.metadevices = {metadevice_address: metadevice}

    coordinator.prune_devices(force_pruning=True)

    assert coordinator.devices == {metadevice_address: metadevice}
