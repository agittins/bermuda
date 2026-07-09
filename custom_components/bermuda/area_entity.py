"""
Area detection based on Home Assistant entity states.

Provides area presence indicators by monitoring configured entity IDs. When an
entity is "on" (triggered), its area becomes a candidate in Bermuda's area
detection, competing with BLE distance-based detection at a configurable
"virtual distance".

Ported from knoop7/bermuda-intent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_ON
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    _LOGGER,
    CONF_AREA_ENTITIES,
    CONF_AREA_ENTITY_DISTANCE,
    CONF_AREA_ENTITY_DISTANCES,
    DEFAULT_AREA_ENTITY_DISTANCE,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from homeassistant.core import HomeAssistant

    from .bermuda_device import BermudaDevice

STATES_TRIGGERED = {STATE_ON, "true"}


class BermudaAreaEntityManager:
    """Manages entity-based area presence indicators."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._er = er.async_get(hass)
        self._dr = dr.async_get(hass)
        self._ar = ar.async_get(hass)

    def resolve_entity_area(self, entity_id: str) -> tuple[str | None, str | None]:
        """
        Resolve the area_id and area_name for a given entity_id.

        Checks the entity's own area first, then falls back to its device's area.
        Returns (area_id, area_name) or (None, None).
        """
        entry = self._er.async_get(entity_id)
        if entry is None:
            return None, None

        area_id = entry.area_id
        if area_id is None and entry.device_id is not None:
            device = self._dr.async_get(entry.device_id)
            if device is not None:
                area_id = device.area_id

        if area_id is None:
            return None, None

        area = self._ar.async_get_area(area_id)
        if area is None:
            return None, None

        return area_id, area.name

    def get_triggered_areas_with_distances(
        self,
        configured_entities: list[str],
        per_entity_distances: dict[str, float],
        default_distance: float,
    ) -> dict[str, tuple[str, float]]:
        """
        Return ``area_id -> (area_name, best_distance)`` for all triggered entities.

        An entity is "triggered" when its state is ``on``/``true``. For each
        triggered area the distance is the MINIMUM across its triggered entities
        (per-entity override or the global default), so multiple presence sensors
        in one room compete: the one with the smallest configured distance wins.
        """
        triggered: dict[str, tuple[str, float]] = {}
        for entity_id in configured_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                _LOGGER.debug("Area entity %s has no state (not loaded?)", entity_id)
                continue
            if state.state.lower() not in STATES_TRIGGERED:
                continue
            area_id, area_name = self.resolve_entity_area(entity_id)
            if area_id is None or area_name is None:
                _LOGGER.debug("Area entity %s is triggered but has no resolvable area", entity_id)
                continue
            entity_distance = per_entity_distances.get(entity_id, default_distance)
            existing = triggered.get(area_id)
            if existing is None or entity_distance < existing[1]:
                triggered[area_id] = (area_name, entity_distance)
        return triggered


def apply_area_entity_overrides(
    manager: BermudaAreaEntityManager,
    devices: Iterable[BermudaDevice],
    options: Mapping[str, Any],
) -> None:
    """
    Override a device's area when a triggered presence entity wins on virtual distance.

    Runs after BLE area selection: each configured HA entity that is "on" makes its
    area a candidate at a (small) virtual distance; if that beats the device's
    BLE-derived area_distance (or the device has no / Unknown area), the device is
    moved into the entity's area. Lets motion/contact sensors reinforce or override
    BLE presence. Ported from knoop7/bermuda-intent.
    """
    configured = options.get(CONF_AREA_ENTITIES, [])
    if not configured:
        return
    default_dist = options.get(CONF_AREA_ENTITY_DISTANCE, DEFAULT_AREA_ENTITY_DISTANCE)
    per_entity_dists = options.get(CONF_AREA_ENTITY_DISTANCES, {})
    triggered_areas = manager.get_triggered_areas_with_distances(configured, per_entity_dists, default_dist)
    if not triggered_areas:
        return

    for device in devices:
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
