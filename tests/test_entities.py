"""Behavioural tests for Bermuda entity classes (sensor/number/device_tracker/entity).

These tests exercise the *behaviour* of entity properties (``native_value``,
``icon``, ``extra_state_attributes``, ``state``, ``source_type``, the rate-limit
cache) WITHOUT touching ``unique_id`` (which is pinned separately in
``test_unique_id_regression.py``). Entities are instantiated with
``object.__new__`` and have only the attributes their property under test reads
set on them, so no running ``hass`` is required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.const import STATE_NOT_HOME, STATE_UNAVAILABLE

from custom_components.bermuda.const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_RANDOM_STATIC,
)
from custom_components.bermuda.device_tracker import BermudaDeviceTracker
from custom_components.bermuda.entity import BermudaEntity, BermudaGlobalEntity
from custom_components.bermuda.number import BermudaNumber
from custom_components.bermuda.sensor import (
    BermudaActiveProxyCount,
    BermudaSensor,
    BermudaSensorAreaLastSeen,
    BermudaSensorAreaSwitchReason,
    BermudaSensorFloor,
    BermudaSensorRange,
    BermudaSensorRssi,
    BermudaSensorScanner,
    BermudaSensorScannerRange,
    BermudaSensorScannerRangeRaw,
    BermudaTotalDeviceCount,
    BermudaTotalProxyCount,
    BermudaVisibleDeviceCount,
)


def _make_entity(cls):
    """Instantiate an entity without running __init__ (no hass needed)."""
    return object.__new__(cls)


# --------------------------------------------------------------------------- #
# BermudaSensor (area sensor)                                                  #
# --------------------------------------------------------------------------- #


def test_area_sensor_native_value_returns_area_name():
    """When the device has an area, native_value is that area name."""
    ent = _make_entity(BermudaSensor)
    ent._device = SimpleNamespace(area_name="Kitchen")
    assert ent.native_value == "Kitchen"


def test_area_sensor_native_value_not_home_when_no_area():
    """When area_name is None, native_value falls back to STATE_NOT_HOME."""
    ent = _make_entity(BermudaSensor)
    ent._device = SimpleNamespace(area_name=None)
    assert ent.native_value == STATE_NOT_HOME


def test_area_sensor_icon_uses_device_area_icon():
    """The area sensor delegates its icon to the device's area_icon."""
    ent = _make_entity(BermudaSensor)
    ent._device = SimpleNamespace(area_icon="mdi:home")
    assert ent.icon == "mdi:home"


def test_area_sensor_has_no_device_class():
    """The text area sensor exposes no device_class (so it shows up in the HA logbook)."""
    ent = _make_entity(BermudaSensor)
    assert ent.device_class is None


def test_area_sensor_extra_attributes_plain_device():
    """For a plain (non-meta) device, current_mac is the raw address and area branch is present."""
    ent = _make_entity(BermudaSensor)
    ent._device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        area_id="area_1",
        area_name="Lounge",
        floor_id="floor_1",
        floor_name="Ground",
        floor_level=0,
        micro_location_name="Key hook",
        micro_location_confidence=0.8,
    )
    attribs = ent.extra_state_attributes
    assert attribs["current_mac"] == "aa:bb:cc:dd:ee:ff"
    # translation_key defaults to "area" at the class level, so the area branch runs
    assert attribs["area_id"] == "area_1"
    assert attribs["area_name"] == "Lounge"
    assert attribs["floor_id"] == "floor_1"
    assert attribs["floor_name"] == "Ground"
    assert attribs["floor_level"] == 0
    # The area branch also surfaces the micro-location for dashboards/automations.
    assert attribs["micro_location"] == "Key hook"
    assert attribs["micro_location_confidence"] == 0.8


