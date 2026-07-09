"""
Pure helper functions for Bermuda.

These functions take Bermuda objects/values and return values, with no hass
access and no I/O, so they can be unit-tested in isolation. Shared by the
options flow steps that offer devices for tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import ADDR_TYPE_PRIVATE_BLE_DEVICE, BDADDR_TYPE_RANDOM_RESOLVABLE

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice


def is_device_selectable(device: BermudaDevice, stale_cutoff: float) -> bool:
    """
    Whether a device should be offered in the tracking pickers.

    Scanners aren't tracked; Private BLE devices configure themselves; and a
    random (resolvable) MAC not seen since ``stale_cutoff`` (monotonic stamp)
    has almost certainly rotated away and would only clutter the list.
    """
    if device.is_scanner or device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
        return False
    return not (device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE and device.last_seen < stale_cutoff)
