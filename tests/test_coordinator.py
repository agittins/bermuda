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
