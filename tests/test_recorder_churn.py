"""Fewer recorder rows: a deadband on Distance, and Floor without area attributes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.bermuda import sensor as sensor_module
from custom_components.bermuda.sensor import (
    BermudaSensor,
    BermudaSensorRange,
    distance_deadband,
    distance_fast_fall,
)

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
    _clock["now"] = NOW + 120
    assert _publish(entity, 1.9, NOW + 120) == 2.0  # a small drop too
    _clock["now"] = NOW + 180
    assert _publish(entity, 2.5, NOW + 180) == 2.5  # a real move is published


def test_the_deadband_grows_with_distance():
    assert distance_deadband(1.0) == pytest.approx(0.2)
    assert distance_deadband(6.0) == pytest.approx(0.6)
    assert distance_fast_fall(2.0) == pytest.approx(1.0)
    assert distance_fast_fall(8.0) == pytest.approx(2.0)


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


def test_a_device_coming_back_is_published_as_it_is(_clock):
    """Out of range in between: the returning reading must not be measured
    against the distance from before it went."""
    entity = _range_sensor(2.0)
    assert _publish(entity, 2.0, NOW) == 2.0
    assert _publish(entity, None, NOW + 5) is None
    _clock["now"] = NOW + 300
    assert _publish(entity, 1.9, NOW + 300) == 1.9


@pytest.mark.parametrize(
    ("last", "moved_to"),
    [(2.0, 1.8), (2.0, 2.2), (6.0, 5.4), (6.0, 6.6)],
)
def test_a_move_of_exactly_the_deadband_is_published(_clock, last, moved_to):
    # 2.0 - 1.8 is 0.19999999999999996 in floats: still a 0.2 m move.
    entity = _range_sensor(last)
    assert _publish(entity, last, NOW) == last
    _clock["now"] = NOW + 60
    assert _publish(entity, moved_to, NOW + 60) == moved_to
    _clock["now"] = NOW + 120
    assert _publish(entity, moved_to, NOW + 120) == moved_to


def test_a_move_just_inside_the_deadband_is_still_held(_clock):
    entity = _range_sensor(6.0)
    assert _publish(entity, 6.0, NOW) == 6.0
    _clock["now"] = NOW + 60
    assert _publish(entity, 5.5, NOW + 60) == 6.0
    assert _publish(entity, 6.5, NOW + 60) == 6.0


def test_readings_scattered_past_the_deadband_are_held(_clock):
    """Every reading is outside the deadband, one way or the other, but the
    median of the last interval's readings is not: nothing is published."""
    entity = _range_sensor(3.0)
    assert _publish(entity, 3.0, NOW) == 3.0
    for second in range(1, 60):
        _clock["now"] = NOW + second
        assert _publish(entity, (2.6, 3.0, 3.4)[second % 3], NOW + second) == 3.0


def test_a_stray_reading_at_the_check_does_not_hold_back_a_move(_clock):
    """A sustained move with one in-band reading when the interval comes up
    is published then, not held for as long as the strays keep landing."""
    entity = _range_sensor(3.0)
    assert _publish(entity, 3.0, NOW) == 3.0
    for second in range(1, 11):
        _clock["now"] = NOW + second
        _publish(entity, 3.6, NOW + second)
    _clock["now"] = NOW + 11
    assert _publish(entity, 3.1, NOW + 11) == 3.6


@pytest.mark.parametrize("moved_to", [3.6, 2.4])
def test_a_move_after_mid_interval_shows_within_an_interval(_clock, moved_to):
    """A move that starts after the middle of an interval loses that
    interval's median; it must not then wait out another whole interval."""
    entity = _range_sensor(3.0)
    assert _publish(entity, 3.0, NOW) == 3.0
    published = {}
    for second in range(1, 30):
        _clock["now"] = NOW + second
        published[second] = _publish(entity, 3.0 if second < 7 else moved_to, NOW + second)
    assert all(published[s] == 3.0 for s in range(1, 7))
    assert published[17] == moved_to  # within one 10 s interval of the move at 7 s
    assert published[29] == moved_to


def test_at_most_one_new_value_per_interval(_clock):
    entity = _range_sensor(3.0)
    assert _publish(entity, 3.0, NOW) == 3.0
    values = []
    for second in range(1, 41):
        _clock["now"] = NOW + second
        values.append(_publish(entity, 3.0 + 0.1 * second, NOW + second))  # a steady walk away
    changes = [i for i in range(1, len(values)) if values[i] != values[i - 1]]
    assert changes and all(b - a > 10 for a, b in zip(changes, changes[1:], strict=False))


def test_a_large_drop_is_shown_at_once(_clock):
    entity = _range_sensor(3.0)
    assert _publish(entity, 3.0, NOW) == 3.0
    _clock["now"] = NOW + 1
    assert _publish(entity, 2.5, NOW + 1) == 3.0  # a small drop waits for the interval
    assert _publish(entity, 2.0, NOW + 1) == 2.0  # 1 m: at once
    _clock["now"] = NOW + 2
    assert _publish(entity, 6.0, NOW + 2) == 2.0  # a rise waits
    entity = _range_sensor(8.0)
    assert _publish(entity, 8.0, NOW) == 8.0
    assert _publish(entity, 6.5, NOW) == 8.0  # 1.5 m < 25 % of 8 m
    assert _publish(entity, 6.0, NOW) == 6.0  # 25 %: at once
