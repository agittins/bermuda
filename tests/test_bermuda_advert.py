"""
Tests for BermudaAdvert class in bermuda_advert.py.
"""

import logging

import pytest
from unittest.mock import MagicMock, patch
from custom_components.bermuda.bermuda_advert import BermudaAdvert
from custom_components.bermuda.bermuda_device import BermudaDevice
from bleak.backends.scanner import AdvertisementData


@pytest.fixture
def mock_parent_device():
    """Fixture for mocking the parent BermudaDevice."""
    device = MagicMock(spec=BermudaDevice)
    device.address = "aa:bb:cc:dd:ee:ff"
    device.ref_power = -59
    device.name_bt_local_name = None
    device.name = "mock parent name"
    device.tracker_type = None  # instance attr; spec mock needs it set explicitly
    return device


@pytest.fixture
def mock_scanner_device():
    """Fixture for mocking the scanner BermudaDevice."""
    scanner = MagicMock(spec=BermudaDevice)
    scanner.address = "11:22:33:44:55:66"
    scanner.name = "Mock Scanner"
    scanner.area_id = "server_room"
    scanner.area_name = "server room"
    scanner.is_remote_scanner = True
    scanner.last_seen = 0.0
    scanner.stamps = {"AA:BB:CC:DD:EE:FF": 123.45}
    scanner.async_as_scanner_get_stamp.return_value = 123.45
    return scanner


@pytest.fixture
def mock_advertisement_data():
    """Fixture for mocking AdvertisementData."""
    advert = MagicMock(spec=AdvertisementData)
    advert.rssi = -70
    advert.tx_power = -20
    advert.local_name = "Mock advert Local Name"
    advert.name = "Mock advert name"
    advert.manufacturer_data = {76: b"\x02\x15"}
    advert.service_data = {"0000abcd-0000-1000-8000-00805f9b34fb": b"\x01\x02"}
    advert.service_uuids = ["0000abcd-0000-1000-8000-00805f9b34fb"]
    return advert


@pytest.fixture
def bermuda_advert(mock_parent_device, mock_advertisement_data, mock_scanner_device):
    """Fixture for creating a BermudaAdvert instance."""
    options = {
        "rssi_offsets": {"11:22:33:44:55:66": 5},
        "ref_power": -59,
        "attenuation": 2.0,
        "max_velocity": 3.0,
        "smoothing_samples": 5,
    }
    ba = BermudaAdvert(
        parent_device=mock_parent_device,
        advertisementdata=mock_advertisement_data,
        options=options,
        scanner_device=mock_scanner_device,
    )
    ba.name = "foo name"
    return ba


def test_bermuda_advert_initialization(bermuda_advert):
    """Test BermudaAdvert initialization."""
    assert bermuda_advert.device_address == "aa:bb:cc:dd:ee:ff"
    assert bermuda_advert.scanner_address == "11:22:33:44:55:66"
    assert bermuda_advert.ref_power == -59
    assert bermuda_advert.stamp == 123.45
    assert bermuda_advert.rssi == -70


def test_rssi_filter_ema_and_outlier_clamp(bermuda_advert):
    """The mobility-aware RSSI filter smooths stable readings and clamps a spike."""
    ba = bermuda_advert
    # A run of stable readings: the filtered value tracks the input closely.
    for _ in range(5):
        ba._update_filtered_rssi(-70.0)
    assert -72 < ba.rssi_filtered < -68
    # A wild spike (well beyond the MAD-derived threshold) is clamped to the median,
    # so it can't drag the filtered value all the way up.
    ba._update_filtered_rssi(-30.0)
    assert ba.rssi_filtered < -50
    assert ba.rssi_dispersion >= 0.0


def test_rssi_filter_policy_depends_on_mobility(bermuda_advert):
    """Stationary devices use a longer/steadier RSSI window than moving ones."""
    ba = bermuda_advert
    ba._device.get_mobility_type = lambda: "stationary"
    assert ba._rssi_filter_policy() == (13, 0.22, 12.0)
    ba._device.get_mobility_type = lambda: "moving"
    assert ba._rssi_filter_policy() == (9, 0.45, 15.0)


