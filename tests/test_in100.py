"""Tests for InPlay IN100 / DFRobot telemetry parsing and gated sensor creation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor.const import SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.const import DOMAIN, SIGNAL_DEVICE_IN100_NEW
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator
from custom_components.bermuda.sensor_entities import (
    BermudaSensorIn100AdcVoltage,
    BermudaSensorIn100Temperature,
    BermudaSensorIn100Vcc,
)

BASE = "aa:bb:cc:dd:ee:ff"


@pytest.fixture
def device():
    """A BermudaDevice with a preset manufacturer (so the fallback-name path is skipped)."""
    coordinator = MagicMock()
    coordinator.options = {}
    dev = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=coordinator)
    dev.manufacturer = "Preset"
    return dev


def _advert(*entries):
    """A minimal advert stub exposing manufacturer_data (newest first)."""
    return SimpleNamespace(manufacturer_data=list(entries))


# --------------------------------------------------------------------------- #
# Payload decoding                                                            #
# --------------------------------------------------------------------------- #


def test_decode_valid_payload(device):
    device._parse_in100_telemetry(_advert({0x0505: bytes.fromhex("680A4C0C6D0000")}))
    assert device.in100_vcc == 3.25
    assert device.in100_temp_c == 26.36
    assert device.in100_adc_voltage == 3.181
    assert device.in100_raw_payload_hex == "680a4c0c6d0000"
    assert device.in100_last_payload_len == 7
    assert device.in100_detected is True


def test_only_first_five_bytes_decoded(device):
    """Trailing bytes beyond the 5-byte telemetry block are ignored."""
    device._parse_in100_telemetry(_advert({0x0505: bytes.fromhex("680A740C6DFFFF")}))
    assert device.in100_vcc == 3.25
    assert device.in100_temp_c == 26.76
    assert device.in100_adc_voltage == 3.181


def test_negative_temperature(device):
    """Temperature is a signed 16-bit value."""
    # 0xFFFF -> -1 / 100 = -0.01 degrees.
    device._parse_in100_telemetry(_advert({0x0505: bytes.fromhex("68FFFF0C6D")}))
    assert device.in100_temp_c == -0.01


def test_short_payload_clears_values_but_flags_detected(device):
    device.in100_vcc = 9.99
    device.in100_temp_c = 99.99
    device.in100_adc_voltage = 9.99
    device._parse_in100_telemetry(_advert({0x0505: bytes.fromhex("680A4C")}))
    assert device.in100_vcc is None
    assert device.in100_temp_c is None
    assert device.in100_adc_voltage is None
    assert device.in100_raw_payload_hex == "680a4c"
    assert device.in100_last_payload_len == 3
    assert device.in100_detected is True


def test_only_latest_manufacturer_entry_used(device):
    """A stale 0x0505 outside the latest manufacturer-data entry is ignored."""
    device._parse_in100_telemetry(_advert({0x004C: b"latest-no-inplay"}, {0x0505: bytes.fromhex("680A4C0C6D")}))
    assert device.in100_detected is False
    assert device.in100_vcc is None


def test_no_inplay_data_is_noop(device):
    device._parse_in100_telemetry(_advert({0x004C: b"apple"}))
    assert device.in100_detected is False
    device._parse_in100_telemetry(_advert())  # empty manufacturer_data
    assert device.in100_detected is False


def test_fallback_manufacturer_name_when_unknown():
    coordinator = MagicMock()
    coordinator.options = {}
    dev = BermudaDevice(address="AA:BB:CC:DD:EE:FF", coordinator=coordinator)
    dev.manufacturer = None
    dev._parse_in100_telemetry(_advert({0x0505: bytes.fromhex("680A4C0C6D")}))
    assert dev.manufacturer == "InPlay / DFRobot"


# --------------------------------------------------------------------------- #
# Gating — sensors only ever spin up for detected IN100 devices               #
# --------------------------------------------------------------------------- #


def test_gating_condition_invariants(device):
    """The coordinator's fire condition flips exactly across detect / created."""

    def would_fire(d):
        return d.in100_detected and not d.create_in100_done

    assert would_fire(device) is False  # fresh, non-IN100 device
    device._parse_in100_telemetry(_advert({0x0505: bytes.fromhex("680A4C0C6D")}))
    assert would_fire(device) is True  # detected, not yet created
    device.create_in100_done = True
    assert would_fire(device) is False  # created once -> never again


def test_in100_sensors_created_sets_flag():
    coord = object.__new__(BermudaDataUpdateCoordinator)
    dev = SimpleNamespace(create_in100_done=False)
    coord._get_device = lambda _address: dev
    coord.in100_sensors_created("addr")
    assert dev.create_in100_done is True


# --------------------------------------------------------------------------- #
# Sensor entities                                                             #
# --------------------------------------------------------------------------- #


def _sensor(cls, **device_attrs):
    ent = object.__new__(cls)
    ent._device = SimpleNamespace(unique_id=BASE, **device_attrs)
    return ent


def test_in100_sensor_unique_ids_values_and_classes():
    vcc = _sensor(BermudaSensorIn100Vcc, in100_vcc=3.25)
    assert vcc.unique_id == f"{BASE}_in100_vcc"
    assert vcc.native_value == 3.25
    assert vcc.device_class == SensorDeviceClass.VOLTAGE
    assert vcc.entity_registry_enabled_default is True

    temp = _sensor(BermudaSensorIn100Temperature, in100_temp_c=26.36)
    assert temp.unique_id == f"{BASE}_in100_temperature"
    assert temp.native_value == 26.36
    assert temp.device_class == SensorDeviceClass.TEMPERATURE

    adc = _sensor(BermudaSensorIn100AdcVoltage, in100_adc_voltage=3.181)
    assert adc.unique_id == f"{BASE}_in100_adc_voltage"
    assert adc.native_value == 3.181
    assert adc.device_class == SensorDeviceClass.VOLTAGE


# --------------------------------------------------------------------------- #
# End-to-end: the signal actually spins up the three sensors                  #
# --------------------------------------------------------------------------- #


async def test_in100_signal_creates_sensors_end_to_end(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """Firing SIGNAL_DEVICE_IN100_NEW registers exactly the three IN100 sensors."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    dev = coordinator._get_or_create_device("AA:BB:CC:DD:EE:01")
    addr = dev.address  # canonical (normalised) key the platform looks up
    dev.create_sensor = True
    dev.name_by_user = "IN100 Beacon"
    dev.make_name()
    dev.in100_detected = True
    dev.in100_vcc = 3.25

    async_dispatcher_send(hass, SIGNAL_DEVICE_IN100_NEW, addr)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    base = dev.unique_id
    for suffix in ("in100_vcc", "in100_temperature", "in100_adc_voltage"):
        assert ent_reg.async_get_entity_id("sensor", DOMAIN, f"{base}_{suffix}") is not None, suffix
    # The coordinator was notified, so the signal won't re-create them.
    assert dev.create_in100_done is True
