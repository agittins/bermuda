"""
Coverage for the scanner & metadevice internals of the coordinator.

Targets ``BermudaDataUpdateCoordinator`` methods that manage scanners and
metadevices:

- ``scanner_list_add`` / ``scanner_list_del``
- ``_async_purge_removed_scanners``
- ``_async_manage_repair_scanners_without_areas``
- ``_refresh_scanners`` / ``_rebuild_scanner_list`` (early-exit + rebuild)
- ``register_ibeacon_source``
- ``discover_private_ble_metadevices``
- ``update_metadevices``
- ``_async_gather_advert_data``

These methods read only a handful of attributes, so (mirroring
tests/test_redaction.py and tests/test_coordinator.py) we build a bare
coordinator with ``object.__new__`` and inject exactly the attributes each
method reads. This avoids needing a live bluetooth backend or a running
HomeAssistant for most of the logic.

TESTS ONLY - the source under custom_components/ is never modified. We never
assert on entity unique_id strings (frozen elsewhere).

Deliberately NOT covered here (would require a full live bluetooth backend or
real HA registries to be deterministic):
- The ``async_as_scanner_init`` device-registry resolution inside
  ``_rebuild_scanner_list`` (we drive the rebuild path with a fake
  bermuda_scanner whose async_as_scanner_init is a no-op).
- The real ``process_advertisement`` parsing inside ``_async_gather_advert_data``
  (we use mock scanner/source devices to exercise the loop's control flow).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import (
    CONF_DEVICES,
    DOMAIN,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    REPAIR_SCANNER_WITHOUT_AREA,
    SIGNAL_SCANNERS_CHANGED,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _HashableScanner:
    """A hashable stand-in for a BaseHaScanner / BermudaDevice in a set.

    SimpleNamespace is not hashable, so anything that lives inside one of the
    coordinator's sets (``_hascanners``, ``_scanners``) needs a real hashable
    object. Identity-based hashing matches how the production code stores them.
    """

    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


def _bare_coordinator():
    """A coordinator skeleton with the common collection attributes."""
    coord = object.__new__(BermudaDataUpdateCoordinator)
    coord.hass = MagicMock()
    coord.devices = {}
    coord.metadevices = {}
    coord._scanner_list = set()
    coord._scanners = set()
    coord._hascanners = set()
    coord.options = {CONF_DEVICES: []}
    coord.pb_state_sources = {}
    return coord


def _fake_source_device(**overrides):
    """A SimpleNamespace standing in for a BermudaDevice source/metadevice.

    Only carries the attributes the metadevice/ibeacon logic actually reads.
    """
    base = {
        "address": "aa:bb:cc:dd:ee:ff",
        "name": "src",
        "metadevice_type": set(),
        "metadevice_sources": [],
        "beacon_unique_id": None,
        "beacon_uuid": None,
        "beacon_major": None,
        "beacon_minor": None,
        "beacon_power": None,
        "name_bt_serviceinfo": None,
        "name_bt_local_name": None,
        "manufacturer": None,
        "ref_power": 0,
        "last_seen": 0,
        "adverts": {},
        "create_sensor": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# scanner_list_add / scanner_list_del
# --------------------------------------------------------------------------- #
def test_scanner_list_add_and_del_dispatch_signal() -> None:
    """Adding/removing a scanner mutates the sets and fires the changed signal."""
    coord = _bare_coordinator()
    scanner = _HashableScanner(address="11:22:33:44:55:66")

    with patch("custom_components.bermuda.coordinator_scanners.async_dispatcher_send") as send:
        coord.scanner_list_add(scanner)

    assert scanner.address in coord._scanner_list
    assert scanner in coord._scanners
    send.assert_called_once_with(coord.hass, SIGNAL_SCANNERS_CHANGED)
    # The public property exposes the underlying set.
    assert coord.scanner_list is coord._scanner_list

    with patch("custom_components.bermuda.coordinator_scanners.async_dispatcher_send") as send:
        coord.scanner_list_del(scanner)

    assert scanner.address not in coord._scanner_list
    assert scanner not in coord._scanners
    send.assert_called_once_with(coord.hass, SIGNAL_SCANNERS_CHANGED)


# --------------------------------------------------------------------------- #
# _async_purge_removed_scanners
# --------------------------------------------------------------------------- #
def test_purge_removed_scanners_demotes_only_absent() -> None:
    """Scanners no longer present in _hascanners are demoted, present ones kept."""
    coord = _bare_coordinator()

    keep = MagicMock()
    keep.address = "aa:aa:aa:aa:aa:aa"
    keep.name = "keep"
    keep.is_scanner = True

    drop = MagicMock()
    drop.address = "bb:bb:bb:bb:bb:bb"
    drop.name = "drop"
    drop.is_scanner = True

    not_a_scanner = MagicMock()
    not_a_scanner.address = "cc:cc:cc:cc:cc:cc"
    not_a_scanner.is_scanner = False

    coord.devices = {
        keep.address: keep,
        drop.address: drop,
        not_a_scanner.address: not_a_scanner,
    }
    # Only "keep" is still reported by HA's backend.
    coord._hascanners = {_HashableScanner(source=keep.address)}

    coord._async_purge_removed_scanners()

    drop.async_as_scanner_nolonger.assert_called_once_with()
    keep.async_as_scanner_nolonger.assert_not_called()
    not_a_scanner.async_as_scanner_nolonger.assert_not_called()


# --------------------------------------------------------------------------- #
# _async_manage_repair_scanners_without_areas
# --------------------------------------------------------------------------- #
def test_repair_raised_when_scanners_missing_areas() -> None:
    """A non-empty area-less list raises the repair (after deleting any stale one)."""
    coord = _bare_coordinator()
    coord._scanners_without_areas = []

    scannerlist = ["Living Room [aa:bb:cc:dd:ee:ff]"]
    with patch("custom_components.bermuda.coordinator_scanners.ir") as ir_mock:
        coord._async_manage_repair_scanners_without_areas(scannerlist)

    assert coord._scanners_without_areas == scannerlist
    ir_mock.async_delete_issue.assert_called_once_with(coord.hass, DOMAIN, REPAIR_SCANNER_WITHOUT_AREA)
    ir_mock.async_create_issue.assert_called_once()
    # Placeholder contains a bulleted version of the scanner name.
    kwargs = ir_mock.async_create_issue.call_args.kwargs
    assert kwargs["translation_key"] == REPAIR_SCANNER_WITHOUT_AREA
    assert "- Living Room [aa:bb:cc:dd:ee:ff]\n" == kwargs["translation_placeholders"]["scannerlist"]


def test_repair_cleared_when_list_becomes_empty() -> None:
    """Transitioning from a populated list to empty deletes (and never re-raises)."""
    coord = _bare_coordinator()
    coord._scanners_without_areas = ["Old [aa:bb:cc:dd:ee:ff]"]

    with patch("custom_components.bermuda.coordinator_scanners.ir") as ir_mock:
        coord._async_manage_repair_scanners_without_areas([])

    assert coord._scanners_without_areas == []
    ir_mock.async_delete_issue.assert_called_once_with(coord.hass, DOMAIN, REPAIR_SCANNER_WITHOUT_AREA)
    ir_mock.async_create_issue.assert_not_called()


def test_repair_noop_when_list_unchanged() -> None:
    """If the list is identical to last time, neither delete nor create runs."""
    coord = _bare_coordinator()
    existing = ["Same [aa:bb:cc:dd:ee:ff]"]
    coord._scanners_without_areas = existing

    with patch("custom_components.bermuda.coordinator_scanners.ir") as ir_mock:
        coord._async_manage_repair_scanners_without_areas(list(existing))

    ir_mock.async_delete_issue.assert_not_called()
    ir_mock.async_create_issue.assert_not_called()


# --------------------------------------------------------------------------- #
# _refresh_scanners / _rebuild_scanner_list
# --------------------------------------------------------------------------- #
def test_rebuild_scanner_list_early_exit_when_unchanged() -> None:
    """If the HA scanner set matches the cached set, the method exits at once."""
    coord = _bare_coordinator()
    existing = {_HashableScanner(source="aa:bb:cc:dd:ee:ff")}
    coord._hascanners = existing

    with patch(
        "custom_components.bermuda.coordinator.bluetooth.async_current_scanners",
        return_value=set(existing),
    ):
        # _refresh_scanners delegates straight to _rebuild_scanner_list.
        coord._refresh_scanners()

    # Unchanged set: same object retained, no purge/repair side effects needed.
    assert coord._hascanners == existing


def test_rebuild_scanner_list_rebuilds_on_change() -> None:
    """A changed scanner set drives purge, per-scanner init and repair management."""
    coord = _bare_coordinator()
    coord._hascanners = set()  # forces "changed"
    coord._scanners_without_areas = []

    hascanner = _HashableScanner(source="AA:BB:CC:DD:EE:FF")

    # The bermuda_scanner returned by _get_or_create_device: async_as_scanner_init
    # is a no-op here so we don't need the real device-registry resolution path.
    bermuda_scanner = MagicMock()
    bermuda_scanner.name = "scanner-one"
    bermuda_scanner.address = "aa:bb:cc:dd:ee:ff"
    bermuda_scanner.area_id = None  # area-less -> should be flagged for repair

    coord._get_or_create_device = MagicMock(return_value=bermuda_scanner)
    coord._async_purge_removed_scanners = MagicMock()
    coord._async_manage_repair_scanners_without_areas = MagicMock()

    with patch(
        "custom_components.bermuda.coordinator.bluetooth.async_current_scanners",
        return_value={hascanner},
    ):
        coord._rebuild_scanner_list()

    assert coord._hascanners == {hascanner}
    coord._async_purge_removed_scanners.assert_called_once_with()
    bermuda_scanner.async_as_scanner_init.assert_called_once_with(hascanner)
    # The area-less scanner is reported to the repair manager.
    coord._async_manage_repair_scanners_without_areas.assert_called_once()
    reported = coord._async_manage_repair_scanners_without_areas.call_args.args[0]
    assert reported == ["scanner-one [aa:bb:cc:dd:ee:ff]"]


def test_rebuild_scanner_list_no_repair_when_area_set() -> None:
    """A scanner that already has an area_id is not added to the repair list."""
    coord = _bare_coordinator()
    coord._hascanners = set()

    hascanner = _HashableScanner(source="AA:BB:CC:DD:EE:FF")
    bermuda_scanner = MagicMock()
    bermuda_scanner.name = "located"
    bermuda_scanner.address = "aa:bb:cc:dd:ee:ff"
    bermuda_scanner.area_id = "living_room"

    coord._get_or_create_device = MagicMock(return_value=bermuda_scanner)
    coord._async_purge_removed_scanners = MagicMock()
    coord._async_manage_repair_scanners_without_areas = MagicMock()

    with patch(
        "custom_components.bermuda.coordinator.bluetooth.async_current_scanners",
        return_value={hascanner},
    ):
        coord._rebuild_scanner_list()

    coord._async_manage_repair_scanners_without_areas.assert_called_once_with([])


# --------------------------------------------------------------------------- #
# register_ibeacon_source
# --------------------------------------------------------------------------- #
def test_register_ibeacon_source_rejects_non_source() -> None:
    """A device lacking the IBEACON_SOURCE marker is rejected (no metadevice made)."""
    coord = _bare_coordinator()
    coord._get_or_create_device = MagicMock()
    src = _fake_source_device(metadevice_type=set())

    coord.register_ibeacon_source(src)

    coord._get_or_create_device.assert_not_called()
    assert coord.metadevices == {}


def test_register_ibeacon_source_rejects_without_beacon_id() -> None:
    """A source flagged as iBeacon but with no beacon_unique_id is rejected."""
    coord = _bare_coordinator()
    coord._get_or_create_device = MagicMock()
    src = _fake_source_device(
        metadevice_type={METADEVICE_TYPE_IBEACON_SOURCE},
        beacon_unique_id=None,
    )

    coord.register_ibeacon_source(src)

    coord._get_or_create_device.assert_not_called()
    assert coord.metadevices == {}


def test_register_ibeacon_source_creates_new_metadevice() -> None:
    """A fresh iBeacon source seeds a new metadevice and copies beacon attrs."""
    coord = _bare_coordinator()
    beacon_id = "00112233445566778899aabbccddeeff_1_2"

    src = _fake_source_device(
        address="aa:bb:cc:dd:ee:ff",
        metadevice_type={METADEVICE_TYPE_IBEACON_SOURCE},
        beacon_unique_id=beacon_id,
        beacon_uuid="00112233445566778899aabbccddeeff",
        beacon_major="1",
        beacon_minor="2",
        beacon_power=-59,
        name_bt_serviceinfo="ServiceName",
        name_bt_local_name="LocalName",
    )

    metadevice = _fake_source_device(address=beacon_id, metadevice_sources=[])
    # _get_or_create_device returns our (empty-sources) metadevice the first time.
    coord._get_or_create_device = MagicMock(return_value=metadevice)

    coord.register_ibeacon_source(src)

    assert coord.metadevices[beacon_id] is metadevice
    # Beacon identity copied across.
    assert metadevice.beacon_unique_id == beacon_id
    assert metadevice.beacon_uuid == "00112233445566778899aabbccddeeff"
    assert metadevice.beacon_major == "1"
    assert metadevice.beacon_minor == "2"
    assert metadevice.beacon_power == -59
    assert metadevice.name_bt_serviceinfo == "ServiceName"
    assert metadevice.name_bt_local_name == "LocalName"
    # The source MAC is registered.
    assert src.address in metadevice.metadevice_sources
    # Not in CONF_DEVICES, so no sensor creation.
    assert metadevice.create_sensor is False


def test_register_ibeacon_source_sets_create_sensor_when_configured() -> None:
    """A new metadevice whose address is in CONF_DEVICES gets create_sensor=True."""
    coord = _bare_coordinator()
    beacon_id = "00112233445566778899aabbccddeeff_1_2"
    coord.options = {CONF_DEVICES: [beacon_id.upper()]}

    src = _fake_source_device(
        address="aa:bb:cc:dd:ee:ff",
        metadevice_type={METADEVICE_TYPE_IBEACON_SOURCE},
        beacon_unique_id=beacon_id,
    )
    metadevice = _fake_source_device(address=beacon_id, metadevice_sources=[])
    coord._get_or_create_device = MagicMock(return_value=metadevice)

    coord.register_ibeacon_source(src)

    assert metadevice.create_sensor is True


def test_register_ibeacon_source_adds_new_mac_to_existing() -> None:
    """A new source MAC for an existing metadevice is inserted at the front."""
    coord = _bare_coordinator()
    beacon_id = "00112233445566778899aabbccddeeff_1_2"

    # Existing metadevice already has one source; new source should prepend.
    metadevice = _fake_source_device(
        address=beacon_id,
        metadevice_sources=["99:99:99:99:99:99"],
        name_bt_serviceinfo=None,
        name_bt_local_name=None,
    )
    coord.metadevices[beacon_id] = metadevice
    coord._get_or_create_device = MagicMock(return_value=metadevice)

    src = _fake_source_device(
        address="aa:bb:cc:dd:ee:ff",
        metadevice_type={METADEVICE_TYPE_IBEACON_SOURCE},
        beacon_unique_id=beacon_id,
        name_bt_serviceinfo="NewService",
        name_bt_local_name="NewLocal",
    )

    coord.register_ibeacon_source(src)

    assert metadevice.metadevice_sources[0] == "aa:bb:cc:dd:ee:ff"
    assert "99:99:99:99:99:99" in metadevice.metadevice_sources
    # Names back-filled from the new source since metadevice had none.
    assert metadevice.name_bt_serviceinfo == "NewService"
    assert metadevice.name_bt_local_name == "NewLocal"


# --------------------------------------------------------------------------- #
# discover_private_ble_metadevices
# --------------------------------------------------------------------------- #
def test_discover_private_ble_skips_when_init_flag_unset() -> None:
    """When _do_private_device_init is False the whole routine is skipped."""
    coord = _bare_coordinator()
    coord._do_private_device_init = False
    # If it tried to read these it would AttributeError -> guards the early exit.
    coord.discover_private_ble_metadevices()  # should simply return


def test_discover_private_ble_creates_metadevice_with_source() -> None:
    """A resolved Private BLE device tracker seeds a metadevice + source device."""
    coord = _bare_coordinator()
    coord._do_private_device_init = True

    # --- Fake the HA registry plumbing the method walks. ---
    pb_entry = SimpleNamespace(entry_id="pbentry1")
    coord.hass.config_entries.async_entries = MagicMock(return_value=[pb_entry])

    from homeassistant.const import Platform

    pb_entity = SimpleNamespace(
        domain=Platform.DEVICE_TRACKER,
        entity_id="device_tracker.phone",
        device_id="dev1",
        unique_id="irkvalue_device_tracker",
    )
    coord.er = MagicMock()
    coord.er.entities.get_entries_for_config_entry_id = MagicMock(return_value=[pb_entity])

    pb_device = SimpleNamespace(name_by_user=None, name="Phone")
    coord.dr = MagicMock()
    coord.dr.async_get = MagicMock(return_value=pb_device)

    # The device_tracker state carries the current source MAC.
    coord.hass.states.get = MagicMock(return_value=SimpleNamespace(attributes={"current_address": "AA:BB:CC:DD:EE:FF"}))

    # _get_or_create_device must return distinct objects for the IRK metadevice
    # and the source device.
    metadevice = _fake_source_device(address="irkvalue", metadevice_sources=[], create_sensor=False)
    metadevice.make_name = MagicMock()
    source_device = _fake_source_device(address="aa:bb:cc:dd:ee:ff", metadevice_type=set())

    def _goc(address):
        return metadevice if address == "irkvalue" else source_device

    coord._get_or_create_device = MagicMock(side_effect=_goc)

    coord.discover_private_ble_metadevices()

    # The init flag must flip so we only run once.
    assert coord._do_private_device_init is False
    # The IRK becomes a tracked metadevice with a sensor.
    assert "irkvalue" in coord.metadevices
    assert metadevice.create_sensor is True
    metadevice.make_name.assert_called_once_with()
    # State source tracked, source MAC registered against the metadevice.
    assert coord.pb_state_sources["device_tracker.phone"] == "aa:bb:cc:dd:ee:ff"
    assert "aa:bb:cc:dd:ee:ff" in metadevice.metadevice_sources


def test_discover_private_ble_no_source_when_state_missing() -> None:
    """If the device_tracker has no state, no source device is registered."""
    coord = _bare_coordinator()
    coord._do_private_device_init = True

    pb_entry = SimpleNamespace(entry_id="pbentry1")
    coord.hass.config_entries.async_entries = MagicMock(return_value=[pb_entry])

    from homeassistant.const import Platform

    pb_entity = SimpleNamespace(
        domain=Platform.DEVICE_TRACKER,
        entity_id="device_tracker.phone",
        device_id=None,  # exercise the "no device" branch as well
        unique_id="irkvalue",
    )
    coord.er = MagicMock()
    coord.er.entities.get_entries_for_config_entry_id = MagicMock(return_value=[pb_entity])
    coord.dr = MagicMock()
    coord.hass.states.get = MagicMock(return_value=None)  # not yet resolved

    metadevice = _fake_source_device(address="irkvalue", metadevice_sources=[])
    metadevice.make_name = MagicMock()
    coord._get_or_create_device = MagicMock(return_value=metadevice)

    coord.discover_private_ble_metadevices()

    assert "irkvalue" in coord.metadevices
    # pb_state_sources gets a None placeholder, no source MAC.
    assert coord.pb_state_sources["device_tracker.phone"] is None
    assert metadevice.metadevice_sources == []


# --------------------------------------------------------------------------- #
# update_metadevices
# --------------------------------------------------------------------------- #
def test_update_metadevices_copies_adverts_and_names() -> None:
    """Sources' adverts/names are copied into the metadevice."""
    coord = _bare_coordinator()
    coord.discover_private_ble_metadevices = MagicMock()  # isolate this method

    source = _fake_source_device(
        address="aa:bb:cc:dd:ee:ff",
        last_seen=100,
        ref_power=-59,
        adverts={("aa:bb:cc:dd:ee:ff", "scanner1"): "ADVERT"},
        name_bt_local_name="PhoneLocal",
        manufacturer="Acme",
        beacon_major="7",
    )
    source.set_ref_power = MagicMock()

    metadevice = _fake_source_device(
        address="meta1",
        metadevice_sources=["aa:bb:cc:dd:ee:ff"],
        last_seen=0,
        ref_power=-50,
        adverts={},
        name_bt_local_name=None,
        manufacturer=None,
    )
    metadevice.make_name = MagicMock()

    coord.metadevices = {"meta1": metadevice}
    coord._get_device = MagicMock(return_value=source)

    coord.update_metadevices()

    # Advert copied across.
    assert metadevice.adverts[("aa:bb:cc:dd:ee:ff", "scanner1")] == "ADVERT"
    # last_seen advanced to the newer source value.
    assert metadevice.last_seen == 100
    # ref_power differs, so it is pushed down to the source device.
    source.set_ref_power.assert_called_once_with(-50)
    # Naming fields back-filled and a name recompute requested.
    assert metadevice.name_bt_local_name == "PhoneLocal"
    assert metadevice.manufacturer == "Acme"
    metadevice.make_name.assert_called_once_with()
    # Beacon identity always propagated.
    assert metadevice.beacon_major == "7"


