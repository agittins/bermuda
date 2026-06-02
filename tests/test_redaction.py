"""Tests for the coordinator redaction logic.

Targets ``BermudaDataUpdateCoordinator.redact_data`` and
``redaction_list_update``. These methods only read a handful of attributes,
so we build a bare coordinator with ``object.__new__`` and inject exactly
those attributes (mirroring what ``__init__`` would have set), without
needing a running HomeAssistant instance.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from custom_components.bermuda.const import (
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    CONF_DEVICES,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

# A regular (random/public) MAC address type marker. Anything that is *not*
# ADDR_TYPE_PRIVATE_BLE_DEVICE flows through the non-IRK branches.
ADDR_TYPE_OTHER = "addr_type_random"


def _make_coordinator(
    *,
    scanners=None,
    configured=None,
    devices=None,
):
    """Build a bare coordinator wired up only for the redaction methods.

    Mirrors the exact regex/sub compiled in ``__init__`` so the generic
    fallback substitution behaves identically to production.
    """
    coord = object.__new__(BermudaDataUpdateCoordinator)
    coord.redactions = {}
    coord.stamp_redactions_expiry = None
    coord.stamp_last_prune = 0.0
    coord._scanner_list = set(scanners or [])
    coord.options = {CONF_DEVICES: list(configured or [])}
    coord.devices = devices or {}
    # Collaborator that the IRK branch never actually calls, but present for
    # parity with the real object.
    coord.irk_manager = SimpleNamespace()
    # Copied verbatim from BermudaDataUpdateCoordinator.__init__.
    coord._redact_generic_re = re.compile(
        r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})"
    )
    coord._redact_generic_sub = r"\g<start>:xx:xx:xx:xx:\g<end>"
    return coord


def _device(address_type=ADDR_TYPE_OTHER):
    return SimpleNamespace(address_type=address_type)


# ---------------------------------------------------------------------------
# redaction_list_update
# ---------------------------------------------------------------------------


def test_scanner_branch_explodes_all_mac_formats():
    """A scanner MAC produces a SCANNER_n entry for every separator format."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    coord.redaction_list_update()

    expected_label = "aa::SCANNER_1::ff"
    # All exploded formats of the lowercased mac map to the same label.
    for variant in (
        "aa:bb:cc:dd:ee:ff",
        "aabbccddeeff",
        "aa-bb-cc-dd-ee-ff",
        "aa_bb_cc_dd_ee_ff",
        "aa.bb.cc.dd.ee.ff",
    ):
        assert coord.redactions[variant] == expected_label


def test_configured_mac_uses_cfg_mac_label():
    """A 17-char configured address is treated as a MAC -> CFG_MAC_n."""
    coord = _make_coordinator(configured=["11:22:33:44:55:66"])
    coord.redaction_list_update()

    assert coord.redactions["11:22:33:44:55:66"] == "11::CFG_MAC_1::66"
    # exploded too
    assert coord.redactions["112233445566"] == "11::CFG_MAC_1::66"


def test_configured_ibeacon_branch():
    """A configured iBeacon (two underscores) yields CFG_iBea entries."""
    # uuid (32 hex) + _major + _minor => exactly two underscores.
    uuid = "0123456789abcdef0123456789abcdef"
    addr = f"{uuid}_100_200"
    coord = _make_coordinator(configured=[addr])
    coord.redaction_list_update()

    # The full beacon address maps to a CFG_iBea label.
    assert coord.redactions[addr] == f"{addr[:4]}::CFG_iBea_1::{addr[32:]}"
    # The raw uuid (split on first underscore) is also redacted.
    assert coord.redactions[uuid] == f"{addr[:4]}::CFG_iBea_1_{addr[32:]}::"


def test_configured_other_branch():
    """A configured address that is neither MAC nor beacon -> CFG_OTHER_n."""
    coord = _make_coordinator(configured=["some-weird-identifier"])
    coord.redaction_list_update()

    assert coord.redactions["some-weird-identifier"] == "CFG_OTHER_1_some-weird-identifier"


def test_other_device_mac_branch():
    """An unconfigured plain-MAC device -> OTHER_MAC_n."""
    coord = _make_coordinator(devices={"AA:11:22:33:44:55": _device()})
    coord.redaction_list_update()

    assert coord.redactions["aa:11:22:33:44:55"] == "aa::OTHER_MAC_1::55"
    assert coord.redactions["aa1122334455"] == "aa::OTHER_MAC_1::55"


def test_other_device_irk_branch():
    """A private-BLE (IRK) device -> IRK_DEV_n with no center redaction."""
    addr = "deadbeefdeadbeefdeadbeefdeadbeef"  # not a MAC, no underscores
    coord = _make_coordinator(devices={addr: _device(address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE)})
    coord.redaction_list_update()

    assert coord.redactions[addr] == f"{addr[:4]}::IRK_DEV_1"


def test_other_device_ibeacon_branch():
    """An unconfigured iBeacon device -> OTHER_iBea_n plus its raw uuid."""
    uuid = "abcdefabcdefabcdefabcdefabcdefab"
    addr = f"{uuid}_7_9"
    coord = _make_coordinator(devices={addr: _device()})
    coord.redaction_list_update()

    assert coord.redactions[addr] == f"{addr[:4]}::OTHER_iBea_1::{addr[32:]}"
    assert coord.redactions[uuid] == f"{addr[:4]}::OTHER_iBea_1_{addr[32:]}::"


