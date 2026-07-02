"""
Metadevice (iBeacon / IRK / Private-BLE) management mixin for the coordinator.

Split out of coordinator.py; mixed into BermudaDataUpdateCoordinator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import (
    _LOGGER,
    CONF_DEVICES,
    DOMAIN_PRIVATE_BLE_DEVICE,
    METADEVICE_IBEACON_DEVICE,
    METADEVICE_TYPE_IBEACON_SOURCE,
    METADEVICE_TYPE_PRIVATE_BLE_SOURCE,
)
from .util import mac_norm

if TYPE_CHECKING:
    from typing import Any

    from homeassistant.core import HomeAssistant

    # Imported directly (not as the `dr`/`er`-aliased modules) so these type-only
    # names don't shadow the `dr`/`er` attributes declared below.
    from homeassistant.helpers.device_registry import DeviceRegistry
    from homeassistant.helpers.entity_registry import EntityRegistry

    from .bermuda_device import BermudaDevice


class BermudaMetadeviceMixin:
    """Metadevice discovery and per-cycle update, mixed into the coordinator."""

    if TYPE_CHECKING:
        # Attributes/methods provided by BermudaDataUpdateCoordinator, the concrete
        # class this mixin is always combined into (see coordinator.py:__init__).
        # Declared here only so mypy can see them; nothing here runs at import time.
        hass: HomeAssistant
        er: EntityRegistry
        dr: DeviceRegistry
        options: dict[str, Any]
        pb_state_sources: dict[str, str | None]
        metadevices: dict[str, BermudaDevice]
        _do_private_device_init: bool

        def _get_or_create_device(self, address: str) -> BermudaDevice: ...
        def _get_device(self, address: str) -> BermudaDevice | None: ...

    def discover_private_ble_metadevices(self) -> None:
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
                pb_entities = self.er.entities.get_entries_for_config_entry_id(pb_entry.entry_id)
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
                            pb_device = self.dr.async_get(pb_entity.device_id)
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

                        # Track this PB entity for source address updates (None = not yet resolved)
                        if pb_entity.entity_id not in self.pb_state_sources:
                            self.pb_state_sources[pb_entity.entity_id] = None

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

    def register_ibeacon_source(self, source_device: BermudaDevice) -> None:
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
            return
        if source_device.beacon_unique_id is None:
            _LOGGER.error("Source device %s is not a valid iBeacon!", source_device.name)
            return

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

    def update_metadevices(self) -> None:
        """
        Create or update iBeacon, Private_BLE and other meta-devices from
        the received advertisements.

        This must be run on each update cycle, after the calculations for each source
        device is done, since we will copy their results into the metadevice.

        Area matching and trilateration will be performed *after* this, as they need
        to consider the full collection of sources, not just the ones of a single
        source device.
        """
        # Seed Private BLE metadevice skeletons (only runs if _do_private_device_init is set).
        # Note: pble devices are also created at realtime when detected.
        self.discover_private_ble_metadevices()

        # iBeacon devices should already have their metadevices created, so nothing more to
        # set up for them.

        for metadevice in self.metadevices.values():
            # Find every known source device and copy their adverts in.

            # Keep track of whether we want to recalculate the name fields at the end.
            _want_name_update = False
            _sources_to_remove = []

            for source_address in metadevice.metadevice_sources:
                # Get the BermudaDevice holding those adverts.
                # We use _get_device (not _get_or_create) to avoid binge/purge cycle
                # during pruning for devices without active adverts.
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
                    # This source device no longer has the same ibeacon uuid+maj+min as
                    # the metadevice has.
                    # Some iBeacons (specifically Bluecharms) change uuid on movement.
                    #
                    # This source device has changed its uuid, so we won't track it against
                    # this metadevice any more / for now, and we will also remove
                    # the existing scanner entries on the metadevice, to ensure it goes
                    # `unknown` immediately (assuming no other source devices show up)
                    #
                    # Note that this won't quick-away devices that change their MAC at the
                    # same time as changing their uuid (like manually altering the beacon
                    # in an Android 15+), since the old source device will still be a match.
                    # and will be subject to the normal DEVTRACK_TIMEOUT.
                    #
                    _LOGGER.debug(
                        "Source %s for metadev %s changed iBeacon identifiers, severing", source_device, metadevice
                    )
                    for key_address, key_scanner in list(metadevice.adverts):
                        if key_address == source_device.address:
                            del metadevice.adverts[(key_address, key_scanner)]
                    if source_device.address in metadevice.metadevice_sources:
                        # Remove this source from the list once we're done iterating on it
                        _sources_to_remove.append(source_device.address)
                    continue  # to next metadevice_source

                # Copy every ADVERT_TUPLE into our metadevice
                for advert_tuple in source_device.adverts:
                    metadevice.adverts[advert_tuple] = source_device.adverts[advert_tuple]

                # Update last_seen if the source is newer.
                metadevice.last_seen = max(metadevice.last_seen, source_device.last_seen)

                # If not done already, set the source device's ref_power from our own. This will cause
                # the source device and all its scanner entries to update their
                # distance measurements. This won't affect Area wins though, because
                # they are "relative", not absolute.

                # Known limitations when multiple metadevices share a source:
                # - They may "fight" over ref_power if different values are set.
                # - Non-meta device (if tracked) uses meta device's ref_power.
                # These are edge cases with minimal practical impact.

                # Note we are setting the ref_power on the source_device, not the
                # individual scanner entries (it will propagate to them though)
                if source_device.ref_power != metadevice.ref_power:
                    source_device.set_ref_power(metadevice.ref_power)

                # Copy naming / manufacturer fields from the source device, but
                # only where the metadevice doesn't already have a value.
                for _attr in ("name_bt_local_name", "name_bt_serviceinfo", "manufacturer"):
                    _srcval = getattr(source_device, _attr)
                    if _srcval is not None and getattr(metadevice, _attr) in (None, False):
                        setattr(metadevice, _attr, _srcval)
                        _want_name_update = True

                # Always propagate the beacon identity fields, since these are
                # what define the metadevice.
                for _attr in ("beacon_major", "beacon_minor", "beacon_power", "beacon_unique_id", "beacon_uuid"):
                    _srcval = getattr(source_device, _attr)
                    if _srcval is not None:
                        setattr(metadevice, _attr, _srcval)
            # Done iterating sources, remove any to be dropped
            for source in _sources_to_remove:
                metadevice.metadevice_sources.remove(source)
            if _want_name_update:
                metadevice.make_name()
