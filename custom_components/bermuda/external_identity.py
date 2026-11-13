"""
Public, ephemeral association of external identities with Bluetooth addresses.

All timestamps are Bluetooth monotonic timestamps, not wall-clock timestamps.
Only actual scanner observations collected by Bermuda are used as evidence.
"""

# This module integrates with Bermuda internals; upstream files also omit copyright headers.
# ruff: noqa: CPY001, SLF001

from __future__ import annotations

import math
import re
import weakref
from copy import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.const import STATE_NOT_HOME
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import ADDR_TYPE_EXTERNAL, DOMAIN, HIST_KEEP_COUNT, ICON_DEFAULT_AREA, SIGNAL_DEVICE_NEW
from .util import mac_norm

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import BermudaDataUpdateCoordinator

API_VERSION = 1
MAX_AGE = 180
MAX_ADDRESSES = 32
MAX_IDENTITIES = 256
MAX_ADVERTS = 256
MAX_ID_LENGTH = 128
MAX_NAME_LENGTH = 128
_DATA_KEY = f"{DOMAIN}_external_identity_v1"
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


class ExternalIdentityError(Exception):
    """An external identity request could not be safely accepted."""


class BermudaUnavailable(ExternalIdentityError):  # noqa: N818
    """Bermuda is not currently loaded."""


@dataclass
class _Association:
    first: float
    latest: float


class ExternalIdentityManager:
    """Own the capability for one Bermuda coordinator lifetime."""

    def __init__(self, coordinator: BermudaDataUpdateCoordinator) -> None:
        self.coordinator = coordinator
        self.handles: dict[str, ExternalIdentity] = {}
        self.addresses: dict[str, ExternalIdentity] = {}
        self.owned_identities: dict[str, str] = {}
        self.active = True

    @callback
    def close(self) -> None:
        """Fence all handles and clear their evidence on Bermuda unload."""
        self.active = False
        for handle in list(self.handles.values()):
            handle._remove()

    def register(self, owner: ConfigEntry, unique_id: str, name: str) -> ExternalIdentity:
        """Create a persistent logical identity for this owner lifetime."""
        if not self.active:
            raise BermudaUnavailable
        if (
            not isinstance(unique_id, str)
            or not 1 <= len(unique_id) <= MAX_ID_LENGTH
            or not isinstance(name, str)
            or not 1 <= len(name) <= MAX_NAME_LENGTH
            or ":" in owner.domain
            or ":" in unique_id
        ):
            message = "Invalid identity"
            raise ExternalIdentityError(message)
        identity = f"external:{owner.domain}:{unique_id}".lower()
        if identity in self.handles or (identity in self.coordinator.devices and identity not in self.owned_identities):
            message = "Identity already exists"
            raise ExternalIdentityError(message)
        if identity in self.owned_identities and self.owned_identities[identity] != owner.entry_id:
            message = "Identity belongs to another owner"
            raise ExternalIdentityError(message)
        if identity not in self.owned_identities and len(self.owned_identities) >= MAX_IDENTITIES:
            message = "Identity limit reached"
            raise ExternalIdentityError(message)
        handle = ExternalIdentity(self, owner, identity, name)
        self.handles[identity] = handle
        self.owned_identities[identity] = owner.entry_id
        async_dispatcher_send(self.coordinator.hass, SIGNAL_DEVICE_NEW, identity)
        handle_ref = weakref.ref(handle)

        @callback
        def owner_unloaded() -> None:
            if existing := handle_ref():
                existing._remove()

        owner.async_on_unload(owner_unloaded)
        return handle

    def update(self) -> None:
        """Rebuild evidence from currently associated source devices."""
        for handle in tuple(self.handles.values()):
            handle._update()


