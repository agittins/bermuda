"""
Pure classification of Bermuda device addresses.

Extracted from ``BermudaDevice._async_process_address_type`` so the
address-shape rules (MAC vs iBeacon identity vs IRK, and the BLE random
sub-types) can be unit-tested in isolation. Classification is pure; any
side effects the type implies (IRK callback registration, metadevice
setup) stay with the device object.
"""

from __future__ import annotations

import re

from .const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESERVED,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    BDADDR_TYPE_RANDOM_STATIC,
    BDADDR_TYPE_RANDOM_UNRESOLVABLE,
)

# iBeacon metadevice identity: uuid_major_minor (uuid is 32 hex chars).
_IBEACON_ADDRESS_RE = re.compile(r"^[A-Fa-f0-9]{32}_[A-Fa-f0-9]*_[A-Fa-f0-9]*$")
# A bare 32-char hex string is an IRK (private BLE device identity).
_IRK_ADDRESS_RE = re.compile(r"^[A-Fa-f0-9]{32}$")

_MAC48_LEN = 17  # aa:bb:cc:dd:ee:ff

# The two MSBs of the first octet dictate the BLE random address sub-type.
_RANDOM_TYPES_BY_MSBS = {
    0b00: BDADDR_TYPE_RANDOM_UNRESOLVABLE,  # First char will be in [0 1 2 3]
    0b01: BDADDR_TYPE_RANDOM_RESOLVABLE,  # First char will be 4, 5, 6 or 7
    0b10: BDADDR_TYPE_RANDOM_RESERVED,
    0b11: BDADDR_TYPE_RANDOM_STATIC,
}


def classify_address(address: str) -> str:
    """
    Classify an address string into one of Bermuda's ADDR/BDADDR types.

    BLE MAC addresses (https://www.bluetooth.com/specifications/core54-html/)
    are differentiated by the two MSBs of the first octet:

    - 0b00 (0x00 - 0x3F) Random Private Non-resolvable
    - 0b01 (0x40 - 0x7F) Random Private Resolvable (ie, IRK devices)
    - 0b10 (0x80 - 0xBF) ~* Reserved *~ (Is this where ALL Publics live?)
    - 0b11 (0xC0 - 0xFF) Random Static (may change on power cycle only)

    Non-MAC shapes are Bermuda metadevice identities: an iBeacon
    uuid_major_minor or a 32-hex IRK.
    """
    if address.count(":") != 5:
        # Doesn't look like an actual MAC address - should be some sort of metadevice.
        if _IBEACON_ADDRESS_RE.match(address):
            return ADDR_TYPE_IBEACON
        if _IRK_ADDRESS_RE.match(address):
            return ADDR_TYPE_PRIVATE_BLE_DEVICE
        # We have no idea, currently. Mark it as such so we don't test it again.
        return BDADDR_TYPE_NOT_MAC48

    if len(address) == _MAC48_LEN:
        top_bits = (int(address[0:1], 16) >> 2) & 0b11
        return _RANDOM_TYPES_BY_MSBS[top_bits]

    # Fallback for any other colon-form address shape.
    # (No OUI->manufacturer lookup here: the SIG tables are keyed by
    # 16-bit company IDs, so a 24-bit OUI prefix never matches.)
    return BDADDR_TYPE_OTHER