def test_area_sensor_extra_attributes_metadevice_picks_latest_mac():
    """For a metadevice (iBeacon/IRK), current_mac is the source advert with the highest stamp."""
    ent = _make_entity(BermudaSensor)
    adverts = {
        "src_old": SimpleNamespace(stamp=10.0, device_address="old:mac"),
        "src_new": SimpleNamespace(stamp=50.0, device_address="new:mac"),
        "src_mid": SimpleNamespace(stamp=20.0, device_address="mid:mac"),
    }
    ent._device = SimpleNamespace(
        address="meta-address",
        address_type=ADDR_TYPE_IBEACON,
        area_id="a",
        area_name="A",
        floor_id="f",
        floor_name="F",
        floor_level=1,
        micro_location_name=None,
        micro_location_confidence=None,
        adverts=adverts,
    )
    attribs = ent.extra_state_attributes
    assert attribs["current_mac"] == "new:mac"


def test_area_sensor_extra_attributes_metadevice_no_valid_adverts():
    """A metadevice with no positive-stamp adverts reports current_mac == STATE_UNAVAILABLE."""
    ent = _make_entity(BermudaSensor)
    ent._device = SimpleNamespace(
        address="meta-address",
        address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE,
        area_id="a",
        area_name="A",
        floor_id="f",
        floor_name="F",
        floor_level=1,
        micro_location_name=None,
        micro_location_confidence=None,
        adverts={"src": SimpleNamespace(stamp=0, device_address="never:used")},
    )
    attribs = ent.extra_state_attributes
    assert attribs["current_mac"] == STATE_UNAVAILABLE


# --------------------------------------------------------------------------- #
# BermudaSensorFloor                                                           #
# --------------------------------------------------------------------------- #


def test_floor_sensor_native_value():
    """Floor sensor returns floor_name, or STATE_NOT_HOME when None."""
    ent = _make_entity(BermudaSensorFloor)
    ent._device = SimpleNamespace(floor_name="Upstairs")
    assert ent.native_value == "Upstairs"

    ent_none = _make_entity(BermudaSensorFloor)
    ent_none._device = SimpleNamespace(floor_name=None)
    assert ent_none.native_value == STATE_NOT_HOME


def test_floor_sensor_icon():
    """Floor sensor uses the device's floor_icon."""
    ent = _make_entity(BermudaSensorFloor)
    ent._device = SimpleNamespace(floor_icon="mdi:floor-plan")
    assert ent.icon == "mdi:floor-plan"


def test_floor_sensor_extra_attributes_includes_area_branch():
    """The floor sensor's translation_key is 'floor' so the area/floor branch runs."""
    ent = _make_entity(BermudaSensorFloor)
    ent._device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        area_id="a1",
        area_name="Hall",
        floor_id="f1",
        floor_name="First",
        floor_level=2,
        micro_location_name=None,
        micro_location_confidence=None,
    )
    attribs = ent.extra_state_attributes
    assert attribs["floor_name"] == "First"
    assert attribs["floor_level"] == 2
    assert attribs["current_mac"] == "aa:bb:cc:dd:ee:ff"


# --------------------------------------------------------------------------- #
# BermudaSensorScanner                                                         #
# --------------------------------------------------------------------------- #


def test_scanner_sensor_native_value_returns_scanner_name():
    """When area_advert resolves to a known scanner device, its name is returned."""
    ent = _make_entity(BermudaSensorScanner)
    scanner_device = SimpleNamespace(name="Proxy Living Room")
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {"scanner-addr": scanner_device}
    ent._device = SimpleNamespace(area_advert=SimpleNamespace(scanner_address="scanner-addr"))
    assert ent.native_value == "Proxy Living Room"


def test_scanner_sensor_native_value_not_home_when_no_advert():
    """No area_advert => STATE_NOT_HOME."""
    ent = _make_entity(BermudaSensorScanner)
    ent._device = SimpleNamespace(area_advert=None)
    assert ent.native_value == STATE_NOT_HOME


def test_scanner_sensor_native_value_not_home_when_scanner_unknown():
    """area_advert present but the scanner address isn't in coordinator.devices => STATE_NOT_HOME."""
    ent = _make_entity(BermudaSensorScanner)
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {}
    ent._device = SimpleNamespace(area_advert=SimpleNamespace(scanner_address="missing"))
    assert ent.native_value == STATE_NOT_HOME


