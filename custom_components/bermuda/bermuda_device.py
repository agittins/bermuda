"""
Bermuda's internal representation of a bluetooth device.

Each discovered bluetooth device (ie, every found transmitter) will
have one of these entries created for it. These are not HA 'devices' but
our own internal thing. They directly correspond to the entries you will
see when calling the dump_devices service call.

Even devices which are not configured/tracked will get entries created
for them, so we can use them to contribute towards measurements.
"""

from __future__ import annotations

import re

from homeassistant.components.bluetooth import MONOTONIC_TIME, BluetoothScannerDevice
from homeassistant.const import STATE_HOME, STATE_NOT_HOME, STATE_UNAVAILABLE
from homeassistant.helpers.device_registry import format_mac

from .bermuda_device_scanner import BermudaDeviceScanner
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_PRIVATE_RESOLVABLE,
    BDADDR_TYPE_UNKNOWN,
    BEACON_IBEACON_DEVICE,
    BEACON_PRIVATE_BLE_DEVICE,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    DEFAULT_DEVTRACK_TIMEOUT,
)


class BermudaDevice(dict):
    """
    This class is to represent a single bluetooth "device" tracked by Bermuda.

    "device" in this context means a bluetooth receiver like an ESPHome
    running bluetooth_proxy or a bluetooth transmitter such as a beacon,
    a thermometer, watch or phone etc.

    We're not storing this as an Entity because we don't want all devices to
    become entities in homeassistant, since there might be a _lot_ of them.
    """

    def __init__(self, address, options) -> None:
        """Initial (empty) data."""
        self.name: str | None = None
        self.local_name: str | None = None
        self.prefname: str | None = None  # "preferred" name - ideally local_name
        self.address: str = address
        self.ref_power: float = 0  # If non-zero, use in place of global ref_power.
        self.ref_power_changed: float = 0  # Stamp for last change to ref_power, for cache zapping.
        self.options = options
        self.unique_id: str | None = None  # mac address formatted.
        self.address_type = BDADDR_TYPE_UNKNOWN
        self.area_id: str | None = None
        self.area_name: str | None = None
        self.area_distance: float | None = None  # how far this dev is from that area
        self.area_rssi: float | None = None  # rssi from closest scanner
        self.area_scanner: str | None = None  # name of closest scanner
        self.zone: str = STATE_UNAVAILABLE  # STATE_HOME or STATE_NOT_HOME
        self.manufacturer: str | None = None
        self.connectable: bool = False
        self.is_scanner: bool = False
        self.beacon_type: set = set()
        self.beacon_sources = []  # list of MAC addresses that have advertised this beacon
        self.beacon_unique_id: str | None = None  # combined uuid_major_minor for *really* unique id
        self.beacon_uuid: str | None = None
        self.beacon_major: str | None = None
        self.beacon_minor: str | None = None
        self.beacon_power: float | None = None

        self.entry_id: str | None = None  # used for scanner devices
        self.create_sensor: bool = False  # Create/update a sensor for this device
        self.create_sensor_done: bool = False  # Sensor should now exist
        self.create_tracker_done: bool = False  # device_tracker should now exist
        self.create_number_done: bool = False
        self.create_button_done: bool = False
        self.create_all_done: bool = False  # All platform entities are done and ready.
        self.last_seen: float = 0  # stamp from most recent scanner spotting. MONOTONIC_TIME
        self.scanners: dict[str, BermudaDeviceScanner] = {}

        # BLE MAC addresses (https://www.bluetooth.com/specifications/core54-html/) can
        # be differentiated by the top two MSBs of the 48bit MAC address. At our end at
        # least, this means the first character of the MAC address in aa:bb:cc:dd:ee:ff
        # I have no idea what the distinction between public and random is by bitwise ident,
        # because the random addresstypes cover the entire address-space.
        #
        # - ?? Public
        # - 0b00 (0x00 - 0x3F) Random Private Non-resolvable
        # - 0b01 (0x40 - 0x7F) Random Private Resolvable (ie, IRK devices)
        # - 0x10 (0x80 - 0xBF) ~* Reserved *~ (Is this where ALL Publics live?)
        # - 0x11 (0xC0 - 0xFF) Random Static (may change on power cycle only)
        #
        # What we are really interested in tracking is IRK devices, since they rotate
        # so rapidly (typically )
        #
        # A given device entry (ie, address) won't change, so we only update
        # it once, and also only if it looks like a MAC address
        #
        if self.address_type is BDADDR_TYPE_UNKNOWN:
            if self.address.count(":") != 5:
                # Doesn't look like an actual MAC address
                if re.match("^[A-Fa-f0-9]{32}_[A-Fa-f0-9]*_[A-Fa-f0-9]*$", self.address):
                    # It's an iBeacon uuid_major_minor
                    self.address_type = ADDR_TYPE_IBEACON
                    self.beacon_type.add(BEACON_IBEACON_DEVICE)
                    self.beacon_unique_id = self.address
                elif re.match("^[A-Fa-f0-9]{32}$", self.address):
                    # 32-char hex-string is an IRK
                    self.beacon_type.add(BEACON_PRIVATE_BLE_DEVICE)
                    self.address_type = ADDR_TYPE_PRIVATE_BLE_DEVICE
                    self.beacon_unique_id = self.address
                else:
                    # We have no idea, currently.
                    # Mark it as such so we don't spend time testing it again.
                    self.address_type = BDADDR_TYPE_NOT_MAC48
            elif len(self.address) == 17 and self.address[0:1] in "4567":
                # We're checking if the first char in the address
                # is one of 4, 5, 6, 7. Python is fun :-)
                _LOGGER.debug("Identified IRK source address on %s", self.address)
                self.address_type = BDADDR_TYPE_PRIVATE_RESOLVABLE
            else:
                self.address_type = BDADDR_TYPE_OTHER

    def set_ref_power(self, new_ref_power: float):
        """
        Set a new reference power for this device and immediately apply
        an interim distance calculation.

        This gets called by the calibration routines, but also by metadevice
        updates, as they need to apply their own ref_power if necessary.
        """
        if new_ref_power != self.ref_power:
            # it's actually changed, proceed...
            self.ref_power = new_ref_power
            nearest_distance = 9999  # running tally to find closest scanner
            nearest_scanner = None
            for scanner in self.scanners.values():
                rawdist = scanner.set_ref_power(new_ref_power)
                if rawdist is not None and rawdist < nearest_distance:
                    nearest_distance = rawdist
                    nearest_scanner = scanner
            # Even though the actual scanner should not have changed (it should
            # remain none or a given scanner, since the relative distances won't have
            # changed due to ref_power), we still call apply so that the new area_distance
            # gets applied.
            # if nearest_scanner is not None:
            self.apply_scanner_selection(nearest_scanner)
            # Update the stamp so that the BermudaEntity can clear the cache and show the
            # new measurement(s) immediately.
            self.ref_power_changed = MONOTONIC_TIME()

    def apply_scanner_selection(self, closest_scanner: BermudaDeviceScanner | None):
        """
        Given a DeviceScanner entry, apply the distance and area attributes
        from it to this device.

        Used to apply a "winning" scanner's data to the device for setting closest Area.
        """
        # FIXME: This might need to check if it's a metadevice source or dest, and
        # ensure things are applied correctly. Might be a non-issue.
        old_area = self.area_name
        if closest_scanner is not None:
            # We found a winner
            self.area_id = closest_scanner.area_id
            self.area_name = closest_scanner.area_name
            self.area_distance = closest_scanner.rssi_distance
            self.area_rssi = closest_scanner.rssi
            self.area_scanner = closest_scanner.name
        else:
            # Not close to any scanners!
            self.area_id = None
            self.area_name = None
            self.area_distance = None
            self.area_rssi = None
            self.area_scanner = None
        if (old_area != self.area_name) and self.create_sensor:
            # Our area has changed!
            _LOGGER.debug(
                "Device %s was in '%s', now '%s'",
                self.name,
                old_area,
                self.area_name,
            )

    def calculate_data(self):
        """
        Call after doing update_scanner() calls so that distances
        etc can be freshly smoothed and filtered.

        """
        # Run calculate_data on each child scanner of this device:
        for scanner in self.scanners.values():
            if isinstance(scanner, BermudaDeviceScanner):
                # in issue #355 someone had an empty dict instead of a scanner object.
                # it may be due to a race condition during startup, but we check now
                # just in case. Was not able to reproduce.
                scanner.calculate_data()
            else:
                _LOGGER_SPAM_LESS.error(
                    "scanner_not_instance", "Scanner device is not a BermudaDevice instance, skipping."
                )

        # Update whether this device has been seen recently, for device_tracker:
        if (
            self.last_seen is not None
            and MONOTONIC_TIME() - self.options.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT) < self.last_seen
        ):
            self.zone = STATE_HOME
        else:
            self.zone = STATE_NOT_HOME

        if self.address.upper() in self.options.get(CONF_DEVICES, []):
            # We are a device we track. Flag for set-up:
            self.create_sensor = True

    def update_scanner(self, scanner_device: BermudaDevice, discoveryinfo: BluetoothScannerDevice):
        """
        Add/Update a scanner entry on this device, indicating a received advertisement.

        This gets called every time a scanner is deemed to have received an advert for
        this device. It only loads data into the structure, all calculations are done
        with calculate_data()

        """
        if format_mac(scanner_device.address) in self.scanners:
            # Device already exists, update it
            self.scanners[format_mac(scanner_device.address)].update_advertisement(
                discoveryinfo,  # the entire BluetoothScannerDevice struct
            )
            device_scanner = self.scanners[format_mac(scanner_device.address)]
        else:
            # Create it
            self.scanners[format_mac(scanner_device.address)] = BermudaDeviceScanner(
                self,
                discoveryinfo,  # the entire BluetoothScannerDevice struct
                self.options,
                scanner_device,
            )
            device_scanner = self.scanners[format_mac(scanner_device.address)]
            # On first creation, we also want to copy our ref_power to it (but not afterwards,
            # since a metadevice might take over that role later)
            device_scanner.ref_power = self.ref_power
        # Let's see if we should update our last_seen based on this...
        if device_scanner.stamp is not None and self.last_seen < device_scanner.stamp:
            self.last_seen = device_scanner.stamp

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices."""
        out = {}
        for var, val in vars(self).items():
            if var == "scanners":
                scanout = {}
                for address, scanner in self.scanners.items():
                    scanout[address] = scanner.to_dict()
                # FIXME: val is overwritten
                val = scanout  # noqa
            out[var] = val
        return out
