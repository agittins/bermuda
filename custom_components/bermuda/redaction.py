"""
MAC-address redaction for Bermuda diagnostics and the dump_devices service.

Builds a stable set of match -> replacement pairs so device entries stay
identifiable in a dump without disclosing real MAC addresses, then applies them
recursively. Extracted from the coordinator, which keeps thin wrappers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .const import ADDR_TYPE_PRIVATE_BLE_DEVICE
from .util import mac_explode_formats

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# Generic fallback: redact the centre octets of any remaining xx:xx:xx:xx:xx:xx
# (with :, _ or - separators) that the substitution table did not catch.
REDACT_GENERIC_RE = re.compile(r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})")
REDACT_GENERIC_SUB = r"\g<start>:xx:xx:xx:xx:\g<end>"

# Second fallback: a bare 32-hex run (an IRK key or an iBeacon UUID) carries no
# separators and so slips past REDACT_GENERIC_RE. An IRK is cryptographically
# sensitive — it permanently de-anonymises a device across every MAC rotation — so
# mask any standalone 32-hex run, keeping only the first 4 chars for correlation.
REDACT_HEX32_RE = re.compile(r"(?<![0-9A-Fa-f])(?P<start>[0-9A-Fa-f]{4})[0-9A-Fa-f]{28}(?![0-9A-Fa-f])")
REDACT_HEX32_SUB = r"\g<start>::redacted_hex32::"

# Human-readable names (set by the user, or broadcast as a BLE local_name) can carry
# personal information ("Jan's iPhone") and never look like a MAC, so the address
# machinery never catches them. Register each name at least this long for substitution;
# shorter ones are skipped to avoid swallowing unrelated substrings of the dump.
REDACT_NAME_MIN_LENGTH = 3
_REDACT_NAME_ATTRS = ("name_by_user", "name_devreg", "name_bt_local_name", "name_bt_serviceinfo")


def _register_device_names(device: Any, redactions: dict[str, str], counter: int) -> int:
    """Add a device's human-readable names to ``redactions`` and return the new counter."""
    for attr in _REDACT_NAME_ATTRS:
        name = getattr(device, attr, None)
        if isinstance(name, str) and len(name) >= REDACT_NAME_MIN_LENGTH:
            key = name.lower()
            if key not in redactions:
                counter += 1
                redactions[key] = f"NAME_{counter}"
    return counter


def update_redaction_list(
    redactions: dict[str, str],
    scanner_list: Iterable[str],
    configured_devices: Iterable[str],
    devices: Mapping[str, Any],
) -> None:
    """
    Freshen ``redactions`` (mutated in place) with match/replacement pairs.

    Scanners, configured devices and everything else each get a distinctive,
    privacy-preserving replacement (e.g. ``aa::SCANNER_1::ff``). The numbering
    counter is seeded from the existing list length so repeat calls are stable.
    """
    i = len(redactions)

    # SCANNERS
    for non_lower_address in scanner_list:
        address = non_lower_address.lower()
        if address not in redactions:
            i += 1
            for altmac in mac_explode_formats(address):
                redactions[altmac] = f"{address[:2]}::SCANNER_{i}::{address[-2:]}"

    # CONFIGURED DEVICES
    for non_lower_address in configured_devices:
        address = non_lower_address.lower()
        if address not in redactions:
            i += 1
            if address.count("_") == 2:
                redactions[address] = f"{address[:4]}::CFG_iBea_{i}::{address[32:]}"
                # Raw uuid in advert
                redactions[address.split("_")[0]] = f"{address[:4]}::CFG_iBea_{i}_{address[32:]}::"
            elif len(address) == 17:
                for altmac in mac_explode_formats(address):
                    redactions[altmac] = f"{address[:2]}::CFG_MAC_{i}::{address[-2:]}"
            else:
                # Don't know what it is, but not a mac.
                redactions[address] = f"CFG_OTHER_{i}_{address}"

    # EVERYTHING ELSE
    for non_lower_address, device in devices.items():
        address = non_lower_address.lower()
        if address not in redactions:
            i += 1
            if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                redactions[address] = f"{address[:4]}::IRK_DEV_{i}"
            elif address.count("_") == 2:
                redactions[address] = f"{address[:4]}::OTHER_iBea_{i}::{address[32:]}"
                # Raw uuid in advert
                redactions[address.split("_")[0]] = f"{address[:4]}::OTHER_iBea_{i}_{address[32:]}::"
            elif len(address) == 17:  # a MAC
                for altmac in mac_explode_formats(address):
                    redactions[altmac] = f"{address[:2]}::OTHER_MAC_{i}::{address[-2:]}"
            else:
                # Don't know what it is.
                redactions[address] = f"OTHER_{i}_{address}"
        # Names live on the device object regardless of how its address was labelled,
        # so register them on every pass (not just when the address is new).
        i = _register_device_names(device, redactions, i)


def redact_value(
    data: Any,
    redactions: dict[str, str],
    generic_re: re.Pattern[str] = REDACT_GENERIC_RE,
    generic_sub: str = REDACT_GENERIC_SUB,
) -> Any:
    """
    Recursively wash any MAC-like addresses out of ``data``.

    Strings are matched against the substitution table (full match first, then
    every substring match applied cumulatively) and any remaining MAC pattern is
    blanked by ``generic_re``. Dicts/lists are walked; other scalars pass through.
    """
    if isinstance(data, str):
        datalower = data.lower()
        if datalower in redactions:
            # Full string match, a quick short-circuit.
            data = redactions[datalower]
        else:
            # Apply every substring match cumulatively so a string with multiple
            # addresses gets all of them redacted.
            redacted = datalower
            for find, fix in list(redactions.items()):
                if find in redacted:
                    redacted = redacted.replace(find, fix)
            if redacted != datalower:
                # Only adopt the lower-cased form if we actually redacted.
                data = redacted
        data = generic_re.sub(generic_sub, data)
        return REDACT_HEX32_RE.sub(REDACT_HEX32_SUB, data)
    if isinstance(data, dict):
        return {
            redact_value(k, redactions, generic_re, generic_sub): redact_value(v, redactions, generic_re, generic_sub)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_value(v, redactions, generic_re, generic_sub) for v in data]
    return data
