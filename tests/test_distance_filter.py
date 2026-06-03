"""Unit tests for the pure distance-smoothing helpers."""

from __future__ import annotations

import pytest

from custom_components.bermuda.const import DISTANCE_INFINITE
from custom_components.bermuda.distance_filter import minimum_hugging_average, peak_retreat_velocity


class TestPeakRetreatVelocity:
    """peak_retreat_velocity finds the fastest implied move away over history."""

    def test_insufficient_history_is_zero(self):
        assert peak_retreat_velocity([5.0], [100.0]) == 0
        assert peak_retreat_velocity([], []) == 0

    def test_approach_returns_recent_negative_velocity(self):
        # Newest reading is closer than the previous → approaching.
        assert peak_retreat_velocity([2.0, 5.0], [101.0, 100.0]) == -3.0

    def test_finds_peak_retreat_across_window(self):
        # Newest is a retreat; the most-recent step (8 m/s) is the peak.
        assert peak_retreat_velocity([10.0, 2.0, 1.0], [103.0, 102.0, 100.0]) == 8.0

    def test_skips_none_stamps_and_nonpositive_intervals(self):
        assert peak_retreat_velocity([10.0, 2.0, 1.0], [103.0, 102.0, None]) == 8.0
        # Equal stamps → delta_t 0 on the first pair → peak stays 0.
        assert peak_retreat_velocity([9.0, 1.0], [100.0, 100.0]) == 0


class TestMinimumHuggingAverage:
    """minimum_hugging_average hugs the closest recent reading."""

    def test_empty_returns_seed_or_infinite(self):
        assert minimum_hugging_average([], 4.0) == 4.0
        assert minimum_hugging_average([], 0) == DISTANCE_INFINITE
        assert minimum_hugging_average([], None) == DISTANCE_INFINITE

    def test_hugs_running_minimum(self):
        # seed 5 → running min walks 5,2,2 → (5+2+2)/3 = 3.0
        assert minimum_hugging_average([5.0, 2.0, 8.0], 5.0) == 3.0

    def test_ignores_none_samples(self):
        # seed 5 → 5, (skip None keeps 5), 2 → (5+5+2)/3 = 4.0
        assert minimum_hugging_average([5.0, None, 2.0], 5.0) == pytest.approx(4.0)

    def test_all_increasing_keeps_seed(self):
        # Nothing closer than the seed → average equals the seed.
        assert minimum_hugging_average([5.0, 6.0, 7.0], 5.0) == 5.0
