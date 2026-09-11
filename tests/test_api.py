"""Tests for the public advert-snapshot API in bermuda.api."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.util import slugify

from custom_components.bermuda.api import (
    SNAPSHOT_VERSION,
    async_get_advert_snapshot,
    async_get_coordinator,
)
from custom_components.bermuda.const import DOMAIN


def _make_hass(coordinator):
    """A hass stand-in whose bermuda config entry carries `coordinator`."""
    entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: [entry] if domain == DOMAIN else [])
    )


def _make_coordinator():
    scanner_device = SimpleNamespace(last_seen=1000.0)
    advert = SimpleNamespace(
        name="Master Bedroom esp32c5 f17464",
        scanner_address="f1:74:64:00:00:01",
        scanner_device=scanner_device,
        area_id="master_bedroom",
        area_name="Master Bedroom",
        rssi_distance=3.5,
        rssi_distance_raw=4.1,
        rssi=-63,
        stamp=999.0,
    )
    device = SimpleNamespace(
        name="Meg",
        address_type="bd_addr_random_resolvable",
        area_id="catwalk",
        area_name="Catwalk",
        adverts={("aa:bb:cc:dd:ee:ff", "f1:74:64:00:00:01"): advert},
    )
    empty_device = SimpleNamespace(
        name="Nothing Heard",
        address_type="bd_addr_other",
        area_id=None,
        area_name=None,
        adverts={},
    )
    return SimpleNamespace(devices={"aa:bb:cc:dd:ee:ff": device, "11:22:33:44:55:66": empty_device})


def test_async_get_coordinator_returns_none_without_bermuda():
    """No bermuda config entry must be a clean None, not an exception."""
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain: []))
    assert async_get_coordinator(hass) is None
    assert async_get_advert_snapshot(hass) is None


def test_snapshot_exposes_per_scanner_readings_without_entities():
    """The whole point: per-scanner distances are available straight from the
    coordinator, with no entity in the state machine involved."""
    hass = _make_hass(_make_coordinator())

    snapshot = async_get_advert_snapshot(hass)

    assert snapshot is not None
    assert snapshot["version"] == SNAPSHOT_VERSION
    device = snapshot["devices"]["aa:bb:cc:dd:ee:ff"]
    assert device["name"] == "Meg"
    assert device["slug"] == "meg"

    scanner = device["scanners"]["f1:74:64:00:00:01"]
    assert scanner["distance"] == 3.5
    assert scanner["distance_raw"] == 4.1
    assert scanner["rssi"] == -63
    assert scanner["area_name"] == "Master Bedroom"
    # age is derived from the advert stamp, so a scanner that has gone quiet
    # ages out even though its last value never changed.
    assert scanner["age"] is not None and scanner["age"] > 0


def test_scanner_slug_tracks_current_name_and_address_is_the_join_key():
    """`slug` is best-effort and `address` is the stable key.

    In the simple case the slug matches Bermuda's ``_distance_to_<slug>``
    entity suffix, which is convenient - but the entity_id is frozen at entity
    creation and does not follow later renames, so consumers must key off the
    scanner address instead.
    """
    hass = _make_hass(_make_coordinator())

    snapshot = async_get_advert_snapshot(hass)
    scanner = snapshot["devices"]["aa:bb:cc:dd:ee:ff"]["scanners"]["f1:74:64:00:00:01"]

    assert scanner["slug"] == slugify("Master Bedroom esp32c5 f17464")
    assert scanner["slug"] == "master_bedroom_esp32c5_f17464"
    # The address is echoed so a consumer iterating values (not items) still
    # has the stable identity to hand.
    assert scanner["address"] == "f1:74:64:00:00:01"


def test_snapshot_exposes_ids_needed_to_join_the_entity_registry():
    """A consumer migrating off entity scraping has to map its stored
    `_distance_to_<slug>` ids to scanners. Bermuda builds those entities'
    unique_ids as f"{device.unique_id}_{scanner.address_wifi_mac or
    scanner.address}_range", so the snapshot must surface both ids - otherwise
    the consumer has to hard-code Bermuda's wifi-mac fallback rule itself.
    """
    coordinator = _make_coordinator()
    scanner_addr = "f1:74:64:00:00:01"
    coordinator.devices[scanner_addr] = SimpleNamespace(
        name="Master Bedroom esp32c5 f17464",
        last_seen=1000.0,
        adverts={},
        address_type="bd_addr_other",
        area_id=None,
        area_name=None,
        # ESPHome proxies report a BLE mac but Bermuda keeps the wifi mac as
        # unique_id for entity-id stability, so these legitimately differ.
        unique_id="aa:11:22:33:44:55",
        address_wifi_mac="aa:11:22:33:44:55",
    )

    snapshot = async_get_advert_snapshot(_make_hass(coordinator))
    device = snapshot["devices"]["aa:bb:cc:dd:ee:ff"]
    scanner = device["scanners"][scanner_addr]

    assert scanner["unique_id"] == "aa:11:22:33:44:55"
    assert scanner["address_wifi_mac"] == "aa:11:22:33:44:55"
    # ...and it is NOT the same as the advert's scanner address, which is the
    # exact trap this field exists to avoid.
    assert scanner["unique_id"] != scanner["address"]
    assert "unique_id" in device


def test_scanner_name_comes_from_the_scanner_device_not_the_advert():
    """advert.name is a copy taken when the advert was created and goes stale
    when the scanner is renamed - Bermuda's own sensor.py deliberately avoids
    it for this reason. The snapshot must report the CURRENT scanner name.

    This is the real-world case where Bermuda appends a MAC to disambiguate two
    scanners that share a name: the device name changes, the advert's cached
    copy does not.
    """
    coordinator = _make_coordinator()
    scanner_addr = "f1:74:64:00:00:01"
    advert = next(iter(coordinator.devices["aa:bb:cc:dd:ee:ff"].adverts.values()))

    # Scanner has since been renamed; the advert still holds the old name.
    coordinator.devices[scanner_addr] = SimpleNamespace(
        name="Master Bedroom RRN00 4e88e0 (DC:06:75:4E:88:E2)",
        last_seen=1000.0,
        # Scanner devices live in coordinator.devices too, and carry their own
        # (usually empty) adverts dict.
        adverts={},
        address_type="bd_addr_other",
        area_id="master_bedroom",
        area_name="Master Bedroom",
    )
    assert advert.name == "Master Bedroom esp32c5 f17464"  # stale copy

    snapshot = async_get_advert_snapshot(_make_hass(coordinator))
    scanner = snapshot["devices"]["aa:bb:cc:dd:ee:ff"]["scanners"][scanner_addr]

    assert scanner["name"] == "Master Bedroom RRN00 4e88e0 (DC:06:75:4E:88:E2)"


def test_snapshot_address_filter_and_empty_devices():
    """`addresses` narrows the result; devices with no adverts are dropped
    unless explicitly requested."""
    hass = _make_hass(_make_coordinator())

    # Empty-advert device excluded by default...
    default = async_get_advert_snapshot(hass)
    assert "11:22:33:44:55:66" not in default["devices"]

    # ...but available on request.
    with_empty = async_get_advert_snapshot(hass, include_empty=True)
    assert "11:22:33:44:55:66" in with_empty["devices"]
    assert with_empty["devices"]["11:22:33:44:55:66"]["scanners"] == {}

    # Address filter is case-insensitive and limits the payload.
    filtered = async_get_advert_snapshot(hass, addresses={"AA:BB:CC:DD:EE:FF"})
    assert set(filtered["devices"]) == {"aa:bb:cc:dd:ee:ff"}


def test_snapshot_is_json_able_primitives():
    """The snapshot must not leak Bermuda's internal objects - that is what
    consumers would otherwise bind to."""
    import json

    hass = _make_hass(_make_coordinator())
    snapshot = async_get_advert_snapshot(hass)

    # Raises TypeError if any internal object leaked through.
    json.dumps(snapshot)


def test_snapshot_tolerates_missing_optional_fields():
    """A device seen but not yet measured (distance None, stamp 0) must not
    break the snapshot - that is a normal transient state in Bermuda."""
    coordinator = _make_coordinator()
    advert = next(iter(coordinator.devices["aa:bb:cc:dd:ee:ff"].adverts.values()))
    advert.rssi_distance = None
    advert.rssi = None
    advert.stamp = 0

    snapshot = async_get_advert_snapshot(_make_hass(coordinator))
    scanner = snapshot["devices"]["aa:bb:cc:dd:ee:ff"]["scanners"]["f1:74:64:00:00:01"]

    assert scanner["distance"] is None
    assert scanner["age"] is None


def test_coordinator_supports_listener_subscription():
    """Push path: consumers subscribe to the coordinator rather than needing a
    bespoke event. Guards against the coordinator losing that contract."""
    from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

    assert hasattr(BermudaDataUpdateCoordinator, "async_add_listener")
    # sanity: MagicMock spec would accept anything, so assert on the real class
    assert callable(BermudaDataUpdateCoordinator.async_add_listener)
    _ = MagicMock  # keep import used for parity with other test modules
