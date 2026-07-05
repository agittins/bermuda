"""Integration-level tests for the Bermuda coordinator update lifecycle.

These exercise the live coordinator created by the ``setup_bermuda_entry``
fixture (the config entry is LOADED, so ``entry.runtime_data.coordinator``
is a fully constructed coordinator wired to a running HomeAssistant).

They deliberately avoid the territory of:
- ``tests/test_coordinator_helpers.py`` (count_active_*, _get_device, summary)
- ``tests/test_coordinator.py`` (service_dump_devices, prune_devices)
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import CONF_DEVICES, SIGNAL_DEVICE_IN100_NEW, SIGNAL_DEVICE_NEW
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _get_coordinator(entry) -> BermudaDataUpdateCoordinator:
    """Pull the live coordinator off the loaded config entry."""
    return entry.runtime_data.coordinator


async def test_async_refresh_cycle_completes(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A full async_refresh()/_async_update_data cycle runs cleanly.

    The test HA has no real scanners or devices, so this proves the update
    loop is robust to empty device/scanner sets and ends in a success state.
    """
    coordinator = _get_coordinator(setup_bermuda_entry)

    # Manufacturer-id loading is patched out in conftest, so the guard flag may
    # be left set; clear it so the real update body actually executes.
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is True
    # The update stamps the run; with the guard cleared this must advance.
    assert coordinator.stamp_last_update > 0


