"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.bermuda.bermuda_device import BermudaDevice
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


def _scanner_coordinator(scanners, known):
    """A stand-in coordinator whose bluetooth manager reports `scanners`."""
    devices = {}

    def _get_or_create_device(address):
        return devices.setdefault(address, MagicMock(area_id="kitchen", address=address))

    return SimpleNamespace(
        _manager=SimpleNamespace(async_current_scanners=lambda: set(scanners)),
        _hascanners=set(known),
        _scanner_init_pending=False,
        _async_purge_removed_scanners=MagicMock(),
        _async_manage_repair_scanners_without_areas=MagicMock(),
        _get_or_create_device=_get_or_create_device,
        devices=devices,
    )


def test_rebuild_scanner_list_force_rereads_an_unchanged_set():
    """force=True must do its work even when no scanner has come or gone.

    The early return for an unchanged scanner set ran before `force` was
    looked at, so a proxy's new area was not read until a reload
    (agittins/bermuda#856).
    """
    scanner = MagicMock(source="AA:BB:CC:DD:EE:01")
    coordinator = _scanner_coordinator({scanner}, known={scanner})

    BermudaDataUpdateCoordinator._rebuild_scanner_list(coordinator)
    assert not coordinator.devices  # unchanged and not forced: quick exit
    coordinator._async_manage_repair_scanners_without_areas.assert_not_called()

    BermudaDataUpdateCoordinator._rebuild_scanner_list(coordinator, force=True)
    (device,) = coordinator.devices.values()
    device.async_as_scanner_init.assert_called_once_with(scanner, force=True)
    # ...and the "scanners without an area" repair is re-evaluated.
    coordinator._async_manage_repair_scanners_without_areas.assert_called_once_with([])


def test_rebuild_scanner_list_force_updates_area_and_repair_from_registry():
    """A forced refresh must carry a registry area change through to the repair.

    The dispatch-oriented tests above check the signal, not the end-to-end
    effect: their device stubs are pinned to ``area_id="kitchen"`` and the
    device-level test patches the resolver. Use a real BermudaDevice with a
    changing registry entry and the same scanner object throughout instead.
    """
    scanner = MagicMock(source="AA:BB:CC:DD:EE:04", name="Test Proxy")
    scanner.time_since_last_detection.return_value = 5.0
    coordinator = MagicMock()
    coordinator._manager.async_current_scanners.return_value = {scanner}
    coordinator._hascanners = {scanner}
    coordinator._scanner_init_pending = False
    coordinator.devices = {}

    def _get_or_create_device(address):
        return coordinator.devices.setdefault(address, BermudaDevice(address, coordinator))

    coordinator._get_or_create_device = _get_or_create_device

    # Deliberately use two different registry providers and values, rather
    # than the fixture defaults: the bluetooth entry supplies the BLE MAC and
    # the other (ESPHome/Shelly) entry supplies the WiFi MAC two lower.
    registry_entry = SimpleNamespace(
        id="proxy-device",
        name="Proxy from registry",
        name_by_user=None,
        area_id=None,
        connections={("bluetooth", "AA:BB:CC:DD:EE:04"), ("mac", "aa:bb:cc:dd:ee:02")},
    )
    coordinator.dr.devices.get_entries.return_value = [registry_entry]

    repair_calls = coordinator._async_manage_repair_scanners_without_areas
    area_less = ["Proxy from registry [aa:bb:cc:dd:ee:04]"]

    # None -> kitchen -> None, forced each time, with no scanner replacement.
    registry_entry.area_id = None
    BermudaDataUpdateCoordinator._rebuild_scanner_list(coordinator, force=True)
    (device,) = coordinator.devices.values()
    assert device.area_id is None
    repair_calls.assert_called_with(area_less)

    registry_entry.area_id = "kitchen"
    BermudaDataUpdateCoordinator._rebuild_scanner_list(coordinator, force=True)
    assert device.area_id == "kitchen"
    repair_calls.assert_called_with([])

    registry_entry.area_id = None
    BermudaDataUpdateCoordinator._rebuild_scanner_list(coordinator, force=True)
    assert device.area_id is None
    repair_calls.assert_called_with(area_less)


def test_rebuild_scanner_list_still_notices_a_new_scanner_unforced():
    old, new = MagicMock(source="AA:BB:CC:DD:EE:01"), MagicMock(source="AA:BB:CC:DD:EE:02")
    coordinator = _scanner_coordinator({old, new}, known={old})

    BermudaDataUpdateCoordinator._rebuild_scanner_list(coordinator)

    assert len(coordinator.devices) == 2
    for device in coordinator.devices.values():
        assert device.async_as_scanner_init.call_args.kwargs == {"force": False}


