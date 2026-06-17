"""
Area detection based on Home Assistant entity states.

Provides area presence indicators by monitoring configured entity IDs. When an
entity is "on" (triggered), its area becomes a candidate in Bermuda's area
detection, competing with BLE distance-based detection at a configurable
"virtual distance".

Ported from knoop7/bermuda-intent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import STATE_ON
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import _LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

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