def test_scanner_sensor_exposes_nearest_scanner_entity_id():
    """The nearest-scanner sensor surfaces the scanner device's HA entity_id (PR #374)."""
    ent = _make_entity(BermudaSensorScanner)
    scanner_device = SimpleNamespace(name="Proxy", scanner_entity_id="switch.proxy_relay")
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {"scanner-addr": scanner_device}
    ent._device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        area_advert=SimpleNamespace(scanner_address="scanner-addr"),
    )
    assert ent.extra_state_attributes["scanner_entity_id"] == "switch.proxy_relay"


def test_scanner_sensor_no_entity_id_attr_when_scanner_has_none():
    """When the nearest scanner has no resolved entity_id, the attribute is omitted."""
    ent = _make_entity(BermudaSensorScanner)
    scanner_device = SimpleNamespace(name="Proxy", scanner_entity_id=None)
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {"scanner-addr": scanner_device}
    ent._device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        area_advert=SimpleNamespace(scanner_address="scanner-addr"),
    )
    assert "scanner_entity_id" not in ent.extra_state_attributes


# --------------------------------------------------------------------------- #
# BermudaSensorAreaSwitchReason                                                #
# --------------------------------------------------------------------------- #


def test_area_switch_reason_state_is_concise_reason_with_diagnostic_attr():
    """State is the concise reason; the full AreaTests dump is an attribute (PR #753)."""
    ent = _make_entity(BermudaSensorAreaSwitchReason)
    ent._device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        diag_area_switch_reason="WIN by not losing!",
        diag_area_switch="device|Phone\nreason|WIN by not losing!\n",
    )
    assert ent.native_value == "WIN by not losing!"
    assert ent.extra_state_attributes["diagnostic"] == "device|Phone\nreason|WIN by not losing!\n"


def test_area_switch_reason_state_none_when_no_switch_recorded():
    """With no recorded reason the state is None."""
    ent = _make_entity(BermudaSensorAreaSwitchReason)
    ent._device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        diag_area_switch_reason=None,
        diag_area_switch=None,
    )
    assert ent.native_value is None


# --------------------------------------------------------------------------- #
# BermudaSensorRssi / BermudaSensorRange (via _cached_ratelimit)               #
# --------------------------------------------------------------------------- #


def _prime_cache(ent, last_state, *, interval=10, stale=False):
    """Set up an entity's per-device cache so _cached_ratelimit can be exercised."""
    now = monotonic_time_coarse()
    ent.bermuda_update_interval = interval
    ent.bermuda_last_state = last_state
    # Make the cache "fresh" (not stale) by default so cached-path can be hit.
    ent.bermuda_last_stamp = now if not stale else now - (interval + 100)
    # ref_power not recently changed
    ent._device = SimpleNamespace(ref_power_changed=0)
    return ent


def test_rssi_sensor_native_value_fast_rising_returns_new():
    """RSSI uses fast_rising: a higher (less negative) value bypasses the cache immediately."""
    ent = _make_entity(BermudaSensorRssi)
    _prime_cache(ent, last_state=-90)
    ent._device.area_rssi = -50  # rising
    assert ent.native_value == -50


def test_rssi_sensor_native_value_cached_when_not_rising():
    """A lower RSSI within the fresh interval returns the cached (previous) value."""
    ent = _make_entity(BermudaSensorRssi)
    _prime_cache(ent, last_state=-50)
    ent._device.area_rssi = -70  # falling, but fast_falling is False for RSSI
    assert ent.native_value == -50  # served from cache


def test_range_sensor_native_value_fast_falling_returns_new():
    """Range uses fast_falling (default): a smaller distance bypasses the cache immediately."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=5.0)
    ent._device.area_distance = 2.0  # falling
    assert ent.native_value == 2.0


def test_range_sensor_native_value_cached_when_rising():
    """A larger distance within the fresh interval returns the cached value."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=2.0)
    ent._device.area_distance = 8.0  # rising, fast_falling won't trip
    assert ent.native_value == 2.0  # cached


