"""Characterization tests for BermudaAdvert distance smoothing.

These pin the *current behaviour* of ``BermudaAdvert.calculate_data`` so that
the planned extraction of a pure ``distance_filter`` module can be proven
behaviour-preserving. The two core, experience-tuned behaviours are:

* the velocity / anti-teleport filter (a reading that implies the device moved
  away faster than ``max_velocity`` is discarded by duplicating the previous
  reading), and
* the moving-window minimum-hugging average (the smoothed distance hugs the
  lowest recent reading, because RSSI noise is asymmetric — a closer reading is
  always more trustworthy than a farther one).

The expected values below are computed by hand from the algorithm and must not
change across the refactor.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bleak.backends.scanner import AdvertisementData

from custom_components.bermuda.bermuda_advert import BermudaAdvert
from custom_components.bermuda.bermuda_device import BermudaDevice


@pytest.fixture
def mock_parent_device():
    """Parent (tracked) device."""
    device = MagicMock(spec=BermudaDevice)
    device.address = "aa:bb:cc:dd:ee:ff"
    device.ref_power = -59
    device.name_bt_local_name = None
    device.name = "tracked device"
    device.create_sensor = False
    device.tracker_type = None  # instance attr; spec mock needs it set explicitly
    return device


@pytest.fixture
def mock_scanner_device():
    """Scanner (receiver) device."""
    scanner = MagicMock(spec=BermudaDevice)
    scanner.address = "11:22:33:44:55:66"
    scanner.name = "Mock Scanner"
    scanner.area_id = "lounge"
    scanner.area_name = "Lounge"
    scanner.is_remote_scanner = True
    scanner.last_seen = 0.0
    scanner.async_as_scanner_get_stamp.return_value = 123.45
    return scanner


@pytest.fixture
def mock_advertisement_data():
    """A representative advertisement."""
    advert = MagicMock(spec=AdvertisementData)
    advert.rssi = -70
    advert.tx_power = -20
    advert.local_name = "Mock advert Local Name"
    advert.name = "Mock advert name"
    advert.manufacturer_data = {76: b"\x02\x15"}
    advert.service_data = {}
    advert.service_uuids = []
    return advert


@pytest.fixture
def advert(mock_parent_device, mock_advertisement_data, mock_scanner_device):
    """A BermudaAdvert with deterministic smoothing config."""
    options = {
        "rssi_offsets": {},
        "ref_power": -59,
        "attenuation": 2.0,
        "max_velocity": 3.0,
        "smoothing_samples": 10,
    }
    return BermudaAdvert(
        parent_device=mock_parent_device,
        advertisementdata=mock_advertisement_data,
        options=options,
        scanner_device=mock_scanner_device,
    )


def test_minimum_hugging_average(advert):
    """The smoothed distance hugs the lowest recent reading, not the raw value.

    With raw=5.0 inserted at the front of [2.0, 8.0] → window [5.0, 2.0, 8.0],
    the running-minimum sum is 5.0 + 2.0 + 2.0 = 9.0 over 3 samples = 3.0,
    which is below the 5.0 raw reading and therefore wins.
    """
    advert.new_stamp = 1000.0
    advert.rssi_distance = 5.0  # non-None → not the "arrived" path
    advert.rssi_distance_raw = 5.0
    advert.stamp = 999.0
    advert.conf_max_velocity = 3.0
    advert.conf_smoothing_samples = 10
    # Flat history → zero velocity, no discard.
    advert.hist_stamp = [1000.0, 999.0]
    advert.hist_distance = [5.0, 5.0]
    advert.hist_distance_by_interval = [2.0, 8.0]

    advert.calculate_data()

    assert advert.hist_velocity[0] == 0
    assert advert.hist_distance_by_interval == [5.0, 2.0, 8.0]
    assert advert.rssi_distance == 3.0


def test_velocity_filter_discards_teleport(advert):
    """A reading implying a too-fast retreat is discarded (duplicate last value).

    raw jumps to 20.0 with velocity 15 m/s (> max 3.0), so the window keeps the
    previous 5.0 (duplicated) and the smoothed distance stays at 5.0 instead of
    teleporting to 20.0.
    """
    advert.new_stamp = 1000.0
    advert.rssi_distance = 5.0  # non-None → not the "arrived" path
    advert.rssi_distance_raw = 20.0  # bogus far reading
    advert.stamp = 999.0
    advert.conf_max_velocity = 3.0
    advert.conf_smoothing_samples = 10
    # Distance jumped 20-5 = 15m in 1s → velocity 15 m/s.
    advert.hist_stamp = [1000.0, 999.0]
    advert.hist_distance = [20.0, 5.0]
    advert.hist_distance_by_interval = [5.0]

    advert.calculate_data()

    assert advert.hist_velocity[0] == 15.0
    assert advert.hist_distance_by_interval == [5.0, 5.0]
    assert advert.rssi_distance == 5.0


def test_device_arrived_accepts_raw(advert):
    """First reading after being away is accepted verbatim (no smoothing)."""
    advert.rssi_distance = None  # was away
    advert.new_stamp = 1000.0
    advert.rssi_distance_raw = 7.5
    advert.hist_distance_by_interval = [99.0]

    advert.calculate_data()

    assert advert.rssi_distance == 7.5
    # History is reset to a fresh start on arrival.
    assert advert.hist_distance_by_interval == [7.5]


def test_device_away_clears_distance(advert):
    """A stale reading past DISTANCE_TIMEOUT marks the device as away."""
    advert.rssi_distance = 4.0
    advert.new_stamp = None
    advert.stamp = 0.0  # ancient → exceeds DISTANCE_TIMEOUT
    advert.hist_distance_by_interval = [4.0, 4.0]

    advert.calculate_data()

    assert advert.rssi_distance is None
    assert advert.hist_distance_by_interval == []
