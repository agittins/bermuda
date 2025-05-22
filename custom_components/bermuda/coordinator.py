"""DataUpdateCoordinator for Bermuda bluetooth data."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

import aiofiles
import voluptuous as vol
import yaml
from bluetooth_data_tools import monotonic_time_coarse
from habluetooth import BaseHaScanner
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.api import _get_manager
from homeassistant.const import MAJOR_VERSION as HA_VERSION_MAJ
from homeassistant.const import MINOR_VERSION as HA_VERSION_MIN
from homeassistant.const import Platform
from homeassistant.core import (
    Event,
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
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.dt import get_age, now

from .bermuda_device import BermudaDevice
from .bermuda_irk import BermudaIrkManager
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    AREA_MAX_AD_AGE,
    BDADDR_TYPE_NOT_MAC48,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
    PRUNE_MAX_COUNT,
    PRUNE_TIME_DEFAULT,
    PRUNE_TIME_INTERVAL,
    PRUNE_TIME_KNOWN_IRK,
    PRUNE_TIME_UNKNOWN_IRK,
    REPAIR_SCANNER_WITHOUT_AREA,
    SAVEOUT_COOLDOWN,
    SIGNAL_DEVICE_NEW,
    SIGNAL_SCANNERS_CHANGED,
    UPDATE_INTERVAL,
)
from .util import mac_explode_formats, mac_math_offset, mac_norm

if TYPE_CHECKING:
    from habluetooth import BluetoothServiceInfoBleak
    from homeassistant.components.bluetooth import (
        BluetoothChange,
    )
    from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager

    from . import BermudaConfigEntry
    from .bermuda_advert import BermudaAdvert

Cancellable = Callable[[], None]

# Using "if" instead of "min/max" triggers PLR1730, but when
# split over two lines, ruff removes it, then complains again.
# so we're just disabling it for the whole file.
# https://github.com/astral-sh/ruff/issues/4244
# ruff: noqa: PLR1730


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
        # when habasescanner.discovered_device_timestamps became a public method.
        self.hass_version_min_2025_4 = HA_VERSION_MAJ > 2025 or (HA_VERSION_MAJ == 2025 and HA_VERSION_MIN >= 4)

        # ##### Redaction Data ###
        #
        # match/replacement pairs for redacting addresses
        self.redactions: dict[str, str] = {}
        # Any remaining MAC addresses will be replaced with this. We define it here
        # so we can compile it once. MAC addresses may have [:_-] separators.
        self._redact_generic_re = re.compile(
            r"(?P<start>[0-9A-Fa-f]{2})[:_-]([0-9A-Fa-f]{2}[:_-]){4}(?P<end>[0-9A-Fa-f]{2})"
        )
        self._redact_generic_sub = r"\g<start>:xx:xx:xx:xx:\g<end>"

        self._has_purged = False
        self._purge_task = hass.loop.call_soon_threadsafe(hass.async_create_task, self.purge_redactions(hass))
        self.stamp_redactions_updated: float = 0

        self.update_in_progress: bool = False  # A lock to guard against huge backlogs / slow processing
        self.stamp_last_update: float = 0  # Last time we ran an update, from monotonic_time_coarse()
        self.stamp_last_update_started: float = 0
        self.stamp_last_prune: float = 0  # When we last pruned device list

        self.member_uuids = {}
        self.company_uuids = {}

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

        self._waitingfor_load_manufacturer_ids = True
        entry.async_create_background_task(
            hass, self.async_load_manufacturer_ids(), "Load Bluetooth IDs", eager_start=True
        )

        self._manager: HomeAssistantBluetoothManager = _get_manager(hass)  # instance of the bluetooth manager
        self._hascanners: set[BaseHaScanner] = set()  # Links to the backend scanners
        self._hascanner_timestamps: dict[str, dict[str, float]] = {}  # scanner_address, device_address, stamp
        self._scanner_list: set[str] = set()
        self._scanners: set[BermudaDevice] = set()  # Set of all in self.devices that is_scanner=True
        self.irk_manager = BermudaIrkManager()

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
        self.last_config_entry_update_request = (
            monotonic_time_coarse() + SAVEOUT_COOLDOWN
        )  # Stamp for save-out requests

        # AJG 2025-04-23 Disabling, see the commented method below for notes.
        # self.config_entry.async_on_unload(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self.handle_state_changes))

        # First time around we freshen the restored scanner info by
        # forcing a scan of the captured info.
        self._scanner_init_pending = True

        # First time go through the private ble devices to see if there's
        # any there for us to track.
        self._do_private_device_init = True

        # Listen for changes to the device registry and handle them.
        # Primarily for changes to scanners and Private BLE Devices.
        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, self.handle_devreg_changes)
        )

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

        self.area_reg = ar.async_get(hass)

        # *** I don't think we need to do this, we generate the scanner list in realtime now,
        # and we react to device registry changes.
        #
        # Restore the scanners saved in config entry data. We maintain
        # a list of known scanners so we can
        # restore the sensor states even if we don't have a full set of
        # scanner receipts in the discovery data.
        # self.scanner_list: list[str] = []
        # if hasattr(entry, "data"):
        #     for address, saved in entry.data.get(CONFDATA_SCANNERS, {}).items():
        #         scanner = self._get_or_create_device(address)
        #         for key, value in saved.items():
        #             if key != "options":
        #                 # We don't restore the options, since they may have changed.
        #                 # the get_or_create will have grabbed the current ones.
        #                 setattr(scanner, key, value)
        #         self.scanner_list.append(address)

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

        # Register for newly discovered / changed BLE devices
        if self.config_entry is not None:
            self.config_entry.async_on_unload(
                bluetooth.async_register_callback(
                    self.hass,
                    self.async_handle_advert,
                    bluetooth.BluetoothCallbackMatcher(connectable=False),
                    bluetooth.BluetoothScanningMode.ACTIVE,
                )
            )

    @property
    def scanner_list(self):
        return self._scanner_list

    @property
    def get_scanners(self) -> set[BermudaDevice]:
        return self._scanners

    def scanner_list_add(self, scanner_device: BermudaDevice):
        self._scanner_list.add(scanner_device.address)
        self._scanners.add(scanner_device)
        async_dispatcher_send(self.hass, SIGNAL_SCANNERS_CHANGED)

    def scanner_list_del(self, scanner_device: BermudaDevice):
        self._scanner_list.remove(scanner_device.address)
        self._scanners.remove(scanner_device)
        async_dispatcher_send(self.hass, SIGNAL_SCANNERS_CHANGED)

    def get_manufacturer_from_id(self, uuid: int | str) -> tuple[str, bool] | tuple[None, None]:
        """
        An opinionated Bluetooth UUID to Name mapper.

        - uuid must be four hex chars in a string, or an `int`

        Retreives the manufacturer name from the Bluetooth SIG Member UUID listing,
        using a cached copy of https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/member_uuids.yaml

        HOWEVER: Bermuda adds some opinionated overrides for the benefit of user clarity:
        - Legal entity names may be overriden with well-known brand names
        - Special-use prefixes may be tagged as such (eg iBeacon etc)
        - Generics can be excluded by setting exclude_generics=True
        """
        if isinstance(uuid, str):
            uuid = int(uuid.replace(":", ""), 16)

        _generic = False
        # Because iBeacon and (soon) GFMD and AppleFindmy etc are common protocols, they
        # don't do a good job of uniquely identifying a manufacturer, so we use them
        # as fallbacks only.
        if uuid == 0x0BA9:
            # allterco robotics, aka...
            _name = "Shelly Devices"
        elif uuid == 0x004C:
            # Apple have *many* UUIDs, but since they don't OEM for others (AFAIK)
            # and only the iBeacon / FindMy adverts seem to be third-partied, match just
            # this one instead of their entire set.
            _name = "Apple Inc."
            _generic = True
        elif uuid == 0x181C:
            _name = "BTHome v1 cleartext"
            _generic = True
        elif uuid == 0x181E:
            _name = "BTHome v1 encrypted"
            _generic = True
        elif uuid == 0xFCD2:
            _name = "BTHome V2"  # Sponsored by Allterco / Shelly
            _generic = True
        elif uuid in self.member_uuids:
            _name = self.member_uuids[uuid]
            # Hardware manufacturers who OEM MAC PHYs etc, or offer the use
            # of their OUIs to third parties (specific known ones can be moved
            # to a case in the above conditions).
            if any(x in _name for x in ["Google", "Realtek"]):
                _generic = True
        elif uuid in self.company_uuids:
            _name = self.company_uuids[uuid]
            _generic = False
        else:
            return None, None
        return (_name, _generic)

    async def async_load_manufacturer_ids(self):
        """Import yaml files containing manufacturer name mappings."""
        try:
            # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/member_uuids.yaml
            file_path = self.hass.config.path(
                f"custom_components/{DOMAIN}/manufacturer_identification/member_uuids.yaml"
            )
            async with aiofiles.open(file_path) as f:
                mi_yaml = yaml.safe_load(await f.read())["uuids"]
            self.member_uuids: dict[int, str] = {member["uuid"]: member["name"] for member in mi_yaml}

            # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/company_identifiers/company_identifiers.yaml
            file_path = self.hass.config.path(
                f"custom_components/{DOMAIN}/manufacturer_identification/company_identifiers.yaml"
            )
            async with aiofiles.open(file_path) as f:
                ci_yaml = yaml.safe_load(await f.read())["company_identifiers"]
            self.company_uuids: dict[int, str] = {member["value"]: member["name"] for member in ci_yaml}
        finally:
            # Ensure that an issue reading these files (which are optional, really) doesn't stop the whole show.
            self._waitingfor_load_manufacturer_ids = False

    # AJG 2025-04-23 - This is probably no longer necessary, since we now hook a callback
    # directly in Private_ble_devices to get updates on new MAC addresses.
    # The device_registry events should cover anything else we need.
    # @callback
    # def handle_state_changes(self, ev: Event[EventStateChangedData]):
    #     """Watch for new mac addresses on private ble devices and act."""
    #     if ev.event_type == EVENT_STATE_CHANGED:
    #         event_entity = ev.data.get("entity_id", "invalid_event_entity")
    #         if event_entity in self.pb_state_sources:
    #             # It's a state change of an entity we are tracking.
    #             new_state = ev.data.get("new_state")
    #             if new_state:
    #                 # _LOGGER.debug("New state change! %s", new_state)
    #                 # check new_state.attributes.assumed_state
    #                 if hasattr(new_state, "attributes"):
    #                     new_address = new_state.attributes.get("current_address")
    #                     if new_address is not None and new_address.lower() != self.pb_state_sources[event_entity]:
    #                         _LOGGER.debug(
    #                             "Have a new source address for %s, %s",
    #                             event_entity,
    #                             new_address,
    #                         )
    #                         self.pb_state_sources[event_entity] = new_address.lower()
    #                         # Flag that we need new pb checks, and work them out:
    #                         self._do_private_device_init = True
    #                         # If no sensors have yet been configured, the coordinator
    #                         # won't be getting polled for fresh data. Since we have
    #                         # found something, we should get it to do that.
    #                         # No longer using async_config_entry_first_refresh as it
    #                         # breaks
    #                         self.hass.add_job(self.async_refresh())

    @callback
    def handle_devreg_changes(self, ev: Event[EventDeviceRegistryUpdatedData]):
        """
        Update our scanner list if the device registry is changed.

        This catches area changes (on scanners) and any new/changed
        Private BLE Devices.
        """
        _LOGGER.debug(
            "Device registry has changed. ev: %s",
            ev,
        )
        if ev.data["action"] in {"create", "update"}:
            device_entry = self._device_registry.async_get(ev.data["device_id"])
            # if this is an "update" we also get a "changes" dict, but we don't
            # bother with it yet.

            if device_entry is not None:
                # Work out if it's a device that interests us and respond appropriately.
                for conn_type, _conn_id in device_entry.connections:
                    if conn_type == "private_ble_device":
                        _LOGGER.debug("Trigger updating of Private BLE Devices")
                        self._do_private_device_init = True
                    elif conn_type == "ibeacon":
                        # this was probably us, nothing else to do
                        pass
                    else:
                        for ident_type, ident_id in device_entry.identifiers:
                            if ident_type == DOMAIN:
                                # One of our sensor devices!
                                try:
                                    if _device := self.devices[ident_id.lower()]:
                                        _device.name_by_user = device_entry.name_by_user
                                        _device.make_name()
                                except KeyError:
                                    pass
                        # might be a scanner, so let's refresh those
                        _LOGGER.debug("Trigger updating of Scanner Listings")
                        self._scanner_init_pending = True
            else:
                _LOGGER.error(
                    "Received DR update/create but device id does not exist: %s",
                    ev.data["device_id"],
                )

        elif ev.data["action"] == "remove":
            device_found = False
            for scanner in self.get_scanners:
                if scanner.entry_id == ev.data["device_id"]:
                    _LOGGER.debug(
                        "Scanner %s removed, trigger update of scanners",
                        scanner.name,
                    )
                    self._scanner_init_pending = True
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
        # carefully and completely if you are tempted to remove / alter this. Bermuda
        # will skip an update cycle if it detects one already in progress.
        # FIXME: self._async_update_data_internal()

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

        # If there are no active entities created after Bermuda's
        # initial setup, then no updates will be triggered on the co-ordinator.
        # So let's check if we haven't updated recently, and do so...
        if self.stamp_last_update < monotonic_time_coarse() - (UPDATE_INTERVAL * 2):
            self._async_update_data_internal()

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
        stamp = monotonic_time_coarse() - 10  # seconds
        fresh_count = 0
        for device in self.devices.values():
            if device.last_seen > stamp:
                fresh_count += 1
        return fresh_count

    def count_active_scanners(self, max_age=10) -> int:
        """Returns count of scanners that have recently sent updates."""
        stamp = monotonic_time_coarse() - max_age  # seconds
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
        stamp = monotonic_time_coarse()
        return [
            {
                "name": scannerdev.name,
                "address": scannerdev.address,
                "last_stamp": scannerdev.last_seen,
                "last_stamp_age": stamp - scannerdev.last_seen,
            }
            for scannerdev in self.get_scanners
        ]

    def _get_device(self, address: str) -> BermudaDevice | None:
        """Search for a device entry based on mac address."""
        # mac_norm tries to return a lower-cased, colon-separated mac address.
        # failing that, it returns the original, lower-cased.
        try:
            return self.devices[mac_norm(address)]
        except KeyError:
            return None

    def _get_or_create_device(self, address: str) -> BermudaDevice:
        mac = mac_norm(address)
        try:
            return self.devices[mac]
        except KeyError:
            self.devices[mac] = device = BermudaDevice(mac, self)
            return device

    async def _async_update_data(self):
        """Implementation of DataUpdateCoordinator update_data function."""
        # return False
        self._async_update_data_internal()

    def _async_update_data_internal(self):
        """
        The primary update loop that processes almost all data in Bermuda.

        This works only with local data, so should be cheap to run
        (no network requests made etc). This function takes care of:

        - gathering all bluetooth adverts since last run and saving them into
          Bermuda's device objects
        - Updating all metadata
        - Performing rssi and statistical calculations
        - Making area determinations
        - (periodically) pruning device entries

        """
        if self._waitingfor_load_manufacturer_ids:
            _LOGGER.debug("Waiting for BT data load...")
            return True
        if self.update_in_progress:
            # Eeep!
            _LOGGER_SPAM_LESS.warning("update_still_running", "Previous update still running, skipping this cycle.")
            return False
        self.update_in_progress = True

        try:  # so we can still clean up update_in_progress
            nowstamp = monotonic_time_coarse()

            result_gather_adverts = self._async_gather_advert_data()

            self.update_metadevices()

            # Calculate per-device data
            #
            # Scanner entries have been loaded up with latest data, now we can
            # process data for all devices over all scanners.
            for device in self.devices.values():
                # Recalculate smoothed distances, last_seen etc
                device.calculate_data()

            self._refresh_areas_by_min_distance()

            # We might need to freshen deliberately on first start if no new scanners
            # were discovered in the first scan update. This is likely if nothing has changed
            # since the last time we booted.
            # if self._do_full_scanner_init:
            #     if not self._refresh_scanners():
            #         # _LOGGER.debug("Failed to refresh scanners, likely config entry not ready.")
            #         # don't fail the update, just try again next time.
            #         # self.last_update_success = False
            #         pass

            # If any *configured* devices have not yet been seen, create device
            # entries for them so they will claim the restored sensors in HA
            # (this prevents them from restoring at startup as "Unavailable" if they
            # are not currently visible, and will instead show as "Unknown" for
            # sensors and "Away" for device_trackers).
            if self.stamp_last_update == 0:
                # First run, let's do it.
                for _source_address in self.options.get(CONF_DEVICES, []):
                    self._get_or_create_device(_source_address)

            # Trigger creation of any new entities
            #
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
                        async_dispatcher_send(self.hass, SIGNAL_DEVICE_NEW, address)

            # Device Pruning (only runs periodically)
            self.prune_devices()

        finally:
            # end of async update
            self.update_in_progress = False

        self.stamp_last_update_started = nowstamp
        self.stamp_last_update = monotonic_time_coarse()
        self.last_update_success = True
        return result_gather_adverts

    def _async_gather_advert_data(self):
        """Perform the gathering of backend Bluetooth Data and updating scanners and devices."""
        nowstamp = monotonic_time_coarse()
        _timestamp_cutoff = nowstamp - min(PRUNE_TIME_DEFAULT, PRUNE_TIME_UNKNOWN_IRK)

        # Initialise ha_scanners if we haven't already
        if self._scanner_init_pending:
            self._refresh_scanners(force=True)

        for ha_scanner in self._hascanners:
            # Create / Get the BermudaDevice for this scanner
            scanner_device = self._get_device(ha_scanner.source)

            if scanner_device is None:
                # Looks like a scanner we haven't met, refresh the list.
                self._refresh_scanners(force=True)
                scanner_device = self._get_device(ha_scanner.source)

            if scanner_device is None:
                # Highly unusual. If we can't find an entry for the scanner
                # maybe it's from an integration that's not yet loaded, or
                # perhaps it's an unexpected type that we don't know how to
                # find.
                _LOGGER_SPAM_LESS.error(
                    f"missing_scanner_entry_{ha_scanner.source}",
                    "Failed to find config for scanner %s, this is probably a bug.",
                    ha_scanner.source,
                )
                continue

            scanner_device.async_as_scanner_update(ha_scanner)

            # Now go through the scanner's adverts and send them to our device objects.
            for bledevice, advertisementdata in ha_scanner.discovered_devices_and_advertisement_data.values():
                if adstamp := scanner_device.async_as_scanner_get_stamp(bledevice.address):
                    if adstamp < self.stamp_last_update_started - 3:
                        # skip older adverts that should already have been processed
                        continue
                if advertisementdata.rssi == -127:
                    # BlueZ is pushing bogus adverts for paired but absent devices.
                    continue

                device = self._get_or_create_device(bledevice.address)
                device.process_advertisement(scanner_device, advertisementdata)

        # end of for ha_scanner loop
        return True

    def prune_devices(self, force_pruning=False):
        """
        Scan through all collected devices, and remove those that meet Pruning criteria.

        By default no pruning will be done if it has been performed within the last
        PRUNE_TIME_INTERVAL, unless the force_pruning flag is set to True.
        """
        if self.stamp_last_prune > monotonic_time_coarse() - PRUNE_TIME_INTERVAL and not force_pruning:
            # We ran recently enough, bail out.
            return
        # stamp the run.
        nowstamp = self.stamp_last_prune = monotonic_time_coarse()
        stamp_known_irk = nowstamp - PRUNE_TIME_KNOWN_IRK
        stamp_unknown_irk = nowstamp - PRUNE_TIME_UNKNOWN_IRK

        # Prune any IRK MACs that have expired
        self.irk_manager.async_prune()

        # Prune devices.
        prune_list: list[str] = []  # list of addresses to be pruned
        prunable_stamps: dict[str, float] = {}  # dict of potential prunees if we need to be more aggressive.

        metadevice_source_keepers = set()
        for metadevice in self.metadevices.values():
            if len(metadevice.metadevice_sources) > 0:
                # Always keep the most recent source, which we keep in index 0.
                # This covers static iBeacon sources, and possibly IRKs that might exceed
                # the spec lifetime but are going stale because they're away for a bit.
                _first = True
                for address in metadevice.metadevice_sources:
                    if _device := self._get_device(address):
                        if _first or _device.last_seen > stamp_known_irk:
                            # The source has been seen within the spec's limits, keep it.
                            metadevice_source_keepers.add(address)
                            _first = False
                        else:
                            # It's too old to be an IRK, and otherwise we'll auto-detect it,
                            # so let's be rid of it.
                            prune_list.append(address)

        for device_address, device in self.devices.items():
            # Prune any devices that haven't been heard from for too long, but only
            # if we aren't actively tracking them and it's a traditional MAC address.
            # We just collect the addresses first, and do the pruning after exiting this iterator
            #
            # Reduced selection criteria - basically if if's not:
            # - a scanner (beacuse we need those!)
            # - any metadevice less than 15 minutes old (PRUNE_TIME_KNOWN_IRK)
            # - a private_ble device (because they will re-create anyway, plus we auto-sensor them
            # - create_sensor
            # then it should be up for pruning. A stale iBeacon that we don't actually track
            # should totally be pruned if it's no longer around.
            if (
                device_address not in metadevice_source_keepers
                and device not in self.metadevices
                and device_address not in self.scanner_list
                and (not device.create_sensor)  # Not if we track the device
                and (not device.is_scanner)  # redundant, but whatevs.
                and device.address_type != BDADDR_TYPE_NOT_MAC48
            ):
                if device.address_type == BDADDR_TYPE_RANDOM_RESOLVABLE:
                    # This is an *UNKNOWN* IRK source address, or a known one which is
                    # well and truly stale (ie, not in keepers).
                    # We prune unknown irk's aggressively because they pile up quickly
                    # in high-density situations, and *we* don't need to hang on to new
                    # enrollments because we'll seed them from PBLE.
                    if device.last_seen < stamp_unknown_irk:
                        _LOGGER.debug(
                            "Marking stale (%ds) Unknown IRK address for pruning: [%s] %s",
                            nowstamp - device.last_seen,
                            device_address,
                            device.name,
                        )
                        prune_list.append(device_address)
                    elif device.last_seen < nowstamp - 200:  # BlueZ cache time
                        # It's not stale, but we will prune it if we can't make our
                        # quota of PRUNE_MAX_COUNT we'll shave these off too.

                        # Note that because BlueZ doesn't give us timestamps, we guess them
                        # based on whether the rssi has changed. If we delete our existing
                        # device we have nothing to compare too and will forever churn them.
                        # This can change if we drop support for BlueZ or we find a way to
                        # make stamps (we could also just keep a separate list but meh)
                        prunable_stamps[device_address] = device.last_seen

                elif device.last_seen < nowstamp - PRUNE_TIME_DEFAULT:
                    # It's a static address, and stale.
                    _LOGGER.debug(
                        "Marking old device entry for pruning: %s",
                        device.name,
                    )
                    prune_list.append(device_address)
                else:
                    # Device is static, not tracked, not so old, but we might have to prune it anyway
                    prunable_stamps[device_address] = device.last_seen

            # Do nothing else at this level without excluding the keepers first.

        prune_quota_shortfall = len(self.devices) - len(prune_list) - PRUNE_MAX_COUNT
        if prune_quota_shortfall > 0:
            # We need to find more addresses to prune. Perhaps we live
            # in a busy train station, or are under some sort of BLE-MAC
            # DOS-attack.
            if len(prunable_stamps) > 0:
                # Sort the prunables by timestamp ascending
                sorted_addresses = sorted([(v, k) for k, v in prunable_stamps.items()])
                cutoff_index = min(len(sorted_addresses), prune_quota_shortfall)

                _LOGGER.debug(
                    "Prune quota short by %d. Pruning %d extra devices (down to age %0.2f seconds)",
                    prune_quota_shortfall,
                    cutoff_index,
                    nowstamp - sorted_addresses[prune_quota_shortfall - 1][0],
                )
                # pylint: disable-next=unused-variable
                for _stamp, address in sorted_addresses[: prune_quota_shortfall - 1]:
                    prune_list.append(address)
            else:
                _LOGGER.warning(
                    "Need to prune another %s devices to make quota, but no extra prunables available",
                    prune_quota_shortfall,
                )
        else:
            _LOGGER.debug(
                "Pruning %d available MACs, we are inside quota by %d.", len(prune_list), prune_quota_shortfall * -1
            )

        # ###############################################
        # Prune_list is now ready to action. It contains no keepers, and is already
        # expanded if necessary to meet quota, as much as we can.

        # Prune the source devices
        for device_address in prune_list:
            _LOGGER.debug("Acting on prune list for %s", device_address)
            del self.devices[device_address]

        # Clean out the scanners dicts in metadevices and scanners
        # (scanners will have entries if they are also beacons, although
        # their addresses should never get stale, but one day someone will
        # have a beacon that uses randomised source addresses for some reason.
        #
        # Just brute-force all devices, because it was getting a bit hairy
        # ensuring we hit the right ones, and the cost is fairly low and periodic.
        for device in self.devices.values():
            # if (
            #     device.is_scanner
            #     or METADEVICE_PRIVATE_BLE_DEVICE in device.metadevice_type
            #     or METADEVICE_IBEACON_DEVICE in device.metadevice_type
            # ):
            # clean out the metadevice_sources field
            for address in prune_list:
                if address in device.metadevice_sources:
                    device.metadevice_sources.remove(address)

            # clean out the device/scanner advert pairs
            for advert_tuple in list(device.adverts.keys()):
                if device.adverts[advert_tuple].device_address in prune_list:
                    _LOGGER.debug(
                        "Pruning metadevice advert %s aged %ds",
                        advert_tuple,
                        nowstamp - device.adverts[advert_tuple].stamp,
                    )
                    del device.adverts[advert_tuple]

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
                    if pb_entity.domain == Platform.DEVICE_TRACKER:
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
                        if pb_device:
                            metadevice.name_by_user = pb_device.name_by_user
                            metadevice.name_devreg = pb_device.name
                            metadevice.make_name()

                        # Ensure we track this PB entity so we get source address updates.
                        if pb_entity.entity_id not in self.pb_state_sources:
                            self.pb_state_sources[pb_entity.entity_id] = None  # FIXME: why none?

                        # Add metadevice to list so it gets included in update_metadevices
                        if metadevice.address not in self.metadevices:
                            self.metadevices[metadevice.address] = metadevice

                        if pb_source_address is not None:
                            # We've got a source MAC address!
                            pb_source_address = mac_norm(pb_source_address)

                            # Set up and tag the source device entry
                            source_device = self._get_or_create_device(pb_source_address)
                            source_device.metadevice_type.add(METADEVICE_TYPE_PRIVATE_BLE_SOURCE)

                            # Add source address. Don't remove anything, as pruning takes care of that.
                            if pb_source_address not in metadevice.metadevice_sources:
                                metadevice.metadevice_sources.insert(0, pb_source_address)

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
        happen each time a new MAC address is used by a given iBeacon,
        or each time an existing MAC sends a *new* iBeacon(!)

        This does not update the beacon's details (distance etc), that is done
        in the update_metadevices function after all data has been gathered.
        """
        if METADEVICE_TYPE_IBEACON_SOURCE not in source_device.metadevice_type:
            _LOGGER.error(
                "Only IBEACON_SOURCE devices can be used to see a beacon metadevice. %s is not",
                source_device.name,
            )
        if source_device.beacon_unique_id is None:
            _LOGGER.error("Source device %s is not a valid iBeacon!", source_device.name)
        else:
            metadevice = self._get_or_create_device(source_device.beacon_unique_id)
            if len(metadevice.metadevice_sources) == 0:
                # #### NEW METADEVICE #####
                # (do one-off init stuff here)
                if metadevice.address not in self.metadevices:
                    self.metadevices[metadevice.address] = metadevice

                # Copy over the beacon attributes
                metadevice.name_bt_serviceinfo = source_device.name_bt_serviceinfo
                metadevice.name_bt_local_name = source_device.name_bt_local_name
                metadevice.beacon_unique_id = source_device.beacon_unique_id
                metadevice.beacon_major = source_device.beacon_major
                metadevice.beacon_minor = source_device.beacon_minor
                metadevice.beacon_power = source_device.beacon_power
                metadevice.beacon_uuid = source_device.beacon_uuid

                # Check if we should set up sensors for this beacon
                if metadevice.address.upper() in self.options.get(CONF_DEVICES, []):
                    # This is a meta-device we track. Flag it for set-up:
                    metadevice.create_sensor = True

            # #### EXISTING METADEVICE ####
            # (only do things that might have to change when MAC address cycles etc)

            if source_device.address not in metadevice.metadevice_sources:
                # We have a *new* source device.
                # insert this device as a known source
                metadevice.metadevice_sources.insert(0, source_device.address)

                # If we have a new / better name, use that..
                metadevice.name_bt_serviceinfo = metadevice.name_bt_serviceinfo or source_device.name_bt_serviceinfo
                metadevice.name_bt_local_name = metadevice.name_bt_local_name or source_device.name_bt_local_name

    def update_metadevices(self):
        """
        Create or update iBeacon, Private_BLE and other meta-devices from
        the received advertisements.

        This must be run on each update cycle, after the calculations for each source
        device is done, since we will copy their results into the metadevice.

        Area matching and trilateration will be performed *after* this, as they need
        to consider the full collection of sources, not just the ones of a single
        source device.
        """
        # First seed the Private BLE metadevice skeletons. It will only do anything
        # if the self._do_private_device_init flag is set.
        # FIXME: Can we delete this? pble's should create at realtime as they
        # are detected now.
        self.discover_private_ble_metadevices()

        # iBeacon devices should already have their metadevices created, so nothing more to
        # set up for them.

        for metadevice in self.metadevices.values():
            # Find every known source device and copy their adverts in.

            # Keep track of whether we want to recalculate the name fields at the end.
            _want_name_update = False

            for source_address in metadevice.metadevice_sources:
                # Get the BermudaDevice holding those adverts
                # TODO: Verify it's OK to not create here. Problem is that if we do create,
                # it causes a binge/purge cycle during pruning since it has no adverts on it.
                source_device = self._get_device(source_address)
                if source_device is None:
                    # No ads current in the backend for this one. Not an issue, the mac might be old
                    # or now showing up yet.
                    # _LOGGER_SPAM_LESS.debug(
                    #     f"metaNoAdsFor_{metadevice.address}_{source_address}",
                    #     "Metadevice %s: no adverts for source MAC %s found during update_metadevices",
                    #     metadevice.__repr__(),
                    #     source_address,
                    # )
                    continue

                if (
                    METADEVICE_IBEACON_DEVICE in metadevice.metadevice_type
                    and metadevice.beacon_unique_id != source_device.beacon_unique_id
                ):
                    # iBeacons (specifically Bluecharms) change uuid on movement.
                    #
                    # This source device has changed its uuid, so we won't track it against
                    # this metadevice any more / for now, and we will also remove
                    # the existing scanner entries on the metadevice, to ensure it goes
                    # `unknown` immediately (assuming no other source devices show up)
                    #
                    # Note that this won't quick-away devices that change their MAC at the
                    # same time as changing their uuid (like manually altering the beacon
                    # in an Android 15+), since the old source device will still be a match.
                    # and will be subject to the nomal DEVTRACK_TIMEOUT.
                    #
                    for key_address, key_scanner in metadevice.adverts:
                        if key_address == source_device.address:
                            del metadevice.adverts[(key_address, key_scanner)]
                    continue  # to next metadevice_source

                # Copy every ADVERT_TUPLE into our metadevice
                for advert_tuple in source_device.adverts:
                    metadevice.adverts[advert_tuple] = source_device.adverts[advert_tuple]

                # Update last_seen if the source is newer.
                if metadevice.last_seen < source_device.last_seen:
                    metadevice.last_seen = source_device.last_seen

                # If not done already, set the source device's ref_power from our own. This will cause
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
                if source_device.ref_power != metadevice.ref_power:
                    source_device.set_ref_power(metadevice.ref_power)

                # anything that isn't already set to something interesting, overwrite
                # it with the new device's data.
                for key, val in source_device.items():
                    if val is any(
                        [
                            source_device.name_bt_local_name,
                            source_device.name_bt_serviceinfo,
                            source_device.manufacturer,
                        ]
                    ) and metadevice[key] in [None, False]:
                        metadevice[key] = val
                        _want_name_update = True

                if _want_name_update:
                    metadevice.make_name()

                # Anything that's VERY interesting, overwrite it regardless of what's already there:
                # INTERESTING:
                for key, val in source_device.items():
                    if val is any(
                        [
                            source_device.beacon_major,
                            source_device.beacon_minor,
                            source_device.beacon_power,
                            source_device.beacon_unique_id,
                            source_device.beacon_uuid,
                        ]
                    ):
                        metadevice[key] = val
                        # _want_name_update = True
            if _want_name_update:
                metadevice.make_name()

    def dt_mono_to_datetime(self, stamp) -> datetime:
        """Given a monotonic timestamp, convert to datetime object."""
        age = monotonic_time_coarse() - stamp
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
            if (
                # device.is_scanner is not True  # exclude scanners.
                device.create_sensor  # include any devices we are tracking
                # or device.metadevice_type in METADEVICE_SOURCETYPES  # and any source devices for PBLE, ibeacon etc
            ):
                self._refresh_area_by_min_distance(device)

    @dataclass
    class AreaTests:
        """
        Holds the results of Area-based tests.

        Likely to become a stand-alone class for performing the whole area-selection
        process.
        """

        device: str = ""
        scannername: tuple[str, str] = ("", "")
        areas: tuple[str, str] = ("", "")
        pcnt_diff: float = 0  # distance percentage difference.
        same_area: bool = False  # The old scanner is in the same area as us.
        # last_detection: tuple[float, float] = (0, 0)  # bt manager's last_detection field. Compare with ours.
        last_ad_age: tuple[float, float] = (0, 0)  # seconds since we last got *any* ad from scanner
        this_ad_age: tuple[float, float] = (0, 0)  # how old the *current* advert is on this scanner
        distance: tuple[float, float] = (0, 0)
        hist_min_max: tuple[float, float] = (0, 0)  # min/max distance from history
        # velocity: tuple[float, float] = (0, 0)
        # last_closer: tuple[float, float] = (0, 0)  # since old was closer and how long new has been closer
        reason: str | None = None  # reason/result

        def sensortext(self) -> str:
            """Return a text summary suitable for use in a sensor entity."""
            out = ""
            for var, val in vars(self).items():
                out += f"{var}|"
                if isinstance(val, tuple):
                    for v in val:
                        if isinstance(v, float):
                            out += f"{v:.2f}|"
                        else:
                            out += f"{v}"
                    # out += "\n"
                elif var == "pcnt_diff":
                    out += f"{val:.3f}"
                else:
                    out += f"{val}"
                out += "\n"
            return out[:255]

        def __str__(self) -> str:
            """
            Create string representation for easy debug logging/dumping
            and potentially a sensor for logging Area decisions.
            """
            out = ""
            for var, val in vars(self).items():
                out += f"** {var:20} "
                if isinstance(val, tuple):
                    for v in val:
                        if isinstance(v, float):
                            out += f"{v:.2f} "
                        else:
                            out += f"{v} "
                    out += "\n"
                elif var == "pcnt_diff":
                    out += f"{val:.3f}\n"
                else:
                    out += f"{val}\n"
            return out

    def _refresh_area_by_min_distance(self, device: BermudaDevice):
        """Very basic Area setting by finding closest proxy to a given device."""
        # The current area_scanner (which might be None) is the one to beat.
        closest_advert: BermudaAdvert | None = device.area_scanner

        _max_radius = self.options.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS)
        nowstamp = monotonic_time_coarse()

        tests = self.AreaTests()
        tests.device = device.name

        _superchatty = False  # Set to true for very verbose logging about area wins
        # if device.name in ("Ash Pixel IRK", "Garage", "Melinda iPhone"):
        #     _superchatty = True

        for advert in device.adverts.values():
            # Check each scanner and any time one is found to be closer / better than
            # the existing closest_scanner, replace it. At the end we should have the
            # right one. In theory.
            #
            # Note that rssi_distance is smoothed/filtered, and might be None if the last
            # reading was old enough that our algo decides it's "away".
            #
            # Every loop, every test is just a two-way race.

            # no competing against ourselves...
            if closest_advert is advert:
                continue

            # No winning with stale adverts. If we didn't win back when it was fresh,
            # we've no business winning now. This guards against a single advert
            # being reported by two proxies at slightly different times, and the area
            # switching to the later one after the reading times out on the first.
            # The timeout value is fairly arbitrary, if it's too small then we risk
            # ignoring valid reports from slow proxies (or if our processing loop is
            # delayed / lengthened). Too long and we add needless jumping around for a
            # device that isn't actually being actively detected.
            if advert.stamp < nowstamp - AREA_MAX_AD_AGE:
                continue

            # If closest scanner lacks critical data, we win.
            if (
                closest_advert is None
                or closest_advert.rssi_distance is None
                or closest_advert.area_id is None
                # Extra checks that are redundant but make linting easier later...
                or closest_advert.hist_distance_by_interval is None
            ):
                # Default Instawin!
                closest_advert = advert
                if _superchatty:
                    _LOGGER.debug(
                        "%s IS closesr to %s: Encumbant is invalid",
                        device.name,
                        advert.name,
                    )
                continue

            # NOTE:
            # From here on in, don't award a win directly. Instead award a loss if the new scanner is
            # not a contender, but otherwise build a set of test scores and make a determination at the
            # end.

            # If we lack critical data we cannot win...
            if (
                advert.rssi_distance is None
                or advert.rssi_distance > _max_radius
                or advert.area_id is None
                # Add others that are redundant, but make the linter happy
                # in future comparisons
                or advert.hist_distance_by_interval is None
            ):
                # if _superchatty:
                #     _LOGGER.debug(
                #         "%s not closest to %s vs %s: we lack the basics",
                #         device.name,
                #         scanner.name,
                #         closest_scanner.name,
                #     )
                continue

            # If we ARE NOT ACTUALLY CLOSER(!) we can not win.
            if closest_advert.rssi_distance < advert.rssi_distance:
                # we are not even closer!
                continue

            tests.reason = None  # ensure we don't trigger logging if no decision was made.
            tests.same_area = closest_advert.area_id == advert.area_id
            tests.areas = (closest_advert.area_name or "", advert.area_name or "")
            tests.scannername = (closest_advert.name, advert.name)
            tests.distance = (closest_advert.rssi_distance, advert.rssi_distance)
            # tests.velocity = (
            #     next((val for val in closest_scanner.hist_velocity), 0),
            #     next((val for val in scanner.hist_velocity), 0),
            # )

            # _old_last_detection = 999.9
            # _new_last_detection = 999.9
            # if (_closest_hascanner := self.devices[closest_scanner.scanner_address]._hascanner) is not None:
            #     _old_last_detection = _closest_hascanner.time_since_last_detection() or 999

            # if (_new_hascanner := self.devices[scanner.scanner_address]._hascanner) is not None:
            #     _new_last_detection = _new_hascanner.time_since_last_detection() or 999.0

            # tests.last_detection = (_old_last_detection, _new_last_detection)

            tests.last_ad_age = (
                nowstamp - closest_advert.scanner_device.last_seen,
                nowstamp - advert.scanner_device.last_seen,
            )
            tests.this_ad_age = (
                nowstamp - closest_advert.stamp,
                nowstamp - advert.stamp,
            )

            # when_old_was_last_this_close = 999.9
            # for seconds, old_dist in enumerate(closest_scanner.hist_distance_by_interval):
            #     if old_dist >= scanner.rssi_distance:
            #         when_old_was_last_this_close = seconds
            #         continue

            # time_new_has_been_closer_for = 999.9
            # for seconds, old_dist in enumerate(closest_scanner.hist_distance_by_interval):
            #     if old_dist >= max(scanner.hist_distance_by_interval[0 : seconds + 1]):
            #         time_new_has_been_closer_for = seconds
            #         continue

            # tests.last_closer = (
            #     # How recently closest_scanner was as close as new scanner is now
            #     when_old_was_last_this_close,
            #     # How long new scanner has been closer
            #     time_new_has_been_closer_for,
            # )
            # if tests.last_closer[0] < 3:
            #     # Our worst hasn't yet beat its best, we're out.
            #     if _superchatty:
            #         _LOGGER.debug(
            #             "%s not closest to %s vs %s: we need to dwell more\n%s",
            #             device.name,
            #             scanner.name,
            #             closest_scanner.name,
            #             tests,
            #         )
            #     continue

            _pda = advert.rssi_distance
            _pdb = closest_advert.rssi_distance
            tests.pcnt_diff = abs(_pda - _pdb) / (_pda + _pdb) / 2

            # Same area. Confirm freshness and distance.
            if (
                tests.same_area
                and (tests.this_ad_age[0] > tests.this_ad_age[1] + 1)
                and tests.distance[0] >= tests.distance[1]
            ):
                tests.reason = "WIN awarded for same area, newer, closer advert"
                closest_advert = advert
                continue

            # Win by historical min/max. Confirm available history and sufficient %diff.
            min_seconds = 3
            max_seconds = 5
            if len(advert.hist_distance_by_interval) > min_seconds:
                tests.hist_min_max = (
                    min(closest_advert.hist_distance_by_interval[:max_seconds]),  # Oldest min
                    max(advert.hist_distance_by_interval[:max_seconds]),  # Newest max
                )
                if (
                    tests.hist_min_max[1] < tests.hist_min_max[0]
                    and tests.pcnt_diff > 0.15  # and we're significantly closer.
                ):
                    tests.reason = "WIN on historical min/max"
                    closest_advert = advert
                    continue

            if tests.pcnt_diff < 0.18:
                # Didn't make the cut. We're not "different enough" given how
                # recently the previous nearest was updated.
                tests.reason = "LOSS - failed on percentage_difference"
                continue

            # If we made it through all of that, we're winning, so far!
            tests.reason = "WIN by not losing!"

            closest_advert = advert

        if _superchatty and tests.reason is not None:
            _LOGGER.info(
                "***************\n**************** %s *******************\n%s",
                tests.reason,
                tests,
            )

        _superchatty = False

        if device.area_scanner != closest_advert and tests.reason is not None:
            device.diag_area_switch = tests.sensortext()

        # Apply the newly-found closest scanner (or apply None if we didn't find one)
        device.apply_scanner_selection(closest_advert)

    def _refresh_scanners(self, force=False):
        """
        Refresh data on existing scanner objects, and rebuild if scannerlist has changed.

        Called on every update cycle, this handles the *fast* updates (such as updating
        timestamps). If it detects that the list of scanners has changed (or is called
        with force=True) then the full list of scanners will be rebuild by calling
        _rebuild_scanners.
        """
        self._rebuild_scanner_list(force=force)

    def _rebuild_scanner_list(self, force=False):
        """
        Rebuild Bermuda's internal list of scanners.

        Called on every update (via _refresh_scanners) but exits *quickly*
        *unless*:
          - the scanner set has changed or
          - force=True or
          - self._force_full_scanner_init=True

        This function also saves out the scanners list to the
        config entry, but only if has changed *AND* we haven't tried to do so in the
        last SAVEOUT_COOLDOWN seconds (10 seems to be enough). This is the reason
        we still run this function on every update. We could probably schedule the
        saveout task instead as long as we can bump it when required, then this task
        can probably be run *only* when we see device registry changes on scanners,
        or hook a callback on manager's hascanner activation/removal events.
        """
        _new_ha_scanners = set[BaseHaScanner]
        # Using new API in 2025.2
        _new_ha_scanners = set(self._manager.async_current_scanners())

        if _new_ha_scanners is self._hascanners or _new_ha_scanners == self._hascanners:
            # No changes.
            return

        _LOGGER.debug("HA Base Scanner Set has changed, rebuilding.")
        self._hascanners = _new_ha_scanners

        self._async_purge_removed_scanners()

        # So we can raise a single repair listing all area-less scanners:
        _scanners_without_areas = []

        # Find active HaBaseScanners in the backend and treat that as our
        # authoritative source of truth.
        #
        # scanner_ha: BaseHaScanner from HA's bluetooth backend
        # scanner_devreg_bt: DeviceEntry from HA's device_registry from Bluetooth integration
        # scanner_devreg_mac: DeviceEntry from HA's *other* integrations, like ESPHome, Shelly.
        # scanner_b: BermudaDevice entry

        for hascanner in self._hascanners:
            scanner_address = mac_norm(hascanner.source)
            scanner_b = self._get_or_create_device(scanner_address)
            scanner_b.async_as_scanner_init(hascanner)

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
            # the ethernet MAC for their connection links. We want both devices (if present) so that
            # we can let the user apply name and area settings to either device.

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
                if (altmac := mac_math_offset(scanner_address, offset)) is not None:
                    connlist.add(("bluetooth", altmac.upper()))
                    connlist.add(("mac", altmac))
                    maclist.add(altmac)

            # Requires 2025.3
            devreg_devices = self._device_registry.devices.get_entries(None, connections=connlist)
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
                    f"multimatch_devreg_{hascanner.source}",
                    "Unexpectedly got %d device registry matches for %s: %s\n",
                    devreg_count,
                    hascanner.name,
                    devreg_stringlist,
                )

            if scanner_devreg_bt is None and scanner_devreg_mac is None:
                _LOGGER_SPAM_LESS.error(
                    f"scanner_not_in_devreg_{scanner_address:s}",
                    "Failed to find scanner %s (%s) in Device Registry",
                    hascanner.name,
                    hascanner.source,
                )
                continue

            # We found the device entry and have created our scannerdevice,
            # now update any fields that might be new from the device reg.
            # First clear the existing to make prioritising the bt/mac matches
            # easier (feel free to refactor, bear in mind we prefer bt first)
            scanner_b.area_id = None

            _bt_name = None
            _mac_name = None
            _bt_name_by_user = None
            _mac_name_by_user = None

            if scanner_devreg_bt is not None:
                scanner_b.area_id = scanner_devreg_bt.area_id
                scanner_b.entry_id = scanner_devreg_bt.id
                _bt_name_by_user = scanner_devreg_bt.name_by_user
                _bt_name = scanner_devreg_bt.name
            if scanner_devreg_mac is not None:
                # Only apply if the bt device entry hasn't been applied:
                scanner_b.area_id = scanner_b.area_id or scanner_devreg_mac.area_id
                scanner_b.entry_id = scanner_b.entry_id or scanner_devreg_mac.id
                _mac_name = scanner_devreg_mac.name
                _mac_name_by_user = scanner_devreg_mac.name_by_user

            # As of ESPHome 2025.3.0 (via aioesphomeapi 29.3.1) ESPHome proxies now
            # report their BLE MAC address instead of their WIFI MAC in the hascanner
            # details.
            # To work around breaking the existing distance_to entities, retain the
            # ESPHome / Shelly integration's MAC as the unique_id
            scanner_b.unique_id = scanner_devreg_mac_address or scanner_devreg_bt_address or hascanner.source
            scanner_b.address_ble_mac = scanner_devreg_bt_address or scanner_devreg_mac_address or hascanner.source
            scanner_b.address_wifi_mac = scanner_devreg_mac_address

            # Populate the possible metadevice source MACs so that we capture any
            # data the scanner is sending (Shelly's already send broadcasts, and
            # future ESPHome Bermuda templates will, too). We can't easily tell
            # if our base address is the wifi mac, ble mac or ether mac, so whack
            # 'em all in and let the loop sort it out.
            for mac in (
                scanner_b.address_ble_mac,  # BLE mac, if known
                mac_math_offset(scanner_b.address_wifi_mac, 2),  # WIFI+2=BLE
                mac_math_offset(scanner_b.address_wifi_mac, -1),  # ETHER-1=BLE
            ):
                if (
                    mac is not None
                    and mac not in scanner_b.metadevice_sources
                    and mac != scanner_b.address  # because it won't need to be a metadevice
                ):
                    scanner_b.metadevice_sources.append(mac)

            # Bluetooth integ names scanners by address, so prefer the source integration's
            # autogenerated name over that.
            scanner_b.name_devreg = _mac_name or _bt_name
            # Bluetooth device reg is newer, so use the user-given name there if it exists.
            scanner_b.name_by_user = _bt_name_by_user or _mac_name_by_user
            # Apply any name changes.
            scanner_b.make_name()

            # Look up areas
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
                _scanners_without_areas.append(f"{scanner_b.name} [{scanner_b.address}]")
                scanner_b.area_name = f"Invalid Area for {scanner_b.name}"

        self._async_manage_repair_scanners_without_areas(_scanners_without_areas)

    #     return self._async_saveout_scanner_config()

    # def _async_saveout_scanner_config(self) -> bool:
    #     """
    #     Check if the scanner config has changed and save out to config_entry.

    #     Currently called on every update so it can choose when to do the actual
    #     saveout (which needs to be debounced). Should ultimately replace it with
    #     a cancellable scheduled task so we can bump it if desired and not have
    #     to poll for when to run it.

    #     Returns False if it can't do something it wants to, which should result
    #     in the caller trying it again next time around.
    #     """
    #     # bail out if the config entry isn't ready yet.
    #     if self.config_entry is None or self.config_entry.state != ConfigEntryState.LOADED:
    #         # _LOGGER.debug("Aborting refresh scanners due to config entry not being ready")
    #         self._do_full_scanner_init = True
    #         return False

    #     # Build the config_data and self.scanner_list structs fresh
    #     # ready to update our config entry if needed.
    #     self.scanner_list.clear()
    #     confdata_scanners: dict[str, dict] = {}
    #     for device in self.devices.values():
    #         if device.is_scanner:
    #             self.scanner_list.append(device.address)
    #             # Only add the necessary fields to confdata
    #             confdata_scanners[device.address] = {
    #                 key: getattr(device, key)
    #                 for key in [
    #                     "name",
    #                     "name_devreg",
    #                     "name_by_user",
    #                     "address",
    #                     "ref_power",
    #                     "unique_id",
    #                     "address_type",
    #                     "area_id",
    #                     "area_name",
    #                     "is_scanner",
    #                     "entry_id",
    #                 ]
    #             }

    #     if self.config_entry.data.get(CONFDATA_SCANNERS, {}) == confdata_scanners:
    #         # **** BAIL OUT, CONFIG HAS NOT CHANGED ****
    #         # _LOGGER.debug("Scanner configs are identical, not doing update.")
    #         self._do_full_scanner_init = False
    #         return True

    #     # We will arrive here every second for as long as the saved config is
    #     # different from our running config. But we don't want to save immediately,
    #     # since there is a lot of bouncing that happens during setup.

    #     # Make sure we haven't requested recently...
    #     if self.last_config_entry_update_request < monotonic_time_coarse() - SAVEOUT_COOLDOWN:
    #         # OK, we're good to go.
    #         self.last_config_entry_update_request = monotonic_time_coarse()
    #         _LOGGER.debug("Requesting save-out of scanner configs")
    #         self.hass.add_job(self.async_call_update_entry, confdata_scanners)
    #         return True
    #     return False

    def _async_purge_removed_scanners(self):
        """Demotes any devices that are no longer scanners based on new self.hascanners."""
        _scanners = [device.address for device in self.devices.values() if device.is_scanner]
        for ha_scanner in self._hascanners:
            scanner_address = mac_norm(ha_scanner.source)
            if scanner_address in _scanners:
                # This is still an extant HA Scanner, so we'll keep it.
                _scanners.remove(scanner_address)
        # Whatever's left are presumably no longer scanners.
        for address in _scanners:
            _LOGGER.info("Demoting ex-scanner %s", self.devices[address].name)
            self.devices[address].async_as_scanner_nolonger()

    def _async_manage_repair_scanners_without_areas(self, scannerlist: list[str]):
        """
        Raise a repair for any scanners that lack an area assignment.

        This function will take care of ensuring a repair is (re)raised
        or cleared (if the list is empty) when given a list of area-less scanner names.

        scannerlist should contain a friendly string to name each scanner missing an area.
        """
        if self._scanners_without_areas != scannerlist:
            self._scanners_without_areas = scannerlist
            # Clear any existing repair, because it's either resolved now (empty list) or we need to re-issue
            # the repair in order to update the scanner list (re-calling doesn't update it).
            ir.async_delete_issue(self.hass, DOMAIN, REPAIR_SCANNER_WITHOUT_AREA)

            if self._scanners_without_areas and len(self._scanners_without_areas) != 0:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    REPAIR_SCANNER_WITHOUT_AREA,
                    translation_key=REPAIR_SCANNER_WITHOUT_AREA,
                    translation_placeholders={
                        "scannerlist": "".join(f"- {name}\n" for name in self._scanners_without_areas),
                    },
                    severity=ir.IssueSeverity.ERROR,
                    is_fixable=False,
                )

    # *** Not required now that we don't reload for scanners.
    # @callback
    # def async_call_update_entry(self, confdata_scanners) -> None:
    #     """
    #     Call in the event loop to update the scanner entries in our config.

    #     We do this via add_job to ensure it runs in the event loop.
    #     """
    #     # Clear the flag for init and update the stamp
    #     self._do_full_scanner_init = False
    #     self.last_config_entry_update = monotonic_time_coarse()
    #     # Apply new config (will cause reload if there are changes)
    #     self.hass.config_entries.async_update_entry(
    #         self.config_entry,
    #         data={
    #             **self.config_entry.data,
    #             CONFDATA_SCANNERS: confdata_scanners,
    #         },
    #     )

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
            _stamp_redact = monotonic_time_coarse()
            out = cast("ServiceResponse", self.redact_data(out))
            _stamp_redact_elapsed = monotonic_time_coarse() - _stamp_redact
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
        _stamp = monotonic_time_coarse()

        # counter for incrementing replacement names (eg, SCANNER_n). The length
        # of the existing redaction list is a decent enough starting point.
        i = len(self.redactions)

        # SCANNERS
        for non_lower_address in self.scanner_list:
            address = non_lower_address.lower()
            if address not in self.redactions:
                i += 1
                for altmac in mac_explode_formats(address):
                    self.redactions[altmac] = f"{address[:2]}::SCANNER_{i}::{address[-2:]}"
        _LOGGER.debug("Redact scanners: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))
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
                    for altmac in mac_explode_formats(address):
                        self.redactions[altmac] = f"{address[:2]}::CFG_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is, but not a mac.
                    self.redactions[address] = f"CFG_OTHER_{1}_{address}"
        _LOGGER.debug("Redact confdevs: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))
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
                    for altmac in mac_explode_formats(address):
                        self.redactions[altmac] = f"{address[:2]}::OTHER_MAC_{i}::{address[-2:]}"
                else:
                    # Don't know what it is.
                    self.redactions[address] = f"OTHER_{i}_{address}"
        _LOGGER.debug("Redact therest: %ss, %d items", monotonic_time_coarse() - _stamp, len(self.redactions))
        _elapsed = monotonic_time_coarse() - _stamp
        if _elapsed > 0.5:
            _LOGGER.warning("Redaction list update took %.3f seconds, has %d items", _elapsed, len(self.redactions))
        else:
            _LOGGER.debug("Redaction list update took %.3f seconds, has %d items", _elapsed, len(self.redactions))

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

    def redact_data(self, data, first_recursion=True):
        """
        Wash any collection of data of any MAC addresses.

        Uses the redaction list of substitutions if already created, then
        washes any remaining mac-like addresses. This routine is recursive,
        so if you're changing it bear that in mind!
        """
        if first_recursion:
            # On first/outer call, refresh the redaction list to ensure
            # we don't let any new addresses slip through. Might be expensive
            # on first call, but will be much cheaper for subsequent calls.
            self.redaction_list_update()
            first_recursion = False

        if isinstance(data, str):  # Base Case
            datalower = data.lower()
            # the end of the recursive wormhole, do the actual work:
            if datalower in self.redactions:
                # Full string match, a quick short-circuit
                data = self.redactions[datalower]
            else:
                # Search for any of the redaction strings in the data.
                for find, fix in list(self.redactions.items()):
                    if find in datalower:
                        data = datalower.replace(find, fix)
                        # don't break out because there might be multiple fixes required.
            # redactions done, now replace any remaining MAC addresses
            # We are only looking for xx:xx:xx... format.
            return self._redact_generic_re.sub(self._redact_generic_sub, data)
        elif isinstance(data, dict):
            return {self.redact_data(k, False): self.redact_data(v, False) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.redact_data(v, False) for v in data]
        else:  # Base Case
            return data
