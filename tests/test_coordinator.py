"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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
