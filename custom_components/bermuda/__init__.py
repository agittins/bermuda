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
from typing import Final
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.bluetooth import BluetoothScannerDevice
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.active_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.core import Config
from homeassistant.core import HomeAssistant
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import area_registry
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify
from homeassistant.util.dt import get_age
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.util.dt import now

from .const import ADVERT_FRESHTIME
from .const import CONF_ATTENUATION
from .const import CONF_DEVICES
from .const import CONF_DEVTRACK_TIMEOUT
from .const import CONF_MAX_RADIUS
from .const import CONF_REF_POWER
from .const import DEFAULT_ATTENUATION
from .const import DEFAULT_DEVTRACK_TIMEOUT
from .const import DEFAULT_MAX_RADIUS
from .const import DEFAULT_REF_POWER
from .const import DOMAIN
from .const import HIST_KEEP_COUNT
from .const import PLATFORMS
from .const import STARTUP_MESSAGE
from .entity import BermudaEntity

# from bthome_ble import BTHomeBluetoothDeviceData

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

SCAN_INTERVAL = timedelta(seconds=5)

MONOTONIC_TIME: Final = monotonic_time_coarse

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

    # username = entry.data.get(CONF_USERNAME)
    # password = entry.data.get(CONF_PASSWORD)

    coordinator = BermudaDataUpdateCoordinator(hass, entry)
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


class BermudaPBDUCoordinator(
    PassiveBluetoothDataUpdateCoordinator,
):
    """Class for receiving bluetooth adverts in realtime

    Looks like this needs to be run through setup, with a specific
    BLEDevice (address) already specified, so won't do "all adverts"
    """

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        ble_device: BLEDevice,
        device: BermudaDevice,
    ) -> None:
        """Init"""
        super().__init__(
            hass=hass,
            logger=logger,
            address=ble_device.address,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=False,
        )
        self.device = device

    @callback
    def _async_handle_unavailable(
        self, service_info: BluetoothServiceInfoBleak
    ) -> None:
        return super()._async_handle_unavailable(service_info)

    @callback
    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        return super()._async_handle_bluetooth_event(service_info, change)


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
        self.hist_interval = []
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
            # We load anything the first time 'round, but from then on
            # we are more cautious.
            self.rssi: float = scandata.advertisement.rssi
            self.hist_rssi.insert(0, self.rssi)
            self.rssi_distance: float = rssi_to_metres(
                self.rssi,
                options.get(CONF_REF_POWER, DEFAULT_REF_POWER),
                options.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
            )
            self.hist_distance.insert(0, self.rssi_distance)
            self.hist_interval.insert(0, new_stamp - self.stamp)
            # Stamp will be faked from above if required.
            if have_new_stamp:
                self.stamp = new_stamp
                self.hist_stamp.insert(0, self.stamp)

        # Safe to update these values regardless of stamps...

        self.adapter: str = scandata.scanner.adapter
        self.source: str = scandata.scanner.source
        if (
            self.tx_power is not None
            and scandata.advertisement.tx_power != self.tx_power
        ):
            # Not really an erorr, we just don't account for this happening. I want to know if it does.
            _LOGGER.warning(
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
        self.area_id: str = None
        self.area_name: str = None
        self.area_distance: float = None  # how far this dev is from that area
        self.area_rssi: float = None  # rssi from closest scanner
        self.area_scanner: str = None  # name of closest scanner
        self.zone: str = None  # home or not_home
        self.manufacturer: str = None
        self.connectable: bool = False
        self.is_scanner: bool = False
        self.entry_id: str = None  # used for scanner devices
        self.send_tracker_see: bool = False  # Create/update device_tracker entity
        self.create_sensor: bool = False  # Create/update a sensor for this device
        self.last_seen: float = (
            0  # stamp from most recent scanner spotting. MONOTONIC_TIME
        )
        self.scanners: dict[str, BermudaDeviceScanner] = {}

    def add_scanner(
        self, scanner_device: BermudaDevice, discoveryinfo: BluetoothScannerDevice
    ):
        """Add/Replace a scanner entry on this device, indicating a received advertisement"""
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
        self.devices: dict[str, BermudaDevice] = {}
        self.created_entities: set[BermudaEntity] = set()

        self.area_reg = area_registry.async_get(hass)

        hass.services.async_register(
            DOMAIN,
            "dump_devices",
            self.service_dump_devices,
            vol.Schema({vol.Optional("addresses"): cv.string}),
            SupportsResponse.ONLY,
        )

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

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)

    def _get_device(self, address: str) -> BermudaDevice:
        """Search for a device entry based on mac address"""
        mac = format_mac(address)
        if mac in self.devices:
            return self.devices[mac]
        return None

    def _get_or_create_device(self, address: str) -> BermudaDevice:
        device = self._get_device(address)
        if device is None:
            mac = format_mac(address)
            self.devices[mac] = device = BermudaDevice(
                address=address, options=self.options
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
            # TODO: Add support for user-defined name, especially since the
            #   device_tracker entry can only be renamed using the editor.
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
                    self._refresh_scanners(matched_scanners)
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

            if device.address.upper() in self.options.get(CONF_DEVICES, []):
                device.send_tracker_see = True
                device.create_sensor = True

            if device.send_tracker_see:
                # Send a "see" notification to device_tracker
                await self._send_device_tracker_see(device)

        self._refresh_areas_by_min_distance()

        # end of async update

    async def _send_device_tracker_see(self, device: BermudaDevice):
        """Send "see" event to the legacy device_tracker integration.

        If the device is not yet in known_devices.yaml it will get added.
        Note that device_tracker can *only* support [home|not_home],
        because device_tracker only deals with "Zones" not "Areas".

        Simply calling the "see" service is the simplest way to
        get this done, but if we need more control (eg, specifying
        the source (gps|router|etc)) we might need to hook and implement
        it specifically. This is probably all we need right now though:

        TODO: Allow user to configure what name to use for the device_tracker.
        """

        # Check if the device has been seen recently
        if (
            MONOTONIC_TIME()
            - self.options.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT)
            < device.last_seen
        ):
            device.zone = "home"
        else:
            device.zone = "not_home"

        # If mac is set, device_tracker will override our dev_id
        # with slugified (hostname OR mac). We don't want that
        # since we want dev_id (the key in known_devices.yaml) to
        # be stable, predictable and identifyably ours.
        #
        # So, we will not set mac, but use bermuda_[mac] as dev_id
        # and prefname or user-supplied name for host_name.
        await self.hass.services.async_call(
            domain="device_tracker",
            service="see",
            service_data={
                "dev_id": "bermuda_" + slugify(device.address),
                # 'mac': device.address,
                "host_name": device.prefname,
                "location_name": device.zone,
            },
        )

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

    def _refresh_scanners(self, scanners: list[BluetoothScannerDevice]):
        """Refresh our local list of scanners (BLE Proxies)"""
        addresses = set()
        for scanner in scanners:
            addresses.add(scanner.scanner.source.upper())
        if len(addresses) > 0:
            # FIXME: Really? This can't possibly be a sensible nesting of loops.
            # should probably look at the API. Anyway, we are checking any devices
            # that have a "mac" or "bluetooth" connection,
            for dev_entry in self.hass.data["device_registry"].devices.data.values():
                for dev_connection in dev_entry.connections:
                    if dev_connection[0] in ["mac", "bluetooth"]:
                        found_address = dev_connection[1].upper()
                        if found_address in addresses:
                            scandev = self._get_or_create_device(found_address)
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