def test_update_metadevices_skips_missing_source() -> None:
    """A source MAC with no backend device is silently skipped."""
    coord = _bare_coordinator()
    coord.discover_private_ble_metadevices = MagicMock()

    metadevice = _fake_source_device(
        address="meta1",
        metadevice_sources=["dead:beef"],
        adverts={},
    )
    metadevice.make_name = MagicMock()
    coord.metadevices = {"meta1": metadevice}
    coord._get_device = MagicMock(return_value=None)

    coord.update_metadevices()

    assert metadevice.adverts == {}
    metadevice.make_name.assert_not_called()


def test_update_metadevices_severs_source_on_uuid_change() -> None:
    """An iBeacon source whose uuid no longer matches is severed from the meta."""
    coord = _bare_coordinator()
    coord.discover_private_ble_metadevices = MagicMock()

    source = _fake_source_device(
        address="aa:bb:cc:dd:ee:ff",
        beacon_unique_id="newuuid_1_2",
        adverts={("aa:bb:cc:dd:ee:ff", "scanner1"): "ADVERT"},
    )

    metadevice = _fake_source_device(
        address="meta1",
        metadevice_type={METADEVICE_IBEACON_DEVICE},
        beacon_unique_id="origuuid_1_2",
        metadevice_sources=["aa:bb:cc:dd:ee:ff"],
        adverts={("aa:bb:cc:dd:ee:ff", "scanner1"): "STALE"},
    )
    metadevice.make_name = MagicMock()
    coord.metadevices = {"meta1": metadevice}
    coord._get_device = MagicMock(return_value=source)

    coord.update_metadevices()

    # The mismatched source is removed and its scanner entries purged.
    assert "aa:bb:cc:dd:ee:ff" not in metadevice.metadevice_sources
    assert metadevice.adverts == {}