def test_range_sensor_native_value_none_distance():
    """When the device has no area_distance, native_value is None."""
    ent = _make_entity(BermudaSensorRange)
    ent._device = SimpleNamespace(area_distance=None)
    assert ent.native_value is None


def test_range_sensor_rounds_to_one_decimal():
    """area_distance is rounded to 1 decimal place before publishing."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=None, stale=True)
    ent._device.area_distance = 3.456
    assert ent.native_value == 3.5


# --------------------------------------------------------------------------- #
# _cached_ratelimit edge cases (entity.py)                                     #
# --------------------------------------------------------------------------- #


def test_cached_ratelimit_stale_cache_publishes_new():
    """A stale cache always publishes (and stores) the fresh value."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=99, stale=True)
    result = ent._cached_ratelimit(7, fast_falling=False, fast_rising=False)
    assert result == 7
    assert ent.bermuda_last_state == 7


def test_cached_ratelimit_ref_power_change_bypasses_cache():
    """If ref_power changed in the last 2s the cache is bypassed even when fresh."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=1)
    ent._device.ref_power_changed = monotonic_time_coarse()  # just changed
    result = ent._cached_ratelimit(123, fast_falling=False, fast_rising=False)
    assert result == 123


def test_cached_ratelimit_none_statevalue_published():
    """A None statevalue is always published (the 'or you' clause)."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=5)
    result = ent._cached_ratelimit(None, fast_falling=False, fast_rising=False)
    assert result is None


def test_cached_ratelimit_interval_override_makes_fresh():
    """A per-call interval controls staleness without mutating the stored default."""
    ent = _make_entity(BermudaSensorRange)
    _prime_cache(ent, last_state=42)
    stored = ent.bermuda_update_interval
    # huge interval => cache considered fresh, and value is not falling/rising
    result = ent._cached_ratelimit(43, fast_falling=False, fast_rising=False, interval=100000)
    assert result == 42  # cached
    # The one-off interval must not stick to the entity's configured default.
    assert ent.bermuda_update_interval == stored


# --------------------------------------------------------------------------- #
# BermudaSensorScannerRange                                                    #
# --------------------------------------------------------------------------- #


def test_scanner_range_translation_placeholders():
    """The per-scanner range entity exposes the scanner's name as a placeholder."""
    ent = _make_entity(BermudaSensorScannerRange)
    ent._scanner = SimpleNamespace(name="Bedroom Proxy")
    assert ent.translation_placeholders == {"scanner_name": "Bedroom Proxy"}


def test_scanner_range_native_value_returns_distance():
    """Returns the rssi_distance of the device's scanner record (rounded to 3dp)."""
    ent = _make_entity(BermudaSensorScannerRange)
    _prime_cache(ent, last_state=None, stale=True)
    ent._scanner = SimpleNamespace(address="scn")
    devscanner = SimpleNamespace(rssi_distance=4.123456)
    ent._device.get_scanner = MagicMock(return_value=devscanner)
    assert ent.native_value == 4.123


def test_scanner_range_native_value_none_when_scanner_absent():
    """When the device has never heard of this scanner, native_value is None."""
    ent = _make_entity(BermudaSensorScannerRange)
    ent._scanner = SimpleNamespace(address="scn")
    ent._device = SimpleNamespace(get_scanner=MagicMock(return_value=None))
    assert ent.native_value is None


def test_scanner_range_native_value_none_when_distance_none():
    """A known scanner record with rssi_distance None yields None."""
    ent = _make_entity(BermudaSensorScannerRange)
    ent._scanner = SimpleNamespace(address="scn")
    devscanner = SimpleNamespace(rssi_distance=None)
    ent._device = SimpleNamespace(get_scanner=MagicMock(return_value=devscanner))
    assert ent.native_value is None


