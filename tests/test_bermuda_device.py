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


def test_repr(bermuda_device):
    """Test __repr__ method."""
    repr_str = repr(bermuda_device)
    assert repr_str == f"{bermuda_device.name} [{bermuda_device.address}]"


def test_scanner_registry_match_prefers_own_device_over_a_mac_neighbour(mock_coordinator, mock_remote_scanner):
    """Regression: the +-3 octet registry search also matches a NEIGHBOUR.

    An ESPHome proxy at BLE ..:4a (WiFi ..:48) and an unrelated ESPHome light
    at WiFi ..:4c both fall inside the window, and the last entry the
    registry returned used to win - so the proxy took the light's name,
    unique_id and wifi mac (and could never be placed as the receiver it is).
    The entry whose bluetooth connection IS the scanner address must win,
    whatever order the registry returns them in.
    """
    from types import SimpleNamespace

    proxy = SimpleNamespace(
        id="dev-proxy",
        name="Eilee Bedroom RRN00 4e8948",
        name_by_user=None,
        area_id="eilee_bedroom",
        connections={("mac", "dc:06:75:4e:89:48"), ("bluetooth", "dc:06:75:4e:89:4a")},
    )
    light = SimpleNamespace(
        id="dev-light",
        name="Basement Lumary 4e894c",
        name_by_user=None,
        area_id="basement",
        connections={("mac", "dc:06:75:4e:89:4c")},
    )
    for order in ([proxy, light], [light, proxy]):
        mock_coordinator.dr.devices.get_entries = MagicMock(return_value=list(order))
        scanner = BermudaDevice(address="DC:06:75:4E:89:4A", coordinator=mock_coordinator)
        scanner._hascanner = mock_remote_scanner
        scanner.async_as_scanner_resolve_device_entries()
        assert scanner.unique_id == "dc:06:75:4e:89:48", order
        assert scanner.address_wifi_mac == "dc:06:75:4e:89:48"
        assert scanner.address_ble_mac == "dc:06:75:4e:89:4a"
        assert scanner.entry_id == "dev-proxy"
        assert scanner.name_devreg == "Eilee Bedroom RRN00 4e8948"


def test_scanner_registry_match_falls_back_to_the_wifi_plus_two_rule(mock_coordinator, mock_remote_scanner):
    """With no bluetooth entry at all (older cores), the espressif BLE = WiFi + 2
    neighbour beats any other offset."""
    from types import SimpleNamespace

    proxy = SimpleNamespace(
        id="p", name="Proxy", name_by_user=None, area_id=None, connections={("mac", "dc:06:75:4e:89:48")}
    )
    light = SimpleNamespace(
        id="l", name="Light", name_by_user=None, area_id=None, connections={("mac", "dc:06:75:4e:89:4c")}
    )
    mock_coordinator.dr.devices.get_entries = MagicMock(return_value=[light, proxy])
    scanner = BermudaDevice(address="dc:06:75:4e:89:4a", coordinator=mock_coordinator)
    scanner._hascanner = mock_remote_scanner
    scanner.async_as_scanner_resolve_device_entries()
    assert scanner.unique_id == "dc:06:75:4e:89:48"
    assert scanner.name_devreg == "Proxy"


