"""
Public, stable read API for other integrations.

Bermuda already holds every device/scanner advertisement in memory, whether or
not the corresponding `distance_to` entities are enabled. Consumers that want
those per-scanner readings have historically had to enable the entities and
scrape `sensor.<device>_distance_to_<scanner>` out of the state machine, which
is expensive at scale: on a busy install that is thousands of entities, each
one writing to the recorder and fanning `state_changed` out to every websocket
client.

This module exposes the same data directly, in-process, without creating a
single entity:

    from custom_components.bermuda.api import async_get_advert_snapshot

    snapshot = async_get_advert_snapshot(hass)

For push updates, Bermuda's coordinator is a normal `DataUpdateCoordinator`, so
callers can subscribe with `async_get_coordinator(hass).async_add_listener(cb)`
and take a fresh snapshot when it fires. No extra signal is needed.

The snapshot is deliberately plain JSON-able primitives rather than Bermuda's
internal objects, so consumers do not bind to internal class layout (which does
change). `SNAPSHOT_VERSION` is bumped if the shape changes incompatibly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.core import callback
from homeassistant.util import slugify

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import BermudaDataUpdateCoordinator

# Bumped only on an incompatible change to the snapshot shape. Consumers should
# check this and degrade gracefully rather than assume.
SNAPSHOT_VERSION = 1


@callback
def async_get_coordinator(hass: HomeAssistant) -> BermudaDataUpdateCoordinator | None:
    """
    Return Bermuda's coordinator, or None if Bermuda is not set up.

    Callers can use the returned coordinator's `async_add_listener()` to be
    notified after each update cycle, then call `async_get_advert_snapshot()`.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime_data, "coordinator", None)
        if coordinator is not None:
            return coordinator
    return None


@callback
def async_get_advert_snapshot(
    hass: HomeAssistant,
    addresses: set[str] | None = None,
    *,
    include_empty: bool = False,
) -> dict[str, Any] | None:
    """
    Return a point-in-time snapshot of every device/scanner advertisement.

    `addresses` optionally limits the result to those device addresses
    (lower-cased MAC, or an iBeacon/IRK metadevice address). `include_empty`
    keeps devices that currently have no adverts at all.

    Returns None if Bermuda is not set up.

    Shape::

        {
          "version": 1,
          "stamp": <monotonic seconds>,
          "devices": {
            "<device address>": {
              "name": str,
              "slug": str,          # matches Bermuda's entity naming
              "address_type": str,
              "area_id": str | None,
              "area_name": str | None,
              "scanners": {
                "<scanner address>": {
                  "name": str,
                  "slug": str,      # best-effort; see caveat below
                  "address": str,   # stable join key
                  "area_id": str | None,
                  "area_name": str | None,
                  "distance": float | None,   # smoothed, METRES
                  "distance_raw": float | None,
                  "rssi": float | None,
                  "stamp": float,   # monotonic, when this advert was received
                  "age": float,     # seconds since that advert
                },
                ...
              },
            },
            ...
          },
        }

    Notes for consumers:

    - `distance` is metres, always. Bermuda's *entities* may render feet
      depending on the user's unit settings; this API does not.
    - `distance` is Bermuda's smoothed value and is None once the reading has
      timed out, which is the "scanner can no longer hear this device" signal.
    - `age` is seconds since this scanner last actually heard the device. That
      is strictly better than an entity's `last_updated` for spotting a stuck
      reading, because it does not depend on the value having changed.
    - Key scanners by their ADDRESS (the dict key / `address` field), not by
      `slug`. Bermuda's `_distance_to_<slug>` entity_ids are frozen at entity
      creation and do not follow later scanner renames, so a slug derived from
      the current name can differ from the historical entity_id. A consumer
      migrating off entity scraping should resolve its stored slugs to scanner
      addresses once via the entity registry (disabled entities are still
      registered, and Bermuda's unique_id embeds the scanner MAC).
    """
    coordinator = async_get_coordinator(hass)
    if coordinator is None:
        return None

    nowstamp = monotonic_time_coarse()
    wanted = {addr.lower() for addr in addresses} if addresses is not None else None

    devices: dict[str, Any] = {}
    for address, device in coordinator.devices.items():
        if wanted is not None and address.lower() not in wanted:
            continue
        # Defensive: this walks shared coordinator state that other code owns,
        # and a public API must not be the thing that raises.
        device_adverts = getattr(device, "adverts", None) or {}
        if not device_adverts and not include_empty:
            continue

        scanners: dict[str, Any] = {}
        for advert in device_adverts.values():
            scanner_device = coordinator.devices.get(advert.scanner_address) or advert.scanner_device
            # The scanner DEVICE's name is authoritative; advert.name is a copy
            # taken when the advert was created and goes stale if the scanner is
            # renamed. sensor.py makes the same distinction deliberately.
            scanner_name = getattr(scanner_device, "name", None) or advert.name
            scanners[advert.scanner_address] = {
                "name": scanner_name,
                # Best-effort only. This is NOT a reliable join key: Bermuda's
                # `_distance_to_<slug>` entity_ids are fixed when the entity is
                # first created and do not follow later renames (Bermuda appends
                # a MAC to disambiguate duplicate scanner names, which changes
                # the name but not the existing entity_id). Consumers migrating
                # from entity scraping should map their stored slug to a scanner
                # address ONCE via the entity registry - disabled entities are
                # still registered - and then key off the address below.
                "slug": slugify(scanner_name),
                "address": advert.scanner_address,
                # Bermuda builds its per-scanner entity unique_ids as
                # f"{device.unique_id}_{scanner.address_wifi_mac or scanner.address}_range",
                # so a consumer resolving stored entity slugs via the entity
                # registry needs these to join on. Exposing both avoids the
                # consumer having to know that wifi-mac fallback rule.
                "unique_id": getattr(scanner_device, "unique_id", None),
                "address_wifi_mac": getattr(scanner_device, "address_wifi_mac", None),
                "area_id": advert.area_id,
                "area_name": advert.area_name,
                "distance": advert.rssi_distance,
                "distance_raw": getattr(advert, "rssi_distance_raw", None),
                "rssi": advert.rssi,
                "stamp": advert.stamp,
                "age": nowstamp - advert.stamp if advert.stamp else None,
                "scanner_last_seen_age": (nowstamp - scanner_device.last_seen if scanner_device.last_seen else None),
            }

        devices[address] = {
            "name": device.name,
            "slug": slugify(device.name),
            "unique_id": getattr(device, "unique_id", None),
            "address_type": device.address_type,
            "area_id": device.area_id,
            "area_name": device.area_name,
            "scanners": scanners,
        }

    return {
        "version": SNAPSHOT_VERSION,
        "stamp": nowstamp,
        "devices": devices,
    }
