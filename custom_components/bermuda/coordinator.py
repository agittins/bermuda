"""DataUpdateCoordinator for Bermuda bluetooth data."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components import bluetooth
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    callback,
)
from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers import (
    floor_registry as fr,
)
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import (
    EVENT_DEVICE_REGISTRY_UPDATED,
    EventDeviceRegistryUpdatedData,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util.dt import get_age, now

from .area_entity import BermudaAreaEntityManager
from .bermuda_device import BermudaDevice
from .bermuda_irk import BermudaIrkManager
from .const import (
    _LOGGER,
    _LOGGER_SPAM_LESS,
    CONF_ADDRESS,
    CONF_AREA_ENTITIES,
    CONF_AREA_ENTITY_DISTANCE,
    CONF_AREA_ENTITY_DISTANCES,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_DEVTRACK_TIMEOUT,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSET,
    CONF_RSSI_OFFSETS,
    CONF_SCANNER,
    CONF_SMOOTHING_SAMPLES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_AREA_ENTITY_DISTANCE,
    DEFAULT_ATTENUATION,
    DEFAULT_DEVTRACK_TIMEOUT,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PRUNE_TIME_REDACTIONS,
    SAVEOUT_COOLDOWN,
    SIGNAL_DEVICE_IN100_NEW,
    SIGNAL_DEVICE_NEW,
    SUBENTRY_TYPE_CALIBRATION,
    SUBENTRY_TYPE_DEVICE,
    UPDATE_INTERVAL,
)
from .coordinator_metadevices import BermudaMetadeviceMixin
from .coordinator_microlocation import BermudaMicrolocationMixin
from .coordinator_scanners import BermudaScannerMixin
from .manufacturers import load_manufacturer_ids, lookup_manufacturer
from .pruning import prune_devices as _prune_devices
from .redaction import redact_value, update_redaction_list
from .trilateration import refresh_area_by_min_distance
from .util import mac_norm

if TYPE_CHECKING:
    from habluetooth import BaseHaScanner, BluetoothServiceInfoBleak
    from homeassistant.components.bluetooth import (
        BluetoothChange,
    )

    from . import BermudaConfigEntry

Cancellable = Callable[[], None]


class BermudaDataUpdateCoordinator(
    BermudaScannerMixin, BermudaMetadeviceMixin, BermudaMicrolocationMixin, DataUpdateCoordinator[None]
):
    """
    Class to manage fetching data from the Bluetooth component.

    Since we are not actually using an external API and only computing local
    data already gathered by the bluetooth integration, the update process is
    very cheap, and the processing process (currently) rather cheap.

    Future improvements:
    - Apply path-loss factor to calculated vectors based on previously measured losses.
    - Fine-tune with real-time measurements from fixed beacons for environmental factors.
    - Implement "radio map" for field strength estimates and wall-crossing attenuation.

    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BermudaConfigEntry,
    ) -> None:
        """Initialize."""
        self.sensor_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

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

        self.stamp_redactions_expiry: float | None = None

        self.update_in_progress: bool = False  # A lock to guard against huge backlogs / slow processing
        self.stamp_last_update: float = 0  # Last time we ran an update, from monotonic_time_coarse()
        self.stamp_last_update_started: float = 0
        self.stamp_last_prune: float = 0  # When we last pruned device list

        self.member_uuids: dict[int, str] = {}
        self.company_uuids: dict[int, str] = {}

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=1.0,
                immediate=False,
            ),
        )
        # DataUpdateCoordinator types config_entry as ConfigEntry | None (it can fall
        # back to a ContextVar), but Bermuda always constructs this coordinator with an
        # explicit entry (see async_setup_entry), so narrow it here for every call site.
        self.config_entry: BermudaConfigEntry = entry

        self._waitingfor_load_manufacturer_ids = True
        entry.async_create_background_task(
            hass, self.async_load_manufacturer_ids(), "Load Bluetooth IDs", eager_start=True
        )

        self._hascanners: set[BaseHaScanner] = set()  # Links to the backend scanners
        self._hascanner_timestamps: dict[str, dict[str, float]] = {}  # scanner_address, device_address, stamp
        self._scanner_list: set[str] = set()
        self._scanners: set[BermudaDevice] = set()  # Set of all in self.devices that is_scanner=True
        self.irk_manager = BermudaIrkManager()

        self.ar = ar.async_get(self.hass)
        self.er = er.async_get(self.hass)
        self.dr = dr.async_get(self.hass)
        self.fr = fr.async_get(self.hass)
        self.area_entity_manager = BermudaAreaEntityManager(self.hass)
        self.have_floors: bool = self.init_floors()

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

        # First time around we freshen the restored scanner info by
        # forcing a scan of the captured info.
        self._scanner_init_pending = True

        self._seed_configured_devices_done = False

        # First time go through the private ble devices to see if there's
        # any there for us to track.
        self._do_private_device_init = True

        # Listen for changes to the device registry and handle them.
        # Primarily for changes to scanners and Private BLE Devices.
        self.config_entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_DEVICE_REGISTRY_UPDATED, self.handle_devreg_changes)
        )

        self.options: dict[str, Any] = {}

        # Initialize with defaults for backward compatibility with older config entries
        self.options[CONF_ATTENUATION] = DEFAULT_ATTENUATION
        self.options[CONF_DEVTRACK_TIMEOUT] = DEFAULT_DEVTRACK_TIMEOUT
        self.options[CONF_MAX_RADIUS] = DEFAULT_MAX_RADIUS
        self.options[CONF_MAX_VELOCITY] = DEFAULT_MAX_VELOCITY
        self.options[CONF_REF_POWER] = DEFAULT_REF_POWER
        self.options[CONF_SMOOTHING_SAMPLES] = DEFAULT_SMOOTHING_SAMPLES
        self.options[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL
        self.options[CONF_RSSI_OFFSETS] = {}

        # Convert the entry's options mapping to a plain dict so it can be
        # serialised properly when it goes into the device and scanner classes.
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

        # Per-scanner RSSI offsets now live in calibration subentries. Mirror them
        # into the runtime options dict so the advert read-path stays unchanged;
        # this is the sole source of offsets once an entry has been migrated to v2.
        self.options[CONF_RSSI_OFFSETS] = {
            se.data[CONF_SCANNER]: se.data[CONF_RSSI_OFFSET]
            for se in entry.subentries.values()
            if se.subentry_type == SUBENTRY_TYPE_CALIBRATION
        }

        # Per-device enrolment (name / ref_power / timeout) from "device" subentries,
        # keyed by upper-case address. Applied to each device as it is created, and
        # read for the per-device away timeout. The subentry is the source of truth;
        # the ref_power number entity remains for live, in-session tweaks.
        self.device_config: dict[str, dict[str, Any]] = {
            se.data[CONF_ADDRESS].upper(): dict(se.data)
            for se in entry.subentries.values()
            if se.subentry_type == SUBENTRY_TYPE_DEVICE and se.data.get(CONF_ADDRESS)
        }

        self.devices: dict[str, BermudaDevice] = {}

        # Micro-location (sub-area RF fingerprinting) + the MCP-friendly services.
        self._microloc_init(hass, entry)

        # Register for newly discovered / changed BLE devices
        self.config_entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self.async_handle_advert,
                bluetooth.BluetoothCallbackMatcher(connectable=False),
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )

    def init_floors(self) -> bool:
        """Check if the system has floors configured, and enable sensors."""
        _have_floors: bool = False
        for area in self.ar.async_list_areas():
            if area.floor_id is not None:
                _have_floors = True
                break
        _LOGGER.debug("Have_floors is %s", _have_floors)
        return _have_floors

    async def async_get_bluetooth_manager_diagnostics(self) -> dict[str, Any]:
        """
        Return the Bluetooth manager's diagnostics.

        Home Assistant exposes no public API for the manager-level diagnostics,
        so the private helper is reached for here, isolated behind this single
        method and guarded so that a future HA change degrades diagnostics
        gracefully instead of breaking the whole integration at import time.
        """
        try:
            from homeassistant.components.bluetooth.api import _get_manager  # noqa: PLC0415

            manager = _get_manager(self.hass)
            return await manager.async_diagnostics()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Bluetooth manager diagnostics unavailable: %s", err)
            return {"error": f"bluetooth manager diagnostics unavailable: {err}"}

    def get_manufacturer_from_id(self, uuid: int | str) -> tuple[str, bool] | tuple[None, None]:
        """Map a Bluetooth UUID to a (name, is_generic) pair (see manufacturers module)."""
        return lookup_manufacturer(uuid, self.member_uuids, self.company_uuids)

    async def async_load_manufacturer_ids(self) -> None:
        """Load Bluetooth SIG manufacturer name mappings (see manufacturers module)."""
        try:
            self.member_uuids, self.company_uuids = await load_manufacturer_ids(self.hass)
        finally:
            self._waitingfor_load_manufacturer_ids = False

    @callback
    def handle_devreg_changes(self, ev: Event[EventDeviceRegistryUpdatedData]) -> None:
        """
        Update our scanner list if the device registry is changed.

        This catches area changes (on scanners) and any new/changed
        Private BLE Devices.
        """
        _LOGGER.debug("Device registry %s for device_id %s", ev.data["action"], ev.data.get("device_id"))

        device_id = ev.data.get("device_id")

        if ev.data["action"] in {"create", "update"}:
            if device_id is None:
                _LOGGER.error("Received Device Registry create/update without a device_id. ev.data: %s", ev.data)
                return

            # First look for any of our devices that have a stored id on them, it'll be quicker.
            for device in self.devices.values():
                if device.entry_id == device_id:
                    # We matched, most likely a scanner.
                    if device.is_scanner:
                        self._refresh_scanners(force=True)
                        return
            # Didn't match an existing, work through the connections etc.

            # Pull up the device registry entry for the device_id
            if device_entry := self.dr.async_get(ev.data["device_id"]):
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
                                if (_device := self.devices.get(ident_id.lower())) is not None:
                                    _device.name_by_user = device_entry.name_by_user
                                    _device.make_name()
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
                if scanner.entry_id == device_id:
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
            # Use a config-entry-bound background task so it is tracked and
            # cancelled cleanly on unload.
            self.config_entry.async_create_background_task(
                self.hass, self._async_update_data_internal(), "bermuda_advert_triggered_update"
            )

    def _check_all_platforms_created(self, address: str) -> None:
        """Checks if all platforms have finished loading a device's entities."""
        dev = self._get_device(address)
        if dev is not None:
            if all(
                [
                    dev.create_sensor_done,
                    dev.create_tracker_done,
                    dev.create_number_done,
                    dev.create_select_done,
                ]
            ):
                dev.create_all_done = True

    def sensor_created(self, address: str) -> None:
        """Allows sensor platform to report back that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_sensor_done = True
        else:
            _LOGGER.warning("Very odd, we got sensor_created for non-tracked device")
        self._check_all_platforms_created(address)

    def device_tracker_created(self, address: str) -> None:
        """Allows device_tracker platform to report back that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_tracker_done = True
        else:
            _LOGGER.warning("Very odd, we got device_tracker_created for non-tracked device")
        self._check_all_platforms_created(address)

    def number_created(self, address: str) -> None:
        """Receives report from number platform that sensors have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_number_done = True
        self._check_all_platforms_created(address)

    def select_created(self, address: str) -> None:
        """Receives report from the select platform that entities have been set up."""
        dev = self._get_device(address)
        if dev is not None:
            dev.create_select_done = True
        self._check_all_platforms_created(address)

    def in100_sensors_created(self, address: str) -> None:
        """
        Receives report from the sensor platform that IN100 telemetry sensors are set up.

        Independent of the create_all_done quorum: these sensors are created only for
        devices that broadcast IN100 telemetry, so this flag must not gate completion.
        """
        dev = self._get_device(address)
        if dev is not None:
            dev.create_in100_done = True

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

    def count_active_scanners(self, max_age: float = 10) -> int:
        """Returns count of scanners that have recently sent updates."""
        stamp = monotonic_time_coarse() - max_age  # seconds
        fresh_count = 0
        for scanner in self.get_active_scanner_summary():
            if scanner.get("last_stamp", 0) > stamp:
                fresh_count += 1
        return fresh_count

    def get_active_scanner_summary(self) -> list[dict[str, Any]]:
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
                "area_name": scannerdev.area_name,
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

    async def _async_update_data(self) -> None:
        """Implementation of DataUpdateCoordinator update_data function."""
        await self._async_update_data_internal()

    async def _async_update_data_internal(self) -> bool:
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

        Yield points (asyncio.sleep(0)) are inserted between heavy phases
        and periodically inside device loops to prevent blocking the event
        loop, which can trigger watchdog resets on radio co-processors
        (e.g. Zigbee ZBT-2 with a 6-second firmware watchdog).

        """
        if self._waitingfor_load_manufacturer_ids:
            _LOGGER.debug("Waiting for BT data load...")
            return True
        if self.update_in_progress:
            # Eeep!
            _LOGGER_SPAM_LESS.warning("update_still_running", "Previous update still running, skipping this cycle.")
            return False
        self.update_in_progress = True

        cycle_start = time.monotonic()
        nowstamp = monotonic_time_coarse()

        try:  # so we can still clean up update_in_progress
            # Phase 1: Gather adverts from the backend
            result_gather_adverts = self._async_gather_advert_data()
            await asyncio.sleep(0)

            # Phase 2: Update metadevices
            self.update_metadevices()
            await asyncio.sleep(0)

            # Phase 3: Calculate per-device data
            #
            # Scanner entries have been loaded up with latest data, now we can
            # process data for all devices over all scanners.
            for _device_count, device in enumerate(self.devices.values()):
                # Recalculate smoothed distances, last_seen etc
                device.calculate_data()
                if _device_count % 20 == 0:
                    await asyncio.sleep(0)

            await asyncio.sleep(0)

            # Phase 4: Area refresh
            self._refresh_areas_by_min_distance()

            # Phase 4a: presence-entity overrides — a triggered HA entity (motion,
            # contact, ...) can win the device's area over BLE at a virtual distance.
            self._apply_area_entity_overrides()

            # Phase 4b: refine to a named "micro-location" (eg Key hook) where a
            # saved fingerprint matches. Purely additive — never alters the Area.
            self._refresh_microlocations()
            await asyncio.sleep(0)

            # If any *configured* devices have not yet been seen, create device
            # entries for them so they will claim the restored sensors in HA
            # (this prevents them from restoring at startup as "Unavailable" if they
            # are not currently visible, and will instead show as "Unknown" for
            # sensors and "Away" for device_trackers).
            #
            # This isn't working right if it runs once. Bodge it for now (cost is low)
            # and sort it out when moving to device-based restoration (ie using DR/ER
            # to decide what devices to track and deprecating CONF_DEVICES)
            #
            # if not self._seed_configured_devices_done:
            for _source_address in self.options.get(CONF_DEVICES, []):
                self._get_or_create_device(_source_address)
            self._seed_configured_devices_done = True

            # Trigger creation of any new entities
            #
            # The devices are all updated now (and any new scanners and beacons seen have been added),
            # so let's ensure any devices that we create sensors for are set up ready to go.
            for address, device in self.devices.items():
                if device.create_sensor:
                    if not device.create_all_done:
                        _LOGGER.debug("Firing device_new for %s (%s)", device.name, address)
                        # Note that the below should be OK thread-wise, debugger indicates this is being
                        # called by _run in events.py, so pretty sure we are "in the event loop".
                        async_dispatcher_send(self.hass, SIGNAL_DEVICE_NEW, address)
                    # Spin up IN100 telemetry sensors only once a device actually broadcasts
                    # 0x0505 (may be long after its base sensors — fires until the platform
                    # reports back via in100_sensors_created).
                    if device.in100_detected and not device.create_in100_done:
                        async_dispatcher_send(self.hass, SIGNAL_DEVICE_IN100_NEW, address)

            # Phase 5: Device Pruning (only runs periodically)
            self.prune_devices()
            await asyncio.sleep(0)

            self.last_update_success = True
        except Exception:
            # The advert-triggered path calls this directly (bypassing the base
            # DataUpdateCoordinator), so record the failure on this path too.
            self.last_update_success = False
            raise
        finally:
            # end of async update
            self.update_in_progress = False
            # Advance the bookkeeping stamps even on failure, so a broken cycle
            # does not make every incoming advert spawn a fresh background update,
            # and the "skip already-processed adverts" optimisation keeps working.
            self.stamp_last_update_started = nowstamp
            self.stamp_last_update = monotonic_time_coarse()

        # Monitor cycle duration
        cycle_elapsed = time.monotonic() - cycle_start
        if cycle_elapsed > 2.0:
            _LOGGER.error(
                "Update cycle took %.2fs (devices: %d) — event loop may have been starved",
                cycle_elapsed,
                len(self.devices),
            )
        elif cycle_elapsed > 0.5:
            _LOGGER.warning(
                "Update cycle took %.2fs (devices: %d)",
                cycle_elapsed,
                len(self.devices),
            )

        return result_gather_adverts

    def _async_gather_advert_data(self) -> bool:
        """Perform the gathering of backend Bluetooth Data and updating scanners and devices."""
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

    def prune_devices(self, *, force_pruning: bool = False) -> None:
        """Remove stale devices to keep the device dict bounded (see pruning module)."""
        _prune_devices(self, force_pruning=force_pruning)

    def dt_mono_to_datetime(self, stamp: float) -> datetime:
        """Given a monotonic timestamp, convert to datetime object."""
        age = monotonic_time_coarse() - stamp
        return now() - timedelta(seconds=age)

    def dt_mono_to_age(self, stamp: float) -> str:
        """Convert monotonic timestamp to age (eg: "6 seconds ago")."""
        return get_age(self.dt_mono_to_datetime(stamp))

    def resolve_area_name(self, area_id: str) -> str | None:
        """
        Given an area_id, return the current area name.

        Will return None if the area id does *not* resolve to a single
        known area name.
        """
        areas = self.ar.async_get_area(area_id)
        if hasattr(areas, "name"):
            return getattr(areas, "name", "invalid_area")
        return None

    def _refresh_areas_by_min_distance(self) -> None:
        """Set area for ALL devices based on closest beacon."""
        for device in self.devices.values():
            if (
                # device.is_scanner is not True  # exclude scanners.
                device.create_sensor  # include any devices we are tracking
                # or device.metadevice_type in METADEVICE_SOURCETYPES  # and any source devices for PBLE, ibeacon etc
            ):
                self._refresh_area_by_min_distance(device)

    def _refresh_area_by_min_distance(self, device: BermudaDevice) -> None:
        """Set a device's closest scanner/area (see the trilateration module)."""
        refresh_area_by_min_distance(device, self.options)

    def _apply_area_entity_overrides(self) -> None:
        """
        Override a device's area when a triggered presence entity wins on virtual distance.

        Runs after BLE area selection: each configured HA entity that is "on" makes its
        area a candidate at a (small) virtual distance; if that beats the device's
        BLE-derived area_distance (or the device has no / Unknown area), the device is
        moved into the entity's area. Lets motion/contact sensors reinforce or override
        BLE presence. Ported from knoop7/bermuda-intent.
        """
        configured = self.options.get(CONF_AREA_ENTITIES, [])
        if not configured:
            return
        default_dist = self.options.get(CONF_AREA_ENTITY_DISTANCE, DEFAULT_AREA_ENTITY_DISTANCE)
        per_entity_dists = self.options.get(CONF_AREA_ENTITY_DISTANCES, {})
        triggered_areas = self.area_entity_manager.get_triggered_areas_with_distances(
            configured, per_entity_dists, default_dist
        )
        if not triggered_areas:
            return

        for device in self.devices.values():
            if not device.create_sensor:
                continue
            current_distance = device.area_distance
            current_area_id = device.area_id

            # Kept as a single Optional pair (rather than two separately-Optional
            # variables) so the "we have a winner" narrowing below covers both at once.
            best: tuple[str, float] | None = None
            for area_id, (_area_name, virtual_dist) in triggered_areas.items():
                if current_area_id == area_id:
                    # Already here via BLE: the entity only "wins" if it is virtually closer.
                    if current_distance is not None and current_distance <= virtual_dist:
                        continue
                    best = (area_id, virtual_dist)
                    break
                if (current_distance is None or virtual_dist < current_distance) and (
                    best is None or virtual_dist < best[1]
                ):
                    best = (area_id, virtual_dist)

            if best is not None:
                best_area_id, best_distance = best
                old_area = device.area_name
                device.apply_area_override(best_area_id, best_distance)
                if old_area != device.area_name:
                    _LOGGER.debug(
                        "Area entity override: %s moved %s -> %s (virtual %.2fm beat BLE %s)",
                        device.name,
                        old_area or "none",
                        device.area_name,
                        best_distance,
                        f"{current_distance:.1f}m" if current_distance is not None else "none",
                    )

    async def service_dump_devices(self, call: ServiceCall) -> ServiceResponse:
        """Return a dump of beacon advertisements by receiver."""
        out: dict[str, Any] = {}
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
            addresses += [address for address in self.pb_state_sources.values() if address is not None]

        # lowercase all the addresses for matching
        addresses = list(map(str.lower, addresses))

        # Build the dict of devices
        for address, device in self.devices.items():
            if len(addresses) == 0 or address.lower() in addresses:
                out[address] = device.to_dict()

        if redact:
            _stamp_redact = monotonic_time_coarse()
            out = cast("dict[str, Any]", self.redact_data(out))
            _stamp_redact_elapsed = monotonic_time_coarse() - _stamp_redact
            if _stamp_redact_elapsed > 3:  # It should be fast now.
                _LOGGER.warning("Dump devices redaction took %.2f seconds", _stamp_redact_elapsed)
            else:
                _LOGGER.debug("Dump devices redaction took %.2f seconds", _stamp_redact_elapsed)
        return out

    def redaction_list_update(self) -> None:
        """Freshen the MAC redaction substitution table (see redaction module)."""
        update_redaction_list(self.redactions, self.scanner_list, self.options.get(CONF_DEVICES, []), self.devices)
        self.stamp_redactions_expiry = monotonic_time_coarse() + PRUNE_TIME_REDACTIONS

    def redact_data(self, data: Any, *, first_recursion: bool = True) -> Any:
        """Recursively redact MAC addresses from a data structure (see redaction module)."""
        if first_recursion:
            # On the outer call, refresh the substitution table so new addresses
            # don't slip through; nested calls reuse it.
            self.redaction_list_update()
        return redact_value(data, self.redactions, self._redact_generic_re, self._redact_generic_sub)
