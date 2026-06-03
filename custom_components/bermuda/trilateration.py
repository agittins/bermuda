"""
Area-selection ("trilateration") for Bermuda.

Extracted from the coordinator so the experience-tuned "closest scanner wins"
race with hysteresis can be reasoned about and unit-tested in isolation.

The core idea: for a given device, each scanner that currently hears it is a
"challenger" against the reigning "incumbent" (the device's current area
scanner). Every comparison is a two-way race, with hysteresis so a device
doesn't flap between two near-equidistant scanners.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    _LOGGER,
    AREA_HISTORY_WINDOW,
    AREA_MAX_AD_AGE,
    AREA_MIN_HISTORY,
    AREA_PCNT_DIFF_HISTORICAL,
    AREA_PCNT_DIFF_OUTRIGHT,
    CONF_MAX_RADIUS,
    DEFAULT_MAX_RADIUS,
    DIAG_TEXT_MAX_LENGTH,
)

if TYPE_CHECKING:
    from .bermuda_advert import BermudaAdvert
    from .bermuda_device import BermudaDevice

# Flip to True to emit very verbose logging about how each area contest was won.
_SUPERCHATTY: Final = False


@dataclass
class AreaTests:
    """
    Holds the results of the per-challenger area tests.

    Also rendered into the ``area_switch_diagnostic`` sensor so users can see
    *why* a device changed area.
    """

    device: str = ""
    scannername: tuple[str, str] = ("", "")
    areas: tuple[str, str] = ("", "")
    pcnt_diff: float = 0  # distance percentage difference.
    same_area: bool = False  # The old scanner is in the same area as us.
    last_ad_age: tuple[float, float] = (0, 0)  # seconds since we last got *any* ad from scanner
    this_ad_age: tuple[float, float] = (0, 0)  # how old the *current* advert is on this scanner
    distance: tuple[float, float] = (0, 0)
    hist_min_max: tuple[float, float] = (0, 0)  # min/max distance from history
    reason: str | None = None  # reason/result

    def sensortext(self) -> str:
        """Return a text summary suitable for use in a sensor entity."""
        out = ""
        for var, val in vars(self).items():
            out += f"{var}|"
            if isinstance(val, tuple):
                for v in val:
                    if isinstance(v, float):
                        out += f"{v:.2f}|"
                    else:
                        out += f"{v}"
            elif var == "pcnt_diff":
                out += f"{val:.3f}"
            else:
                out += f"{val}"
            out += "\n"
        return out[:DIAG_TEXT_MAX_LENGTH]

    def __str__(self) -> str:
        """Create a string representation for easy debug logging/dumping."""
        out = ""
        for var, val in vars(self).items():
            out += f"** {var:20} "
            if isinstance(val, tuple):
                for v in val:
                    if isinstance(v, float):
                        out += f"{v:.2f} "
                    else:
                        out += f"{v} "
                out += "\n"
            elif var == "pcnt_diff":
                out += f"{val:.3f}\n"
            else:
                out += f"{val}\n"
        return out


def refresh_area_by_min_distance(device: BermudaDevice, options: dict) -> None:
    """Set a device's closest scanner/area by racing every advert it has."""
    # The current area_scanner (which might be None) is the one to beat.
    incumbent: BermudaAdvert | None = device.area_advert

    _max_radius = options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
    nowstamp = monotonic_time_coarse()

    tests = AreaTests()
    tests.device = device.name

    for challenger in device.adverts.values():
        # Every comparison below is a two-way race between the incumbent and
        # this challenger. rssi_distance is smoothed/filtered, and may be None
        # if the last reading was old enough to be considered "away".

        # No competing against ourselves.
        if incumbent is challenger:
            continue

        # No winning with stale adverts. If we didn't win when it was fresh, we
        # have no business winning now (guards against two proxies reporting the
        # same advert at slightly different times and flapping after a timeout).
        if challenger.stamp < nowstamp - AREA_MAX_AD_AGE:
            continue

        # If we are too far away or don't have an area, we cannot win.
        if challenger.rssi_distance is None or challenger.rssi_distance > _max_radius or challenger.area_id is None:
            continue

        # At this point the challenger is a valid contender.
        # If the incumbent lacks critical data, the challenger wins outright.
        if incumbent is None or incumbent.rssi_distance is None or incumbent.area_id is None:
            incumbent = challenger
            if _SUPERCHATTY:
                _LOGGER.debug("%s IS closer to %s: incumbent is invalid", device.name, challenger.name)
            continue

        # From here on, don't award a win directly. Award a loss if the
        # challenger is not a contender, otherwise score the tests and decide.

        # If we are not actually closer, we cannot win.
        if incumbent.rssi_distance < challenger.rssi_distance:
            continue

        tests.reason = None  # ensure we don't trigger logging if no decision was made.
        tests.same_area = incumbent.area_id == challenger.area_id
        tests.areas = (incumbent.area_name or "", challenger.area_name or "")
        tests.scannername = (incumbent.name, challenger.name)
        tests.distance = (incumbent.rssi_distance, challenger.rssi_distance)

        # How recently have we heard from the scanners themselves?
        tests.last_ad_age = (
            nowstamp - incumbent.scanner_device.last_seen,
            nowstamp - challenger.scanner_device.last_seen,
        )

        # How old are the adverts?
        tests.this_ad_age = (
            nowstamp - incumbent.stamp,
            nowstamp - challenger.stamp,
        )

        # Percentage difference between the challenger and incumbent distances.
        _pda = challenger.rssi_distance
        _pdb = incumbent.rssi_distance
        tests.pcnt_diff = abs(_pda - _pdb) / ((_pda + _pdb) / 2)

        # Same area: confirm freshness and distance.
        if (
            tests.same_area
            and (tests.this_ad_age[0] > tests.this_ad_age[1] + 1)
            and tests.distance[0] >= tests.distance[1]
        ):
            tests.reason = "WIN awarded for same area, newer, closer advert"
            incumbent = challenger
            continue

        # Hysteresis: if our worst reading in the window is still closer than
        # the incumbent's best, and we are over a percentage threshold, we win.
        if len(challenger.hist_distance_by_interval) > AREA_MIN_HISTORY:
            tests.hist_min_max = (
                min(incumbent.hist_distance_by_interval[:AREA_HISTORY_WINDOW]),  # closest the incumbent has been
                max(challenger.hist_distance_by_interval[:AREA_HISTORY_WINDOW]),  # furthest we have been
            )
            if tests.hist_min_max[1] < tests.hist_min_max[0] and tests.pcnt_diff > AREA_PCNT_DIFF_HISTORICAL:
                tests.reason = "WIN on historical min/max"
                incumbent = challenger
                continue

        if tests.pcnt_diff < AREA_PCNT_DIFF_OUTRIGHT:
            # Not "different enough" given how recently the incumbent updated.
            tests.reason = "LOSS - failed on percentage_difference"
            continue

        # Made it through all of that — we're winning, so far!
        tests.reason = "WIN by not losing!"
        incumbent = challenger

    if _SUPERCHATTY and tests.reason is not None:
        _LOGGER.info("***** %s *****\n%s", tests.reason, tests)

    if device.area_advert != incumbent and tests.reason is not None:
        device.diag_area_switch = tests.sensortext()

    # Apply the newly-found closest scanner (or None if we didn't find one).
    device.apply_scanner_selection(incumbent)
