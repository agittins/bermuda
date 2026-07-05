"""Regression snapshot for entity ``unique_id`` and device-identity stability.

This is the *safety net* established before any structural refactor of Bermuda.
``unique_id`` values are persisted in Home Assistant's entity registry: if any of
them change, existing user entities are silently orphaned and their history /
automations break. These tests pin, byte-for-byte:

* the per-entity ``unique_id`` suffix scheme (``_floor``, ``_scanner``, ``_rssi``,
  ``_range``, ``_range_raw``, ``_area_switch_reason``, ``_area_last_seen``,
  ``_ref_power``) including the per-scanner ``wifi_mac or address`` fallback,
* the fixed global sensor ids,
* the base ``BermudaDevice.unique_id`` derivation (normalised MAC, iBeacon
  ``uuid_major_minor`` and IRK 32-char forms),
* the ``device_info`` identifiers / connections per device type,
* the ``translation_key`` ↔ ``unique_id`` 1:1 contract with strings.json,
* ``async_remove_config_entry_device`` suffix-stripping that must NOT corrupt
  underscore-bearing iBeacon ids.

Entity ``unique_id``/``device_info`` are pinned via ``object.__new__`` so no
running ``hass`` is needed and the assertions stay decoupled from coordinator
internals (which the refactor will move around).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.bermuda import async_remove_config_entry_device
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_RANDOM_STATIC,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
)
from custom_components.bermuda.device_tracker import BermudaDeviceTracker
from custom_components.bermuda.entity import BermudaEntity, BermudaGlobalEntity
from custom_components.bermuda.number import BermudaNumber
from custom_components.bermuda.sensor import (
    BermudaActiveProxyCount,
    BermudaSensor,
    BermudaSensorAreaLastSeen,
    BermudaSensorAreaSwitchReason,
    BermudaSensorFloor,
    BermudaSensorRange,
    BermudaSensorRssi,
    BermudaSensorScanner,
    BermudaSensorScannerRange,
    BermudaSensorScannerRangeRaw,
    BermudaTotalDeviceCount,
    BermudaTotalProxyCount,
    BermudaNearbyDevices,
    BermudaVisibleDeviceCount,
)

BASE = "aa:bb:cc:dd:ee:ff"
WIFI_MAC = "11:22:33:44:55:66"
SCANNER_BLE = "cc:cc:cc:cc:cc:cc"


def _make_entity(cls, device=None, scanner=None):
    """Instantiate an entity without running __init__ (no hass needed)."""
    ent = object.__new__(cls)
    if device is not None:
        ent._device = device
    if scanner is not None:
        ent._scanner = scanner
    return ent


# --------------------------------------------------------------------------- #
# Per-entity unique_id suffix scheme                                          #
# --------------------------------------------------------------------------- #


def test_device_level_entities_use_base_unique_id():
    """device_tracker, the area sensor and the base entity expose the raw base id."""
    device = SimpleNamespace(unique_id=BASE)
    assert _make_entity(BermudaEntity, device).unique_id == BASE
    assert _make_entity(BermudaSensor, device).unique_id == BASE
    assert _make_entity(BermudaDeviceTracker, device).unique_id == BASE


def test_suffixed_sensor_unique_ids():
    """The fixed per-device suffixes must never drift."""
    device = SimpleNamespace(unique_id=BASE)
    assert _make_entity(BermudaSensorFloor, device).unique_id == f"{BASE}_floor"
    assert _make_entity(BermudaSensorScanner, device).unique_id == f"{BASE}_scanner"
    assert _make_entity(BermudaSensorRssi, device).unique_id == f"{BASE}_rssi"
    assert _make_entity(BermudaSensorRange, device).unique_id == f"{BASE}_range"
    assert _make_entity(BermudaSensorAreaSwitchReason, device).unique_id == f"{BASE}_area_switch_reason"
    assert _make_entity(BermudaSensorAreaLastSeen, device).unique_id == f"{BASE}_area_last_seen"


def test_number_ref_power_unique_id():
    """The ref_power number keeps its legacy suffix."""
    device = SimpleNamespace(unique_id=BASE)
    assert _make_entity(BermudaNumber, device).unique_id == f"{BASE}_ref_power"


def test_per_scanner_range_uses_wifi_mac_then_address():
    """Per-scanner range ids are pinned to wifi/mac with a fallback to address."""
    device = SimpleNamespace(unique_id=BASE)

    scanner_wifi = SimpleNamespace(address_wifi_mac=WIFI_MAC, address=SCANNER_BLE)
    assert _make_entity(BermudaSensorScannerRange, device, scanner_wifi).unique_id == f"{BASE}_{WIFI_MAC}_range"
    assert _make_entity(BermudaSensorScannerRangeRaw, device, scanner_wifi).unique_id == f"{BASE}_{WIFI_MAC}_range_raw"

    scanner_no_wifi = SimpleNamespace(address_wifi_mac=None, address=SCANNER_BLE)
    assert _make_entity(BermudaSensorScannerRange, device, scanner_no_wifi).unique_id == f"{BASE}_{SCANNER_BLE}_range"
    assert (
        _make_entity(BermudaSensorScannerRangeRaw, device, scanner_no_wifi).unique_id
        == f"{BASE}_{SCANNER_BLE}_range_raw"
    )


def test_global_sensor_unique_ids_are_fixed_literals():
    """The global counters use immutable literal ids."""
    assert _make_entity(BermudaTotalProxyCount).unique_id == "BERMUDA_GLOBAL_PROXY_COUNT"
    assert _make_entity(BermudaActiveProxyCount).unique_id == "BERMUDA_GLOBAL_ACTIVE_PROXY_COUNT"
    assert _make_entity(BermudaTotalDeviceCount).unique_id == "BERMUDA_GLOBAL_DEVICE_COUNT"
    assert _make_entity(BermudaVisibleDeviceCount).unique_id == "BERMUDA_GLOBAL_VISIBLE_DEVICE_COUNT"
    assert _make_entity(BermudaNearbyDevices).unique_id == "BERMUDA_GLOBAL_NEARBY_DEVICES"


# --------------------------------------------------------------------------- #
# translation_key <-> unique_id contract (must match strings.json/icons.json)  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cls", "translation_key"),
    [
        (BermudaSensor, "area"),
        (BermudaSensorFloor, "floor"),
        (BermudaSensorScanner, "nearest_scanner"),
        (BermudaSensorRssi, "nearest_rssi"),
        (BermudaSensorRange, "distance"),
        (BermudaSensorScannerRange, "distance_to_scanner"),
        (BermudaSensorScannerRangeRaw, "unfiltered_distance_to_scanner"),
        (BermudaSensorAreaSwitchReason, "area_switch_diagnostic"),
        (BermudaSensorAreaLastSeen, "area_last_seen"),
        (BermudaNumber, "ref_power"),
        (BermudaDeviceTracker, "bermuda_tracker"),
        (BermudaTotalProxyCount, "total_proxy_count"),
        (BermudaActiveProxyCount, "active_proxy_count"),
        (BermudaTotalDeviceCount, "total_device_count"),
        (BermudaVisibleDeviceCount, "visible_device_count"),
        (BermudaNearbyDevices, "nearby_devices"),
    ],
)
def test_translation_keys_are_stable(cls, translation_key):
    """Each entity's translation_key maps 1:1 to its strings.json entry.

    HA's CachedProperties metaclass exposes ``_attr_translation_key`` as a
    descriptor at class level, so the value must be read from an instance.
    """
    assert _make_entity(cls).translation_key == translation_key


# --------------------------------------------------------------------------- #
# device_info identifiers / connections per device type                       #
# --------------------------------------------------------------------------- #


def test_device_info_generic_device():
    """A regular BLE device is identified by (DOMAIN, base) + a bluetooth connection."""
    device = SimpleNamespace(
        is_scanner=False,
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        address=BASE,
        unique_id=BASE,
        name="Dev",
    )
    info = _make_entity(BermudaEntity, device).device_info
    assert info["identifiers"] == {(DOMAIN, BASE)}
    assert info["connections"] == {(dr.CONNECTION_BLUETOOTH, BASE.upper())}


def test_device_info_ibeacon():
    """iBeacon metadevices keep the (DOMAIN, base) identifier + ibeacon connection."""
    device = SimpleNamespace(
        is_scanner=False,
        address_type=ADDR_TYPE_IBEACON,
        address=BASE,
        unique_id=BASE,
        name="Beacon",
    )
    info = _make_entity(BermudaEntity, device).device_info
    assert info["identifiers"] == {(DOMAIN, BASE)}
    assert info["connections"] == {("ibeacon", BASE.lower())}
    assert info["model"] == f"iBeacon: {BASE.lower()}"


def test_device_info_private_ble():
    """IRK metadevices congeal onto the private_ble_device domain identifier."""
    device = SimpleNamespace(
        is_scanner=False,
        address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE,
        address=BASE,
        unique_id=BASE,
        name="Phone",
    )
    info = _make_entity(BermudaEntity, device).device_info
    assert info["identifiers"] == {(DOMAIN_PRIVATE_BLE_DEVICE, BASE)}
    assert info["connections"] == {("private_ble_device", BASE.lower())}


def test_device_info_scanner_pins_wifi_and_ble_mac():
    """Scanner device_info exposes both the wifi (network) and bluetooth MACs."""
    device = SimpleNamespace(
        is_scanner=True,
        address_wifi_mac=WIFI_MAC,
        address_ble_mac="AA:BB:CC:DD:EE:00",
        address="aa:bb:cc:dd:ee:00",
        unique_id=WIFI_MAC,
        name="Scanner",
    )
    info = _make_entity(BermudaEntity, device).device_info
    assert info["identifiers"] == {(DOMAIN, WIFI_MAC)}
    assert info["connections"] == {
        (dr.CONNECTION_NETWORK_MAC, WIFI_MAC.lower()),
        (dr.CONNECTION_BLUETOOTH, "AA:BB:CC:DD:EE:00"),
    }


def test_device_info_global_entity():
    """The global device groups all global counters under a single identifier."""
    info = _make_entity(BermudaGlobalEntity).device_info
    assert info["identifiers"] == {(DOMAIN, "BERMUDA_GLOBAL")}
    assert info["name"] == "Bermuda Global"


# --------------------------------------------------------------------------- #
# Base BermudaDevice.unique_id derivation                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_coordinator():
    """Minimal coordinator for constructing BermudaDevice in isolation."""
    coordinator = MagicMock()
    coordinator.options = {}
    return coordinator


def test_base_unique_id_is_normalised_mac(mock_coordinator):
    """A MAC device's unique_id is the lower-cased colon-form address."""
    device = BermudaDevice("AA:BB:CC:DD:EE:FF", mock_coordinator)
    assert device.address == BASE
    assert device.unique_id == BASE


