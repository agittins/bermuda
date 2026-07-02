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
from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.private_ble_device import coordinator as pble_coordinator
from homeassistant.const import CONF_NAME, STATE_HOME, STATE_NOT_HOME
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import floor_registry as fr
from homeassistant.util import slugify

from .bermuda_advert import BermudaAdvert
from .bermuda_device_scanner import BermudaScannerDeviceMixin
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESERVED,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    BDADDR_TYPE_RANDOM_STATIC,
    BDADDR_TYPE_RANDOM_UNRESOLVABLE,
    BDADDR_TYPE_UNKNOWN,
    CATEGORY_IBEACON,
    CATEGORY_IRK,
    CATEGORY_NAMED,
    CATEGORY_PUBLIC,
    CATEGORY_RANDOM,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_EXCLUDE_DEVICES,
    CONF_REF_POWER,
    CONF_TRACK_CATEGORIES,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MOBILITY_TYPE,
    DOMAIN,
    ICON_DEFAULT_AREA,
    ICON_DEFAULT_FLOOR,
    IN100_PAYLOAD_LEN,
    MANUFACTURER_ID_INPLAY,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_PRIVATE_BLE_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    MOBILITY_MOVING,
    MOBILITY_OPTIONS,
    VENDOR_CATEGORIES,
)
from .util import mac_norm

if TYPE_CHECKING:
    from bleak.backends.scanner import AdvertisementData
    from homeassistant.components.bluetooth import (
        BaseHaRemoteScanner,
        BaseHaScanner,
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )

    from .coordinator import BermudaDataUpdateCoordinator


