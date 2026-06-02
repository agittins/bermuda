"""Platform async_setup_entry / device_new / create_scanner_entities coverage.

The existing entity tests (``tests/test_entities.py``) instantiate entity
classes directly with ``object.__new__`` and therefore never run the platform
``async_setup_entry`` functions nor their inner ``device_new`` /
``create_scanner_entities`` / ``scanners_changed`` closures. Those closures are
the wiring that turns a coordinator "new device" / "scanners changed" signal
into actual added entities.

These tests use the ``setup_bermuda_entry`` fixture (config entry LOADED, so
every platform's ``async_setup_entry`` has already run and connected its
callbacks to the dispatcher) and then drive entity creation by:

* injecting a real ``BermudaDevice`` into the live ``coordinator.devices`` and
  firing ``SIGNAL_DEVICE_NEW`` via ``async_dispatcher_send`` -> exercises the
  ``device_new`` callback in sensor.py / number.py / device_tracker.py;
* injecting a scanner device (with ``address_wifi_mac``) into the coordinator's
  scanner set and firing ``SIGNAL_SCANNERS_CHANGED`` -> exercises
  ``create_scanner_entities`` and the ``scanners_changed`` callback in
  sensor.py, including its readiness guard.

Created entities are verified through the HA entity registry. We never assert
on ``unique_id`` *values* (those are pinned in ``test_unique_id_regression.py``);
we only count/inspect entity-registry rows by platform/domain.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.bermuda.const import (
    DOMAIN,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _get_coordinator(entry) -> BermudaDataUpdateCoordinator:
    """Pull the live coordinator off the loaded config entry."""
    return entry.runtime_data.coordinator


def _bermuda_entities(hass: HomeAssistant, domain: str | None = None) -> list[er.RegistryEntry]:
    """Return Bermuda entity-registry rows, optionally filtered by entity domain."""
    registry = er.async_get(hass)
    rows = [e for e in registry.entities.values() if e.platform == DOMAIN]
    if domain is not None:
        rows = [e for e in rows if e.domain == domain]
    return rows


# --------------------------------------------------------------------------- #
# device_new across all three platforms                                        #
# --------------------------------------------------------------------------- #


async def test_device_new_signal_creates_entities_on_all_platforms(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """Firing SIGNAL_DEVICE_NEW creates sensor + number + device_tracker entities.

    This drives the ``device_new`` closures registered by sensor.py,
    number.py and device_tracker.py ``async_setup_entry``.
    """
    coordinator = _get_coordinator(setup_bermuda_entry)

    address = "AA:BB:CC:DD:EE:10"
    device = coordinator._get_or_create_device(address)
    device.create_sensor = True  # mark as a device we want entities for

    sensors_before = len(_bermuda_entities(hass, "sensor"))
    numbers_before = len(_bermuda_entities(hass, "number"))
    trackers_before = len(_bermuda_entities(hass, "device_tracker"))

    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    sensors_after = len(_bermuda_entities(hass, "sensor"))
    numbers_after = len(_bermuda_entities(hass, "number"))
    trackers_after = len(_bermuda_entities(hass, "device_tracker"))

    # device_tracker + number platforms each add exactly one entity per device.
    assert trackers_after == trackers_before + 1
    assert numbers_after == numbers_before + 1
    # The sensor platform adds several per-device sensors (area, range, scanner,
    # rssi, area_last_seen, area_switch_reason, optionally floor).
    assert sensors_after >= sensors_before + 5

    # The coordinator's per-platform "created" callbacks must have flagged the
    # device as fully set up (sensor_created / number_created / tracker_created).
    assert device.create_sensor_done is True
    assert device.create_number_done is True
    assert device.create_tracker_done is True


async def test_device_new_signal_is_idempotent(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A repeated SIGNAL_DEVICE_NEW for the same address does not duplicate entities.

    The ``created_devices`` guard inside each ``device_new`` closure short-circuits
    the second dispatch (the ``else`` branch).
    """
    coordinator = _get_coordinator(setup_bermuda_entry)

    address = "AA:BB:CC:DD:EE:11"
    device = coordinator._get_or_create_device(address)
    device.create_sensor = True

    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    sensors_once = len(_bermuda_entities(hass, "sensor"))
    numbers_once = len(_bermuda_entities(hass, "number"))
    trackers_once = len(_bermuda_entities(hass, "device_tracker"))

    # Fire again for the same address.
    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    assert len(_bermuda_entities(hass, "sensor")) == sensors_once
    assert len(_bermuda_entities(hass, "number")) == numbers_once
    assert len(_bermuda_entities(hass, "device_tracker")) == trackers_once


# --------------------------------------------------------------------------- #
# create_scanner_entities (sensor.py)                                          #
# --------------------------------------------------------------------------- #


