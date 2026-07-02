"""
Tests for Bermuda's micro-location + config services and intents.

These drive a fully set-up entry (via the ``setup_bermuda_entry`` fixture) and
hand-build a tracked device with per-scanner adverts so we can calibrate spots,
exercise matching/hysteresis, and call every service and intent end-to-end.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import (
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    DOMAIN,
    ICON_MICROLOCATION,
    MICROLOC_HYSTERESIS_CYCLES,
)
from custom_components.bermuda.sensor import BermudaSensor, BermudaSensorMicroLocation

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mock_ad(rssi: int = -60) -> MagicMock:
    ad = MagicMock()
    ad.rssi = rssi
    ad.tx_power = None
    ad.local_name = None
    ad.manufacturer_data = {}
    ad.service_data = {}
    ad.service_uuids = []
    return ad


def _make_scanner(coordinator, address: str, name: str, area_id: str) -> BermudaDevice:
    """Create a scanner-ish BermudaDevice and register it with the coordinator."""
    scanner = BermudaDevice(address, coordinator)
    # name_by_user is a real name source, so it survives make_name() (which the
    # update loop may run); setting only .name would get reset to the default.
    scanner.name_by_user = name
    scanner.name = name
    scanner.area_id = area_id
    scanner.area_name = name
    coordinator.devices[scanner.address] = scanner
    return scanner


def _make_tracked_device(coordinator, address: str, name: str) -> BermudaDevice:
    device = BermudaDevice(address, coordinator)
    device.name_by_user = name  # a name source, so make_name() keeps it (see _make_scanner)
    device.name = name
    device.create_sensor = True
    coordinator.devices[device.address] = device
    return device


def _set_distance(device: BermudaDevice, scanner: BermudaDevice, distance: float, rssi: int = -60):
    """Ensure an advert exists for device<-scanner and pin its smoothed distance."""
    advert = device.get_scanner(scanner.address)
    if advert is None:
        device.process_advertisement(scanner, _mock_ad(rssi))
        advert = device.get_scanner(scanner.address)
    advert.rssi_distance = distance
    advert.rssi = rssi
    advert.hist_distance_by_interval = [distance] * 5
    advert.area_id = scanner.area_id
    advert.area_name = scanner.area_name
    return advert


def _coordinator(entry):
    return entry.runtime_data.coordinator


async def _setup_keys_in_kitchen(hass, entry):
    """Two kitchen proxies and a 'Keys' device near the first one."""
    coordinator = _coordinator(entry)
    await coordinator.fingerprints.async_load()
    s1 = _make_scanner(coordinator, "11:11:11:11:11:11", "Kitchen Left", "kitchen")
    s2 = _make_scanner(coordinator, "22:22:22:22:22:22", "Kitchen Right", "kitchen")
    keys = _make_tracked_device(coordinator, "ec:00:00:00:00:01", "Keys")
    keys.area_id = "kitchen"
    keys.area_name = "Kitchen"
    _set_distance(keys, s1, 1.0)
    _set_distance(keys, s2, 5.0)
    return coordinator, keys, s1, s2


# ---------------------------------------------------------------------------
# coordinator-level: calibrate, build, match, hysteresis
# ---------------------------------------------------------------------------


async def test_build_live_fingerprint(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    live = coordinator.build_live_fingerprint(keys)
    assert live == {"11:11:11:11:11:11": 1.0, "22:22:22:22:22:22": 5.0}


async def test_calibrate_then_match_and_hysteresis(hass, setup_bermuda_entry):
    coordinator, keys, s1, s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)

    hook = coordinator.calibrate_location(keys, "Key hook")
    assert hook["name"] == "Key hook"
    assert hook["scanner_count"] == 2
    assert "warning" not in hook  # 2 proxies is enough

    # Calibrate a second, distinct spot.
    _set_distance(keys, s1, 5.0)
    _set_distance(keys, s2, 1.0)
    coordinator.calibrate_location(keys, "Drawer")
    assert len(coordinator.fingerprints.list(keys.address)) == 2

    # Put it back near the hook; the match must persist before it commits.
    _set_distance(keys, s1, 1.1)
    _set_distance(keys, s2, 4.9)
    for _ in range(MICROLOC_HYSTERESIS_CYCLES - 1):
        coordinator._refresh_microlocations()  # noqa: SLF001
        assert keys.micro_location_name is None  # not yet persistent enough
    coordinator._refresh_microlocations()  # noqa: SLF001
    assert keys.micro_location_name == "Key hook"
    assert keys.micro_location_confidence is not None


async def test_calibrate_single_scanner_warns(hass, setup_bermuda_entry):
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    s1 = _make_scanner(coordinator, "11:11:11:11:11:11", "Lonely Proxy", "garage")
    fob = _make_tracked_device(coordinator, "ec:00:00:00:00:09", "Fob")
    _set_distance(fob, s1, 2.0)
    summary = coordinator.calibrate_location(fob, "Workbench")
    assert summary["scanner_count"] == 1
    assert "warning" in summary


async def test_calibrate_no_scanners_raises(hass, setup_bermuda_entry):
    from homeassistant.exceptions import ServiceValidationError
    import pytest

    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    ghost = _make_tracked_device(coordinator, "ec:00:00:00:00:0a", "Ghost")
    with pytest.raises(ServiceValidationError):
        coordinator.calibrate_location(ghost, "Nowhere")


# ---------------------------------------------------------------------------
# services
# ---------------------------------------------------------------------------


async def test_service_calibrate_and_where_is(hass, setup_bermuda_entry):
    coordinator, keys, s1, s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)

    resp = await hass.services.async_call(
        DOMAIN, "calibrate_location", {"device": "Keys", "name": "Key hook"}, blocking=True, return_response=True
    )
    assert resp["name"] == "Key hook"
    assert resp["device"] == "Keys"

    # Commit the match via the update loop, then ask where it is.
    for _ in range(MICROLOC_HYSTERESIS_CYCLES):
        coordinator._refresh_microlocations()  # noqa: SLF001

    where = await hass.services.async_call(DOMAIN, "where_is", {"device": "Keys"}, blocking=True, return_response=True)
    assert where["device"] == "Keys"
    assert where["micro_location"] == "Key hook"
    assert where["best_match"] == "Key hook"
    assert isinstance(where["scores"], list)


async def test_service_list_remove_rename(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")

    listing = await hass.services.async_call(
        DOMAIN, "list_locations", {"device": "Keys"}, blocking=True, return_response=True
    )
    assert listing["count"] == 1
    assert listing["locations"][0]["name"] == "Key hook"

    renamed = await hass.services.async_call(
        DOMAIN,
        "rename_location",
        {"device": "Keys", "name": "Key hook", "new_name": "Coat hook"},
        blocking=True,
        return_response=True,
    )
    assert renamed["renamed"] is True
    assert coordinator.fingerprints.find_by_name(keys.address, "Coat hook") is not None

    removed = await hass.services.async_call(
        DOMAIN, "remove_location", {"device": "Keys", "name": "Coat hook"}, blocking=True, return_response=True
    )
    assert removed["removed"] is True
    assert coordinator.fingerprints.list(keys.address) == []


async def test_service_unknown_device_raises(hass, setup_bermuda_entry):
    from homeassistant.exceptions import ServiceValidationError
    import pytest

    _coordinator(setup_bermuda_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "where_is", {"device": "No Such Thing"}, blocking=True, return_response=True
        )


async def test_service_track_untrack_updates_options(hass, setup_bermuda_entry):
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    widget = _make_tracked_device(coordinator, "ec:00:00:00:00:02", "Widget")

    resp = await hass.services.async_call(
        DOMAIN, "track_device", {"device": "Widget"}, blocking=True, return_response=True
    )
    assert resp["tracked"] is True
    assert "EC:00:00:00:00:02" in setup_bermuda_entry.options.get(CONF_DEVICES, [])

    # Persisting options reloads the entry (so CONF_DEVICES takes effect): wait for
    # the reload, then re-fetch the fresh coordinator and re-register the otherwise
    # unseen device before untracking it.
    await hass.async_block_till_done()
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:02", "Widget")

    resp = await hass.services.async_call(
        DOMAIN, "untrack_device", {"device": "Widget"}, blocking=True, return_response=True
    )
    assert resp["tracked"] is False
    assert "EC:00:00:00:00:02" not in setup_bermuda_entry.options.get(CONF_DEVICES, [])


async def test_service_set_global_calibration(hass, setup_bermuda_entry):
    resp = await hass.services.async_call(
        DOMAIN,
        "set_global_calibration",
        {"ref_power": -59, "attenuation": 3.5},
        blocking=True,
        return_response=True,
    )
    assert resp["updated"] is True
    assert setup_bermuda_entry.options.get(CONF_REF_POWER) == -59
    assert setup_bermuda_entry.options.get(CONF_ATTENUATION) == 3.5


async def test_service_set_global_calibration_empty_raises(hass, setup_bermuda_entry):
    from homeassistant.exceptions import ServiceValidationError
    import pytest

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_global_calibration", {}, blocking=True, return_response=True)


async def test_service_set_scanner_offset(hass, setup_bermuda_entry):
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    scanner = _make_scanner(coordinator, "33:33:33:33:33:33", "Hall Proxy", "hall")
    coordinator._scanner_list.add(scanner.address)  # noqa: SLF001 - register as a known proxy

    resp = await hass.services.async_call(
        DOMAIN,
        "set_scanner_offset",
        {"scanner": "Hall Proxy", "rssi_offset": 4},
        blocking=True,
        return_response=True,
    )
    assert resp["updated"] is True
    assert setup_bermuda_entry.options.get(CONF_RSSI_OFFSETS, {}).get(scanner.address) == 4


async def test_service_set_scanner_offset_not_a_scanner_raises(hass, setup_bermuda_entry):
    from homeassistant.exceptions import ServiceValidationError
    import pytest

    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:03", "Not A Proxy")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_scanner_offset", {"scanner": "Not A Proxy", "rssi_offset": 1}, blocking=True
        )


async def test_service_get_config(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")

    cfg = await hass.services.async_call(DOMAIN, "get_config", {}, blocking=True, return_response=True)
    assert CONF_REF_POWER in cfg["global"]
    assert cfg["micro_location_count"] == 1
    assert isinstance(cfg["scanners"], list)


# ---------------------------------------------------------------------------
# intents
# ---------------------------------------------------------------------------


def _speech(response) -> str:
    return response.speech["plain"]["speech"]


async def test_intent_calibrate_and_where_is(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)

    cal = await intent.async_handle(
        hass, DOMAIN, "BermudaCalibrateLocation", {"device": {"value": "Keys"}, "location": {"value": "Key hook"}}
    )
    assert "Key hook" in _speech(cal)

    for _ in range(MICROLOC_HYSTERESIS_CYCLES):
        coordinator._refresh_microlocations()  # noqa: SLF001

    where = await intent.async_handle(hass, DOMAIN, "BermudaWhereIs", {"name": {"value": "Keys"}})
    assert "Key hook" in _speech(where)


async def test_intent_where_is_unknown_device(hass, setup_bermuda_entry):
    _coordinator(setup_bermuda_entry)
    where = await intent.async_handle(hass, DOMAIN, "BermudaWhereIs", {"name": {"value": "Nonsense"}})
    assert "don't know" in _speech(where).lower()


async def test_intent_list_locations(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")

    listing = await intent.async_handle(hass, DOMAIN, "BermudaListLocations", {})
    assert "Key hook" in _speech(listing)

    empty = await intent.async_handle(hass, DOMAIN, "BermudaListLocations", {"name": {"value": "Keys"}})
    assert "Key hook" in _speech(empty)


# ---------------------------------------------------------------------------
# resolution branches, areas, leaving-a-spot, listing-all
# ---------------------------------------------------------------------------


async def test_calibrate_with_named_area(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    area_reg = ar.async_get(hass)
    den = area_reg.async_get_or_create("Den")

    resp = await hass.services.async_call(
        DOMAIN,
        "calibrate_location",
        {"device": "Keys", "name": "Beanbag", "area": "Den"},
        blocking=True,
        return_response=True,
    )
    assert resp["area_id"] == den.id
    assert resp["area_name"] == "Den"


async def test_calibrate_unknown_area_raises(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "calibrate_location",
            {"device": "Keys", "name": "Nope", "area": "No Such Area"},
            blocking=True,
            return_response=True,
        )


async def test_where_is_by_address_with_no_spots(hass, setup_bermuda_entry):
    """Resolve by raw MAC; with no saved spots, micro_location is None."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    where = await hass.services.async_call(
        DOMAIN, "where_is", {"device": "EC:00:00:00:00:01"}, blocking=True, return_response=True
    )
    assert where["device"] == "Keys"
    assert where["micro_location"] is None
    assert "scores" not in where  # no candidates, so no match block