class BermudaDevice(BermudaScannerDeviceMixin):
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
        # Not annotated Final: BermudaScannerDeviceMixin (a base class, mixed in below)
        # also declares this attribute, and mypy disallows a subclass narrowing an
        # inherited attribute to Final. Runtime behaviour is unaffected either way, as
        # CPython does not enforce `Final`.
        self.address: str = _address
        self.address_ble_mac: str = _address
        self.address_wifi_mac: str | None = None
        # We use a weakref to avoid any possible GC issues (only likely if we add a __del__ method, but *shrug*)
        self._coordinator: BermudaDataUpdateCoordinator = coordinator
        self.ref_power: float = 0  # If non-zero, use in place of global ref_power.
        self.ref_power_changed: float = 0  # Stamp for last change to ref_power, for cache zapping.
        self.options = self._coordinator.options
        # Per-device enrolment from a "device" subentry (authoritative at setup; the
        # ref_power number entity may still tweak it live within the session).
        self.name_subentry: str | None = None
        _dc = getattr(self._coordinator, "device_config", None)
        _device_cfg = _dc.get(_address.upper()) if isinstance(_dc, dict) else None
        if _device_cfg:
            if _device_cfg.get(CONF_REF_POWER):
                self.ref_power = _device_cfg[CONF_REF_POWER]
            self.name_subentry = _device_cfg.get(CONF_NAME) or None
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

        # Mobility-aware area resolution: the device's mobility mode (set by the
        # mobility select), whether the area is the explicit "Unknown" outcome, and
        # the rolling decision state used to smooth area switches (lazily created by
        # the trilateration module so this stays decoupled from it).
        self.mobility_type: str = DEFAULT_MOBILITY_TYPE
        self.area_is_unknown: bool = False
        self.area_decision_state: Any = None

        # Micro-location (sub-area RF fingerprint match): a named spot like "Key hook",
        # finer than an Area, set by the coordinator each cycle when a saved fingerprint
        # for this device matches confidently.
        self.micro_location_id: str | None = None
        self.micro_location_name: str | None = None
        self.micro_location_confidence: float | None = None
        self.micro_location_last_seen: str | None = None
        # (candidate_id, consecutive_cycles) used for switch hysteresis.
        self.micro_location_streak: tuple[str | None, int] = (None, 0)

        self.floor: fr.FloorEntry | None = None
        self.floor_id: str | None = None
        self.floor_name: str | None = None
        self.floor_icon: str = ICON_DEFAULT_FLOOR
        self.floor_level: int | None = None  # matches FloorEntry.level (int | None); was mistyped as str | None

        self.zone: str = STATE_NOT_HOME  # STATE_HOME or STATE_NOT_HOME
        self.manufacturer: str | None = None
        self.manufacturer_id: int | None = None  # Bluetooth SIG company id, for vendor categorisation
        # InPlay IN100 / DFRobot Fermion telemetry (manufacturer data 0x0505).
        self.in100_detected: bool = False
        self.in100_vcc: float | None = None
        self.in100_temp_c: float | None = None
        self.in100_adc_voltage: float | None = None
        self.in100_raw_payload_hex: str | None = None
        self.in100_last_payload_len: int | None = None
        self.create_in100_done: bool = False  # IN100 telemetry sensors have been spun up
        self._hascanner: BaseHaRemoteScanner | BaseHaScanner | None = None  # HA's scanner
        self._is_scanner: bool = False
        self._is_remote_scanner: bool | None = None
        self.stamps: dict[str, float] = {}
        self.metadevice_type: set[str] = set()
        self.metadevice_sources: list[str] = []  # list of MAC addresses that have/should match this beacon
        self.beacon_unique_id: str | None = None  # combined uuid_major_minor for *really* unique id
        self.beacon_uuid: str | None = None
        self.beacon_major: str | None = None
        self.beacon_minor: str | None = None
        self.beacon_power: float | None = None

        self.entry_id: str | None = None  # used for scanner devices
        self.scanner_entity_id: str | None = None  # if this device is a scanner: its HA switch/light entity_id
        self.create_sensor: bool = False  # Create/update a sensor for this device
        self.create_sensor_done: bool = False  # Sensor should now exist
        self.create_tracker_done: bool = False  # device_tracker should now exist
        self.create_number_done: bool = False
        self.create_select_done: bool = False
        self.create_all_done: bool = False  # All platform entities are done and ready.
        self.last_seen: float = 0  # stamp from most recent scanner spotting. monotonic_time_coarse
        self.diag_area_switch: str | None = None  # saves the full AreaTests diagnostic dump
        self.diag_area_switch_reason: str | None = None  # saves the concise AreaTests reason string
        self.adverts: dict[
            tuple[str, str], BermudaAdvert
        ] = {}  # str will be a scanner address OR a deviceaddress__scanneraddress
        self._async_process_address_type()

    def _async_process_address_type(self) -> None:
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
                    # self.address is the raw IRK here; log only a short prefix (key material).
                    _LOGGER.debug("Private BLE Callback registered for %s (irk %s…)", self.name, self.address[:4])
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
                if (top_bits & 0b11) == 0b00:  # First char will be in [0 1 2 3]
                    self.address_type = BDADDR_TYPE_RANDOM_UNRESOLVABLE
                elif (top_bits & 0b11) == 0b01:  # Addresses where the first char will be 4,5,6 or 7
                    self.address_type = BDADDR_TYPE_RANDOM_RESOLVABLE
                    self._coordinator.irk_manager.check_mac(self.address)
                elif (top_bits & 0b11) == 0b10:
                    self.address_type = BDADDR_TYPE_RANDOM_RESERVED
                elif (top_bits & 0b11) == 0b11:
                    self.address_type = BDADDR_TYPE_RANDOM_STATIC

            else:
                # Fallback for any other colon-form address shape.
                # (No OUI->manufacturer lookup here: the SIG tables are keyed by
                # 16-bit company IDs, so a 24-bit OUI prefix never matches.)
                self.address_type = BDADDR_TYPE_OTHER

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

    def make_name(self) -> str:
        """
        Refreshes self.name, sets and returns it, based on naming preferences.

        Will prefer the friendly names sent by bluetooth advert, but will fall back
        to manufacturer name and bluetooth address.
        """
        _newname = (
            self.name_by_user  # an explicit Home Assistant rename always wins
            or self.name_subentry  # then a per-device enrolment subentry name
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

    def set_ref_power(self, new_ref_power: float) -> None:
        """
        Set a new reference power for this device and immediately apply
        an interim distance calculation.

        This gets called by the calibration routines, but also by metadevice
        updates, as they need to apply their own ref_power if necessary.
        """
        if new_ref_power != self.ref_power:
            # it's actually changed, proceed...
            self.ref_power = new_ref_power
            nearest_distance: float = 9999  # running tally to find closest scanner
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

    def get_mobility_type(self) -> str:
        """Return the validated mobility mode (moving/stationary)."""
        if self.mobility_type in MOBILITY_OPTIONS:
            return self.mobility_type
        return MOBILITY_MOVING

    def set_mobility_type(self, mobility_type: str | None) -> None:
        """Set the mobility mode, falling back to the default if invalid."""
        self.mobility_type = mobility_type if mobility_type in MOBILITY_OPTIONS else DEFAULT_MOBILITY_TYPE

    @property
    def category(self) -> str:
        """
        Single ESPresense-style fingerprint for this device.

        Precedence: iBeacon → IRK (resolved private BLE) → known vendor (by company
        id) → named (has an advertised local name) → random MAC → public MAC. Used to
        group the discovery list and to track whole classes of device at once.
        """
        if self.address_type == ADDR_TYPE_IBEACON:
            return CATEGORY_IBEACON
        if self.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
            return CATEGORY_IRK
        vendor = VENDOR_CATEGORIES.get(self.manufacturer_id) if self.manufacturer_id is not None else None
        if vendor is not None:
            return vendor
        if self.name_bt_local_name:
            return CATEGORY_NAMED
        if self.address_type in (
            BDADDR_TYPE_RANDOM_RESOLVABLE,
            BDADDR_TYPE_RANDOM_UNRESOLVABLE,
            BDADDR_TYPE_RANDOM_STATIC,
            BDADDR_TYPE_RANDOM_RESERVED,
        ):
            return CATEGORY_RANDOM
        return CATEGORY_PUBLIC

    def apply_scanner_selection(self, bermuda_advert: BermudaAdvert | None, *, force_unknown: bool = False) -> None:
        """
        Given a BermudaAdvert entry, apply the distance and area attributes
        from it to this device.

        Used to apply a "winning" scanner's data to the device for setting closest Area.
        ``force_unknown`` reports the explicit "Unknown" area (evidence too weak to
        place the device) rather than not_home (device not seen at all).
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
            self._update_area_and_floor(None, force_unknown=force_unknown)
            self.area_distance = None
            self.area_rssi = None

        if (old_area != self.area_name) and self.create_sensor:
            _LOGGER.debug(
                "Device %s was in '%s', now '%s'",
                self.name,
                old_area,
                self.area_name,
            )

    def apply_area_override(self, area_id: str, distance: float) -> None:
        """
        Force this device's area from an external presence entity (not a BLE scanner).

        Used by the area-entity override: the area comes from a triggered HA entity at
        a virtual distance, so there is no winning advert (area_advert is cleared).
        """
        self._update_area_and_floor(area_id)
        self.area_distance = distance
        self.area_rssi = 0
        self.area_advert = None

    def get_scanner(self, scanner_address: str) -> BermudaAdvert | None:
        """
        Given a scanner address, return the most recent BermudaDeviceScanner (advert) that matches.

        This is required as the list of device.scanners is keyed by [address, scanner], and
        a device might switch back and forth between multiple addresses.
        """
        _stamp = 0.0
        _found_scanner = None
        for advert in self.adverts.values():
            if advert.scanner_address == scanner_address:
                # Keep the most recent matching advert. Use _found_scanner (not a
                # zero stamp) as the "first match" sentinel, otherwise a matched
                # advert with stamp 0/None makes every later match win regardless.
                advert_stamp = advert.stamp or 0.0
                if _found_scanner is None or advert_stamp > _stamp:
                    _found_scanner = advert
                    _stamp = advert_stamp

        return _found_scanner

    def calculate_data(self) -> None:
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

        # Update whether this device has been seen recently, for device_tracker.
        # A "device" enrolment subentry may override the global away timeout per-device.
        _dc = getattr(self._coordinator, "device_config", None)
        _dev_cfg = _dc.get(self.address.upper(), {}) if isinstance(_dc, dict) else {}
        _timeout = _dev_cfg.get(CONF_DEVTRACK_TIMEOUT) or self.options.get(
            CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT
        )
        if self.last_seen is not None and monotonic_time_coarse() - _timeout < self.last_seen:
            self.zone = STATE_HOME
        else:
            self.zone = STATE_NOT_HOME

        addr = self.address.upper()
        if addr not in self.options.get(CONF_EXCLUDE_DEVICES, []) and (
            addr in self.options.get(CONF_DEVICES, []) or self.category in self.options.get(CONF_TRACK_CATEGORIES, [])
        ):
            # Tracked either explicitly (configured_devices) or by category
            # (track_categories), and not on the exclusion denylist. Flag for set-up.
            self.create_sensor = True

    def process_advertisement(self, scanner_device: BermudaDevice, advertisementdata: AdvertisementData) -> None:
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

    def process_manufacturer_data(self, advert: BermudaAdvert) -> None:
        """Parse manufacturer data for maker name and iBeacon etc."""
        # Only override existing manufacturer name if it's "better"

        # ==== Check service uuids (type 0x16)
        _want_name_update = False
        for uuid in advert.service_uuids:
            # Extract UUID short form - try both full UUID format and short format
            uuid_str = str(uuid).upper()

            # Try to extract the 16-bit UUID from the full format
            if len(uuid_str) >= 8:
                uuid_short = uuid_str[4:8]
            else:
                uuid_short = uuid_str

            name, generic = self._coordinator.get_manufacturer_from_id(uuid_short)
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
                    self.manufacturer_id = company_code

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

        # Decode InPlay IN100 / DFRobot Fermion telemetry (manufacturer data 0x0505).
        self._parse_in100_telemetry(advert)

    def _parse_in100_telemetry(self, advert: BermudaAdvert) -> None:
        """
        Decode InPlay IN100 / DFRobot Fermion telemetry from manufacturer data 0x0505.

        Only the latest manufacturer-data entry is considered (telemetry is
        time-sensitive); the first five bytes encode supply voltage, temperature and an
        ADC voltage. A short/malformed payload clears the decoded values but still flags
        the device as detected. Ported/adapted from kamilzierke/bermuda.
        """
        latest = advert.manufacturer_data[0] if advert.manufacturer_data else None
        man_data = latest.get(MANUFACTURER_ID_INPLAY) if latest else None
        if man_data is None:
            return

        self.in100_raw_payload_hex = man_data.hex()
        self.in100_last_payload_len = len(man_data)
        self.in100_detected = True

        applied_fallback_name = False
        if self.manufacturer is None:
            self.manufacturer = "InPlay / DFRobot"
            applied_fallback_name = True

        # Reset the decoded values; a short payload leaves them cleared.
        self.in100_vcc = None
        self.in100_temp_c = None
        self.in100_adc_voltage = None

        if len(man_data) >= IN100_PAYLOAD_LEN:
            payload = man_data[:IN100_PAYLOAD_LEN]
            self.in100_vcc = payload[0] / 32.0
            self.in100_temp_c = int.from_bytes(payload[1:3], byteorder="big", signed=True) / 100.0
            self.in100_adc_voltage = int.from_bytes(payload[3:5], byteorder="big", signed=False) / 1000.0

        if applied_fallback_name:
            self.make_name()

    def to_dict(self) -> dict[str, Any]:
        """Convert class to serialisable dict for dump_devices."""
        out: dict[str, Any] = {}
        for var, val in vars(self).items():
            if val is None:
                # Catch the Nones first, as otherwise they might match some other objects below if
                # they are None (like self._hascanner), which will prevent them showing at all.
                out[var] = val
                continue
            if val is self._coordinator or val is self.floor or val is self.area or val is self.ar or val is self.fr:
                # Objects to ignore completely.
                continue
            if val is self._hascanner:
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