class ExternalIdentity:
    """An owner-scoped handle, invalid after removal or Bermuda unload."""

    def __init__(self, manager: ExternalIdentityManager, owner: ConfigEntry, identity: str, name: str) -> None:
        self._manager = manager
        self._owner = owner
        self.identity_address = identity
        self._associations: dict[str, _Association] = {}
        self._watermarks: dict[str, float] = {}
        self._cancel_timer = None
        self._active = True
        self._device = manager.coordinator._get_or_create_device(identity)
        self._device.address_type = ADDR_TYPE_EXTERNAL
        self._device.name_devreg = name
        self._device.make_name()
        self._device.create_sensor = True

    def _check(self) -> None:
        if not self._active or not self._manager.active:
            message = "Identity handle is no longer active"
            raise BermudaUnavailable(message)

    async def async_observe_address(self, address: str, observed_at: float) -> None:
        """Associate an address using the original Bluetooth observation time."""
        self._check()
        if not isinstance(address, str) or not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
            message = "Invalid observation"
            raise ExternalIdentityError(message)
        address = mac_norm(address)
        now = monotonic_time_coarse()
        if not _MAC.fullmatch(address) or not math.isfinite(observed_at):
            message = "Invalid observation"
            raise ExternalIdentityError(message)
        if observed_at > now or observed_at < now - MAX_AGE:
            message = "Observation is outside the current window"
            raise ExternalIdentityError(message)
        self._update()
        association = self._associations.get(address)
        self._watermarks = {key: value for key, value in self._watermarks.items() if value > now - MAX_AGE}
        if observed_at < self._watermarks.get(address, float("-inf")):
            message = "Observation timestamp regressed"
            raise ExternalIdentityError(message)
        claimant = self._manager.addresses.get(address)
        if claimant is not None and claimant is not self:
            claimant._update()
            if self._manager.addresses.get(address) is not None:
                message = "Address already associated"
                raise ExternalIdentityError(message)
        if association is None:
            if len(self._associations) >= MAX_ADDRESSES or (
                address not in self._watermarks and len(self._watermarks) >= MAX_ADDRESSES
            ):
                message = "Address limit reached"
                raise ExternalIdentityError(message)
            association = self._associations[address] = _Association(observed_at, observed_at)
            self._manager.addresses[address] = self
        association.latest = observed_at
        self._watermarks[address] = observed_at
        self._update()
        self._schedule_expiry()

    async def async_remove_address(self, address: str) -> None:
        """Immediately sever all evidence for an address."""
        self._check()
        if not isinstance(address, str) or not _MAC.fullmatch(mac_norm(address)):
            message = "Invalid address"
            raise ExternalIdentityError(message)
        self._drop(mac_norm(address))
        self._update()
        self._schedule_expiry()

    async def async_remove(self) -> None:
        """Remove this identity and its ephemeral associations."""
        self._check()
        self._remove()

    @callback
    def _drop(self, address: str) -> None:
        self._associations.pop(address, None)
        if self._manager.addresses.get(address) is self:
            del self._manager.addresses[address]

    @callback
    def _remove(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        for address in tuple(self._associations):
            self._drop(address)
        self._clear_evidence()
        self._device.create_sensor = False
        self._manager.handles.pop(self.identity_address, None)
        self._manager.coordinator.async_update_listeners()

    def _clear_evidence(self) -> None:
        self._device.adverts.clear()
        self._device.metadevice_sources.clear()
        self._device.last_seen = 0
        self._device.apply_scanner_selection(None)
        self._device.area_last_seen = None
        self._device.area_last_seen_id = None
        self._device.area_last_seen_icon = ICON_DEFAULT_AREA
        self._device.calculate_data()

    @callback
    def _update(self) -> None:
        """
        Copy only new stamped observations, never a source's smoothed history.

        Local USB scanners with synthesized timestamps are unsupported by API v1.
        """
        if not self._active:
            return
        now = monotonic_time_coarse()
        for address, association in tuple(self._associations.items()):
            if association.latest <= now - MAX_AGE:
                self._drop(address)
        old_winner = self._device.area_advert
        previous = self._device.adverts
        retained = {}
        self._device.metadevice_sources.clear()
        self._device.last_seen = 0
        for address, association in self._associations.items():
            source = self._manager.coordinator.devices.get(address)
            if source is None:
                continue
            self._device.metadevice_sources.append(address)
            for key, advert in source.adverts.items():
                if len(retained) >= MAX_ADVERTS:
                    break
                if (
                    not advert.scanner_sends_stamps
                    or not isinstance(advert.stamp, (int, float))
                    or not math.isfinite(advert.stamp)
                    or not association.first <= advert.stamp <= now
                    or advert.stamp <= now - MAX_AGE
                    or not isinstance(advert.rssi, (int, float))
                    or not math.isfinite(advert.rssi)
                ):
                    continue
                clone = previous.get(key)
                if clone is not None and advert.stamp < clone.stamp:
                    continue
                if clone is None:
                    clone = copy(advert)
                    clone._device = self._device
                    clone.ref_power = self._device.ref_power
                    for field in vars(clone):
                        if field.startswith("hist_") or field in {
                            "local_name",
                            "manufacturer_data",
                            "service_data",
                            "service_uuids",
                        }:
                            setattr(clone, field, [])
                    clone.stamp = 0
                    clone.new_stamp = None
                    clone.rssi_distance = None
                    clone.rssi_distance_raw = 0
                # Reconnect/area edits may replace the scanner object. Keep its
                # current identity and area while retaining only our own history.
                clone.apply_new_scanner(advert.scanner_device)
                if advert.stamp > clone.stamp:
                    clone.rssi = advert.rssi
                    clone.tx_power = advert.tx_power
                    clone.stamp = advert.stamp
                    clone.new_stamp = advert.stamp
                    clone.hist_stamp.insert(0, advert.stamp)
                    clone.hist_rssi.insert(0, advert.rssi)
                    clone._update_raw_distance()
                    del clone.hist_stamp[HIST_KEEP_COUNT:]
                    del clone.hist_rssi[HIST_KEEP_COUNT:]
                    del clone.hist_distance[HIST_KEEP_COUNT:]
                retained[key] = clone
                self._device.last_seen = max(self._device.last_seen, clone.stamp)
        self._device.adverts = retained
        if old_winner is not None and all(old_winner is not advert for advert in retained.values()):
            self._device.apply_scanner_selection(None)
            self._device.area_last_seen = None
            self._device.area_last_seen_id = None
            self._device.area_last_seen_icon = ICON_DEFAULT_AREA
        if not retained:
            self._device.area_last_seen = None
            self._device.area_last_seen_id = None
            self._device.area_last_seen_icon = ICON_DEFAULT_AREA
            self._device.zone = STATE_NOT_HOME
        if set(previous) != set(retained):
            self._manager.coordinator.async_update_listeners()

    @callback
    def _expire(self, _now) -> None:
        self._cancel_timer = None
        self._update()
        self._schedule_expiry()

    def _schedule_expiry(self) -> None:
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None
        if self._active and self._associations:
            delay = max(0, min(item.latest for item in self._associations.values()) + MAX_AGE - monotonic_time_coarse())
            self._cancel_timer = async_call_later(self._manager.coordinator.hass, delay, self._expire)


@callback
def async_is_available(hass: HomeAssistant) -> bool:
    """Return whether a loaded Bermuda coordinator offers this API."""
    manager = hass.data.get(_DATA_KEY)
    return isinstance(manager, ExternalIdentityManager) and manager.active


async def async_register_identity(
    hass: HomeAssistant, owner: ConfigEntry, unique_id: str, name: str
) -> ExternalIdentity:
    """Register an identity while Bermuda is loaded; its owner controls its lifetime."""
    manager = hass.data.get(_DATA_KEY)
    if not isinstance(manager, ExternalIdentityManager) or not manager.active:
        message = "Bermuda external identity API is unavailable"
        raise BermudaUnavailable(message)
    return manager.register(owner, unique_id, name)
