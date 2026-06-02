"""Regression tests locking in fixes from the full code review."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.const import BDADDR_TYPE_OTHER
from custom_components.bermuda.pruning import prune_devices


def test_pruning_keeps_tracked_metadevice_source():
    """A tracked (create_sensor) device must not be pruned merely for being a stale, non-index-0 metadevice source.

    Regression for the review finding: the metadevice-source pruning pass
    previously bypassed the tracked/scanner guards used by the per-device pass.
    """
    now = monotonic_time_coarse()
    recent = SimpleNamespace(
        address="aa:bb:cc:dd:ee:02",
        last_seen=now,
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        metadevice_sources=[],
    )
    # Tracked by the user, but stale and not the most-recent source of the metadevice.
    tracked = SimpleNamespace(
        address="aa:bb:cc:dd:ee:01",
        last_seen=now - 99999,
        create_sensor=True,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        metadevice_sources=[],
    )
    metadevice = SimpleNamespace(metadevice_sources=["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:01"])

    coord = SimpleNamespace(
        stamp_last_prune=0,
        stamp_redactions_expiry=None,
        redactions={},
        irk_manager=MagicMock(),
        metadevices={"meta": metadevice},
        devices={recent.address: recent, tracked.address: tracked},
        scanner_list=set(),
    )
    coord._get_device = lambda address: coord.devices.get(address)

    prune_devices(coord, force_pruning=True)

    # The tracked source survives despite being a stale, non-index-0 source.
    assert tracked.address in coord.devices
    assert recent.address in coord.devices


def test_pruning_still_drops_untracked_stale_source():
    """An untracked, stale, non-index-0 source IS still pruned (the guard is not over-broad)."""
    now = monotonic_time_coarse()
    recent = SimpleNamespace(
        address="aa:bb:cc:dd:ee:02",
        last_seen=now,
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        metadevice_sources=[],
    )
    junk = SimpleNamespace(
        address="aa:bb:cc:dd:ee:09",
        last_seen=now - 99999,
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        metadevice_sources=[],
    )
    metadevice = SimpleNamespace(metadevice_sources=["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:09"])

    coord = SimpleNamespace(
        stamp_last_prune=0,
        stamp_redactions_expiry=None,
        redactions={},
        irk_manager=MagicMock(),
        metadevices={"meta": metadevice},
        devices={recent.address: recent, junk.address: junk},
        scanner_list=set(),
    )
    coord._get_device = lambda address: coord.devices.get(address)

    prune_devices(coord, force_pruning=True)

    assert junk.address not in coord.devices  # untracked stale source still pruned


def test_pruning_keeps_source_that_is_keeper_for_another_metadevice():
    """A source kept (index 0) by one metadevice must not be pruned for being stale in another.

    Regression for the cross-metadevice keeper/prunable collision.
    """
    now = monotonic_time_coarse()
    shared = SimpleNamespace(
        address="aa:bb:cc:dd:ee:01",
        last_seen=now - 99999,
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        metadevice_sources=[],
    )
    other = SimpleNamespace(
        address="aa:bb:cc:dd:ee:02",
        last_seen=now,
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        metadevice_sources=[],
    )
    meta_x = SimpleNamespace(metadevice_sources=["aa:bb:cc:dd:ee:01"])  # shared = most-recent keeper
    meta_y = SimpleNamespace(metadevice_sources=["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:01"])  # shared = stale source

    coord = SimpleNamespace(
        stamp_last_prune=0,
        stamp_redactions_expiry=None,
        redactions={},
        irk_manager=MagicMock(),
        metadevices={"x": meta_x, "y": meta_y},
        devices={shared.address: shared, other.address: other},
        scanner_list=set(),
    )
    coord._get_device = lambda address: coord.devices.get(address)

    prune_devices(coord, force_pruning=True)

    # shared is a keeper of meta_x, so it survives despite being stale in meta_y.
    assert shared.address in coord.devices
