"""
Additional coverage for BermudaDevice (bermuda_device.py).

These tests complement tests/test_bermuda_device.py and focus on methods
that were previously uncovered: iBeacon manufacturer-data parsing,
make_name precedence, scanner device-entry resolution, get_scanner,
apply_scanner_selection and BLE address-type detection.

TESTS ONLY - the source is never modified. We never assert on unique_id
strings (they are frozen elsewhere).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESERVED,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    BDADDR_TYPE_RANDOM_STATIC,
    BDADDR_TYPE_RANDOM_UNRESOLVABLE,
    ICON_DEFAULT_AREA,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_PRIVATE_BLE_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
)


def make_coordinator():
    """A MagicMock coordinator usable by BermudaDevice.__init__.

    BermudaDevice.__init__ calls ar.async_get(hass) / fr.async_get(hass)
    using coordinator.hass, and reads coordinator.options. We give it an
    irk_manager and a manufacturer lookup that returns (None, None) by
    default so address-type detection stays inert unless overridden.
    """
    coordinator = MagicMock()
    coordinator.options = {}
    coordinator.hass_version_min_2025_4 = True
    coordinator.irk_manager = MagicMock()
    coordinator.get_manufacturer_from_id.return_value = (None, None)
    return coordinator


@pytest.fixture
def mock_coordinator():
    return make_coordinator()


def build_ibeacon_manudata(uuid_hex: str, major: int, minor: int, power: int | None = -59) -> bytes:
    """Build an Apple iBeacon manufacturer-data payload.

    Layout consumed by process_manufacturer_data:
      byte 0    : 0x02  (iBeacon subtype)
      byte 1    : 0x15  (length, 21)
      bytes 2-17: 16-byte proximity UUID
      bytes 18-19: major (big-endian)
      bytes 20-21: minor (big-endian)
      byte 22   : measured power (signed) -- optional
    """
    uuid_bytes = bytes.fromhex(uuid_hex)
    assert len(uuid_bytes) == 16
    payload = b"\x02\x15" + uuid_bytes + major.to_bytes(2, "big") + minor.to_bytes(2, "big")
    if power is not None:
        payload += int(power).to_bytes(1, "big", signed=True)
    return payload


# ---------------------------------------------------------------------------
# Address-type detection (_async_process_address_type via __init__)
# ---------------------------------------------------------------------------


def test_address_type_random_unresolvable(mock_coordinator):
    """First nibble 0,1,2,3 -> top two bits 00 -> random unresolvable."""
    dev = BermudaDevice(address="00:11:22:33:44:55", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_RANDOM_UNRESOLVABLE


def test_address_type_random_resolvable_irk_checked(mock_coordinator):
    """First nibble 4-7 -> resolvable, and irk_manager.check_mac is invoked."""
    dev = BermudaDevice(address="40:11:22:33:44:55", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE
    mock_coordinator.irk_manager.check_mac.assert_called_once_with("40:11:22:33:44:55")


def test_address_type_random_reserved(mock_coordinator):
    """First nibble 8-B -> top two bits 10 -> reserved."""
    dev = BermudaDevice(address="80:11:22:33:44:55", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_RANDOM_RESERVED


def test_address_type_random_static(mock_coordinator):
    """First nibble C-F -> top two bits 11 -> random static."""
    dev = BermudaDevice(address="C0:11:22:33:44:55", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_RANDOM_STATIC


def test_address_type_normal_mac_applies_manufacturer(mock_coordinator):
    """A 'normal' (public) MAC fetches a manufacturer name from the OUI."""
    # nibble 'a' -> 1010 -> top two bits 10 ... wait that is reserved.
    # To reach the BDADDR_TYPE_OTHER branch the address must NOT be 17 chars
    # of mac form with len()==17. Public MACs are still 17 chars though, so
    # the only way to land in the final else is len != 17. Use a 12-char
    # un-separated form which mac_norm expands to a 17-char mac... that would
    # be len 17 again. Instead exercise OTHER via the documented path: the
    # final else triggers when address has 5 colons but len != 17 (e.g. a
    # short octet). mac_norm leaves odd forms unchanged.
    mock_coordinator.get_manufacturer_from_id.return_value = ("Acme Corp", False)
    dev = BermudaDevice(address="a:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_OTHER
    assert dev.manufacturer == "Acme Corp"


def test_address_type_ibeacon_metadevice(mock_coordinator):
    """A uuid_major_minor string is detected as an iBeacon meta-device."""
    addr = "0123456789abcdef0123456789abcdef_100_200"
    dev = BermudaDevice(address=addr, coordinator=mock_coordinator)
    assert dev.address_type == ADDR_TYPE_IBEACON
    assert METADEVICE_IBEACON_DEVICE in dev.metadevice_type
    assert dev.beacon_unique_id == addr


def test_address_type_irk_private_ble_device(mock_coordinator):
    """A 32-char hex string is an IRK and registers PBLE + internal callbacks."""
    irk_hex = "0123456789abcdef0123456789abcdef"
    pble_coord = MagicMock()
    with patch(
        "custom_components.bermuda.bermuda_device.pble_coordinator.async_get_coordinator",
        return_value=pble_coord,
    ) as mock_get_coord:
        dev = BermudaDevice(address=irk_hex, coordinator=mock_coordinator)

    assert dev.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE
    assert METADEVICE_PRIVATE_BLE_DEVICE in dev.metadevice_type
    assert dev.beacon_unique_id == irk_hex
    mock_get_coord.assert_called_once_with(mock_coordinator.hass)
    # PBLE tracking and our own IRK callback were both wired up.
    pble_coord.async_track_service_info.assert_called_once()
    mock_coordinator.irk_manager.register_irk_callback.assert_called_once()
    mock_coordinator.irk_manager.add_irk.assert_called_once()


def test_address_type_not_mac48(mock_coordinator):
    """A non-mac, non-beacon, non-irk string is flagged NOT_MAC48."""
    dev = BermudaDevice(address="not-a-mac-at-all", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_NOT_MAC48


# ---------------------------------------------------------------------------
# make_name precedence
# ---------------------------------------------------------------------------


def test_make_name_precedence_user_first(mock_coordinator):
    """name_by_user wins over every other source."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.name_by_user = "User Name"
    dev.name_devreg = "Devreg Name"
    dev.name_bt_local_name = "Local Name"
    dev.name_bt_serviceinfo = "ServiceInfo Name"
    assert dev.make_name() == "User Name"


