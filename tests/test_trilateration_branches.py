"""
Branch tests for the trilateration module: the explicit "Unknown" outcomes, the
RSSI scoring, and the AreaTests diagnostic formatting.

Complements ``tests/test_area_selection_characterization.py`` (the fast/slow-lane
hysteresis paths). The module clock is frozen so module-level advert stamps never
age out; time-gated branches are exercised by pre-aging the decision state.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.bermuda.const import CONF_MAX_RADIUS, MOBILITY_MOVING
from custom_components.bermuda.trilateration import (
    AreaDecisionState,
    AreaTests,
    _score_rssi,
    refresh_area_by_min_distance,
)

# Synthetic clock base. Must be a large constant, NOT monotonic_time_coarse():
# that clock counts from machine boot, so on a freshly-booted CI runner
# "NOW - 100" can go negative and break the 0-means-unset sentinel fields
# (ambiguous_since / unknown_since / challenger_since).
NOW = 1_000_000.0


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch):
    """Pin trilateration's clock so the NOW-stamped adverts never age out."""
    monkeypatch.setattr("custom_components.bermuda.trilateration.monotonic_time_coarse", lambda: NOW)


def _advert(scanner: str, area: str | None, rssi_filtered: float, distance: float, *, dispersion=1.0):
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
        # Stamp far in the future so contenders never age out of AREA_MAX_AD_AGE,
        # regardless of how slow the suite is or whether the clock freeze holds.
        stamp=NOW + 1e6,
        scanner_device=SimpleNamespace(last_seen=NOW + 1e6),
    )


def _run(area_advert, adverts, *, mobility=MOBILITY_MOVING, state=None, max_radius=20.0):
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
    refresh_area_by_min_distance(device, {CONF_MAX_RADIUS: max_radius})
    return device, applied


# ---------------------------------------------------------------------------
# RSSI scoring
# ---------------------------------------------------------------------------


def test_score_rssi_is_monotonic_and_bounded():
    assert _score_rssi(None) == 0.0
    assert _score_rssi(-50.0) > _score_rssi(-70.0) > _score_rssi(-95.0)
    # Bounded: extreme inputs don't overflow.
    assert _score_rssi(-200.0) >= 0.0
    assert _score_rssi(10.0) < float("inf")


# ---------------------------------------------------------------------------
# Unknown outcomes
# ---------------------------------------------------------------------------


def test_weak_signal_not_unknown_on_first_dip():
    """A single weak cycle is debounced: the device stays placed, not Unknown."""
    weak = _advert("s1", "Garage", -96.0, 15.0)  # < moving floor (-94)
    _device, applied = _run(weak, [weak])
    # weak_hold_seconds has not elapsed yet, so the incumbent area is held.
    assert applied[-1] == (weak, False)


def test_weak_signal_unknown_when_sustained():
    """A weak signal held past weak_hold_seconds reports Unknown."""
    weak = _advert("s1", "Garage", -96.0, 15.0)  # < moving floor (-94)
    state = AreaDecisionState()
    state.weak_since = NOW - 100  # already weak long enough to trip the debounce
    device, applied = _run(weak, [weak], state=state)
    assert applied[-1] == (None, True)  # force_unknown
    assert "UNKNOWN" in device.diag_area_switch
    assert device.diag_area_switch_reason.startswith("UNKNOWN")


def test_weak_signal_exit_needs_margin():
    """Once weak-Unknown, a recovery that only reaches the bare floor stays Unknown."""
    # Already Unknown; signal recovers to exactly the floor (-94) but not past
    # floor + weak_exit_margin (-90), so hysteresis keeps it Unknown.
    borderline = _advert("s1", "Garage", -94.0, 15.0)
    state = AreaDecisionState()
    state.unknown_since = NOW - 50
    state.weak_since = NOW - 50
    _device, applied = _run(borderline, [borderline], state=state)
    assert applied[-1][1] is True  # still force_unknown


def test_unknown_when_sustained_ambiguous():
    """Two near-equal contenders, ambiguity sustained past the hold, report Unknown."""
    a = _advert("s1", "Garage", -70.0, 3.0)
    b = _advert("s2", "Roadside", -71.0, 3.1)  # score ratio ~1.13 < ambiguity_ratio (1.2)
    state = AreaDecisionState()
    state.ambiguous_since = NOW - 100  # already held long enough
    _device, applied = _run(a, [a, b], state=state)
    assert applied[-1][1] is True


def test_not_unknown_when_clearly_separated():
    """Well-separated contenders are not ambiguous; the best is selected normally."""
    a = _advert("s1", "Kitchen", -55.0, 1.5)
    b = _advert("s2", "Garage", -80.0, 5.0)
    _device, applied = _run(a, [a, b])
    assert applied[-1] == (a, False)


def test_unknown_holds_until_evidence_clears():
    """While already Unknown, a still-too-close ratio keeps reporting Unknown."""
    a = _advert("s1", "Garage", -70.0, 3.0)
    b = _advert("s2", "Roadside", -71.5, 3.1)  # ratio ~1.2 < unknown_exit_ratio (1.35)
    state = AreaDecisionState()
    state.unknown_since = NOW - 50  # we were already Unknown
    _device, applied = _run(a, [a, b], state=state)
    assert applied[-1][1] is True


# ---------------------------------------------------------------------------
# Diagnostics + AreaTests formatting (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_diag_populated_on_area_switch():
    """A genuine switch records the reason + diagnostic text."""
    incumbent = _advert("s1", "Garage", -85.0, 6.0)
    challenger = _advert("s2", "Kitchen", -55.0, 1.5)
    device, applied = _run(incumbent, [incumbent, challenger])
    assert applied[-1][0] is challenger
    assert device.diag_area_switch is not None
    assert device.diag_area_switch_reason is not None
    assert "WIN" in device.diag_area_switch_reason


def test_areatests_sensortext_and_str():
    """AreaTests still renders tuples/floats/scalars for the diagnostic sensor."""
    tests = AreaTests(
        device="Phone",
        scannername=("Kitchen", "Lounge"),
        pcnt_diff=0.1818,
        distance=(3.0, 2.5),
        reason="WIN fast_lane ratio=3.20",
    )
    text = tests.sensortext()
    assert "device|Phone" in text
    assert "3.00" in text  # distance tuple float
    assert len(text) <= 255

    out = str(tests)
    assert "** device" in out
    assert "0.182" in out  # pcnt_diff special-case (3 decimals)
    assert "WIN fast_lane ratio=3.20" in out


def test_areatests_defaults_stringify():
    """A default AreaTests stringifies without error (reason None)."""
    out = str(AreaTests())
    assert "** reason" in out
    assert "None" in out
