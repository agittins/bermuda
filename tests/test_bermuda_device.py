"""
Tests for BermudaDevice class in bermuda_device.py.
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from homeassistant.components.bluetooth import BaseHaScanner, BaseHaRemoteScanner
from homeassistant.helpers.json import JSONEncoder
from custom_components.bermuda.bermuda_advert import BermudaAdvert
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


def test_update_area_and_floor_reads_the_floor_level(bermuda_device):
    """floor_level comes from the floor registry entry (it used to be assigned to itself)."""
    bermuda_device.ar.async_get_area.return_value = SimpleNamespace(name="Loft", icon=None, floor_id="top")
    bermuda_device.fr.async_get_floor.return_value = SimpleNamespace(name="Top", icon=None, level=2)
    bermuda_device._update_area_and_floor("loft")
    assert (bermuda_device.area_id, bermuda_device.area_name) == ("loft", "Loft")
    assert (bermuda_device.floor_id, bermuda_device.floor_name, bermuda_device.floor_level) == ("top", "Top", 2)
    # An area without a floor clears the level along with the rest.
    bermuda_device.ar.async_get_area.return_value = SimpleNamespace(name="Yard", icon=None, floor_id=None)
    bermuda_device._update_area_and_floor("yard")
    assert (bermuda_device.floor_id, bermuda_device.floor_name, bermuda_device.floor_level) == (None, None, None)


def test_update_area_and_floor_with_a_deleted_area_clears_area_id(bermuda_device):
    """An area id that no longer resolves must not stay on the device."""
    bermuda_device.ar.async_get_area.return_value = SimpleNamespace(name="Loft", icon=None, floor_id="top")
    bermuda_device.fr.async_get_floor.return_value = SimpleNamespace(name="Top", icon=None, level=2)
    bermuda_device._update_area_and_floor("loft")
    bermuda_device.ar.async_get_area.return_value = None
    bermuda_device._update_area_and_floor("loft")
    assert bermuda_device.area is None
    assert bermuda_device.area_id is None
    assert bermuda_device.area_name.startswith("Invalid Area")
    assert (bermuda_device.floor_id, bermuda_device.floor_name, bermuda_device.floor_level) == (None, None, None)


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


def test_async_as_scanner_init_force_rereads_registry_entries(bermuda_scanner, mock_coordinator, mock_scanner):
    """A forced init on a known scanner re-reads its registry entries.

    Changing a proxy's area in Home Assistant does not replace its scanner
    object, so an init that only reacts to a new object never saw the change
    (agittins/bermuda#856).
    """
    with patch.object(bermuda_scanner, "async_as_scanner_resolve_device_entries") as resolve:
        bermuda_scanner.async_as_scanner_init(mock_scanner)
        assert resolve.call_count == 1  # first set-up

        bermuda_scanner.async_as_scanner_init(mock_scanner)
        assert resolve.call_count == 1  # same object, not forced: nothing to do

        bermuda_scanner.async_as_scanner_init(mock_scanner, force=True)
        assert resolve.call_count == 2  # forced: read the registry again

    # Re-reading must not announce the scanner as new again.
    assert mock_coordinator.scanner_list_add.call_count == 1


def test_scanner_resolves_wifi_mac_before_announcing(bermuda_scanner, mock_coordinator, mock_remote_scanner):
    """The roster-changed signal must see the scanner's resolved wifi mac.

    scanner_list_add() dispatches SIGNAL_SCANNERS_CHANGED, and the sensor platform
    skips scanners whose wifi mac is still None. A scanner that registers after
    startup gets no other signal, so announcing it before resolving left it
    without per-scanner entities until a reload.
    """
    wifi_mac = "11:22:33:44:55:64"
    mock_coordinator.dr.devices.get_entries.return_value = [
        SimpleNamespace(
            id="shelly_device",
            name="Shelly",
            name_by_user=None,
            area_id=None,
            connections={("mac", wifi_mac)},
        )
    ]
    seen_at_announce = []
    mock_coordinator.scanner_list_add.side_effect = lambda scanner: seen_at_announce.append(scanner.address_wifi_mac)

    bermuda_scanner.async_as_scanner_init(mock_remote_scanner)

    assert seen_at_announce == [wifi_mac]


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


def test_to_dict_is_json_serialisable_with_area_advert(bermuda_device, bermuda_scanner):
    """to_dict() output must be JSON-serialisable, including area_advert.

    Regression test: to_dict() passed the area_advert BermudaAdvert object
    through unconverted. That happened to survive JSON encoding only because
    BermudaAdvert subclassed dict (it serialised as a useless empty {}).
    Once that unused dict base was removed, the dump_devices service call and
    the config-entry diagnostics download - which both serialise this output -
    failed outright with "Unable to serialize to JSON. Bad data found at
    $.service_response.<device>.area_advert".
    """
    advertisement_data = MagicMock()
    bermuda_device.process_advertisement(bermuda_scanner, advertisement_data)
    advert = next(iter(bermuda_device.adverts.values()))
    bermuda_device.area_advert = advert

    device_dict = bermuda_device.to_dict()

    # The advert must be reduced to an identifying string, not passed through
    # as a BermudaAdvert object (which the JSON encoder cannot handle, and
    # which the old dict-subclass behaviour silently rendered as an empty {}).
    assert isinstance(device_dict["area_advert"], str)
    assert device_dict["area_advert"] == repr(advert)

    # That field must survive the encoder HA serialises service responses with.
    json.dumps(device_dict["area_advert"], cls=JSONEncoder)


def test_repr(bermuda_device):
    """Test __repr__ method."""
    repr_str = repr(bermuda_device)
    assert repr_str == f"{bermuda_device.name} [{bermuda_device.address}]"


def test_irk_device_is_seeded_from_the_sighting_core_already_holds(mock_coordinator):
    """After a restart core only calls back on the next delivered advert, which
    can be minutes away for a phone whose payload has not changed. A new IRK
    device takes the current address core already knows."""
    info = MagicMock()
    info.address = "4A:BB:CC:DD:EE:FF"
    with patch("custom_components.bermuda.bermuda_device.pble_coordinator") as pble:
        pble.async_last_service_info.return_value = info
        device = BermudaDevice(address="0123456789abcdef0123456789abcdef", coordinator=mock_coordinator)
    assert device.metadevice_sources == ["4a:bb:cc:dd:ee:ff"]


def test_irk_device_with_nothing_heard_yet_starts_empty(mock_coordinator):
    with patch("custom_components.bermuda.bermuda_device.pble_coordinator") as pble:
        pble.async_last_service_info.return_value = None
        device = BermudaDevice(address="0123456789abcdef0123456789abcdef", coordinator=mock_coordinator)
    assert device.metadevice_sources == []


def test_metadevice_does_not_recalculate_its_sources_adverts(mock_coordinator, bermuda_scanner):
    """Each advert is calculated once per update cycle, even when a metadevice shares it.

    update_metadevices() copies a source device's advert *objects* into the metadevice,
    and both devices are in coordinator.devices. If both calculated the shared advert,
    it would add two history entries per cycle and halve the smoothing window.
    """
    source = BermudaDevice(address="AA:BB:CC:DD:EE:01", coordinator=mock_coordinator)
    source.process_advertisement(bermuda_scanner, MagicMock())
    with patch("custom_components.bermuda.bermuda_device.pble_coordinator"):
        metadevice = BermudaDevice(address="0123456789abcdef0123456789abcdef", coordinator=mock_coordinator)
    metadevice.adverts.update(source.adverts)  # as update_metadevices() does
    (shared,) = source.adverts.values()

    with patch.object(BermudaAdvert, "calculate_data", autospec=True) as calculate:
        source.calculate_data()
        metadevice.calculate_data()

    assert [call.args[0] for call in calculate.call_args_list] == [shared]
