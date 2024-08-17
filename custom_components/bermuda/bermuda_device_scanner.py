"""
Bermuda's internal representation of a device's scanner entry.

Every bluetooth scanner gets its own BermudaDevice, but this class
is the nested entry that gets attached to each device's `scanners`
dict. It is a sub-set of a 'device' and will have attributes specific
to the combination of the scanner and the device it is reporting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.bluetooth import MONOTONIC_TIME, BluetoothScannerDevice

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

    A BermudaDevice will contain 0 or more of these depending on whether
    it has been "seen" by that scanner.

    Note that details on a scanner itself are BermudaDevice instances
    in their own right.
    """

    def __init__(
        self,
        device_address: str,
        scandata: BluetoothScannerDevice,
        area_id: str,
        options,
        scanner_device: BermudaDevice,
    ) -> None:
        # I am declaring these just to control their order in the dump,
        # which is a bit silly, I suspect.
        self.name: str = scandata.scanner.name
        self.scanner_device_name = scanner_device.name
        self.adapter: str = scandata.scanner.adapter
        self.address = scanner_device.address
        self.source: str = scandata.scanner.source
        self.area_id: str = area_id
        self.parent_device = device_address
        self.options = options
        self.stamp: float | None = 0
        self.new_stamp: float | None = None  # Set when a new advert is loaded from update
        self.hist_stamp = []
        self.rssi: float | None = None
        self.hist_rssi = []
        self.hist_distance = []
        self.hist_distance_by_interval = []  # updated per-interval
        self.hist_interval = []  # WARNING: This is actually "age of ad when we polled"
        self.hist_velocity = []  # Effective velocity versus previous stamped reading
        self.stale_update_count = 0  # How many times we did an update but no new stamps were found.
        self.tx_power: float | None = None
        self.rssi_distance: float | None = None
        self.rssi_distance_raw: float | None = None
        self.adverts: dict[str, bytes] = {}

        # Just pass the rest on to update...
        self.update_advertisement(device_address, scandata, area_id)

    def update_advertisement(self, device_address: str, scandata: BluetoothScannerDevice, area_id: str):
        """
        Update gets called every time we see a new packet or
        every time we do a polled update.

        This method needs to update all the history and tracking data for this
        device+scanner combination. This method only gets called when a given scanner
        claims to have data.
        """
        # In case the scanner has changed it's details since startup:
        self.name: str = scandata.scanner.name
        self.area_id: str = area_id
        new_stamp: float | None = None

        # Only remote scanners log timestamps here (local usb adaptors do not),
        if hasattr(scandata.scanner, "_discovered_device_timestamps"):
            # Found a remote scanner which has timestamp history...
            self.scanner_sends_stamps = True
            # There's no API for this, so we somewhat sneakily are accessing
            # what is intended to be a protected dict.
            # pylint: disable-next=protected-access
            stamps = scandata.scanner._discovered_device_timestamps  # type: ignore #noqa

            # In this dict all MAC address keys are upper-cased
            uppermac = device_address.upper()
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
                    scandata.scanner.source,
                    device_address,
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
            # this is the first entry or a new one...

            self.rssi = scandata.advertisement.rssi
            self.hist_rssi.insert(0, self.rssi)
            self.rssi_distance_raw = rssi_to_metres(
                self.rssi + self.options.get(CONF_RSSI_OFFSETS, {}).get(self.address, 0),
                self.options.get(CONF_REF_POWER),
                self.options.get(CONF_ATTENUATION),
            )
            self.hist_distance.insert(0, self.rssi_distance_raw)

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

        self.adapter: str = scandata.scanner.adapter
        self.source: str = scandata.scanner.source
        if self.tx_power is not None and scandata.advertisement.tx_power != self.tx_power:
            # Not really an erorr, we just don't account for this happening -
            # I want to know if it does.
            # AJG 2024-01-11: This does happen. Looks like maybe apple devices?
            # Changing from warning to debug to quiet users' logs.
            _LOGGER.debug(
                "Device changed TX-POWER! That was unexpected: %s %sdB",
                device_address,
                scandata.advertisement.tx_power,
            )
        self.tx_power = scandata.advertisement.tx_power
        for ad_str, ad_bytes in scandata.advertisement.service_data.items():
            self.adverts[ad_str] = ad_bytes

        self.new_stamp = new_stamp

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

        if new_stamp is not None and self.rssi_distance is None:
            # DEVICE HAS ARRIVED!
            # We have just newly come into range (or we're starting up)
            # accept the new reading as-is.
            self.rssi_distance = self.rssi_distance_raw
            # And ensure the smoothing history gets a fresh start
            self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)
            del self.hist_distance_by_interval[1:]

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
                for i, old_distance in enumerate(self.hist_distance):
                    if i == 0:
                        # (skip the first entry since it's what we're comparing with)
                        continue

                    if self.hist_stamp[i] is None:
                        continue  # Skip this iteration if hist_stamp[i] is None

                    delta_t = velo_newstamp - self.hist_stamp[i]
                    delta_d = velo_newdistance - old_distance
                    if delta_t <= 0:
                        # Additionally, skip if delta_t is zero or negative
                        # to avoid division by zero
                        continue

                    velocity = delta_d / delta_t

                    # Approach velocities are only interesting vs the previous
                    # reading, while retreats need to be sensible over time
                    if i == 1:
                        # on first round we want approach or retreat velocity
                        peak_velocity = velocity
                        if velocity < 0:
                            # if our new reading is an approach, we are done here
                            # (not so for == 0 since it might still be an invalid retreat)
                            break

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

            if velocity > self.options.get(CONF_MAX_VELOCITY):
                if self.parent_device.upper() in self.options.get(CONF_DEVICES, []):
                    _LOGGER.debug(
                        "This sparrow %s flies too fast (%2fm/s), ignoring",
                        self.parent_device,
                        velocity,
                    )
                # Discard the bogus reading by duplicating the last.
                self.hist_distance_by_interval.insert(0, self.hist_distance_by_interval[0])
            else:
                # Looks valid enough, add the current reading to the interval log
                self.hist_distance_by_interval.insert(0, self.rssi_distance_raw)

            # trim the log to length
            del self.hist_distance_by_interval[self.options.get(CONF_SMOOTHING_SAMPLES) :]

            # Calculate a moving-window average, that only includes
            # historical values if their "closer" (ie more reliable).
            #
            # This might be improved by weighting the values by age, but
            # already does a fairly reasonable job of hugging the bottom
            # of the noisy rssi data. A better way to control the maximum
            # slope angle (other than increasing bucket count) might be
            # helpful, but probably dependent on use-case.
            #
            dist_total: float = 0
            dist_count: int = 0
            local_min: float = self.rssi_distance_raw or DISTANCE_INFINITE
            for distance in self.hist_distance_by_interval:
                if distance <= local_min:
                    dist_total += distance
                    local_min = distance
                else:
                    dist_total += local_min
                dist_count += 1

            if dist_count > 0:
                movavg = dist_total / dist_count
            else:
                movavg = local_min

            # The average is only helpful if it's lower than the actual reading.
            if self.rssi_distance_raw is None or movavg < self.rssi_distance_raw:
                self.rssi_distance = movavg
            else:
                self.rssi_distance = self.rssi_distance_raw

        # Trim our history lists
        for histlist in (
            self.hist_distance,
            self.hist_interval,
            self.hist_rssi,
            self.hist_stamp,
            self.hist_velocity,
        ):
            del histlist[HIST_KEEP_COUNT:]

    def to_dict(self):
        """Convert class to serialisable dict for dump_devices."""
        out = {}
        for var, val in vars(self).items():
            if var == "adverts":
                # FIXME: val is overwritten in loop
                val = {}  # noqa
                for uuid, thebytes in self.adverts.items():
                    val[uuid] = thebytes.hex()
            out[var] = val
        return out
