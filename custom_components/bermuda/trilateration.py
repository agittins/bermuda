"""
Area-selection ("trilateration") for Bermuda.

Extracted from the coordinator so the area-resolution logic can be reasoned
about and unit-tested in isolation.

For each device, every scanner that currently hears it becomes a scored
"contender" (the score is a monotonic function of the device's mobility-aware,
robustly-filtered RSSI at that scanner). The best contender wins, but switching
between contenders is damped by mobility-aware hysteresis (a fast lane for clear
wins, a slow lane needing sustained dwell or a recent majority), and when the
evidence is weak or ambiguous the device is reported as the explicit "Unknown"
area rather than a phantom room.

Ported and adapted from philbert/ble-trilateration (mobility-aware RSSI scoring).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    AREA_MAX_AD_AGE,
    AREA_NAME_UNKNOWN,
    CONF_MAX_RADIUS,
    DEFAULT_MAX_RADIUS,
    DIAG_TEXT_MAX_LENGTH,
    MOBILITY_MOVING,
    MOBILITY_STATIONARY,
)

if TYPE_CHECKING:
    from .bermuda_advert import BermudaAdvert
    from .bermuda_device import BermudaDevice


@dataclass
class AreaTests:
    """
    Holds the results of the area decision.

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


@dataclass
class AreaCandidate:
    """A valid area contender (fresh, in range, with an area), scored by RSSI."""

    advert: BermudaAdvert
    score: float
    rssi_filtered: float
    dispersion: float


@dataclass
class MobilityPolicy:
    """Mobility-aware area-switch policy. Field defaults are the 'moving' profile."""

    mode: str = MOBILITY_MOVING
    fast_ratio: float = 1.6
    dwell_seconds: float = 8.0
    majority_window: int = 9
    majority_need: int = 6
    min_rssi_confidence: float = -94.0
    ambiguity_ratio: float = 1.2
    ambiguity_hold_seconds: float = 8.0
    unknown_exit_ratio: float = 1.35


@dataclass
class AreaDecisionState:
    """Per-device rolling state that smooths area decisions across cycles."""

    dominant_history: deque[str] = field(default_factory=lambda: deque(maxlen=21))
    challenger_scanner: str | None = None
    challenger_since: float = 0.0
    ambiguous_since: float = 0.0
    unknown_since: float = 0.0


def _score_rssi(rssi_filtered: float | None) -> float:
    """Convert a filtered RSSI (dBm) to a bounded, monotonic confidence score."""
    if rssi_filtered is None:
        return 0.0
    # ~6-10 dB should matter noticeably; keep the exponent bounded for stability.
    exponent = (max(min(rssi_filtered, -30.0), -120.0) + 90.0) / 8.0
    exponent = max(min(exponent, 12.0), -12.0)
    return math.exp(exponent)


def _mobility_policy(mobility_type: str) -> MobilityPolicy:
    """Return the area-switch policy for a device's mobility mode."""
    if mobility_type == MOBILITY_STATIONARY:
        return MobilityPolicy(
            mode=MOBILITY_STATIONARY,
            fast_ratio=2.0,
            dwell_seconds=24.0,
            majority_window=15,
            majority_need=10,
            min_rssi_confidence=-92.0,
            ambiguity_ratio=1.25,
            ambiguity_hold_seconds=14.0,
            unknown_exit_ratio=1.45,
        )
    return MobilityPolicy()


def _majority_wins(history: deque[str], candidate: str, window: int, need: int) -> bool:
    """Return True if ``candidate`` owns at least ``need`` of the last ``window`` winners."""
    if window <= 0 or need <= 0:
        return False
    recent = list(history)[-window:]
    return recent.count(candidate) >= need


