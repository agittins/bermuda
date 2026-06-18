"""
Enrol a privacy device (iPhone, Apple Watch, …) by its IRK.

Bermuda cannot pair the way ESPresense's firmware does: its scanners are HA
Bluetooth proxies, which only *listen*. The Home Assistant native equivalent is
the ``private_ble_device`` integration — you give it a device's Identity
Resolving Key (IRK) and it resolves the rotating MAC. Bermuda then tracks that
private-BLE metadevice automatically.

This module is the thin front-end that turns an IRK into a private_ble_device
config entry, shared by the options flow ("Enrol a private device") and the
``bermuda.enrol_private_device`` service.
"""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from .const import CONF_IRK, DOMAIN_PRIVATE_BLE_DEVICE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def parse_irk(irk: str) -> bytes | None:
    """
    Decode an IRK from hex or iOS base64 into its 16 raw bytes.

    Accepts an optional ``irk:`` prefix. The iOS Keychain "Remote IRK" form is
    base64 ending in ``=`` and is byte-reversed to match private_ble_device.
    Returns None if the value is not a valid 16-byte key.
    """
    irk = irk.strip().removeprefix("irk:")
    try:
        if irk.endswith("="):
            raw = bytes(reversed(base64.b64decode(irk)))
        else:
            raw = binascii.unhexlify(irk)
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == 16 else None


async def async_enrol_private_device(hass: HomeAssistant, irk: str, name: str = "") -> str:
    """
    Create a private_ble_device entry for ``irk`` so Bermuda tracks it.

    Returns an empty string on success, otherwise an error key:
    ``irk_not_valid`` | ``irk_not_found`` | ``bluetooth_not_available`` | ``unknown``.

    Validation (16-byte key, device currently advertising in range, a Bluetooth
    adapter/proxy being available) is delegated to private_ble_device's own
    config flow so there is a single source of truth.
    """
    if parse_irk(irk) is None:
        return "irk_not_valid"

    result = await hass.config_entries.flow.async_init(DOMAIN_PRIVATE_BLE_DEVICE, context={"source": SOURCE_USER})
    if result["type"] == FlowResultType.ABORT:
        # e.g. bluetooth_not_available
        return result.get("reason", "unknown")

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_IRK: irk})
    if result["type"] == FlowResultType.CREATE_ENTRY:
        if name and result.get("result") is not None:
            hass.config_entries.async_update_entry(result["result"], title=name)
        return ""

    # The flow re-showed its form: surface the first field error (irk_not_found…).
    return next(iter(result.get("errors", {}).values()), "unknown")
