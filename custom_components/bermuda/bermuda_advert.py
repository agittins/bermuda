"""
Bermuda's internal representation of a device to scanner relationship.

This can also be thought of as the representation of an advertisement
received by a given scanner, in that it's the advert that links the
device to a scanner. Multiple scanners will receive a given advert, but
each receiver experiences it (well, the rssi) uniquely.

Every bluetooth scanner is a BermudaDevice, but this class
is the nested entry that gets attached to each device's `scanners`
dict. It is a sub-set of a 'device' and will have attributes specific
to the combination of the scanner and the device it is reporting.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any, Final

from bluetooth_data_tools import monotonic_time_coarse

from .const import (
    _LOGGER,
    CONF_ATTENUATION,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    DEFAULT_MAX_VELOCITY,
    DEFAULT_SMOOTHING_SAMPLES,
    DISTANCE_TIMEOUT,
    HIST_KEEP_COUNT,
    MOBILITY_STATIONARY,
)
from .distance_filter import median_abs_deviation, minimum_hugging_average, peak_retreat_velocity
from .util import clean_charbuf, rssi_to_metres

if TYPE_CHECKING:
    from bleak.backends.scanner import AdvertisementData

    from .bermuda_device import BermudaDevice


class BermudaAdvert:
    """
    Represents details from a scanner relevant to a specific device.

    Effectively a link between two BermudaDevices, being the tracked device
    and the scanner device. So each transmitting device will have a collection
    of these BermudaDeviceScanner entries, one for each scanner that has picked
    up the advertisement.

    This is created (and updated) by the receipt of an advertisement, which represents
    a BermudaDevice hearing an advert from another BermudaDevice, if that makes sense!

    A BermudaDevice's "adverts" property will contain one of these for each
    scanner that has "seen" it.

    """

    def __hash__(self) -> int:
        """The device-mac / scanner mac uniquely identifies a received advertisement pair."""
        return hash((self.device_address, self.scanner_address))

    def __init__(
        self,
        parent_device: BermudaDevice,  # The device being tracked
        advertisementdata: AdvertisementData,  # The advertisement info from the device, received by the scanner
        options: dict[str, Any],
        scanner_device: BermudaDevice,  # The scanner device that "saw" it.
    ) -> None:
        self.scanner_address: Final[str] = scanner_device.address
        self.device_address: Final[str] = parent_device.address
        self._device = parent_device
        self.ref_power: float = self._device.ref_power  # Take from parent at first, might be changed by metadevice l8r
        self.apply_new_scanner(scanner_device)

        self.options = options

        self.stamp: float = 0
        self.new_stamp: float | None = None  # Set when a new advert is loaded from update
        self.rssi: float | None = None
        self.rssi_filtered: float | None = None  # robust-clamped, EMA-smoothed RSSI (mobility-aware)
        self.rssi_dispersion: float = 0.0  # MAD-based jitter estimate of the filtered RSSI
        self.rssi_adjusted_raw: float | None = None  # raw RSSI + scanner offset, pre-filter
        self.tx_power: float | None = None
        self.rssi_distance: float | None = None
        self.rssi_distance_raw: float | None = None
        self.stale_update_count = 0  # How many times we did an update but no new stamps were found.
        self.hist_stamp: list[float] = []
        self.hist_rssi: list[int] = []
        self.hist_rssi_adjusted: list[float] = []  # offset-adjusted RSSI samples (pre-filter)
        self.hist_rssi_filtered: list[float] = []  # filtered RSSI history (for dispersion)
        self.hist_distance: list[float | None] = []
        self.hist_distance_by_interval: list[float | None] = []  # updated per-interval
        self.hist_interval: list[float | None] = []  # WARNING: This is actually "age of ad when we polled"
        self.hist_velocity: list[float] = []  # Effective velocity versus previous stamped reading
        self.conf_rssi_offset = self.options.get(CONF_RSSI_OFFSETS, {}).get(self.scanner_address, 0)
        self.conf_ref_power = self.options.get(CONF_REF_POWER)
        self.conf_attenuation = self.options.get(CONF_ATTENUATION)
        # Coordinator always seeds these two into options before any advert is
        # created; the fallback here is purely defensive (matches the same default).
        self.conf_max_velocity: float = self.options.get(CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY)
        self.conf_smoothing_samples: int = self.options.get(CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES)
        self.local_name: list[tuple[str, bytes]] = []
        self.manufacturer_data: list[dict[int, bytes]] = []
        self.service_data: list[dict[str, bytes]] = []
        self.service_uuids: list[str] = []

        # Just pass the rest on to update...
        self.update_advertisement(advertisementdata, self.scanner_device)

    def apply_new_scanner(self, scanner_device: BermudaDevice) -> None:
        self.name: str = scanner_device.name  # or scandata.scanner.name
        self.scanner_device = scanner_device  # links to the source device
        if self.scanner_address != scanner_device.address:
            _LOGGER.error("Advert %s received new scanner with wrong address %s", self.__repr__(), scanner_device)
        self.area_id: str | None = scanner_device.area_id
        self.area_name: str | None = scanner_device.area_name
        # Only remote scanners log timestamps, local usb adaptors do not.
        self.scanner_sends_stamps = scanner_device.is_remote_scanner

    def update_advertisement(self, advertisementdata: AdvertisementData, scanner_device: BermudaDevice) -> None:
        """
        Update gets called every time we see a new packet or
        every time we do a polled update.

        This method needs to update all the history and tracking data for this
        device+scanner combination. This method only gets called when a given scanner
        claims to have data.
        """
        #
        # We might get called without there being a new advert to process, so
        # exit quickly if that's the case (ideally we will catch it earlier in future)
        #
        if scanner_device is not self.scanner_device:
            _LOGGER.debug(
                "Replacing stale scanner device %s with %s", self.scanner_device.__repr__(), scanner_device.__repr__()
            )
            self.apply_new_scanner(scanner_device)

        scanner = self.scanner_device
        new_stamp: float | None = None

        if self.scanner_sends_stamps:
            new_stamp = scanner.async_as_scanner_get_stamp(self.device_address)

            if new_stamp is None:
                self.stale_update_count += 1
                return

            if self.stamp > new_stamp:
                # The existing stamp is NEWER, bail.
                self.stale_update_count += 1
                return

            if self.stamp == new_stamp:
                # We've seen this stamp before. Bail.
                self.stale_update_count += 1
                return

        elif self.rssi != advertisementdata.rssi:
            # If the rssi has changed from last time, consider it "new". Since this scanner does
            # not send stamps, this is probably a USB bluetooth adaptor.
            new_stamp = monotonic_time_coarse() - 3.0  # age usb adaptors slightly, since they are not "fresh"
        else:
            # USB Adaptor has nothing new for us, bail.
            return

        # Update our parent scanner's last_seen if we have a new stamp.
        if new_stamp > self.scanner_device.last_seen + 0.01:  # some slight warp seems common.
            self.scanner_device.last_seen = new_stamp

        if len(self.hist_stamp) == 0 or new_stamp is not None:
            # this is the first entry or a new one, bring in the new reading
            # and calculate the distance.

            self.rssi = advertisementdata.rssi
            self.hist_rssi.insert(0, self.rssi)

            self._update_raw_distance(reading_is_new=True)

            # Note: this is not actually the interval between adverts,
            # but rather a function of our UPDATE_INTERVAL plus the packet
            # interval. The bluetooth integration does not currently store
            # interval data, only stamps of the most recent packet.
            # So it more accurately reflects "How much time passed between
            # the two last packets we observed" - which should be a multiple
            # of the true inter-packet interval. For stamps from local bluetooth
            # adaptors (usb dongles) it reflects "Which update cycle last saw a
            # different rssi", which will be a multiple of our update interval.
            if new_stamp is not None and self.stamp is not None:
                _interval = new_stamp - self.stamp
            else:
                _interval = None
            self.hist_interval.insert(0, _interval)

            self.stamp = new_stamp or 0
            self.hist_stamp.insert(0, self.stamp)

        # if self.tx_power is not None and scandata.advertisement.tx_power != self.tx_power:
        #     # Not really an error, we just don't account for this happening -
        #     # I want to know if it does.
        #     # AJG 2024-01-11: This does happen. Looks like maybe apple devices?
        #     # Changing from warning to debug to quiet users' logs.
        #     # Also happens with esphome set with long beacon interval tx, as it alternates
        #     # between sending some generic advert and the iBeacon advert. ie, it's bogus for that
        #     # case.
        #     _LOGGER.debug(
        #         "Device changed TX-POWER! That was unexpected: %s %sdB",
        #         self.parent_device_address,
        #         scandata.advertisement.tx_power,
        #     )
        self.tx_power = advertisementdata.tx_power

        # Store each of the extra advertisement fields in historical lists.
        # Track if we should tell the parent device to update its name
        _want_name_update = False
        if advertisementdata.local_name is not None:
            # It's not uncommon to find BT devices with nonascii junk in their
            # local_name (like nulls, \n, etc). Store a cleaned version as str
            # and the original as bytes.
            # Devices may also advert multiple names over time.
            nametuplet = (clean_charbuf(advertisementdata.local_name), advertisementdata.local_name.encode())
            if len(self.local_name) == 0 or self.local_name[0] != nametuplet:
                self.local_name.insert(0, nametuplet)
                del self.local_name[HIST_KEEP_COUNT:]
                # Lets see if we should pass the new name up to the parent device.
                if self._device.name_bt_local_name is None or len(self._device.name_bt_local_name) < len(nametuplet[0]):
                    self._device.name_bt_local_name = nametuplet[0]
                    _want_name_update = True

        if len(self.manufacturer_data) == 0 or self.manufacturer_data[0] != advertisementdata.manufacturer_data:
            self.manufacturer_data.insert(0, advertisementdata.manufacturer_data)

            # If manufacturing data changes, we call the update. This is because iBeacons might change their
            # sent details, in which case we need to re-match them.
            self._device.process_manufacturer_data(self)
            _want_name_update = True
            del self.manufacturer_data[HIST_KEEP_COUNT:]

        if len(self.service_data) == 0 or self.service_data[0] != advertisementdata.service_data:
            self.service_data.insert(0, advertisementdata.service_data)
            if advertisementdata.service_data not in self.service_data[1:]:
                _want_name_update = True
            del self.service_data[HIST_KEEP_COUNT:]

        for service_uuid in advertisementdata.service_uuids:
            if service_uuid not in self.service_uuids:
                self.service_uuids.insert(0, service_uuid)
                _want_name_update = True
                del self.service_uuids[HIST_KEEP_COUNT:]

        if _want_name_update:
            self._device.make_name()

        # Finally, save the new advert timestamp.
        self.new_stamp = new_stamp

    def _rssi_filter_policy(self) -> tuple[int, float, float]:
        """Return mobility-aware RSSI filter params: (window, ema_alpha, base_outlier_db)."""
        if self._device.get_mobility_type() == MOBILITY_STATIONARY:
            return (13, 0.22, 12.0)
        return (9, 0.45, 15.0)

    def _update_filtered_rssi(self, adjusted_rssi: float) -> float:
        """
        Apply robust outlier handling + EMA to a new (offset-adjusted) RSSI sample.

        Clamps a spike to the recent median when it exceeds a MAD-derived threshold,
        then exponentially smooths it, and refreshes the dispersion (jitter) estimate.
        The window/alpha/threshold come from the device's mobility mode.
        """
        window, alpha, outlier_db = self._rssi_filter_policy()
        prior = self.hist_rssi_adjusted[:window]
        sample = adjusted_rssi

        if len(prior) >= 3:
            med = statistics.median(prior)
            mad = median_abs_deviation(prior, med)
            robust_sigma = max(mad * 1.4826, 1.0)
            threshold = max(outlier_db, robust_sigma * 3.0)
            if abs(sample - med) > threshold:
                sample = med

        if self.rssi_filtered is None:
            self.rssi_filtered = sample
        else:
            self.rssi_filtered = (alpha * sample) + ((1 - alpha) * self.rssi_filtered)

        self.hist_rssi_adjusted.insert(0, sample)
        self.hist_rssi_filtered.insert(0, self.rssi_filtered)
        del self.hist_rssi_adjusted[HIST_KEEP_COUNT:]
        del self.hist_rssi_filtered[HIST_KEEP_COUNT:]

        filt_window = self.hist_rssi_filtered[:window]
        self.rssi_dispersion = median_abs_deviation(filt_window) * 1.4826 if len(filt_window) >= 3 else 0.0

        return self.rssi_filtered

    def _update_raw_distance(self, *, reading_is_new: bool = True) -> float | None:
        """
        Converts rssi to raw distance and updates history stack and
        returns the new raw distance.

        reading_is_new should only be called by the regular update
        cycle, as it creates a new entry in the histories. Call with
        false if you just need to set / override distance measurements
        immediately, perhaps between cycles, in order to reflect a
        setting change (such as altering a device's ref_power setting).
        """
        # Check if we should use a device-based ref_power
        if self.ref_power == 0:  # No user-supplied per-device value
            # use global default
            ref_power = self.conf_ref_power
        else:
            ref_power = self.ref_power

        if self.rssi is None:
            # No rssi reading yet (advert created but the first update was a
            # stale / no-stamp early-return). Nothing to recalculate yet.
            return self.rssi_distance_raw

        adjusted_rssi = self.rssi + self.conf_rssi_offset
        self.rssi_adjusted_raw = adjusted_rssi
        if reading_is_new:
            filtered_rssi = self._update_filtered_rssi(adjusted_rssi)
        else:
            # Override (e.g. a ref_power change between cycles): reuse the last
            # filtered RSSI rather than feeding a phantom sample into the filter.
            filtered_rssi = self.rssi_filtered if self.rssi_filtered is not None else adjusted_rssi
        distance = rssi_to_metres(filtered_rssi, ref_power, self.conf_attenuation)
        self.rssi_distance_raw = distance
        if reading_is_new:
            # Add a new historical reading
            self.hist_distance.insert(0, distance)
            # don't insert into hist_distance_by_interval, that's done by the caller.
        elif self.rssi_distance is not None:
            # We are over-riding readings between cycles.
            # We will force the new measurement, but only if we were
            # already showing a "current" distance, as we don't want
            # to "freshen" a measurement that was already out of date,
            # hence the elif not none above.
            self.rssi_distance = distance
            if len(self.hist_distance) > 0:
                self.hist_distance[0] = distance
            else:
                self.hist_distance.append(distance)
            if len(self.hist_distance_by_interval) > 0:
                self.hist_distance_by_interval[0] = distance
            # We don't else because we don't want to *add* a hist-by-interval reading, only
            # modify in-place.
        return distance

    def set_ref_power(self, value: float) -> float | None:
        """
        Set a new reference power and return the resulting distance.

        Typically called from the parent device when either the user changes the calibration
        of ref_power for a device, or when a metadevice takes on a new source device, and
        propagates its own ref_power to our parent.

        Note that it is unlikely to return None as its only returning the raw, not filtered
        distance = the exception being uninitialised entries.
        """
        # When the user updates the ref_power we want to reflect that change immediately,
        # and not subject it to the normal smoothing algo.
        # But make sure it's actually different, in case it's just a metadevice propagating
        # its own ref_power without need.
        if value != self.ref_power:
            self.ref_power = value
            return self._update_raw_distance(reading_is_new=False)
        return self.rssi_distance_raw

    def calculate_data(self) -> None:
        """
        Filter and update distance estimates.

        All smoothing and noise-management of the distance between a scanner
        and a device should be done in this method, as it is
        guaranteed to be called on every update cycle, for every
        scanner that has ever reported an advert for this device
        (even if it is not reporting one currently).

        If new_stamp is None it implies that the scanner has not reported
        an updated advertisement since our last update cycle,
        so we may need to check if this device should be timed
        out or otherwise dealt with.

        If new_stamp is not None it means we just had an updated
        rssi_distance_raw value which should be processed.

        This is called by self.update, but should also be called for
        any remaining scanners that have not sent in an update in this
        cycle. This is mainly because usb/bluez adaptors seem to flush
        their advertisement lists quicker than we time out, so we need
        to make sure we still update the scanner entry even if the scanner
        no longer carries advert history for this device.

        Note: Noise in RSSI readings is VERY asymmetric. Ultimately,
        a closer distance is *always* more accurate than a previous
        more distant measurement. Any measurement might be true,
        or it is likely longer than the truth - and (almost) never
        shorter.

        For a new, long measurement to be true, we'd want to see some
        indication of rising measurements preceding it, or at least a
        long time since our last measurement.

        It's tempting to treat no recent measurement as implying an increase
        in distance, but doing so would wreak havoc when we later try to
        implement trilateration, so better to simply cut a sensor off as
        "away" from a scanner when it hears no new adverts. DISTANCE_TIMEOUT
        is how we decide how long to wait, and should accommodate for dropped
        packets and for temporary occlusion (dogs' bodies etc)
        """
        new_stamp = self.new_stamp  # should have been set by update()
        self.new_stamp = None  # Clear so we know if an update is missed next cycle

        if self.rssi_distance is None and new_stamp is not None:
            # DEVICE HAS ARRIVED!
            # We have just newly come into range (or we're starting up)
            # accept the new reading as-is.
            self.rssi_distance = self.rssi_distance_raw
            # And ensure the smoothing history gets a fresh start

            if self.rssi_distance_raw is not None:
                # clear tends to be more efficient than re-creating
                # and might have fewer side-effects.
                self.hist_distance_by_interval.clear()
                self.hist_distance_by_interval.append(self.rssi_distance_raw)
                # Seed the RSSI filter so it starts fresh on arrival too.
                if self.rssi_filtered is None:
                    self.rssi_filtered = self.rssi_adjusted_raw
                self.hist_rssi_filtered.clear()
                if self.rssi_filtered is not None:
                    self.hist_rssi_filtered.append(self.rssi_filtered)

        elif new_stamp is None and (self.stamp is None or self.stamp < monotonic_time_coarse() - DISTANCE_TIMEOUT):
            # DEVICE IS AWAY!
            # Last distance reading is stale, mark device distance as unknown.
            self.rssi_distance = None
            self.rssi_filtered = None
            self.rssi_dispersion = 0.0
            # Clear the smoothing history
            if len(self.hist_distance_by_interval) > 0:
                self.hist_distance_by_interval.clear()
            self.hist_rssi_filtered.clear()
            self.hist_rssi_adjusted.clear()

        else:
            # Add the current reading (whether new or old) to
            # a historical log that is evenly spaced by update_interval.

            # Verify the new reading is vaguely sensible. If it isn't, we
            # ignore it by duplicating the last cycle's reading. The peak
            # retreat velocity over recent history tells us if it looks bogus.
            velocity = peak_retreat_velocity(self.hist_distance, self.hist_stamp)
            self.hist_velocity.insert(0, velocity)

            if velocity > self.conf_max_velocity:
                if self._device.create_sensor:
                    _LOGGER.debug(
                        "This sparrow %s flies too fast (%.2fm/s), ignoring",
                        self._device.name,
                        velocity,
                    )

                # Discard the bogus reading by duplicating the last
                if len(self.hist_distance_by_interval) > 0:
                    self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
                else:
                    # If nothing to duplicate, just plug in the raw distance.
                    self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
            else:
                self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)

            # trim the log to length
            if len(self.hist_distance_by_interval) > self.conf_smoothing_samples:
                del self.hist_distance_by_interval[self.conf_smoothing_samples :]

            # Calculate a moving-window average that hugs the closest (most
            # reliable) recent readings. See distance_filter for the rationale.
            movavg = minimum_hugging_average(self.hist_distance_by_interval, self.rssi_distance_raw)

            # Finally, set the new, smoothed rssi_distance value.
            # The average is only helpful if it's lower than the actual reading.
            if self.rssi_distance_raw is None or movavg < self.rssi_distance_raw:
                self.rssi_distance = movavg
            else:
                self.rssi_distance = self.rssi_distance_raw

        # Trim our history lists
        del self.hist_distance[HIST_KEEP_COUNT:]
        del self.hist_interval[HIST_KEEP_COUNT:]
        del self.hist_rssi[HIST_KEEP_COUNT:]
        del self.hist_stamp[HIST_KEEP_COUNT:]
        del self.hist_velocity[HIST_KEEP_COUNT:]

    def to_dict(self) -> dict[str, Any]:
        """Convert class to serialisable dict for dump_devices."""
        # using "is" comparisons instead of string matching means
        # linting and typing can catch errors.
        out: dict[str, Any] = {}
        for var, val in vars(self).items():
            if val is self.options:
                # skip certain vars that we don't want in the dump output.
                continue
            if val is self._device or val is self.scanner_device:
                # objects we might want to represent but not fully iterate etc.
                out[var] = val.__repr__()
                continue
            if val is self.local_name:
                out[var] = {}
                for namestr, namebytes in self.local_name:
                    out[var][namestr] = namebytes.hex()
                continue
            if val is self.manufacturer_data:
                out[var] = {}
                for manrow in self.manufacturer_data:
                    for manid, manbytes in manrow.items():
                        out[var][manid] = manbytes.hex()
                continue
            if val is self.service_data:
                out[var] = {}
                for svrow in self.service_data:
                    for svid, svbytes in svrow.items():
                        out[var][svid] = svbytes.hex()
                continue
            if isinstance(val, str | int):
                out[var] = val
                continue
            if isinstance(val, float):
                out[var] = round(val, 4)
                continue
            if isinstance(val, list):
                out[var] = []
                for row in val:
                    if isinstance(row, float):
                        out[var].append(round(row, 4))
                    else:
                        out[var].append(row)
                continue
            out[var] = val.__repr__()
        return out

    def __repr__(self) -> str:
        """Help debugging by giving it a clear name instead of empty dict."""
        return f"{self.device_address}__{self.scanner_device.name}"
