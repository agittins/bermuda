"""Edge-case coverage for the helper utilities in util.py."""

from __future__ import annotations

import pytest

from custom_components.bermuda.util import (
    address_is_resolvable,
    clean_charbuf,
    mac_math_offset,
    mac_norm,
    mac_redact,
    rssi_to_metres,
)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        # 0b01 (first nibble 4-7) -> resolvable private address (IRK device).
        ("40:11:22:33:44:55", True),
        ("4a:00:00:00:00:01", True),
        ("5f:00:00:00:00:01", True),
        ("6c:00:00:00:00:01", True),
        ("7d:00:00:00:00:01", True),
        # 0b00 (0-3) -> non-resolvable private.
        ("00:11:22:33:44:55", False),
        ("3f:11:22:33:44:55", False),
        # 0b10 (8-B) -> reserved.
        ("80:11:22:33:44:55", False),
        ("bf:11:22:33:44:55", False),
        # 0b11 (C-F) -> static random. The old `& 0x04` test wrongly flagged these
        # resolvable; the corrected `>> 2` check classifies them non-resolvable.
        ("c0:11:22:33:44:55", False),
        ("d0:11:22:33:44:55", False),
        ("ef:11:22:33:44:55", False),
        ("ff:11:22:33:44:55", False),
    ],
)
def test_address_is_resolvable(address, expected):
    assert address_is_resolvable(address) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),  # already colon form
        ("AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"),  # dash separators
        ("AA_BB_CC_DD_EE_FF", "aa:bb:cc:dd:ee:ff"),  # underscore separators
        ("AABB.CCDD.EEFF", "aa:bb:cc:dd:ee:ff"),  # dotted 14-char form
        ("AABBCCDDEEFF", "aa:bb:cc:dd:ee:ff"),  # bare 12-char form
        ("not-a-mac", "not-a-mac"),  # passthrough, lower-cased
    ],
)
def test_mac_norm_forms(raw, expected):
    assert mac_norm(raw) == expected


def test_mac_redact_default_tag():
    """mac_redact keeps the first/last octet and hides the middle."""
    redacted = mac_redact("aa:bb:cc:dd:ee:ff")
    assert redacted.startswith("aa")
    assert redacted.endswith("ff")
    assert "bb" not in redacted and "cc" not in redacted


def test_mac_redact_custom_tag():
    assert mac_redact("aa:bb:cc:dd:ee:ff", "TAG").startswith("aa")
    assert "TAG" in mac_redact("aa:bb:cc:dd:ee:ff", "TAG")


def test_rssi_to_metres_requires_calibration():
    assert rssi_to_metres(-70, ref_power=None, attenuation=2.0) is None
    assert rssi_to_metres(-70, ref_power=-55, attenuation=None) is None
    # With calibration it returns a positive distance.
    assert rssi_to_metres(-55, ref_power=-55, attenuation=2.0) == pytest.approx(1.0)


def test_mac_math_offset():
    assert mac_math_offset("aa:bb:cc:dd:ee:01", 1) == "aa:bb:cc:dd:ee:02"
    assert mac_math_offset("aa:bb:cc:dd:ee:02", -1) == "aa:bb:cc:dd:ee:01"
    assert mac_math_offset("aa:bb:cc:dd:ee:ff", 1) is None  # overflow past 0xFF
    assert mac_math_offset(None, 1) is None


def test_clean_charbuf():
    assert clean_charbuf(None) == ""
    assert clean_charbuf("  hello\x00world  ") == "hello"
    assert clean_charbuf("\t tidy \r\n") == "tidy"