def test_scanner_registry_match_ignores_a_neighbour_two_above(mock_coordinator, mock_remote_scanner):
    """Issue #859: two ESP32-S3 proxies with adjacent MACs. Proxy A's BLE MAC
    (..:6e) is its own WiFi MAC (..:6c) + 2 and also proxy B's WiFi MAC
    (..:70) - 2, so both ESPHome entries fall in the +-3 window. A must keep
    its own name and WiFi MAC whatever order the registry returns them in."""
    from types import SimpleNamespace

    bluetooth_a = SimpleNamespace(
        id="bt-a",
        name="Bluetooth Proxy 44696c",
        name_by_user=None,
        area_id="bedroom",
        connections={("bluetooth", "1C:69:7A:44:69:6E")},
    )
    esphome_a = SimpleNamespace(
        id="esp-a",
        name="ESP32-S3 Bluetooth Proxy 44696c",
        name_by_user=None,
        area_id="bedroom",
        connections={("mac", "1c:69:7a:44:69:6c")},
    )
    esphome_b = SimpleNamespace(
        id="esp-b",
        name="ESP32-S3 Bluetooth Proxy 446970",
        name_by_user=None,
        area_id="bathroom",
        connections={("mac", "1c:69:7a:44:69:70")},
    )
    for order in (
        [bluetooth_a, esphome_a, esphome_b],
        [esphome_b, esphome_a, bluetooth_a],
        [bluetooth_a, esphome_b, esphome_a],
    ):
        mock_coordinator.dr.devices.get_entries = MagicMock(return_value=list(order))
        scanner = BermudaDevice(address="1C:69:7A:44:69:6E", coordinator=mock_coordinator)
        scanner._hascanner = mock_remote_scanner
        scanner.async_as_scanner_resolve_device_entries()
        assert scanner.address_wifi_mac == "1c:69:7a:44:69:6c", order
        assert scanner.address_ble_mac == "1c:69:7a:44:69:6e"
        assert scanner.unique_id == "1c:69:7a:44:69:6c"
        assert scanner.name_devreg == "ESP32-S3 Bluetooth Proxy 44696c"


def _with_domains(mock_coordinator, domains):
    """Give the mock coordinator a config-entry lookup: entry id -> domain."""
    from types import SimpleNamespace

    mock_coordinator.hass.config_entries.async_get_entry = MagicMock(
        side_effect=lambda entry_id: SimpleNamespace(domain=domains[entry_id]) if entry_id in domains else None
    )


def test_scanner_registry_match_prefers_the_scanner_integration_over_a_router_with_the_same_mac(
    mock_coordinator, mock_remote_scanner
):
    """Issue #722: a router integration (TP-Link here) registers the same proxy
    under the same WiFi MAC, named "linux" and with no area. Both entries are
    the same distance from the BLE address, so registry order used to decide:
    router first meant no area (the repair) and the router's name. The
    ESPHome entry must win whatever the order, and with the core Bluetooth
    entry present the name must still be ESPHome's, not the router's."""
    from types import SimpleNamespace

    _with_domains(mock_coordinator, {"e-esp": "esphome", "e-tp": "tplink", "e-bt": "bluetooth"})
    esphome = SimpleNamespace(
        id="esp",
        name="TECHO5 proxy",
        name_by_user=None,
        area_id="office",
        connections={("mac", "aa:bb:cc:dd:ee:10")},
        config_entries={"e-esp"},
    )
    router = SimpleNamespace(
        id="tplink",
        name="linux",
        name_by_user=None,
        area_id=None,
        connections={("mac", "aa:bb:cc:dd:ee:10")},
        config_entries={"e-tp"},
    )
    bluetooth = SimpleNamespace(
        id="bt",
        name="Bluetooth proxy",
        name_by_user=None,
        area_id="office",
        connections={("bluetooth", "AA:BB:CC:DD:EE:12")},
        config_entries={"e-bt"},
    )
    for order, entry in (
        ([esphome, router], "esp"),
        ([router, esphome], "esp"),
        ([bluetooth, router, esphome], "bt"),
        ([router, esphome, bluetooth], "bt"),
    ):
        mock_coordinator.dr.devices.get_entries = MagicMock(return_value=list(order))
        scanner = BermudaDevice(address="AA:BB:CC:DD:EE:12", coordinator=mock_coordinator)
        scanner._hascanner = mock_remote_scanner
        scanner.async_as_scanner_resolve_device_entries()
        ids = [d.id for d in order]
        assert scanner.entry_id == entry, ids
        assert scanner.area_id == "office", ids
        assert scanner.name_devreg == "TECHO5 proxy", ids
        assert scanner.address_wifi_mac == "aa:bb:cc:dd:ee:10", ids