def test_make_name_precedence_devreg_then_local_then_serviceinfo(mock_coordinator):
    """Falls through devreg -> bt local name -> bt serviceinfo -> beacon id."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.name_devreg = "Devreg Name"
    dev.name_bt_local_name = "Local Name"
    dev.name_bt_serviceinfo = "ServiceInfo Name"
    assert dev.make_name() == "Devreg Name"

    dev.name_devreg = None
    assert dev.make_name() == "Local Name"

    dev.name_bt_local_name = None
    assert dev.make_name() == "ServiceInfo Name"

    dev.name_bt_serviceinfo = None
    dev.beacon_unique_id = "beacon-id"
    assert dev.make_name() == "beacon-id"


def test_make_name_fallback_address_with_manufacturer(mock_coordinator):
    """With no friendly name, the address is prefixed by the manufacturer slug."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.manufacturer = "Acme Corp"
    name = dev.make_name()
    assert name.startswith("acme_corp_")
    assert "aa_bb_cc_dd_ee_ff" in name


def test_make_name_fallback_address_no_manufacturer(mock_coordinator):
    """With no name and no manufacturer, the bermuda domain prefixes the address."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.manufacturer = None
    name = dev.make_name()
    assert name.startswith("bermuda_")


def test_make_name_not_mac48_keeps_existing_name(mock_coordinator):
    """A NOT_MAC48 device with no friendly source keeps whatever name it had."""
    dev = BermudaDevice(address="not-a-mac", coordinator=mock_coordinator)
    assert dev.address_type == BDADDR_TYPE_NOT_MAC48
    before = dev.name
    # No friendly names set -> the elif guards against NOT_MAC48, so name is unchanged.
    assert dev.make_name() == before


# ---------------------------------------------------------------------------
# process_manufacturer_data
# ---------------------------------------------------------------------------


def test_process_manufacturer_data_full_ibeacon(mock_coordinator):
    """A full 23-byte Apple iBeacon parses uuid/major/minor/power and registers source."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    uuid_hex = "0123456789abcdef0123456789abcdef"
    manudata = build_ibeacon_manudata(uuid_hex, major=4660, minor=22136, power=-59)

    advert = SimpleNamespace(
        service_uuids=[],
        manufacturer_data=[{0x004C: manudata}],
    )
    dev.process_manufacturer_data(advert)

    assert METADEVICE_TYPE_IBEACON_SOURCE in dev.metadevice_type
    assert dev.beacon_uuid == uuid_hex
    assert dev.beacon_major == "4660"
    assert dev.beacon_minor == "22136"
    assert dev.beacon_power == -59
    assert dev.beacon_unique_id == f"{uuid_hex}_4660_22136"
    mock_coordinator.register_ibeacon_source.assert_called_once_with(dev)


