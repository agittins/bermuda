"""
Custom integration to integrate Bermuda BLE Triangulation with Home Assistant.

For more details about this integration, please refer to
https://github.com/agittins/bermuda
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config
from homeassistant.core import HomeAssistant
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import BermudaApiClient
from .const import CONF_PASSWORD
from .const import CONF_USERNAME
from .const import DOMAIN
from .const import PLATFORMS
from .const import STARTUP_MESSAGE

SCAN_INTERVAL = timedelta(seconds=30)

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup(
    hass: HomeAssistant, config: Config
):  # pylint: disable=unused-argument;
    """Set up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)

    session = async_get_clientsession(hass)
    client = BermudaApiClient(username, password, session)

    coordinator = BermudaDataUpdateCoordinator(hass, client=client)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = coordinator

    for platform in PLATFORMS:
        if entry.options.get(platform, True):
            coordinator.platforms.append(platform)
            hass.async_add_job(
                hass.config_entries.async_forward_entry_setup(entry, platform)
            )

    entry.add_update_listener(async_reload_entry)
    return True


class BermudaDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Bluetooth component."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: BermudaApiClient,
    ) -> None:
        """Initialize."""
        self.api = client
        self.platforms = []
        self.devices = []

        hass.services.async_register(
            DOMAIN,
            "dump_beacons",
            self.service_dump_beacons,
            None,
            SupportsResponse.ONLY,
        )

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    """Some algorithms to keep in mind:

    https://en.wikipedia.org/wiki/Triangle_inequality
    - with distance to two rx nodes, we can apply min and max bounds
      on the distance between them (less than the sum, more than the
      difference). This could allow us to iterively approximate toward
      the rx layout, esp as devices move between (and right up to) rx.
      - bear in mind that rssi errors are typically attenuation-only.
        This means that we should favour *minimum* distances as being
        more accurate, both when weighting measurements from distant
        receivers, and when whittling down a max distance between
        receivers (but beware of the min since that uses differences)

    https://mdpi-res.com/d_attachment/applsci/applsci-10-02003/article_deploy/applsci-10-02003.pdf?version=1584265508
    - lots of good info and ideas.

    TODO / IDEAS:
    - when we get to establishing a fix, we can apply a path-loss factor to
      a calculated vector based on previously measured losses on that path.
      We could perhaps also fine-tune that with real-time measurements from
      fixed beacons to compensate for environmental factors.
    - An "obstruction map" or "radio map" could provide field strength estimates
      at given locations, and/or hint at attenuation by counting "wall crossings"
      for a given vector/path.

    """

    async def _rssi_to_metres(self, rssi):
        """Convert instant rssi value to a distance in metres

        Based on the information from
        https://mdpi-res.com/d_attachment/applsci/applsci-10-02003/article_deploy/applsci-10-02003.pdf?version=1584265508

        attenuation:    a factor representing environmental attenuation
                        along the path. Will vary by humidity, terrain etc.
        ref_power:      db. measured rssi when at 1m distance from rx. The will
                        be affected by both receiver sensitivity and transmitter
                        calibration, antenna design and orientation etc.

        TODO: the ref_power and attenuation figures can/should probably be mapped
            against each receiver and transmitter for variances. We could also fine-
            tune the attenuation in real time based on changing values coming from
            known-fixed beacons (eg thermometers, window sensors etc)
        """
        attenuation = 3.0  # Will range depending on environmental factors
        ref_power = -55.0  # db reference measured at 1.0m

        distance = 10 ** ((ref_power - rssi) / (10 * attenuation))
        return distance

    async def _async_update_data(self):
        """Update data on known devices."""
        beacon_details = []
        # Fixme/todo: We re-create the device list from scratch. This means
        # we lose devices as they are expunged from the discovery lists.
        # Instead we might want to update our list incrementally to keep
        # more history and/or not over-write extra info we've calculated!

        # We only trawl through this as there's no API I could see for
        # acessing the scanner devices except by address. One probably
        # could access the data structs directly but that would be rude.
        for service_info in bluetooth.async_discovered_service_info(self.hass, False):
            # Not all discovered service info entries have corresponding
            # scanner entries, which seems a little odd.
            # redict.append(service_info.address)
            device = {
                "address": service_info.address,
                "name": service_info.device.name,
                "local_name": service_info.advertisement.local_name,
                "connectable": service_info.connectable,
            }
            device["scanners"] = []

            for discovered in bluetooth.async_scanner_devices_by_address(
                self.hass, service_info.address, False
            ):
                adverts = []
                for sd in discovered.advertisement.service_data:
                    adverts.append(
                        {
                            "advert": sd,
                            "bytes": discovered.advertisement.service_data[sd].hex(),
                        }
                    )

                device["scanners"].append(
                    {
                        "scanner_name": discovered.scanner.name,
                        "scanner_address": discovered.scanner.adapter,
                        "rssi": discovered.advertisement.rssi,
                        "rssi_distance": await self._rssi_to_metres(
                            discovered.advertisement.rssi
                        ),
                        "adverts": adverts,
                    }
                )
            beacon_details.append(device)
        self.devices = beacon_details
        # try:
        #    return await self.api.async_get_data()
        # except Exception as exception:
        #    raise UpdateFailed() from exception

    async def service_dump_beacons(self, call):  # pylint: disable=unused-argument;
        """Return a dump of beacon advertisements by receiver"""
        return {"items": self.devices}


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, platform)
                for platform in PLATFORMS
                if platform in coordinator.platforms
            ]
        )
    )
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