# --------------------------------------------------------------------------- #
# _async_gather_advert_data
# --------------------------------------------------------------------------- #
def _make_gather_coordinator():
    coord = _bare_coordinator()
    coord._scanner_init_pending = False
    coord.stamp_last_update_started = 1_000_000.0
    return coord


def test_gather_advert_data_forces_refresh_when_pending() -> None:
    """When scanner init is pending, a forced scanner refresh is performed first."""
    coord = _make_gather_coordinator()
    coord._scanner_init_pending = True
    coord._hascanners = set()  # no scanners -> loop body never runs
    coord._refresh_scanners = MagicMock()

    assert coord._async_gather_advert_data() is True
    coord._refresh_scanners.assert_called_once_with(force=True)


def test_gather_advert_data_processes_fresh_advert() -> None:
    """A current advert is dispatched to the target device's process_advertisement."""
    coord = _make_gather_coordinator()

    scanner_device = MagicMock()
    scanner_device.async_as_scanner_get_stamp = MagicMock(return_value=None)  # treat as fresh
    ble_device = SimpleNamespace(address="DE:AD:BE:EF:00:01")
    advert = SimpleNamespace(rssi=-70)

    ha_scanner = _HashableScanner(
        source="aa:bb:cc:dd:ee:ff",
        discovered_devices_and_advertisement_data={
            ble_device.address: (ble_device, advert),
        },
    )
    coord._hascanners = {ha_scanner}
    coord._get_device = MagicMock(return_value=scanner_device)

    target_device = MagicMock()
    coord._get_or_create_device = MagicMock(return_value=target_device)

    assert coord._async_gather_advert_data() is True

    scanner_device.async_as_scanner_update.assert_called_once_with(ha_scanner)
    coord._get_or_create_device.assert_called_once_with(ble_device.address)
    target_device.process_advertisement.assert_called_once_with(scanner_device, advert)


