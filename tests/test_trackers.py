"""Unit tests for BLE item-tracker signature recognition (trackers module).

The signatures are grounded in the ESPresense/Theengs source and the Apple Find
My / Google FMDN specs (see the research notes). These tests pin each match so a
future edit can't silently break recognition or start mislabelling a plain
offline iPhone as a tracker.
"""

from __future__ import annotations

from custom_components.bermuda.trackers import (
    TRACKER_AIRPODS,
    TRACKER_AIRTAG,
    TRACKER_APPLE_ACCESSORY,
    TRACKER_GOOGLE_FINDMY,
    TRACKER_NUT,
    TRACKER_SMARTTAG,
    TRACKER_TILE,
    TRACKER_TRACKR,
    identify_tracker,
    short_uuid,
)

APPLE = 0x004C


def _findmy_payload(status: int) -> bytes:
    """A 27-byte Apple Find My manufacturer-data payload with the given status byte."""
    return bytes([0x12, 0x19, status]) + bytes(24)


def test_short_uuid_from_128bit_and_short():
    assert short_uuid("0000fd5a-0000-1000-8000-00805f9b34fb") == "FD5A"
    assert short_uuid("feed") == "FEED"


def test_tile_matched_by_service_uuid_without_manufacturer_data():
    assert identify_tracker({}, {"FEED"}, {}, None) == TRACKER_TILE
    assert identify_tracker({}, {"FEEC"}, {}, None) == TRACKER_TILE
    # A device advertising manufacturer data alongside is not the Tile signature.
    assert identify_tracker({0x00E0: b"x"}, {"FEED"}, {}, None) != TRACKER_TILE


def test_samsung_smarttag_registered_and_unregistered():
    assert identify_tracker({}, set(), {"FD5A": b"\x00" * 20}, None) == TRACKER_SMARTTAG
    assert identify_tracker({}, set(), {"FD59": b"\x00" * 14}, None) == TRACKER_SMARTTAG
    # FD69 is a *lost Samsung phone*, not a SmartTag.
    assert identify_tracker({}, set(), {"FD69": b"\x00" * 20}, None) is None


def test_google_fmdn_distinguished_from_eddystone_by_frame_type():
    assert identify_tracker({}, set(), {"FEAA": bytes([0x40]) + bytes(20)}, None) == TRACKER_GOOGLE_FINDMY
    assert identify_tracker({}, set(), {"FEAA": bytes([0x41]) + bytes(20)}, None) == TRACKER_GOOGLE_FINDMY
    # Classic Eddystone frame types (0x00/0x10/0x20/0x30) are not trackers.
    assert identify_tracker({}, set(), {"FEAA": bytes([0x00]) + bytes(18)}, None) is None


def test_trackr_and_itag_and_nut():
    assert identify_tracker({}, {"0F3E"}, {}, None) == TRACKER_TRACKR
    # iTAG's UUID is too generic on its own; the name is required.
    assert identify_tracker({}, {"FFE0"}, {}, "iTAG") is not None
    assert identify_tracker({}, {"FFE0"}, {}, "some-sensor") is None
    assert identify_tracker({}, {"0900"}, {}, "Nutale") == TRACKER_NUT


def test_apple_airtag_vs_accessory_vs_airpods_vs_iphone():
    # bits 2-3 of the status byte categorise the emitter.
    assert identify_tracker({APPLE: _findmy_payload(0b0100)}, set(), {}, None) == TRACKER_AIRTAG  # 01
    assert identify_tracker({APPLE: _findmy_payload(0b1000)}, set(), {}, None) == TRACKER_APPLE_ACCESSORY  # 10
    assert identify_tracker({APPLE: _findmy_payload(0b1100)}, set(), {}, None) == TRACKER_AIRPODS  # 11
    # 00 == a plain offline iPhone/Mac: must NOT be labelled a tracker.
    assert identify_tracker({APPLE: _findmy_payload(0b0000)}, set(), {}, None) is None


def test_apple_non_findmy_frame_ignored():
    # iBeacon (type 0x02) and wrong length are not Find My.
    assert identify_tracker({APPLE: bytes([0x02, 0x15]) + bytes(23)}, set(), {}, None) is None
    assert identify_tracker({APPLE: bytes([0x12, 0x19, 0x04])}, set(), {}, None) is None  # too short


def test_unknown_device_returns_none():
    assert identify_tracker({0x00E0: b"\x01\x02"}, {"180F"}, {"180F": b"\x64"}, "Some Sensor") is None
