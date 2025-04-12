"""BermudaEntity class."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.bluetooth import MONOTONIC_TIME
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    ATTRIBUTION,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
)

if TYPE_CHECKING:
    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator
    # from . import BermudaDevice


class BermudaEntity(CoordinatorEntity):
    """
    Co-ordinator for Bermuda data.

    Gathers the device infor for receivers and transmitters, calculates
    distances etc.
    """

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.config_entry = config_entry
        self.address = address
        self._device = coordinator.devices[address]
        self._lastname = self._device.name  # So we can track when we get a new name
        self.area_reg = ar.async_get(coordinator.hass)
        self.devreg = dr.async_get(coordinator.hass)
        self.devreg_init_done = False

        self.bermuda_update_interval = config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        self.bermuda_last_state: Any = 0
        self.bermuda_last_stamp: float = 0

    def _cached_ratelimit(self, statevalue: Any, fast_falling=True, fast_rising=False, interval=None):
        """
        Uses the CONF_UPDATE_INTERVAL and other logic to return either the given statevalue
        or an older, cached value. Helps to reduce excess sensor churn without compromising latency.

        Mostly suitable for MEASUREMENTS, but should work with strings, too.
        If interval is specified the cache will use that (in seconds), otherwise the deafult is
        the CONF_UPPDATE_INTERVAL (typically suitable for fast-close slow-far sensors)
        """
        if interval is not None:
            self.bermuda_update_interval = interval

        nowstamp = MONOTONIC_TIME()
        if (
            (self.bermuda_last_stamp < nowstamp - self.bermuda_update_interval)  # Cache is stale
            or (self._device.ref_power_changed > nowstamp + 2)  # ref power changed in last 2sec
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
            # Send the cached value, don't update cache
            return self.bermuda_last_state

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
                self.devreg.async_update_device(self.device_entry.id, name=self._device.name)
        self.async_write_ha_state()

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._device.unique_id

    @property
    def device_info(self):
        """
        Implementing this creates an entry in the device registry.

        This is responsible for linking Bermuda entities to devices,
        and also for matching up to device entries for other integrations.
        """
        # Match up our entity with any existing device entries.
        # For scanners we use ethernet MAC, which looks like they are
        # normally stored lowercased, otherwise we use our btmac, which
        # seem to be stored uppercased.
        # existing_device_id = None
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
            # model = f"IRK: {self._device.address.lower()[:4]}"
            # We look up and use the device from the registry so we get
            # the private_ble_device device congealment!
            # The "connection" is actually being used as the "identifiers" tuple
            # here.
            # dr_device = self.devreg.async_get_device(connection)
            # if dr_device is not None:
            #    existing_device_id = dr_device.id
            domain_name = DOMAIN_PRIVATE_BLE_DEVICE
        else:
            connections = {(dr.CONNECTION_BLUETOOTH, self._device.address.upper())}
            # No need to set model, since MAC address will be shown via connection.
            # model = f"Bermuda: {self._device.address.lower()}"

        device_info = {
            "identifiers": {(domain_name, self._device.unique_id)},
            "connections": connections,
            "name": self._device.name,
        }
        if model is not None:
            device_info["model"] = model
        # if existing_device_id is not None:
        #    device_info['id'] = existing_device_id

        return device_info

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return {
            "attribution": ATTRIBUTION,
            "id": str(self.coordinator.data.get("id")),
            "integration": DOMAIN,
        }


class BermudaGlobalEntity(CoordinatorEntity):
    """Holds all Bermuda global data under one entity type/device."""

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        config_entry: BermudaConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
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

    def _cached_ratelimit(self, statevalue: Any, interval: int | None = None):
        """A simple way to rate-limit sensor updates."""
        if interval is not None:
            self._cache_ratelimit_interval = interval
        nowstamp = MONOTONIC_TIME()

        if nowstamp > self._cache_ratelimit_stamp + self._cache_ratelimit_interval:
            self._cache_ratelimit_stamp = nowstamp
            self._cache_ratelimit_value = statevalue
            return statevalue
        else:
            return self._cache_ratelimit_value

    @property
    def device_info(self):
        """Implementing this creates an entry in the device registry."""
        return {
            "identifiers": {(DOMAIN, "BERMUDA_GLOBAL")},
            "name": "Bermuda Global",
        }