def test_apply_new_scanner(bermuda_advert, mock_scanner_device):
    """Test apply_new_scanner method."""
    bermuda_advert.apply_new_scanner(mock_scanner_device)
    assert bermuda_advert.scanner_device == mock_scanner_device
    assert bermuda_advert.scanner_sends_stamps is True


def test_update_advertisement(bermuda_advert, mock_advertisement_data, mock_scanner_device):
    """Test update_advertisement method."""
    bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)
    assert bermuda_advert.rssi == -70
    assert bermuda_advert.tx_power == -20
    assert bermuda_advert.local_name[0][0] == "Mock advert Local Name"
    assert bermuda_advert.manufacturer_data[0][76] == b"\x02\x15"
    assert bermuda_advert.service_data[0]["0000abcd-0000-1000-8000-00805f9b34fb"] == b"\x01\x02"


def test_set_ref_power(bermuda_advert):
    """Test set_ref_power method."""
    new_distance = bermuda_advert.set_ref_power(-65)
    assert bermuda_advert.ref_power == -65
    assert new_distance is not None


def test_calculate_data_device_arrived(bermuda_advert):
    """Test calculate_data method when device arrives."""
    bermuda_advert.new_stamp = 123.45
    bermuda_advert.rssi_distance_raw = 5.0
    bermuda_advert.calculate_data()
    assert bermuda_advert.rssi_distance == 5.0


def test_calculate_data_device_away(bermuda_advert):
    """Test calculate_data method when device is away."""
    bermuda_advert.stamp = 0.0
    bermuda_advert.new_stamp = None
    bermuda_advert.calculate_data()
    assert bermuda_advert.rssi_distance is None


def test_to_dict(bermuda_advert):
    """Test to_dict method."""
    advert_dict = bermuda_advert.to_dict()
    assert isinstance(advert_dict, dict)
    assert advert_dict["device_address"] == "aa:bb:cc:dd:ee:ff"
    assert advert_dict["scanner_address"] == "11:22:33:44:55:66"


def test_repr(bermuda_advert):
    """Test __repr__ method."""
    repr_str = repr(bermuda_advert)
    assert repr_str == "aa:bb:cc:dd:ee:ff__Mock Scanner"


def test_hash(bermuda_advert):
    """__hash__ is derived from the (device_address, scanner_address) pair."""
    assert hash(bermuda_advert) == hash((bermuda_advert.device_address, bermuda_advert.scanner_address))
    # Also usable as a dict/set key, as intended.
    assert {bermuda_advert: "x"}[bermuda_advert] == "x"


def test_apply_new_scanner_wrong_address_logs_error(bermuda_advert, caplog):
    """A scanner_device with a mismatched address is only logged, not enforced."""
    wrong_scanner = MagicMock(spec=BermudaDevice)
    wrong_scanner.address = "ff:ff:ff:ff:ff:ff"  # different from bermuda_advert.scanner_address
    wrong_scanner.area_id = "other_area"
    wrong_scanner.area_name = "Other"
    wrong_scanner.is_remote_scanner = True
    wrong_scanner.name = "Wrong Scanner"

    with caplog.at_level(logging.ERROR):
        bermuda_advert.apply_new_scanner(wrong_scanner)  # must not raise

    assert "wrong address" in caplog.text
    # It still applies the (mismatched) scanner, since this is only a log warning.
    assert bermuda_advert.scanner_device is wrong_scanner


def test_update_advertisement_replaces_stale_scanner_device(bermuda_advert, mock_advertisement_data):
    """A different scanner_device object (same address) triggers apply_new_scanner."""
    new_scanner = MagicMock(spec=BermudaDevice)
    new_scanner.address = bermuda_advert.scanner_address
    new_scanner.area_id = "new_area"
    new_scanner.area_name = "New Area"
    new_scanner.is_remote_scanner = True
    new_scanner.name = "New Scanner"
    new_scanner.last_seen = 0.0
    new_scanner.async_as_scanner_get_stamp.return_value = 200.0

    bermuda_advert.update_advertisement(mock_advertisement_data, new_scanner)

    assert bermuda_advert.scanner_device is new_scanner
    assert bermuda_advert.area_name == "New Area"


