"""Stale iBeacons nobody tracks must be pruned."""

from __future__ import annotations

from types import SimpleNamespace

from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.const import BDADDR_TYPE_NOT_MAC48, METADEVICE_IBEACON_DEVICE, PRUNE_TIME_DEFAULT
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _beacon(address, last_seen, tracked=False):
    md = SimpleNamespace(
        address=address,
        metadevice_type={METADEVICE_IBEACON_DEVICE},
        create_sensor=tracked,
        last_seen=last_seen,
        metadevice_sources=["11:22:33:44:55:66"],
        address_type=BDADDR_TYPE_NOT_MAC48,
        is_scanner=False,
        adverts={},
    )
    return md


def test_stale_untracked_ibeacons_are_pruned():
    now = monotonic_time_coarse()
    stale = _beacon("aaaa_1_1", now - PRUNE_TIME_DEFAULT - 10)
    fresh = _beacon("bbbb_1_1", now - 10)
    tracked = _beacon("cccc_1_1", now - PRUNE_TIME_DEFAULT - 10, tracked=True)
    devices = {d.address: d for d in (stale, fresh, tracked)}
    coordinator = SimpleNamespace(
        stamp_last_prune=0,
        stamp_redactions_expiry=None,
        redactions={},
        irk_manager=SimpleNamespace(async_prune=lambda: None),
        metadevices=dict(devices),
        devices=dict(devices),
        scanner_list=[],
        _get_device=lambda address: None,
    )
    BermudaDataUpdateCoordinator.prune_devices(coordinator, force_pruning=True)
    assert set(coordinator.metadevices) == {"bbbb_1_1", "cccc_1_1"}
    assert "aaaa_1_1" not in coordinator.devices