def test_ibeacon_unique_id_is_uuid_major_minor(mock_coordinator):
    """iBeacon metadevice unique_id is the uuid_major_minor string (underscores kept)."""
    addr = "0123456789abcdef0123456789abcdef_100_200"
    device = BermudaDevice(addr, mock_coordinator)
    assert device.address_type == ADDR_TYPE_IBEACON
    assert device.beacon_unique_id == addr
    assert device.unique_id == addr


def test_irk_unique_id_is_resolved_key(mock_coordinator):
    """IRK metadevice unique_id is the 32-char resolvable-key string."""
    irk = "0123456789abcdef0123456789abcdef"
    mock_coordinator.irk_manager = MagicMock()
    with patch("custom_components.bermuda.bermuda_device.pble_coordinator.async_get_coordinator"):
        device = BermudaDevice(irk, mock_coordinator)
    assert device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE
    assert device.beacon_unique_id == irk
    assert device.unique_id == irk


# --------------------------------------------------------------------------- #
# async_remove_config_entry_device suffix handling                            #
# --------------------------------------------------------------------------- #


def _entry_with_devices(devices):
    entry = MagicMock()
    coordinator = MagicMock()
    coordinator.devices = devices
    entry.runtime_data.coordinator = coordinator
    return entry


async def test_remove_device_strips_entity_suffix():
    """A legacy identifier carrying an entity suffix resolves to the base device."""
    device_obj = SimpleNamespace(create_sensor=True)
    entry = _entry_with_devices({BASE: device_obj})
    device_entry = SimpleNamespace(identifiers={(DOMAIN, f"{BASE}_range")}, name="Dev")

    result = await async_remove_config_entry_device(MagicMock(), entry, device_entry)

    assert result is True
    assert device_obj.create_sensor is False


async def test_remove_device_keeps_ibeacon_underscores_intact():
    """An iBeacon uuid_major_minor identifier must match without being mangled."""
    addr = "0123456789abcdef0123456789abcdef_1_2"
    device_obj = SimpleNamespace(create_sensor=True)
    entry = _entry_with_devices({addr: device_obj})
    device_entry = SimpleNamespace(identifiers={(DOMAIN, addr)}, name="Beacon")

    result = await async_remove_config_entry_device(MagicMock(), entry, device_entry)

    assert result is True
    assert device_obj.create_sensor is False


async def test_remove_unknown_device_is_allowed():
    """Unknown identifiers are permitted to be deleted (stale/legacy)."""
    entry = _entry_with_devices({})
    device_entry = SimpleNamespace(identifiers={(DOMAIN, "zz:zz:zz:zz:zz:zz")}, name="Ghost")

    result = await async_remove_config_entry_device(MagicMock(), entry, device_entry)

    assert result is True
