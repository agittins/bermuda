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

import binascii
import re
from typing import TYPE_CHECKING, Final

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.bluetooth import (
    BaseHaRemoteScanner,
    BaseHaScanner,
    BluetoothChange,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.private_ble_device import coordinator as pble_coordinator
from homeassistant.const import STATE_HOME, STATE_NOT_HOME
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.util import slugify

from .bermuda_advert import BermudaAdvert
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    BDADDR_TYPE_RANDOM_STATIC,
    BDADDR_TYPE_RANDOM_UNRESOLVABLE,
    BDADDR_TYPE_UNKNOWN,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    DEFAULT_DEVTRACK_TIMEOUT,
    DOMAIN,
    ICON_DEFAULT_AREA,
    ICON_DEFAULT_FLOOR,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_PRIVATE_BLE_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
)
from .util import mac_math_offset, mac_norm

if TYPE_CHECKING:
    from bleak.backends.scanner import AdvertisementData

    from .coordinator import BermudaDataUpdateCoordinator


class BermudaDevice(dict):
    """
    This class is to represent a single bluetooth "device" tracked by Bermuda.

    "device" in this context means a bluetooth receiver like an ESPHome
    running bluetooth_proxy or a bluetooth transmitter such as a beacon,
    a thermometer, watch or phone etc.

    We're not storing this as an Entity because we don't want all devices to
    become entities in homeassistant, since there might be a _lot_ of them.
    """

    def __hash__(self) -> int:
        """A BermudaDevice can be uniquely identified by the address used."""
        return hash(self.address)

    def __init__(self, address: str, coordinator: BermudaDataUpdateCoordinator) -> None:
        """Initial (empty) data."""
        _address = mac_norm(address)
        self.name: str = f"{DOMAIN}_{slugify(_address)}"  # "preferred" name built by Bermuda.
        self.name_bt_serviceinfo: str | None = None  # From serviceinfo.device.name
        self.name_bt_local_name: str | None = None  # From service_info.advertisement.local_name
        self.name_devreg: str | None = None  # From device registry, for other integrations like scanners, pble devices
        self.name_by_user: str | None = None  # Any user-defined (in the HA UI) name discovered for a device.
        self.address: Final[str] = _address
        self.address_ble_mac: str = _address
        self.address_wifi_mac: str | None = None
        # We use a weakref to avoid any possible GC issues (only likely if we add a __del__ method, but *shrug*)
        self._coordinator: BermudaDataUpdateCoordinator = coordinator
        self.ref_power: float = 0  # If non-zero, use in place of global ref_power.
        self.ref_power_changed: float = 0  # Stamp for last change to ref_power, for cache zapping.
        self.options = self._coordinator.options
        self.unique_id: str | None = _address  # mac address formatted.
        self.address_type = BDADDR_TYPE_UNKNOWN

        self.ar = ar.async_get(self._coordinator.hass)
        self.fr = fr.async_get(self._coordinator.hass)

        self.area: ar.AreaEntry | None = None
        self.area_id: str | None = None
        self.area_name: str | None = None
        self.area_icon: str = ICON_DEFAULT_AREA
        self.area_last_seen: str | None = None
        self.area_last_seen_id: str | None = None
        self.area_last_seen_icon: str = ICON_DEFAULT_AREA

        self.area_distance: float | None = None  # how far this dev is from that area
        self.area_rssi: float | None = None  # rssi from closest scanner
        self.area_advert: BermudaAdvert | None = None  # currently closest BermudaScanner

        self.floor: fr.FloorEntry | None = None
        self.floor_id: str | None = None
        self.floor_name: str | None = None
        self.floor_icon: str = ICON_DEFAULT_FLOOR
        self.floor_level: str | None = None

        self.zone: str = STATE_NOT_HOME  # STATE_HOME or STATE_NOT_HOME
        self.manufacturer: str | None = None
        self._hascanner: BaseHaRemoteScanner | BaseHaScanner | None = None  # HA's scanner
        self._is_scanner: bool = False
        self._is_remote_scanner: bool | None = None
        self.stamps: dict[str, float] = {}
        self.metadevice_type: set = set()
        self.metadevice_sources: list[str] = []  # list of MAC addresses that have/should match this beacon
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
        self.last_seen: float = 0  # stamp from most recent scanner spotting. monotonic_time_coarse
        self.diag_area_switch: str | None = None  # saves output of AreaTests
        self.adverts: dict[
            tuple[str, str], BermudaAdvert
        ] = {}  # str will be a scanner address OR a deviceaddress__scanneraddress
        self._async_process_address_type()

    def _async_process_address_type(self):
        """
        Identify the address type (MAC, IRK, iBeacon etc) and perform any setup.

        This will set the self.address_type and metadevice-related properties,
        as well as register for PBLE updates for IRK resolution.
        Note that we don't have an advertisement yet, so we can only do the things
        that we can infer from the address alone.
        """
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
                # Doesn't look like an actual MAC address - should be some sort of metadevice.

                if re.match("^[A-Fa-f0-9]{32}_[A-Fa-f0-9]*_[A-Fa-f0-9]*$", self.address):
                    # It's an iBeacon uuid_major_minor
                    self.address_type = ADDR_TYPE_IBEACON
                    self.metadevice_type.add(METADEVICE_IBEACON_DEVICE)
                    self.beacon_unique_id = self.address
                elif re.match("^[A-Fa-f0-9]{32}$", self.address):
                    # 32-char hex-string is an IRK
                    self.metadevice_type.add(METADEVICE_PRIVATE_BLE_DEVICE)
                    self.address_type = ADDR_TYPE_PRIVATE_BLE_DEVICE
                    self.beacon_unique_id = self.address
                    # If we've been given a private BLE address, then the integration must be up.
                    # register to get callbacks for address changes.
                    _irk_bytes = binascii.unhexlify(self.address)
                    _pble_coord = pble_coordinator.async_get_coordinator(self._coordinator.hass)
                    self._coordinator.config_entry.async_on_unload(
                        _pble_coord.async_track_service_info(self.async_handle_pble_callback, _irk_bytes)
                    )
                    _LOGGER.debug("Private BLE Callback registered for %s, %s", self.name, self.address)
                    #
                    # Also register a callback with our own, which can fake the PBLE callbacks.
                    self._coordinator.config_entry.async_on_unload(
                        self._coordinator.irk_manager.register_irk_callback(self.async_handle_pble_callback, _irk_bytes)
                    )
                    self._coordinator.irk_manager.add_irk(_irk_bytes)
                else:
                    # We have no idea, currently.
                    # Mark it as such so we don't spend time testing it again.
                    self.address_type = BDADDR_TYPE_NOT_MAC48
            elif len(self.address) == 17:
                top_bits = int(self.address[0:1], 16) >> 2
                # The two MSBs of the first octet dictate the random type...
                if top_bits & 0b00:  # First char will be in [0 1 2 3]
                    self.address_type = BDADDR_TYPE_RANDOM_UNRESOLVABLE
                elif top_bits & 0b01:  # Addresses where the first char will be 4,5,6 or 7
                    _LOGGER.debug("Identified Resolvable Private (potential IRK source) Address on %s", self.address)
                    self.address_type = BDADDR_TYPE_RANDOM_RESOLVABLE
                    self._coordinator.irk_manager.check_mac(self.address)
                elif top_bits & 0b10:
                    self.address_type = "reserved"
                    _LOGGER.debug("Hey, got one of those reserved MACs, %s", self.address)
                elif top_bits & 0b11:
                    self.address_type = BDADDR_TYPE_RANDOM_STATIC

            else:
                # This is a normal MAC address.
                self.address_type = BDADDR_TYPE_OTHER
                name, generic = self._coordinator.get_manufacturer_from_id(self.address[:8])
                if name and (self.manufacturer is None or not generic):
                    self.manufacturer = name

    @property
    def is_scanner(self):
        return self._is_scanner

    @property
    def is_remote_scanner(self):
        return self._is_remote_scanner

    def async_as_scanner_nolonger(self):
        """Call when this device is unregistered as a BaseHaScanner."""
        self._is_scanner = False
        self._is_remote_scanner = False
        self._coordinator.scanner_list_del(self)

    def async_as_scanner_init(self, ha_scanner: BaseHaScanner):
        """
        Configure this device as a scanner device.

        Use to set up a device as a scanner.
        """
        if self._hascanner is ha_scanner:
            # Actual object has not changed, we're good.
            return

        # If we don't already have a self._hascanner, then this must be our
        # first initialisation. Otherwise we're just updating with a (potentially) new
        # hascanner.
        _first_init = self._hascanner is None

        self._hascanner = ha_scanner
        self._is_scanner = True
        # Only Remote ha scanners provide explicit timestamps...
        if isinstance(self._hascanner, BaseHaRemoteScanner):
            self._is_remote_scanner = True
        else:
            self._is_remote_scanner = False
        self._coordinator.scanner_list_add(self)

        # Find the relevant device entries in HA for this scanner and apply the names, addresses etc
        self.async_as_scanner_resolve_device_entries()

        # Call the per-update processor as well, but only
        # if this is our first ha_scanner.
        # This is because we must avoid an infinite loop in the case
        # where the scanner_update might call us.
        if _first_init:
            self.async_as_scanner_update(ha_scanner)

    def async_as_scanner_resolve_device_entries(self):
        """From the known MAC address, resolve any relevant device entries and names etc."""
        # As of 2025.2.0 The bluetooth integration creates its own device entries
        # for all HaScanners, not just local adaptors. So since there are two integration
        # pages where a user might apply an area setting (eg, the bluetooth page or the shelly/esphome pages)
        # we should check both to see if the user has applied an area (or name) anywhere, and
        # prefer the bluetooth one if both are set.

        # espressif devices have a base_mac
        # https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/misc_system_api.html#local-mac-addresses
        # base_mac (WiFi STA), +1 (AP), +2 (BLE), +3 (Ethernet)
        # Also possible for them to use LocalMAC, where the AP and Ether MACs are derived from STA and BLE
        # MACs, with first octet having bitvalue0x2 set, or if it was already, bitvalue0x4 XORd
        #
        # core Bluetooth now reports the BLE MAC address, while ESPHome (and maybe Shelly?) use
        # the ethernet or wifi MAC for their connection links. We want both devices (if present) so that
        # we can let the user apply name and area settings to either device.

        if self._hascanner is None:
            _LOGGER.warning("Scanner %s has no ha_scanner, can not resolve devices.", self.__repr__())
            return

        # scanner_ha: BaseHaScanner from HA's bluetooth backend
        # scanner_devreg_bt: DeviceEntry from HA's device_registry from Bluetooth integration
        # scanner_devreg_mac: DeviceEntry from HA's *other* integrations, like ESPHome, Shelly.

        connlist = set()  # For macthing against device_registry connections
        maclist = set()  # For matching against device_registry identifier

        # The device registry devices for the bluetooth and ESPHome/Shelly devices.
        scanner_devreg_bt = None
        scanner_devreg_mac = None
        scanner_devreg_mac_address = None
        scanner_devreg_bt_address = None

        # We don't know which address is being reported/used. So create the full
        # range of possible addresses, and see what we find in the device registry,
        # on the *assumption* that there won't be overlap between devices.
        for offset in range(-3, 3):
            if (altmac := mac_math_offset(self.address, offset)) is not None:
                connlist.add(("bluetooth", altmac.upper()))
                connlist.add(("mac", altmac))
                maclist.add(altmac)

        # Requires 2025.3
        devreg_devices = self._coordinator.dr.devices.get_entries(None, connections=connlist)
        devreg_count = 0  # can't len() an iterable.
        devreg_stringlist = ""  # for debug logging
        for devreg_device in devreg_devices:
            devreg_count += 1
            # _LOGGER.debug("DevregScanner: %s", devreg_device)
            devreg_stringlist += f"** {devreg_device.name_by_user or devreg_device.name}\n"
            for conn in devreg_device.connections:
                if conn[0] == "bluetooth":
                    # Bluetooth component's device!
                    scanner_devreg_bt = devreg_device
                    scanner_devreg_bt_address = conn[1].lower()
                if conn[0] == "mac":
                    # ESPHome, Shelly
                    scanner_devreg_mac = devreg_device
                    scanner_devreg_mac_address = conn[1]

        if devreg_count not in (1, 2, 3):
            # We expect just the bt, or bt and another like esphome/shelly, or
            # two bt's and shelly/esphome, the second bt being the alternate
            # MAC address.
            _LOGGER_SPAM_LESS.warning(
                f"multimatch_devreg_{self._hascanner.source}",
                "Unexpectedly got %d device registry matches for %s: %s\n",
                devreg_count,
                self._hascanner.name,
                devreg_stringlist,
            )

        if scanner_devreg_bt is None and scanner_devreg_mac is None:
            _LOGGER_SPAM_LESS.error(
                f"scanner_not_in_devreg_{self.address:s}",
                "Failed to find scanner %s (%s) in Device Registry",
                self._hascanner.name,
                self._hascanner.source,
            )
            return

        # We found the device entry and have created our scannerdevice,
        # now update any fields that might be new from the device reg.
        # First clear the existing to make prioritising the bt/mac matches
        # easier (feel free to refactor, bear in mind we prefer bt first)
        _area_id = None

        _bt_name = None
        _mac_name = None
        _bt_name_by_user = None
        _mac_name_by_user = None

        if scanner_devreg_bt is not None:
            _area_id = scanner_devreg_bt.area_id
            self.entry_id = scanner_devreg_bt.id
            _bt_name_by_user = scanner_devreg_bt.name_by_user
            _bt_name = scanner_devreg_bt.name
        if scanner_devreg_mac is not None:
            # Only apply if the bt device entry hasn't been applied:
            _area_id = _area_id or scanner_devreg_mac.area_id
            self.entry_id = self.entry_id or scanner_devreg_mac.id
            _mac_name = scanner_devreg_mac.name
            _mac_name_by_user = scanner_devreg_mac.name_by_user

        # As of ESPHome 2025.3.0 (via aioesphomeapi 29.3.1) ESPHome proxies now
        # report their BLE MAC address instead of their WIFI MAC in the hascanner
        # details.
        # To work around breaking the existing distance_to entities, retain the
        # ESPHome / Shelly integration's MAC as the unique_id
        self.unique_id = scanner_devreg_mac_address or scanner_devreg_bt_address or self._hascanner.source
        self.address_ble_mac = scanner_devreg_bt_address or scanner_devreg_mac_address or self._hascanner.source
        self.address_wifi_mac = scanner_devreg_mac_address

        # Populate the possible metadevice source MACs so that we capture any
        # data the scanner is sending (Shelly's already send broadcasts, and
        # future ESPHome Bermuda templates will, too). We can't easily tell
        # if our base address is the wifi mac, ble mac or ether mac, so whack
        # 'em all in and let the loop sort it out.
        for mac in (
            self.address_ble_mac,  # BLE mac, if known
            mac_math_offset(self.address_wifi_mac, 2),  # WIFI+2=BLE
            mac_math_offset(self.address_wifi_mac, -1),  # ETHER-1=BLE
        ):
            if (
                mac is not None
                and mac not in self.metadevice_sources
                and mac != self.address  # because it won't need to be a metadevice
            ):
                self.metadevice_sources.append(mac)

        # Bluetooth integ names scanners by address, so prefer the source integration's
        # autogenerated name over that.
        self.name_devreg = _mac_name or _bt_name
        # Bluetooth device reg is newer, so use the user-given name there if it exists.
        self.name_by_user = _bt_name_by_user or _mac_name_by_user
        # Apply any name changes.
        self.make_name()

        self._update_area_and_floor(_area_id)

    def _update_area_and_floor(self, area_id: str | None):
        """Given an area_id, update the area and floor properties."""
        if area_id is None:
            self.area = None
            self.area_id = None
            self.area_name = None
            self.area_icon = ICON_DEFAULT_AREA
            self.floor = None
            self.floor_id = None
            self.floor_name = None
            self.floor_icon = ICON_DEFAULT_FLOOR
            self.floor_level = None
            return

        # Look up areas
        if area := self.ar.async_get_area(area_id):
            self.area = area
            self.area_id = area_id
            self.area_name = area.name
            self.area_icon = area.icon or ICON_DEFAULT_AREA
            self.floor_id = area.floor_id
            if self.floor_id is not None:
                self.floor = self.fr.async_get_floor(self.floor_id)
                if self.floor is not None:
                    self.floor_name = self.floor.name
                    self.floor_icon = self.floor.icon or ICON_DEFAULT_FLOOR
                    self.floor_level = self.floor_level
                else:
                    # floor_id was invalid
                    _LOGGER_SPAM_LESS.warning(
                        f"floor_id invalid for {self.__repr__()}",
                        "Update of area for %s has invalid floor_id of %s",
                        self.__repr__(),
                        self.floor_id,
                    )
                    self.floor_id = None
                    self.floor_name = "Invalid Floor ID"
                    self.floor_icon = ICON_DEFAULT_FLOOR
                    self.floor_level = None
            else:
                # Floor_id is none
                self.floor = None
                self.floor_name = None
                self.floor_icon = ICON_DEFAULT_FLOOR
        else:
            _LOGGER_SPAM_LESS.warning(
                f"no_area_on_update{self.name}",
                "Setting area of %s with invalid area id of %s",
                self.__repr__(),
                area_id,
            )
            self.area = None
            self.area_name = f"Invalid Area for {self.name}"
            self.area_icon = ICON_DEFAULT_AREA
            self.floor = None
            self.floor_id = None
            self.floor_name = None
            self.floor_icon = ICON_DEFAULT_FLOOR

    def async_as_scanner_update(self, ha_scanner: BaseHaScanner):
        """
        Fast update of scanner details per update-cycle.

        Typically only performs fast-update tasks (like refreshing the stamps list)
        but if a new ha_scanner is passed it will first call the init function. This
        can be avoided by separately re-calling async_as_scanner_init() first.
        """
        if self._hascanner is not ha_scanner:
            # The ha_scanner instance is new or we never had one, let's [re]init ourselves.
            if self._hascanner is not None:
                # Ordinarily we'd expect init to have been called first, so...
                _LOGGER.info("Received replacement ha_scanner object for %s", self.__repr__)
            self.async_as_scanner_init(ha_scanner)

        # This needs to be recalculated each run, since we don't have access to _last_update
        # and need to use a derived value rather than reference.
        scannerstamp = 0 - ha_scanner.time_since_last_detection() + monotonic_time_coarse()
        if scannerstamp > self.last_seen:
            self.last_seen = scannerstamp
        elif self.last_seen - scannerstamp > 0.8:  # For some reason small future-offsets are common.
            _LOGGER.debug(
                "Scanner stamp for %s went backwards %.2fs. new %f < last %f",
                self.name,
                self.last_seen - scannerstamp,
                scannerstamp,
                self.last_seen,
            )

        # Populate the local copy of timestamps, if applicable
        # Only Remote ha scanners provide explicit timestamps...
        if self.is_remote_scanner:
            # Set typing ignore to avoid cost of an if isinstance, since is_remote_scanner already implies
            # that ha_scanner is a BaseHaRemoteScanner.
            # New API in 2025.4.0
            if self._coordinator.hass_version_min_2025_4:
                self.stamps = self._hascanner.discovered_device_timestamps  # type: ignore
            else:
                # pylint: disable=W0212,C0301
                self.stamps = self._hascanner._discovered_device_timestamps  # type: ignore # noqa: SLF001

    def async_as_scanner_get_stamp(self, address: str) -> float | None:
        """
        Returns the latest known timestamp for the given address from this scanner.

        Does *not* pull directly from backend, but will be current as at the
        last update cycle as the data is copied in at that time. Returns None
        if the scanner has no current stamp for that device or if the scanner
        itself does not provide stamps (such as usb Bluetooth / BlueZ devices).
        """
        if self.is_remote_scanner:
            if self.stamps is None:
                _LOGGER_SPAM_LESS.debug(
                    f"remote_no_stamps{self.address}", "Remote Scanner %s has no stamps dict", self.__repr__()
                )
                return None
            if len(self.stamps) == 0:
                _LOGGER_SPAM_LESS.debug(
                    f"remote_stamps_empty{self.address}", "Remote scanner %s has an empty stamps dict", self.__repr__()
                )
                return None
            try:
                return self.stamps[address.upper()]
            except (KeyError, AttributeError):
                # No current record, device might have "stale"d out.
                return None
        # Probably a usb / BlueZ device.
        return None

    @callback
    def async_handle_pble_callback(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """
        If this is an IRK device, this callback will be called on IRK updates.

        This method gets registered with core's Private BLE Device integration,
        and will be called each time that its co-ordinator sees a new MAC address
        for this IRK.
        """
        address = mac_norm(service_info.address)
        if address not in self.metadevice_sources:
            self.metadevice_sources.insert(0, address)
            _LOGGER.debug("Got %s callback for new IRK address on %s of %s", change, self.name, address)
            # Add the new mac/irk pair to our internal tracker so we don't spend
            # time calculating it on the update. Be wary of causing a loop here, should
            # be fine because our irk_manager will only fire another callback if the mac is new.
            self._coordinator.irk_manager.add_macirk(address, bytes.fromhex(self.address))

    def make_name(self):
        """
        Refreshes self.name, sets and returns it, based on naming preferences.

        Will prefer the friendly names sent by bluetooth advert, but will fall back
        to manufacturer name and bluetooth address.
        """
        _newname = (
            self.name_by_user
            or self.name_devreg
            or self.name_bt_local_name
            or self.name_bt_serviceinfo
            or self.beacon_unique_id
        )

        if _newname is not None:
            self.name = _newname
        elif self.address_type != BDADDR_TYPE_NOT_MAC48:
            # Couldn't find anything nice, we'll have to use the address.
            # At least see if we can prefix it with manufacturer name
            if self.manufacturer:
                _prefix = f"{slugify(self.manufacturer)}"
            else:
                _prefix = DOMAIN
            self.name = f"{_prefix}_{slugify(self.address)}"

        return self.name

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
            for advert in self.adverts.values():
                rawdist = advert.set_ref_power(new_ref_power)
                if rawdist is not None and rawdist < nearest_distance:
                    nearest_distance = rawdist
                    nearest_scanner = advert
            # Even though the actual scanner should not have changed (it should
            # remain none or a given scanner, since the relative distances won't have
            # changed due to ref_power), we still call apply so that the new area_distance
            # gets applied.
            # if nearest_scanner is not None:
            self.apply_scanner_selection(nearest_scanner)
            # Update the stamp so that the BermudaEntity can clear the cache and show the
            # new measurement(s) immediately.
            self.ref_power_changed = monotonic_time_coarse()

    def apply_scanner_selection(self, bermuda_advert: BermudaAdvert | None):
        """
        Given a BermudaAdvert entry, apply the distance and area attributes
        from it to this device.

        Used to apply a "winning" scanner's data to the device for setting closest Area.
        """
        old_area = self.area_name
        if bermuda_advert is not None and bermuda_advert.rssi_distance is not None:
            # We found a winner
            self.area_advert = bermuda_advert
            self._update_area_and_floor(bermuda_advert.area_id)
            self.area_distance = bermuda_advert.rssi_distance
            self.area_rssi = bermuda_advert.rssi
            self.area_last_seen = self.area_name
            self.area_last_seen_id = self.area_id
            self.area_last_seen_icon = self.area_icon
        else:
            # Not close to any scanners, or closest scanner has timed out!
            self.area_advert = None
            self._update_area_and_floor(None)
            self.area_distance = None
            self.area_rssi = None

        if (old_area != self.area_name) and self.create_sensor:
            _LOGGER.debug(
                "Device %s was in '%s', now '%s'",
                self.name,
                old_area,
                self.area_name,
            )

    def get_scanner(self, scanner_address) -> BermudaAdvert | None:
        """
        Given a scanner address, return the most recent BermudaDeviceScanner (advert) that matches.

        This is required as the list of device.scanners is keyed by [address, scanner], and
        a device might switch back and forth between multiple addresses.
        """
        _stamp = 0
        _found_scanner = None
        for advert in self.adverts.values():
            if advert.scanner_address == scanner_address:
                # we have matched the scanner, but is it the most recent address?
                if _stamp == 0 or (advert.stamp is not None and advert.stamp > _stamp):
                    _found_scanner = advert
                    _stamp = _found_scanner.stamp or 0

        return _found_scanner

    def calculate_data(self):
        """
        Call after doing update_scanner() calls so that distances
        etc can be freshly smoothed and filtered.

        """
        # Run calculate_data on each child scanner of this device:
        for advert in self.adverts.values():
            if isinstance(advert, BermudaAdvert):
                # in issue #355 someone had an empty dict instead of a scanner object.
                # it may be due to a race condition during startup, but we check now
                # just in case. Was not able to reproduce.
                advert.calculate_data()
            else:
                _LOGGER_SPAM_LESS.error(
                    "scanner_not_instance", "Scanner device is not a BermudaDevice instance, skipping."
                )

        # Update whether this device has been seen recently, for device_tracker:
        if (
            self.last_seen is not None
            and monotonic_time_coarse() - self.options.get(CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT)
            < self.last_seen
        ):
            self.zone = STATE_HOME
        else:
            self.zone = STATE_NOT_HOME

        if self.address.upper() in self.options.get(CONF_DEVICES, []):
            # We are a device we track. Flag for set-up:
            self.create_sensor = True

    def process_advertisement(self, scanner_device: BermudaDevice, advertisementdata: AdvertisementData):
        """
        Add/Update a scanner/advert entry pair on this device, indicating a received advertisement.

        This gets called every time a scanner is deemed to have received an advert for
        this device. It only loads data into the structure, all calculations are done
        with calculate_data()

        """
        scanner_address = mac_norm(scanner_device.address)
        device_address = self.address
        # Ensure this is used for referencing self.scanners[], as self.address might point elsewhere!
        advert_tuple = (device_address, scanner_address)

        if len(self.metadevice_sources) > 0 and not self._is_scanner:
            # If we're a metadevice we should never be in this function,
            # unless we _used_ to be a scanner but are no longer. Shelly proxies
            # seem to do this when they go offline. See #608
            _LOGGER_SPAM_LESS.debug(
                f"meta_{self.address}_{advert_tuple}",
                "process_advertisement called on a metadevice (%s) - probably a dead proxy. Advert tuple: (%s)",
                self.__repr__(),
                advert_tuple,
            )
            return

        if advert_tuple in self.adverts:
            # Device already exists, update it
            self.adverts[advert_tuple].update_advertisement(advertisementdata, scanner_device)
            device_advert = self.adverts[advert_tuple]
        else:
            # Create it
            device_advert = self.adverts[advert_tuple] = BermudaAdvert(
                self,
                advertisementdata,
                self.options,
                scanner_device,
            )

        # Let's see if we should update our last_seen based on this...
        if device_advert.stamp is not None and self.last_seen < device_advert.stamp:
            self.last_seen = device_advert.stamp

    def process_manufacturer_data(self, advert: BermudaAdvert):
        """Parse manufacturer data for maker name and iBeacon etc."""
        # Only override existing manufacturer name if it's "better"

        # ==== Check service uuids (type 0x16)
        _want_name_update = False
        for uuid in advert.service_uuids:
            name, generic = self._coordinator.get_manufacturer_from_id(uuid[4:8])
            # We'll use the name if we don't have one already, or if it's non-generic.
            if name and (self.manufacturer is None or not generic):
                self.manufacturer = name
                _want_name_update = True
        if _want_name_update:
            self.make_name()

        # ==== Check manfuacturer data (type 0xFF)
        for manudict in advert.manufacturer_data:
            for company_code, man_data in manudict.items():
                name, generic = self._coordinator.get_manufacturer_from_id(company_code)
                if name and (self.manufacturer is None or not generic):
                    self.manufacturer = name

                if company_code == 0x004C:  # 76 Apple Inc
                    if man_data[:1] == b"\x02":  # iBeacon: Almost always 0x0215, but 0x15 is the length part
                        # iBeacon / UUID Support

                        # Bermuda supports iBeacons by creating a "metadevice", which
                        # looks just like any other Bermuda device, but its address is
                        # the iBeacon full uuid_maj_min and it has helpers that gather
                        # together the advertisements from a set of source_devices - this
                        # device instance is about to become just such a metadevice.source_device

                        # At least one(!) iBeacon out there sends only 22 bytes (it has no tx_power field)
                        # which is weird. So Let's just decode what we can that exists, and blindly proceed
                        # otherwise. We could reject it, but it can still be useful, so...
                        if len(man_data) >= 22:
                            # Proper iBeacon packet has 23 bytes.
                            self.metadevice_type.add(METADEVICE_TYPE_IBEACON_SOURCE)
                            self.beacon_uuid = man_data[2:18].hex().lower()
                            self.beacon_major = str(int.from_bytes(man_data[18:20], byteorder="big"))
                            self.beacon_minor = str(int.from_bytes(man_data[20:22], byteorder="big"))
                        if len(man_data) >= 23:
                            # There really is at least one out there that lacks this! See #466
                            self.beacon_power = int.from_bytes([man_data[22]], signed=True)

                        # The irony of adding major/minor is that the
                        # UniversallyUniqueIDentifier is not even unique
                        # locally, so we need to make one :-)

                        self.beacon_unique_id = f"{self.beacon_uuid}_{self.beacon_major}_{self.beacon_minor}"
                        # Note: it's possible that a device sends multiple
                        # beacons. We are only going to process the latest
                        # one in any single update cycle, so we ignore that
                        # possibility for now. Given we re-process completely
                        # each cycle it should *just work*, for the most part.

                        # Create a metadevice for this beacon. Metadevices get updated
                        # after all adverts are processed and distances etc are calculated
                        # for the sources.
                        self.make_name()
                        self._coordinator.register_ibeacon_source(self)

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices."""
        out = {}
        for var, val in vars(self).items():
            if val is None:
                # Catch the Nones first, as otherwise they might match some other objects below if
                # they are None (like self._hascanner), which will prevent them showing at all.
                out[var] = val
                continue
            if val in [self._coordinator, self.floor, self.area, self.ar, self.fr]:
                # Objects to ignore completely.
                continue
            if val in [self._hascanner, self.area, self.floor, self.ar, self.fr]:
                if hasattr(val, "__repr__"):
                    out[var] = val.__repr__()
                continue
            if val is self.adverts:
                advertout = {}
                for advert in self.adverts.values():
                    advertout[f"{advert.device_address}__{advert.scanner_address}"] = advert.to_dict()
                out[var] = advertout
                continue
            out[var] = val
        return out

    def __repr__(self) -> str:
        """Help debug devices and figure out what device it is at a glance."""
        return f"{self.name} [{self.address}]"
