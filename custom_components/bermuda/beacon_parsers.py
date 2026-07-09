"""
Pure parsers for BLE advertisement payloads.

Extracted from ``BermudaDevice.process_manufacturer_data`` so the binary
decoding of iBeacon frames and InPlay IN100 / DFRobot Fermion telemetry can be
unit-tested in isolation. These functions are pure: bytes in, parsed values
out, no side effects on the device object.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import IN100_PAYLOAD_LEN

# An iBeacon frame is 23 bytes, but at least one beacon in the wild omits the
# trailing tx_power byte and still carries a usable uuid/major/minor (see #466).
IBEACON_MIN_LEN = 22
IBEACON_FULL_LEN = 23

# IN100 telemetry scaling factors (payload byte layout is fixed by the vendor).
_IN100_VCC_DIVISOR = 32.0
_IN100_TEMP_DIVISOR = 100.0
_IN100_ADC_DIVISOR = 1000.0


@dataclass(frozen=True, slots=True)
class IBeaconParse:
    """Decoded fields of an Apple iBeacon manufacturer-data frame."""

    uuid: str
    major: str
    minor: str
    power: float | None

    @property
    def unique_id(self) -> str:
        """The uuid_major_minor identity Bermuda uses for iBeacon metadevices."""
        return f"{self.uuid}_{self.major}_{self.minor}"


@dataclass(frozen=True, slots=True)
class In100Telemetry:
    """Decoded InPlay IN100 / DFRobot Fermion telemetry (manufacturer data 0x0505)."""

    raw_payload_hex: str
    payload_len: int
    vcc: float | None
    temp_c: float | None
    adc_voltage: float | None


def parse_ibeacon(man_data: bytes) -> IBeaconParse | None:
    """
    Parse an Apple manufacturer-data payload as an iBeacon frame.

    Returns None when the payload is not an iBeacon advert (wrong type byte) or
    is too short to carry a uuid/major/minor identity. The tx_power field is
    optional (None when the truncated 22-byte variant is received).
    """
    if man_data[:1] != b"\x02" or len(man_data) < IBEACON_MIN_LEN:
        # 0x02 is the iBeacon type; the following 0x15 byte is the length part.
        return None
    power: float | None = None
    if len(man_data) >= IBEACON_FULL_LEN:
        # There really is at least one beacon out there that lacks this! See #466
        power = int.from_bytes(man_data[22:23], signed=True)
    return IBeaconParse(
        uuid=man_data[2:18].hex().lower(),
        major=str(int.from_bytes(man_data[18:20], byteorder="big")),
        minor=str(int.from_bytes(man_data[20:22], byteorder="big")),
        power=power,
    )


def parse_in100(man_data: bytes) -> In100Telemetry:
    """
    Decode IN100 telemetry from a 0x0505 manufacturer-data payload.

    The first five bytes encode supply voltage, temperature and an ADC voltage.
    A short/malformed payload yields cleared (None) values while still carrying
    the raw payload, so the caller can flag the device as detected regardless.
    """
    vcc: float | None = None
    temp_c: float | None = None
    adc_voltage: float | None = None
    if len(man_data) >= IN100_PAYLOAD_LEN:
        payload = man_data[:IN100_PAYLOAD_LEN]
        vcc = payload[0] / _IN100_VCC_DIVISOR
        temp_c = int.from_bytes(payload[1:3], byteorder="big", signed=True) / _IN100_TEMP_DIVISOR
        adc_voltage = int.from_bytes(payload[3:5], byteorder="big", signed=False) / _IN100_ADC_DIVISOR
    return In100Telemetry(
        raw_payload_hex=man_data.hex(),
        payload_len=len(man_data),
        vcc=vcc,
        temp_c=temp_c,
        adc_voltage=adc_voltage,
    )
