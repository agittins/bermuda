"""Branch-coverage tests for the trilateration / area-selection module.

These complement ``tests/test_area_selection_characterization.py`` (which pins
the instant-win / farther / marginal / significant / too-far / stale paths) by
exercising the remaining branches of
``custom_components.bermuda.trilateration``:

* ``AreaTests.__str__`` formatting (tuple/float, ``pcnt_diff`` special-case,
  and the plain scalar branch);
* the *same-area, newer + closer* hysteresis win;
* the *historical min/max* hysteresis win; and
* ``device.diag_area_switch`` being populated when the selected advert changes
  and a decision (``reason``) was recorded.

The module entry point is called directly (``refresh_area_by_min_distance``)
with an options dict, mirroring how the coordinator delegates to it. Adverts and
the device are lightweight ``SimpleNamespace`` stand-ins, exactly as in the
existing characterization suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from bluetooth_data_tools import monotonic_time_coarse

from custom_components.bermuda.const import (
    AREA_PCNT_DIFF_HISTORICAL,
    AREA_PCNT_DIFF_OUTRIGHT,
    CONF_MAX_RADIUS,
)
from custom_components.bermuda.trilateration import AreaTests, refresh_area_by_min_distance

NOW = monotonic_time_coarse()


def _advert(name, area_id, distance, *, stamp=None, history=None, last_seen=None):
    """Build a minimal BermudaAdvert stand-in for the area race."""
    return SimpleNamespace(
        name=name,
        area_id=area_id,
        area_name=area_id.title() if area_id else None,
        rssi_distance=distance,
        stamp=NOW if stamp is None else stamp,
        scanner_device=SimpleNamespace(last_seen=NOW if last_seen is None else last_seen),
        hist_distance_by_interval=history if history is not None else [],
    )


def _run(area_advert, adverts, *, max_radius=20.0):
    """Drive the race and return (selected_advert, device)."""
    selected = []
    device = SimpleNamespace(
        name="tracked",
        area_advert=area_advert,
        adverts={i: ad for i, ad in enumerate(adverts)},
        diag_area_switch=None,
        apply_scanner_selection=lambda adv: selected.append(adv),
    )
    refresh_area_by_min_distance(device, {CONF_MAX_RADIUS: max_radius})
    assert len(selected) == 1
    return selected[0], device


# ---------------------------------------------------------------------------
# AreaTests.__str__ formatting
# ---------------------------------------------------------------------------


def test_areatests_str_renders_all_field_kinds():
    """``__str__`` formats tuples (floats), the pcnt_diff special-case and scalars."""
    tests = AreaTests(
        device="Phone",
        scannername=("Kitchen Proxy", "Lounge Proxy"),
        areas=("Kitchen", "Lounge"),
        pcnt_diff=0.1818,
        same_area=False,
        last_ad_age=(1.5, 0.25),
        this_ad_age=(2.0, 0.5),
        distance=(3.0, 2.5),
        hist_min_max=(3.0, 2.6),
        reason="WIN on historical min/max",
    )
    out = str(tests)

    # Field labels are left-justified to width 20 and prefixed with "** ".
    assert "** device" in out
    assert "** scannername" in out
    assert "** pcnt_diff" in out
    assert "** reason" in out

    # Tuple-of-float branch: each float rendered with two decimals.
    assert "3.00 2.50" in out  # distance
    assert "1.50 0.25" in out  # last_ad_age

    # Tuple-of-str branch: scanner names and area names appear verbatim.
    assert "Kitchen Proxy Lounge Proxy" in out
    assert "Kitchen Lounge" in out

    # pcnt_diff special-case: three decimals.
    assert "0.182" in out

    # Plain scalar branch: bool and string scalars.
    assert "False" in out
    assert "WIN on historical min/max" in out

    # Multi-line: one line per dataclass field.
    assert out.count("\n") == len(vars(tests))


def test_areatests_str_handles_defaults():
    """A freshly-constructed AreaTests stringifies without error (reason None)."""
    out = str(AreaTests())
    assert "** reason" in out
    assert "None" in out


# ---------------------------------------------------------------------------
# Same-area, newer + closer hysteresis win  (lines 171-173)
# ---------------------------------------------------------------------------


def test_same_area_newer_closer_advert_wins():
    """A same-area challenger whose advert is newer and at least as close wins.

    Conditions, from the source:
      * same area_id (same_area == True),
      * this_ad_age[0] > this_ad_age[1] + 1  -> incumbent advert is >1s older,
      * distance[0] >= distance[1]           -> incumbent not closer.

    The incumbent advert is stamped 5s in the past while the challenger is
    fresh, and both are at the same distance so the earlier "not actually
    closer" guard (incumbent < challenger) does not short-circuit.
    """
    incumbent = _advert("Kitchen Proxy", "kitchen", 3.0, stamp=NOW - 5)
    challenger = _advert("Kitchen Proxy 2", "kitchen", 3.0, stamp=NOW)
    chosen, device = _run(incumbent, [incumbent, challenger])

    assert chosen is challenger
    # Winner changed and a reason was recorded -> diagnostic populated.
    assert device.diag_area_switch is not None
    assert "WIN awarded for same area, newer, closer advert" in device.diag_area_switch


def test_same_area_not_newer_enough_does_not_win_on_same_area_path():
    """Same area but advert age gap <= 1s must not trigger the same-area win.

    With equal distances and pcnt_diff == 0 this falls all the way through to
    the percentage-difference loss, so the incumbent is retained.
    """
    incumbent = _advert("Kitchen Proxy", "kitchen", 3.0, stamp=NOW - 0.5)
    challenger = _advert("Kitchen Proxy 2", "kitchen", 3.0, stamp=NOW)
    chosen, device = _run(incumbent, [incumbent, challenger])

    assert chosen is incumbent
    # No switch -> diagnostic left untouched.
    assert device.diag_area_switch is None


# ---------------------------------------------------------------------------
# Historical min/max hysteresis win  (lines 178-185)
# ---------------------------------------------------------------------------


def test_historical_min_max_win():
    """Challenger wins when its worst recent reading beats the incumbent's best.

    Requirements:
      * different areas so the same-area block is skipped,
      * challenger has > AREA_MIN_HISTORY (3) history samples,
      * max(challenger history) < min(incumbent history),
      * pcnt_diff > AREA_PCNT_DIFF_HISTORICAL (0.15) but kept below
        AREA_PCNT_DIFF_OUTRIGHT (0.30) so the win is attributable to the
        historical test rather than the outright threshold.
    """
    incumbent = _advert(
        "Kitchen Proxy",
        "kitchen",
        3.0,
        history=[3.0, 3.1, 3.2, 3.0, 3.3],  # min over window == 3.0
    )
    challenger = _advert(
        "Lounge Proxy",
        "lounge",
        2.5,
        history=[2.5, 2.4, 2.6, 2.5],  # 4 samples (> 3); max over window == 2.6
    )
    # pcnt_diff = |2.5-3.0| / ((2.5+3.0)/2) = 0.1818...
    pcnt_diff = abs(2.5 - 3.0) / ((2.5 + 3.0) / 2)
    assert AREA_PCNT_DIFF_HISTORICAL < pcnt_diff < AREA_PCNT_DIFF_OUTRIGHT

    chosen, device = _run(incumbent, [incumbent, challenger])

    assert chosen is challenger
    assert device.diag_area_switch is not None
    assert "WIN on historical min/max" in device.diag_area_switch


def test_historical_test_skipped_without_enough_samples():
    """With history but <= AREA_MIN_HISTORY samples the historical test is skipped.

    pcnt_diff (~0.18) is below the outright threshold, so with the historical
    branch unavailable the challenger loses and the incumbent is kept.
    """
    incumbent = _advert("Kitchen Proxy", "kitchen", 3.0, history=[3.0, 3.1, 3.2])
    challenger = _advert("Lounge Proxy", "lounge", 2.5, history=[2.5, 2.4, 2.6])  # exactly 3 -> not > 3
    chosen, device = _run(incumbent, [incumbent, challenger])

    assert chosen is incumbent
    assert device.diag_area_switch is None


def test_historical_min_max_not_separated_enough_falls_through_to_loss():
    """Enough samples but overlapping ranges: historical test fails, outright loses."""
    incumbent = _advert("Kitchen Proxy", "kitchen", 3.0, history=[2.4, 3.1, 3.2, 3.0])
    # challenger max (2.6) is NOT < incumbent min (2.4) -> historical test fails.
    challenger = _advert("Lounge Proxy", "lounge", 2.5, history=[2.5, 2.4, 2.6, 2.5])
    chosen, device = _run(incumbent, [incumbent, challenger])

    assert chosen is incumbent
    assert device.diag_area_switch is None


# ---------------------------------------------------------------------------
# diag_area_switch on an outright win
# ---------------------------------------------------------------------------


def test_diag_area_switch_set_on_outright_win():
    """A clear outright win also populates diag_area_switch with its reason."""
    incumbent = _advert("Kitchen Proxy", "kitchen", 5.0)
    challenger = _advert("Lounge Proxy", "lounge", 2.0)  # pcnt_diff ~= 0.857
    chosen, device = _run(incumbent, [incumbent, challenger])

    assert chosen is challenger
    assert device.diag_area_switch is not None
    assert "WIN by not losing!" in device.diag_area_switch