def test_refresh_scanners_serves_a_pending_request_once():
    """A pending request forces one rebuild and is then cleared.

    It used to stay set for good. That was harmless only while `force` was
    ignored; honoured, it would re-read every scanner on every cycle.
    """
    calls = []
    coordinator = SimpleNamespace(
        _scanner_init_pending=True,
        _rebuild_scanner_list=lambda force=False: calls.append(force),
    )

    BermudaDataUpdateCoordinator._refresh_scanners(coordinator)
    BermudaDataUpdateCoordinator._refresh_scanners(coordinator)
    BermudaDataUpdateCoordinator._refresh_scanners(coordinator, force=True)

    assert calls == [True, False, True]
    assert coordinator._scanner_init_pending is False


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
    metadevice = SimpleNamespace(
        metadevice_sources=[fresh_irk, stale_irk],
        adverts={},
        metadevice_type=set(),
    )

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


def test_update_metadevices_copies_source_attributes():
    """update_metadevices must copy name/manufacturer/beacon fields from a
    source device onto its metadevice.

    Regression test: the copy loops used to iterate `source_device.items()`
    and test `val is any([...])`. BermudaDevice never actually populated
    dict storage (state lives entirely in instance attributes), so
    `.items()` was always empty and this whole block was a silent no-op -
    metadevices never picked up their source's name/manufacturer/beacon
    fields through this path. `val is any([...])` was also broken on its
    own (any() returns a bool; that's an identity check against True/False,
    not the intended membership test).
    """
    mock_coordinator = MagicMock()
    mock_coordinator.options = {}
    mock_coordinator.hass_version_min_2025_4 = True

    source = BermudaDevice(address="AA:BB:CC:DD:EE:01", coordinator=mock_coordinator)
    source.name_bt_local_name = "My Beacon"
    source.manufacturer = "Acme Corp"
    source.beacon_major = "1"
    source.beacon_minor = "2"
    source.beacon_uuid = "abc123"

    # A non-MAC, non-iBeacon-shaped address keeps _async_process_address_type
    # from classifying this as an iBeacon metadevice, which would pull in the
    # (unrelated) beacon_unique_id mismatch branch above the code under test.
    metadevice = BermudaDevice(address="test_metadevice", coordinator=mock_coordinator)
    metadevice.metadevice_sources = [source.address]

    coordinator = SimpleNamespace(
        devices={source.address: source},
        metadevices={metadevice.address: metadevice},
        _get_device=lambda address: {source.address: source}.get(address),
        _do_private_device_init=False,
        discover_private_ble_metadevices=lambda: None,
    )

    BermudaDataUpdateCoordinator.update_metadevices(coordinator)

    assert metadevice.name_bt_local_name == "My Beacon"
    assert metadevice.manufacturer == "Acme Corp"
    assert metadevice.beacon_major == "1"
    assert metadevice.beacon_minor == "2"
    assert metadevice.beacon_uuid == "abc123"


def test_update_metadevices_does_not_overwrite_existing_name_fields():
    """The 'not already set to something interesting' fields must not clobber
    an existing metadevice value, while the 'VERY interesting' beacon fields
    always take the source's latest value.
    """
    mock_coordinator = MagicMock()
    mock_coordinator.options = {}
    mock_coordinator.hass_version_min_2025_4 = True

    source = BermudaDevice(address="AA:BB:CC:DD:EE:02", coordinator=mock_coordinator)
    source.manufacturer = "New Manufacturer"
    source.beacon_major = "9"

    metadevice = BermudaDevice(address="test_metadevice_2", coordinator=mock_coordinator)
    metadevice.metadevice_sources = [source.address]
    metadevice.manufacturer = "Existing Manufacturer"
    metadevice.beacon_major = "1"

    coordinator = SimpleNamespace(
        devices={source.address: source},
        metadevices={metadevice.address: metadevice},
        _get_device=lambda address: {source.address: source}.get(address),
        _do_private_device_init=False,
        discover_private_ble_metadevices=lambda: None,
    )

    BermudaDataUpdateCoordinator.update_metadevices(coordinator)

    # manufacturer was already set on the metadevice - must be left alone.
    assert metadevice.manufacturer == "Existing Manufacturer"
    # beacon_major is "VERY interesting" - always takes the source's value.
    assert metadevice.beacon_major == "9"