def test_scanner_range_extra_attributes_present():
    """When the device knows the scanner, the area attributes come from the scanner device."""
    ent = _make_entity(BermudaSensorScannerRange)
    ent._scanner = SimpleNamespace(
        address="scn-mac",
        area_id="kitchen_id",
        area_name="Kitchen",
        name="Kitchen Proxy",
    )
    ent._device = SimpleNamespace(get_scanner=MagicMock(return_value=SimpleNamespace()))
    attribs = ent.extra_state_attributes
    assert attribs == {
        "area_id": "kitchen_id",
        "area_name": "Kitchen",
        "area_scanner_mac": "scn-mac",
        "area_scanner_name": "Kitchen Proxy",
    }


def test_scanner_range_extra_attributes_none_when_scanner_absent():
    """No scanner record => extra_state_attributes is None."""
    ent = _make_entity(BermudaSensorScannerRange)
    ent._scanner = SimpleNamespace(address="scn")
    ent._device = SimpleNamespace(get_scanner=MagicMock(return_value=None))
    assert ent.extra_state_attributes is None


# --------------------------------------------------------------------------- #
# BermudaSensorScannerRangeRaw                                                 #
# --------------------------------------------------------------------------- #


def test_scanner_range_raw_native_value_unfiltered():
    """Raw variant returns rssi_distance_raw rounded to 3dp, without rate-limiting."""
    ent = _make_entity(BermudaSensorScannerRangeRaw)
    ent._scanner = SimpleNamespace(address="scn")
    devscanner = SimpleNamespace(rssi_distance_raw=6.98765)
    ent._device = SimpleNamespace(get_scanner=MagicMock(return_value=devscanner))
    assert ent.native_value == 6.988


def test_scanner_range_raw_native_value_none_when_absent():
    """No scanner record => None (getattr default)."""
    ent = _make_entity(BermudaSensorScannerRangeRaw)
    ent._scanner = SimpleNamespace(address="scn")
    ent._device = SimpleNamespace(get_scanner=MagicMock(return_value=None))
    assert ent.native_value is None


# --------------------------------------------------------------------------- #
# BermudaSensorAreaSwitchReason / AreaLastSeen                                 #
# --------------------------------------------------------------------------- #


def test_area_switch_reason_native_value_truncated():
    """The concise reason is defensively truncated to 255 characters."""
    ent = _make_entity(BermudaSensorAreaSwitchReason)
    long_reason = "x" * 400
    ent._device = SimpleNamespace(diag_area_switch_reason=long_reason, diag_area_switch=None)
    value = ent.native_value
    assert value == "x" * 255
    assert len(value) == 255


def test_area_last_seen_native_value_and_icon():
    """Area-last-seen mirrors the device's area_last_seen value and icon."""
    ent = _make_entity(BermudaSensorAreaLastSeen)
    ent._device = SimpleNamespace(area_last_seen="Garage", area_last_seen_icon="mdi:garage")
    assert ent.native_value == "Garage"
    assert ent.icon == "mdi:garage"


# --------------------------------------------------------------------------- #
# BermudaDeviceTracker                                                         #
# --------------------------------------------------------------------------- #


def test_device_tracker_state_is_zone():
    """The tracker state mirrors the device's computed zone."""
    ent = _make_entity(BermudaDeviceTracker)
    ent._device = SimpleNamespace(zone="home")
    assert ent.state == "home"


def test_device_tracker_source_type():
    """Tracker source type is BLUETOOTH_LE."""
    ent = _make_entity(BermudaDeviceTracker)
    assert ent.source_type == SourceType.BLUETOOTH_LE


def test_device_tracker_icon_home_vs_away():
    """Icon flips based on whether the device is in the home zone."""
    home = _make_entity(BermudaDeviceTracker)
    home._device = SimpleNamespace(zone="home")
    assert home.icon == "mdi:bluetooth-connect"

    away = _make_entity(BermudaDeviceTracker)
    away._device = SimpleNamespace(zone="not_home")
    assert away.icon == "mdi:bluetooth-off"


