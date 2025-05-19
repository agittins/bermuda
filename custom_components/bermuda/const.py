"""Constants for Bermuda BLE Trilateration."""

# Base component constants
from __future__ import annotations

import logging
from enum import Enum
from typing import Final

from homeassistant.const import Platform

from .log_spam_less import BermudaLogSpamLess

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

# Issue/repair translation keys. If you change these you MUST also update the key in the translations/xx.json files.
REPAIR_SCANNER_WITHOUT_AREA = "scanner_without_area"

# Device classes
BINARY_SENSOR_DEVICE_CLASS = "connectivity"

# Platforms
PLATFORMS = [
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.NUMBER,
    # Platform.BUTTON,
    # Platform.SWITCH,
    # Platform.BINARY_SENSOR
]

# Should probably retreive this from the component, but it's in "DOMAIN" *shrug*
DOMAIN_PRIVATE_BLE_DEVICE = "private_ble_device"

# Signal names we are using:
SIGNAL_DEVICE_NEW = f"{DOMAIN}-device-new"
SIGNAL_SCANNERS_CHANGED = f"{DOMAIN}-scanners-changed"

UPDATE_INTERVAL = 1.05  # Seconds between bluetooth data processing cycles
# Note: this is separate from the CONF_UPDATE_INTERVAL which allows the
# user to indicate how often sensors should update. We need to check bluetooth
# stats often to get good responsiveness for beacon approaches and to make
# the smoothing algo's easier. But sensor updates should bear in mind how
# much data it generates for databases and browser traffic.

LOGSPAM_INTERVAL = 22
# Some warnings, like not having an area assigned to a scanner, are important for
# users to see and act on, but we don't want to spam them on every update. This
# value in seconds is how long we wait between emitting a particular error message
# when encountering it - primarily for our update loop.

DISTANCE_TIMEOUT = 30  # seconds to wait before marking a sensor distance measurement
# as unknown/none/stale/away. Separate from device_tracker.
DISTANCE_INFINITE = 999  # arbitrary distance for infinite/unknown rssi range

AREA_MAX_AD_AGE: Final = max(DISTANCE_TIMEOUT / 3, UPDATE_INTERVAL * 2)
# Adverts older than this can not win an area contest.

# Beacon-handling constants. Source devices are tracked by MAC-address and are the
# originators of beacon-like data. We then create a "meta-device" for the beacon's
# uuid. Other non-static-mac protocols should use this method as well, by adding their
# own BEACON_ types.
METADEVICE_TYPE_IBEACON_SOURCE: Final = "beacon source"  # The source-device sending a beacon packet (MAC-tracked)
METADEVICE_IBEACON_DEVICE: Final = "beacon device"  # The meta-device created to track the beacon
METADEVICE_TYPE_PRIVATE_BLE_SOURCE: Final = "private_ble_src"  # current (random) MAC of a private ble device
METADEVICE_PRIVATE_BLE_DEVICE: Final = "private_ble_device"  # meta-device create to track private ble device

METADEVICE_SOURCETYPES: Final = {METADEVICE_TYPE_IBEACON_SOURCE, METADEVICE_TYPE_PRIVATE_BLE_SOURCE}
METADEVICE_DEVICETYPES: Final = {METADEVICE_IBEACON_DEVICE, METADEVICE_PRIVATE_BLE_DEVICE}

# Bluetooth Device Address Type - classify MAC addresses
BDADDR_TYPE_UNKNOWN: Final = "bd_addr_type_unknown"  # uninitialised
BDADDR_TYPE_OTHER: Final = "bd_addr_other"  # Default 48bit MAC
BDADDR_TYPE_RANDOM_RESOLVABLE: Final = "bd_addr_random_resolvable"
BDADDR_TYPE_RANDOM_UNRESOLVABLE: Final = "bd_addr_random_unresolvable"
BDADDR_TYPE_RANDOM_STATIC: Final = "bd_addr_random_static"
BDADDR_TYPE_NOT_MAC48: Final = "bd_addr_not_mac48"
# Non-bluetooth address types - for our metadevice entries
ADDR_TYPE_IBEACON: Final = "addr_type_ibeacon"
ADDR_TYPE_PRIVATE_BLE_DEVICE: Final = "addr_type_private_ble_device"


