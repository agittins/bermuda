"""Tests for Bermuda's local Find My advertisement matching."""

from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.bermuda_findmy import (
    CandidateKey,
    LocalObservation,
    build_candidate_key_lookup_from_json,
    extract_offline_finding_payload,
    match_local_advertisement,
    match_dult_advertisement,
    update_alignment_from_observation,
)
from findmy import FindMyAccessory, NearbyOfflineFindingDevice, OfflineFindingDevice


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


def test_match_nearby_offline_finding_advertisement():
    """Match the short payload AirTags use when they are nearby their owner."""
    detected_at = datetime.now(UTC)
    candidate = CandidateKey(
        index=42,
        adv_key=bytes.fromhex("123456789abc") + bytes(22),
        can_align=True,
    )
    lookup = {bytes.fromhex("123456789abc"): (candidate,)}
    payload = bytes([OfflineFindingDevice.OF_TYPE, NearbyOfflineFindingDevice.OF_PAYLOAD_LEN, 0, 0])

    observation = match_local_advertisement(
        "D2:34:56:78:9A:BC",
        payload,
        detected_at,
        -58,
        lookup,
    )

    assert observation is not None
    assert observation.state == "nearby"
    assert observation.key_index == 42
    assert observation.can_align is True


def test_update_alignment_from_primary_observation():
    """Persist the verified primary-key index and ignore non-aligning observations."""
    paired_at = datetime(2025, 1, 1, tzinfo=UTC)
    detected_at = datetime(2025, 1, 2, tzinfo=UTC)
    accessory = FindMyAccessory(
        master_key=bytes([1]) * 28,
        skn=bytes([2]) * 32,
        sks=bytes([3]) * 32,
        paired_at=paired_at,
        identifier="test-accessory",
    )
    observation = LocalObservation(
        mac_address="D2:34:56:78:9A:BC",
        detected_at=detected_at,
        rssi=-58,
        state="nearby",
        battery_level=None,
        status=0,
        key_index=96,
        can_align=True,
    )

    assert update_alignment_from_observation(accessory, observation) is True
    saved = accessory.to_json()
    assert saved["alignment_index"] == 96
    assert datetime.fromisoformat(saved["alignment_date"]) == detected_at
    candidates = build_candidate_key_lookup_from_json(saved, detected_at)
    assert any(candidate.index == 96 and candidate.can_align for keys in candidates.values() for candidate in keys)

    address = "findmy_test-accessory"
    storage_data = BermudaDataUpdateCoordinator._findmy_alignment_store_data(
        SimpleNamespace(
            _findmy_accessories={address: accessory},
            _findmy_accessory_ids={address: "test-accessory"},
        )
    )
    assert storage_data == {
        "test-accessory": {
            "alignment_date": detected_at.isoformat(),
            "alignment_index": 96,
        }
    }

    non_primary = LocalObservation(
        mac_address=observation.mac_address,
        detected_at=datetime(2025, 1, 3, tzinfo=UTC),
        rssi=-58,
        state="separated",
        battery_level=None,
        status=0,
        key_index=97,
        can_align=False,
    )
    assert update_alignment_from_observation(accessory, non_primary) is False
    assert accessory.to_json() == saved