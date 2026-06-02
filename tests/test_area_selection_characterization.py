"""Characterization tests for the area-selection / trilateration race.

These pin the current behaviour of
``BermudaDataUpdateCoordinator._refresh_area_by_min_distance`` — the per-device
"closest scanner wins" race with hysteresis — so that extracting it into a
dedicated ``trilateration`` module can be proven behaviour-preserving.

The decision logic (per challenger advert, versus the reigning incumbent):

* skip the incumbent itself, stale adverts, adverts beyond ``max_radius`` and
  adverts with no area;
* if the incumbent is invalid (None / no distance / no area) the challenger wins
  instantly;
* a challenger that is not actually closer cannot win;
* otherwise hysteresis applies — a same-area newer+closer advert wins, a
  historical min/max beat wins, and finally a percentage-distance gap below
  ``pdiff_outright`` (0.30) loses while a larger gap wins.

The coordinator is built with ``object.__new__`` because the method only needs
``self.options`` and ``self.AreaTests``; no running hass is required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.const import CONF_MAX_RADIUS
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

NOW = monotonic_time_coarse()


def _advert(name, area_id, distance, *, stamp=None, history=None):
    """Build a minimal BermudaAdvert stand-in for the area race."""
    return SimpleNamespace(
        name=name,
        area_id=area_id,
        area_name=area_id.title() if area_id else None,
        rssi_distance=distance,
        stamp=NOW if stamp is None else stamp,
        scanner_device=SimpleNamespace(last_seen=NOW),
        hist_distance_by_interval=history if history is not None else [],
    )


@pytest.fixture
def coordinator():
    """Coordinator stub exposing only what the area race touches."""
    coord = object.__new__(BermudaDataUpdateCoordinator)
    coord.options = {CONF_MAX_RADIUS: 20.0}
    return coord


def _run(coord, area_advert, adverts):
    """Drive the race and return the selected advert."""
    selected = []
    device = SimpleNamespace(
        name="tracked",
        area_advert=area_advert,
        adverts={i: ad for i, ad in enumerate(adverts)},
        diag_area_switch=None,
        apply_scanner_selection=lambda adv: selected.append(adv),
    )
    coord._refresh_area_by_min_distance(device)
    assert len(selected) == 1
    return selected[0]


def test_instant_win_when_no_incumbent(coordinator):
    """With no current area, the first valid challenger is selected."""
    challenger = _advert("Lounge Proxy", "lounge", 3.0)
    chosen = _run(coordinator, area_advert=None, adverts=[challenger])
    assert chosen is challenger


def test_farther_challenger_cannot_unseat_incumbent(coordinator):
    """A challenger that is farther than the incumbent never wins."""
    incumbent = _advert("Kitchen Proxy", "kitchen", 2.0)
    challenger = _advert("Lounge Proxy", "lounge", 5.0)
    chosen = _run(coordinator, area_advert=incumbent, adverts=[incumbent, challenger])
    assert chosen is incumbent


def test_marginally_closer_challenger_loses_on_hysteresis(coordinator):
    """A closer challenger below the 0.30 percentage-diff threshold is rejected."""
    incumbent = _advert("Kitchen Proxy", "kitchen", 3.0)
    challenger = _advert("Lounge Proxy", "lounge", 2.9)  # pcnt_diff ~= 0.034
    chosen = _run(coordinator, area_advert=incumbent, adverts=[incumbent, challenger])
    assert chosen is incumbent


def test_significantly_closer_challenger_wins(coordinator):
    """A challenger well past the 0.30 threshold takes the area."""
    incumbent = _advert("Kitchen Proxy", "kitchen", 5.0)
    challenger = _advert("Lounge Proxy", "lounge", 2.0)  # pcnt_diff ~= 0.857
    chosen = _run(coordinator, area_advert=incumbent, adverts=[incumbent, challenger])
    assert chosen is challenger


def test_too_far_challenger_is_ignored(coordinator):
    """A challenger beyond max_radius cannot win, even with no incumbent."""
    challenger = _advert("Distant Proxy", "garage", 25.0)  # > max_radius 20
    chosen = _run(coordinator, area_advert=None, adverts=[challenger])
    assert chosen is None


def test_stale_challenger_is_ignored(coordinator):
    """A challenger whose advert is older than AREA_MAX_AD_AGE is skipped."""
    challenger = _advert("Old Proxy", "attic", 1.0, stamp=NOW - 3600)
    chosen = _run(coordinator, area_advert=None, adverts=[challenger])
    assert chosen is None
