"""
Stale-device pruning for Bermuda.

Letting the discovered-device dict grow forever slows the update loop, so this
periodically removes devices that are not tracked, not scanners and not recent
metadevice sources, with a quota backstop (PRUNE_MAX_COUNT) for busy areas.

Extracted from the coordinator; it operates on the live coordinator instance
(the logic is intrinsically tied to its device/metadevice/scanner state). The
coordinator keeps a thin wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    _LOGGER,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_DEFAULT,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_KNOWN_IRK,
    PRUNE_TIME_UNKNOWN_IRK,
)

if TYPE_CHECKING:
    from .coordinator import BermudaDataUpdateCoordinator

# BlueZ doesn't give us timestamps; we guess them from rssi changes, so we keep
# devices seen within the BlueZ cache window even when over quota.
_BLUEZ_CACHE_SECONDS = 200


def prune_devices(coordinator: BermudaDataUpdateCoordinator, *, force_pruning: bool = False) -> None:
    """
    Remove devices meeting the pruning criteria from ``coordinator.devices``.

    By default nothing is pruned if it ran within PRUNE_TIME_INTERVAL, unless
    ``force_pruning`` is set. Also prunes expired redaction data and IRK MACs.
    """
    co = coordinator
    if co.stamp_last_prune > monotonic_time_coarse() - PRUNE_TIME_INTERVAL and not force_pruning:
        # We ran recently enough, bail out.
        return

    nowstamp = co.stamp_last_prune = monotonic_time_coarse()
    stamp_known_irk = nowstamp - PRUNE_TIME_KNOWN_IRK
    stamp_unknown_irk = nowstamp - PRUNE_TIME_UNKNOWN_IRK

    # Prune redaction data once it has expired.
    if co.stamp_redactions_expiry is not None and co.stamp_redactions_expiry < nowstamp:
        _LOGGER.debug("Clearing redaction data (%d items)", len(co.redactions))
        co.redactions.clear()
        co.stamp_redactions_expiry = None

    # Prune any IRK MACs that have expired.
    co.irk_manager.async_prune()

    # Use a set so an address queued by both the metadevice-source pass and the
    # per-device pass is not deleted twice (which raises KeyError).
    prune_list: set[str] = set()
    prunable_stamps: dict[str, float] = {}  # potential prunees if we must be more aggressive

    metadevice_source_keepers = set()
    for metadevice in co.metadevices.values():
        if len(metadevice.metadevice_sources) > 0:
            # Always keep the most recent source (index 0): static iBeacon sources
            # and IRKs that may exceed spec lifetime while briefly away.
            _first = True
            for address in metadevice.metadevice_sources:
                if _device := co._get_device(address):  # noqa: SLF001
                    if _first or _device.last_seen > stamp_known_irk:
                        metadevice_source_keepers.add(address)
                        _first = False
                    elif (
                        address not in co.scanner_list
                        and address not in co.metadevices
                        and not _device.create_sensor
                        and not _device.is_scanner
                    ):
                        # Same protections as the per-device pass: never drop a
                        # tracked device, a scanner, or a metadevice just because
                        # it is a stale, non-most-recent source of some metadevice.
                        prune_list.add(address)

    for device_address, device in co.devices.items():
        # Prunable if it is not a scanner, not a metadevice, not configured-tracked,
        # not a private_ble device, and a real MAC. A stale untracked iBeacon goes.
        if (
            device_address not in metadevice_source_keepers
            and device_address not in co.metadevices
            and device_address not in co.scanner_list
            and (not device.create_sensor)
            and (not device.is_scanner)
            and device.address_type != BDADDR_TYPE_NOT_MAC48
        ):
            if device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE:
                # Unknown (or truly stale known) IRK source addresses pile up fast
                # in dense areas; prune aggressively (PBLE will re-seed enrollments).
                if device.last_seen < stamp_unknown_irk:
                    prune_list.add(device_address)
                elif device.last_seen < nowstamp - _BLUEZ_CACHE_SECONDS:
                    # Not stale yet, but a candidate if we fall short of quota.
                    prunable_stamps[device_address] = device.last_seen
            elif device.last_seen < nowstamp - PRUNE_TIME_DEFAULT:
                # A static address, and stale.
                prune_list.add(device_address)
            else:
                # Static, not tracked, not so old, but a quota candidate.
                prunable_stamps[device_address] = device.last_seen

    # A source can be the most-recent (kept) source of one metadevice while being
    # a stale, prunable source of another. Drop keepers *before* computing the quota
    # shortfall: otherwise the soon-to-be-removed keepers inflate len(prune_list), the
    # shortfall is undercounted, and the quota backstop under-prunes exactly when a
    # busy area / BLE-MAC DOS makes it matter. (prunable_stamps already excludes
    # keepers via the per-device pass, so the quota expansion can't re-add them.)
    prune_list -= metadevice_source_keepers

    prune_quota_shortfall = len(co.devices) - len(prune_list) - PRUNE_MAX_COUNT
    if prune_quota_shortfall > 0:
        # We need more to prune (busy train station, or a BLE-MAC DOS).
        if len(prunable_stamps) > 0:
            sorted_addresses = sorted([(v, k) for k, v in prunable_stamps.items()])
            cutoff_index = min(len(sorted_addresses), prune_quota_shortfall)
            for _stamp, address in sorted_addresses[:cutoff_index]:
                prune_list.add(address)
        else:
            _LOGGER.warning(
                "Need to prune another %s devices to make quota, but no extra prunables available",
                prune_quota_shortfall,
            )

    # prune_list is ready: no keepers, expanded to quota where possible.
    if prune_list:
        _LOGGER.debug("Pruning %d devices (%d total remaining)", len(prune_list), len(co.devices) - len(prune_list))
    for device_address in prune_list:
        del co.devices[device_address]

    # Brute-force clean pruned addresses out of every device's metadevice_sources.
    for device in co.devices.values():
        for address in prune_list:
            if address in device.metadevice_sources:
                device.metadevice_sources.remove(address)
