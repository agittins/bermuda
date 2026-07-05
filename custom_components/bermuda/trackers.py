"""
Recognise consumer BLE item-trackers by their advertisement signature.

Trackers (AirTag, Tile, Samsung SmartTag, Chipolo, Pebblebee, ...) each leave a
recognisable fingerprint in their BLE advertisement: a brand-specific service
UUID, a company id, or membership of a finder network (Apple Find My, Google
Find My Device Network). This module maps those fingerprints to a human label.

Signatures are grounded in the ESPresense fingerprinting source
(``src/BleFingerprint.cpp`` / ``util.h``), the Theengs decoder
(``src/devices/tracker_json.h``), the Bluetooth SIG assigned-numbers database
and the official Apple Find My / Google Fast Pair FMDN specifications. Two
finder networks cover most modern trackers regardless of brand:

* **Apple Find My** — company id ``0x004C``, manufacturer-data type byte
  ``0x12``. Covers the AirTag and every "Works with Apple Find My" accessory
  (Chipolo Spot, eufy SmartTrack, Pebblebee Apple variant, ...). The payload
  carries only a rotating public key, so the brand is not recoverable from BLE.
* **Google Find My Device Network** (a.k.a. Find Hub) — Eddystone service UUID
  ``0xFEAA`` but with an FMDN frame type ``0x40``/``0x41`` instead of the
  classic Eddystone ``0x00``/``0x10``/``0x20``/``0x30``. Covers Chipolo Point,
  Pebblebee Google variant and other Fast-Pair-certified tags.

The remaining brands (Tile, Samsung SmartTag, TrackR, Nut, iTAG) use their own
(usually SIG-private) service UUIDs and are matched brand by brand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

# --- Apple Find My network ------------------------------------------------- #
APPLE_COMPANY_ID: Final = 0x004C
FINDMY_TYPE_BYTE: Final = 0x12
# ESPresense matches a total manufacturer-data length of 29 bytes; Home Assistant
# strips the 2 company-id bytes into the dict key, leaving a 27-byte payload
# ([0]=0x12 type, [1]=0x19 length(25), [2]=status byte, then 24 bytes of rotating
# key material). Frame prefix confirmed as ``1E FF 4C 00 12 19`` (Adam Catley /
# OpenHaystack reverse engineering).
FINDMY_PAYLOAD_LEN: Final = 27
# The status byte (payload offset 2) categorises the emitter via its bits 2-3,
# per the SEEMOO/AirGuard reverse engineering (arXiv 2202.11813): this is what
# separates a genuine tracker from a plain offline iPhone/Mac, which broadcasts
# the SAME 0x004C+0x12 frame. Documented but not guaranteed by Apple and
# firmware-spoofable, so it is a best-effort classification, not proof.
FINDMY_STATUS_OFFSET: Final = 2
FINDMY_STATUS_AIRTAG: Final = 0b01  # "Durian"
FINDMY_STATUS_ACCESSORY: Final = 0b10  # "Hawkeye" — third-party MFi (Chipolo Spot, eufy...)
FINDMY_STATUS_AIRPODS: Final = 0b11  # "HELE"
# 0b00 == a generic Apple device beaconing Find My (offline iPhone/Mac), NOT a tracker.

# --- Tile ------------------------------------------------------------------ #
# Tile advertises one of these service UUIDs and (per Theengs) no manufacturer
# data. 0xFEED is historical; 0xFEEC / 0xFD84 are newer generations. (0xFEEB is
# Swirl Networks, NOT Tile — deliberately excluded.)
TILE_SERVICE_UUIDS: Final = frozenset({"FEED", "FEEC", "FD84"})

# --- Samsung Galaxy SmartTag / SmartTag2 ----------------------------------- #
# 0xFD5A = registered tag, 0xFD59 = not-yet-registered tag. (0xFD69 is a *lost
# Samsung phone*, not a SmartTag — excluded.)
SMARTTAG_SERVICE_UUIDS: Final = frozenset({"FD5A", "FD59"})

# --- Google Find My Device Network (FMDN / Find Hub) ----------------------- #
EDDYSTONE_FMDN_UUID: Final = "FEAA"
FMDN_FRAME_TYPES: Final = frozenset({0x40, 0x41})

# --- TrackR (discontinued but still in the wild) --------------------------- #
TRACKR_SERVICE_UUID: Final = "0F3E"

# --- iTAG generic ODM tags ------------------------------------------------- #
# 0xFFE0 is a very generic vendor UUID (HM-10 modules), so the exact BLE name is
# required too (Theengs does the same).
ITAG_SERVICE_UUID: Final = "FFE0"

# --- Nut / Nutale ---------------------------------------------------------- #
# Nut reuses generic SIG services, so the BLE name disambiguates.
NUT_DIS_UUID: Final = "180A"  # Device Information Service, with name "nut"
NUTALE_SERVICE_UUID: Final = "0900"  # with name "nutale"

# Labels (kept here so callers/tests share one source of truth).
TRACKER_AIRTAG: Final = "Apple AirTag"
TRACKER_APPLE_ACCESSORY: Final = "Apple Find My"  # third-party MFi accessory (brand not in BLE)
TRACKER_AIRPODS: Final = "AirPods"
TRACKER_GOOGLE_FINDMY: Final = "Google Find My"
TRACKER_TILE: Final = "Tile"
TRACKER_SMARTTAG: Final = "Samsung SmartTag"
TRACKER_TRACKR: Final = "TrackR"
TRACKER_ITAG: Final = "iTAG"
TRACKER_NUT: Final = "Nut"


def short_uuid(uuid: str) -> str:
    """
    Return the upper-case 16-bit short form of a Bluetooth UUID string.

    ``0000fd5a-0000-1000-8000-00805f9b34fb`` -> ``FD5A``. A value already in short
    form is returned upper-cased unchanged.
    """
    compact = uuid.replace("-", "").upper()
    # 128-bit base-UUID form: the 16-bit value sits in bytes 4-8 (chars 4-8).
    if len(compact) >= 8:
        return compact[4:8]
    return compact


class _Advert(NamedTuple):
    """The advert fields the brand predicates read (name is lower-cased)."""

    manufacturer_data: dict[int, bytes]
    service_uuids: set[str]
    service_data: dict[str, bytes]
    name: str


class _Signature(NamedTuple):
    """A recognised-tracker rule: a label and a predicate over the advert."""

    label: str
    matches: Callable[[_Advert], bool]


def _fmdn_frame(adv: _Advert) -> bool:
    """True when the Eddystone service data carries a Google FMDN frame type."""
    frame = adv.service_data.get(EDDYSTONE_FMDN_UUID)
    return frame is not None and len(frame) > 0 and frame[0] in FMDN_FRAME_TYPES


# Brand rules, most-specific first.
_BRAND_SIGNATURES: Final[tuple[_Signature, ...]] = (
    # Tile: brand service UUID and (per Theengs) no manufacturer data.
    _Signature(TRACKER_TILE, lambda a: bool(a.service_uuids & TILE_SERVICE_UUIDS) and not a.manufacturer_data),
    # Samsung SmartTag: registered (FD5A) or not-yet-registered (FD59) service data.
    _Signature(TRACKER_SMARTTAG, lambda a: bool(a.service_data.keys() & SMARTTAG_SERVICE_UUIDS)),
    _Signature(TRACKER_TRACKR, lambda a: TRACKR_SERVICE_UUID in a.service_uuids),
    # iTAG's UUID is far too generic on its own, so require the exact name too.
    _Signature(TRACKER_ITAG, lambda a: ITAG_SERVICE_UUID in a.service_uuids and a.name == "itag"),
    _Signature(
        TRACKER_NUT,
        lambda a: (
            (NUT_DIS_UUID in a.service_uuids and "nut" in a.name)
            or (NUTALE_SERVICE_UUID in a.service_uuids and "nutale" in a.name)
        ),
    ),
    # Google Find My Device Network: Eddystone UUID carrying an FMDN frame type.
    _Signature(TRACKER_GOOGLE_FINDMY, _fmdn_frame),
)

# Apple Find My status-byte category (bits 2-3) -> label. 0b00 is deliberately
# absent: it is a plain offline Apple device (iPhone/Mac), not an item tracker.
_APPLE_FINDMY_LABELS: Final[dict[int, str]] = {
    FINDMY_STATUS_AIRTAG: TRACKER_AIRTAG,
    FINDMY_STATUS_ACCESSORY: TRACKER_APPLE_ACCESSORY,
    FINDMY_STATUS_AIRPODS: TRACKER_AIRPODS,
}


def _apple_findmy(manufacturer_data: dict[int, bytes]) -> str | None:
    """
    Classify an Apple Find My frame, or return None.

    The same 0x004C + type 0x12 frame is emitted by AirTags, third-party
    accessories, AirPods AND plain offline iPhones/Macs, so the status byte's
    bits 2-3 are what avoid labelling the user's own phone as a tracker.
    """
    apple = manufacturer_data.get(APPLE_COMPANY_ID)
    if not (apple and apple[:1] == bytes([FINDMY_TYPE_BYTE]) and len(apple) == FINDMY_PAYLOAD_LEN):
        return None
    category = (apple[FINDMY_STATUS_OFFSET] >> 2) & 0b11
    return _APPLE_FINDMY_LABELS.get(category)


def identify_tracker(
    manufacturer_data: dict[int, bytes],
    service_uuids: set[str],
    service_data: dict[str, bytes],
    name: str | None,
) -> str | None:
    """
    Return a human label for a recognised item-tracker, or ``None``.

    ``service_uuids`` and the keys of ``service_data`` are expected as 16-bit
    short upper-case forms (see :func:`short_uuid`). ``manufacturer_data`` maps a
    company id to its payload (the company-id bytes already stripped, as Home
    Assistant provides it).
    """
    advert = _Advert(manufacturer_data, service_uuids, service_data, (name or "").lower())
    for signature in _BRAND_SIGNATURES:
        if signature.matches(advert):
            return signature.label
    return _apple_findmy(manufacturer_data)
