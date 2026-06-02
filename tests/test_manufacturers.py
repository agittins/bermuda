"""Unit tests for the opinionated Bluetooth manufacturer-id mapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.manufacturers import load_manufacturer_ids


@pytest.fixture
def coordinator():
    """Coordinator stub carrying just the UUID lookup tables."""
    coord = object.__new__(BermudaDataUpdateCoordinator)
    coord.member_uuids = {0x1234: "Acme Robotics", 0xABCD: "Google LLC"}
    coord.company_uuids = {0x5678: "CompanyX", 0x004C: "Apple should not reach here"}
    return coord


@pytest.mark.parametrize(
    ("uuid", "expected"),
    [
        (0x0BA9, ("Shelly Devices", False)),  # Allterco → Shelly override
        (0x004C, ("Apple Inc.", True)),  # Apple is generic (iBeacon/FindMy)
        (0x181C, ("BTHome v1 cleartext", True)),
        (0x181E, ("BTHome v1 encrypted", True)),
        (0xFCD2, ("BTHome V2", True)),
        (0x1234, ("Acme Robotics", False)),  # member_uuids, non-generic
        (0xABCD, ("Google LLC", True)),  # member_uuids, generic OEM
        (0x5678, ("CompanyX", False)),  # company_uuids
        (0x9999, (None, None)),  # unknown
    ],
)
def test_get_manufacturer_from_id(coordinator, uuid, expected):
    """Each override and lookup branch maps to the documented result."""
    assert coordinator.get_manufacturer_from_id(uuid) == expected


def test_get_manufacturer_accepts_hex_string(coordinator):
    """A four-hex-char string is parsed identically to the int form."""
    assert coordinator.get_manufacturer_from_id("004C") == ("Apple Inc.", True)
    assert coordinator.get_manufacturer_from_id("1234") == ("Acme Robotics", False)


def test_apple_override_takes_priority_over_tables(coordinator):
    """The 0x004C override wins even when present in company_uuids."""
    assert coordinator.get_manufacturer_from_id(0x004C) == ("Apple Inc.", True)


async def test_load_manufacturer_ids_parses_yaml(tmp_path):
    """The loader parses the SIG member/company yaml into int-keyed dicts."""
    member_file = tmp_path / "member_uuids.yaml"
    member_file.write_text("uuids:\n  - uuid: 76\n    name: Apple\n")
    company_file = tmp_path / "company_identifiers.yaml"
    company_file.write_text("company_identifiers:\n  - value: 6\n    name: Microsoft\n")

    hass = MagicMock()
    hass.config.path.side_effect = lambda rel: str(member_file if "member" in rel else company_file)
    # yaml parsing is offloaded to the executor; run it inline for the test.
    hass.async_add_executor_job = AsyncMock(side_effect=lambda func, *args: func(*args))

    member_uuids, company_uuids = await load_manufacturer_ids(hass)

    assert member_uuids == {76: "Apple"}
    assert company_uuids == {6: "Microsoft"}


async def test_load_manufacturer_ids_missing_files_returns_empty():
    """Missing/optional files degrade gracefully to empty tables."""
    hass = MagicMock()
    hass.config.path.return_value = "/nonexistent/bermuda/x.yaml"

    member_uuids, company_uuids = await load_manufacturer_ids(hass)

    assert member_uuids == {}
    assert company_uuids == {}
