"""Tests for BermudaDataUpdateCoordinator."""

from __future__ import annotations

import re
from types import SimpleNamespace

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator


def test_handle_devreg_malformed_identifier():
    """A malformed device identifier must not crash the devreg handler.

    Regression test: Home Assistant device identifiers are expected to be
    ``(domain, id)`` 2-tuples, but a buggy integration can register a
    malformed one (observed in the wild: a Plejd device whose id string was
    stored as many single-character elements). Bermuda unpacked every
    identifier directly, so such a device raised
    ``ValueError: too many values to unpack`` and broke the entire
    ``device_registry_updated`` handler on every registry change.

    The handler must skip malformed identifiers, still process valid ones,
    and run to completion.
    """
    # A device with a non-Bermuda connection (so we reach the identifier
    # branch), one malformed identifier and one valid Bermuda identifier.
    device_entry = SimpleNamespace(
        connections={("mac", "AA:BB:CC:DD:EE:FF")},
        identifiers={
            ("plejd", "D", "8", "9", "D", "F", "D", "A"),  # malformed: not a 2-tuple
            ("bermuda", "aa:bb:cc:dd:ee:ff"),  # valid (domain, id)
        },
        name_by_user=None,
    )

    # Lightweight stand-in for the coordinator; we invoke the real (unbound)
    # handler with it as ``self`` to avoid setting up the full integration.
    coordinator = SimpleNamespace(
        devices={},
        dr=SimpleNamespace(async_get=lambda device_id: device_entry),
        _scanner_init_pending=False,
        _do_private_device_init=False,
    )

    event = SimpleNamespace(data={"action": "update", "device_id": "malformed-device", "changes": {}})

    # Previously raised ValueError: too many values to unpack (expected 2).
    BermudaDataUpdateCoordinator.handle_devreg_changes(coordinator, event)

    # Reached the end of the identifier branch without raising.
    assert coordinator._scanner_init_pending is True


def test_redact_data_redacts_every_address_in_one_string():
    """Replacements compose: a string holding two known addresses loses both."""
    coordinator = SimpleNamespace(
        redactions={
            "aa:bb:cc:dd:ee:01": "aa::SCANNER_1::01",
            "aa:bb:cc:dd:ee:02": "aa::CFG_MAC_2::02",
        },
        _redact_generic_re=re.compile(
            r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})"
        ),
        _redact_generic_sub=r"\g<start>:xx:xx:xx:xx:\g<end>",
        redaction_list_update=lambda: None,
    )
    out = BermudaDataUpdateCoordinator.redact_data(
        coordinator, "AA:BB:CC:DD:EE:01 heard by aa:bb:cc:dd:ee:02 and 11:22:33:44:55:66"
    )
    assert out == "aa::SCANNER_1::01 heard by aa::CFG_MAC_2::02 and 11:xx:xx:xx:xx:66"
