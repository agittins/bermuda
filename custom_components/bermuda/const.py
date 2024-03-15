"""Constants for Bermuda BLE Trilateration."""

# Base component constants
from __future__ import annotations

from typing import Final

NAME = "Bermuda BLE Trilateration"
DOMAIN = "bermuda"
DOMAIN_DATA = f"{DOMAIN}_data"
# Version gets updated by github workflow during release.
# The version in the repository should always be 0.0.0 to reflect
# that the component has been checked out from git, not pulled from
# an officially built release. HACS will use the git tag (or the zip file,
# either way it works).
VERSION = "0.0.0"

ATTRIBUTION = "Data provided by http://jsonplaceholder.typicode.com/"
ISSUE_URL = "https://github.com/agittins/bermuda/issues"

# Icons
ICON = "mdi:format-quote-close"

# Device classes
BINARY_SENSOR_DEVICE_CLASS = "connectivity"

# Platforms
BINARY_SENSOR = "binary_sensor"
SENSOR = "sensor"
SWITCH = "switch"
DEVICE_TRACKER = "device_tracker"
# PLATFORMS = [BINARY_SENSOR, SENSOR, SWITCH]
PLATFORMS = [SENSOR, DEVICE_TRACKER]

# Signal names we are using:
SIGNAL_DEVICE_NEW = f"{DOMAIN}-device-new"

# Beacon-handling constants. Source devices are tracked by MAC-address and are the
# originators of beacon-like data. We then create a "meta-device" for the beacon's
# uuid. Other non-static-mac protocols should use this method as well, by adding their
# own BEACON_ types.
BEACON_NOT_A_BEACON: Final = "not a beacon"  # This device is not any sort of beacon.
BEACON_IBEACON_SOURCE: Final = (
    "beacon source"  # The source-device sending a beacon packet (MAC-tracked)
)
BEACON_IBEACON_DEVICE: Final = (
    "beacon device"  # The meta-device created to track the beacon
)

DOCS = {}

ADVERT_FRESHTIME = 2.5
# If two scanners are battling to "win" a device, the winner can not be more than
# this many seconds older than its opponent. Prevents a stale but very close
# advert from overriding a newer advertisement from a less-close scanner.


HIST_KEEP_COUNT = (
    10  # How many old timestamps, rssi, etc to keep for each device/scanner pairing.
)

# Config entry DATA entries

CONFDATA_SCANNERS = "scanners"
DOCS[CONFDATA_SCANNERS] = "Persisted set of known scanners (proxies)"

# Configuration and options

CONF_DEVICES = "configured_devices"
DOCS[CONF_DEVICES] = "Identifies which bluetooth devices we wish to expose"

CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS = "max_area_radius", 20
DOCS[CONF_MAX_RADIUS] = "For simple area-detection, max radius from receiver"

CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT = "devtracker_nothome_timeout", 30
DOCS[CONF_DEVTRACK_TIMEOUT] = (
    "Timeout in seconds for setting devices as `Not Home` / `Away`."  # fmt: skip
)

CONF_ATTENUATION, DEFAULT_ATTENUATION = "attenuation", 3
DOCS[CONF_ATTENUATION] = "Factor for environmental signal attenuation."
CONF_REF_POWER, DEFAULT_REF_POWER = "ref_power", -55.0
DOCS[CONF_REF_POWER] = "Default RSSI for signal at 1 metre."

CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL = "update_interval", 1.0
DOCS[CONF_UPDATE_INTERVAL] = "How often to update bluetooth stats, in seconds"

# Defaults
DEFAULT_NAME = DOMAIN


STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
