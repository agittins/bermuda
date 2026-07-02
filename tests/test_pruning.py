"""
Coverage for custom_components/bermuda/pruning.py (prune_devices).

Mirrors the "bare coordinator" pattern from tests/test_coordinator.py and
tests/test_coordinator_scanners.py: build a skeleton coordinator with
``object.__new__`` and set only the attributes ``prune_devices`` reads, then
call the free function directly.

TESTS ONLY - the source under custom_components/ is never modified.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from custom_components.bermuda.const import (
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_UNKNOWN_IRK,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.pruning import prune_devices


def _bare_coordinator() -> BermudaDataUpdateCoordinator:
    """A coordinator skeleton carrying exactly what prune_devices reads."""
    co = object.__new__(BermudaDataUpdateCoordinator)
    co.stamp_last_prune = 0
    co.stamp_redactions_expiry = None
    co.redactions = {}
    co.irk_manager = MagicMock()
    co.metadevices = {}
    co.devices = {}
    co._scanner_list = set()
    return co


def _device(**overrides) -> SimpleNamespace:
    base = {
        "address_type": BDADDR_TYPE_OTHER,
        "last_seen": 1000.0,
        "create_sensor": False,
        "is_scanner": False,
        "metadevice_sources": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Early-return guard (line 48)
# --------------------------------------------------------------------------- #
def test_prune_devices_early_return_when_recently_pruned() -> None:
    """Without force_pruning, a recent stamp_last_prune skips the whole run."""
    co = _bare_coordinator()
    dev = _device()
    co.devices = {"aa:bb:cc:dd:ee:ff": dev}

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        co.stamp_last_prune = 1000.0 - (PRUNE_TIME_INTERVAL - 10)  # ran 170s ago (< 180s interval)
        prune_devices(co, force_pruning=False)

    # Nothing was touched: stamp is unchanged and no pruning logic ran.
    assert co.stamp_last_prune == 1000.0 - (PRUNE_TIME_INTERVAL - 10)
    assert co.devices == {"aa:bb:cc:dd:ee:ff": dev}
    co.irk_manager.async_prune.assert_not_called()


# --------------------------------------------------------------------------- #
# Redaction-expiry cleanup (lines 56-58)
# --------------------------------------------------------------------------- #
def test_prune_devices_clears_expired_redactions() -> None:
    """Expired redaction data is cleared regardless of the device-pruning outcome."""
    co = _bare_coordinator()
    co.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED"}
    co.stamp_redactions_expiry = 500.0  # in the past relative to "now" below

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        prune_devices(co, force_pruning=True)

    assert co.redactions == {}
    assert co.stamp_redactions_expiry is None
    co.irk_manager.async_prune.assert_called_once_with()


def test_prune_devices_keeps_unexpired_redactions() -> None:
    """Redaction data that hasn't expired yet is left alone."""
    co = _bare_coordinator()
    co.redactions = {"aa:bb:cc:dd:ee:ff": "REDACTED"}
    co.stamp_redactions_expiry = 5000.0  # still in the future

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        prune_devices(co, force_pruning=True)

    assert co.redactions == {"aa:bb:cc:dd:ee:ff": "REDACTED"}
    assert co.stamp_redactions_expiry == 5000.0


# --------------------------------------------------------------------------- #
# RANDOM_RESOLVABLE quota-candidate path + quota backstop expansion
# (lines ~104-108, 124-131)
# --------------------------------------------------------------------------- #
def test_prune_devices_quota_backstop_prunes_bluez_cache_candidate() -> None:
    """
    A RANDOM_RESOLVABLE device inside the BlueZ-cache window is not pruned
    directly (it's not yet stale enough for PRUNE_TIME_UNKNOWN_IRK), but
    becomes a "prunable_stamps" quota candidate. With enough total devices to
    exceed PRUNE_MAX_COUNT, the quota backstop expands prune_list to include it.
    """
    co = _bare_coordinator()

    # Plenty of untouchable "keeper" devices (tracked -> create_sensor True),
    # inflating the total count past PRUNE_MAX_COUNT without being prunable
    # themselves.
    keepers = {f"keeper-{i}": _device(create_sensor=True) for i in range(PRUNE_MAX_COUNT + 4)}

    # last_seen is 210s in the past: older than _BLUEZ_CACHE_SECONDS (200) so it
    # becomes a quota candidate, but newer than PRUNE_TIME_UNKNOWN_IRK (240) so it
    # is NOT pruned by the direct staleness check.
    candidate = _device(address_type=BDADDR_TYPE_RANDOM_RESOLVABLE, last_seen=1000.0 - 210)

    co.devices = {**keepers, "random-resolvable-1": candidate}

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        prune_devices(co, force_pruning=True)

    assert "random-resolvable-1" not in co.devices
    assert len(co.devices) == PRUNE_MAX_COUNT + 4


def test_prune_devices_random_resolvable_beyond_unknown_irk_pruned_directly() -> None:
    """A RANDOM_RESOLVABLE device older than PRUNE_TIME_UNKNOWN_IRK is pruned directly."""
    co = _bare_coordinator()
    stale_addr = "aa:bb:cc:dd:ee:ff"
    # Older than PRUNE_TIME_UNKNOWN_IRK (240s) -> direct prune, no quota needed.
    co.devices = {
        stale_addr: _device(address_type=BDADDR_TYPE_RANDOM_RESOLVABLE, last_seen=1000.0 - PRUNE_TIME_UNKNOWN_IRK - 10)
    }

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        prune_devices(co, force_pruning=True)

    assert stale_addr not in co.devices


# --------------------------------------------------------------------------- #
# Quota shortfall with nothing prunable (line ~133)
# --------------------------------------------------------------------------- #
def test_prune_devices_warns_when_quota_shortfall_has_no_candidates(caplog) -> None:
    """A quota shortfall with no eligible prunable_stamps logs a warning, no-op otherwise."""
    co = _bare_coordinator()
    # Every device is a tracked keeper: none are ever added to prune_list or
    # prunable_stamps, so the shortfall can never be filled.
    co.devices = {f"keeper-{i}": _device(create_sensor=True) for i in range(PRUNE_MAX_COUNT + 5)}

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        with caplog.at_level("WARNING", logger="custom_components.bermuda"):
            prune_devices(co, force_pruning=True)

    # Nothing eligible -> nothing pruned.
    assert len(co.devices) == PRUNE_MAX_COUNT + 5
    assert "Need to prune another 5 devices" in caplog.text


# --------------------------------------------------------------------------- #
# Final metadevice_sources cleanup (line ~148)
# --------------------------------------------------------------------------- #
def test_prune_devices_cleans_metadevice_sources_of_pruned_addresses() -> None:
    """A surviving device's metadevice_sources is scrubbed of pruned addresses."""
    co = _bare_coordinator()
    stale_addr = "aa:bb:cc:dd:ee:ff"
    # Old enough to exceed PRUNE_TIME_DEFAULT (86400s) -> pruned directly.
    stale_device = _device(last_seen=-1_000_000.0)
    survivor = _device(create_sensor=True, metadevice_sources=[stale_addr, "other-addr"])

    co.devices = {stale_addr: stale_device, "survivor": survivor}

    with patch("custom_components.bermuda.pruning.monotonic_time_coarse", return_value=1000.0):
        prune_devices(co, force_pruning=True)

    assert stale_addr not in co.devices
    assert stale_addr not in survivor.metadevice_sources
    assert "other-addr" in survivor.metadevice_sources
