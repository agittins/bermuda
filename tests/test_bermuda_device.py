"""
Tests for BermudaDevice class in bermuda_device.py.
"""

import pytest
from types import SimpleNamespace
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


def test_repr(bermuda_device):
    """Test __repr__ method."""
    repr_str = repr(bermuda_device)
    assert repr_str == f"{bermuda_device.name} [{bermuda_device.address}]"


@pytest.mark.parametrize("modern_registry", [True, False])
@pytest.mark.parametrize("device_types", [("bluetooth",), ("mac",), ("bluetooth", "mac"), ()])
def test_scanner_resolves_all_device_entries(
    bermuda_scanner, mock_coordinator, mock_scanner, modern_registry, device_types
):
    """Preserve scanner identity and metadata with both registry lookup APIs."""
    entries = {
        "bluetooth": SimpleNamespace(
            id="bluetooth_device",
            connections={("bluetooth", "11:22:33:44:55:66")},
            name="Bluetooth scanner",
            name_by_user="Bluetooth user name",
            area_id="bluetooth_area",
        ),
        "mac": SimpleNamespace(
            id="network_device",
            connections={("mac", "11:22:33:44:55:64")},
            name="ESPHome scanner",
            name_by_user="ESPHome user name",
            area_id="network_area",
        ),
    }
    lookup = MagicMock(return_value=[entries[key] for key in device_types])
    if modern_registry:
        # No devices attribute: any deprecated container access must fail.
        registry = SimpleNamespace(async_get_devices=lookup)
    else:
        registry = SimpleNamespace(devices=SimpleNamespace(get_entries=lookup))
    mock_coordinator.dr = registry
    bermuda_scanner._hascanner = mock_scanner

    with patch.object(bermuda_scanner, "_update_area_and_floor") as update_area:
        bermuda_scanner.async_as_scanner_resolve_device_entries()

    expected_connections = {
        (kind, f"11:22:33:44:55:{suffix:02x}") for kind in ("bluetooth", "mac") for suffix in range(0x63, 0x69)
    }
    if modern_registry:
        lookup.assert_called_once_with(connections=expected_connections)
    else:
        lookup.assert_called_once_with(None, connections=expected_connections)
    if not device_types:
        update_area.assert_not_called()
        return

    preferred = entries["bluetooth" if "bluetooth" in device_types else "mac"]
    update_area.assert_called_once_with(preferred.area_id)
    assert bermuda_scanner.entry_id == preferred.id
    assert bermuda_scanner.name_by_user == preferred.name_by_user
    assert bermuda_scanner.name_devreg == entries["mac" if "mac" in device_types else "bluetooth"].name
    assert bermuda_scanner.unique_id == ("11:22:33:44:55:64" if "mac" in device_types else "11:22:33:44:55:66")
    assert bermuda_scanner.address_ble_mac == (
        "11:22:33:44:55:66" if "bluetooth" in device_types else "11:22:33:44:55:64"
    )
    assert bermuda_scanner.address_wifi_mac == ("11:22:33:44:55:64" if "mac" in device_types else None)


async def test_scanner_resolves_real_registry(
    hass, device_registry, bermuda_scanner, mock_coordinator, mock_scanner, caplog
):
    """Resolve separate Bluetooth and ESPHome devices through HA's registry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    for domain, address in (("bluetooth", "11:22:33:44:55:66"), ("esphome", "11:22:33:44:55:64")):
        entry = MockConfigEntry(domain=domain)
        entry.add_to_hass(hass)
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={("bluetooth" if domain == "bluetooth" else "mac", address)},
            name=domain,
        )

    mock_coordinator.dr = device_registry
    bermuda_scanner._hascanner = mock_scanner
    bermuda_scanner.async_as_scanner_resolve_device_entries()

    assert bermuda_scanner.unique_id == "11:22:33:44:55:64"
    assert bermuda_scanner.address_ble_mac == "11:22:33:44:55:66"
    assert bermuda_scanner.name_devreg == "esphome"
    assert "uses `device_registry.devices`" not in caplog.text
