"""
Tests for BermudaDevice class in bermuda_device.py.
"""

import pytest
from unittest.mock import MagicMock, patch
from homeassistant.components.bluetooth import BaseHaScanner, BaseHaRemoteScanner
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import ICON_DEFAULT_AREA, ICON_DEFAULT_FLOOR


@pytest.fixture
def mock_coordinator():
    """Fixture for mocking BermudaDataUpdateCoordinator."""
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.hass_version_min_2025_4 = True
    return coordinator


@pytest.fixture
def mock_scanner():
    """Fixture for mocking BaseHaScanner."""
    scanner = MagicMock(spec=BaseHaScanner)
    scanner.time_since_last_detection.return_value = 5.0
    scanner.source = "mock_source"
    return scanner


@pytest.fixture
def mock_remote_scanner():
    """Fixture for mocking BaseHaRemoteScanner."""
    scanner = MagicMock(spec=BaseHaRemoteScanner)
    scanner.time_since_last_detection.return_value = 5.0
    scanner.source = "mock_source"
    return scanner


@pytest.fixture
def bermuda_device(mock_coordinator):
    """Fixture for creating a BermudaDevice instance."""
    return BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=mock_coordinator)


@pytest.fixture
def bermuda_scanner(mock_coordinator):
    """Fixture for creating a BermudaDevice Scanner instance."""
    return BermudaDevice(address="11:22:33:44:55:66", coordinator=mock_coordinator)


def test_bermuda_device_initialization(bermuda_device):
    """Test BermudaDevice initialization."""
    assert bermuda_device.address == "aa:bb:cc:dd:ee:ff"
    assert bermuda_device.name.startswith("bermuda_")
    assert bermuda_device.area_icon == ICON_DEFAULT_AREA
    assert bermuda_device.floor_icon == ICON_DEFAULT_FLOOR
    assert bermuda_device.zone == "not_home"


def test_async_as_scanner_init(bermuda_scanner, mock_scanner):
    """Test async_as_scanner_init method."""
    bermuda_scanner.async_as_scanner_init(mock_scanner)
    assert bermuda_scanner._hascanner == mock_scanner
    assert bermuda_scanner.is_scanner is True
    assert bermuda_scanner.is_remote_scanner is False


def test_async_as_scanner_update(bermuda_scanner, mock_scanner):
    """Test async_as_scanner_update method."""
    bermuda_scanner.async_as_scanner_update(mock_scanner)
    assert bermuda_scanner.last_seen > 0


def test_async_as_scanner_get_stamp(bermuda_scanner, mock_scanner, mock_remote_scanner):
    """Test async_as_scanner_get_stamp method."""
    bermuda_scanner.async_as_scanner_init(mock_scanner)
    bermuda_scanner.stamps = {"AA:BB:CC:DD:EE:FF": 123.45}

    stamp = bermuda_scanner.async_as_scanner_get_stamp("AA:bb:CC:DD:EE:FF")
    assert stamp is None

    bermuda_scanner.async_as_scanner_init(mock_remote_scanner)

    stamp = bermuda_scanner.async_as_scanner_get_stamp("AA:bb:CC:DD:EE:FF")
    assert stamp == 123.45

    stamp = bermuda_scanner.async_as_scanner_get_stamp("AA:BB:CC:DD:E1:FF")
    assert stamp is None


def test_make_name(bermuda_device):
    """Test make_name method."""
    bermuda_device.name_by_user = "Custom Name"
    name = bermuda_device.make_name()
    assert name == "Custom Name"
    assert bermuda_device.name == "Custom Name"


def test_process_advertisement(bermuda_device, bermuda_scanner):
    """Test process_advertisement method."""
    advertisement_data = MagicMock()
    bermuda_device.process_advertisement(bermuda_scanner, advertisement_data)
    assert len(bermuda_device.adverts) == 1


# def test_process_manufacturer_data(bermuda_device):
#     """Test process_manufacturer_data method."""
#     mock_advert = MagicMock()
#     mock_advert.service_uuids = ["0000abcd-0000-1000-8000-00805f9b34fb"]
#     mock_advert.manufacturer_data = [{"004C": b"\x02\x15"}]
#     bermuda_device.process_manufacturer_data(mock_advert)
#     assert bermuda_device.manufacturer == "Apple Inc."