def _build_contenders(device: BermudaDevice, nowstamp: float, max_radius: float) -> list[AreaCandidate]:
    """Build the scored list of valid area contenders for a device."""
    contenders: list[AreaCandidate] = []
    for challenger in device.adverts.values():
        # No winning with stale adverts, or from too far away / with no area.
        if challenger.stamp < nowstamp - AREA_MAX_AD_AGE:
            continue
        if challenger.rssi_distance is None or challenger.rssi_distance > max_radius or challenger.area_id is None:
            continue
        rssi_for_score = challenger.rssi_filtered
        if rssi_for_score is None and challenger.rssi is not None:
            rssi_for_score = challenger.rssi + challenger.conf_rssi_offset
        contenders.append(
            AreaCandidate(
                advert=challenger,
                score=_score_rssi(rssi_for_score),
                rssi_filtered=rssi_for_score if rssi_for_score is not None else -127.0,
                dispersion=getattr(challenger, "rssi_dispersion", 0.0),
            )
        )
    contenders.sort(key=lambda c: (-c.score, c.advert.rssi_distance or 999.0))
    return contenders


def _unknown_reason(
    best: AreaCandidate,
    second: AreaCandidate | None,
    policy: MobilityPolicy,
    state: AreaDecisionState,
    nowstamp: float,
    max_radius: float,
) -> str | None:
    """Decide whether the (non-None) best contender is too weak/ambiguous → 'Unknown'."""
    if best.rssi_filtered < policy.min_rssi_confidence:
        return f"weak_rssi({best.rssi_filtered:.1f}dBm)"

    reason: str | None = None
    if second is not None:
        ratio = best.score / max(second.score, 1e-9)
        if ratio < policy.ambiguity_ratio:
            if state.ambiguous_since <= 0:
                state.ambiguous_since = nowstamp
            elif nowstamp - state.ambiguous_since >= policy.ambiguity_hold_seconds:
                reason = f"ambiguous_ratio({ratio:.2f})"
        else:
            state.ambiguous_since = 0.0
        if (
            reason is None
            and best.advert.rssi_distance is not None
            and best.advert.rssi_distance > max_radius * 0.92
            and ratio < (policy.ambiguity_ratio + 0.1)
        ):
            reason = "edge_of_radius_ambiguous"
    else:
        state.ambiguous_since = 0.0

    # Hold "Unknown" until the evidence becomes clearer than the exit ratio.
    if (
        reason is None
        and state.unknown_since > 0
        and second is not None
        and (best.score / max(second.score, 1e-9)) < policy.unknown_exit_ratio
    ):
        reason = "hold_unknown_until_clear"
    return reason


