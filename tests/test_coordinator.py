"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from homeassistant.const import Platform

from custom_components.bermuda.const import (
    ADDR_TYPE_FINDMY_DEVICE,
    METADEVICE_FINDMY_DEVICE,
    METADEVICE_TYPE_FINDMY_SOURCE,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.entity import BermudaEntity


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


def test_discover_findmy_metadevice_uses_only_fresh_local_addresses():
    """A rolling FindMy tracker becomes one stable device with fresh MAC sources."""
    findmy_entry = SimpleNamespace(data={"type": "device_rolling"}, entry_id="findmy-entry")
    findmy_entity = SimpleNamespace(
        domain=Platform.DEVICE_TRACKER,
        unique_id="AIRTAG-ID",
        entity_id="device_tracker.findmy_bike",
        device_id="findmy-device",
    )
    findmy_state = SimpleNamespace(
        attributes={
            "mac_address": "C1:22:33:44:55:66",
            "local_detected_at": datetime.now(UTC).isoformat(),
        },
        name="Bike",
    )
    findmy_device = SimpleNamespace(name_by_user=None, name="Bike")
    metadevice = SimpleNamespace(
        address="findmy_airtag-id",
        address_type=None,
        metadevice_type=set(),
        findmy_identifier=None,
        create_sensor=False,
        name_by_user=None,
        name_devreg=None,
        metadevice_sources=[],
        make_name=lambda: None,
    )
    source_device = SimpleNamespace(metadevice_type=set())

    coordinator = SimpleNamespace(
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(async_entries=lambda *args, **kwargs: [findmy_entry]),
            states=SimpleNamespace(get=lambda entity_id: findmy_state),
        ),
        er=SimpleNamespace(entities=SimpleNamespace(get_entries_for_config_entry_id=lambda entry_id: [findmy_entity])),
        dr=SimpleNamespace(async_get=lambda device_id: findmy_device),
        metadevices={},
        _get_or_create_device=lambda address: metadevice,
        _get_device=lambda address: source_device,
    )

    BermudaDataUpdateCoordinator.discover_findmy_metadevices(coordinator)

    assert metadevice.address_type == ADDR_TYPE_FINDMY_DEVICE
    assert METADEVICE_FINDMY_DEVICE in metadevice.metadevice_type
    assert METADEVICE_TYPE_FINDMY_SOURCE in source_device.metadevice_type
    assert metadevice.findmy_identifier == "AIRTAG-ID"
    assert metadevice.create_sensor is True
    assert metadevice.metadevice_sources == ["c1:22:33:44:55:66"]
    assert coordinator.metadevices == {"findmy_airtag-id": metadevice}

    findmy_state.attributes = {
        "mac_address": "D1:22:33:44:55:66",
        "local_detected_at": datetime.now(UTC) - timedelta(minutes=6),
    }
    BermudaDataUpdateCoordinator.discover_findmy_metadevices(coordinator)

    assert metadevice.metadevice_sources == ["c1:22:33:44:55:66"]


def test_findmy_metadevice_links_to_findmy_device_registry_identifier():
    """Bermuda entities congeal with the originating FindMy device."""
    entity = object.__new__(BermudaEntity)
    entity._device = SimpleNamespace(
        is_scanner=False,
        address_type=ADDR_TYPE_FINDMY_DEVICE,
        findmy_identifier="AIRTAG-ID",
        unique_id="findmy_airtag-id",
        address="findmy_airtag-id",
        name="Bike",
    )

    device_info = entity.device_info

    assert device_info["identifiers"] == {("findmy", "AIRTAG-ID")}
    assert device_info["connections"] == set()