async def test_resolve_device_by_registry_id(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    devreg = dr.async_get(hass)
    reg_device = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, "EC:00:00:00:00:01")},
    )
    where = await hass.services.async_call(
        DOMAIN, "where_is", {"device": reg_device.id}, blocking=True, return_response=True
    )
    assert where["device"] == "Keys"


async def test_device_leaves_spot_is_cleared(hass, setup_bermuda_entry):
    coordinator, keys, s1, s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")
    for _ in range(MICROLOC_HYSTERESIS_CYCLES):
        coordinator._refresh_microlocations()  # noqa: SLF001
    assert keys.micro_location_name == "Key hook"

    # Move it far from the calibrated spot; after hysteresis it should clear.
    _set_distance(keys, s1, 40.0)
    _set_distance(keys, s2, 40.0)
    for _ in range(MICROLOC_HYSTERESIS_CYCLES):
        coordinator._refresh_microlocations()  # noqa: SLF001
    assert keys.micro_location_name is None


async def test_list_locations_all_devices(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")
    listing = await hass.services.async_call(DOMAIN, "list_locations", {}, blocking=True, return_response=True)
    assert listing["count"] == 1


async def test_intent_where_is_area_only_and_unlocatable(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    # Area known, but no committed micro-location -> "is in the <area>".
    where = await intent.async_handle(hass, DOMAIN, "BermudaWhereIs", {"name": {"value": "Keys"}})
    assert "Kitchen" in _speech(where)

    # A device with neither area nor spot -> "can't currently locate".
    lost = _make_tracked_device(coordinator, "ec:00:00:00:00:bb", "Lost Thing")
    lost.area_name = None
    nowhere = await intent.async_handle(hass, DOMAIN, "BermudaWhereIs", {"name": {"value": "Lost Thing"}})
    assert "locate" in _speech(nowhere).lower()


async def test_intent_calibrate_unknown_device(hass, setup_bermuda_entry):
    _coordinator(setup_bermuda_entry)
    resp = await intent.async_handle(
        hass, DOMAIN, "BermudaCalibrateLocation", {"device": {"value": "Ghost"}, "location": {"value": "X"}}
    )
    assert "don't know" in _speech(resp).lower()


async def test_intent_calibrate_no_scanners_reports_error(hass, setup_bermuda_entry):
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:cc", "Silent")
    resp = await intent.async_handle(
        hass, DOMAIN, "BermudaCalibrateLocation", {"device": {"value": "Silent"}, "location": {"value": "X"}}
    )
    assert "no scanners" in _speech(resp).lower()


async def test_micro_location_sensor(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    keys.micro_location_name = "Key hook"
    keys.micro_location_id = "abc123"
    keys.micro_location_confidence = 0.82
    keys.micro_location_last_seen = "Key hook"

    sensor = BermudaSensorMicroLocation(coordinator, setup_bermuda_entry, keys.address)
    # Name comes from the translation_key (i18n), not a hardcoded property.
    assert sensor.translation_key == "micro_location"
    assert sensor.native_value == "Key hook"
    assert sensor.icon == ICON_MICROLOCATION
    assert sensor.device_class is None
    assert sensor.entity_registry_enabled_default is True
    assert sensor.unique_id.endswith("_micro_location")
    attrs = sensor.extra_state_attributes
    assert attrs["micro_location_id"] == "abc123"
    assert attrs["confidence"] == 0.82


async def test_area_sensor_exposes_microlocation(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    keys.micro_location_name = "Key hook"
    keys.micro_location_confidence = 0.9

    area_sensor = BermudaSensor(coordinator, setup_bermuda_entry, keys.address)
    attrs = area_sensor.extra_state_attributes
    assert attrs["micro_location"] == "Key hook"
    assert attrs["micro_location_confidence"] == 0.9


# ---------------------------------------------------------------------------
# additional coverage: build_live_fingerprint / _refresh_microlocations edges,
# _resolve_device / _resolve_area_id branches, calibrate edge cases, and the
# remaining track/untrack/remove/rename/get_config branches.
# ---------------------------------------------------------------------------


async def test_build_live_fingerprint_skips_none_distance(hass, setup_bermuda_entry):
    """A scanner whose advert currently has no rssi_distance is excluded from the live vector."""
    coordinator, keys, s1, s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    advert1 = keys.get_scanner(s1.address)
    advert1.rssi_distance = None

    live = coordinator.build_live_fingerprint(keys)

    assert s1.address not in live
    assert s2.address in live


async def test_refresh_microlocations_noop_when_store_not_loaded(hass, setup_bermuda_entry):
    """While the fingerprint store hasn't finished loading, the refresh is a pure no-op."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.fingerprints.loaded = False
    coordinator.build_live_fingerprint = MagicMock()

    coordinator._refresh_microlocations()  # noqa: SLF001

    coordinator.build_live_fingerprint.assert_not_called()


async def test_refresh_microlocations_applies_none_when_no_saved_fingerprints(hass, setup_bermuda_entry):
    """A tracked device with zero saved spots is explicitly resolved to 'no match'."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    assert coordinator.fingerprints.list(keys.address) == []
    spy = MagicMock(wraps=coordinator._apply_microloc_result)
    coordinator._apply_microloc_result = spy

    coordinator._refresh_microlocations()  # noqa: SLF001

    spy.assert_called_once_with(keys, None)
    assert keys.micro_location_id is None


async def test_resolve_device_empty_string_returns_none(hass, setup_bermuda_entry):
    """_resolve_device("") short-circuits to None rather than matching anything."""
    coordinator = _coordinator(setup_bermuda_entry)
    assert coordinator._resolve_device("") is None  # noqa: SLF001


async def test_resolve_device_by_registry_identifiers(hass, setup_bermuda_entry):
    """A device-registry entry resolved purely via (DOMAIN, address) identifiers, no connections."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    devreg = dr.async_get(hass)
    reg_device = devreg.async_get_or_create(
        config_entry_id=setup_bermuda_entry.entry_id,
        identifiers={(DOMAIN, keys.address)},
    )
    device = coordinator._resolve_device(reg_device.id)  # noqa: SLF001
    assert device is keys


async def test_service_calibrate_location_blank_name_raises(hass, setup_bermuda_entry):
    """calibrate_location rejects a blank (whitespace-only, after strip) name."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "calibrate_location",
            {"device": "Keys", "name": "   "},
            blocking=True,
            return_response=True,
        )


async def test_calibrate_with_area_id_instead_of_name(hass, setup_bermuda_entry):
    """Passing an actual area id (not a name) to calibrate_location is accepted as-is."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    area_reg = ar.async_get(hass)
    den = area_reg.async_get_or_create("Den")

    resp = await hass.services.async_call(
        DOMAIN,
        "calibrate_location",
        {"device": "Keys", "name": "Beanbag", "area": den.id},
        blocking=True,
        return_response=True,
    )
    assert resp["area_id"] == den.id
    assert resp["area_name"] == "Den"


async def test_calibrate_falls_back_to_live_distance_and_skips_dataless_scanner(hass, setup_bermuda_entry):
    """No smoothed history but a live rssi_distance still counts; no history AND no live reading is skipped."""
    coordinator, keys, s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)

    s3 = _make_scanner(coordinator, "33:33:33:33:33:33", "Kitchen Rear", "kitchen")
    advert3 = _set_distance(keys, s3, 2.5)
    advert3.hist_distance_by_interval = []  # no smoothed history yet...
    advert3.rssi_distance = 2.5  # ...but a live reading is available (the fallback branch)

    s4 = _make_scanner(coordinator, "44:44:44:44:44:44", "Kitchen Loft", "kitchen")
    advert4 = _set_distance(keys, s4, 9.9)
    advert4.hist_distance_by_interval = []
    advert4.rssi_distance = None  # neither history nor a live reading -> silently skipped

    summary = coordinator.calibrate_location(keys, "Pantry")

    assert s1.name in summary["scanners"]
    assert s3.name in summary["scanners"]  # fell back to rssi_distance
    assert s4.name not in summary["scanners"]  # no data at all, excluded


async def test_recalibrate_same_name_preserves_id_and_flags_replaced(hass, setup_bermuda_entry):
    """Re-calibrating the same device+name keeps the fingerprint's id/created and marks 'replaced'."""
    coordinator, keys, s1, s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)

    first = coordinator.calibrate_location(keys, "Key hook")
    assert first["replaced"] is False

    _set_distance(keys, s1, 3.0)
    _set_distance(keys, s2, 3.0)
    second = coordinator.calibrate_location(keys, "Key hook")

    assert second["id"] == first["id"]
    assert second["replaced"] is True


async def test_service_remove_location_unknown_name_raises(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "remove_location", {"device": "Keys", "name": "Nowhere"}, blocking=True, return_response=True
        )


async def test_service_remove_location_clears_active_microlocation(hass, setup_bermuda_entry):
    """Removing the spot a device is currently sitting at clears its micro_location_* fields."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")
    for _ in range(MICROLOC_HYSTERESIS_CYCLES):
        coordinator._refresh_microlocations()  # noqa: SLF001
    assert keys.micro_location_id is not None

    await hass.services.async_call(
        DOMAIN, "remove_location", {"device": "Keys", "name": "Key hook"}, blocking=True, return_response=True
    )

    assert keys.micro_location_id is None
    assert keys.micro_location_name is None
    assert keys.micro_location_confidence is None


async def test_service_rename_location_blank_new_name_raises(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "rename_location",
            {"device": "Keys", "name": "Key hook", "new_name": "   "},
            blocking=True,
            return_response=True,
        )


async def test_service_rename_location_unknown_name_raises(hass, setup_bermuda_entry):
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "rename_location",
            {"device": "Keys", "name": "Nope", "new_name": "New"},
            blocking=True,
            return_response=True,
        )


async def test_service_rename_location_updates_active_microlocation_name(hass, setup_bermuda_entry):
    """Renaming the spot a device is currently sitting at also updates its live micro_location_name."""
    coordinator, keys, _s1, _s2 = await _setup_keys_in_kitchen(hass, setup_bermuda_entry)
    coordinator.calibrate_location(keys, "Key hook")
    for _ in range(MICROLOC_HYSTERESIS_CYCLES):
        coordinator._refresh_microlocations()  # noqa: SLF001
    assert keys.micro_location_name == "Key hook"

    await hass.services.async_call(
        DOMAIN,
        "rename_location",
        {"device": "Keys", "name": "Key hook", "new_name": "Coat hook"},
        blocking=True,
        return_response=True,
    )

    assert keys.micro_location_name == "Coat hook"


async def test_service_track_device_second_call_reports_already_tracked(hass, setup_bermuda_entry):
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:04", "Widget2")

    first = await hass.services.async_call(
        DOMAIN, "track_device", {"device": "Widget2"}, blocking=True, return_response=True
    )
    assert first["already"] is False

    # Persisting options reloads the entry; re-fetch the fresh coordinator and re-register the
    # device before the second call (mirrors test_service_track_untrack_updates_options).
    await hass.async_block_till_done()
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:04", "Widget2")

    second = await hass.services.async_call(
        DOMAIN, "track_device", {"device": "Widget2"}, blocking=True, return_response=True
    )
    assert second["already"] is True


async def test_service_untrack_device_when_never_tracked(hass, setup_bermuda_entry):
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:05", "Widget3")

    resp = await hass.services.async_call(
        DOMAIN, "untrack_device", {"device": "Widget3"}, blocking=True, return_response=True
    )

    assert resp["already"] is True
    assert resp["tracked"] is False


async def test_service_get_config_lists_tracked_devices(hass, setup_bermuda_entry):
    """get_config's tracked_devices list is populated once CONF_DEVICES is non-empty."""
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:06", "Widget4")

    await hass.services.async_call(DOMAIN, "track_device", {"device": "Widget4"}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    coordinator = _coordinator(setup_bermuda_entry)
    await coordinator.fingerprints.async_load()
    _make_tracked_device(coordinator, "ec:00:00:00:00:06", "Widget4")

    cfg = await hass.services.async_call(DOMAIN, "get_config", {}, blocking=True, return_response=True)

    assert cfg["tracked_devices"] == [{"name": "Widget4", "address": "EC:00:00:00:00:06"}]