def test_device_tracker_extra_attributes_with_advert():
    """When an area_advert exists, scanner name is exposed alongside area."""
    ent = _make_entity(BermudaDeviceTracker)
    ent._device = SimpleNamespace(
        area_advert=SimpleNamespace(name="Hallway Proxy"),
        area_name="Hallway",
    )
    assert ent.extra_state_attributes == {"scanner": "Hallway Proxy", "area": "Hallway"}


def test_device_tracker_extra_attributes_without_advert():
    """When there is no area_advert, scanner is None but area is still reported."""
    ent = _make_entity(BermudaDeviceTracker)
    ent._device = SimpleNamespace(area_advert=None, area_name=None)
    assert ent.extra_state_attributes == {"scanner": None, "area": None}


# --------------------------------------------------------------------------- #
# BermudaNumber                                                                #
# --------------------------------------------------------------------------- #


def test_number_native_value_reads_device_ref_power():
    """The ref_power number reads the value off the addressed device."""
    ent = _make_entity(BermudaNumber)
    ent.address = "aa:bb:cc:dd:ee:ff"
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {"aa:bb:cc:dd:ee:ff": SimpleNamespace(ref_power=-59.0)}
    assert ent.native_value == -59.0


def test_number_native_value_none():
    """A device with no ref_power set returns None."""
    ent = _make_entity(BermudaNumber)
    ent.address = "dev"
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {"dev": SimpleNamespace(ref_power=None)}
    assert ent.native_value is None


# --------------------------------------------------------------------------- #
# BermudaGlobalEntity._cached_ratelimit                                        #
# --------------------------------------------------------------------------- #


def _prime_global_cache(ent, value, *, interval=60, fresh=True):
    now = monotonic_time_coarse()
    ent._cache_ratelimit_interval = interval
    ent._cache_ratelimit_value = value
    # If fresh, the stamp is recent so the (now > stamp + interval) test is False.
    ent._cache_ratelimit_stamp = now if fresh else now - (interval + 100)
    return ent


def test_global_cached_ratelimit_serves_cache_when_fresh():
    """A fresh global cache returns the stored value, ignoring the new one."""
    ent = _make_entity(BermudaTotalProxyCount)
    _prime_global_cache(ent, value=3, fresh=True)
    assert ent._cached_ratelimit(99) == 3


def test_global_cached_ratelimit_publishes_when_stale():
    """A stale global cache publishes (and stores) the new value."""
    ent = _make_entity(BermudaTotalProxyCount)
    _prime_global_cache(ent, value=3, fresh=False)
    assert ent._cached_ratelimit(7) == 7
    assert ent._cache_ratelimit_value == 7


def test_global_cached_ratelimit_interval_override():
    """A per-call interval drives the freshness window without mutating the stored default."""
    ent = _make_entity(BermudaTotalProxyCount)
    _prime_global_cache(ent, value=5, fresh=False)
    stored = ent._cache_ratelimit_interval
    # tiny interval and a stale-ish stamp => new value published
    assert ent._cached_ratelimit(8, interval=0) == 8
    # The one-off interval must not stick to the entity's configured default.
    assert ent._cache_ratelimit_interval == stored


# --------------------------------------------------------------------------- #
# Global sensors native_value                                                  #
# --------------------------------------------------------------------------- #


def test_total_proxy_count_native_value():
    """Total proxy count = len(coordinator.scanner_list)."""
    ent = _make_entity(BermudaTotalProxyCount)
    _prime_global_cache(ent, value=None, fresh=False)
    ent.coordinator = MagicMock()
    ent.coordinator.scanner_list = ["a", "b", "c"]
    assert ent.native_value == 3


def test_total_proxy_count_zero_fallback():
    """Empty scanner_list yields 0 (via the `or 0` guard)."""
    ent = _make_entity(BermudaTotalProxyCount)
    _prime_global_cache(ent, value=None, fresh=False)
    ent.coordinator = MagicMock()
    ent.coordinator.scanner_list = []
    assert ent.native_value == 0


