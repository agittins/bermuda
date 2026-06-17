"""Tests for the per-device mobility-mode select entity."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.bermuda.const import MOBILITY_MOVING, MOBILITY_OPTIONS, MOBILITY_STATIONARY
from custom_components.bermuda.select import BermudaMobilityTypeSelect


def _make_select(initial=MOBILITY_MOVING):
    """A select entity wired to a tiny device stub (no hass needed)."""
    ent = object.__new__(BermudaMobilityTypeSelect)
    state = {"mode": initial}

    def _set(value):
        state["mode"] = value if value in MOBILITY_OPTIONS else MOBILITY_MOVING

    ent._device = SimpleNamespace(
        unique_id="aa:bb:cc:dd:ee:ff",
        get_mobility_type=lambda: state["mode"],
        set_mobility_type=_set,
    )
    ent.async_write_ha_state = lambda: None
    return ent, state


def test_select_exposes_options_and_unique_id():
    ent, _state = _make_select()
    assert ent.options == MOBILITY_OPTIONS
    assert ent.unique_id == "aa:bb:cc:dd:ee:ff_mobility"
    assert ent.translation_key == "mobility_type"


def test_select_current_option_reflects_device():
    ent, _state = _make_select(initial=MOBILITY_STATIONARY)
    assert ent.current_option == MOBILITY_STATIONARY


async def test_select_async_select_option_sets_device():
    ent, state = _make_select(initial=MOBILITY_MOVING)
    await ent.async_select_option(MOBILITY_STATIONARY)
    assert state["mode"] == MOBILITY_STATIONARY
    assert ent.current_option == MOBILITY_STATIONARY