def test_process_manufacturer_data_ibeacon_without_power(mock_coordinator):
    """A 22-byte iBeacon (no tx_power) still parses uuid/major/minor, power stays None."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    uuid_hex = "ffffffffffffffffffffffffffffffff"
    manudata = build_ibeacon_manudata(uuid_hex, major=1, minor=2, power=None)
    assert len(manudata) == 22

    advert = SimpleNamespace(service_uuids=[], manufacturer_data=[{0x004C: manudata}])
    dev.process_manufacturer_data(advert)

    assert dev.beacon_uuid == uuid_hex
    assert dev.beacon_major == "1"
    assert dev.beacon_minor == "2"
    assert dev.beacon_power is None
    assert dev.beacon_unique_id == f"{uuid_hex}_1_2"


def test_process_manufacturer_data_apple_non_ibeacon_no_beacon(mock_coordinator):
    """Apple data whose first byte is not 0x02 (e.g. FindMy 0x12) is not an iBeacon."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    advert = SimpleNamespace(service_uuids=[], manufacturer_data=[{0x004C: b"\x12\x19" + b"\x00" * 20}])
    dev.process_manufacturer_data(advert)
    assert METADEVICE_TYPE_IBEACON_SOURCE not in dev.metadevice_type
    assert dev.beacon_uuid is None
    mock_coordinator.register_ibeacon_source.assert_not_called()


def test_process_manufacturer_data_sets_manufacturer_from_company_code(mock_coordinator):
    """A known company code populates manufacturer (and a non-generic one is kept)."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    mock_coordinator.get_manufacturer_from_id.return_value = ("Acme Corp", False)
    advert = SimpleNamespace(service_uuids=[], manufacturer_data=[{0x1234: b"\x00\x01"}])
    dev.process_manufacturer_data(advert)
    assert dev.manufacturer == "Acme Corp"


def test_process_manufacturer_data_service_uuid_updates_name(mock_coordinator):
    """A service uuid resolving to a manufacturer triggers a name refresh."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    mock_coordinator.get_manufacturer_from_id.return_value = ("Acme Corp", False)
    advert = SimpleNamespace(
        service_uuids=["0000abcd-0000-1000-8000-00805f9b34fb"],
        manufacturer_data=[],
    )
    dev.process_manufacturer_data(advert)
    assert dev.manufacturer == "Acme Corp"
    # get_manufacturer_from_id was called with the 16-bit short form "ABCD".
    mock_coordinator.get_manufacturer_from_id.assert_called_with("ABCD")
    assert dev.name.startswith("acme_corp_")


def test_process_manufacturer_data_generic_does_not_override_specific(mock_coordinator):
    """A generic manufacturer must not overwrite an already-specific one."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.manufacturer = "Specific Maker"
    mock_coordinator.get_manufacturer_from_id.return_value = ("Generic", True)
    advert = SimpleNamespace(service_uuids=[], manufacturer_data=[{0x1234: b"\x00"}])
    dev.process_manufacturer_data(advert)
    assert dev.manufacturer == "Specific Maker"


# ---------------------------------------------------------------------------
# get_scanner
# ---------------------------------------------------------------------------


def test_get_scanner_returns_most_recent_match(mock_coordinator):
    """get_scanner returns the advert with the newest stamp for a scanner address."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    older = SimpleNamespace(scanner_address="11:11:11:11:11:11", stamp=100.0)
    newer = SimpleNamespace(scanner_address="11:11:11:11:11:11", stamp=200.0)
    other = SimpleNamespace(scanner_address="22:22:22:22:22:22", stamp=999.0)
    dev.adverts = {
        ("aa:bb:cc:dd:ee:ff", "older"): older,
        ("aa:bb:cc:dd:ee:ff", "newer"): newer,
        ("aa:bb:cc:dd:ee:ff", "other"): other,
    }
    assert dev.get_scanner("11:11:11:11:11:11") is newer


