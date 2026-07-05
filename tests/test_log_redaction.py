"""Tests for the centralized 32-hex secret (IRK) log-redaction filter.

Ported and adapted from philbert/ble-trilateration: a logging.Filter safety net
behind the targeted IRK-log truncations, masking any record that still emits a
full IRK.
"""

from __future__ import annotations

import logging

from custom_components.bermuda.const import (
    BermudaSecretFilter,
    _ensure_secret_filter,
    redact_secret_hex32,
)


def test_redact_masks_standalone_irk():
    irk = "deadbeefdeadbeefdeadbeefdeadbeef"
    assert redact_secret_hex32(f"resolved to {irk}!") == "resolved to [REDACTED_HEX32]!"
    assert irk not in redact_secret_hex32(irk)


def test_redact_leaves_short_and_longer_hex_alone():
    assert redact_secret_hex32("aa:bb:cc") == "aa:bb:cc"  # too short
    assert redact_secret_hex32("ab" * 20) == "ab" * 20  # 40-hex run, not exactly 32


def test_filter_redacts_record_message_and_clears_args():
    irk = "0123456789abcdef0123456789abcdef"
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "irk=%s done", (irk,), None)
    flt = BermudaSecretFilter()
    assert flt.filter(record) is True
    msg = record.getMessage()
    assert irk not in msg
    assert "[REDACTED_HEX32]" in msg


def test_filter_passes_clean_messages_untouched():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "nothing secret here", None, None)
    assert BermudaSecretFilter().filter(record) is True
    assert record.getMessage() == "nothing secret here"


def test_ensure_secret_filter_is_idempotent():
    logger = logging.getLogger("bermuda_test_secret_filter")
    logger.filters.clear()
    _ensure_secret_filter(logger)
    _ensure_secret_filter(logger)
    assert sum(isinstance(f, BermudaSecretFilter) for f in logger.filters) == 1


def test_package_logger_has_filter_attached():
    """Importing const attaches the filter to the package logger."""
    package_logger = logging.getLogger("custom_components.bermuda")
    assert any(isinstance(f, BermudaSecretFilter) for f in package_logger.filters)