def _resolve_hysteresis(
    device: BermudaDevice,
    contenders: list[AreaCandidate],
    best: AreaCandidate,
    policy: MobilityPolicy,
    state: AreaDecisionState,
    nowstamp: float,
    tests: AreaTests,
) -> tuple[AreaCandidate, str]:
    """Apply adaptive fast/slow-lane hysteresis; return (chosen_candidate, reason)."""
    incumbent_advert = device.area_advert
    incumbent = next((c for c in contenders if incumbent_advert is not None and c.advert is incumbent_advert), None)
    if incumbent is None and incumbent_advert is not None:
        incumbent = next((c for c in contenders if c.advert.scanner_address == incumbent_advert.scanner_address), None)

    if incumbent is None:
        state.challenger_scanner = None
        state.challenger_since = 0.0
        return best, "WIN initial_valid_contender"
    if best.advert.scanner_address == incumbent.advert.scanner_address:
        state.challenger_scanner = None
        state.challenger_since = 0.0
        return incumbent, "HOLD incumbent_best_score"

    score_ratio = best.score / max(incumbent.score, 1e-9)
    # Adaptive thresholds: noisier signals (higher dispersion) demand more evidence.
    max_dispersion = max(best.dispersion, incumbent.dispersion)
    noise_scale = min(max((max_dispersion - 3.0) / 5.0, 0.0), 1.0)
    fast_ratio = policy.fast_ratio + (0.25 if policy.mode == MOBILITY_MOVING else 0.45) * noise_scale
    dwell_seconds = policy.dwell_seconds + (6.0 if policy.mode == MOBILITY_MOVING else 12.0) * noise_scale
    majority_window = policy.majority_window + (2 if noise_scale > 0.6 else 0)
    majority_need = min(majority_window, policy.majority_need + (1 if noise_scale > 0.4 else 0))

    if score_ratio >= fast_ratio:
        state.challenger_scanner = None
        state.challenger_since = 0.0
        return best, f"WIN fast_lane ratio={score_ratio:.2f}"

    # Legacy distance tie-break for near-equal scores.
    if score_ratio < 1.05 and incumbent.advert.rssi_distance is not None and best.advert.rssi_distance is not None:
        _pda = best.advert.rssi_distance
        _pdb = incumbent.advert.rssi_distance
        tests.pcnt_diff = abs(_pda - _pdb) / ((_pda + _pdb) / 2)
        if tests.pcnt_diff < 0.15:
            state.challenger_scanner = None
            state.challenger_since = 0.0
            return incumbent, "HOLD distance_tie_break"

    # Slow lane: a challenger must persist (dwell) or own a recent majority to win.
    if state.challenger_scanner != best.advert.scanner_address:
        state.challenger_scanner = best.advert.scanner_address
        state.challenger_since = nowstamp
    dwell_ok = nowstamp - state.challenger_since >= dwell_seconds
    majority_ok = _majority_wins(state.dominant_history, best.advert.scanner_address, majority_window, majority_need)
    if dwell_ok or majority_ok:
        state.challenger_scanner = None
        state.challenger_since = 0.0
        lane = "dwell" if dwell_ok else "majority"
        return best, f"WIN slow_lane {lane} ratio={score_ratio:.2f} disp={max_dispersion:.2f}"
    return incumbent, f"HOLD incumbent slow_lane ratio={score_ratio:.2f} disp={max_dispersion:.2f}"


def refresh_area_by_min_distance(device: BermudaDevice, options: dict) -> None:
    """Resolve a device's area using score-based contenders and mobility-aware hysteresis."""
    nowstamp = monotonic_time_coarse()
    max_radius = options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
    tests = AreaTests()
    tests.device = device.name
    policy = _mobility_policy(device.get_mobility_type())
    if device.area_decision_state is None:
        device.area_decision_state = AreaDecisionState()
    state: AreaDecisionState = device.area_decision_state

    contenders = _build_contenders(device, nowstamp, max_radius)
    best = contenders[0] if contenders else None
    second = contenders[1] if len(contenders) > 1 else None
    state.dominant_history.append(best.advert.scanner_address if best is not None else AREA_NAME_UNKNOWN)

    if best is None:
        # Nothing currently hears the device well enough to place it: report
        # not_home (the device_tracker timeout handles genuine away) rather than
        # the explicit "Unknown" area, which we reserve for weak/ambiguous evidence.
        state.unknown_since = 0.0
        state.ambiguous_since = 0.0
        state.challenger_scanner = None
        state.challenger_since = 0.0
        device.apply_scanner_selection(None)
        return

    unknown_reason = _unknown_reason(best, second, policy, state, nowstamp, max_radius)
    if unknown_reason is not None:
        if state.unknown_since <= 0:
            state.unknown_since = nowstamp
        state.challenger_scanner = None
        state.challenger_since = 0.0
        tests.reason = f"UNKNOWN - {unknown_reason}"
        device.diag_area_switch = tests.sensortext()
        device.diag_area_switch_reason = tests.reason
        device.apply_scanner_selection(None, force_unknown=True)
        return

    state.unknown_since = 0.0
    chosen, tests.reason = _resolve_hysteresis(device, contenders, best, policy, state, nowstamp, tests)

    if device.area_advert != chosen.advert and tests.reason is not None:
        tests.scannername = (device.area_advert.name if device.area_advert is not None else "", chosen.advert.name)
        tests.areas = ("", chosen.advert.area_name or "")
        tests.distance = (0, chosen.advert.rssi_distance or 0)
        device.diag_area_switch = tests.sensortext()
        device.diag_area_switch_reason = tests.reason

    device.apply_scanner_selection(chosen.advert)
