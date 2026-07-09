"""
Bluetooth SIG manufacturer-id loading and lookup for Bermuda.

The raw SIG tables don't always identify a *brand* clearly (and some prefixes
like Apple/iBeacon or BTHome are shared protocols rather than manufacturers),
so a small set of opinionated overrides is applied on top of the tables for
better user-facing labels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiofiles
import yaml

from .const import _LOGGER, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# OEMs who license their OUIs / UUIDs to third parties — treat as "generic" so
# they are only used as a fallback identity.
_GENERIC_OEM_MARKERS = ("Google", "Realtek")

# Opinionated overrides (uuid -> (name, is_generic)) for shared protocols and
# well-known brands the raw SIG tables don't label clearly.
_MANUFACTURER_OVERRIDES: dict[int, tuple[str, bool]] = {
    0x0BA9: ("Shelly Devices", False),  # Allterco Robotics
    0x004C: ("Apple Inc.", True),  # only iBeacon / FindMy adverts get third-partied
    0x181C: ("BTHome v1 cleartext", True),
    0x181E: ("BTHome v1 encrypted", True),
    0xFCD2: ("BTHome V2", True),  # Sponsored by Allterco / Shelly
}


def lookup_manufacturer(
    uuid: int | str,
    member_uuids: dict[int, str],
    company_uuids: dict[int, str],
) -> tuple[str, bool] | tuple[None, None]:
    """
    Map a Bluetooth UUID to a ``(name, is_generic)`` pair.

    ``uuid`` is either an ``int`` or a hex string (``":"`` separators allowed).
    ``is_generic`` flags shared protocols / OEM identities that should only be
    used as a fallback. Returns ``(None, None)`` when nothing matches.
    """
    if isinstance(uuid, str):
        uuid = int(uuid.replace(":", ""), 16)

    if uuid in _MANUFACTURER_OVERRIDES:
        return _MANUFACTURER_OVERRIDES[uuid]
    if uuid in member_uuids:
        name = member_uuids[uuid]
        return (name, any(marker in name for marker in _GENERIC_OEM_MARKERS))
    if uuid in company_uuids:
        return (company_uuids[uuid], False)

    return (None, None)


async def load_manufacturer_ids(hass: HomeAssistant) -> tuple[dict[int, str], dict[int, str]]:
    """
    Load the SIG member/company UUID name tables from the bundled yaml files.

    These mappings improve labels only, so any failure is logged at debug and
    returns empty tables — Bermuda must still load without them.
    """
    member_uuids: dict[int, str] = {}
    company_uuids: dict[int, str] = {}
    try:
        # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/member_uuids.yaml
        # The company table is ~192KB / 11k entries, so parse it off the event loop.
        path = hass.config.path(f"custom_components/{DOMAIN}/manufacturer_identification/member_uuids.yaml")
        async with aiofiles.open(path) as f:
            member_content = await f.read()
        member_data = await hass.async_add_executor_job(yaml.safe_load, member_content)
        member_uuids = {member["uuid"]: member["name"] for member in member_data["uuids"]}

        # https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/company_identifiers/company_identifiers.yaml
        path = hass.config.path(f"custom_components/{DOMAIN}/manufacturer_identification/company_identifiers.yaml")
        async with aiofiles.open(path) as f:
            company_content = await f.read()
        company_data = await hass.async_add_executor_job(yaml.safe_load, company_content)
        company_uuids = {member["value"]: member["name"] for member in company_data["company_identifiers"]}
    except OSError, KeyError, TypeError, yaml.YAMLError:
        _LOGGER.debug("Unable to load Bluetooth manufacturer metadata", exc_info=True)

    return member_uuids, company_uuids
