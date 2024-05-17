"""Bermuda's internal representation of a bluetooth device.

Each discovered bluetooth device (ie, every found transmitter) will
have one of these entries created for it. These are not HA 'devices' but
our own internal thing. They directly correspond to the entries you will
see when calling the dump_devices service call.

Even devices which are not configured/tracked will get entries created
for them, so we can use them to contribute towards measurements."""

from __future__ import annotations

from homeassistant.components.bluetooth import MONOTONIC_TIME
from homeassistant.components.bluetooth import BluetoothScannerDevice
from homeassistant.const import STATE_HOME
from homeassistant.const import STATE_NOT_HOME
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers.device_registry import format_mac

from .bermuda_device_scanner import BermudaDeviceScanner
from .const import BDADDR_TYPE_UNKNOWN
from .const import CONF_DEVICES
from .const import CONF_DEVTRACK_TIMEOUT
from .const import DEFAULT_DEVTRACK_TIMEOUT


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
        self.name: str | None = None
        self.local_name: str | None = None
        self.prefname: str | None = None  # "preferred" name - ideally local_name
        self.address: str = address
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
        self.beacon_sources = (
            []
        )  # list of MAC addresses that have advertised this beacon
        self.beacon_unique_id: str | None = (
            None  # combined uuid_major_minor for *really* unique id
        )
        self.beacon_uuid: str | None = None
        self.beacon_major: str | None = None
        self.beacon_minor: str | None = None
        self.beacon_power: float | None = None

        self.entry_id: str | None = None  # used for scanner devices
        self.create_sensor: bool = False  # Create/update a sensor for this device
        self.create_sensor_done: bool = False  # Sensor should now exist
        self.create_tracker_done: bool = False  # device_tracker should now exist
        self.last_seen: float = (
            0  # stamp from most recent scanner spotting. MONOTONIC_TIME
        )
        self.scanners: dict[str, BermudaDeviceScanner] = {}

    def calculate_data(self):
        """Call after doing update_scanner() calls so that distances
        etc can be freshly smoothed and filtered.

        """
        for scanner in self.scanners.values():
            scanner.calculate_data()

        # Update whether the device has been seen recently, for device_tracker:
        if (
            self.last_seen is not None
            and MONOTONIC_TIME()
            - self.options.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT)
            < self.last_seen
        ):
            self.zone = STATE_HOME
        else:
            self.zone = STATE_NOT_HOME

        if self.address.upper() in self.options.get(CONF_DEVICES, []):
            # We are a device we track. Flag for set-up:
            self.create_sensor = True

    def update_scanner(
        self, scanner_device: BermudaDevice, discoveryinfo: BluetoothScannerDevice
    ):
        """Add/Update a scanner entry on this device, indicating a received advertisement

        This gets called every time a scanner is deemed to have received an advert for
        this device. It only loads data into the structure, all calculations are done
        with calculate_data()

        """
        if format_mac(scanner_device.address) in self.scanners:
            # Device already exists, update it
            self.scanners[format_mac(scanner_device.address)].update_advertisement(
                self.address,
                discoveryinfo,  # the entire BluetoothScannerDevice struct
                scanner_device.area_id or "area_not_defined",
                self.options,
            )
        else:
            self.scanners[format_mac(scanner_device.address)] = BermudaDeviceScanner(
                self.address,
                discoveryinfo,  # the entire BluetoothScannerDevice struct
                scanner_device.area_id or "area_not_defined",
                self.options,
            )
        device_scanner = self.scanners[format_mac(scanner_device.address)]
        # Let's see if we should update our last_seen based on this...
        if device_scanner.stamp is not None and self.last_seen < device_scanner.stamp:
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
