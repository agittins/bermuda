"""
Device-as-scanner aspect of a BermudaDevice (mixin).

Split out of bermuda_device.py: the methods used when a device is itself a
Bluetooth scanner/proxy (registry resolution, area/floor, stamps).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components.bluetooth import (
    BaseHaRemoteScanner,
    BaseHaScanner,
)

from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    AREA_NAME_UNKNOWN,
    ICON_DEFAULT_AREA,
    ICON_DEFAULT_FLOOR,
)
from .util import mac_math_offset

if TYPE_CHECKING:
    # Imported directly (not as the `ar`/`fr`-aliased modules) so these type-only
    # names don't shadow the `ar`/`fr` attributes declared below.
    from homeassistant.helpers.area_registry import AreaEntry, AreaRegistry
    from homeassistant.helpers.floor_registry import FloorEntry, FloorRegistry

    from .bermuda_device import BermudaDevice
    from .coordinator import BermudaDataUpdateCoordinator


class BermudaScannerDeviceMixin:
    """Scanner-device behaviour, mixed into BermudaDevice."""

    if TYPE_CHECKING:
        # Attributes/methods provided by BermudaDevice, the concrete class this
        # mixin is always combined into (see BermudaDevice.__init__). Declared
        # here only so mypy can see them; nothing in this block runs at import time.
        address: str
        name: str
        ar: AreaRegistry
        fr: FloorRegistry
        _coordinator: BermudaDataUpdateCoordinator
        _hascanner: BaseHaRemoteScanner | BaseHaScanner | None
        _is_scanner: bool
        _is_remote_scanner: bool | None
        stamps: dict[str, float]
        metadevice_sources: list[str]
        entry_id: str | None
        scanner_entity_id: str | None
        address_ble_mac: str
        address_wifi_mac: str | None
        unique_id: str | None
        name_devreg: str | None
        name_by_user: str | None
        area: AreaEntry | None
        area_id: str | None
        area_name: str | None
        area_icon: str
        area_is_unknown: bool
        floor: FloorEntry | None
        floor_id: str | None
        floor_name: str | None
        floor_icon: str
        floor_level: int | None
        last_seen: float

        def make_name(self) -> str:
            """Declared for mypy; implemented by BermudaDevice."""
            ...

    @property
    def is_scanner(self) -> bool:
        """Whether this device is currently registered as a Home Assistant Bluetooth scanner."""
        return self._is_scanner

    @property
    def is_remote_scanner(self) -> bool | None:
        """Whether this device is a remote scanner that reports explicit advertisement timestamps."""
        return self._is_remote_scanner

    def async_as_scanner_nolonger(self) -> None:
        """Call when this device is unregistered as a BaseHaScanner."""
        self._is_scanner = False
        self._is_remote_scanner = False
        # self is always a BermudaDevice at runtime (the mixin is only ever combined
        # with it); mypy can't see that from within the mixin's own class body.
        self._coordinator.scanner_list_del(cast("BermudaDevice", self))

    def async_as_scanner_init(self, ha_scanner: BaseHaScanner) -> None:
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
        self._coordinator.scanner_list_add(cast("BermudaDevice", self))

        # Find the relevant device entries in HA for this scanner and apply the names, addresses etc
        self.async_as_scanner_resolve_device_entries()

        # Call the per-update processor as well, but only
        # if this is our first ha_scanner.
        # This is because we must avoid an infinite loop in the case
        # where the scanner_update might call us.
        if _first_init:
            self.async_as_scanner_update(ha_scanner)

    def async_as_scanner_resolve_device_entries(self) -> None:
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

        connlist = set()  # For matching against device_registry connections
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

        # Resolve a representative HA entity_id for this scanner's device (e.g. a
        # Shelly switch/light), so a tracked device can expose its nearest scanner's
        # entity_id for downstream automations (labels, custom attributes, etc.).
        self.scanner_entity_id = None
        if self.entry_id is not None:
            scanner_entities = list(self._coordinator.er.entities.get_entries_for_device_id(self.entry_id))
            if scanner_entities:
                preferred = [e for e in scanner_entities if e.domain in ("switch", "light")]
                self.scanner_entity_id = (preferred or scanner_entities)[0].entity_id

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

    def _update_area_and_floor(self, area_id: str | None, *, force_unknown: bool = False) -> None:
        """
        Given an area_id, update the area and floor properties.

        ``force_unknown`` reports the explicit "Unknown" area name (evidence too weak
        to place the device) rather than None (which the entities map to not_home).
        """
        self.area_is_unknown = force_unknown and area_id is None
        if area_id is None:
            self.area = None
            self.area_id = None
            self.area_name = AREA_NAME_UNKNOWN if force_unknown else None
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
                    self.floor_level = self.floor.level
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

    def async_as_scanner_update(self, ha_scanner: BaseHaScanner) -> None:
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
                _LOGGER.info("Received replacement ha_scanner object for %s", self)
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
            # that ha_scanner is a BaseHaRemoteScanner (hence non-None here too).
            self.stamps = self._hascanner.discovered_device_timestamps  # type: ignore[union-attr]

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
            except KeyError, AttributeError:
                # No current record, device might have "stale"d out.
                return None
        # Probably a usb / BlueZ device.
        return None