async def test_scanners_changed_creates_per_scanner_range_sensors(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """A ready scanner triggers per-scanner range sensors for known devices.

    First a device is created (so ``created_devices`` is non-empty), then a
    remote scanner *with* an ``address_wifi_mac`` is registered and
    SIGNAL_SCANNERS_CHANGED is fired -> ``create_scanner_entities`` passes its
    readiness guard and adds two sensors (range + raw range) per device/scanner.
    """
    coordinator = _get_coordinator(setup_bermuda_entry)

    # A normal device that wants entities.
    dev_addr = "AA:BB:CC:DD:EE:20"
    device = coordinator._get_or_create_device(dev_addr)
    device.create_sensor = True
    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    sensors_before = len(_bermuda_entities(hass, "sensor"))

    # A remote scanner that is "ready" (has a wifi mac).
    scanner_addr = "AA:BB:CC:DD:EE:21"
    scanner = coordinator._get_or_create_device(scanner_addr)
    scanner._is_remote_scanner = True
    scanner.address_wifi_mac = "aa:bb:cc:dd:ee:21"
    coordinator._scanner_list.add(scanner.address)
    coordinator._scanners.add(scanner)

    async_dispatcher_send(hass, SIGNAL_SCANNERS_CHANGED)
    await hass.async_block_till_done()

    sensors_after = len(_bermuda_entities(hass, "sensor"))
    # Two per-scanner sensors (BermudaSensorScannerRange + ...RangeRaw) per device.
    assert sensors_after == sensors_before + 2


async def test_scanners_changed_guard_blocks_when_scanner_not_ready(hass: HomeAssistant, setup_bermuda_entry) -> None:
    """The readiness guard prevents per-scanner sensors for a not-ready remote scanner.

    A remote scanner with ``address_wifi_mac is None`` makes
    ``create_scanner_entities`` return early, so no per-scanner sensors appear.
    """
    coordinator = _get_coordinator(setup_bermuda_entry)

    dev_addr = "AA:BB:CC:DD:EE:30"
    device = coordinator._get_or_create_device(dev_addr)
    device.create_sensor = True
    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    sensors_before = len(_bermuda_entities(hass, "sensor"))

    # A remote scanner that is NOT ready (no wifi mac yet).
    scanner_addr = "AA:BB:CC:DD:EE:31"
    scanner = coordinator._get_or_create_device(scanner_addr)
    scanner._is_remote_scanner = True
    scanner.address_wifi_mac = None
    coordinator._scanner_list.add(scanner.address)
    coordinator._scanners.add(scanner)

    async_dispatcher_send(hass, SIGNAL_SCANNERS_CHANGED)
    await hass.async_block_till_done()

    # Guard tripped: no new per-scanner sensors.
    assert len(_bermuda_entities(hass, "sensor")) == sensors_before


async def test_scanners_changed_guard_blocks_when_is_remote_scanner_is_none(
    hass: HomeAssistant, setup_bermuda_entry
) -> None:
    """Characterisation: a scanner with ``is_remote_scanner is None`` blocks creation.

    NOTE (possible source bug, NOT fixed here): the readiness guard in
    ``sensor.create_scanner_entities`` is::

        if scanner.is_remote_scanner is None or (
            scanner.is_remote_scanner and scanner.address_wifi_mac is None
        ):
            return

    The inline comment claims "usb/HCI scanner's are fine", implying a *local*
    scanner (``is_remote_scanner is None``) should be allowed through; but the
    code actually ``return``s (skips creation) for that case. This test pins the
    *current* behaviour: a None-remote-scanner trips the guard and no per-scanner
    sensors are created. (The same guard appears commented-out in the disabled
    ``device_new`` snippet near the top of sensor.py, using the inverse logic.)
    """
    coordinator = _get_coordinator(setup_bermuda_entry)

    dev_addr = "AA:BB:CC:DD:EE:40"
    device = coordinator._get_or_create_device(dev_addr)
    device.create_sensor = True
    async_dispatcher_send(hass, SIGNAL_DEVICE_NEW, device.address)
    await hass.async_block_till_done()

    sensors_before = len(_bermuda_entities(hass, "sensor"))

    # Local scanner: _is_remote_scanner stays None (the __init__ default).
    scanner_addr = "AA:BB:CC:DD:EE:41"
    scanner = coordinator._get_or_create_device(scanner_addr)
    assert scanner.is_remote_scanner is None
    coordinator._scanner_list.add(scanner.address)
    coordinator._scanners.add(scanner)

    async_dispatcher_send(hass, SIGNAL_SCANNERS_CHANGED)
    await hass.async_block_till_done()

    sensors_after = len(_bermuda_entities(hass, "sensor"))
    # Guard tripped on ``is_remote_scanner is None``: no per-scanner sensors.
    assert sensors_after == sensors_before
