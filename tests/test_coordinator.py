"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


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


def test_prune_devices_tolerates_duplicate_prune_entries(monkeypatch):
    """A device listed twice in prune_list must not crash the update cycle.

    Regression test: ``prune_list`` is appended to from three independent
    places (the metadevice-source sweep, the main device sweep, and the quota
    top-up) whose selections overlap. A stale IRK source older than
    PRUNE_TIME_KNOWN_IRK (960s) satisfies both the metadevice sweep's
    ``last_seen > stamp_known_irk`` test and the main sweep's
    ``last_seen < stamp_unknown_irk`` (240s) test, so it is appended by each.

    The prune loop then used ``del self.devices[addr]``, so the second delete
    raised ``KeyError`` which propagated out of ``prune_devices`` and aborted
    the whole coordinator refresh ("Unexpected error fetching bermuda data").
    Observed in the wild on a 60-proxy install.
    """
    import custom_components.bermuda.coordinator as coordinator_module

    # A last_seen of 0 is only stale once the host has been up longer than
    # the prune timeouts; pin the clock so the test does not depend on how
    # long the machine (a fresh CI container) has been running.
    monotonic_time_coarse = lambda: 5000.0  # noqa: E731
    monkeypatch.setattr(coordinator_module, "monotonic_time_coarse", monotonic_time_coarse)

    from custom_components.bermuda.const import BDADDR_TYPE_RANDOM_RESOLVABLE

    stale_irk = "73:ec:0e:56:42:9e"
    fresh_irk = "73:ec:0e:56:42:01"

    class _Dev(SimpleNamespace):
        """Device stub. Hashable, as the real BermudaDevice is."""

        def __hash__(self):
            return hash(self.address)

    # Old enough to trip BOTH the known-IRK (960s) and unknown-IRK (240s)
    # staleness tests, which is what produces the duplicate append.
    stale_device = _Dev(
        address=stale_irk,
        name="stale irk source",
        last_seen=0,
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_RANDOM_RESOLVABLE,
        metadevice_sources=[],
        adverts={},
    )
    # The metadevice sweep unconditionally keeps index 0, so a second, stale
    # source is required to reach the duplicate-append path.
    fresh_device = _Dev(
        address=fresh_irk,
        name="current irk source",
        last_seen=monotonic_time_coarse(),
        create_sensor=False,
        is_scanner=False,
        address_type=BDADDR_TYPE_RANDOM_RESOLVABLE,
        metadevice_sources=[],
        adverts={},
    )
    metadevice = SimpleNamespace(metadevice_sources=[fresh_irk, stale_irk], adverts={})

    devices = {fresh_irk: fresh_device, stale_irk: stale_device}

    coordinator = SimpleNamespace(
        devices=devices,
        metadevices={"irk-meta": metadevice},
        scanner_list=[],
        stamp_last_prune=0,
        redactions={},
        stamp_redactions_expiry=None,
        irk_manager=SimpleNamespace(async_prune=lambda: None),
        _get_device=lambda address: devices.get(address),
    )

    # Previously raised KeyError on the second delete of the same address.
    BermudaDataUpdateCoordinator.prune_devices(coordinator, force_pruning=True)

    # Pruned exactly once, the run completed, and the current source survived.
    assert stale_irk not in coordinator.devices
    assert fresh_irk in coordinator.devices