def test_update_advertisement_no_new_stamp_marks_stale(bermuda_advert, mock_advertisement_data, mock_scanner_device):
    """When the remote scanner has no stamp for us, we count it stale and change nothing else."""
    mock_scanner_device.async_as_scanner_get_stamp.return_value = None
    prior_rssi = bermuda_advert.rssi
    prior_count = bermuda_advert.stale_update_count

    bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)

    assert bermuda_advert.stale_update_count == prior_count + 1
    assert bermuda_advert.rssi == prior_rssi


def test_update_advertisement_older_stamp_marks_stale(bermuda_advert, mock_advertisement_data, mock_scanner_device):
    """A stamp older than our current one is rejected and counted as stale."""
    bermuda_advert.stamp = 99999.0
    mock_scanner_device.async_as_scanner_get_stamp.return_value = 1.0
    prior_rssi = bermuda_advert.rssi
    prior_count = bermuda_advert.stale_update_count

    bermuda_advert.update_advertisement(mock_advertisement_data, mock_scanner_device)

    assert bermuda_advert.stale_update_count == prior_count + 1
    assert bermuda_advert.rssi == prior_rssi


def test_update_advertisement_usb_adaptor_nothing_new_returns(bermuda_advert, mock_advertisement_data):
    """A non-remote (USB) scanner with an unchanged rssi is a no-op."""
    usb_scanner = MagicMock(spec=BermudaDevice)
    usb_scanner.address = bermuda_advert.scanner_address
    usb_scanner.area_id = "a"
    usb_scanner.area_name = "A"
    usb_scanner.is_remote_scanner = False
    usb_scanner.name = "USB Scanner"
    usb_scanner.last_seen = 0.0
    bermuda_advert.apply_new_scanner(usb_scanner)
    assert bermuda_advert.scanner_sends_stamps is False

    mock_advertisement_data.rssi = bermuda_advert.rssi  # unchanged from last reading
    prior_len = len(bermuda_advert.hist_stamp)

    bermuda_advert.update_advertisement(mock_advertisement_data, usb_scanner)

    assert len(bermuda_advert.hist_stamp) == prior_len


def test_update_advertisement_interval_none_when_stamp_corrupted(bermuda_advert, mock_advertisement_data):
    """_interval falls back to None if self.stamp is (unrealistically) None.

    Under normal operation self.stamp is always a float (see the type hint and every
    assignment site in bermuda_advert.py: `self.stamp = new_stamp or 0`), so this
    ``else`` branch is not reachable through any legitimate call sequence. This test
    reaches it only by directly corrupting the internal attribute afterwards, which is
    a real (if narrow/unrealistic) state transition Python permits -- see the final
    report for why this looks like dead code in practice.
    """
    usb_scanner = MagicMock(spec=BermudaDevice)
    usb_scanner.address = bermuda_advert.scanner_address
    usb_scanner.area_id = "a"
    usb_scanner.area_name = "A"
    usb_scanner.is_remote_scanner = False
    usb_scanner.name = "USB Scanner"
    usb_scanner.last_seen = 0.0
    bermuda_advert.apply_new_scanner(usb_scanner)

    bermuda_advert.stamp = None  # unrealistic corruption of a normally-always-float attr
    mock_advertisement_data.rssi = bermuda_advert.rssi - 1  # force "changed" -> new_stamp assigned

    bermuda_advert.update_advertisement(mock_advertisement_data, usb_scanner)

    assert bermuda_advert.hist_interval[0] is None


def test_update_raw_distance_rssi_none_returns_raw_unchanged(bermuda_advert):
    """With no rssi reading yet, _update_raw_distance is a no-op returning the current raw distance."""
    bermuda_advert.rssi = None
    bermuda_advert.rssi_distance_raw = 42.0

    result = bermuda_advert._update_raw_distance()

    assert result == 42.0
    assert bermuda_advert.rssi_distance_raw == 42.0


def test_set_ref_power_overrides_history_in_place(bermuda_advert):
    """Between-cycle overrides overwrite hist_distance[0]/hist_distance_by_interval[0], not append."""
    bermuda_advert.rssi_distance = 10.0
    bermuda_advert.hist_distance = [10.0, 9.0]
    bermuda_advert.hist_distance_by_interval = [10.0, 9.0]

    new_distance = bermuda_advert.set_ref_power(-70)

    assert bermuda_advert.ref_power == -70
    assert bermuda_advert.rssi_distance == new_distance
    assert bermuda_advert.hist_distance[0] == new_distance
    assert bermuda_advert.hist_distance_by_interval[0] == new_distance
    assert len(bermuda_advert.hist_distance) == 2  # overwritten in place, not appended