class IrkTypes(Enum):
    """
    Enum of IRK Types.

    Values used to mark if a device matches a known IRK, or is yet to be checked.
    Since IRK's are 16-bytes (128bits) long and the spec requires that IRKs be validated
    against https://doi.org/10.6028/NIST.SP.800-22r1a we can be confident that our use of
    some short ints must not be capable of matching any valid IRK as they would fail
    most of the required tests (such as longest run of ones)

    If the irk field does not match any of these values, then it is a valid IRK.
    """

    ADRESS_NOT_EVALUATED = bytes.fromhex("0000")  # default
    NOT_RESOLVABLE_ADDRESS = bytes.fromhex("0001")  # address is not a resolvable private address.
    NO_KNOWN_IRK_MATCH = bytes.fromhex("0002")  # none of the known keys match this address.

    @classmethod
    def unresolved(cls) -> list[bytes]:
        return [bytes(k.value) for k in IrkTypes.__members__.values()]


# Device entry pruning. Letting the gathered list of devices grow forever makes the
# processing loop slower. It doesn't seem to have as much impact on memory, but it
# would certainly use up more, and gets worse in high "traffic" areas.
#
# Pruning ignores tracked devices (ie, ones we keep sensors for) and scanners. It also
# avoids pruning the most recent IRK for a known private device.
#
# IRK devices typically change their MAC every 15 minutes, so 96 addresses/day.
#
# Accoring to the backend comments, BlueZ times out adverts at 180 seconds, and HA
# expires adverts at 195 seconds to avoid churning.
#
PRUNE_MAX_COUNT = 1000  # How many device entries to allow at maximum
PRUNE_TIME_INTERVAL = 180  # Every 3m, prune stale devices
# ### Note about timeouts: Bluez and HABT cache for 180 or 195 seconds. Setting
# timeouts below that may result in prune/create/prune churn, but as long as
# we only re-create *fresh* devices the risk is low.
PRUNE_TIME_DEFAULT = 86400  # Max age of regular device entries (1day)
PRUNE_TIME_UNKNOWN_IRK = 240  # Resolvable Private addresses change often, prune regularly.
# see Bluetooth Core Spec, Vol3, Part C, Appendix A, Table A.1: Defined GAP timers
PRUNE_TIME_KNOWN_IRK: Final[int] = 16 * 60  # spec "recommends" 15 min max address age. Round up to 16 :-)

SAVEOUT_COOLDOWN = 10  # seconds to delay before re-trying config entry save.

DOCS = {}


HIST_KEEP_COUNT = 10  # How many old timestamps, rssi, etc to keep for each device/scanner pairing.

# Config entry DATA entries

CONFDATA_SCANNERS = "scanners"
DOCS[CONFDATA_SCANNERS] = "Persisted set of known scanners (proxies)"

# Configuration and options

CONF_DEVICES = "configured_devices"
DOCS[CONF_DEVICES] = "Identifies which bluetooth devices we wish to expose"

CONF_SCANNERS = "configured_scanners"


CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS = "max_area_radius", 20
DOCS[CONF_MAX_RADIUS] = "For simple area-detection, max radius from receiver"

CONF_MAX_VELOCITY, DEFAULT_MAX_VELOCITY = "max_velocity", 3
DOCS[CONF_MAX_VELOCITY] = (
    "In metres per second - ignore readings that imply movement away faster than",
    "this limit. 3m/s (10km/h) is good.",  # fmt: skip
)

CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT = "devtracker_nothome_timeout", 30
DOCS[CONF_DEVTRACK_TIMEOUT] = "Timeout in seconds for setting devices as `Not Home` / `Away`."  # fmt: skip

CONF_ATTENUATION, DEFAULT_ATTENUATION = "attenuation", 3
DOCS[CONF_ATTENUATION] = "Factor for environmental signal attenuation."
CONF_REF_POWER, DEFAULT_REF_POWER = "ref_power", -55.0
DOCS[CONF_REF_POWER] = "Default RSSI for signal at 1 metre."

CONF_SAVE_AND_CLOSE = "save_and_close"
CONF_SCANNER_INFO = "scanner_info"
CONF_RSSI_OFFSETS = "rssi_offsets"

CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL = "update_interval", 10
DOCS[CONF_UPDATE_INTERVAL] = (
    "Maximum time between sensor updates in seconds. Smaller intervals",
    "means more data, bigger database.",  # fmt: skip
)

CONF_SMOOTHING_SAMPLES, DEFAULT_SMOOTHING_SAMPLES = "smoothing_samples", 20
DOCS[CONF_SMOOTHING_SAMPLES] = (
    "How many samples to average distance smoothing. Bigger numbers"
    " make for slower distance increases. 10 or 20 seems good."
)

# Defaults
DEFAULT_NAME = DOMAIN

_LOGGER: logging.Logger = logging.getLogger(__package__)
_LOGGER_SPAM_LESS = BermudaLogSpamLess(_LOGGER, LOGSPAM_INTERVAL)


STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