async def test_update_data_internal_returns_true_when_unblocked(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """_async_update_data_internal returns the gather result (True) on a clean run."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    result = await coordinator._async_update_data_internal()

    assert result is True
    assert coordinator.update_in_progress is False  # always cleared in finally


async def test_update_data_internal_skips_while_loading_manufacturer_ids(
    hass: HomeAssistant, setup_bermuda_entry
) -> None:
    """While manufacturer ids are still loading, the update short-circuits to True."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = True

    result = await coordinator._async_update_data_internal()

    assert result is True


async def test_update_data_internal_skips_when_update_in_progress(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A re-entrant update is rejected (returns False) when one is already running."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = True

    result = await coordinator._async_update_data_internal()

    assert result is False
    # The guard belongs to the (simulated) other in-flight run; it stays set
    # because the early-return path does not touch it.
    assert coordinator.update_in_progress is True


async def test_get_or_create_device_creates_then_returns_same(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """_get_or_create_device creates a BermudaDevice once then returns the same object."""
    coordinator = _get_coordinator(setup_bermuda_entry)

    addr = "AA:BB:CC:DD:EE:01"
    first = coordinator._get_or_create_device(addr)
    assert isinstance(first, BermudaDevice)
    # Stored under the normalised (lower-cased) key.
    assert "aa:bb:cc:dd:ee:01" in coordinator.devices

    # A second call with differently-cased input returns the identical object.
    second = coordinator._get_or_create_device("aa:bb:cc:dd:ee:01")
    assert second is first
    third = coordinator._get_or_create_device(addr)
    assert third is first

    # _get_device (read-only) agrees with _get_or_create_device.
    assert coordinator._get_device(addr) is first


async def test_dt_mono_to_datetime_and_age_types(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """dt_mono_to_datetime returns a datetime; dt_mono_to_age returns a human string."""
    coordinator = _get_coordinator(setup_bermuda_entry)

    from bluetooth_data_tools import monotonic_time_coarse

    stamp = monotonic_time_coarse() - 30  # 30 seconds ago

    dt = coordinator.dt_mono_to_datetime(stamp)
    assert isinstance(dt, datetime)
    # The computed datetime should be in the past relative to now().
    from homeassistant.util.dt import now as ha_now

    assert dt < ha_now()

    age = coordinator.dt_mono_to_age(stamp)
    assert isinstance(age, str)
    assert age != ""


async def test_resolve_area_name_hit_and_miss(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """resolve_area_name returns the name on a hit and None when the id is unknown."""
    coordinator = _get_coordinator(setup_bermuda_entry)

    # Swap the area registry for a mock so we control the lookup result.
    real_ar = coordinator.ar
    try:
        coordinator.ar = SimpleNamespace(
            async_get_area=lambda area_id: SimpleNamespace(name="Living Room") if area_id == "known" else None
        )
        assert coordinator.resolve_area_name("known") == "Living Room"
        # An unknown id resolves to a non-area object (None), which lacks .name.
        assert coordinator.resolve_area_name("nope") is None
    finally:
        coordinator.ar = real_ar


async def test_resolve_area_name_object_without_name(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """If the registry returns an object lacking a name attribute, result is None."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    real_ar = coordinator.ar
    try:
        coordinator.ar = SimpleNamespace(async_get_area=lambda area_id: object())
        assert coordinator.resolve_area_name("anything") is None
    finally:
        coordinator.ar = real_ar


async def test_handle_devreg_changes_create_unknown_device_id_is_noop(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A create/update for an unknown device_id just flags scanner init (no crash)."""
    coordinator = _get_coordinator(setup_bermuda_entry)

    # device_id that the device registry does not know about: handler logs an
    # error and returns without raising.
    ev = SimpleNamespace(data={"action": "create", "device_id": "nonexistent-device-id"})
    coordinator.handle_devreg_changes(ev)
    # No exception is the assertion; sanity-check the coordinator is intact.
    assert isinstance(coordinator.devices, dict)


async def test_handle_devreg_changes_create_without_device_id_returns(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A create action missing a device_id is rejected without error."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    ev = SimpleNamespace(data={"action": "create", "device_id": None})
    # Should log an error and return, not raise.
    coordinator.handle_devreg_changes(ev)


async def test_handle_devreg_changes_update_matches_scanner_device(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """An update whose device_id matches a Bermuda scanner triggers a scanner refresh."""
    coordinator = _get_coordinator(setup_bermuda_entry)

    # Build a fake scanner device that the handler will match by entry_id.
    scanner = coordinator._get_or_create_device("AA:BB:CC:DD:EE:F0")
    scanner.entry_id = "scanner-entry-id"
    scanner._is_scanner = True  # is_scanner is a read-only property

    called = {"refresh": False}
    real_refresh = coordinator._refresh_scanners
    try:
        coordinator._refresh_scanners = lambda force=False: called.__setitem__("refresh", True)
        ev = SimpleNamespace(data={"action": "update", "device_id": "scanner-entry-id"})
        coordinator.handle_devreg_changes(ev)
    finally:
        coordinator._refresh_scanners = real_refresh

    assert called["refresh"] is True


async def test_handle_devreg_changes_remove_triggers_private_init(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A remove for a device that is not a scanner opportunistically re-inits PBLE."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._do_private_device_init = False

    ev = SimpleNamespace(data={"action": "remove", "device_id": "some-unrelated-id"})
    coordinator.handle_devreg_changes(ev)

    assert coordinator._do_private_device_init is True


async def test_handle_devreg_changes_remove_matches_scanner(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A remove for a known scanner flags a scanner re-init instead of PBLE re-init."""
    coordinator = _get_coordinator(setup_bermuda_entry)

    scanner = coordinator._get_or_create_device("AA:BB:CC:DD:EE:F1")
    scanner.entry_id = "removed-scanner-id"
    scanner._is_scanner = True  # is_scanner is a read-only property
    # Register it as a scanner so get_scanners includes it.
    if scanner not in coordinator.get_scanners:
        coordinator._scanners.add(scanner)

    coordinator._scanner_init_pending = False
    coordinator._do_private_device_init = False

    ev = SimpleNamespace(data={"action": "remove", "device_id": "removed-scanner-id"})
    coordinator.handle_devreg_changes(ev)

    assert coordinator._scanner_init_pending is True


async def test_update_cycle_seeds_configured_devices(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """Devices listed in CONF_DEVICES get created even if never seen on the air."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False
    coordinator.options[CONF_DEVICES] = ["AA:BB:CC:DD:EE:FF"]

    await coordinator._async_update_data_internal()

    assert "aa:bb:cc:dd:ee:ff" in coordinator.devices


async def test_update_cycle_fires_signal_device_new_for_new_sensor_device(
    hass: HomeAssistant, setup_bermuda_entry
) -> None:
    """A tracked device that hasn't finished entity creation fires SIGNAL_DEVICE_NEW."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    device = coordinator._get_or_create_device("BB:BB:BB:BB:BB:BB")
    device.create_sensor = True

    with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_send:
        await coordinator._async_update_data_internal()

    new_calls = [call for call in mock_send.call_args_list if call.args[1] == SIGNAL_DEVICE_NEW]
    assert any(call.args[2] == device.address for call in new_calls)


async def test_update_cycle_fires_signal_device_in100_new_when_detected(
    hass: HomeAssistant, setup_bermuda_entry
) -> None:
    """A tracked device that started broadcasting IN100 telemetry fires SIGNAL_DEVICE_IN100_NEW."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    device = coordinator._get_or_create_device("CC:CC:CC:CC:CC:CC")
    device.create_sensor = True
    device.in100_detected = True
    assert device.create_in100_done is False  # sanity: real default, not yet flagged done

    with patch("custom_components.bermuda.coordinator.async_dispatcher_send") as mock_send:
        await coordinator._async_update_data_internal()

    in100_calls = [call for call in mock_send.call_args_list if call.args[1] == SIGNAL_DEVICE_IN100_NEW]
    assert any(call.args[2] == device.address for call in in100_calls)


async def test_update_cycle_marks_failure_and_reraises_on_gather_error(
    hass: HomeAssistant, setup_bermuda_entry
) -> None:
    """An exception during the update body still clears update_in_progress and re-raises."""
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    with (
        patch.object(coordinator, "_async_gather_advert_data", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await coordinator._async_update_data_internal()

    assert coordinator.last_update_success is False
    assert coordinator.update_in_progress is False  # finally block still ran


async def test_update_cycle_logs_error_when_very_slow(
    hass: HomeAssistant, setup_bermuda_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """A cycle taking >2s logs an ERROR about the event loop being starved.

    Only the ``time`` name inside coordinator.py is swapped out (not the real,
    process-wide ``time`` module) so asyncio's own clock -- used internally on
    every loop iteration -- is unaffected; patching ``time.monotonic`` globally
    starves the event loop's scheduler and corrupts unrelated background tasks.
    """
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    fake_time = SimpleNamespace(monotonic=MagicMock(side_effect=[0.0, 3.0]))
    with (
        patch("custom_components.bermuda.coordinator.time", fake_time),
        caplog.at_level(logging.ERROR, logger="custom_components.bermuda"),
    ):
        await coordinator._async_update_data_internal()

    assert "Update cycle took" in caplog.text


async def test_update_cycle_logs_warning_when_moderately_slow(
    hass: HomeAssistant, setup_bermuda_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """A cycle taking between 0.5s and 2s logs a WARNING instead of an ERROR.

    See test_update_cycle_logs_error_when_very_slow for why only the module-local
    ``time`` name is swapped instead of the real ``time.monotonic`` globally.
    """
    coordinator = _get_coordinator(setup_bermuda_entry)
    coordinator._waitingfor_load_manufacturer_ids = False
    coordinator.update_in_progress = False

    fake_time = SimpleNamespace(monotonic=MagicMock(side_effect=[0.0, 1.0]))
    with (
        patch("custom_components.bermuda.coordinator.time", fake_time),
        caplog.at_level(logging.WARNING, logger="custom_components.bermuda"),
    ):
        await coordinator._async_update_data_internal()

    assert "Update cycle took" in caplog.text