def test_set_ref_power_overrides_history_appends_when_hist_distance_empty(bermuda_advert):
    """When hist_distance is empty, the override appends; hist_distance_by_interval never gains an entry."""
    bermuda_advert.rssi_distance = 10.0
    bermuda_advert.hist_distance = []
    bermuda_advert.hist_distance_by_interval = []

    new_distance = bermuda_advert.set_ref_power(-71)

    assert bermuda_advert.hist_distance == [new_distance]
    assert bermuda_advert.hist_distance_by_interval == []


def test_set_ref_power_same_value_returns_raw_without_mutating(bermuda_advert):
    """Setting ref_power to its current value is a no-op that just returns the raw distance."""
    current = bermuda_advert.ref_power
    prior_raw = bermuda_advert.rssi_distance_raw

    result = bermuda_advert.set_ref_power(current)

    assert result == prior_raw
    assert bermuda_advert.ref_power == current


def test_calculate_data_seeds_rssi_filtered_on_arrival(bermuda_advert):
    """On arrival, if rssi_filtered is still None it is seeded from rssi_adjusted_raw."""
    bermuda_advert.rssi_distance = None
    bermuda_advert.new_stamp = 555.0
    bermuda_advert.rssi_distance_raw = 5.0
    bermuda_advert.rssi_filtered = None
    bermuda_advert.rssi_adjusted_raw = -65.0

    bermuda_advert.calculate_data()

    assert bermuda_advert.rssi_filtered == -65.0


def test_calculate_data_too_fast_logs_and_seeds_empty_interval_history(bermuda_advert, mock_parent_device, caplog):
    """An implausible retreat velocity is discarded; with no history to duplicate, raw distance seeds it."""
    mock_parent_device.create_sensor = True
    bermuda_advert.rssi_distance = 5.0  # not None -> skip "arrived" branch
    bermuda_advert.new_stamp = 10.0  # not None -> skip "away" branch
    bermuda_advert.rssi_distance_raw = 1.0
    bermuda_advert.hist_distance = [100.0, 1.0]
    bermuda_advert.hist_stamp = [1.0, 0.0]
    bermuda_advert.hist_distance_by_interval = []

    with caplog.at_level(logging.DEBUG):
        bermuda_advert.calculate_data()

    assert "flies too fast" in caplog.text
    assert bermuda_advert.hist_distance_by_interval[0] == bermuda_advert.rssi_distance_raw


def test_calculate_data_trims_hist_distance_by_interval_to_smoothing_samples(bermuda_advert):
    """hist_distance_by_interval is trimmed back down to conf_smoothing_samples entries."""
    bermuda_advert.conf_smoothing_samples = 2
    bermuda_advert.rssi_distance = 5.0
    bermuda_advert.new_stamp = 10.0
    bermuda_advert.rssi_distance_raw = 2.0
    bermuda_advert.hist_distance = [2.0, 2.0]
    bermuda_advert.hist_stamp = [10.0, 9.0]
    bermuda_advert.hist_distance_by_interval = [9.0, 8.0]  # already at the limit before the new insert

    bermuda_advert.calculate_data()

    assert len(bermuda_advert.hist_distance_by_interval) == 2


def test_calculate_data_normal_insert_and_average_not_lower_than_raw(bermuda_advert):
    """A plausible velocity inserts the raw reading; when the average isn't lower, raw wins outright."""
    bermuda_advert.rssi_distance = 5.0
    bermuda_advert.new_stamp = 10.0
    bermuda_advert.rssi_distance_raw = 2.0
    bermuda_advert.hist_distance = [2.0, 2.0]
    bermuda_advert.hist_stamp = [10.0, 9.0]
    bermuda_advert.hist_distance_by_interval = []

    bermuda_advert.calculate_data()

    assert bermuda_advert.hist_distance_by_interval[0] == 2.0
    assert bermuda_advert.rssi_distance == bermuda_advert.rssi_distance_raw
