"""BermudaEntity class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
)
from .coordinator import BermudaDataUpdateCoordinator

if TYPE_CHECKING:
    from . import BermudaConfigEntry


class BermudaEntity(CoordinatorEntity[BermudaDataUpdateCoordinator]):
    """
    Co-ordinator for Bermuda data.

    Gathers the device info for receivers and transmitters, calculates
    distances etc.
    """

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self.address = address
        self._device = coordinator.devices[address]
        self._lastname = self._device.name  # So we can track when we get a new name
        self.ar = ar.async_get(coordinator.hass)
        self.dr = dr.async_get(coordinator.hass)
        self.devreg_init_done = False

        self.bermuda_update_interval = config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        self.bermuda_last_state: Any = 0
        self.bermuda_last_stamp: float = 0

    def _cached_ratelimit[T](
        self, statevalue: T, *, fast_falling: bool = True, fast_rising: bool = False, interval: int | None = None
    ) -> T:
        """
        Uses the CONF_UPDATE_INTERVAL and other logic to return either the given statevalue
        or an older, cached value. Helps to reduce excess sensor churn without compromising latency.

        Mostly suitable for MEASUREMENTS, but should work with strings, too.
        If interval is specified the cache will use that (in seconds), otherwise the default is
        the CONF_UPPDATE_INTERVAL (typically suitable for fast-close slow-far sensors)
        """
        # Use the per-call interval if given, without mutating the entity's
        # configured default (a one-off interval must not stick for later calls).
        effective_interval = interval if interval is not None else self.bermuda_update_interval

        nowstamp = monotonic_time_coarse()
        if (
            (self.bermuda_last_stamp < nowstamp - effective_interval)  # Cache is stale
            or (self._device.ref_power_changed > nowstamp - 2)  # ref power changed in last 2sec
            or (self.bermuda_last_state is None)  # Nothing compares to you.
            or (statevalue is None)  # or you.
            or (fast_falling and statevalue < self.bermuda_last_state)  # (like Distance)
            or (fast_rising and statevalue > self.bermuda_last_state)  # (like RSSI)
        ):
            # Publish the new value and update cache
            self.bermuda_last_stamp = nowstamp
            self.bermuda_last_state = statevalue
            return statevalue
        else:
            # Send the cached value, don't update cache.
            # bermuda_last_state is Any (it caches whatever value-type this entity's
            # native_value has been passing in, consistently, across calls).
            return cast("T", self.bermuda_last_state)

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the co-ordinator.

        Any specific things we want to do during an update cycle
        """
        if not self.devreg_init_done and self.device_entry:
            self._device.name_by_user = self.device_entry.name_by_user
            self.devreg_init_done = True
        if self._device.name != self._lastname:
            self._lastname = self._device.name
            if self.device_entry:
                # We have a new name locally, so let's update the device registry.
                self.dr.async_update_device(self.device_entry.id, name=self._device.name)
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID to use for this entity."""
        return self._device.unique_id

    @property
    def device_info(self) -> dr.DeviceInfo:
        """
        Implementing this creates an entry in the device registry.

        This is responsible for linking Bermuda entities to devices,
        and also for matching up to device entries for other integrations.
        """
        # Match up our entity with any existing device entries.
        # For scanners we use ethernet MAC, which looks like they are
        # normally stored lowercased, otherwise we use our btmac, which
        # seem to be stored uppercased.
        domain_name = DOMAIN
        model = None

        if self._device.is_scanner:
            # ESPHome proxies prior to 2025.3 report their WIFI MAC for any address,
            # except for received iBeacons.
            connections = {
                # Keeps the distance_to entities the same across pre/post 2025.3
                (dr.CONNECTION_NETWORK_MAC, (self._device.address_wifi_mac or self._device.address).lower()),
                # Ensures we can also match the Bluetooth integration entities.
                (dr.CONNECTION_BLUETOOTH, (self._device.address_ble_mac or self._device.address).upper()),
            }
        elif self._device.address_type == ADDR_TYPE_IBEACON:
            # ibeacon doesn't (yet) actually set a "connection", but
            # this "matches" what it stores for identifier.
            connections = {("ibeacon", self._device.address.lower())}
            model = f"iBeacon: {self._device.address.lower()}"
        elif self._device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
            # Private BLE Device integration doesn't specify "connection" tuples,
            # so we use what it defines for the "identifier" instead.
            connections = {("private_ble_device", self._device.address.lower())}
            # We don't set the model since the Private BLE integration should have
            # already named it nicely.
            domain_name = DOMAIN_PRIVATE_BLE_DEVICE
        else:
            connections = {(dr.CONNECTION_BLUETOOTH, self._device.address.upper())}
            # No need to set model, since MAC address will be shown via connection.

        device_info = dr.DeviceInfo(
            # BermudaDevice.unique_id is set (to a str) in __init__ and only ever
            # reassigned to another str (see BermudaScannerDeviceMixin); it is typed
            # Optional only to mirror Entity.unique_id's own signature.
            identifiers={(domain_name, cast("str", self._device.unique_id))},
            connections=connections,
            name=self._device.name,
        )
        if model is not None:
            device_info["model"] = model

        return device_info


class BermudaGlobalEntity(CoordinatorEntity[BermudaDataUpdateCoordinator]):
    """Holds all Bermuda global data under one entity type/device."""

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._cache_ratelimit_value = None
        self._cache_ratelimit_stamp: float = 0
        self._cache_ratelimit_interval = 60

    @callback
    def _handle_coordinator_update(self) -> None:
        """
        Handle updated data from the co-ordinator.

        (we don't need to implement this, but if we want to do anything special we can)
        """
        self.async_write_ha_state()

    def _cached_ratelimit(self, statevalue: Any, interval: int | None = None) -> Any:
        """A simple way to rate-limit sensor updates."""
        # Per-call interval without mutating the entity's default.
        effective_interval = interval if interval is not None else self._cache_ratelimit_interval
        nowstamp = monotonic_time_coarse()

        if nowstamp > self._cache_ratelimit_stamp + effective_interval:
            self._cache_ratelimit_stamp = nowstamp
            self._cache_ratelimit_value = statevalue
            return statevalue
        else:
            return self._cache_ratelimit_value

    @property
    def device_info(self) -> dr.DeviceInfo:
        """Implementing this creates an entry in the device registry."""
        return dr.DeviceInfo(
            identifiers={(DOMAIN, "BERMUDA_GLOBAL")},
            name="Bermuda Global",
            manufacturer="Bermuda",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