def test_to_dict(bermuda_device):
    """Test to_dict method."""
    device_dict = bermuda_device.to_dict()
    assert isinstance(device_dict, dict)
    assert device_dict["address"] == "aa:bb:cc:dd:ee:ff"


def test_repr(bermuda_device):
    """Test __repr__ method."""
    repr_str = repr(bermuda_device)
    assert repr_str == f"{bermuda_device.name} [{bermuda_device.address}]"


# ---------------------------------------------------------------------------
# Scanner resolution against the real registries
# ---------------------------------------------------------------------------

UNIFI_MAC = "02:aa:bb:cc:dd:13"  # another integration's entry inside the proxy's MAC window


@pytest.fixture
def registry_coordinator(hass):
    """Coordinator mock backed by the real device registry."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.dr = dr.async_get(hass)
    coordinator.options = {}
    coordinator.hass_version_min_2025_4 = True
    return coordinator


def _init_scanner(address, coordinator):
    scanner = MagicMock(spec=BaseHaRemoteScanner)
    scanner.source = address
    scanner.name = f"scanner {address}"
    scanner.time_since_last_detection.return_value = 5.0
    device = BermudaDevice(address=address, coordinator=coordinator)
    device.async_as_scanner_init(scanner)
    return device


def _esphome_proxy(hass, name, entry_id, ble_mac, mac, unique_id):
    """An esphome entry that reports ble_mac as its scanner address, with its registry devices."""
    entry = MockConfigEntry(
        domain="esphome", data={"bluetooth_mac_address": ble_mac}, entry_id=entry_id, unique_id=unique_id
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(config_entry_id=entry.entry_id, connections={("mac", mac)}, name=name)
    registry.async_get_or_create(config_entry_id=entry.entry_id, connections={("bluetooth", ble_mac)}, name="bt")


async def test_scanner_resolves_through_esphome_entry(hass, registry_coordinator):
    """A proxy whose MACs don't follow the Espressif layout is linked through its esphome entry."""
    _esphome_proxy(hass, "Desk Tablet", "kiosk", "02:11:22:33:44:55", "02:66:77:88:99:aa", "02:66:77:88:99:aa")

    scanner = _init_scanner("02:11:22:33:44:55", registry_coordinator)

    assert scanner.address_wifi_mac == "02:66:77:88:99:aa"
    assert scanner.unique_id == "02:66:77:88:99:aa"
    assert scanner.address_ble_mac == "02:11:22:33:44:55"
    assert scanner.name == "Desk Tablet"


async def test_esphome_link_wins_over_other_device_in_mac_range(hass, registry_coordinator):
    """Another integration's entry in the MAC range no longer supplies the scanner's identity or name."""
    _esphome_proxy(
        hass, "Breakfast Bluetooth Proxy", "esp", "02:AA:BB:CC:DD:12", "02:aa:bb:cc:dd:10", "02:aa:bb:cc:dd:10"
    )
    unifi = MockConfigEntry(domain="unifi", entry_id="unifi")
    unifi.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=unifi.entry_id, connections={("mac", UNIFI_MAC)}, name="proxy"
    )

    scanner = _init_scanner("02:AA:BB:CC:DD:12", registry_coordinator)

    assert scanner.unique_id == scanner.address_wifi_mac == "02:aa:bb:cc:dd:10"
    assert scanner.name == "Breakfast Bluetooth Proxy"


async def test_scanner_without_matching_entry_resolves_by_mac_offset(hass, registry_coordinator):
    """Without a matching entry the Espressif-layout search still runs and keeps the WiFi-MAC unique_id."""
    _esphome_proxy(hass, "Kitchen Proxy", "esp", "AA:AA:AA:AA:AA:AA", "02:aa:bb:cc:dd:20", "02:aa:bb:cc:dd:20")

    scanner = _init_scanner("02:AA:BB:CC:DD:22", registry_coordinator)

    assert scanner.unique_id == scanner.address_wifi_mac == "02:aa:bb:cc:dd:20"
