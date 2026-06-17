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


def test_pruning_quota_shortfall_computed_after_keeper_removal(monkeypatch):
    """The quota shortfall must be computed AFTER keepers leave prune_list.

    Regression: keepers still in prune_list inflated len(prune_list), under-counted
    the shortfall and under-pruned under quota pressure. With PRUNE_MAX_COUNT forced
    low, all three quota candidates must be pruned (only the two keepers survive) —
    which only holds once the keeper subtraction precedes the shortfall calculation.
    """
    monkeypatch.setattr("custom_components.bermuda.pruning.PRUNE_MAX_COUNT", 2)
    now = monotonic_time_coarse()

    def _dev(addr, last_seen):
        return SimpleNamespace(
            address=addr,
            last_seen=last_seen,
            create_sensor=False,
            is_scanner=False,
            address_type=BDADDR_TYPE_OTHER,
            metadevice_sources=[],
        )

    # ee:01 is the index-0 keeper of meta_x AND a stale (index-1) source of meta_y.
    shared = _dev("aa:bb:cc:dd:ee:01", now - 99999)
    # ee:02 is the index-0 (kept) source of meta_y.
    other = _dev("aa:bb:cc:dd:ee:02", now)
    # Three recent static devices: quota candidates (prunable_stamps), not auto-pruned.
    p1 = _dev("aa:bb:cc:dd:ee:03", now - 10)
    p2 = _dev("aa:bb:cc:dd:ee:04", now - 20)
    p3 = _dev("aa:bb:cc:dd:ee:05", now - 30)

    meta_x = SimpleNamespace(metadevice_sources=["aa:bb:cc:dd:ee:01"])
    meta_y = SimpleNamespace(metadevice_sources=["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:01"])

    coord = SimpleNamespace(
        stamp_last_prune=0,
        stamp_redactions_expiry=None,
        redactions={},
        irk_manager=MagicMock(),
        metadevices={"x": meta_x, "y": meta_y},
        devices={d.address: d for d in (shared, other, p1, p2, p3)},
        scanner_list=set(),
    )
    coord._get_device = lambda address: coord.devices.get(address)

    prune_devices(coord, force_pruning=True)

    # Keepers survive; all three quota candidates are pruned (pre-fix left one behind).
    assert shared.address in coord.devices
    assert other.address in coord.devices
    for cand in (p1, p2, p3):
        assert cand.address not in coord.devices
    assert len(coord.devices) == 2
