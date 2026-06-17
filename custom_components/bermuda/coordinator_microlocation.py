"""
Micro-location (sub-area RF fingerprinting) + the MCP-friendly service/intent API.

Split out of coordinator.py and mixed into BermudaDataUpdateCoordinator. Bermuda
resolves location only down to the Area of the nearest scanner; this adds finer
"micro-locations" — named spots (eg "Key hook") calibrated by example — plus a
service and intent surface so the whole thing is reachable from automations, MCP
clients and the voice assistant without the options menu. The matching maths and
persistence live in location_fingerprints.py; this is the integration glue.

Ported and adapted from belikh/bermuda2 (itself a fork of agittins/bermuda).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    _LOGGER,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_MAX_RADIUS,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    DEFAULT_ATTENUATION,
    DEFAULT_MAX_RADIUS,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_REF_POWER,
    DEFAULT_SMOOTHING_SAMPLES,
    DOMAIN,
    DOMAIN_PRIVATE_BLE_DEVICE,
    MICROLOC_CALIBRATION_SAMPLES,
    MICROLOC_HYSTERESIS_CYCLES,
    MICROLOC_MIN_USEFUL_SCANNERS,
)
from .location_fingerprints import Fingerprint, FingerprintMatcher, FingerprintStore
from .util import mac_norm

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse

    from . import BermudaConfigEntry
    from .bermuda_device import BermudaDevice
    from .location_fingerprints import MatchResult


class BermudaMicrolocationMixin:
    """Micro-location fingerprinting + MCP-friendly services, mixed into the coordinator."""

    def _microloc_init(self, hass: HomeAssistant, entry: BermudaConfigEntry) -> None:
        """Set up the fingerprint store/matcher, kick off a background load, and register services."""
        # The store persists named spots to disk; the matcher compares a device's
        # live distance vector against its saved spots each update cycle. Loading
        # happens in the background; matching is a no-op until it completes.
        self.fingerprints = FingerprintStore(hass)
        self.fingerprint_matcher = FingerprintMatcher()
        entry.async_create_background_task(
            hass, self.fingerprints.async_load(), "Load Bermuda micro-locations", eager_start=True
        )
        self._register_services(hass, entry)

    # --- live matching (runs each update cycle) -----------------------------

    def build_live_fingerprint(self, device: BermudaDevice) -> dict[str, float]:
        """
        Build a device's current RF fingerprint: scanner_address -> distance.

        Uses the smoothed ``rssi_distance`` from each advert that currently has a
        reading. For metadevices (which may have several source adverts on one
        scanner) we keep the closest reading per scanner.
        """
        live: dict[str, float] = {}
        for advert in device.adverts.values():
            distance = advert.rssi_distance
            if distance is None:
                continue
            current = live.get(advert.scanner_address)
            if current is None or distance < current:
                live[advert.scanner_address] = distance
        return live

    def _refresh_microlocations(self) -> None:
        """Match each tracked device's live fingerprint against its saved spots."""
        if not self.fingerprints.loaded:
            # Store still loading from disk; try again next cycle.
            return
        for device in self.devices.values():
            if not device.create_sensor:
                # Only bother for devices we actually track / expose.
                continue
            candidates = self.fingerprints.list(device.address)
            if not candidates:
                self._apply_microloc_result(device, None)
                continue
            live = self.build_live_fingerprint(device)
            result = self.fingerprint_matcher.match(live, candidates)
            self._apply_microloc_result(device, result)

    def _apply_microloc_result(self, device: BermudaDevice, result: MatchResult | None) -> None:
        """
        Apply a match result to a device, with switch hysteresis.

        A new winner (including "no spot") must persist for MICROLOC_HYSTERESIS_CYCLES
        cycles before we change the reported micro-location, so noisy readings don't
        make it flap.
        """
        winner_id = result.id if (result is not None and result.accepted) else None

        prev_id, streak = device.micro_location_streak
        streak = streak + 1 if winner_id == prev_id else 1
        device.micro_location_streak = (winner_id, streak)

        if streak < MICROLOC_HYSTERESIS_CYCLES:
            # Not yet persistent enough to commit to a change.
            return

        if winner_id is None:
            if device.micro_location_id is not None:
                _LOGGER.debug("Device %s left micro-location '%s'", device.name, device.micro_location_name)
                device.micro_location_id = None
                device.micro_location_name = None
                device.micro_location_confidence = None
        else:
            if device.micro_location_id != winner_id:
                _LOGGER.debug("Device %s now at micro-location '%s'", device.name, result.name)
            device.micro_location_id = result.id
            device.micro_location_name = result.name
            device.micro_location_confidence = result.confidence
            device.micro_location_last_seen = result.name

    # --- service registration ----------------------------------------------

    def _register_services(self, hass: HomeAssistant, entry: BermudaConfigEntry) -> None:
        """Register the micro-location and config-helper services (removed on unload)."""
        opt = SupportsResponse.OPTIONAL
        text = cv.string
        services: list[tuple] = [
            (
                "calibrate_location",
                self.service_calibrate_location,
                vol.Schema({vol.Required("device"): text, vol.Required("name"): text, vol.Optional("area"): text}),
                opt,
            ),
            ("where_is", self.service_where_is, vol.Schema({vol.Required("device"): text}), opt),
            ("list_locations", self.service_list_locations, vol.Schema({vol.Optional("device"): text}), opt),
            (
                "remove_location",
                self.service_remove_location,
                vol.Schema({vol.Required("device"): text, vol.Required("name"): text}),
                opt,
            ),
            (
                "rename_location",
                self.service_rename_location,
                vol.Schema({vol.Required("device"): text, vol.Required("name"): text, vol.Required("new_name"): text}),
                opt,
            ),
            ("track_device", self.service_track_device, vol.Schema({vol.Required("device"): text}), opt),
            ("untrack_device", self.service_untrack_device, vol.Schema({vol.Required("device"): text}), opt),
            (
                "set_global_calibration",
                self.service_set_global_calibration,
                vol.Schema(
                    {vol.Optional("ref_power"): vol.Coerce(float), vol.Optional("attenuation"): vol.Coerce(float)}
                ),
                opt,
            ),
            (
                "set_scanner_offset",
                self.service_set_scanner_offset,
                vol.Schema({vol.Required("scanner"): text, vol.Required("rssi_offset"): vol.Coerce(float)}),
                opt,
            ),
            ("get_config", self.service_get_config, vol.Schema({}), SupportsResponse.ONLY),
        ]
        for name, handler, schema, response in services:
            hass.services.async_register(DOMAIN, name, handler, schema, response)

        names = [name for name, *_ in services]

        def _unregister() -> None:
            for name in names:
                hass.services.async_remove(DOMAIN, name)

        entry.async_on_unload(_unregister)

    # --- resolution helpers -------------------------------------------------

    def _resolve_device(self, value: str) -> BermudaDevice | None:
        """
        Resolve a service/intent argument to a BermudaDevice.

        Accepts a Home Assistant device-registry id (eg from a device picker),
        a raw address (MAC / iBeacon uuid / IRK), or a friendly device name.
        """
        if not value:
            return None
        # Home Assistant device-registry id?
        if (devreg := self.dr.async_get(value)) is not None:
            for conn_type, conn_value in devreg.connections:
                if conn_type in {DOMAIN_PRIVATE_BLE_DEVICE, dr.CONNECTION_BLUETOOTH, "ibeacon"} and (
                    device := self.devices.get(mac_norm(conn_value))
                ):
                    return device
            for ident_domain, ident in devreg.identifiers:
                if ident_domain == DOMAIN and (device := self.devices.get(mac_norm(ident.split("_")[0]))):
                    return device
        # A raw address?
        if (device := self.devices.get(mac_norm(value))) is not None:
            return device
        # A friendly name?
        lowered = value.casefold()
        for device in self.devices.values():
            if device.name and device.name.casefold() == lowered:
                return device
        return None

    def _require_device(self, value: str) -> BermudaDevice:
        """Resolve a device or raise a user-facing error."""
        device = self._resolve_device(value)
        if device is None:
            msg = f"Bermuda doesn't know any device matching '{value}'."
            raise ServiceValidationError(msg)
        return device

    def _resolve_area_id(self, value: str | None) -> str | None:
        """Resolve an area id or area name to an area id, or None if not given."""
        if not value:
            return None
        if self.ar.async_get_area(value) is not None:
            return value
        if (area := self.ar.async_get_area_by_name(value)) is not None:
            return area.id
        msg = f"No Home Assistant area matches '{value}'."
        raise ServiceValidationError(msg)

    # --- calibration core (shared by service + intent) ----------------------

    def calibrate_location(self, device: BermudaDevice, name: str, area_id: str | None = None) -> dict:
        """
        Snapshot ``device``'s current fingerprint and save it as a named spot.

        Recalibrating an existing spot (same device + name) updates it in place.
        Returns a summary dict suitable for a service response or speech.
        """
        vector: dict[str, float] = {}
        vector_std: dict[str, float] = {}
        rssi_vector: dict[str, float] = {}
        samples_used = 0
        for advert in device.adverts.values():
            history = [d for d in advert.hist_distance_by_interval[:MICROLOC_CALIBRATION_SAMPLES] if d is not None]
            if not history and advert.rssi_distance is not None:
                history = [advert.rssi_distance]
            if not history:
                continue
            mean = sum(history) / len(history)
            variance = sum((value - mean) ** 2 for value in history) / len(history)
            scanner = advert.scanner_address
            if scanner not in vector or mean < vector[scanner]:
                vector[scanner] = mean
                vector_std[scanner] = variance**0.5
                if advert.rssi is not None:
                    rssi_vector[scanner] = float(advert.rssi)
            samples_used = max(samples_used, len(history))

        if not vector:
            msg = (
                f"No scanners currently hear '{device.name}', so there's nothing to calibrate. "
                "Make sure the device is awake and in range of at least one proxy."
            )
            raise ServiceValidationError(msg)

        # Default the spot's area to wherever Bermuda currently places the device.
        if area_id is None:
            area_id = device.area_id
        floor_id = None
        if area_id is not None and (area := self.ar.async_get_area(area_id)) is not None:
            floor_id = area.floor_id

        existing = self.fingerprints.find_by_name(device.address, name)
        fingerprint = Fingerprint(
            name=name,
            device_address=device.address,
            vector=vector,
            vector_std=vector_std,
            rssi_vector=rssi_vector,
            area_id=area_id,
            floor_id=floor_id,
            sample_count=samples_used,
        )
        if existing is not None:
            # Preserve identity/age when recalibrating.
            fingerprint.id = existing.id
            fingerprint.created = existing.created
        self.fingerprints.add(fingerprint)
        # Let the new spot win cleanly on the next cycle.
        device.micro_location_streak = (None, 0)
        return self._fingerprint_summary(fingerprint, device, replaced=existing is not None)

    def _fingerprint_summary(
        self, fingerprint: Fingerprint, device: BermudaDevice | None = None, *, replaced: bool = False
    ) -> dict:
        """Build a JSON-friendly summary of a saved spot."""
        scanners = {}
        for scanner_addr, distance in sorted(fingerprint.vector.items(), key=lambda item: item[1]):
            scanner_device = self.devices.get(scanner_addr)
            scanners[scanner_device.name if scanner_device else scanner_addr] = round(distance, 2)
        summary = {
            "id": fingerprint.id,
            "name": fingerprint.name,
            "device": device.name if device else None,
            "device_address": fingerprint.device_address,
            "area_id": fingerprint.area_id,
            "area_name": self.resolve_area_name(fingerprint.area_id) if fingerprint.area_id else None,
            "scanner_count": len(fingerprint.vector),
            "scanners": scanners,
            "samples": fingerprint.sample_count,
            "replaced": replaced,
        }
        if len(fingerprint.vector) < MICROLOC_MIN_USEFUL_SCANNERS:
            summary["warning"] = (
                f"Only {len(fingerprint.vector)} proxy heard this device. Micro-locations work best with "
                f"{MICROLOC_MIN_USEFUL_SCANNERS}+ proxies in range; with fewer, this spot may be hard to "
                "tell apart from nearby ones."
            )
        return summary

    def _device_location(self, device: BermudaDevice) -> dict:
        """Build a 'where is this device' answer: area + micro-location + scores."""
        candidates = self.fingerprints.list(device.address)
        result = None
        if candidates:
            result = self.fingerprint_matcher.match(self.build_live_fingerprint(device), candidates)
        out: dict = {
            "device": device.name,
            "device_address": device.address,
            "area_name": device.area_name,
            "area_id": device.area_id,
            "floor_name": device.floor_name,
            "micro_location": device.micro_location_name,
            "micro_location_confidence": device.micro_location_confidence,
            "distance": round(device.area_distance, 2) if device.area_distance is not None else None,
        }
        if result is not None:
            out["best_match"] = result.name if result.accepted else None
            out["best_match_confidence"] = result.confidence
            out["scores"] = [{"name": name, "score": round(score, 2)} for (_fid, name, score) in result.scores]
        return out

    def _update_options(self, **changes) -> None:
        """Persist option changes to the config entry (triggers a reload)."""
        new_options = dict(self.config_entry.options)
        new_options.update(changes)
        self.hass.config_entries.async_update_entry(self.config_entry, options=new_options)

    # --- service handlers ---------------------------------------------------

    async def service_calibrate_location(self, call: ServiceCall) -> ServiceResponse:
        """Capture the current fingerprint of a device as a named micro-location."""
        device = self._require_device(call.data["device"])
        name = call.data["name"].strip()
        if not name:
            msg = "Please provide a name for the location."
            raise ServiceValidationError(msg)
        area_id = self._resolve_area_id(call.data.get("area"))
        return cast("ServiceResponse", self.calibrate_location(device, name, area_id))

    async def service_where_is(self, call: ServiceCall) -> ServiceResponse:
        """Report a device's current area and micro-location."""
        device = self._require_device(call.data["device"])
        return cast("ServiceResponse", self._device_location(device))

    async def service_list_locations(self, call: ServiceCall) -> ServiceResponse:
        """List saved micro-locations, optionally filtered to one device."""
        device_value = call.data.get("device")
        device_address = self._require_device(device_value).address if device_value else None
        locations = [
            self._fingerprint_summary(fingerprint, self.devices.get(fingerprint.device_address))
            for fingerprint in self.fingerprints.list(device_address)
        ]
        return {"count": len(locations), "locations": locations}

    async def service_remove_location(self, call: ServiceCall) -> ServiceResponse:
        """Delete a device's saved micro-location by name."""
        device = self._require_device(call.data["device"])
        name = call.data["name"]
        fingerprint = self.fingerprints.find_by_name(device.address, name)
        if fingerprint is None:
            msg = f"'{device.name}' has no saved location called '{name}'."
            raise ServiceValidationError(msg)
        self.fingerprints.remove(fingerprint.id)
        if device.micro_location_id == fingerprint.id:
            device.micro_location_id = None
            device.micro_location_name = None
            device.micro_location_confidence = None
        device.micro_location_streak = (None, 0)
        return {"removed": True, "name": name, "device": device.name}

    async def service_rename_location(self, call: ServiceCall) -> ServiceResponse:
        """Rename a device's saved micro-location."""
        device = self._require_device(call.data["device"])
        name = call.data["name"]
        new_name = call.data["new_name"].strip()
        if not new_name:
            msg = "Please provide a new name."
            raise ServiceValidationError(msg)
        fingerprint = self.fingerprints.find_by_name(device.address, name)
        if fingerprint is None:
            msg = f"'{device.name}' has no saved location called '{name}'."
            raise ServiceValidationError(msg)
        self.fingerprints.rename(fingerprint.id, new_name)
        if device.micro_location_id == fingerprint.id:
            device.micro_location_name = new_name
        return {"renamed": True, "old_name": name, "new_name": new_name, "device": device.name}

    async def service_track_device(self, call: ServiceCall) -> ServiceResponse:
        """Add a device to the tracked/configured devices list (creates sensors)."""
        device = self._require_device(call.data["device"])
        configured = [address.upper() for address in self.config_entry.options.get(CONF_DEVICES, [])]
        address = device.address.upper()
        if address in configured:
            return {"tracked": True, "already": True, "device": device.name}
        configured.append(address)
        self._update_options(**{CONF_DEVICES: configured})
        return {"tracked": True, "already": False, "device": device.name, "device_address": device.address}

    async def service_untrack_device(self, call: ServiceCall) -> ServiceResponse:
        """Remove a device from the tracked/configured devices list."""
        device = self._require_device(call.data["device"])
        configured = [address.upper() for address in self.config_entry.options.get(CONF_DEVICES, [])]
        address = device.address.upper()
        if address not in configured:
            return {"tracked": False, "already": True, "device": device.name}
        self._update_options(**{CONF_DEVICES: [a for a in configured if a != address]})
        return {"tracked": False, "already": False, "device": device.name}

    async def service_set_global_calibration(self, call: ServiceCall) -> ServiceResponse:
        """Set the global ref_power and/or attenuation calibration values."""
        changes: dict = {}
        if "ref_power" in call.data:
            changes[CONF_REF_POWER] = call.data["ref_power"]
        if "attenuation" in call.data:
            changes[CONF_ATTENUATION] = call.data["attenuation"]
        if not changes:
            msg = "Provide ref_power and/or attenuation to set."
            raise ServiceValidationError(msg)
        self._update_options(**changes)
        return {"updated": True, **changes}

    async def service_set_scanner_offset(self, call: ServiceCall) -> ServiceResponse:
        """Set a per-scanner RSSI offset (the Calibration-2 menu, as a service)."""
        scanner = self._require_device(call.data["scanner"])
        if scanner.address not in self.scanner_list:
            msg = f"'{scanner.name}' is not a known Bermuda scanner/proxy."
            raise ServiceValidationError(msg)
        offset = max(min(call.data["rssi_offset"], 127), -127)
        offsets = dict(self.config_entry.options.get(CONF_RSSI_OFFSETS, {}))
        offsets[scanner.address] = offset
        self._update_options(**{CONF_RSSI_OFFSETS: offsets})
        return {"updated": True, "scanner": scanner.name, "rssi_offset": offset}

    async def service_get_config(self, call: ServiceCall) -> ServiceResponse:
        """Return the current Bermuda configuration, for MCP introspection."""
        opts = self.config_entry.options
        tracked = []
        for address in opts.get(CONF_DEVICES, []):
            device = self.devices.get(mac_norm(address))
            tracked.append({"name": device.name if device else None, "address": address})
        return {
            "global": {
                CONF_REF_POWER: opts.get(CONF_REF_POWER, DEFAULT_REF_POWER),
                CONF_ATTENUATION: opts.get(CONF_ATTENUATION, DEFAULT_ATTENUATION),
                CONF_MAX_RADIUS: opts.get(CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS),
                CONF_MAX_VELOCITY: opts.get(CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY),
                CONF_SMOOTHING_SAMPLES: opts.get(CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES),
            },
            "scanner_offsets": dict(opts.get(CONF_RSSI_OFFSETS, {})),
            "tracked_devices": tracked,
            "scanners": list(self.get_active_scanner_summary()),
            "micro_location_count": len(self.fingerprints.list()),
        }
