"""Fewer recorder rows: a deadband on Distance, and Floor without area attributes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.bermuda import sensor as sensor_module
from custom_components.bermuda.sensor import BermudaSensor, BermudaSensorRange, distance_deadband

NOW = 10_000.0


def _range_sensor(distance):
    device = SimpleNamespace(area_distance=distance, ref_power_changed=0.0)
    entity = SimpleNamespace(
        _device=device,
        bermuda_update_interval=10,
        bermuda_last_state=0,  # what BermudaEntity starts with
        bermuda_last_stamp=0,
    )
    entity._cached_ratelimit = lambda value, **kw: BermudaSensorRange._cached_ratelimit(entity, value, **kw)
    return entity


def _publish(entity, distance, now):
    entity._device.area_distance = distance
    return BermudaSensorRange.native_value.fget(entity)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(sensor_module, "monotonic_time_coarse", lambda: clock["now"])
    from custom_components.bermuda import entity as entity_module

    monkeypatch.setattr(entity_module, "monotonic_time_coarse", lambda: clock["now"])
    return clock


def test_the_first_reading_is_published_even_when_small(_clock):
    entity = _range_sensor(0.1)
    assert _publish(entity, 0.1, NOW) == 0.1


def test_jitter_inside_the_deadband_is_held_even_after_the_cache_interval(_clock):
    entity = _range_sensor(2.0)
    assert _publish(entity, 2.0, NOW) == 2.0
    _clock["now"] = NOW + 60  # long past the 10 s cache
    assert _publish(entity, 2.1, NOW + 60) == 2.0  # 0.1 m < max(0.2, 10%)
    assert _publish(entity, 1.9, NOW + 60) == 2.0  # a small drop too
    assert _publish(entity, 2.5, NOW + 60) == 2.5  # a real move is published


def test_the_deadband_grows_with_distance():
    assert distance_deadband(1.0) == pytest.approx(0.2)
    assert distance_deadband(6.0) == pytest.approx(0.6)


def test_a_ref_power_change_is_shown_at_once(_clock):
    entity = _range_sensor(2.0)
    _publish(entity, 2.0, NOW)
    entity._device.ref_power_changed = NOW  # calibration just changed
    assert _publish(entity, 2.1, NOW) == 2.1


def _attrs(name):
    device = SimpleNamespace(
        address="aa:bb:cc:dd:ee:ff",
        address_type="public",
        adverts={},
        area_id="kitchen",
        area_name="Kitchen",
        floor_id="ground",
        floor_name="Ground",
        floor_level=0,
    )
    entity = SimpleNamespace(_device=device, name=name)
    return BermudaSensor.extra_state_attributes.fget(entity)


def test_the_floor_sensor_carries_only_floor_attributes():
    """An area change on the same floor must not rewrite the floor sensor."""
    assert _attrs("Floor") == {"floor_id": "ground", "floor_name": "Ground", "floor_level": 0}


def test_the_area_sensor_keeps_its_attributes():
    attrs = _attrs("Area")
    assert (
        attrs["area_id"] == "kitchen" and attrs["floor_id"] == "ground" and attrs["current_mac"] == "aa:bb:cc:dd:ee:ff"
    )
