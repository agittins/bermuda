"""Tests for Bermuda coordinator edge cases."""

from __future__ import annotations
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bermuda.const import ADDR_TYPE_IBEACON, CONF_AREA_ENTITIES, CONF_DEVICES, DOMAIN
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def test_dump_devices_includes_private_ble_current_addresses() -> None:
    """Test configured-device dumps include Private BLE source addresses."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.options = {CONF_DEVICES: []}
    coordinator.pb_state_sources = {"device_tracker.phone": "aa:bb:cc:dd:ee:ff"}
    coordinator.devices = {
        "aa:bb:cc:dd:ee:ff": SimpleNamespace(to_dict=lambda: {"name": "phone"}),
        "11:22:33:44:55:66": SimpleNamespace(to_dict=lambda: {"name": "other"}),
    }

    service_call = coordinator.service_dump_devices(SimpleNamespace(data={"configured_devices": True, "redact": False}))
    try:
        service_call.send(None)
    except StopIteration as exc:
        result = exc.value
    else:
        raise AssertionError("service_dump_devices unexpectedly awaited")

    assert result == {"aa:bb:cc:dd:ee:ff": {"name": "phone"}}


def test_prune_devices_keeps_metadevices() -> None:
    """Test pruning does not delete iBeacon/IRK metadevice entries."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.stamp_last_prune = 0
    coordinator.stamp_redactions_expiry = None
    coordinator.redactions = {}
    coordinator.irk_manager = MagicMock()

    metadevice_address = "00112233445566778899aabbccddeeff_1_2"
    metadevice = SimpleNamespace(
        address=metadevice_address,
        metadevice_sources=[],
        last_seen=-999999999,
        create_sensor=False,
        is_scanner=False,
        address_type=ADDR_TYPE_IBEACON,
        adverts={},
    )
    coordinator.devices = {metadevice_address: metadevice}
    coordinator.metadevices = {metadevice_address: metadevice}

    coordinator.prune_devices(force_pruning=True)

    assert coordinator.devices == {metadevice_address: metadevice}


def test_handle_devreg_changes_private_ble_device_connection_sets_flag() -> None:
    """A create/update event whose device has a private_ble_device connection triggers PBLE re-init."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.devices = {}
    coordinator._do_private_device_init = False
    device_entry = SimpleNamespace(connections={("private_ble_device", "some-id")}, identifiers=set())
    coordinator.dr = SimpleNamespace(async_get=lambda device_id: device_entry)

    ev = SimpleNamespace(data={"action": "create", "device_id": "dev-id"})
    coordinator.handle_devreg_changes(ev)

    assert coordinator._do_private_device_init is True


def test_handle_devreg_changes_ibeacon_noop_and_identifiers_keyerror_caught() -> None:
    """An ibeacon connection is a no-op; an identifiers match for an unknown device is swallowed."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.devices = {}  # "not-in-devices" is absent -> KeyError must be caught, not raised
    coordinator._do_private_device_init = False
    coordinator._scanner_init_pending = False
    device_entry = SimpleNamespace(
        # One connection hits the "ibeacon" no-op branch, the other (neither private_ble_device
        # nor ibeacon) falls through to the identifiers loop.
        connections={("ibeacon", "ib-id"), ("other", "other-id")},
        identifiers={(DOMAIN, "not-in-devices")},
        name_by_user=None,
    )
    coordinator.dr = SimpleNamespace(async_get=lambda device_id: device_entry)

    ev = SimpleNamespace(data={"action": "update", "device_id": "dev-id"})
    coordinator.handle_devreg_changes(ev)  # must not raise

    # ibeacon branch is a no-op: PBLE init was never requested.
    assert coordinator._do_private_device_init is False
    # The "else" branch (identifiers walk) still ran and flagged a scanner refresh.
    assert coordinator._scanner_init_pending is True


def test_async_handle_advert_schedules_background_task_when_stale() -> None:
    """A stale stamp_last_update causes async_handle_advert to schedule an update task."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.hass = MagicMock()
    coordinator.stamp_last_update = 0
    coordinator.config_entry = MagicMock()
    # Stub out the real update coroutine so the MagicMock'd background-task call doesn't
    # leave a genuine, never-awaited coroutine object behind (harmless but noisy at GC time).
    coordinator._async_update_data_internal = MagicMock(return_value="update-coro-stub")

    coordinator.async_handle_advert(MagicMock(), MagicMock())

    coordinator.config_entry.async_create_background_task.assert_called_once()
    args = coordinator.config_entry.async_create_background_task.call_args.args
    assert args[0] is coordinator.hass


def test_sensor_created_warns_and_checks_platforms_when_device_missing() -> None:
    """sensor_created for an address with no tracked device logs and still checks completion."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._get_device = MagicMock(return_value=None)
    coordinator._check_all_platforms_created = MagicMock()

    coordinator.sensor_created("aa:bb:cc:dd:ee:ff")

    coordinator._check_all_platforms_created.assert_called_once_with("aa:bb:cc:dd:ee:ff")


def test_device_tracker_created_warns_and_checks_platforms_when_device_missing() -> None:
    """device_tracker_created for an address with no tracked device logs and still checks completion."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._get_device = MagicMock(return_value=None)
    coordinator._check_all_platforms_created = MagicMock()

    coordinator.device_tracker_created("aa:bb:cc:dd:ee:ff")

    coordinator._check_all_platforms_created.assert_called_once_with("aa:bb:cc:dd:ee:ff")


def test_refresh_areas_by_min_distance_calls_per_tracked_device() -> None:
    """Only devices with create_sensor=True get their area refreshed."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    tracked = SimpleNamespace(create_sensor=True)
    untracked = SimpleNamespace(create_sensor=False)
    coordinator.devices = {"a": tracked, "b": untracked}
    coordinator._refresh_area_by_min_distance = MagicMock()

    coordinator._refresh_areas_by_min_distance()

    coordinator._refresh_area_by_min_distance.assert_called_once_with(tracked)


