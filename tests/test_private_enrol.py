"""Tests for IRK enrolment: parsing + driving the private_ble_device config flow."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.bermuda.private_enrol import async_enrol_private_device, parse_irk

VALID_HEX = "00112233445566778899aabbccddeeff"  # 16 bytes


# --------------------------------------------------------------------------- #
# parse_irk
# --------------------------------------------------------------------------- #


def test_parse_irk_hex():
    assert parse_irk(VALID_HEX) == bytes.fromhex(VALID_HEX)


def test_parse_irk_strips_prefix_and_whitespace():
    assert parse_irk(f"  irk:{VALID_HEX}  ") == bytes.fromhex(VALID_HEX)


def test_parse_irk_base64_is_byte_reversed():
    raw = bytes(range(16))
    b64 = base64.b64encode(bytes(reversed(raw))).decode()  # iOS "Remote IRK" form
    assert b64.endswith("=")
    assert parse_irk(b64) == raw


def test_parse_irk_rejects_bad_input():
    assert parse_irk("not-hex-at-all") is None
    assert parse_irk("00112233") is None  # too short
    assert parse_irk("") is None
    assert parse_irk(VALID_HEX + "ff") is None  # too long


# --------------------------------------------------------------------------- #
# async_enrol_private_device
# --------------------------------------------------------------------------- #


async def test_enrol_invalid_irk_never_touches_the_flow(hass: HomeAssistant):
    hass.config_entries.flow.async_init = AsyncMock()
    assert await async_enrol_private_device(hass, "bogus") == "irk_not_valid"
    hass.config_entries.flow.async_init.assert_not_called()


async def test_enrol_success_creates_and_renames_entry(hass: HomeAssistant):
    fake_entry = MagicMock()
    hass.config_entries.flow.async_init = AsyncMock(return_value={"type": FlowResultType.FORM, "flow_id": "abc"})
    hass.config_entries.flow.async_configure = AsyncMock(
        return_value={"type": FlowResultType.CREATE_ENTRY, "result": fake_entry}
    )
    hass.config_entries.async_update_entry = MagicMock()

    assert await async_enrol_private_device(hass, VALID_HEX, "Jan's phone") == ""
    hass.config_entries.flow.async_configure.assert_awaited_once_with("abc", {"irk": VALID_HEX})
    hass.config_entries.async_update_entry.assert_called_once_with(fake_entry, title="Jan's phone")


async def test_enrol_success_without_name_keeps_default_title(hass: HomeAssistant):
    hass.config_entries.flow.async_init = AsyncMock(return_value={"type": FlowResultType.FORM, "flow_id": "abc"})
    hass.config_entries.flow.async_configure = AsyncMock(
        return_value={"type": FlowResultType.CREATE_ENTRY, "result": MagicMock()}
    )
    hass.config_entries.async_update_entry = MagicMock()

    assert await async_enrol_private_device(hass, VALID_HEX) == ""
    hass.config_entries.async_update_entry.assert_not_called()


async def test_enrol_surfaces_irk_not_found(hass: HomeAssistant):
    hass.config_entries.flow.async_init = AsyncMock(return_value={"type": FlowResultType.FORM, "flow_id": "abc"})
    hass.config_entries.flow.async_configure = AsyncMock(
        return_value={"type": FlowResultType.FORM, "errors": {"irk": "irk_not_found"}}
    )
    assert await async_enrol_private_device(hass, VALID_HEX) == "irk_not_found"


async def test_enrol_surfaces_bluetooth_not_available_abort(hass: HomeAssistant):
    hass.config_entries.flow.async_init = AsyncMock(
        return_value={"type": FlowResultType.ABORT, "reason": "bluetooth_not_available"}
    )
    assert await async_enrol_private_device(hass, VALID_HEX) == "bluetooth_not_available"
