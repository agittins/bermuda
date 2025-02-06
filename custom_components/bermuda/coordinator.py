"""DataUpdateCoordinator for Bermuda bluetooth data."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import voluptuous as vol
import yaml
from habluetooth import BaseHaRemoteScanner, BaseHaScanner
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    MONOTONIC_TIME,
    BluetoothChange,
    BluetoothScannerDevice,
)
from homeassistant.components.bluetooth.api import _get_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.const import MAJOR_VERSION as HA_VERSION_MAJ
from homeassistant.const import MINOR_VERSION as HA_VERSION_MIN
from homeassistant.core import (
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import (
    EVENT_DEVICE_REGISTRY_UPDATED,
    EventDeviceRegistryUpdatedData,
    format_mac,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import slugify
from homeassistant.util.dt import get_age, now

from .bermuda_device import BermudaDevice
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_PRIVATE_RESOLVABLE,
    BEACON_IBEACON_SOURCE,
    BEACON_PRIVATE_BLE_SOURCE,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    CONFDATA_SCANNERS,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DEVICE_TRACKER,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
    HIST_KEEP_COUNT,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_DEFAULT,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_IRK,
    REPAIR_SCANNER_WITHOUT_AREA,
    SAVEOUT_COOLDOWN,
    SIGNAL_DEVICE_NEW,
    UPDATE_INTERVAL,
)
from .util import clean_charbuf

if TYPE_CHECKING:
    from habluetooth import BluetoothServiceInfoBleak
    from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager

    from . import BermudaConfigEntry
    from .bermuda_device_scanner import BermudaDeviceScanner

Cancellable = Callable[[], None]


class BermudaDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Class to manage fetching data from the Bluetooth component.

    Since we are not actually using an external API and only computing local
    data already gathered by the bluetooth integration, the update process is
    very cheap, and the processing process (currently) rather cheap.

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
        entry: BermudaConfigEntry,
    ) -> None:
        """Initialize."""
        self.platforms = []
        self.config_entry = entry

        self.sensor_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        # set some version flags
        self.hass_version_min_2025_2 = HA_VERSION_MAJ > 2025 or (HA_VERSION_MAJ == 2025 and HA_VERSION_MIN >= 2)

        # match/replacement pairs for redacting addresses
        self.redactions: dict[str, str] = {}
        # Any remaining MAC addresses will be replaced with this. We define it here
        # so we can compile it once.
        self._redact_generic_re = re.compile(r"(?P<start>[0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}:){4}(?P<end>[0-9A-Fa-f]{2})")
        self._redact_generic_sub = r"\g<start>:xx:xx:xx:xx:\g<end>"

        self.stamp_last_update: float = 0  # Last time we ran an update, from MONOTONIC_TIME()
        self.stamp_last_prune: float = 0  # When we last pruned device list

        self.member_uuids = {}

        hass.async_add_executor_job(self.load_manufacturer_ids)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

        self._manager: HomeAssistantBluetoothManager = _get_manager(hass)  # instance of the bluetooth manager
        self._hascanners: set[BaseHaScanner]  # Links to the backend scanners
        self._hascanner_timestamps: dict[str, dict[str, float]] = {}  # scanner_address, device_address, stamp

        self._entity_registry = er.async_get(self.hass)
        self._device_registry = dr.async_get(self.hass)

        self._scanners_without_areas: list[str] | None = None  # Tracks any proxies that don't have an area assigned.

        # Track the list of Private BLE devices, noting their entity id
        # and current "last address".
        self.pb_state_sources: dict[str, str | None] = {}

        self.metadevices: dict[str, BermudaDevice] = {}

        self._ad_listener_cancel: Cancellable | None = None

        # Tracks the last stamp that we *actually* saved our config entry. Mostly for debugging,
        # we use a request stamp for tracking our add_job request.
        self.last_config_entry_update: float = 0  # Stamp of last *save-out* of config.data

        # We want to delay the first save-out, since it takes a few seconds for things
        # to stabilise. So set the stamp into the future.
        self.last_config_entry_update_request = MONOTONIC_TIME() + SAVEOUT_COOLDOWN  # Stamp for save-out requests

        self.hass.bus.async_listen(EVENT_STATE_CHANGED, self.handle_state_changes)

        # First time around we freshen the restored scanner info by
        # forcing a scan of the captured info.
        self._do_full_scanner_init = True

        # First time go through the private ble devices to see if there's
        # any there for us to track.
        self._do_private_device_init = True

        # Listen for changes to the device registry and handle them.
        # Primarily for changes to scanners and Private BLE Devices.
        hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, self.handle_devreg_changes)

        self.options = {}

        # TODO: This is only here because we haven't set up migration of config
        # entries yet, so some users might not have this defined after an update.
        self.options[CONF_ATTENUATION] = DEFAULT_ATTENUATION
        self.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        self.options[CONF_MAX_RADIUS] = DEFAULT_MAX_RADIUS
        self.options[CONF_MAX_VELOCITY] = DEFAULT_MAX_VELOCITY
        self.options[CONF_REF_POWER] = DEFAULT_REF_POWER
        self.options[CONF_SMOOTHING_SAMPLES] = DEFAULT_SMOOTHING_SAMPLES
        self.options[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL
        self.options[CONF_RSSI_OFFSETS] = {}

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
                    CONF_MAX_VELOCITY,
                    CONF_REF_POWER,
                    CONF_SMOOTHING_SAMPLES,
                    CONF_RSSI_OFFSETS,
                ):
                    self.options[key] = val

        self.devices: dict[str, BermudaDevice] = {}
        # self.updaters: dict[str, BermudaPBDUCoordinator] = {}
        self._has_purged = False
        self._purge_task = hass.loop.call_soon_threadsafe(hass.async_create_task, self.purge_redactions(hass))
        self.area_reg = ar.async_get(hass)

        # Restore the scanners saved in config entry data. We maintain
        # a list of known scanners so we can
        # restore the sensor states even if we don't have a full set of
        # scanner receipts in the discovery data.
        self.scanner_list: list[str] = []
        if hasattr(entry, "data"):
            for address, saved in entry.data.get(CONFDATA_SCANNERS, {}).items():
                scanner = self._get_or_create_device(address)
                for key, value in saved.items():
                    if key != "options":
                        # We don't restore the options, since they may have changed.
                        # the get_or_create will have grabbed the current ones.
                        setattr(scanner, key, value)
                self.scanner_list.append(address)

        # Register the dump_devices service
        hass.services.async_register(
            DOMAIN,
            "dump_devices",
            self.service_dump_devices,
            vol.Schema(
                {
                    vol.Optional("addresses"): cv.string,
                    vol.Optional("configured_devices"): cv.boolean,
                    vol.Optional("redact"): cv.boolean,
                }
            ),
            SupportsResponse.ONLY,
        )

        # Register to get callbacks on every bluetooth advert received!
        if self.config_entry is not None:
            self.config_entry.async_on_unload(
                bluetooth.async_register_callback(
                    self.hass,
                    self.async_handle_advert,
                    bluetooth.BluetoothCallbackMatcher(connectable=False),
                    bluetooth.BluetoothScanningMode.ACTIVE,
                )
            )

    def load_manufacturer_ids(self):
        """Import yaml file containing manufacturer name mappings."""
        file_path = Path(__file__).parent / "manufacturer_identification" / "member_uuids.yaml"

        with file_path.open("r") as f:
            member_uuids_yaml = yaml.safe_load(f)["uuids"]
        self.member_uuids = {hex(member["uuid"])[2:]: member["name"] for member in member_uuids_yaml}

    @callback
    def handle_state_changes(self, ev: Event[EventStateChangedData]):
        """Watch for new mac addresses on private ble devices and act."""
        if ev.event_type == EVENT_STATE_CHANGED:
            event_entity = ev.data.get("entity_id", "invalid_event_entity")
            if event_entity in self.pb_state_sources:
                # It's a state change of an entity we are tracking.
                new_state = ev.data.get("new_state")
                if new_state:
                    # _LOGGER.debug("New state change! %s", new_state)
                    # check new_state.attributes.assumed_state
                    if hasattr(new_state, "attributes"):
                        new_address = new_state.attributes.get("current_address")
                        if new_address is not None and new_address.lower() != self.pb_state_sources[event_entity]:
                            _LOGGER.debug(
                                "Have a new source address for %s, %s",
                                event_entity,
                                new_address,
                            )
                            self.pb_state_sources[event_entity] = new_address.lower()
                            # Flag that we need new pb checks, and work them out:
                            self._do_private_device_init = True
                            # If no sensors have yet been configured, the coordinator
                            # won't be getting polled for fresh data. Since we have
                            # found something, we should get it to do that.
                            # No longer using async_config_entry_first_refresh as it
                            # breaks
                            self.hass.add_job(self.async_refresh())

    @callback
    def handle_devreg_changes(self, ev: Event[EventDeviceRegistryUpdatedData]):
        """
        Update our scanner list if the device registry is changed.

        This catches area changes (on scanners) and any new/changed
        Private BLE Devices.
        """
        # TODO: Ignore the below, and implement filtering. This gets
        # called a "fair number" of times each time we get reloaded.
        #
        # We could try filtering on "updates" and "area" but I doubt
        # this will fire all that often, and even when it does fire
        # the difference in cycle time appears to be less than 1ms.
        _LOGGER.debug(
            "Device registry has changed. ev: %s",
            ev,
        )
        if ev.data["action"] in {"create", "update"}:
            device = self._device_registry.async_get(ev.data["device_id"])
            # if this is an "update" we also get a "changes" dict, but we don't
            # bother with it yet.

            if device is not None:
                # Work out if it's a device that interests us and respond appropriately.
                for conn_type, _conn_id in device.connections:
                    if conn_type == "private_ble_device":
                        _LOGGER.debug("Trigger updating of Private BLE Devices")
                        self._do_private_device_init = True
                    elif conn_type == "ibeacon":
                        # this was probably us, nothing else to do
                        pass
                    else:
                        # might be a scanner, so let's refresh those
                        _LOGGER.debug("Trigger updating of Scanner Listings")
                        self._do_full_scanner_init = True
            else:
                _LOGGER.error(
                    "Received DR update/create but device id does not exist: %s",
                    ev.data["device_id"],
                )

        elif ev.data["action"] == "remove":
            device_found = False
            for scanner in self.scanner_list:
                if self.devices[scanner].entry_id == ev.data["device_id"]:
                    _LOGGER.debug(
                        "Scanner %s removed, trigger update of scanners.",
                        self.devices[scanner].name,
                    )
                    self._do_full_scanner_init = True
                    device_found = True
            if not device_found:
                # If we save the private ble device's device_id into devices[].entry_id
                # we could check ev.data["device_id"] against it to decide if we should
                # rescan PBLE devices. But right now we don't, so scan 'em anyway.
                _LOGGER.debug("Opportunistic trigger of update for Private BLE Devices")
                self._do_private_device_init = True
        # The co-ordinator will only get updates if we have created entities already.
        # Since this might not always be the case (say, private_ble_device loads after
        # we do), then we trigger an update here with the expectation that we got a
        # device registry update after the private ble device was created. There might
        # be other corner cases where we need to trigger our own update here, so test
        # carefully and completely if you are tempted to remove / alter this.
        self.hass.add_job(self._async_update_data())

    @callback
    def async_handle_advert(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """
        Handle an incoming advert callback from the bluetooth integration.

        These should come in as adverts are received, rather than on our update schedule.
        The data *should* be as fresh as can be, but actually the backend only sends
        these periodically (mainly when the data changes, I think). So it's no good for
        responding to changing rssi values, but it *is* good for seeding our updates in case
        there are no defined sensors yet (or the defined ones are away).
        """
        # _LOGGER.debug(
        #     "New Advert! change: %s, scanner: %s mac: %s name: %s serviceinfo: %s",
        #     change,
        #     service_info.source,
        #     service_info.address,
        #     service_info.name,
        #     service_info,
        # )
        #
        # If there are no configured_devices already present during Bermuda's
        # initial setup, then no sensors will be created, and no updates will
        # be triggered on the co-ordinator. So let's check if we haven't updated
        # recently, and do so...
        if self.stamp_last_update < MONOTONIC_TIME() - (UPDATE_INTERVAL * 2):
            self.hass.add_job(self._async_update_data())

    def _check_all_platforms_created(self, address):
        """Checks if all platforms have finished loading a device's entities."""
        dev = self._get_device(address)
        if dev is not None:
            if all(
                [
                    dev.create_sensor_done,
                    dev.create_tracker_done,
                    dev.create_number_done,
                ]
            ):
                dev.create_all_done = True

    def sensor_created(self, address):
        """Allows sensor platform to report back that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_sensor_done = True
            # _LOGGER.debug("Sensor confirmed created for %s", address)
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")
        self._check_all_platforms_created(address)

    def device_tracker_created(self, address):
        """Allows device_tracker platform to report back that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_tracker_done = True
            # _LOGGER.debug("Device_tracker confirmed created for %s", address)
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")
        self._check_all_platforms_created(address)

    def number_created(self, address):
        """Receives report from number platform that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_number_done = True
        self._check_all_platforms_created(address)

    # def button_created(self, address):
    #     """Receives report from number platform that sensors have been set up."""
    #     dev = self._get_device(address)
    #     if dev is not None:
    #         dev.create_button_done = True
    #     self._check_all_platforms_created(address)

    def count_active_devices(self) -> int:
        """
        Returns the number of bluetooth devices that have recent timestamps.

        Useful as a general indicator of health
        """
        stamp = MONOTONIC_TIME() - 10  # seconds
        fresh_count = 0
        for device in self.devices.values():
            if device.last_seen > stamp:
                fresh_count += 1
        return fresh_count

    def count_active_scanners(self, max_age=10) -> int:
        """Returns count of scanners that have recently sent updates."""
        stamp = MONOTONIC_TIME() - max_age  # seconds
        fresh_count = 0
        for scanner in self.get_active_scanner_summary():
            if scanner.get("last_stamp", 0) > stamp:
                fresh_count += 1
        return fresh_count

    def get_active_scanner_summary(self) -> list[dict]:
        """
        Returns a list of dicts suitable for seeing which scanners
        are configured in the system and how long it has been since
        each has returned an advertisement.
        """
        stamp = MONOTONIC_TIME()
        results = []
        for scanner in self.scanner_list:
            scannerdev = self.devices[scanner]
            last_stamp: float = 0
            for device in self.devices.values():
                record = device.scanners.get(scanner, None)
                if record is not None and record.stamp is not None:
                    last_stamp = max(record.stamp, last_stamp)
            results.append(
                {
                    "name": scannerdev.name,
                    "address": scanner,
                    "last_stamp": last_stamp,
                    "last_stamp_age": stamp - last_stamp,
                }
            )
        return results

    def _get_device(self, address: str) -> BermudaDevice | None:
        """Search for a device entry based on mac address."""
        mac = format_mac(address).lower()
        # format_mac tries to return a lower-cased, colon-separated mac address.
        # failing that, it returns the original unaltered.
        if mac in self.devices:
            return self.devices[mac]
        return None

    def _get_or_create_device(self, address: str) -> BermudaDevice:
        device = self._get_device(address)
        if device is None:
            mac = format_mac(address).lower()
            self.devices[mac] = device = BermudaDevice(address=mac, options=self.options)
            device.address = mac
            device.unique_id = mac
        return device

    async def _async_update_data(self):
        """
        Update data for known devices by scanning bluetooth advert cache.

        This works only with local data, so should be cheap to run
        (no network requests made etc).

        """
        for service_info in bluetooth.async_discovered_service_info(self.hass, False):
            # Note that some of these entries are restored from storage,
            # so we won't necessarily find (immediately, or perhaps ever)
            # scanner entries for any given device.

            # Get/Create a device entry
            device = self._get_or_create_device(service_info.address)

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

                        device.beacon_type.add(BEACON_IBEACON_SOURCE)
                        device.beacon_uuid = man_data[2:18].hex().lower()
                        device.beacon_major = str(int.from_bytes(man_data[18:20], byteorder="big"))
                        device.beacon_minor = str(int.from_bytes(man_data[20:22], byteorder="big"))
                        device.beacon_power = int.from_bytes([man_data[22]], signed=True)

                        # So, the irony of having major/minor is that the
                        # UniversallyUniqueIDentifier is not even unique
                        # locally, so we need to make one :-)

                        device.beacon_unique_id = f"{device.beacon_uuid}_{device.beacon_major}_{device.beacon_minor}"  # pylint: disable=line-too-long
                        # Note: it's possible that a device sends multiple
                        # beacons. We are only going to process the latest
                        # one in any single update cycle, so we ignore that
                        # possibility for now. Given we re-process completely
                        # each cycle it should *just work*, for the most part.

                        # expose the full id in prefname
                        device.prefname = device.beacon_unique_id

                        # Create a metadevice for this beacon. Metadevices get updated
                        # after all adverts are processed and distances etc are calculated
                        # for the sources.
                        self.register_ibeacon_source(device)

                    else:
                        # apple but not an iBeacon, expose the data in case it's useful.
                        device.prefname = clean_charbuf(man_data.hex())
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
            # Clean up names because it seems plenty of bluetooth device creators
            # don't seem to know that buffers !== strings.
            if device.name is None and service_info.device.name:
                device.name = clean_charbuf(service_info.device.name)
            if device.local_name is None and service_info.advertisement.local_name:
                device.local_name = clean_charbuf(service_info.advertisement.local_name)
            device.manufacturer = device.manufacturer or service_info.manufacturer
            if device.manufacturer is None:
                if (
                    service_info.service_uuids
                    and (member_uuid := service_info.service_uuids[0][4:8]) in self.member_uuids
                ):
                    # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/member_uuids.yaml
                    device.manufacturer = self.member_uuids[member_uuid]
            device.connectable = service_info.connectable

            # Try to make a nice name for prefname.
            if device.prefname is None or device.prefname.startswith(DOMAIN + "_"):
                if device.manufacturer:
                    default_prefix = f"{slugify(device.manufacturer)}"
                else:
                    default_prefix = DOMAIN
                device.prefname = device.name or device.local_name or f"{default_prefix}_{slugify(device.address)}"

            # Work through the scanner entries...
            matched_scanners = bluetooth.async_scanner_devices_by_address(self.hass, service_info.address, False)
            for discovered in matched_scanners:
                scanner_device = self._get_device(discovered.scanner.source)
                if scanner_device is None:
                    # The receiver doesn't have a device entry yet, let's refresh
                    # all of them in this batch...
                    self._do_full_scanner_init = True  # Flag that we need a full init
                    self._do_private_device_init = True
                    self._refresh_scanners(matched_scanners)
                    scanner_device = self._get_device(discovered.scanner.source)

                if scanner_device is None:
                    # Highly unusual. If we can't find an entry for the scanner
                    # maybe it's from an integration that's not yet loaded, or
                    # perhaps it's an unexpected type that we don't know how to
                    # find.
                    _LOGGER_SPAM_LESS.error(
                        f"missing_scanner_entry_{discovered.scanner.source}",
                        "Failed to find config for scanner %s, this is probably a bug.",
                        discovered.scanner.source,
                    )
                    continue

                # Update the scanner entry on the current device
                device.update_scanner(scanner_device, discovered)

            # END of per-advertisement-by-device loop

        # If any *configured* devices have not yet been seen, create device
        # entries for them so they will claim the restored sensors in HA
        # (this prevents them from restoring at startup as "Unavailable" if they
        # are not currently visible, and will instead show as "Unknown" for
        # sensors and "Away" for device_trackers).
        if self.stamp_last_update == 0:
            # First run, let's do it.
            for _source_address in self.options.get(CONF_DEVICES, []):
                self._get_or_create_device(_source_address)

        # Scanner entries have been loaded up with latest data, now we can
        # process data for all devices over all scanners.
        for device in self.devices.values():
            # Recalculate smoothed distances, last_seen etc
            device.calculate_data()

        self._refresh_areas_by_min_distance()

        # We might need to freshen deliberately on first start if no new scanners
        # were discovered in the first scan update. This is likely if nothing has changed
        # since the last time we booted.
        if self._do_full_scanner_init:
            if not self._refresh_scanners():
                # _LOGGER.debug("Failed to refresh scanners, likely config entry not ready.")
                # don't fail the update, just try again next time.
                # self.last_update_success = False
                pass

        # set up any beacons and update their data. We do this after all the devices
        # have had their updates done since any beacon inherits data from its source
        # device(s). We do this *before* sensor creation, though.
        self.update_metadevices()

        # The devices are all updated now (and any new scanners and beacons seen have been added),
        # so let's ensure any devices that we create sensors for are set up ready to go.
        # We don't do this sooner because we need to ensure we have every active scanner
        # already loaded up.
        for address, device in self.devices.items():
            if device.create_sensor:
                if not device.create_all_done:
                    _LOGGER.debug("Firing device_new for %s (%s)", device.name, address)
                    # Note that the below should be OK thread-wise, debugger indicates this is being
                    # called by _run in events.py, so pretty sure we are "in the event loop".
                    async_dispatcher_send(self.hass, SIGNAL_DEVICE_NEW, address, self.scanner_list)

        if self.stamp_last_prune < MONOTONIC_TIME() - PRUNE_TIME_INTERVAL:
            # (periodically) prune any stale device entries...
            self.prune_devices()
            self.stamp_last_prune = MONOTONIC_TIME()

        # end of async update
        self.stamp_last_update = MONOTONIC_TIME()
        self.last_update_success = True

    def prune_devices(self):
        """Scan through all collected devices, and remove those that meet Pruning criteria."""
        prune_list = []
        prunable_stamps = {}

        # build a set of source devices that are still beacon_sources[0]
        metadevice_source_primos = set()
        for metadevice in self.metadevices.values():
            if len(metadevice.beacon_sources) > 0:
                metadevice_source_primos.add(metadevice.beacon_sources[0])

        for device_address, device in self.devices.items():
            # Prune any devices that haven't been heard from for too long, but only
            # if we aren't actively tracking them and it's a traditional MAC address.
            # We just collect the addresses first, and do the pruning after exiting this iterator
            #
            # Reduced selection criteria - basically if if's not:
            # - a scanner (beacuse we need those!)
            # - a private_ble device (because they will re-create anyway, plus we auto-sensor them
            # - create_sensor
            # then it should be up for pruning. A stale iBeacon that we don't actually track
            # should totally be pruned if it's no longer around.
            if (
                device_address not in metadevice_source_primos
                and (not device.create_sensor)  # Not if we track the device
                and (not device.is_scanner)
                and (device.last_seen > 0)  # Don't prune if we haven't initialised yet!
                and device.address_type != BDADDR_TYPE_NOT_MAC48
            ):
                if device.address_type == BDADDR_TYPE_PRIVATE_RESOLVABLE:
                    # This is an IRK source address. We'll *only* want to keep
                    # if if belongs to one of our known Private BLE devices *and*
                    # it's the latest address we have for it.

                    if device.last_seen < MONOTONIC_TIME() - PRUNE_TIME_IRK:
                        _LOGGER.debug(
                            "Marking stale IRK address for pruning: %s",
                            device.name or device_address,
                        )
                        prune_list.append(device_address)
                    else:
                        # It's not stale, but we will prune it if we have to later to fit
                        # into PRUNE_MAX_COUNT
                        prunable_stamps[device_address] = device.last_seen

                elif device.last_seen < MONOTONIC_TIME() - PRUNE_TIME_DEFAULT:
                    # It's a static address, and stale.
                    _LOGGER.debug(
                        "Marking old device entry for pruning: %s",
                        device.name or device_address,
                    )
                    prune_list.append(device_address)
                else:
                    # Device is static, not so old, but we might have to prune it anyway
                    prunable_stamps[device_address] = device.last_seen

        prune_quota = len(self.devices) - len(prune_list) - PRUNE_MAX_COUNT
        if prune_quota > 0:
            # We need to find more addresses to prune. Perhaps we live
            # in a busy train station, or are under some sort of BLE-MAC
            # DOS-attack.
            sorted_addresses = sorted([(v, k) for k, v in prunable_stamps.items()])
            _LOGGER.info("Having to prune %s extra devices to make quota.", prune_quota)
            # pylint: disable-next=unused-variable
            for _stamp, address in sorted_addresses[:prune_quota]:
                prune_list.append(address)

        # Perform any pruning we found to do
        for device_address in prune_list:
            _LOGGER.debug("Acting on prune list for %s", device_address)
            del self.devices[device_address]

    def discover_private_ble_metadevices(self):
        """
        Access the Private BLE Device integration to find metadevices to track.

        This function sets up the skeleton metadevice entry for Private BLE (IRK)
        devices, ready for update_metadevices to manage.
        """
        if self._do_private_device_init:
            self._do_private_device_init = False
            _LOGGER.debug("Refreshing Private BLE Device list")

            # Iterate through the Private BLE Device integration's entities,
            # and ensure for each "device" we create a source device.
            # pb here means "private ble device"
            pb_entries = self.hass.config_entries.async_entries(DOMAIN_PRIVATE_BLE_DEVICE, include_disabled=False)
            for pb_entry in pb_entries:
                pb_entities = self._entity_registry.entities.get_entries_for_config_entry_id(pb_entry.entry_id)
                # This will be a list of entities for a given private ble device,
                # let's pull out the device_tracker one, since it has the state
                # info we need.
                for pb_entity in pb_entities:
                    if pb_entity.domain == DEVICE_TRACKER:
                        # We found a *device_tracker* entity for the private_ble device.
                        _LOGGER.debug(
                            "Found a Private BLE Device Tracker! %s",
                            pb_entity.entity_id,
                        )

                        # Grab the device entry (for the name, mostly)
                        if pb_entity.device_id is not None:
                            pb_device = self._device_registry.async_get(pb_entity.device_id)
                        else:
                            pb_device = None

                        # Grab the current state (so we can access the source address attrib)
                        pb_state = self.hass.states.get(pb_entity.entity_id)

                        if pb_state:  # in case it's not there yet
                            pb_source_address = pb_state.attributes.get("current_address", None)
                        else:
                            # Private BLE Device hasn't yet found a source device
                            pb_source_address = None

                        # Get the IRK of the device, which we will use as the address
                        # for the metadevice.
                        # As of 2024.4.0b4 Private_ble appends _device_tracker to the
                        # unique_id of the entity, while we really want to know
                        # the actual IRK, so handle either case by splitting it:
                        _irk = pb_entity.unique_id.split("_")[0]

                        # Create our Meta-Device and tag it up...
                        metadevice = self._get_or_create_device(_irk)
                        # Since user has already configured the Private BLE Device, we
                        # always create sensors for them.
                        metadevice.create_sensor = True

                        # Set a nice name
                        metadevice.name = getattr(pb_device, "name_by_user", getattr(pb_device, "name", None))
                        metadevice.prefname = metadevice.name

                        # Ensure we track this PB entity so we get source address updates.
                        if pb_entity.entity_id not in self.pb_state_sources:
                            self.pb_state_sources[pb_entity.entity_id] = None

                        # Add metadevice to list so it gets included in update_metadevices
                        if metadevice.address not in self.metadevices:
                            self.metadevices[metadevice.address] = metadevice

                        if pb_source_address is not None:
                            # We've got a source MAC address!
                            pb_source_address = pb_source_address.lower()

                            # Set up and tag the source device entry
                            source_device = self._get_or_create_device(pb_source_address)
                            source_device.beacon_type.add(BEACON_PRIVATE_BLE_SOURCE)

                            # This should always be the latest known source address,
                            # since private ble device tells us so.
                            # So ensure it's listed, and listed first.
                            if len(metadevice.beacon_sources) == 0 or metadevice.beacon_sources[0] != pb_source_address:
                                metadevice.beacon_sources.insert(0, pb_source_address)

                            # Update state_sources so we can track when it changes
                            self.pb_state_sources[pb_entity.entity_id] = pb_source_address

                        else:
                            _LOGGER.debug(
                                "No address available for PB Device %s",
                                pb_entity.entity_id,
                            )

    def register_ibeacon_source(self, source_device: BermudaDevice):
        """
        Create or update the meta-device for tracking an iBeacon.

        This should be called each time we discover a new address advertising
        an iBeacon. This might happen only once at startup, but will also
        happen each time a new MAC address is used by a given iBeacon.

        This does not update the beacon's details (distance etc), that is done
        in the update_metadevices function after all data has been gathered.
        """
        if BEACON_IBEACON_SOURCE not in source_device.beacon_type:
            _LOGGER.error(
                "Only IBEACON_SOURCE devices can be used to see a beacon metadevice. %s is not.",
                source_device.name,
            )
        if source_device.beacon_unique_id is None:
            _LOGGER.error("Source device %s is not a valid iBeacon!", source_device.name)
        else:
            metadevice = self._get_or_create_device(source_device.beacon_unique_id)
            if len(metadevice.beacon_sources) == 0:
                # #### NEW METADEVICE #####
                # (do one-off init stuff here)
                if metadevice.address not in self.metadevices:
                    self.metadevices[metadevice.address] = metadevice
                else:
                    _LOGGER.warning(
                        "Metadevice already tracked despite not existing yet. %s",
                        metadevice.address,
                    )

                # Copy over the beacon attributes
                for attribute in (
                    "beacon_unique_id",
                    "beacon_uuid",
                    "beacon_major",
                    "beacon_minor",
                    "beacon_power",
                ):
                    setattr(metadevice, attribute, getattr(source_device, attribute, None))

                # Check if we should set up sensors for this beacon
                if metadevice.address.upper() in self.options.get(CONF_DEVICES, []):
                    # This is a meta-device we track. Flag it for set-up:
                    metadevice.create_sensor = True

            # #### EXISTING METADEVICE ####
            # (only do things that might have to change when MAC address cycles etc)

            if source_device.address not in metadevice.beacon_sources:
                # We have a *new* source device.
                # insert this device as a known source
                metadevice.beacon_sources.insert(0, source_device.address)
                # and trim the list of sources
                del metadevice.beacon_sources[HIST_KEEP_COUNT:]

    def update_metadevices(self):
        """
        Create or update iBeacon, Private_BLE and other meta-devices from
        the received advertisements.

        Note that at this point all the distances etc should be fresh for
        the source devices, so we can just copy values from them to the metadevice.
        However, the sources might not yet be using the metadevice's custom ref_power,
        so their *first* update might have the un-adjusted value after a mac change or
        other initialisation.
        """
        # First seed the metadevice skeletons and set their latest beacon_source entries
        # Private BLE Devices. It will only do anything if the self._do_private_device_init
        # flag is set.
        self.discover_private_ble_metadevices()

        # iBeacon devices should already have their metadevices created.
        # FIXME: irk and ibeacons will fight over their relative ref_power too.

        for metadev in self.metadevices.values():
            # We Expect the first beacon source to be the current one.
            # This is maintained by ibeacon or private_ble metadevice creation/update
            latest_source: str | None = None
            source_device: BermudaDevice | None = None
            if len(metadev.beacon_sources) > 0:
                latest_source = metadev.beacon_sources[0]
                if latest_source is not None:
                    source_device = self._get_device(latest_source)

            if latest_source is not None and source_device is not None:
                # Map the source device's scanner list into ours
                metadev.scanners = source_device.scanners

                # Set the source device's ref_power from our own. This will cause
                # the source device and all its scanner entries to update their
                # distance measurements. This won't affect Area wins though, because
                # they are "relative", not absolute.

                # FIXME: This has two potential bugs:
                # - if multiple metadevices share a source, they will
                #   "fight" over their preferred ref_power, if different.
                # - The non-meta device (if tracked) will receive distances
                #   based on the meta device's ref_power.
                # - The non-meta device if tracked will have its own ref_power ignored.
                #
                # None of these are terribly awful, but worth fixing.

                # Note we are setting the ref_power on the source_device, not the
                # individual scanner entries (it will propagate to them though)
                if source_device.ref_power != metadev.ref_power:
                    source_device.set_ref_power(metadev.ref_power)

                # anything that isn't already set to something interesting, overwrite
                # it with the new device's data.
                # Defaults:
                for attribute in [
                    # "create_sensor",  # don't copy this, maybe we're tracking the device alone
                    "local_name",  # names we copy if there isn't one already.
                    "manufacturer",
                    "name",
                    # "options",
                    "prefname",
                ]:
                    if hasattr(metadev, attribute):
                        if getattr(metadev, attribute) in [None, False]:
                            setattr(metadev, attribute, getattr(source_device, attribute))
                    else:
                        _LOGGER.error(
                            "Devices don't have a '%s' attribute, this is a bug.",
                            attribute,
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
                    "zone",
                ]:
                    if hasattr(metadev, attribute):
                        setattr(metadev, attribute, getattr(source_device, attribute))
                    else:
                        _LOGGER.error(
                            "Devices don't have a '%s' attribute, this is a bug.",
                            attribute,
                        )

                if source_device.last_seen > metadev.last_seen:
                    # Source is newer than the latest recorded, update last_seen
                    metadev.last_seen = source_device.last_seen

                elif source_device.last_seen == 0:
                    # _LOGGER.debug(
                    #     "New source %s for %s has no stamp yet. This is"
                    #     " expected if it's a fresh Private BLE source.",
                    #     source_device.address,
                    #     metadev.name
                    # )
                    pass
                elif source_device.last_seen < metadev.last_seen:
                    # We should not have a source device that is older than the
                    # current metadevice, so flag this if it occurs.
                    # This caught bug #138, not that I realised it at the time!
                    # (https://github.com/agittins/bermuda/issues/138)
                    _LOGGER.debug(
                        "Using freshest advert from %s for %s but it's still %s seconds too old!",
                        source_device.address,
                        metadev.name,
                        metadev.last_seen - source_device.last_seen,
                    )
                # else the stamps are equal, which is perfectly OK.

    def dt_mono_to_datetime(self, stamp) -> datetime:
        """Given a monotonic timestamp, convert to datetime object."""
        age = MONOTONIC_TIME() - stamp
        return now() - timedelta(seconds=age)

    def dt_mono_to_age(self, stamp) -> str:
        """Convert monotonic timestamp to age (eg: "6 seconds ago")."""
        return get_age(self.dt_mono_to_datetime(stamp))

    def resolve_area_name(self, area_id) -> str | None:
        """
        Given an area_id, return the current area name.

        Will return None if the area id does *not* resolve to a single
        known area name.
        """
        areas = self.area_reg.async_get_area(area_id)
        if hasattr(areas, "name"):
            return getattr(areas, "name", "invalid_area")
        return None

    def _refresh_areas_by_min_distance(self):
        """Set area for ALL devices based on closest beacon."""
        for device in self.devices.values():
            if device.is_scanner is not True:
                self._refresh_area_by_min_distance(device)

    def _refresh_area_by_min_distance(self, device: BermudaDevice):
        """Very basic Area setting by finding closest beacon to a given device."""
        closest_scanner: BermudaDeviceScanner | None = None
        for scanner in device.scanners.values():
            # Check each scanner and keep note of the closest one based on rssi_distance.
            # Note that rssi_distance is smoothed/filtered, and might be None if the last
            # reading was old enough that our algo decides it's "away".
            if scanner.rssi_distance is not None and scanner.rssi_distance < self.options.get(
                CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS
            ):
                # It's inside max_radius...
                if closest_scanner is None:
                    # no encumbent, we win!
                    closest_scanner = scanner
                elif closest_scanner.rssi_distance is None or scanner.rssi_distance < closest_scanner.rssi_distance:
                    # We're closer than the last-closest, we win!
                    closest_scanner = scanner

        # Apply the newly-found closest scanner (or apply None if we didn't find one)
        device.apply_scanner_selection(closest_scanner)

    def _refresh_scanners(self, scanners: list[BluetoothScannerDevice] | None = None):
        """
        Refresh our local (and saved) list of scanners (BLE Proxies).

        The scanners list param is ignored and no longer required. We refresh all scanners
        each time we are called, since the overhead is now lower and we had prematurely
        optimised the routine. We only save out the config entry if it has changed *AND*
        we haven't tried to do so in the last SAVEOUT_COOLDOWN seconds (10 seems to be enough,
        we only do it when the proxies config has *actually* changed).
        """
        _previous_scannerlist = [device.address for device in self.devices.values() if device.is_scanner]
        _purge_scanners = _previous_scannerlist.copy()
        _scanners_without_areas = []

        # _LOGGER.error("Preserving %d current scanner entries", len(_previous_scannerlist))

        # Find active HaBaseScanners in the backend, and only pay attention to those
        # instead of trawling through the device registry first.
        #
        # scanner_ha: BaseHaScanner from HA's bluetooth backend
        # scanner_devreg_bt: DeviceEntry from HA's device_registry from Bluetooth integration
        # scanner_devreg_mac: DeviceEntry from HA's *other* integrations, like ESPHome, Shelly.
        # scanner_b: BermudaDevice entry

        # TODO: Eventually replace this with a minver requirement in hacs.json.
        if self.hass_version_min_2025_2:
            # New api
            self._hascanners = set(self._manager.async_current_scanners())
        else:
            # Evil: We're acessing private members of bt manager to do it since there's no API call for it.
            self._hascanners = self._manager._connectable_scanners | self._manager._non_connectable_scanners  # noqa: SLF001

        for hascanner in self._hascanners:
            scanner_address = format_mac(hascanner.source).lower()
            # As of 2025.2.0 The bluetooth integration creates its own device entries
            # for all HaScanners, not just local adaptors. So since there are two integration
            # pages where a user might apply an area setting (eg, the bluetooth page or the shelly or esphome page)
            # we should check both to see if the user has applied an area anywhere, and prefer the bluetooth one
            # if both are set.
            scanner_devreg_bt = self._device_registry.async_get_device(
                connections={
                    ("bluetooth", scanner_address.upper()),  # bluetooth, uppercase: matches bluetooth integration
                }
            )
            scanner_devreg_mac = self._device_registry.async_get_device(
                connections={
                    ("mac", scanner_address),  # mac, lowercase: matches ESPHome, Shellys integrations etc
                }
            )

            if scanner_devreg_bt is None and scanner_devreg_mac is None:
                _LOGGER_SPAM_LESS.error(
                    f"scanner_not_in_devreg_{scanner_address:s}",
                    "Failed to find scanner %s (%s) in Device Registry",
                    hascanner.name,
                    hascanner.source,
                )
                continue
            # _LOGGER.info("Great! Found scanner: %s (%s)", scanner_ha.name, scanner_ha.source)
            # Since this scanner still exists, we won't purge it
            if scanner_address in _purge_scanners:
                _purge_scanners.remove(scanner_address)

            # Populate the local copy of timestamps, if applicable
            if isinstance(hascanner, BaseHaRemoteScanner):
                self._hascanner_timestamps[hascanner.source.lower()] = hascanner._discovered_device_timestamps  # noqa: SLF001

            scanner_b = self._get_device(scanner_address)
            if scanner_b is None:
                # It's a new scanner, we will need to update our saved config.
                # _LOGGER.debug("New Scanner: %s", scanner_ha.name)
                scanner_b = self._get_or_create_device(scanner_address)

            # We found the device entry and have created our scannerdevice,
            # now update any fields that might be new from the device reg.
            # First clear the existing to make prioritising the bt/mac matches
            # easier (feel free to refactor, bear in mind we prefer bt first)
            scanner_b.area_id = None
            scanner_b.name = None

            _bt_name = None

            if scanner_devreg_bt is not None:
                scanner_b.area_id = scanner_devreg_bt.area_id
                scanner_b.entry_id = scanner_devreg_bt.id
                scanner_b.name = scanner_devreg_bt.name_by_user  # might be None
                _bt_name = scanner_devreg_bt.name
            if scanner_devreg_mac is not None:
                # Only apply if the bt device entry hasn't been applied:
                scanner_b.area_id = scanner_b.area_id or scanner_devreg_mac.area_id
                scanner_b.entry_id = scanner_b.entry_id or scanner_devreg_mac.id
                # Name preference order:
                # - bluetooth, user-supplied
                # - other, user-supplied
                # - other, default (because they pre-date bluetooth device)
                # - bluetooth, default.
                scanner_b.name = (
                    scanner_b.name  # user-supplied in bluetooth integration (above)
                    or scanner_devreg_mac.name_by_user  # user-supplied in esphome/shelly etc
                    or scanner_devreg_mac.name
                    or _bt_name
                )
            else:
                # there was no mac device, use the bt default name as last resort
                # (this will mostly just happen with local bt usb adaptors)
                scanner_b.name = _bt_name

            areas = self.area_reg.async_get_area(scanner_b.area_id) if scanner_b.area_id else None
            if areas is not None and hasattr(areas, "name") and areas.name is not None:
                scanner_b.area_name = areas.name
            else:
                _LOGGER_SPAM_LESS.warning(
                    f"no_area_on_update{scanner_b.name}",
                    "No area name or no area id updating scanner %s, area_id %s",
                    scanner_b.name,
                    areas,
                )
                _scanners_without_areas.append(scanner_b.name or scanner_b.address)
                scanner_b.area_name = f"Invalid Area for {scanner_b.name}"
            scanner_b.is_scanner = True

        # Now un-tag any devices that are no longer scanners
        for address in _purge_scanners:
            self.devices[address].is_scanner = False
            update_scannerlist = True

        if _scanners_without_areas != self._scanners_without_areas:
            # the set has changed, or we have just started (since the one in self is defaulted to None)

            # Clear any existing repair, because it's either resolved now (empty list) or we need to re-issue
            # the repair in order to update the scanner list (re-calling doesn't update it).
            ir.async_delete_issue(self.hass, DOMAIN, REPAIR_SCANNER_WITHOUT_AREA)

            if len(_scanners_without_areas) != 0:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    REPAIR_SCANNER_WITHOUT_AREA,
                    translation_key=REPAIR_SCANNER_WITHOUT_AREA,
                    translation_placeholders={
                        "scannerlist": "".join(f"- {name}\n" for name in _scanners_without_areas),
                    },
                    severity=ir.IssueSeverity.ERROR,
                    is_fixable=False,
                )

            # copy to self so we don't re-raise unless something changes in future.
            self._scanners_without_areas = _scanners_without_areas

        # Because of the quick check-time and the checks we have on saving the config_entry,
        # we'll update on every call:
        update_scannerlist = True
        if update_scannerlist:
            # bail out if the config entry isn't ready yet.
            if self.config_entry is None or self.config_entry.state != ConfigEntryState.LOADED:
                # _LOGGER.debug("Aborting refresh scanners due to config entry not being ready")
                self._do_full_scanner_init = True
                return False

            # Build the config_data and self.scanner_list structs fresh
            # ready to update our config entry if needed.
            self.scanner_list.clear()
            confdata_scanners: dict[str, dict] = {}
            for device in self.devices.values():
                if device.is_scanner:
                    self.scanner_list.append(device.address)
                    # Only add the necessary fields to confdata
                    confdata_scanners[device.address] = {
                        key: getattr(device, key)
                        for key in [
                            "name",
                            "local_name",
                            "prefname",
                            "address",
                            "ref_power",
                            "unique_id",
                            "address_type",
                            "area_id",
                            "area_name",
                            "is_scanner",
                            "entry_id",
                        ]
                    }

            if self.config_entry.data.get(CONFDATA_SCANNERS, {}) == confdata_scanners:
                # **** BAIL OUT, CONFIG HAS NOT CHANGED ****
                # _LOGGER.debug("Scanner configs are identical, not doing update.")
                self._do_full_scanner_init = False
                return True

            # We will arrive here every second for as long as the saved config is
            # different from our running config. But we don't want to save immediately,
            # since there is a lot of bouncing that happens during setup.

            # Make sure we haven't requested recently...
            if self.last_config_entry_update_request < MONOTONIC_TIME() - SAVEOUT_COOLDOWN:
                # OK, we're good to go.
                self.last_config_entry_update_request = MONOTONIC_TIME()
                _LOGGER.debug("Requesting save-out of scanner configs")
                self.hass.add_job(self.async_call_update_entry, confdata_scanners)

        return True

    @callback
    def async_call_update_entry(self, confdata_scanners) -> None:
        """
        Call in the event loop to update the scanner entries in our config.

        We do this via add_job to ensure it runs in the event loop.
        """
        # Clear the flag for init and update the stamp
        self._do_full_scanner_init = False
        self.last_config_entry_update = MONOTONIC_TIME()
        # Apply new config (will cause reload if there are changes)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONFDATA_SCANNERS: confdata_scanners,
            },
        )

    async def service_dump_devices(self, call: ServiceCall) -> ServiceResponse:  # pylint: disable=unused-argument;
        """Return a dump of beacon advertisements by receiver."""
        out = {}
        addresses_input = call.data.get("addresses", "")
        redact = call.data.get("redact", False)
        configured_devices = call.data.get("configured_devices", False)

        # Choose filter for device/address selection
        addresses = []
        if addresses_input != "":
            # Specific devices
            addresses += addresses_input.upper().split()
        if configured_devices:
            # configured and scanners
            addresses += self.scanner_list
            addresses += self.options.get(CONF_DEVICES, [])
            # known IRK/Private BLE Devices
            addresses += self.pb_state_sources

        # lowercase all the addresses for matching
        addresses = list(map(str.lower, addresses))

        # Build the dict of devices
        for address, device in self.devices.items():
            if len(addresses) == 0 or address.lower() in addresses:
                out[address] = device.to_dict()

        if redact:
            _stamp_redact = MONOTONIC_TIME()
            out = cast(ServiceResponse, self.redact_data(out))
            _stamp_redact_elapsed = MONOTONIC_TIME() - _stamp_redact
            if _stamp_redact_elapsed > 3:  # It should be fast now.
                _LOGGER.warning("Dump devices redaction took %2f seconds", _stamp_redact_elapsed)
            else:
                _LOGGER.debug("Dump devices redaction took %2f seconds", _stamp_redact_elapsed)
        return out

    def redaction_list_update(self):
        """
        Freshen or create the list of match/replace pairs that we use to
        redact MAC addresses. This gives a set of helpful address replacements
        that still allows identifying device entries without disclosing MAC
        addresses.
        """
        i = len(self.redactions)  # not entirely accurate but we don't care.

        # SCANNERS
        for non_lower_address in self.scanner_list:
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                self.redactions[address] = f"{address[:2]}::SCANNER_{i}::{address[-2:]}"
        # CONFIGURED DEVICES
        for non_lower_address in self.options.get(CONF_DEVICES, []):
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                if address.count("_") == 2:
                    self.redactions[address] = f"{address[:4]}::CFG_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    self.redactions[address.split("_")[0]] = f"{address[:4]}::CFG_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:
                    self.redactions[address] = f"{address[:2]}::CFG_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is, but not a mac.
                    self.redactions[address] = f"CFG_OTHER_{1}_{address}"
        # EVERYTHING ELSE
        for non_lower_address, device in self.devices.items():
            address = non_lower_address.lower()
            if address not in self.redactions:
                # Only add if they are not already there.
                i += 1
                if device.address_type == ADDR_TYPE_PRIVATE_BLE_DEVICE:
                    self.redactions[address] = f"{address[:4]}::IRK_DEV_{i}"
                elif address.count("_") == 2:
                    self.redactions[address] = f"{address[:4]}::OTHER_iBea_{i}::{address[32:]}"
                    # Raw uuid in advert
                    self.redactions[address.split("_")[0]] = f"{address[:4]}::OTHER_iBea_{i}_{address[32:]}::"
                elif len(address) == 17:  # a MAC
                    self.redactions[address] = f"{address[:2]}::OTHER_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is.
                    self.redactions[address] = f"OTHER_{1}_{address}"

    async def purge_redactions(self, hass: HomeAssistant):
        """Empty redactions and free up some memory."""
        self.redactions = {}
        self._purge_task = async_call_later(
            hass,
            8 * 60 * 60,
            lambda _: HassJob(
                hass.loop.call_soon_threadsafe(hass.async_create_task, self.purge_redactions(hass)),
                cancel_on_shutdown=True,
            ),
        )
        self._has_purged = True

    async def stop_purging(self):
        """Stop purging. There might be a better way to do this?."""
        if self._purge_task:
            if self._has_purged:
                self._purge_task()  # This cancels the async_call_later task
                self._purge_task = None
            else:
                self._purge_task.cancel()
                self._purge_task = None

    def redact_data(self, data, first_run=True):
        """
        Wash any collection of data of any MAC addresses.

        Uses the redaction list of substitutions if already created, then
        washes any remaining mac-like addresses. This routine is recursive,
        so if you're changing it bear that in mind!
        """
        if first_run:
            # On first/outer call, refresh the redaction list to ensure
            # we don't let any new addresses slip through. Might be expensive
            # on first call, but will be much cheaper for subsequent calls.
            self.redaction_list_update()
            first_run = False
        if isinstance(data, str):
            data = data.lower()
            # the end of the recursive wormhole, do the actual work:
            if data not in self.redactions:
                for find, fix in list(self.redactions.items()):
                    if find in data:
                        self.redactions[data] = data.replace(find, fix)
                        data = self.redactions[data]
                        break
            else:
                data = self.redactions[data]
            # redactions done, now replace any remaining MAC addresses
            # We are only looking for xx:xx:xx... format.
            return self._redact_generic_re.sub(self._redact_generic_sub, data)
        elif isinstance(data, dict):
            return {self.redact_data(k, False): self.redact_data(v, False) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.redact_data(v, False) for v in data]
        else:
            return data
