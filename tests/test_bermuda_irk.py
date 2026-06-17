"""Unit tests for BermudaIrkManager (IRK / resolvable-private-address handling).

These tests exercise the real public API of ``custom_components.bermuda.bermuda_irk``
against genuine crypto: we craft a valid Resolvable Private Address (RPA) for a known
16-byte IRK using the same AES-128 ECB construction that ``bluetooth_data_tools`` uses,
so the resolution paths run for real rather than being mocked away.

The IrkManager constructor takes no arguments and has no dependency on ``hass``, so
each test simply instantiates ``BermudaIrkManager()`` directly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bluetooth_data_tools import get_cipher_for_irk, monotonic_time_coarse, resolve_private_address

from custom_components.bermuda.bermuda_irk import BermudaIrkManager, ResolvableMAC
from custom_components.bermuda.const import IrkTypes

# A known 16-byte IRK (32 hex chars) and a second, unrelated one.
IRK_A = bytes.fromhex("0123456789abcdef0123456789abcdef")
IRK_B = bytes.fromhex("fedcba9876543210fedcba9876543210")

# AES-128 ECB padding used by the BLE RPA hash function ah().
_PADDING = b"\x00" * 13


def _make_rpa(irk: bytes, prand3: bytes) -> str:
    """Build a colon-formatted RPA that resolves against ``irk``.

    Mirrors bluetooth_data_tools.resolve_private_address: the address is
    ``prand[0:3] || hash[0:3]`` where hash = AES(irk, PADDING || prand)[13:16].
    The top two bits of prand[0] must be 0b01 for the address to be a valid RPA.
    """
    cipher = get_cipher_for_irk(irk)
    enc = cipher.encryptor()
    ct = enc.update(_PADDING + prand3) + enc.finalize()
    rpa = prand3 + ct[13:16]
    return ":".join(f"{b:02x}" for b in rpa)


# A real resolvable MAC for IRK_A (prand high byte 0x40 -> valid RPA, first nibble '4').
RESOLVABLE_MAC = _make_rpa(IRK_A, bytes([0x40, 0x11, 0x22]))

# A "resolvable-format" MAC (top two bits 0b01) that does NOT match any IRK.
# First nibble '4' => (int('4',16) >> 2) == 0b01, so the unresolved branch is NO_KNOWN_IRK_MATCH.
UNMATCHED_RPA_MAC = "40:00:00:00:00:01"

# A non-resolvable address: first char '0' (top bits 0b00, not 0b01).
NON_RESOLVABLE_MAC = "08:11:22:33:44:55"

# A static-random address: first nibble 'c' (top bits 0b11). The old `& 0x04` test
# wrongly flagged this resolvable-format; the corrected `>> 2` check makes it NOT_RESOLVABLE.
STATIC_RANDOM_MAC = "c0:11:22:33:44:55"


def test_setup_sanity_resolvable_mac_matches_only_its_irk():
    """The crafted RPA resolves against its own IRK and not an unrelated one."""
    assert resolve_private_address(get_cipher_for_irk(IRK_A), RESOLVABLE_MAC) is True
    assert resolve_private_address(get_cipher_for_irk(IRK_B), RESOLVABLE_MAC) is False


def test_init_starts_empty():
    """A fresh manager has no IRKs, MACs or callbacks."""
    mgr = BermudaIrkManager()
    assert mgr._irks == {}
    assert mgr._macs == {}
    assert mgr._irk_callbacks == {}
    assert mgr.async_diagnostics_no_redactions() == {"irks": [], "macs": {}}


def test_add_irk_registers_cipher_and_is_idempotent():
    """add_irk stores a cipher and returns no matches when no MACs are known yet."""
    mgr = BermudaIrkManager()
    assert mgr.add_irk(IRK_A) == []
    assert IRK_A in mgr._irks
    cipher_first = mgr._irks[IRK_A]
    # Adding the same IRK again must not create a new cipher nor return matches.
    assert mgr.add_irk(IRK_A) == []
    assert mgr._irks[IRK_A] is cipher_first


def test_check_mac_resolves_known_irk():
    """A MAC that is a valid RPA for a registered IRK resolves to that IRK."""
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    result = mgr.check_mac(RESOLVABLE_MAC)
    assert result == IRK_A
    # And it is now cached.
    cached = mgr._macs[RESOLVABLE_MAC]
    assert isinstance(cached, ResolvableMAC)
    assert cached.irk == IRK_A
    assert cached.mac == RESOLVABLE_MAC


def test_check_mac_cached_returns_without_recompute():
    """Once resolved, check_mac returns the cached IRK even if IRKs are cleared."""
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    assert mgr.check_mac(RESOLVABLE_MAC) == IRK_A
    # Wipe the IRK table; the cached MAC entry must still drive the answer.
    mgr._irks.clear()
    assert mgr.check_mac(RESOLVABLE_MAC) == IRK_A


def test_check_mac_no_known_irk_match_for_rpa_format():
    """A resolvable-format MAC matching no IRK yields NO_KNOWN_IRK_MATCH."""
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    result = mgr.check_mac(UNMATCHED_RPA_MAC)
    assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value
    assert result in IrkTypes.unresolved()


def test_check_mac_static_random_is_not_resolvable():
    """A static-random address (first nibble C-F) is classified NOT_RESOLVABLE_ADDRESS.

    Regression for the address-type bit logic: the previous `& 0x04` test marked
    static-random addresses as resolvable-format (NO_KNOWN_IRK_MATCH); the corrected
    `>> 2 == 0b01` check classifies them NOT_RESOLVABLE_ADDRESS so they are not
    pointlessly re-tested against every known IRK.
    """
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    result = mgr.check_mac(STATIC_RANDOM_MAC)
    assert result == IrkTypes.NOT_RESOLVABLE_ADDRESS.value
    assert result in IrkTypes.unresolved()


def test_check_mac_non_resolvable_is_marked_not_resolvable():
    """check_mac marks an unmatched non-RPA-format address as NOT_RESOLVABLE_ADDRESS.

    The post-loop fallback in _validate_mac is format-aware: a non-resolvable
    address (first nibble & 0x04 == 0) is classified NOT_RESOLVABLE_ADDRESS so it
    is never re-tested, while a resolvable-format address that matched no IRK
    stays NO_KNOWN_IRK_MATCH. Both remain in IrkTypes.unresolved().
    """
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    result = mgr.check_mac(NON_RESOLVABLE_MAC)
    assert result == IrkTypes.NOT_RESOLVABLE_ADDRESS.value
    assert result in IrkTypes.unresolved()


def test_validate_mac_irk_returns_not_resolvable_for_non_rpa():
    """The per-IRK validator marks a non-RPA address as NOT_RESOLVABLE_ADDRESS.

    This branch (first hex nibble & 0x04 == 0 and no resolution) is only observable
    through the lower-level _validate_mac_irk; check_mac's post-loop fallback masks it.
    """
    mgr = BermudaIrkManager()
    cipher = get_cipher_for_irk(IRK_A)
    result = mgr._validate_mac_irk(NON_RESOLVABLE_MAC, IRK_A, cipher)
    assert result == IrkTypes.NOT_RESOLVABLE_ADDRESS.value


def test_check_mac_with_no_irks_returns_unresolved():
    """With no IRKs registered at all, _validate_mac still classifies the address."""
    mgr = BermudaIrkManager()
    # No IRKs -> the for-loop is empty -> falls through to NO_KNOWN_IRK_MATCH.
    result = mgr.check_mac(RESOLVABLE_MAC)
    assert result == IrkTypes.NO_KNOWN_IRK_MATCH.value


def test_add_irk_matches_previously_unknown_mac():
    """A MAC seen (and unresolved) before the IRK arrives is matched on add_irk."""
    mgr = BermudaIrkManager()
    # See the MAC first with no matching IRK present.
    first = mgr.check_mac(RESOLVABLE_MAC)
    assert first == IrkTypes.NO_KNOWN_IRK_MATCH.value
    # Now the IRK is learned: add_irk should report the now-matching MAC.
    matched = mgr.add_irk(IRK_A)
    assert RESOLVABLE_MAC in matched
    # The cached entry is upgraded to the real IRK.
    assert mgr._macs[RESOLVABLE_MAC].irk == IRK_A


def test_add_macirk_returns_resolved_irk():
    """add_macirk registers the IRK, validates the MAC and returns the IRK."""
    mgr = BermudaIrkManager()
    result = mgr.add_macirk(RESOLVABLE_MAC, IRK_A)
    assert result == IRK_A
    assert IRK_A in mgr._irks
    assert mgr._macs[RESOLVABLE_MAC].irk == IRK_A


def test_add_macirk_mismatch_warns_and_returns_unresolved(caplog):
    """add_macirk with a MAC that does not resolve against the IRK warns."""
    mgr = BermudaIrkManager()
    # IRK_B does not resolve RESOLVABLE_MAC; first char '4' -> NO_KNOWN_IRK_MATCH.
    result = mgr.add_macirk(RESOLVABLE_MAC, IRK_B)
    assert result in IrkTypes.unresolved()
    assert "do not resolve" in caplog.text


def test_known_macs_filters_unresolved():
    """known_macs(resolved=True) returns only matched MACs; False returns all."""
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    mgr.check_mac(RESOLVABLE_MAC)  # resolves to IRK_A
    mgr.check_mac(NON_RESOLVABLE_MAC)  # NOT_RESOLVABLE_ADDRESS
    resolved_only = mgr.known_macs(resolved=True)
    assert set(resolved_only) == {RESOLVABLE_MAC}
    all_macs = mgr.known_macs(resolved=False)
    assert set(all_macs) == {RESOLVABLE_MAC, NON_RESOLVABLE_MAC}
    # known_macs must return copies / fresh dicts (not the internal store).
    assert all_macs is not mgr._macs


def test_validate_mac_irk_with_unprepared_cipher():
    """_validate_mac_irk builds a cipher on the fly when passed None."""
    mgr = BermudaIrkManager()
    # Do not pre-register the IRK; pass cipher=None to force the fallback path.
    result = mgr._validate_mac_irk(RESOLVABLE_MAC, IRK_A, None)
    assert result == IRK_A


def test_async_prune_removes_expired_macs():
    """async_prune deletes entries whose expiry has passed, keeps fresh ones."""
    mgr = BermudaIrkManager()
    now = monotonic_time_coarse()
    fresh = "11:22:33:44:55:66"
    stale = "aa:bb:cc:dd:ee:ff"
    mgr._macs[fresh] = ResolvableMAC(fresh, int(now + 600), IRK_A)
    mgr._macs[stale] = ResolvableMAC(stale, int(now - 10), IRK_A)
    mgr.async_prune()
    assert fresh in mgr._macs
    assert stale not in mgr._macs


def test_async_prune_no_expired_is_noop():
    """async_prune leaves everything in place when nothing is expired."""
    mgr = BermudaIrkManager()
    now = monotonic_time_coarse()
    mgr._macs["11:22:33:44:55:66"] = ResolvableMAC("11:22:33:44:55:66", int(now + 600), IRK_A)
    mgr.async_prune()
    assert len(mgr._macs) == 1


def test_register_irk_callback_fires_for_existing_mac():
    """Registering a callback fires immediately for already-matched MACs."""
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    mgr.check_mac(RESOLVABLE_MAC)  # establishes the matched MAC
    cb = MagicMock()
    cancel = mgr.register_irk_callback(cb, IRK_A)
    # Should have been invoked once for the existing matching MAC.
    cb.assert_called_once()
    service_info, change = cb.call_args.args
    assert service_info.address == RESOLVABLE_MAC
    # Cancelling removes the registration and cleans up the empty list.
    cancel()
    assert IRK_A not in mgr._irk_callbacks


def test_register_irk_callback_fires_on_new_resolution():
    """A registered callback is invoked when a new MAC resolves to its IRK."""
    mgr = BermudaIrkManager()
    cb = MagicMock()
    mgr.register_irk_callback(cb, IRK_A)
    cb.assert_not_called()
    # Now a new MAC arrives and resolves; the callback should fire.
    mgr.check_mac(RESOLVABLE_MAC)
    cb.assert_called_once()
    service_info, _change = cb.call_args.args
    assert service_info.address == RESOLVABLE_MAC


def test_cancel_callback_stops_future_firing():
    """Once cancelled, a callback no longer fires for new resolutions."""
    mgr = BermudaIrkManager()
    cb = MagicMock()
    cancel = mgr.register_irk_callback(cb, IRK_A)
    cancel()
    mgr.check_mac(RESOLVABLE_MAC)
    cb.assert_not_called()


def test_diagnostics_shape_and_contents():
    """Diagnostics exposes registered IRKs and resolved/no-match MACs only.

    Entries marked NOT_RESOLVABLE_ADDRESS or ADDRESS_NOT_EVALUATED are filtered out;
    we insert those directly since check_mac never produces them as a final state.
    """
    mgr = BermudaIrkManager()
    mgr.add_irk(IRK_A)
    mgr.check_mac(RESOLVABLE_MAC)  # resolved -> IRK_A
    mgr.check_mac(UNMATCHED_RPA_MAC)  # NO_KNOWN_IRK_MATCH (included in diag)
    # Directly seed filtered-out marker states.
    now = monotonic_time_coarse()
    mgr._macs[NON_RESOLVABLE_MAC] = ResolvableMAC(
        NON_RESOLVABLE_MAC, int(now + 600), IrkTypes.NOT_RESOLVABLE_ADDRESS.value
    )
    pending = "0a:0b:0c:0d:0e:0f"
    mgr._macs[pending] = ResolvableMAC(pending, int(now + 600), IrkTypes.ADDRESS_NOT_EVALUATED.value)

    diag = mgr.async_diagnostics_no_redactions()

    # IRK key material must NEVER appear; it is shown as a stable label instead.
    assert "IRK_0" in diag["irks"]
    assert IRK_A.hex() not in diag["irks"]
    macs = diag["macs"]
    # Resolved MAC reports the IRK's stable label, never the raw key.
    assert macs[RESOLVABLE_MAC]["irk"] == "IRK_0"
    assert IRK_A.hex() not in str(macs[RESOLVABLE_MAC])
    assert isinstance(macs[RESOLVABLE_MAC]["expires_in"], int)
    # NO_KNOWN_IRK_MATCH is rendered by name.
    assert macs[UNMATCHED_RPA_MAC]["irk"] == IrkTypes.NO_KNOWN_IRK_MATCH.name
    # NOT_RESOLVABLE_ADDRESS and ADDRESS_NOT_EVALUATED entries are filtered out.
    assert NON_RESOLVABLE_MAC not in macs
    assert pending not in macs


def test_irktypes_unresolved_contains_all_markers():
    """The unresolved() helper lists every IrkTypes marker value."""
    unresolved = IrkTypes.unresolved()
    assert IrkTypes.ADDRESS_NOT_EVALUATED.value in unresolved
    assert IrkTypes.NOT_RESOLVABLE_ADDRESS.value in unresolved
    assert IrkTypes.NO_KNOWN_IRK_MATCH.value in unresolved
    # A genuine 16-byte IRK must never collide with a marker.
    assert IRK_A not in unresolved


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