def test_scanner_area_comes_from_another_entry_for_the_same_hardware(mock_coordinator, mock_remote_scanner):
    """The workaround people use for #722 - giving the router's entry an area -
    keeps working: the scanner's own entry has none, the same MAC's other
    entry has one, and that area is the scanner's."""
    from types import SimpleNamespace

    _with_domains(mock_coordinator, {"e-esp": "esphome", "e-tp": "tplink"})
    esphome = SimpleNamespace(
        id="esp",
        name="TECHO5 proxy",
        name_by_user=None,
        area_id=None,
        connections={("mac", "aa:bb:cc:dd:ee:10")},
        config_entries={"e-esp"},
    )
    router = SimpleNamespace(
        id="tplink",
        name="linux",
        name_by_user=None,
        area_id="office",
        connections={("mac", "aa:bb:cc:dd:ee:10")},
        config_entries={"e-tp"},
    )
    neighbour = SimpleNamespace(
        id="light",
        name="Hall light",
        name_by_user=None,
        area_id="hall",
        connections={("mac", "aa:bb:cc:dd:ee:14")},
        config_entries={"e-esp"},
    )
    for order in ([esphome, router, neighbour], [neighbour, router, esphome]):
        mock_coordinator.dr.devices.get_entries = MagicMock(return_value=list(order))
        scanner = BermudaDevice(address="AA:BB:CC:DD:EE:12", coordinator=mock_coordinator)
        scanner._hascanner = mock_remote_scanner
        scanner.async_as_scanner_resolve_device_entries()
        assert scanner.entry_id == "esp" and scanner.name_devreg == "TECHO5 proxy"
        # The router's area (same MAC), never the neighbour light's (different MAC).
        assert scanner.area_id == "office", [d.id for d in order]


def test_a_re_resolve_replaces_an_earlier_winners_entry_id(mock_coordinator, mock_remote_scanner):
    """A scanner first resolved to the router's entry (the scanner
    integration's entry not registered yet), then re-resolved once it is,
    must carry the new winner's entry_id, not keep the router's."""
    from types import SimpleNamespace

    _with_domains(mock_coordinator, {"e-esp": "esphome", "e-tp": "tplink"})
    esphome = SimpleNamespace(
        id="esp",
        name="TECHO5 proxy",
        name_by_user=None,
        area_id="office",
        connections={("mac", "aa:bb:cc:dd:ee:10")},
        config_entries={"e-esp"},
    )
    router = SimpleNamespace(
        id="tplink",
        name="linux",
        name_by_user=None,
        area_id=None,
        connections={("mac", "aa:bb:cc:dd:ee:10")},
        config_entries={"e-tp"},
    )
    scanner = BermudaDevice(address="AA:BB:CC:DD:EE:12", coordinator=mock_coordinator)
    scanner._hascanner = mock_remote_scanner
    mock_coordinator.dr.devices.get_entries = MagicMock(return_value=[router])
    scanner.async_as_scanner_resolve_device_entries()
    assert scanner.entry_id == "tplink"
    mock_coordinator.dr.devices.get_entries = MagicMock(return_value=[router, esphome])
    scanner.async_as_scanner_resolve_device_entries()
    assert scanner.entry_id == "esp" and scanner.name_devreg == "TECHO5 proxy" and scanner.area_id == "office"


def test_scanner_integration_is_read_from_config_entry_id_on_current_cores(mock_coordinator):
    """From Home Assistant 2026.10 a device has one ``config_entry_id`` and
    reading ``config_entries`` is deprecated: use the new attribute, and do
    not touch the old one when the new one is there."""
    from types import SimpleNamespace

    class NewCoreDevice:
        config_entry_id = "e-esp"

        @property
        def config_entries(self):
            raise AssertionError("config_entries read on a core that has config_entry_id")

    _with_domains(mock_coordinator, {"e-esp": "esphome", "e-tp": "tplink"})
    scanner = BermudaDevice(address="AA:BB:CC:DD:EE:12", coordinator=mock_coordinator)
    assert scanner._from_scanner_integration(NewCoreDevice()) is True
    # An older core: no config_entry_id, the set of entries is read instead.
    assert scanner._from_scanner_integration(SimpleNamespace(config_entries={"e-tp"})) is False
    assert scanner._from_scanner_integration(SimpleNamespace(config_entries={"e-tp", "e-esp"})) is True
