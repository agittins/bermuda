"""IRKs are key material: never logged or written to diagnostics in full."""

from __future__ import annotations

import json
import logging

from custom_components.bermuda.bermuda_irk import BermudaIrkManager, get_cipher_for_irk

IRK = bytes(range(16))


def _address_resolved_by(irk: bytes) -> str:
    prand = bytes([0x40, 0x00, 0x01])
    enc = get_cipher_for_irk(irk).encryptor()
    ah = (enc.update(bytes(13) + prand) + enc.finalize())[-3:]
    return ":".join(f"{b:02x}" for b in prand + ah)


def test_irk_never_appears_in_full(caplog):
    manager = BermudaIrkManager()
    caplog.set_level(logging.DEBUG, logger="custom_components.bermuda.bermuda_irk")
    manager.add_irk(IRK)
    assert manager.check_mac(_address_resolved_by(IRK)) == IRK

    blob = json.dumps(manager.async_diagnostics_no_redactions())
    assert IRK.hex() not in blob
    assert "0001..." in blob
    assert IRK.hex() not in caplog.text
    assert "Saved NEW Macirk pair" in caplog.text
