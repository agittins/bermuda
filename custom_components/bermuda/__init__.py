"""
Custom integration to integrate Bermuda BLE Trilateration with Home Assistant.

For more details about this integration, please refer to
https://github.com/agittins/bermuda
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timedelta

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import MONOTONIC_TIME
from homeassistant.components.bluetooth import BluetoothScannerDevice
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME
from homeassistant.const import STATE_NOT_HOME
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Config
from homeassistant.core import HomeAssistant
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import area_registry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify
from homeassistant.util.dt import get_age
from homeassistant.util.dt import now

from .const import ADVERT_FRESHTIME
from .const import BEACON_IBEACON_DEVICE
from .const import BEACON_IBEACON_SOURCE
from .const import BEACON_NOT_A_BEACON
from .const import CONF_ATTENUATION
from .const import CONF_DEVICES
from .const import CONF_DEVTRACK_TIMEOUT
from .const import CONF_MAX_RADIUS
from .const import CONF_REF_POWER
from .const import CONF_UPDATE_INTERVAL
from .const import CONFDATA_SCANNERS
from .const import DEFAULT_ATTENUATION
from .const import DEFAULT_DEVTRACK_TIMEOUT
from .const import DEFAULT_MAX_RADIUS
from .const import DEFAULT_REF_POWER
from .const import DEFAULT_UPDATE_INTERVAL
from .const import DOMAIN
from .const import HIST_KEEP_COUNT
from .const import PLATFORMS
from .const import SIGNAL_DEVICE_NEW
from .const import STARTUP_MESSAGE

# from typing import TYPE_CHECKING

# from bthome_ble import BTHomeBluetoothDeviceData

# if TYPE_CHECKING:
#     from bleak.backends.device import BLEDevice

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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

    coordinator = BermudaDataUpdateCoordinator(hass, entry)
    await coordinator.async_refresh()

    if not coordinator.last_update_success:
        raise ConfigEntryNotReady

    hass.data[DOMAIN][entry.entry_id] = coordinator

    for platform in PLATFORMS:
        coordinator.platforms.append(platform)
        hass.async_add_job(
            hass.config_entries.async_forward_entry_setup(entry, platform)
        )

    entry.add_update_listener(async_reload_entry)
    return True


def rssi_to_metres(rssi, ref_power=None, attenuation=None):
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
    if ref_power is None:
        return False
        # ref_power = self.ref_power
    if attenuation is None:
        return False
        # attenuation= self.attenuation

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
        area_id: str,
        options,
    ):
        # I am declaring these just to control their order in the dump,
        # which is a bit silly, I suspect.
        self.name: str = scandata.scanner.name
        self.area_id: str = area_id

        self.stamp: float = 0
        self.hist_stamp = []
        self.rssi: float = None
        self.hist_rssi = []
        self.hist_distance = []
        self.hist_interval = []  # WARNING: This is actually "age of ad when we polled"
        self.stale_update_count = (
            0  # How many times we did an update but no new stamps were found.
        )
        self.tx_power: float = None

        # Just pass the rest on to update...
        self.update(device_address, scandata, area_id, options)

    def update(
        self,
        device_address: str,
        scandata: BluetoothScannerDevice,
        area_id: str,
        options,
    ):
        """Update gets called every time we see a new packet or
        every time we do a polled update.

        This method needs to update all the history and tracking data for this
        device+scanner combination.
        """
        # We over-write pretty much everything, except our locally-preserved stats.
        #
        # In case the scanner has changed it's details since startup though:
        self.name: str = scandata.scanner.name
        self.area_id: str = area_id

        # Only remote scanners log timestamps here (local usb adaptors do not),
        if hasattr(scandata.scanner, "_discovered_device_timestamps"):
            # Found a remote scanner which has timestamp history...
            scanner_sends_stamps = True
            # FIXME: Doesn't appear to be any API to get this otherwise...
            # pylint: disable-next=protected-access
            stamps = scandata.scanner._discovered_device_timestamps

            # In this dict all MAC address keys are upper-cased
            uppermac = device_address.upper()
            if uppermac in stamps:
                if stamps[uppermac] > self.stamp:
                    have_new_stamp = True
                    new_stamp = stamps[uppermac]
                else:
                    # We have no updated advert in this run.
                    have_new_stamp = False
                    self.stale_update_count += 1
            else:
                # This shouldn't happen, as we shouldn't have got a record
                # of this scanner if it hadn't seen this device.
                _LOGGER.error(
                    "Scanner %s has no stamp for %s - very odd.",
                    scandata.scanner.source,
                    device_address,
                )
                have_new_stamp = False
        else:
            # Not a bluetooth_proxy device / remote scanner, but probably a USB Bluetooth adaptor.
            # We don't get advertisement timestamps from bluez, so currently there's no way to
            # reliably include it in our calculations.

            scanner_sends_stamps = False
            # But if the rssi has changed from last time, consider it "new"
            if self.rssi != scandata.advertisement.rssi:
                # Since rssi has changed, we'll consider this "new", but
                # since it could be pretty much any age, make it a multiple
                # of freshtime. This means it can still be useful for home/away
                # detection in device_tracker, but won't factor in to area localisation.
                have_new_stamp = True
                new_stamp = MONOTONIC_TIME() - (ADVERT_FRESHTIME * 4)
            else:
                have_new_stamp = False

        if len(self.hist_stamp) == 0 or have_new_stamp:
            # this is the first entry or a new one...

            self.rssi: float = scandata.advertisement.rssi
            self.hist_rssi.insert(0, self.rssi)
            self.rssi_distance: float = rssi_to_metres(
                self.rssi,
                options.get(CONF_REF_POWER, DEFAULT_REF_POWER),
                options.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
            )
            self.hist_distance.insert(0, self.rssi_distance)

            # Stamp will be faked from above if required.
            if have_new_stamp:
                # Note: this is not actually the interval between adverts,
                # but rather a function of our UPDATE_INTERVAL plus the packet
                # interval. The bluetooth integration does not currently store
                # interval data, only stamps of the most recent packet.
                self.hist_interval.insert(0, new_stamp - self.stamp)

                self.stamp = new_stamp
                self.hist_stamp.insert(0, self.stamp)

        # Safe to update these values regardless of stamps...

        self.adapter: str = scandata.scanner.adapter
        self.source: str = scandata.scanner.source
        if (
            self.tx_power is not None
            and scandata.advertisement.tx_power != self.tx_power
        ):
            # Not really an erorr, we just don't account for this happening -
            # I want to know if it does.
            # AJG 2024-01-11: This does happen. Looks like maybe apple devices?
            # Changing from warning to debug to quiet users' logs.
            _LOGGER.debug(
                "Device changed TX-POWER! That was unexpected: %s %sdB",
                device_address,
                scandata.advertisement.tx_power,
            )
        self.tx_power: float = scandata.advertisement.tx_power
        self.adverts: dict[str, bytes] = scandata.advertisement.service_data.items()
        self.scanner_sends_stamps = scanner_sends_stamps
        self.options = options

        # Trim our history lists
        for histlist in (
            self.hist_distance,
            self.hist_interval,
            self.hist_rssi,
            self.hist_stamp,
        ):
            del histlist[HIST_KEEP_COUNT:]

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices"""
        out = {}
        for var, val in vars(self).items():
            if var == "adverts":
                val = {}
                for uuid, thebytes in self.adverts:
                    val[uuid] = thebytes.hex()
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

    def __init__(self, address, options):
        """Initial (empty) data"""
        self.name: str = None
        self.local_name: str = None
        self.prefname: str = None  # "preferred" name - ideally local_name
        self.address: str = address
        self.options = options
        self.unique_id: str = None  # mac address formatted.
        self.mac_is_random: bool = False
        self.area_id: str = None
        self.area_name: str = None
        self.area_distance: float = None  # how far this dev is from that area
        self.area_rssi: float = None  # rssi from closest scanner
        self.area_scanner: str = None  # name of closest scanner
        self.zone: str = STATE_UNAVAILABLE  # STATE_HOME or STATE_NOT_HOME
        self.manufacturer: str = None
        self.connectable: bool = False
        self.is_scanner: bool = False
        self.beacon_type: bool = BEACON_NOT_A_BEACON
        self.beacon_sources = (
            []
        )  # list of MAC addresses that have advertised this beacon
        self.beacon_unique_id: str = (
            None  # combined uuid_major_minor for *really* unique id
        )
        self.beacon_uuid: str = None
        self.beacon_major: str = None
        self.beacon_minor: str = None
        self.beacon_power: float = None

        self.entry_id: str = None  # used for scanner devices
        self.create_sensor: bool = False  # Create/update a sensor for this device
        self.create_sensor_done: bool = False  # Sensor should now exist
        self.create_tracker_done: bool = False  # device_tracker should now exist
        self.last_seen: float = (
            0  # stamp from most recent scanner spotting. MONOTONIC_TIME
        )
        self.scanners: dict[str, BermudaDeviceScanner] = {}

    def add_scanner(
        self, scanner_device: BermudaDevice, discoveryinfo: BluetoothScannerDevice
    ):
        """Add/Replace a scanner entry on this device, indicating a received advertisement

        This gets called every time a scanner is deemed to have received an advert for
        this device.

        """
        if format_mac(scanner_device.address) in self.scanners:
            # Device already exists, update it
            self.scanners[format_mac(scanner_device.address)].update(
                self.address,
                discoveryinfo,  # the entire BluetoothScannerDevice struct
                scanner_device.area_id,
                self.options,
            )
        else:
            self.scanners[format_mac(scanner_device.address)] = BermudaDeviceScanner(
                self.address,
                discoveryinfo,  # the entire BluetoothScannerDevice struct
                scanner_device.area_id,
                self.options,
            )
        device_scanner = self.scanners[format_mac(scanner_device.address)]
        # Let's see if we should update our last_seen based on this...
        if self.last_seen < device_scanner.stamp:
            self.last_seen = device_scanner.stamp

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices"""
        out = {}
        for var, val in vars(self).items():
            if var == "scanners":
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
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        # self.config_entry = entry
        self.platforms = []

        self.config_entry = entry

        interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=interval)
        )

        # First time around we freshen the restored scanner info by
        # forcing a scan of the captured info.
        self._do_full_scanner_init = True

        self.options = {}
        if hasattr(entry, "options"):
            # Firstly, on some calls (specifically during reload after settings changes)
            # we seem to get called with a non-existant config_entry.
            # Anyway... if we DO have one, convert it to a plain dict so we can
            # serialise it properly when it goes into the device and scanner classes.
            for key, val in entry.options.items():
                if key in (
                    CONF_ATTENUATION,
                    CONF_DEVICES,
                    CONF_DEVTRACK_TIMEOUT,
                    CONF_MAX_RADIUS,
                    CONF_REF_POWER,
                ):
                    self.options[key] = val

        self.devices: dict[str, BermudaDevice] = {}
        # self.updaters: dict[str, BermudaPBDUCoordinator] = {}

        self.area_reg = area_registry.async_get(hass)

        # Restore the scanners saved in config entry data. We maintain
        # a list of known scanners so we can
        # restore the sensor states even if we don't have a full set of
        # scanner receipts in the discovery data.
        self.scanner_list = []
        if hasattr(entry, "data"):
            for address, saved in entry.data.get(CONFDATA_SCANNERS, {}).items():
                scanner = self._get_or_create_device(address)
                for key, value in saved.items():
                    setattr(scanner, key, value)
                self.scanner_list.append(address)

        hass.services.async_register(
            DOMAIN,
            "dump_devices",
            self.service_dump_devices,
            vol.Schema({vol.Optional("addresses"): cv.string}),
            SupportsResponse.ONLY,
        )

    def sensor_created(self, address):
        """Allows sensor platform to report back that sensors have been set up"""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_sensor_done = True
            _LOGGER.debug("Sensor confirmed created for %s", address)
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")

    def device_tracker_created(self, address):
        """Allows device_tracker platform to report back that sensors have been set up"""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_tracker_done = True
            _LOGGER.debug("Device_tracker confirmed created for %s", address)
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")

    def _get_device(self, address: str) -> BermudaDevice:
        """Search for a device entry based on mac address"""
        mac = format_mac(address)
        # format_mac tries to return a lower-cased, colon-separated mac address.
        # failing that, it returns the original unaltered.
        if mac in self.devices:
            return self.devices[mac]
        return None

    def _get_or_create_device(self, address: str) -> BermudaDevice:
        device = self._get_device(address)
        if device is None:
            mac = format_mac(address)
            self.devices[mac] = device = BermudaDevice(
                address=mac, options=self.options
            )
            device.address = mac
            device.unique_id = mac
        return device

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
            device = self._get_or_create_device(service_info.address)

            # random mac addresses have 0b11000000 in the MSB. Endianness shenanigans
            # ensue. I think if we & match on 0x0C in the first _byte_ of the address, that's
            # what we want. I think. PR welcome!
            device.mac_is_random = int(device.address[1:2], 16) & 0x0C == 0x0C

            # Check if it's broadcasting an Apple Inc manufacturing data (ID: 0x004C)
            for (
                company_code,
                man_data,
            ) in service_info.advertisement.manufacturer_data.items():
                # if company_code == 0x00E0:  # 224 Google
                #     _LOGGER.debug(
                #         "Found Google Device: %s %s", device.address, man_data.hex()
                #     )
                if company_code == 0x004C:  # 76 Apple Inc
                    # _LOGGER.debug(
                    #     "Found Apple Manufacturer data: %s %s",
                    #     device.address,
                    #     man_data.hex(),
                    # )
                    if man_data[:2] == b"\x02\x15":  # 0x0215:  # iBeacon packet
                        # iBeacon / UUID Support

                        # We provide simplistic support for iBeacons. The
                        # initial/primary use-case is the companion app
                        # for Android phones. We are working with these
                        # assumptions to start with:
                        # - UUID, Major and Minor are static
                        # - MAC address may or may not be static
                        # - We treat a given UUID/Major/Minor combination
                        #   as being unique. If a device sends multiple
                        #   ID's we treat it as *wanting* to be seen as multiple
                        #   devices.

                        # Internally, we still treat the MAC address as the primary
                        # "entity", but if a beacon payload is attached, we
                        # essentially create a duplicate BermudaDevice which uses
                        # the UUID as its "address", and we copy the most recently
                        # received device's details to it. This allows one to decide
                        # to track the MAC address or the UUID.

                        # Combining multiple Minor/Major's into one device isn't
                        # supported at this stage, and I'd suggest doing that sort
                        # of grouping at a higher level (eg using Groups in HA or
                        # matching automations on attributes or a subset of
                        # devices), but if there are prominent use-cases we can
                        # alter our approach.
                        #

                        device.beacon_type = BEACON_IBEACON_SOURCE
                        device.beacon_uuid = man_data[2:18].hex().lower()
                        device.beacon_major = int.from_bytes(
                            man_data[18:20], byteorder="big"
                        )
                        device.beacon_minor = int.from_bytes(
                            man_data[20:22], byteorder="big"
                        )
                        device.beacon_power = int.from_bytes(
                            [man_data[22]], signed=True
                        )

                        # So, the irony of having major/minor is that the
                        # UniversallyUniqueIDentifier is not even unique
                        # locally, so we need to make one :-)

                        device.beacon_unique_id = f"{device.beacon_uuid}_{device.beacon_major}_{device.beacon_minor}"

                        # Note: it's possible that a device sends multiple
                        # beacons. We are only going to process the latest
                        # one in any single update cycle, so we ignore that
                        # possibility for now. Given we re-process completely
                        # each cycle it should *just work*, for the most part.

                        # _LOGGER.debug(
                        #     "Device %s is iBeacon with UUID %s %s %s %sdB",
                        #     device.address,
                        #     device.beacon_uuid,
                        #     device.beacon_major,
                        #     device.beacon_minor,
                        #     device.beacon_power,
                        # )

                        # NOTE: The processing of the BEACON_IBEACON_DEVICE
                        # meta-device is done later, after the rest of this
                        # source device is set up.

                        # expose the full id in prefname
                        device.prefname = device.beacon_unique_id
                    else:
                        # apple but not an iBeacon, expose the data in case it's useful.
                        device.prefname = man_data.hex()
                # else:
                #     _LOGGER.debug(
                #         "Found unknown manufacturer %d data: %s %s",
                #         company_code,
                #         device.address,
                #         man_data.hex(),
                #     )

            # We probably don't need to do all of this every time, but we
            # want to catch any changes, eg when the system learns the local
            # name etc.
            device.name = device.name or service_info.device.name
            device.local_name = (
                device.local_name or service_info.advertisement.local_name
            )
            device.manufacturer = device.manufacturer or service_info.manufacturer
            device.connectable = service_info.connectable

            # Try to make a nice name for prefname.
            if device.prefname is None or device.prefname.startswith(DOMAIN + "_"):
                device.prefname = (
                    device.name
                    or device.local_name
                    or DOMAIN + "_" + slugify(device.address)
                )

            # Work through the scanner entries...
            matched_scanners = bluetooth.async_scanner_devices_by_address(
                self.hass, service_info.address, False
            )
            for discovered in matched_scanners:
                scanner_device = self._get_device(discovered.scanner.source)
                if scanner_device is None:
                    # The receiver doesn't have a device entry yet, let's refresh
                    # all of them in this batch...
                    self._refresh_scanners(matched_scanners, self._do_full_scanner_init)
                    self._do_full_scanner_init = False
                    scanner_device = self._get_device(discovered.scanner.source)

                if scanner_device is None:
                    # Highly unusual. If we can't find an entry for the scanner
                    # maybe it's from an integration that's not yet loaded, or
                    # perhaps it's an unexpected type that we don't know how to
                    # find.
                    _LOGGER.error(
                        "Failed to find config for scanner %s, this is probably a bug.",
                        discovered.scanner.source,
                    )
                    continue

                # Replace the scanner entry on the current device
                device.add_scanner(scanner_device, discovered)

            # Update whether the device has been seen recently, for device_tracker:
            if (
                MONOTONIC_TIME()
                - self.options.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT)
                < device.last_seen
            ):
                device.zone = STATE_HOME
            else:
                device.zone = STATE_NOT_HOME

            if device.address.upper() in self.options.get(CONF_DEVICES, []):
                # This is a device we track. Flag it for set-up:
                device.create_sensor = True

        self._refresh_areas_by_min_distance()

        # We might need to freshen deliberately on first start this if no new scanners
        # were discovered in the first scan update. This is likely if nothing has changed
        # since the last time we booted.
        if self._do_full_scanner_init:
            self._refresh_scanners([], self._do_full_scanner_init)
            self._do_full_scanner_init = False

        # set up any beacons and update their data. We do this after all the devices
        # have had their updates done since any beacon inherits data from its source
        # device(s). We do this *before* sensor creation, though.
        self.configure_beacons()

        # The devices are all updated now (and any new scanners and beacons seen have been added),
        # so let's ensure any devices that we create sensors for are set up ready to go.
        # We don't do this sooner because we need to ensure we have every active scanner
        # already loaded up.
        for address in self.options.get(CONF_DEVICES, []):
            device = self._get_device(format_mac(address.lower()))
            if device is not None:
                if not device.create_sensor_done or not device.create_tracker_done:
                    _LOGGER.debug(
                        "Firing device_new for %s (%s)", device.name, device.address
                    )
                    async_dispatcher_send(
                        self.hass, SIGNAL_DEVICE_NEW, device.address, self.scanner_list
                    )

        # end of async update

    def configure_beacons(self):
        """Create iBeacon and other meta-devices from the received advertisements

        Note that at this point all the distances etc should be fresh for
        the source devices, so we can just copy values from them to the beacon metadevice.
        """

        # First let's find the freshest device advert for each Beacon unique_id
        freshest_beacon_sources: dict[str, BermudaDevice] = {}
        for device in self.devices.values():
            if device.beacon_type == BEACON_IBEACON_SOURCE:
                if (
                    device.beacon_unique_id not in freshest_beacon_sources  # first-find
                    or device.last_seen
                    > freshest_beacon_sources[
                        device.beacon_unique_id
                    ].last_seen  # fresher find
                ):
                    # then we are the freshest!
                    freshest_beacon_sources[device.beacon_unique_id] = device

        # Now let's go through the freshest adverts and set up those beacons.
        for beacon_unique_id, device in freshest_beacon_sources.items():
            # Copy this device's info to the meta-device for tracking the beacon

            metadev = self._get_or_create_device(beacon_unique_id)
            metadev.beacon_type = BEACON_IBEACON_DEVICE

            # anything that isn't already set to something interesting, overwrite
            # it with the new device's data.
            # Defaults:
            for attribute in [
                # "create_sensor",  # don't copy this, we might track the device but not the beacon.
                "local_name",  # name's we copy if there isn't one already.
                "manufacturer",
                "name",
                "options",
                "prefname",
            ]:
                if hasattr(metadev, attribute):
                    if getattr(metadev, attribute) in [None, False]:
                        setattr(metadev, attribute, getattr(device, attribute))
                else:
                    _LOGGER.error(
                        "Devices don't have a '%s' attribute, this is a bug.", attribute
                    )
            # Anything that's VERY interesting, overwrite it regardless of what's already there:
            # INTERESTING:
            for attribute in [
                "area_distance",
                "area_id",
                "area_name",
                "area_rssi",
                "area_scanner",
                "beacon_major",
                "beacon_minor",
                "beacon_power",
                "beacon_unique_id",
                "beacon_uuid",
                "connectable",
                "mac_is_random",
                "zone",
            ]:
                if hasattr(metadev, attribute):
                    setattr(metadev, attribute, getattr(device, attribute))
                else:
                    _LOGGER.error(
                        "Devices don't have a '%s' attribute, this is a bug.", attribute
                    )

            # copy (well, link, I guess) the scanner data.
            metadev.scanners = device.scanners

            if device.last_seen > metadev.last_seen:
                metadev.last_seen = device.last_seen
            elif device.last_seen < metadev.last_seen:
                _LOGGER.warning("Using freshest advert but it's still too old!")
            # else there's no newer advert

            if device.address not in metadev.beacon_sources:
                # add this device as a known source
                metadev.beacon_sources.insert(0, device.address)
                # and trim the list of sources
                del metadev.beacon_sources[HIST_KEEP_COUNT:]

            # Check if we should set up sensors for this beacon
            if metadev.address.upper() in self.options.get(CONF_DEVICES, []):
                # This is a meta-device we track. Flag it for set-up:
                metadev.create_sensor = True

            # BEWARE: Currently we just copy the entire scanners dict from
            # the freshest device's info. This means the history on the beacon device
            # doesn't have scanner history etc from other devices, which might be
            # relevant. If you need this, it's recommended to instead look up the
            # metadev.beacon_sources list, and iterate through those to put together
            # the history etc you need.

    def dt_mono_to_datetime(self, stamp) -> datetime:
        """Given a monotonic timestamp, convert to datetime object"""
        age = MONOTONIC_TIME() - stamp
        return now() - timedelta(seconds=age)

    def dt_mono_to_age(self, stamp) -> str:
        """Convert monotonic timestamp to age (eg: "6 seconds ago")"""
        return get_age(self.dt_mono_to_datetime(stamp))

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
            if scanner.rssi_distance < self.options.get(
                CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS
            ):  # It's inside max_radius...
                if closest_scanner is None:
                    # no encumbent, we win! (unless we don't have a stamp to validate our claim)
                    # FIXME: This effectively excludes HCI/usb adaptors currently since we
                    # haven't found a way to get ad timestamps from HA's bluez yet.
                    if scanner.stamp > 0:
                        closest_scanner = scanner
                else:
                    # is it fresh enough to win on proximity alone?
                    is_fresh_enough = (
                        scanner.stamp > closest_scanner.stamp - ADVERT_FRESHTIME
                    )
                    # is it so much fresher that it wins outright?
                    is_fresher = (
                        scanner.stamp > closest_scanner.stamp + ADVERT_FRESHTIME
                    )
                    # is it closer?
                    is_closer = scanner.rssi_distance < closest_scanner.rssi_distance

                    if is_fresher or (
                        is_closer and is_fresh_enough
                    ):  # This scanner is closer, and the advert is still fresh in comparison..
                        closest_scanner = scanner

        if closest_scanner is not None:
            # We found a winner
            old_area = device.area_name
            device.area_id = closest_scanner.area_id
            areas = self.area_reg.async_get_area(device.area_id)
            if hasattr(areas, "name"):
                device.area_name = areas.name
            else:
                # Wasn't a single area entry. Let's freak out.
                _LOGGER.warning(
                    "Could not discern area from scanner %s: %s."
                    "Please assign an area then reload this integration",
                    closest_scanner.name,
                    areas,
                )
                device.area_name = f"No area: {closest_scanner.name}"
            device.area_distance = closest_scanner.rssi_distance
            device.area_rssi = closest_scanner.rssi
            device.area_scanner = closest_scanner.name
            if old_area != device.area_name and device.create_sensor:
                _LOGGER.debug("Device %s now in %s", device.name, device.area_name)
        else:
            # Not close to any scanners!
            device.area_id = None
            device.area_name = None
            device.area_distance = None
            device.area_rssi = None
            device.area_scanner = None

    def _refresh_scanners(
        self, scanners: list[BluetoothScannerDevice], do_full_scan=False
    ):
        """Refresh our local (and saved) list of scanners (BLE Proxies)"""
        addresses = set()
        update_scannerlist = False

        for scanner in scanners:
            addresses.add(scanner.scanner.source.upper())

        # If we are doing a full scan, add all the known
        # scanner addresses to the list, since that will cover
        # the scanners that have been restored from config.data
        if do_full_scan:
            for address in self.scanner_list:
                addresses.add(address)

        if len(addresses) > 0:
            # FIXME: Really? This can't possibly be a sensible nesting of loops.
            # should probably look at the API. Anyway, we are checking any devices
            # that have a "mac" or "bluetooth" connection,
            for dev_entry in self.hass.data["device_registry"].devices.data.values():
                for dev_connection in dev_entry.connections:
                    if dev_connection[0] in ["mac", "bluetooth"]:
                        found_address = dev_connection[1].upper()
                        if found_address in addresses:
                            scandev = self._get_device(found_address)
                            if scandev is None:
                                # It's a new scanner, we will need to update our saved config.
                                _LOGGER.debug("New Scanner: %s", found_address)
                                update_scannerlist = True
                                scandev = self._get_or_create_device(found_address)
                            scandev_orig = scandev
                            scandev.area_id = dev_entry.area_id
                            scandev.entry_id = dev_entry.id
                            if dev_entry.name_by_user is not None:
                                scandev.name = dev_entry.name_by_user
                            else:
                                scandev.name = dev_entry.name
                            areas = self.area_reg.async_get_area(dev_entry.area_id)
                            if hasattr(areas, "name"):
                                scandev.area_name = areas.name
                            else:
                                _LOGGER.warning(
                                    "No area name for while updating scanner %s",
                                    scandev.name,
                                )
                            scandev.is_scanner = True
                            if scandev_orig != scandev:
                                # something changed, let's update the saved list.
                                update_scannerlist = True
        if update_scannerlist:
            # We need to update our saved list of scanners in config data.
            self.scanner_list = []
            scanners: dict[str, str] = {}
            for device in self.devices.values():
                if device.is_scanner:
                    scanners[device.address] = device.to_dict()
                    self.scanner_list.append(device.address)
            _LOGGER.debug(
                "Replacing config data scanners was %s now %s",
                self.config_entry.data.get(CONFDATA_SCANNERS, {}),
                scanners,
            )
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONFDATA_SCANNERS: scanners},
            )

    async def service_dump_devices(self, call):  # pylint: disable=unused-argument;
        """Return a dump of beacon advertisements by receiver"""
        out = {}
        addresses_input = call.data.get("addresses", "")
        if addresses_input != "":
            addresses = addresses_input.upper().split()
        else:
            addresses = []
        for address, device in self.devices.items():
            if len(addresses) == 0 or address.upper() in addresses:
                out[address] = device.to_dict()
        return out


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    coordinator: BermudaDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    address = None
    for ident in device_entry.identifiers:
        try:
            if ident[0] == DOMAIN:
                # the identifier should be the mac address, and
                # may have "_range" or some other per-sensor suffix. Just grab
                # the mac address part.
                address = ident[1][:17]
        except KeyError:
            pass
    if address is not None:
        try:
            coordinator.devices[format_mac(address)].create_sensor = False
        except KeyError:
            _LOGGER.warning("Failed to locate device entry for %s", address)
        return True
    return False


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