def test_other_device_other_branch():
    """An unconfigured, unidentifiable device -> OTHER_n."""
    coord = _make_coordinator(devices={"mystery-token": _device()})
    coord.redaction_list_update()

    assert coord.redactions["mystery-token"] == "OTHER_1_mystery-token"


def test_configured_takes_priority_over_device_entry():
    """A configured device is labelled CFG_* and the device loop skips it."""
    mac = "ab:cd:ef:01:23:45"
    coord = _make_coordinator(configured=[mac], devices={mac: _device()})
    coord.redaction_list_update()

    # Configured branch wins; the 'EVERYTHING ELSE' loop must not overwrite it.
    assert coord.redactions[mac] == "ab::CFG_MAC_1::45"
    assert "OTHER_MAC" not in coord.redactions[mac]


def test_update_sets_expiry_stamp():
    """redaction_list_update arms the expiry stamp."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    assert coord.stamp_redactions_expiry is None
    coord.redaction_list_update()
    assert coord.stamp_redactions_expiry is not None
    assert coord.stamp_redactions_expiry > 0


def test_update_is_idempotent_for_existing_entries():
    """Re-running does not re-number or duplicate already-known addresses."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    coord.redaction_list_update()
    before = dict(coord.redactions)
    coord.redaction_list_update()
    assert coord.redactions == before


def test_counter_seeds_from_existing_redaction_length():
    """The numbering counter starts at len(self.redactions).

    With one pre-existing redaction entry, a freshly added scanner is
    numbered 2 rather than 1.
    """
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    coord.redactions = {"preexisting": "PRE_0"}
    coord.redaction_list_update()
    assert coord.redactions["aa:bb:cc:dd:ee:ff"] == "aa::SCANNER_2::ff"


# ---------------------------------------------------------------------------
# redact_data
# ---------------------------------------------------------------------------


def test_redact_known_address_full_string_match():
    """A string equal to a known address is replaced with its label."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    out = coord.redact_data("AA:BB:CC:DD:EE:FF")
    assert out == "aa::SCANNER_1::ff"


def test_redact_generic_regex_for_unknown_mac():
    """An unknown MAC-shaped string is masked by the generic fallback."""
    coord = _make_coordinator()  # no known redactions
    out = coord.redact_data("12:34:56:78:9a:bc")
    assert out == "12:xx:xx:xx:xx:bc"


def test_redact_generic_regex_inside_larger_string():
    """The generic regex masks a MAC embedded in surrounding text."""
    coord = _make_coordinator()
    out = coord.redact_data("device at 12:34:56:78:9a:bc is here")
    assert out == "device at 12:xx:xx:xx:xx:bc is here"


def test_non_address_string_passes_through():
    """Plain text with no addresses is returned unchanged."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    assert coord.redact_data("just a label") == "just a label"


def test_substring_match_redacts_within_string():
    """A known address appearing as a substring is redacted in place."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    out = coord.redact_data("scanner=AA:BB:CC:DD:EE:FF online")
    assert out == "scanner=aa::SCANNER_1::ff online"


def test_recursion_over_nested_dict_keys_and_values():
    """Recursion redacts both keys and values inside dicts."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    out = coord.redact_data(
        {
            "AA:BB:CC:DD:EE:FF": {"address": "AA:BB:CC:DD:EE:FF"},
        }
    )
    assert out == {"aa::SCANNER_1::ff": {"address": "aa::SCANNER_1::ff"}}


def test_recursion_over_list():
    """Recursion redacts every element of a list."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    out = coord.redact_data(["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66", "plain"])
    assert out == ["aa::SCANNER_1::ff", "11:xx:xx:xx:xx:66", "plain"]


def test_non_string_scalars_pass_through_unchanged():
    """Ints, floats, None and bools are returned as-is by the base case."""
    coord = _make_coordinator()
    payload = {"count": 5, "ratio": 1.5, "missing": None, "flag": True}
    out = coord.redact_data(payload)
    assert out == payload


def test_redact_data_refreshes_redaction_list_on_first_call():
    """The outer call rebuilds the redaction list, picking up new addresses.

    The redactions dict starts empty; redact_data must populate it from the
    configured/scanner/device lists before doing the substitution.
    """
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF"])
    assert coord.redactions == {}
    out = coord.redact_data("AA:BB:CC:DD:EE:FF")
    assert out == "aa::SCANNER_1::ff"
    # The list was built as a side effect.
    assert coord.redactions != {}


def test_multiple_addresses_in_one_string_all_redacted():
    """Cumulative replacement redacts every known address in a string."""
    coord = _make_coordinator(scanners=["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"])
    coord.redaction_list_update()
    # Build a string with both known scanner addresses.
    out = coord.redact_data("AA:BB:CC:DD:EE:FF and 11:22:33:44:55:66")
    assert "SCANNER_1" in out
    assert "SCANNER_2" in out
    # No raw mac octets should survive.
    assert "cc:dd" not in out
    assert "33:44" not in out


@pytest.mark.parametrize(
    ("sep", "expected"),
    [
        (":", "12:xx:xx:xx:xx:bc"),
        ("-", "12:xx:xx:xx:xx:bc"),
        ("_", "12:xx:xx:xx:xx:bc"),
    ],
)
def test_generic_regex_handles_separator_variants(sep, expected):
    """The generic fallback masks colon/dash/underscore-separated MACs."""
    coord = _make_coordinator()
    mac = sep.join(["12", "34", "56", "78", "9a", "bc"])
    assert coord.redact_data(mac) == expected
