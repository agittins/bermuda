"""
Pure RSSI filtering helpers for Bermuda.

Extracted from ``BermudaAdvert`` so the robust outlier-clamping and EMA
smoothing maths can be unit-tested in isolation. These functions are pure:
samples and history in, filtered values out, no side effects on the advert
object (history mutation stays with the advert).

The filter parameters are mobility-aware: a stationary device can afford a
longer window and gentler smoothing than one that is being carried around.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .distance_filter import median_abs_deviation

# MAD -> standard-deviation scale factor for normally-distributed noise.
MAD_TO_SIGMA = 1.4826
# An outlier is a sample further from the recent median than this many
# (robust) standard deviations, with the policy's base threshold as a floor.
OUTLIER_SIGMA_FACTOR = 3.0
# Below this many prior samples, outlier clamping has nothing robust to
# compare against and is skipped.
MIN_PRIOR_SAMPLES = 3


@dataclass(frozen=True, slots=True)
class RssiFilterPolicy:
    """Mobility-aware RSSI filter parameters."""

    window: int  # how many recent samples inform the median/dispersion
    ema_alpha: float  # exponential-moving-average weight of the new sample
    base_outlier_db: float  # minimum dB deviation to consider a sample a spike


RSSI_POLICY_STATIONARY = RssiFilterPolicy(window=13, ema_alpha=0.22, base_outlier_db=12.0)
RSSI_POLICY_MOVING = RssiFilterPolicy(window=9, ema_alpha=0.45, base_outlier_db=15.0)


def clamp_outlier(sample: float, prior: list[float], base_outlier_db: float) -> float:
    """
    Clamp a spike to the recent median when it exceeds a MAD-derived threshold.

    ``prior`` is the recent (offset-adjusted) sample window, newest first. With
    fewer than MIN_PRIOR_SAMPLES entries the sample is returned unchanged.
    """
    if len(prior) < MIN_PRIOR_SAMPLES:
        return sample
    med = statistics.median(prior)
    mad = median_abs_deviation(prior, med)
    robust_sigma = max(mad * MAD_TO_SIGMA, 1.0)
    threshold = max(base_outlier_db, robust_sigma * OUTLIER_SIGMA_FACTOR)
    if abs(sample - med) > threshold:
        return med
    return sample


def ema(previous: float | None, sample: float, alpha: float) -> float:
    """Exponentially smooth ``sample`` against ``previous`` (seeds on first call)."""
    if previous is None:
        return sample
    return (alpha * sample) + ((1 - alpha) * previous)


def rssi_dispersion(filtered_window: list[float]) -> float:
    """MAD-based jitter estimate of the filtered RSSI window (0.0 when too short)."""
    if len(filtered_window) < MIN_PRIOR_SAMPLES:
        return 0.0
    return median_abs_deviation(filtered_window) * MAD_TO_SIGMA