def test_gather_advert_data_skips_bogus_rssi() -> None:
    """An advert with rssi == -127 (BlueZ phantom) is dropped before processing."""
    coord = _make_gather_coordinator()

    scanner_device = MagicMock()
    scanner_device.async_as_scanner_get_stamp = MagicMock(return_value=None)
    ble_device = SimpleNamespace(address="DE:AD:BE:EF:00:02")
    advert = SimpleNamespace(rssi=-127)

    ha_scanner = _HashableScanner(
        source="aa:bb:cc:dd:ee:ff",
        discovered_devices_and_advertisement_data={ble_device.address: (ble_device, advert)},
    )
    coord._hascanners = {ha_scanner}
    coord._get_device = MagicMock(return_value=scanner_device)
    coord._get_or_create_device = MagicMock()

    assert coord._async_gather_advert_data() is True
    coord._get_or_create_device.assert_not_called()


def test_gather_advert_data_skips_stale_advert() -> None:
    """An advert older than (last_update_started - 3) is skipped."""
    coord = _make_gather_coordinator()

    scanner_device = MagicMock()
    # stamp well before the cutoff (last_update_started - 3).
    scanner_device.async_as_scanner_get_stamp = MagicMock(return_value=coord.stamp_last_update_started - 100)
    ble_device = SimpleNamespace(address="DE:AD:BE:EF:00:03")
    advert = SimpleNamespace(rssi=-60)

    ha_scanner = _HashableScanner(
        source="aa:bb:cc:dd:ee:ff",
        discovered_devices_and_advertisement_data={ble_device.address: (ble_device, advert)},
    )
    coord._hascanners = {ha_scanner}
    coord._get_device = MagicMock(return_value=scanner_device)
    coord._get_or_create_device = MagicMock()

    assert coord._async_gather_advert_data() is True
    coord._get_or_create_device.assert_not_called()


def test_gather_advert_data_recovers_missing_scanner_device() -> None:
    """A first-miss scanner triggers a forced refresh; if still missing, skip it."""
    coord = _make_gather_coordinator()

    ha_scanner = _HashableScanner(
        source="aa:bb:cc:dd:ee:ff",
        discovered_devices_and_advertisement_data={},
    )
    coord._hascanners = {ha_scanner}
    # _get_device always returns None -> never resolves.
    coord._get_device = MagicMock(return_value=None)
    coord._refresh_scanners = MagicMock()
    coord._get_or_create_device = MagicMock()

    assert coord._async_gather_advert_data() is True
    # Refresh attempted once when the scanner was first missing.
    coord._refresh_scanners.assert_called_once_with(force=True)
    # No adverts processed for an unresolved scanner.
    coord._get_or_create_device.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
