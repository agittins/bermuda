"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import BDADDR_TYPE_OTHER
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


@pytest.mark.parametrize("prunable_count, quota", [(2, 1), (0, 1), (3, 4), (3, 3), (3, 2), (3, 5)])
def test_prune_devices_quota(prunable_count, quota):
    """Prune the oldest eligible devices up to the quota, preserving tracked devices."""
    devices = {
        "tracked": MagicMock(create_sensor=True, metadevice_sources=[], adverts={}),
        "also_tracked": MagicMock(create_sensor=True, metadevice_sources=[], adverts={}),
    }
    # Insert newest first so the test also checks timestamp ordering.
    for index in reversed(range(prunable_count)):
        devices[f"device_{index}"] = MagicMock(
            create_sensor=False,
            is_scanner=False,
            address_type=BDADDR_TYPE_OTHER,
            last_seen=900 + index,
            metadevice_sources=[],
            adverts={},
        )
    coordinator = SimpleNamespace(
        devices=devices,
        metadevices={},
        scanner_list=[],
        stamp_last_prune=0,
        stamp_redactions_expiry=None,
        irk_manager=MagicMock(),
    )

    with (
        patch("custom_components.bermuda.coordinator.monotonic_time_coarse", return_value=1000),
        patch("custom_components.bermuda.coordinator.PRUNE_MAX_COUNT", quota),
    ):
        BermudaDataUpdateCoordinator.prune_devices(coordinator, force_pruning=True)

    prune_count = min(prunable_count, max(0, prunable_count + 2 - quota))
    assert set(devices) == {"tracked", "also_tracked"} | {
        f"device_{index}" for index in range(prune_count, prunable_count)
    }


def test_handle_devreg_malformed_identifier():
    """A malformed device identifier must not crash the devreg handler.

    Regression test: Home Assistant device identifiers are expected to be
    ``(domain, id)`` 2-tuples, but a buggy integration can register a
    malformed one (observed in the wild: a Plejd device whose id string was
    stored as many single-character elements). Bermuda unpacked every
    identifier directly, so such a device raised
    ``ValueError: too many values to unpack`` and broke the entire
    ``device_registry_updated`` handler on every registry change.

    The handler must skip malformed identifiers, still process valid ones,
    and run to completion.
    """
    # A device with a non-Bermuda connection (so we reach the identifier
    # branch), one malformed identifier and one valid Bermuda identifier.
    device_entry = SimpleNamespace(
        connections={("mac", "AA:BB:CC:DD:EE:FF")},
        identifiers={
            ("plejd", "D", "8", "9", "D", "F", "D", "A"),  # malformed: not a 2-tuple
            ("bermuda", "aa:bb:cc:dd:ee:ff"),  # valid (domain, id)
        },
        name_by_user=None,
    )

    # Lightweight stand-in for the coordinator; we invoke the real (unbound)
    # handler with it as ``self`` to avoid setting up the full integration.
    coordinator = SimpleNamespace(
        devices={},
        dr=SimpleNamespace(async_get=lambda device_id: device_entry),
        _scanner_init_pending=False,
        _do_private_device_init=False,
    )

    event = SimpleNamespace(data={"action": "update", "device_id": "malformed-device", "changes": {}})

    # Previously raised ValueError: too many values to unpack (expected 2).
    BermudaDataUpdateCoordinator.handle_devreg_changes(coordinator, event)

    # Reached the end of the identifier branch without raising.
    assert coordinator._scanner_init_pending is True
