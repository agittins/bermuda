"""
Pure distance-smoothing helpers for Bermuda.

Extracted from ``BermudaAdvert.calculate_data`` so the experience-tuned
smoothing maths can be unit-tested in isolation. These functions are pure:
they take history / configuration inputs and return values, with no side
effects on the advert object.

Noise in RSSI readings is very asymmetric — a closer reading is (almost)
always more accurate than a farther one. Both helpers below exploit that.
"""

from __future__ import annotations

import statistics

from .const import DISTANCE_INFINITE


def median_abs_deviation(values: list[float], center: float | None = None) -> float:
    """
    Return the median absolute deviation (MAD) of ``values``.

    A robust dispersion measure: the median of ``|value - center|``, where
    ``center`` defaults to the median. Used to clamp RSSI outliers without being
    dragged by the very spikes it is meant to reject. Returns 0.0 when empty.
    """
    if not values:
        return 0.0
    _center = statistics.median(values) if center is None else center
    deviations = [abs(value - _center) for value in values]
    return statistics.median(deviations) if deviations else 0.0


def peak_retreat_velocity(hist_distance: list[float | None], hist_stamp: list[float]) -> float:
    """
    Return the peak away-velocity (m/s) implied by the recent distance history.

    We compare the newest reading against each older reading to find the fastest
    *retreat* across the window; this is used to reject readings that imply the
    device moved away implausibly quickly (a noise spike). Returns 0 only when
    there is insufficient history; an approach yields a negative velocity.
    """
    if len(hist_stamp) <= 1:
        # There's no history, so no velocity.
        return 0

    velo_newdistance = hist_distance[0]
    velo_newstamp = hist_stamp[0]
    old_distance_1 = hist_distance[1]
    old_stamp_1 = hist_stamp[1]
    # Guard against gaps in the histories (a reading can be None before the first
    # real distance is computed), consistent with the None checks in the loop below.
    if velo_newdistance is None or velo_newstamp is None or old_stamp_1 is None or old_distance_1 is None:
        return 0
    delta_t = velo_newstamp - old_stamp_1
    delta_d = velo_newdistance - old_distance_1
    peak_velocity = delta_d / delta_t if delta_t > 0 else 0

    # If the most recent move is an approach (or flat), that is our answer.
    if peak_velocity >= 0:
        for old_distance, old_stamp in zip(hist_distance[2:], hist_stamp[2:], strict=False):
            if old_stamp is None or old_distance is None:
                # Skip gaps in either history (a reading can be None before the
                # first real distance is computed), consistent with the guard on
                # the first pair above.
                continue
            delta_t = velo_newstamp - old_stamp
            if delta_t <= 0:
                # Skip zero/negative intervals to avoid division by zero.
                continue
            velocity = (velo_newdistance - old_distance) / delta_t
            # We only care about faster retreats from here on.
            if velocity > peak_velocity:  # noqa: PLR1730
                peak_velocity = velocity

    return peak_velocity


def minimum_hugging_average(samples: list[float | None], rssi_distance_raw: float | None) -> float:
    """
    Average the samples while hugging the lowest recent reading.

    Walking newest-to-oldest, a sample only ever lowers the running minimum;
    each step contributes that running minimum to the total. This keeps the
    smoothed distance close to the most trustworthy (closest) recent readings
    rather than being dragged up by noisy far readings.

    ``rssi_distance_raw`` seeds the running minimum (falling back to
    ``DISTANCE_INFINITE`` when it is ``None``) and is also returned when
    there are no samples to average. A raw distance of exactly 0.0 is a
    legitimate closest-possible reading, not a missing one.
    """
    local_min: float = rssi_distance_raw if rssi_distance_raw is not None else DISTANCE_INFINITE
    if not samples:
        return local_min

    dist_total: float = 0
    for distance in samples:
        if distance is not None and distance <= local_min:
            local_min = distance
        dist_total += local_min

    return dist_total / len(samples)
