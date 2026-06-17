"""
Characterization tests for score-based, mobility-aware area selection.

These pin the behaviour of ``trilateration.refresh_area_by_min_distance`` after
the move from "closest scanner wins" to RSSI-score contenders with adaptive
hysteresis and an explicit "Unknown" outcome. Adverts/devices are lightweight
SimpleNamespace stand-ins; the module clock is frozen so the module-level
stamps never age out under a slow suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.const import CONF_MAX_RADIUS, MOBILITY_MOVING, MOBILITY_STATIONARY
from custom_components.bermuda.trilateration import AreaDecisionState, refresh_area_by_min_distance

NOW = monotonic_time_coarse()


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch):
    """Pin trilateration's clock so the NOW-stamped adverts never age out."""
    monkeypatch.setattr("custom_components.bermuda.trilateration.monotonic_time_coarse", lambda: NOW)


def _advert(scanner: str, area: str | None, rssi_filtered: float, distance: float, *, stamp=None, dispersion=1.0):
    """Build a minimal BermudaAdvert stand-in for the area race."""
    return SimpleNamespace(
        scanner_address=scanner,
        name=scanner,
        area_id=f"{area}_id" if area else None,
        area_name=area,
        rssi_distance=distance,
        rssi_filtered=rssi_filtered,
        rssi=rssi_filtered,
        conf_rssi_offset=0.0,
        rssi_dispersion=dispersion,
        stamp=NOW if stamp is None else stamp,
        scanner_device=SimpleNamespace(last_seen=NOW),
    )


def _run(area_advert, adverts, *, mobility=MOBILITY_MOVING, state=None, max_radius=20.0, cycles=1):
    """Drive the area race ``cycles`` times; return (device, applied_calls)."""
    applied: list[tuple[object | None, bool]] = []
    device = SimpleNamespace(
        name="dev",
        area_advert=area_advert,
        adverts={i: ad for i, ad in enumerate(adverts)},
        diag_area_switch=None,
        diag_area_switch_reason=None,
        area_decision_state=state if state is not None else AreaDecisionState(),
        get_mobility_type=lambda: mobility,
    )

    def _apply(advert, *, force_unknown=False):
        applied.append((advert, force_unknown))
        device.area_advert = advert

    device.apply_scanner_selection = _apply
    for _ in range(cycles):
        refresh_area_by_min_distance(device, {CONF_MAX_RADIUS: max_radius})
    return device, applied


# ---------------------------------------------------------------------------
# Contender validity
# ---------------------------------------------------------------------------


def test_initial_win_when_no_incumbent():
    """With no incumbent, the single valid contender is selected (not Unknown)."""
    challenger = _advert("s1", "Kitchen", -60.0, 2.0)
    _device, applied = _run(None, [challenger])
    assert applied[-1] == (challenger, False)


def test_too_far_challenger_is_not_a_contender():
    """A reading beyond max_radius is dropped; with nothing left, selection is None."""
    far = _advert("s1", "Garage", -60.0, 30.0)  # distance 30 > max_radius 20
    _device, applied = _run(None, [far])
    assert applied[-1] == (None, False)


def test_stale_advert_is_not_a_contender():
    """An advert older than AREA_MAX_AD_AGE cannot win."""
    stale = _advert("s1", "Garage", -60.0, 2.0, stamp=NOW - 3600)
    _device, applied = _run(None, [stale])
    assert applied[-1] == (None, False)


def test_advert_without_area_is_not_a_contender():
    """A scanner with no assigned area cannot win."""
    no_area = _advert("s1", None, -60.0, 2.0)
    _device, applied = _run(None, [no_area])
    assert applied[-1] == (None, False)


# ---------------------------------------------------------------------------
# Hysteresis: fast lane / hold / slow lane
# ---------------------------------------------------------------------------


def test_fast_lane_clear_winner_takes_over_immediately():
    """A challenger whose score dwarfs the incumbent wins in one cycle."""
    incumbent = _advert("s1", "Garage", -85.0, 6.0)
    challenger = _advert("s2", "Kitchen", -55.0, 1.5)  # ~30 dB stronger -> huge score ratio
    _device, applied = _run(incumbent, [incumbent, challenger])
    assert applied[-1][0] is challenger
    assert applied[-1][1] is False


def test_incumbent_with_best_score_is_held():
    """When the incumbent already has the best score, it is kept."""
    incumbent = _advert("s1", "Kitchen", -55.0, 1.5)
    weaker = _advert("s2", "Garage", -75.0, 4.0)
    _device, applied = _run(incumbent, [incumbent, weaker])
    assert applied[-1][0] is incumbent


def test_marginal_challenger_held_in_slow_lane_first_cycle():
    """A slightly-better challenger does not immediately unseat the incumbent."""
    incumbent = _advert("s1", "Garage", -70.0, 3.0)
    challenger = _advert("s2", "Roadside", -68.0, 3.2)  # score ratio ~1.28 < fast_ratio
    _device, applied = _run(incumbent, [incumbent, challenger])
    assert applied[-1][0] is incumbent


def test_slow_lane_majority_eventually_switches():
    """A persistently-stronger challenger wins once it owns the recent majority."""
    incumbent = _advert("s1", "Garage", -70.0, 3.0)
    challenger = _advert("s2", "Roadside", -68.0, 3.2)
    # Over enough cycles the challenger (always the best score) owns the history.
    _device, applied = _run(incumbent, [incumbent, challenger], cycles=9)
    assert applied[-1][0] is challenger


def test_slow_lane_dwell_switches_when_sustained():
    """A sustained challenger wins via the dwell timer (state pre-aged)."""
    incumbent = _advert("s1", "Garage", -70.0, 3.0)
    challenger = _advert("s2", "Roadside", -68.0, 3.2)
    state = AreaDecisionState()
    state.challenger_scanner = "s2"
    state.challenger_since = NOW - 100  # already dwelt past the threshold
    _device, applied = _run(incumbent, [incumbent, challenger], state=state)
    assert applied[-1][0] is challenger


def test_distance_tie_break_holds_incumbent_on_near_equal_scores():
    """Near-equal scores + near-equal distance keep the incumbent (no churn)."""
    incumbent = _advert("s1", "Garage", -70.0, 3.0)
    challenger = _advert("s2", "Roadside", -69.8, 3.02)  # ratio < 1.05, distance within 15%
    _device, applied = _run(incumbent, [incumbent, challenger])
    assert applied[-1][0] is incumbent


# ---------------------------------------------------------------------------
# Mobility policy
# ---------------------------------------------------------------------------


def test_stationary_mobility_needs_more_to_switch_than_moving():
    """The same marginal challenger that a moving device may consider, a stationary one resists."""
    incumbent = _advert("s1", "Garage", -70.0, 3.0)
    challenger = _advert("s2", "Roadside", -68.0, 3.2)
    # Stationary: bigger majority_need (10) than the 9 cycles we run -> still held.
    _device, applied = _run(incumbent, [incumbent, challenger], mobility=MOBILITY_STATIONARY, cycles=9)
    assert applied[-1][0] is incumbent
