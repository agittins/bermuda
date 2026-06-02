"""Tests for BermudaLogSpamLess rate-limited logging helper."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from custom_components.bermuda import log_spam_less
from custom_components.bermuda.log_spam_less import BermudaLogSpamLess


@pytest.fixture
def fake_clock(monkeypatch):
    """Provide a controllable monotonic clock for the module under test."""
    state = {"now": 1000.0}

    def _now():
        return state["now"]

    monkeypatch.setattr(log_spam_less, "monotonic_time_coarse", _now)
    return state


def _make(interval=10.0):
    logger = MagicMock(spec=logging.Logger)
    return BermudaLogSpamLess(logger, interval), logger


def test_first_occurrence_logs_as_is(fake_clock):
    """The first time a key is seen the message is emitted verbatim."""
    spam, logger = _make()
    spam.debug("k1", "hello %s", "world")
    logger.debug.assert_called_once_with("hello %s", "world")


def test_repeat_within_interval_is_suppressed(fake_clock):
    """Repeats with the same key inside the interval are not emitted."""
    spam, logger = _make(interval=10.0)
    spam.warning("k1", "first")
    logger.warning.assert_called_once_with("first")
    logger.reset_mock()

    # Advance time but stay within the interval.
    fake_clock["now"] += 5.0
    spam.warning("k1", "second")
    spam.warning("k1", "third")
    logger.warning.assert_not_called()


def test_occurrence_after_interval_logs_with_suppressed_count(fake_clock):
    """After the interval elapses the message logs again with a suppressed count."""
    spam, logger = _make(interval=10.0)
    spam.error("k1", "boom")
    logger.error.assert_called_once_with("boom")
    logger.reset_mock()

    # Two suppressed attempts inside the interval.
    fake_clock["now"] += 1.0
    spam.error("k1", "boom")
    fake_clock["now"] += 1.0
    spam.error("k1", "boom")
    logger.error.assert_not_called()

    # Now move past the interval; the next emission reports the suppressed count.
    fake_clock["now"] += 20.0
    spam.error("k1", "boom")
    logger.error.assert_called_once_with("boom (2 previous messages suppressed)")


def test_after_interval_with_zero_suppressed_logs_plain(fake_clock):
    """After interval but with no intervening attempts, count is zero -> plain msg."""
    spam, logger = _make(interval=10.0)
    spam.info("k1", "tick")
    logger.info.assert_called_once_with("tick")
    logger.reset_mock()

    # No suppressed attempts; jump past interval.
    fake_clock["now"] += 50.0
    spam.info("k1", "tick")
    logger.info.assert_called_once_with("tick")


def test_each_severity_first_occurrence(fake_clock):
    """Every severity level emits the first occurrence through its logger method."""
    spam, logger = _make()
    spam.debug("d", "dmsg")
    spam.info("i", "imsg")
    spam.warning("w", "wmsg")
    spam.error("e", "emsg")
    logger.debug.assert_called_once_with("dmsg")
    logger.info.assert_called_once_with("imsg")
    logger.warning.assert_called_once_with("wmsg")
    logger.error.assert_called_once_with("emsg")


def test_each_severity_suppresses_repeats(fake_clock):
    """Every severity level suppresses repeats within the interval."""
    spam, logger = _make(interval=10.0)
    for method_name in ("debug", "info", "warning", "error"):
        method = getattr(spam, method_name)
        method(method_name, "msg1")
        method(method_name, "msg2")  # suppressed
        logmethod = getattr(logger, method_name)
        logmethod.assert_called_once_with("msg1")


def test_separate_keys_are_independent(fake_clock):
    """Distinct keys do not interfere with each other."""
    spam, logger = _make(interval=10.0)
    spam.debug("a", "amsg")
    spam.debug("b", "bmsg")
    assert logger.debug.call_count == 2
    logger.reset_mock()

    # 'a' repeat is suppressed but 'b' is a fresh-first... no, 'b' already seen.
    spam.debug("a", "amsg")
    spam.debug("b", "bmsg")
    logger.debug.assert_not_called()


def test_args_and_kwargs_forwarded(fake_clock):
    """Positional args and kwargs are passed through to the logger call."""
    spam, logger = _make()
    spam.warning("k", "val=%s", 42, exc_info=True)
    logger.warning.assert_called_once_with("val=%s", 42, exc_info=True)


def test_check_key_protocol_directly(fake_clock):
    """_check_key returns 0 (new), -1 (suppressed), then count after interval."""
    spam, _ = _make(interval=10.0)
    # First use: brand new key -> 0
    assert spam._check_key("k") == 0
    assert spam._keycache["k"] == {"stamp": 1000.0, "count": 0}

    # Within interval -> suppressed, count increments
    assert spam._check_key("k") == -1
    assert spam._keycache["k"]["count"] == 1
    assert spam._check_key("k") == -1
    assert spam._keycache["k"]["count"] == 2

    # After interval -> returns prior count and resets count + stamp
    fake_clock["now"] += 20.0
    assert spam._check_key("k") == 2
    assert spam._keycache["k"]["count"] == 0
    assert spam._keycache["k"]["stamp"] == 1020.0


def test_prep_message_return_protocol(fake_clock):
    """_prep_message returns msg, suppression sentinel None, then annotated msg."""
    spam, _ = _make(interval=10.0)
    # count == 0 -> message as-is
    assert spam._prep_message("k", "base") == "base"
    # suppressed (-1) -> None
    assert spam._prep_message("k", "base") is None
    # after interval -> annotated with count
    fake_clock["now"] += 20.0
    assert spam._prep_message("k", "base") == "base (1 previous messages suppressed)"


def test_instances_have_isolated_cache(fake_clock):
    """Each instance keeps its own rate-limit state."""
    spam1, logger1 = _make(interval=10.0)
    spam2, logger2 = _make(interval=10.0)
    spam1.debug("shared", "m")
    # spam2 has not seen the key, so it logs as a fresh first occurrence.
    spam2.debug("shared", "m")
    logger1.debug.assert_called_once_with("m")
    logger2.debug.assert_called_once_with("m")