def test_get_scanner_no_match(mock_coordinator):
    """get_scanner returns None when no advert matches the scanner address."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.adverts = {
        ("aa:bb:cc:dd:ee:ff", "x"): SimpleNamespace(scanner_address="33:33:33:33:33:33", stamp=10.0),
    }
    assert dev.get_scanner("99:99:99:99:99:99") is None


# ---------------------------------------------------------------------------
# apply_scanner_selection
# ---------------------------------------------------------------------------


def test_apply_scanner_selection_winner_sets_area_distance(mock_coordinator):
    """A winning advert applies its area, distance and rssi to the device."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    # Provide a real area so _update_area_and_floor populates the name.
    area = SimpleNamespace(name="Lounge", icon="mdi:sofa", floor_id=None)
    dev.ar = MagicMock()
    dev.ar.async_get_area.return_value = area

    advert = SimpleNamespace(rssi_distance=3.5, area_id="area-1", rssi=-60)
    dev.apply_scanner_selection(advert)

    assert dev.area_advert is advert
    assert dev.area_distance == 3.5
    assert dev.area_rssi == -60
    assert dev.area_name == "Lounge"
    assert dev.area_last_seen == "Lounge"


def test_apply_scanner_selection_none_clears_area(mock_coordinator):
    """Passing None (no winner) clears area attributes back to default."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev.area_advert = MagicMock()
    dev.area_distance = 1.0
    dev.area_rssi = -50
    dev.area_name = "Somewhere"

    dev.apply_scanner_selection(None)

    assert dev.area_advert is None
    assert dev.area_distance is None
    assert dev.area_rssi is None
    assert dev.area_name is None
    assert dev.area_icon == ICON_DEFAULT_AREA


def test_apply_scanner_selection_advert_without_distance_clears(mock_coordinator):
    """An advert whose rssi_distance is None is treated as 'no winner'."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    advert = SimpleNamespace(rssi_distance=None, area_id="area-1", rssi=-60)
    dev.apply_scanner_selection(advert)
    assert dev.area_advert is None
    assert dev.area_distance is None


# ---------------------------------------------------------------------------
# async_as_scanner_resolve_device_entries
# ---------------------------------------------------------------------------


def test_resolve_device_entries_no_hascanner_warns_and_returns(mock_coordinator):
    """With no ha_scanner the method logs and returns without touching state."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._hascanner = None
    # Should not raise even though coordinator.dr is never consulted.
    dev.async_as_scanner_resolve_device_entries()
    mock_coordinator.dr.devices.get_entries.assert_not_called()


def test_resolve_device_entries_not_found_logs_error(mock_coordinator):
    """When no devreg device is found the method bails after logging an error."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._hascanner = SimpleNamespace(source="aa:bb:cc:dd:ee:ff", name="ScannerName")
    mock_coordinator.dr.devices.get_entries.return_value = []
    # name_devreg should remain untouched (None) because we return early.
    dev.async_as_scanner_resolve_device_entries()
    assert dev.name_devreg is None


def test_resolve_device_entries_bt_and_mac_match(mock_coordinator):
    """A bluetooth + mac match populates names/area and updates the device name."""
    dev = BermudaDevice(address="aa:bb:cc:dd:ee:ff", coordinator=mock_coordinator)
    dev._hascanner = SimpleNamespace(source="aa:bb:cc:dd:ee:ff", name="HAScanner")

    bt_entry = SimpleNamespace(
        id="bt-id",
        area_id="area-1",
        name="BT Auto Name",
        name_by_user="My BT Name",
        connections={("bluetooth", "AA:BB:CC:DD:EE:FF")},
    )
    mac_entry = SimpleNamespace(
        id="mac-id",
        area_id="area-2",
        name="ESPHome Proxy",
        name_by_user=None,
        connections={("mac", "aa:bb:cc:dd:ee:fd")},
    )
    mock_coordinator.dr.devices.get_entries.return_value = [bt_entry, mac_entry]

    # Real area registry stub returning a usable area.
    area = SimpleNamespace(name="Lounge", icon="mdi:sofa", floor_id=None)
    dev.ar = MagicMock()
    dev.ar.async_get_area.return_value = area

    dev.async_as_scanner_resolve_device_entries()

    # MAC integration's autogenerated name is preferred over the BT one.
    assert dev.name_devreg == "ESPHome Proxy"
    # The BT user name wins for name_by_user.
    assert dev.name_by_user == "My BT Name"
    # bt area_id is preferred over the mac one.
    assert dev.area_id == "area-1"
    assert dev.area_name == "Lounge"
    # entry_id prefers the bt entry.
    assert dev.entry_id == "bt-id"
    # The device name reflects the user-supplied BT name.
    assert dev.name == "My BT Name"