def test_refresh_area_by_min_distance_delegates_to_trilateration() -> None:
    """_refresh_area_by_min_distance calls the trilateration helper with device + options."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.options = {"foo": "bar"}
    device = SimpleNamespace()

    with patch("custom_components.bermuda.coordinator.refresh_area_by_min_distance") as mock_refresh:
        coordinator._refresh_area_by_min_distance(device)

    mock_refresh.assert_called_once_with(device, coordinator.options)


def test_apply_area_entity_overrides_noop_when_no_area_triggered() -> None:
    """Entities are configured, but none are currently triggered: no device is touched."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.options = {CONF_AREA_ENTITIES: ["binary_sensor.motion"]}
    coordinator.area_entity_manager = MagicMock()
    coordinator.area_entity_manager.get_triggered_areas_with_distances.return_value = {}
    device = SimpleNamespace(create_sensor=True, area_id=None, area_name=None, area_distance=None)
    coordinator.devices = {"d": device}

    coordinator._apply_area_entity_overrides()

    assert device.area_id is None


def test_apply_area_entity_overrides_skips_devices_not_tracked() -> None:
    """A device with create_sensor=False is skipped even if an area is triggered."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.options = {CONF_AREA_ENTITIES: ["binary_sensor.motion"]}
    coordinator.area_entity_manager = MagicMock()
    coordinator.area_entity_manager.get_triggered_areas_with_distances.return_value = {"kitchen": ("Kitchen", 0.1)}
    device = SimpleNamespace(create_sensor=False, area_id="garage", area_name="Garage", area_distance=3.0)
    coordinator.devices = {"d": device}

    coordinator._apply_area_entity_overrides()

    assert device.area_id == "garage"  # untouched


def test_apply_area_entity_overrides_wins_when_already_in_area_but_virtually_closer() -> None:
    """A device already in the triggered area is overridden if the virtual distance beats BLE,
    and the loop breaks immediately rather than considering other, would-be-better candidates."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator.options = {CONF_AREA_ENTITIES: ["binary_sensor.motion"]}
    coordinator.area_entity_manager = MagicMock()
    coordinator.area_entity_manager.get_triggered_areas_with_distances.return_value = {
        "kitchen": ("Kitchen", 2.0),
        # "den" would win here (0.1 < 2.0) if the break after the "kitchen" match didn't stop the loop.
        "den": ("Den", 0.1),
    }
    applied = []
    device = SimpleNamespace(create_sensor=True, area_id="kitchen", area_name="Kitchen", area_distance=5.0)
    device.apply_area_override = lambda area_id, distance: applied.append((area_id, distance))
    coordinator.devices = {"d": device}

    coordinator._apply_area_entity_overrides()

    assert applied == [("kitchen", 2.0)]


async def test_dump_devices_filters_by_specific_addresses() -> None:
    """The addresses_input list is uppercased/split and matched case-insensitively."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.options = {CONF_DEVICES: []}
    coordinator.pb_state_sources = {}
    coordinator.devices = {
        "aa:bb:cc:dd:ee:ff": SimpleNamespace(to_dict=lambda: {"name": "target"}),
        "11:22:33:44:55:66": SimpleNamespace(to_dict=lambda: {"name": "other"}),
    }
    call = SimpleNamespace(data={"addresses": "AA:BB:CC:DD:EE:FF", "redact": False, "configured_devices": False})

    result = await coordinator.service_dump_devices(call)

    assert result == {"aa:bb:cc:dd:ee:ff": {"name": "target"}}


async def test_dump_devices_redact_warns_when_slow(caplog: pytest.LogCaptureFixture) -> None:
    """A redaction pass over 3s logs a WARNING (still returns the redacted payload)."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.options = {CONF_DEVICES: []}
    coordinator.pb_state_sources = {}
    coordinator.devices = {}
    coordinator.redact_data = MagicMock(return_value={"redacted": True})
    call = SimpleNamespace(data={"addresses": "", "redact": True, "configured_devices": False})

    with (
        patch("custom_components.bermuda.coordinator.monotonic_time_coarse", side_effect=[0.0, 4.0]),
        caplog.at_level(logging.WARNING, logger="custom_components.bermuda"),
    ):
        result = await coordinator.service_dump_devices(call)

    assert result == {"redacted": True}
    assert "Dump devices redaction took" in caplog.text


async def test_dump_devices_redact_debug_logs_when_fast(caplog: pytest.LogCaptureFixture) -> None:
    """A fast (<=3s) redaction pass logs at DEBUG instead of WARNING."""
    coordinator = object.__new__(BermudaDataUpdateCoordinator)
    coordinator._scanner_list = set()
    coordinator.options = {CONF_DEVICES: []}
    coordinator.pb_state_sources = {}
    coordinator.devices = {}
    coordinator.redact_data = MagicMock(return_value={"redacted": True})
    call = SimpleNamespace(data={"addresses": "", "redact": True, "configured_devices": False})

    with (
        patch("custom_components.bermuda.coordinator.monotonic_time_coarse", side_effect=[0.0, 1.0]),
        caplog.at_level(logging.DEBUG, logger="custom_components.bermuda"),
    ):
        result = await coordinator.service_dump_devices(call)

    assert result == {"redacted": True}
    assert "Dump devices redaction took" in caplog.text
