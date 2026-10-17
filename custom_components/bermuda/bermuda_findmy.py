"""Match local Find My advertisements against imported rolling keys."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from findmy import (
    FindMyAccessory,
    KeyPairType,
    NearbyOfflineFindingDevice,
    OfflineFindingDevice,
    SeparatedOfflineFindingDevice,
)

if TYPE_CHECKING:
    from findmy.accessory import FindMyAccessoryMapping

APPLE_COMPANY_ID = 0x004C
DULT_SERVICE_UUID = "0000fcb2-0000-1000-8000-00805f9b34fb"
DULT_NETWORK_ID_APPLE = 0x01
_DULT_MIN_PAYLOAD_LEN = 2
_MAC_ADDRESS_LEN = 6
_KEY_WINDOW_PAST = timedelta(hours=36)
_KEY_WINDOW_FUTURE = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class CandidateKey:
    """A generated rolling key and its alignment information."""

    index: int
    adv_key: bytes
    can_align: bool


type CandidateKeyLookup = Mapping[bytes, tuple[CandidateKey, ...]]


@dataclass(frozen=True, slots=True)
class LocalObservation:
    """A verified local observation of a rolling-key accessory."""

    mac_address: str
    detected_at: datetime
    rssi: int | None
    state: str
    battery_level: str | None
    status: int
    key_index: int
    can_align: bool


def update_alignment_from_observation(
    accessory: FindMyAccessory,
    observation: LocalObservation,
) -> bool:
    """Update a rolling accessory's alignment from a verified primary key."""
    if not observation.can_align:
        return False

    previous = accessory.to_json()
    accessory.update_alignment(observation.detected_at, observation.key_index)
    updated = accessory.to_json()
    return (
        previous["alignment_date"] != updated["alignment_date"]
        or previous["alignment_index"] != updated["alignment_index"]
    )


def _lookup_prefix(key_prefix: bytes) -> bytes:
    """Mask the top two key bits, which a MAC address cannot carry."""
    return bytes([key_prefix[0] & 0x3F]) + key_prefix[1:6]


def build_candidate_key_lookup(
    accessory: FindMyAccessory,
    observed_at: datetime,
) -> dict[bytes, tuple[CandidateKey, ...]]:
    """Build a lookup of rolling keys near the current time."""
    candidates: defaultdict[bytes, list[CandidateKey]] = defaultdict(list)
    for index, key in accessory.keys_between(
        observed_at - _KEY_WINDOW_PAST,
        observed_at + _KEY_WINDOW_FUTURE,
    ):
        adv_key = key.adv_key_bytes
        candidates[_lookup_prefix(adv_key)].append(
            CandidateKey(
                index=index,
                adv_key=adv_key,
                can_align=key.key_type == KeyPairType.PRIMARY,
            ),
        )
    return {prefix: tuple(keys) for prefix, keys in candidates.items()}


def build_candidate_key_lookup_from_json(
    accessory_data: Mapping[str, object],
    observed_at: datetime,
) -> dict[bytes, tuple[CandidateKey, ...]]:
    """Build candidates from a snapshot without mutating the live key generator."""
    return build_candidate_key_lookup(
        FindMyAccessory.from_json(cast("FindMyAccessoryMapping", accessory_data)),
        observed_at,
    )


def extract_offline_finding_payload(apple_data: bytes) -> bytes | None:
    """Extract a complete nearby or separated Offline Finding section."""
    for offset in range(max(0, len(apple_data) - 1)):
        if apple_data[offset] != OfflineFindingDevice.OF_TYPE:
            continue

        payload_length = apple_data[offset + 1]
        if payload_length not in (
            NearbyOfflineFindingDevice.OF_PAYLOAD_LEN,
            SeparatedOfflineFindingDevice.OF_PAYLOAD_LEN,
        ):
            continue

        section_end = offset + OfflineFindingDevice.OF_HEADER_SIZE + payload_length
        if section_end <= len(apple_data):
            return apple_data[offset:section_end]
    return None


def match_local_advertisement(
    address: str,
    apple_data: bytes,
    detected_at: datetime,
    rssi: int | None,
    candidates: CandidateKeyLookup,
) -> LocalObservation | None:
    """Match an Offline Finding advertisement to an imported accessory key."""
    payload = extract_offline_finding_payload(apple_data)
    if payload is None:
        return None

    try:
        device = OfflineFindingDevice.from_ble_payload(address, payload, detected_at, rssi)
    except (IndexError, ValueError):
        return None

    if isinstance(device, NearbyOfflineFindingDevice):
        partial_key = device.partial_adv_key
        full_key = None
        state = "nearby"
    elif isinstance(device, SeparatedOfflineFindingDevice):
        partial_key = device.adv_key_bytes[:6]
        full_key = device.adv_key_bytes
        state = "separated"
    else:
        return None

    for candidate in candidates.get(_lookup_prefix(partial_key), ()):
        if full_key is not None and candidate.adv_key != full_key:
            continue
        if full_key is None and candidate.adv_key[:6] != partial_key:
            continue
        return LocalObservation(
            mac_address=device.mac_address,
            detected_at=device.detected_at,
            rssi=device.rssi,
            state=state,
            battery_level=device.battery_level,
            status=device.status,
            key_index=candidate.index,
            can_align=candidate.can_align,
        )
    return None


def match_dult_advertisement(
    address: str,
    service_data: bytes,
    detected_at: datetime,
    rssi: int | None,
    candidates: CandidateKeyLookup,
) -> LocalObservation | None:
    """Match an Apple DULT advertisement using its rotating MAC address."""
    if len(service_data) < _DULT_MIN_PAYLOAD_LEN or service_data[0] != DULT_NETWORK_ID_APPLE:
        return None

    try:
        mac = bytes.fromhex(address.replace(":", ""))
    except ValueError:
        return None
    if len(mac) != _MAC_ADDRESS_LEN:
        return None

    for candidate in candidates.get(_lookup_prefix(mac), ()):
        return LocalObservation(
            mac_address=address.upper(),
            detected_at=detected_at,
            rssi=rssi,
            state="near_owner" if service_data[1] & 0x01 else "separated_dult",
            battery_level=None,
            status=service_data[1],
            key_index=candidate.index,
            can_align=candidate.can_align,
        )
    return None