def test_active_proxy_count_native_value():
    """Active proxy count comes from coordinator.count_active_scanners()."""
    ent = _make_entity(BermudaActiveProxyCount)
    _prime_global_cache(ent, value=None, fresh=False)
    ent.coordinator = MagicMock()
    ent.coordinator.count_active_scanners = MagicMock(return_value=2)
    assert ent.native_value == 2


def test_active_proxy_count_extra_attributes_groups_by_area():
    """Active-proxy attributes count only recently-seen scanners and group them by area."""
    ent = _make_entity(BermudaActiveProxyCount)
    ent.coordinator = MagicMock()
    ent.coordinator.get_active_scanner_summary = MagicMock(
        return_value=[
            {"area_name": "Kitchen", "last_stamp_age": 1},
            {"area_name": "Kitchen", "last_stamp_age": 5},
            {"area_name": "Lounge", "last_stamp_age": 2},
            {"area_name": "Attic", "last_stamp_age": 999},  # too old, excluded
            {"area_name": "null", "last_stamp_age": 1},  # 'null' excluded from areas
            {"area_name": None, "last_stamp_age": 1},  # None excluded from areas
        ]
    )
    attribs = ent.extra_state_attributes
    assert attribs["areas"] == {"Kitchen": 2, "Lounge": 1}
    # total_active counts everything <= max_age (10s): Kitchen x2, Lounge, null, None = 5
    assert attribs["total_active"] == 5


def test_total_device_count_native_value():
    """Total device count = len(coordinator.devices)."""
    ent = _make_entity(BermudaTotalDeviceCount)
    _prime_global_cache(ent, value=None, fresh=False)
    ent.coordinator = MagicMock()
    ent.coordinator.devices = {"a": 1, "b": 2}
    assert ent.native_value == 2


def test_visible_device_count_native_value():
    """Visible device count comes from coordinator.count_active_devices()."""
    ent = _make_entity(BermudaVisibleDeviceCount)
    _prime_global_cache(ent, value=None, fresh=False)
    ent.coordinator = MagicMock()
    ent.coordinator.count_active_devices = MagicMock(return_value=4)
    assert ent.native_value == 4


def test_global_entity_device_info():
    """The global entity groups counters under the single BERMUDA_GLOBAL identifier."""
    info = _make_entity(BermudaGlobalEntity).device_info
    assert info["name"] == "Bermuda Global"


# --------------------------------------------------------------------------- #
# BermudaEntity.device_info behaviour (non unique_id parts)                    #
# --------------------------------------------------------------------------- #


def test_entity_device_info_name_propagates():
    """device_info carries the device's display name."""
    ent = _make_entity(BermudaEntity)
    ent._device = SimpleNamespace(
        is_scanner=False,
        address_type=BDADDR_TYPE_RANDOM_STATIC,
        address="aa:bb:cc:dd:ee:ff",
        unique_id="aa:bb:cc:dd:ee:ff",
        name="My Phone",
    )
    info = ent.device_info
    assert info["name"] == "My Phone"


# --------------------------------------------------------------------------- #
# BermudaSensorScannerRange.available                                          #
# --------------------------------------------------------------------------- #


def test_scanner_range_available_tracks_roster_and_coordinator():
    """A per-scanner range sensor is available only while its proxy is in the roster."""
    ent = _make_entity(BermudaSensorScannerRange)
    ent._scanner = SimpleNamespace(address="aa:bb:cc:dd:ee:ff")

    # In the roster, coordinator healthy -> available.
    ent.coordinator = SimpleNamespace(last_update_success=True, scanner_list={"aa:bb:cc:dd:ee:ff"})
    assert ent.available is True

    # Proxy dropped from the roster -> unavailable.
    ent.coordinator = SimpleNamespace(last_update_success=True, scanner_list=set())
    assert ent.available is False

    # Coordinator update failure -> unavailable even while still in the roster.
    ent.coordinator = SimpleNamespace(last_update_success=False, scanner_list={"aa:bb:cc:dd:ee:ff"})
    assert ent.available is False
