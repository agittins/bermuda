"""Tests for per-scanner maximum detection radius during Area selection."""

from __future__ import annotations

from types import SimpleNamespace

from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.const import CONF_MAX_RADIUS
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def _advert(name, area_id, distance, scanner_max_radius=0):
    """Build a minimal stand-in for a BermudaAdvert and its scanner device.

    ``hist_distance_by_interval`` is kept shorter than the historical-test
    threshold so the decision rests on the distance/percentage tests alone,
    and the stamps are fresh so the stale-advert guard does not fire.
    """
    now = monotonic_time_coarse()
    return SimpleNamespace(
        name=name,
        area_id=area_id,
        area_name=area_id,
        rssi_distance=distance,
        stamp=now,
        hist_distance_by_interval=[distance, distance],
        scanner_device=SimpleNamespace(max_radius=scanner_max_radius, last_seen=now),
    )


def _device(adverts):
    """A device carrying the given adverts and no existing area winner."""
    selected = []
    return (
        SimpleNamespace(
            name="test device",
            adverts=adverts,
            area_advert=None,
            diag_area_switch=None,
            apply_scanner_selection=selected.append,
        ),
        selected,
    )


def _coordinator(global_max_radius=20.0):
    return SimpleNamespace(
        options={CONF_MAX_RADIUS: global_max_radius},
        AreaTests=BermudaDataUpdateCoordinator.AreaTests,
    )


def test_scanner_with_tighter_max_radius_cannot_win_beyond_it():
    """A scanner must not win an Area for a device beyond its own max_radius.

    The case this protects: a sparse install where one proxy over-reads, so it
    reports a device as nearer than the proxy actually in the room with it. The
    global max_radius cannot fix this without also cutting off the proxies that
    need the range, and an RSSI offset penalises the over-reading proxy even
    when it is the one genuinely closest.

    Here the far proxy claims 2.0m but is capped at 1.0m, while the in-room
    proxy honestly reports 3.0m. The capped proxy must be excluded, so the
    in-room proxy wins despite reading further away.
    """
    far = _advert("far proxy", "area_far", distance=2.0, scanner_max_radius=1.0)
    near = _advert("near proxy", "area_near", distance=3.0)
    device, selected = _device({"near": near, "far": far})

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(_coordinator(), device)

    assert selected == [near]


def test_scanner_within_its_max_radius_still_wins():
    """The cap must only exclude readings beyond it, not the scanner itself.

    Same layout as above, but the capped proxy's reading is inside its own
    limit, so ordinary "closest wins" behaviour must be unchanged.
    """
    far = _advert("far proxy", "area_far", distance=0.8, scanner_max_radius=1.0)
    near = _advert("near proxy", "area_near", distance=3.0)
    device, selected = _device({"near": near, "far": far})

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(_coordinator(), device)

    assert selected == [far]


def test_zero_max_radius_falls_back_to_the_global_limit():
    """max_radius of 0 means "unset", so the global limit applies.

    Both proxies are beyond the (deliberately tiny) global radius, so nothing
    may win the Area.
    """
    far = _advert("far proxy", "area_far", distance=6.0, scanner_max_radius=0)
    near = _advert("near proxy", "area_near", distance=5.0)
    device, selected = _device({"near": near, "far": far})

    BermudaDataUpdateCoordinator._refresh_area_by_min_distance(_coordinator(global_max_radius=4.0), device)

    assert selected == [None]
