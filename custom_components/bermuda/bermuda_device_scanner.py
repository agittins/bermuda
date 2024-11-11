"""
Bermuda's internal representation of a device's scanner entry.

Every bluetooth scanner gets its own BermudaDevice, but this class
is the nested entry that gets attached to each device's `scanners`
dict. It is a sub-set of a 'device' and will have attributes specific
to the combination of the scanner and the device it is reporting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from homeassistant.components.bluetooth import (
    MONOTONIC_TIME,
    BaseHaRemoteScanner,
    BluetoothScannerDevice,
)

from .const import (
    _LOGGER,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SMOOTHING_SAMPLES,
    DISTANCE_INFINITE,
    DISTANCE_TIMEOUT,
    HIST_KEEP_COUNT,
)

# from .const import _LOGGER_SPAM_LESS
from .util import rssi_to_metres

if TYPE_CHECKING:
    from .bermuda_device import BermudaDevice


class BermudaDeviceScanner(dict):
    """
    Represents details from a scanner relevant to a specific device.

    Effectively a link between two BermudaDevices, being the tracked device
    and the scanner device. So each transmitting device will have a collection
    of these BermudaDeviceScanner entries, one for each scanner that has picked
    up the advertisement.

    This is created (and updated) by the receipt of an advertisement, which represents
    a BermudaDevice hearing an advert from another BermudaDevice, if that makes sense!

    A BermudaDevice's "scanners" property will contain one of these for each
    scanner that has "seen" it.

    """

    def __init__(
        self,
        parent_device: BermudaDevice,  # The device being tracked
        scandata: BluetoothScannerDevice,  # The advertisement info from the device, received by the scanner
        options,
        scanner_device: BermudaDevice,  # The scanner device that "saw" it.
    ) -> None:
        # I am declaring these just to control their order in the dump,
        # which is a bit silly, I suspect.
        self.name: str = scandata.scanner.name
        self.scanner_device_name = scanner_device.name
        self.adapter: str = scandata.scanner.adapter
        self.address = scanner_device.address
        self.source: str = scandata.scanner.source
        self.area_id: str | None = scanner_device.area_id
        self.area_name: str | None = scanner_device.area_name
        self.parent_device = parent_device
        self.parent_device_address = parent_device.address
        self.scanner_device = scanner_device  # links to the source device
        self.options = options
        self.stamp: float | None = 0
        self.scanner_sends_stamps: bool = False
        self.new_stamp: float | None = None  # Set when a new advert is loaded from update
        self.rssi: float | None = None
        self.tx_power: float | None = None
        self.rssi_distance: float | None = None
        self.rssi_distance_raw: float | None = None
        self.ref_power: float = 0  # Override of global, set from parent device.
        self.stale_update_count = 0  # How many times we did an update but no new stamps were found.
        self.hist_stamp = []
        self.hist_rssi = []
        self.hist_distance = []
        self.hist_distance_by_interval = []  # updated per-interval
        self.hist_interval = []  # WARNING: This is actually "age of ad when we polled"
        self.hist_velocity = []  # Effective velocity versus previous stamped reading
        self.cached_remote_scanners = set()
        self.rssi_offset = self.options.get(CONF_RSSI_OFFSETS, {}).get(self.address, 0)
        self.ref_power = self.options.get(CONF_REF_POWER)
        self.attenuation = self.options.get(CONF_ATTENUATION)
        self.max_velocity = self.options.get(CONF_MAX_VELOCITY)
        self.smoothing_samples = self.options.get(CONF_SMOOTHING_SAMPLES)
        self.adverts: dict[str, list] = {
            "manufacturer_data": [],
            "service_data": [],
            "service_uuids": [],
            "platform_data": [],
        }

        # Just pass the rest on to update...
        self.update_advertisement(scandata)

    def update_advertisement(self, scandata: BluetoothScannerDevice):
        """
        Update gets called every time we see a new packet or
        every time we do a polled update.

        This method needs to update all the history and tracking data for this
        device+scanner combination. This method only gets called when a given scanner
        claims to have data.
        """
        # In case the scanner has changed it's details since startup:
        scanner = scandata.scanner
        self.name: str = scanner.name
        self.area_id: str = self.scanner_device.area_id
        self.area_name = self.scanner_device.area_name
        new_stamp: float | None
        # Only remote scanners log timestamps here (local usb adaptors do not),
        if scanner.source in self.cached_remote_scanners or isinstance(scanner, BaseHaRemoteScanner):
            self.cached_remote_scanners.add(scanner.source)
            # Found a remote scanner which has timestamp history...
            self.scanner_sends_stamps = True
            # There's no API for this, so we somewhat sneakily are accessing
            # what is intended to be a protected dict.
            # pylint: disable-next=protected-access
            stamps = scanner._discovered_device_timestamps  # type: ignore #noqa

            # In this dict all MAC address keys are upper-cased
            uppermac = self.parent_device_address.upper()
            if uppermac in stamps:
                if self.stamp is None or (stamps[uppermac] is not None and stamps[uppermac] > self.stamp):
                    new_stamp = stamps[uppermac]
                else:
                    # We have no updated advert in this run.
                    new_stamp = None
                    self.stale_update_count += 1
            else:
                # This shouldn't happen, as we shouldn't have got a record
                # of this scanner if it hadn't seen this device.
                _LOGGER.error(
                    "Scanner %s has no stamp for %s - very odd.",
                    scanner.source,
                    self.parent_device_address,
                )
                new_stamp = None
        else:
            # Not a bluetooth_proxy device / remote scanner, but probably a USB Bluetooth adaptor.
            # We don't get advertisement timestamps from bluez, so the stamps in our history
            # won't be terribly accurate, and the advert might actually be rather old.
            # All we can do is check if it has changed and assume it's fresh from that.

            self.scanner_sends_stamps = False
            # If the rssi has changed from last time, consider it "new"
            if self.rssi != scandata.advertisement.rssi:
                # 2024-03-16: We're going to treat it as fresh for now and see how that goes.
                # We can do that because we smooth distances now every update_interval, regardless
                # of when the last advertisement was received, so we shouldn't see bluez trumping
                # proxies with stale adverts. Hopefully.
                # new_stamp = MONOTONIC_TIME() - (ADVERT_FRESHTIME * 4)
                new_stamp = MONOTONIC_TIME()
            else:
                new_stamp = None

        if len(self.hist_stamp) == 0 or new_stamp is not None:
            # this is the first entry or a new one, bring in the new reading
            # and calculate the distance.

            self.rssi = scandata.advertisement.rssi
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

            self.stamp = new_stamp
            self.hist_stamp.insert(0, self.stamp)

        # Safe to update these values regardless of stamps...

        self.adapter: str = scanner.adapter
        self.source: str = scanner.source
        if self.tx_power is not None and scandata.advertisement.tx_power != self.tx_power:
            # Not really an erorr, we just don't account for this happening -
            # I want to know if it does.
            # AJG 2024-01-11: This does happen. Looks like maybe apple devices?
            # Changing from warning to debug to quiet users' logs.
            # Also happens with esphome set with long beacon interval tx, as it alternates
            # between sending some generic advert and the iBeacon advert. ie, it's bogus for that
            # case.
            # _LOGGER.debug(
            #     "Device changed TX-POWER! That was unexpected: %s %sdB",
            #     self.parent_device_address,
            #     scandata.advertisement.tx_power,
            # )
            pass
        self.tx_power = scandata.advertisement.tx_power

        # Track each advertisement element as or if they change.
        for key, data in self.adverts.items():
            new_data = getattr(scandata.advertisement, key, {})
            if len(new_data) > 0:
                if len(data) == 0 or data[0] != new_data:
                    data.insert(0, new_data)
                    # trim to keep size in check
                    del data[HIST_KEEP_COUNT:]

        self.new_stamp = new_stamp

    def _update_raw_distance(self, reading_is_new=True) -> float:
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
        if self.ref_power == 0:
            ref_power = self.options.get(CONF_REF_POWER)
        else:
            ref_power = self.ref_power

        distance = rssi_to_metres(
            self.rssi + self.options.get(CONF_RSSI_OFFSETS, {}).get(self.address, 0),
            ref_power,
            self.options.get(CONF_ATTENUATION),
        )
        self.rssi_distance_raw = distance
        if reading_is_new:
            # Add a new historical reading
            self.hist_distance.insert(0, distance)
            # don't insert into hist_distance_by_interval, that's done by the caller.
        else:
            # We are over-riding readings between cycles. Force the
            # new value in-place.
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

    def set_ref_power(self, value: float):
        """Set a new reference power from the parent device and immediately update distance."""
        # When the user updates the ref_power we want to reflect that change immediately,
        # and not subject it to the normal smoothing algo.
        self.ref_power = value
        return self._update_raw_distance(False)

    def calculate_data(self):
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
        cycle. This is mainly beacuse usb/bluez adaptors seem to flush
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

            self.hist_distance_by_interval = [self.rssi_distance_raw]

        elif new_stamp is None and (self.stamp is None or self.stamp < MONOTONIC_TIME() - DISTANCE_TIMEOUT):
            # DEVICE IS AWAY!
            # Last distance reading is stale, mark device distance as unknown.
            self.rssi_distance = None
            # Clear the smoothing history
            if len(self.hist_distance_by_interval) > 0:
                self.hist_distance_by_interval.clear()

        else:
            # Add the current reading (whether new or old) to
            # a historical log that is evenly spaced by update_interval.

            # Verify the new reading is vaguely sensible. If it isn't, we
            # ignore it by duplicating the last cycle's reading.
            if len(self.hist_stamp) > 1:
                # How far (away) did it travel in how long?
                # we check this reading against the recent readings to find
                # the peak average velocity we are alleged to have reached.
                velo_newdistance = self.hist_distance[0]
                velo_newstamp = self.hist_stamp[0]
                peak_velocity = 0
                # walk through the history of distances/stamps, and find
                # the peak
                delta_t = velo_newstamp - self.hist_stamp[1]
                delta_d = velo_newdistance - self.hist_distance[1]
                if delta_t > 0:
                    peak_velocity = delta_d / delta_t
                # if our initial reading is an approach, we are done here
                if peak_velocity >= 0:
                    for old_distance, hist_stamp in zip(self.hist_distance[2:], self.hist_stamp[2:], strict=False):
                        if hist_stamp is None:
                            continue  # Skip this iteration if hist_stamp[i] is None

                        delta_t = velo_newstamp - hist_stamp
                        if delta_t <= 0:
                            # Additionally, skip if delta_t is zero or negative
                            # to avoid division by zero
                            continue
                        delta_d = velo_newdistance - old_distance

                        velocity = delta_d / delta_t

                        if velocity > peak_velocity:
                            # but on subsequent comparisons we only care if they're faster retreats
                            peak_velocity = velocity
                # we've been through the history and have peak velo retreat, or the most recent
                # approach velo.
                velocity = peak_velocity
            else:
                # There's no history, so no velocity
                velocity = 0

            self.hist_velocity.insert(0, velocity)

            if velocity > self.max_velocity:
                if self.parent_device_address.upper() in self.options.get(CONF_DEVICES, []):
                    _LOGGER.debug(
                        "This sparrow %s flies too fast (%2fm/s), ignoring",
                        self.parent_device_address,
                        velocity,
                    )
                # Discard the bogus reading by duplicating the last.
                self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
            elif len(self.hist_distance_by_interval) == 0:
                self.hist_distance_by_interval = [self.rssi_distance_raw]
            else:
                self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])

            dist_count = len(self.hist_distance_by_interval)
            # trim the log to length
            if self.smoothing_samples < dist_count:
                del self.hist_distance_by_interval[self.smoothing_samples :]
                # Set equal to smoothing samples
                dist_count = self.smoothing_samples

            # Calculate a moving-window average, that only includes
            # historical values if they're "closer" (ie more reliable).
            #
            # This might be improved by weighting the values by age, but
            # already does a fairly reasonable job of hugging the bottom
            # of the noisy rssi data. A better way to control the maximum
            # slope angle (other than increasing bucket count) might be
            # helpful, but probably dependent on use-case.
            #
            dist_total: float = 0
            local_min: float = self.rssi_distance_raw or DISTANCE_INFINITE
            for distance in self.hist_distance_by_interval:
                if distance <= local_min:
                    local_min = distance
                dist_total += local_min

            if dist_total > 0:  # Calculate the minimised-windowed-average
                movavg = dist_total / len(self.hist_distance_by_interval)
            else:  # we have only a single measurement.
                movavg = local_min
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

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices."""
        out = {}
        for var, val in vars(self).items():
            if var in ["options", "parent_device", "scanner_device"]:
                # skip certain vars that we don't want in the dump output.
                continue
            if var == "adverts":
                adout = {}
                for adtype, adarray in val.items():
                    out_adarray = []
                    for ad_data in adarray:
                        if adtype in ["manufacturer_data", "service_data"]:
                            for ad_key, ad_value in ad_data.items():
                                out_adarray.append({ad_key: cast(bytes, ad_value).hex()})
                        else:
                            out_adarray.append(ad_data)
                    adout[adtype] = out_adarray
                out[var] = adout
                continue
            out[var] = val
        return out

    def __repr__(self) -> str:
        """Help debugging by giving it a clear name instead of empty dict."""
        return self.address
