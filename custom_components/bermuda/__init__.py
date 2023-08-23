"""
Custom integration to integrate Bermuda BLE Triangulation with Home Assistant.

For more details about this integration, please refer to
https://github.com/agittins/bermuda
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Final

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothScannerDevice
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Config
from homeassistant.core import HomeAssistant
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import area_registry
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify
from homeassistant.util.dt import monotonic_time_coarse, now

from .const import CONF_PASSWORD
from .const import CONF_USERNAME
from .const import DOMAIN
from .const import PLATFORMS
from .const import STARTUP_MESSAGE

from .entity import BermudaEntity

SCAN_INTERVAL = timedelta(seconds=10)

MONOTONIC_TIME: Final = monotonic_time_coarse

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup(
    hass: HomeAssistant, config: Config
):  # pylint: disable=unused-argument;
    """Setting up this integration using YAML is not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration using UI."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(STARTUP_MESSAGE)

    #username = entry.data.get(CONF_USERNAME)
    #password = entry.data.get(CONF_PASSWORD)

    coordinator = BermudaDataUpdateCoordinator(hass)
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

def rssi_to_metres(rssi):
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


class BermudaDeviceScanner(dict):
    """Represents details from a scanner relevant to a specific device

    A BermudaDevice will contain 0 or more of these depending on whether
    it has been "seen" by that scanner.

    Note that details on a scanner itself are BermudaDevice instances
    in their own right.
    """
    def __init__(
        self,
        device_address: str,
        scandata: BluetoothScannerDevice,
        area_id: str
        ):
        self.name = scandata.scanner.name
        self.area_id = area_id
        self.adapter = scandata.scanner.adapter
        self.source = scandata.scanner.source
        self.stamp = scandata.scanner._discovered_device_timestamps[device_address]
        self.rssi = scandata.advertisement.rssi
        self.rssi_distance = rssi_to_metres(self.rssi)
        self.adverts = scandata.advertisement.service_data.items()

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices"""
        out = {}
        for ( var, val) in vars(self).items():
            if var == 'adverts':
                val = {}
                for ad, thebytes in self.adverts:
                    val[ad] = thebytes.hex()
            out[var] = val
        return out



class BermudaDevice(dict):
    """This class is to represent a single bluetooth "device" tracked by Bermuda.

    "device" in this context means a bluetooth receiver like an ESPHome
    running bluetooth_proxy or a bluetooth transmitter such as a beacon,
    a thermometer, watch or phone etc.

    We're not storing this as an Entity because we don't want all devices to
    become entities in homeassistant, since there might be a _lot_ of them.
    """
    def __init__(self):
        """Initial (empty) data"""
        self.address = None
        self.unique_id = None # mac address formatted.
        self.name = None
        self.local_name = None
        self.prefname = None # "preferred" name - ideally local_name
        self.area_id = None
        self.area_name = None
        self.area_distance = None # how far this dev is from that area
        self.location = None # home or not_home
        self.manufacturer = None
        self.connectable = False
        self.is_scanner = False
        self.entry_id = None # used for scanner devices
        self.send_tracker_see = False # Create/update device_tracker entity
        self.create_sensor = False # Create/update a sensor for this device
        self.last_seen = 0 # stamp from most recent scanner spotting
        self.scanners: dict[str, BermudaDeviceScanner] = {}

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices"""
        out = {}
        for ( var, val) in vars(self).items():
            if var == 'scanners':
                scanout = {}
                for address, scanner in self.scanners.items():
                    scanout[address] = scanner.to_dict()
                val = scanout
            out[var] = val
        return out


class BermudaDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Bluetooth component.

    Since we are not actually using an external API and only computing local
    data already gathered by the bluetooth integration, the update process is
    very cheap, and the processing process (currently) rather cheap.

    Future work / algo's etc to keep in mind:

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

    def __init__(
        self,
        hass: HomeAssistant,

    ) -> None:
        """Initialize."""
        self.platforms = []
        self.devices: dict[str, BermudaDevice] = {}
        self.created_entities: set[BermudaEntity] = set()

        self.ar = area_registry.async_get(hass)

        # TODO: These settings are to be moved into the config flow
        self.max_area_radius = 3.0 # maximum distance to consider "in the area"
        self.timeout_not_home = 60 # seconds to wait before declaring "not_home"



        hass.services.async_register(
            DOMAIN,
            "dump_devices",
            self.service_dump_devices,
            None,
            SupportsResponse.ONLY,
        )

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)


    async def _async_update_data(self):
        """Update data on known devices.

        This works only with local data, so should be cheap to run
        (no network requests made etc).

        """

        for service_info in bluetooth.async_discovered_service_info(self.hass, False):
            # Note that some of these entries are restored from storage,
            # so we won't necessarily find (immediately, or perhaps ever)
            # scanner entries for any given device.

            # Get/Create a device entry
            if service_info.address not in self.devices:
                #Initialise an empty device
                self.devices[service_info.address] = BermudaDevice()
            device: BermudaDevice = self.devices[service_info.address]

            # We probably don't need to do all of this every time, but we
            # want to catch any changes, eg when the system learns the local
            # name etc.
            device.address = service_info.address
            device.unique_id = format_mac(service_info.address)
            device.name = service_info.device.name
            device.local_name = service_info.advertisement.local_name
            device.manufacturer = service_info.manufacturer
            device.connectable = service_info.connectable

            # Try to make a nice name for prefname.
            # TODO: Add support for user-defined name, especially since the
            #   device_tracker entry can only be renamed using the editor.
            if service_info.advertisement.local_name is not None:
                device.prefname = service_info.advertisement.local_name
            elif service_info.device.name is not None:
                device.prefname = service_info.device.name
            else:
                # we tried. Fall back to boring...
                device.prefname = 'bermuda_' + slugify(service_info.address)

            # Work through the scanner entries...
            for discovered in bluetooth.async_scanner_devices_by_address(
                self.hass, service_info.address, False
            ):

                if discovered.scanner.source not in self.devices:
                    self._refresh_scanners()

                #FIXME: Find a method or request one be added for this
                stamps = discovered.scanner._discovered_device_timestamps # pylint: disable=protected-access
                scanner_stamp = stamps[service_info.address]
                if device.last_seen < scanner_stamp:
                    device.last_seen = scanner_stamp

                # Just replace the scanner entries outright...
                device.scanners[discovered.scanner.source] = BermudaDeviceScanner(
                    device.address,
                    discovered,
                    self.devices[discovered.scanner.source].area_id
                )

            #FIXME: This should be configurable...
            if device.address in [
                    "EE:E8:37:9F:6B:54", # infinitime, main watch
                    "C7:B8:C6:B0:27:11", # pinetime, devwatch
                    "A4:C1:38:C8:58:91", # bthome thermo, with reed switch
                ]:
                device.send_tracker_see = True
                device.create_sensor = True

            if device.send_tracker_see:
                # Send a "see" notification to device_tracker
                await self._send_device_tracker_see(device)

        self._refresh_areas_by_min_distance()

        # end of async update

    # async def _create_or_update_sensor(self, device):
    #     if self.async_sensor_add_entities is not None:
    #         #await self.async_sensor_add_entities([BermudaSensor, self.config_entry])
    #         NotImplemented

    async def _send_device_tracker_see(self, device):
        """Send "see" event to the legacy device_tracker integration.

        If the device is not yet in known_devices.yaml it will get added.
        Note that device_tracker can *only* support [home|not_home].
        It does support Zones (not via the service though?), but Zones
        are only EXTERNAL to the home, not the same as "Area"s.

        I'm not implementing device_tracker proper because I don't grok it
        well enough yet. And to be honest this is probably all we need
        since it doesn't support Areas anyway.

        TODO: Allow user to configure what name to use for the device_tracker.
        """

        # Check if the device has been seen recently
        rightnow = MONOTONIC_TIME()
        if rightnow - device.last_seen > self.timeout_not_home:
            location_name = 'not_home'
        else:
            location_name = 'home'

        # If mac is set, dt will:
        #   slugify the hostname (if set) or mac, and use that as the dev_id.
        # Else:
        #   will slugify dev_id
        # So, we will not set mac, but use bermuda_[mac] as dev_id and prefname
        # for host_name.
        await self.hass.services.async_call(
            domain='device_tracker',
            service='see',
            service_data={
                'dev_id': 'bermuda_' + slugify(device.address),
                #'mac': device.address,
                'host_name': device.prefname,
                'location_name': location_name,
            }
        )

    def dt_mono_to_datetime(self, stamp):
        """Given a monotonic timestamp, convert to datetime object"""
        age = MONOTONIC_TIME() - stamp
        return now() - timedelta(seconds=age)



    def _refresh_areas_by_min_distance(self):
        """Set area for ALL devices based on closest beacon"""
        for device in self.devices.values():
            if device.is_scanner is not True:
                self._refresh_area_by_min_distance(device)

    def _refresh_area_by_min_distance(self, device: BermudaDevice):
        """Very basic Area setting by finding closest beacon to a given device"""
        assert device.is_scanner is not True
        closest_scanner: BermudaDeviceScanner = None

        for scanner in device.scanners.values():
            # whittle down to the closest beacon inside max range
            if scanner.rssi_distance < self.max_area_radius: # potential...
                if  closest_scanner is None \
                    or scanner.rssi_distance < closest_scanner.rssi_distance:
                    closest_scanner = scanner
        if closest_scanner is not None:
            # We found a winner
            device.area_id = closest_scanner.area_id
            areas = self.ar.async_get_area(device.area_id).name # which is actually a list.
            if len(areas) == 1:
                device.area_name = areas[0]
            else:
                device.area_name = areas

            device.area_distance = closest_scanner.rssi_distance
        else:
            # Not close to any scanners!
            device.area_id = None
            device.area_name = None
            device.area_distance = None


    def _refresh_scanners(self, address = None):
        """Refresh our local list of scanners (BLE Proxies)"""
        #FIXME: Really? This can't possibly be a sensible nesting of loops.
        for dev_entry in self.hass.data['device_registry'].devices.data.values():
            if len(dev_entry.connections) > 0:
                for dev_connection in dev_entry.connections:
                    if dev_connection[0] == 'mac':
                        if address is None or address == dev_connection[1]:
                            found_address = dev_connection[1]
                            self.devices[found_address] = BermudaDevice()
                            scandev = self.devices[found_address]
                            scandev.address = found_address
                            scandev.area_id = dev_entry.area_id
                            scandev.entry_id = dev_entry.id
                            if dev_entry.name_by_user is not None:
                                scandev.name = dev_entry.name_by_user
                            else:
                                scandev.name = dev_entry.name
                            scandev.is_scanner = True


    async def service_dump_devices(self, call):  # pylint: disable=unused-argument;
        """Return a dump of beacon advertisements by receiver"""
        out = {}
        for address, device in self.devices.items():
            out[address] = device.to_dict()
        return out

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
