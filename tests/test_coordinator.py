"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

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


def test_refresh_areas_in_use_dispatches_on_change():
    """A changed set of in-use areas should be stored and dispatched."""
    coordinator = SimpleNamespace(
        hass=SimpleNamespace(),
        _areas_in_use=set(),
        get_scanners=[
            SimpleNamespace(area_id="area_a"),
            SimpleNamespace(area_id=None),
        ],
    )

    with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_dispatch:
        BermudaDataUpdateCoordinator._refresh_areas_in_use(coordinator)

    assert coordinator._areas_in_use == {"area_a"}
    mock_dispatch.assert_called_once()


def test_refresh_areas_in_use_no_dispatch_when_unchanged():
    """An unchanged set of in-use areas should not trigger a dispatch."""
    coordinator = SimpleNamespace(
        hass=SimpleNamespace(),
        _areas_in_use={"area_a"},
        get_scanners=[SimpleNamespace(area_id="area_a")],
    )

    with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_dispatch:
        BermudaDataUpdateCoordinator._refresh_areas_in_use(coordinator)

    assert coordinator._areas_in_use == {"area_a"}
    mock_dispatch.assert_not_called()


def test_areas_in_use_property():
    """The areas_in_use property should expose the backing set."""
    coordinator = SimpleNamespace(_areas_in_use={"area_a", "area_b"})
    assert BermudaDataUpdateCoordinator.areas_in_use.fget(coordinator) == {"area_a", "area_b"}
