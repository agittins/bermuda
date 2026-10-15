"""Tests for Bermuda's local Find My advertisement matching."""

from datetime import UTC, datetime

from custom_components.bermuda.bermuda_findmy import (
    CandidateKey,
    extract_offline_finding_payload,
    match_dult_advertisement,
)
from findmy import NearbyOfflineFindingDevice, OfflineFindingDevice


def test_extract_offline_finding_payload():
    """Extract a complete Offline Finding section from Apple manufacturer data."""
    payload_length = NearbyOfflineFindingDevice.OF_PAYLOAD_LEN
    payload = bytes([OfflineFindingDevice.OF_TYPE, payload_length]) + bytes(payload_length)

    assert extract_offline_finding_payload(b"\x01\x02" + payload) == payload
    assert extract_offline_finding_payload(payload[:-1]) is None


def test_match_dult_advertisement():
    """Match a DULT key prefix and report its near-owner state."""
    detected_at = datetime.now(UTC)
    candidate = CandidateKey(index=12, adv_key=bytes.fromhex("123456789abc") + bytes(26), can_align=True)
    lookup = {bytes.fromhex("123456789abc"): (candidate,)}

    observation = match_dult_advertisement(
        "52:34:56:78:9A:BC",
        b"\x01\x01",
        detected_at,
        -61,
        lookup,
    )

    assert observation is not None
    assert observation.mac_address == "52:34:56:78:9A:BC"
    assert observation.detected_at == detected_at
    assert observation.rssi == -61
    assert observation.state == "near_owner"
    assert observation.key_index == 12


def test_match_dult_advertisement_rejects_unmatched_prefix():
    """Do not claim a DULT advertisement when its key prefix is unknown."""
    assert (
        match_dult_advertisement(
            "52:34:56:78:9A:BC",
            b"\x01\x01",
            datetime.now(UTC),
            -61,
            {},
        )
        is None
    )