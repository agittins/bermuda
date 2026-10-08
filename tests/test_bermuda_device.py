"""
Tests for BermudaDevice class in bermuda_device.py.
"""

import pytest
from unittest.mock import MagicMock, patch
from homeassistant.components.bluetooth import BaseHaScanner, BaseHaRemoteScanner
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


def _registry_device(device_id, name, connections, via_device_id=None):
    """Minimal device-registry stand-in."""
    device = MagicMock()
    device.id = device_id
    device.name = name
    device.name_by_user = None
    device.connections = connections
    device.via_device_id = via_device_id
    device.area_id = None
    return device


def test_scanner_name_prefers_via_device_over_shared_mac(mock_coordinator, mock_scanner):
    """UniFi and Shelly share a MAC; the Bluetooth via_device_id selects Shelly.

    The UniFi entry is last, which used to win because the loop kept the last
    MAC match.
    """
    wifi = "54:43:b2:3d:ac:18"
    ble = "54:43:B2:3D:AC:1A"
    shelly = _registry_device("shelly-id", "Rollo EG Wohnzimmer Fenster", {("mac", wifi)})
    unifi = _registry_device("unifi-id", "shellyplus2pm-5443b23dac18", {("mac", wifi)})
    bluetooth = _registry_device(
        "bt-id",
        f"Rollo EG Wohnzimmer Fenster ({ble})",
        {("bluetooth", ble)},
        via_device_id="shelly-id",
    )
    mock_coordinator.dr.devices.get_entries.return_value = [bluetooth, shelly, unifi]
    mock_coordinator.dr.async_get.return_value = shelly

    scanner = BermudaDevice(address=ble, coordinator=mock_coordinator)
    scanner._hascanner = mock_scanner
    mock_scanner.source = ble
    scanner.async_as_scanner_resolve_device_entries()

    assert scanner.name == "Rollo EG Wohnzimmer Fenster"
    assert scanner.address_wifi_mac == wifi


def test_scanner_name_falls_back_to_first_mac_without_via(mock_coordinator, mock_scanner):
    """Without via_device_id, the first MAC match is used, not the last."""
    wifi = "54:43:b2:3d:ac:18"
    ble = "54:43:B2:3D:AC:1A"
    shelly = _registry_device("shelly-id", "Rollo EG Wohnzimmer Fenster", {("mac", wifi)})
    unifi = _registry_device("unifi-id", "shellyplus2pm-5443b23dac18", {("mac", wifi)})
    bluetooth = _registry_device("bt-id", "scanner", {("bluetooth", ble)}, via_device_id=None)
    mock_coordinator.dr.devices.get_entries.return_value = [unifi, shelly, bluetooth]
    mock_coordinator.dr.async_get.return_value = None

    scanner = BermudaDevice(address=ble, coordinator=mock_coordinator)
    scanner._hascanner = mock_scanner
    scanner.async_as_scanner_resolve_device_entries()

    assert scanner.name == "shellyplus2pm-5443b23dac18"


def test_repr(bermuda_device):
    """Test __repr__ method."""
    repr_str = repr(bermuda_device)
    assert repr_str == f"{bermuda_device.name} [{bermuda_device.address}]"
