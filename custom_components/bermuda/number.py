"""Create Number entities - like per-device rssi ref_power, etc."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberExtraStoredData,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_DEVICE_NEW, SIGNAL_SCANNERS_CHANGED
from .entity import BermudaEntity

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import BermudaConfigEntry
    from .coordinator import BermudaDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BermudaConfigEntry,
    async_add_devices: AddEntitiesCallback,
) -> None:
    """Load Number entities for a config entry."""
    coordinator: BermudaDataUpdateCoordinator = entry.runtime_data.coordinator

    created_devices = []  # list of devices we've already created entities for

    @callback
    def device_new(address: str) -> None:
        """
        Create entities for newly-found device.

        Called from the data co-ordinator when it finds a new device that needs
        to have sensors created. Not called directly, but via the dispatch
        facility from HA.
        Make sure you have a full list of scanners ready before calling this.
        """
        if address not in created_devices:
            entities = []
            entities.append(BermudaNumber(coordinator, entry, address))
            # We set update before add to False because we are being
            # call(back(ed)) from the update, so causing it to call another would be... bad.
            async_add_devices(entities, False)
            created_devices.append(address)
        else:
            # _LOGGER.debug(
            #     "Ignoring create request for existing dev_tracker %s", address
            # )
            pass
        # Scanners become "ready" (wifi mac known) over several update cycles and
        # emit no reliable per-readiness signal, so retry per-scanner creation on
        # each device discovery too (mirrors sensor.py).
        create_scanner_entities()
        # tell the co-ord we've done it.
        coordinator.number_created(address)

    created_scanners: set[str] = set()  # scanner addresses we've made a max-radius control for

    @callback
    def create_scanner_entities() -> None:
        """Create one 'max detection radius' control per scanner.

        Scanners are not tracked devices and never flow through device_new, so
        we hook the scanner roster instead (mirroring sensor.py). Wait until each
        remote scanner has its wifi mac so the control attaches to the right
        device rather than a placeholder.
        """
        entities = []
        for scanner in coordinator.get_scanners:
            if scanner.address in created_scanners:
                continue
            # Wait until a remote scanner has its wifi mac so the control attaches
            # to the correct device. Skip (not bail) so other ready scanners are
            # still created now; this is retried as scanners become ready.
            if scanner.is_remote_scanner and scanner.address_wifi_mac is None:
                continue
            entities.append(BermudaScannerMaxRadius(coordinator, entry, scanner.address))
            created_scanners.add(scanner.address)
        if entities:
            async_add_devices(entities, False)

    @callback
    def scanners_changed() -> None:
        """The roster of scanners changed — ensure each has its control."""
        create_scanner_entities()

    # Connect device_new (tracked devices) and scanners_changed (scanners) signals.
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DEVICE_NEW, device_new))
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_SCANNERS_CHANGED, scanners_changed))
    # Scanners may already be known by the time this platform loads.
    create_scanner_entities()

    # Now we must tell the co-ord to do initial refresh, so that it will call our callback.
    # await coordinator.async_config_entry_first_refresh()


class BermudaNumber(BermudaEntity, RestoreNumber):
    """A Number entity for bermuda devices."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Calibration Ref Power at 1m. 0 for default."
    _attr_translation_key = "ref_power"
    _attr_device_class = NumberDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.CONFIG
    # _attr_entity_registry_enabled_default = False
    _attr_native_min_value = -127
    _attr_native_max_value = 0
    _attr_native_step = 1
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialise the number entity."""
        self.restored_data: NumberExtraStoredData | None = None
        super().__init__(coordinator, entry, address)

    async def async_added_to_hass(self) -> None:
        """Restore values from HA storage on startup."""
        await super().async_added_to_hass()
        self.restored_data = await self.async_get_last_number_data()
        if self.restored_data is not None and self.restored_data.native_value is not None:
            self.coordinator.devices[self.address].set_ref_power(self.restored_data.native_value)

    @property
    def native_value(self) -> float | None:
        """Return value of number."""
        # if self.restored_data is not None and self.restored_data.native_value is not None:
        #     return self.restored_data.native_value
        return self.coordinator.devices[self.address].ref_power
        return 0

    async def async_set_native_value(self, value: float) -> None:
        """Set value."""
        self.coordinator.devices[self.address].set_ref_power(value)
        self.async_write_ha_state()
        # Beware that STATE_DUMP_INTERVAL for restore_state's dump_state
        # is 15 minutes, so if HA is killed instead of exiting cleanly,
        # updated values may not be restored. Tempting to schedule a dump
        # here, since updates to calib will be infrequent, but users are
        # moderately likely to restart HA after playing with them.

    @property
    def unique_id(self):
        """
        "Uniquely identify this sensor so that it gets stored in the entity_registry,
        and can be maintained / renamed etc by the user.
        """
        return f"{self._device.unique_id}_ref_power"


class BermudaScannerMaxRadius(BermudaEntity, RestoreNumber):
    """Per-scanner maximum detection radius, in metres. 0 = use the global default.

    The per-scanner analogue of ESPresense's per-node ``max_distance``: while a
    device is farther than this many metres from the scanner, the scanner cannot
    win that device's Area. This lets an over-reading proxy (e.g. an ESP reused
    as both a sensor and a BLE scanner) be reined in without touching the global
    max radius. Only created for scanner devices.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Max detection radius (m). 0 for default."
    _attr_translation_key = "max_radius"
    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_entity_category = EntityCategory.CONFIG
    # Disabled by default: only needed to rein in an over-reading scanner, so it
    # stays out of the way (no screen real-estate, no state-machine slot) until a
    # user enables it per scanner. 0 = use the global default when left disabled.
    _attr_entity_registry_enabled_default = False
    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: BermudaDataUpdateCoordinator,
        entry: BermudaConfigEntry,
        address: str,
    ) -> None:
        """Initialise the number entity."""
        self.restored_data: NumberExtraStoredData | None = None
        super().__init__(coordinator, entry, address)

    async def async_added_to_hass(self) -> None:
        """Restore the saved radius from HA storage on startup."""
        await super().async_added_to_hass()
        self.restored_data = await self.async_get_last_number_data()
        if self.restored_data is not None and self.restored_data.native_value is not None:
            self.coordinator.devices[self.address].set_max_radius(self.restored_data.native_value)

    @property
    def native_value(self) -> float | None:
        """Return the configured per-scanner max radius (metres)."""
        return self.coordinator.devices[self.address].max_radius

    async def async_set_native_value(self, value: float) -> None:
        """Set a new per-scanner max radius."""
        self.coordinator.devices[self.address].set_max_radius(value)
        self.async_write_ha_state()

    @property
    def unique_id(self):
        """Uniquely identify this control in the entity registry."""
        return f"{self._device.unique_id}_max_radius"

    # @property
    # def extra_state_attributes(self) -> Mapping[str, Any]:
    #     """Return extra state attributes for this device."""
    #     return {"scanner": self._device.area_scanner, "area": self._device.area_name}

    # @property
    # def state(self) -> str:
    #     """Return the state of the device."""
    #     return self._device.zone

    # @property
    # def source_type(self) -> SourceType:
    #     """Return the source type, eg gps or router, of the device."""
    #     return SourceType.BLUETOOTH_LE

    # @property
    # def icon(self) -> str:
    #     """Return device icon."""
    #     return "mdi:bluetooth-connect" if self._device.zone == STATE_HOME else "mdi:bluetooth-off"
