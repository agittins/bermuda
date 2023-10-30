"""Constants for Bermuda BLE Trilateration."""
# Base component constants
NAME = "Bermuda BLE Trilateration"
DOMAIN = "bermuda"
DOMAIN_DATA = f"{DOMAIN}_data"
VERSION = "0.3.1"

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
# PLATFORMS = [BINARY_SENSOR, SENSOR, SWITCH]
PLATFORMS = [SENSOR]

DOCS = {}

ADVERT_FRESHTIME = 2.5
# If two scanners are battling to "win" a device, the winner can not be more than
# this many seconds older than its opponent. Prevents a stale but very close
# advert from overriding a newer advertisement from a less-close scanner.


HIST_KEEP_COUNT = (
    10  # How many old timestamps, rssi, etc to keep for each device/scanner pairing.
)

# Configuration and options

CONF_DEVICES = "configured_devices"
DOCS[CONF_DEVICES] = "Identifies which bluetooth devices we wish to expose"

CONF_MAX_RADIUS, DEFAULT_MAX_RADIUS = "max_area_radius", 20
DOCS[CONF_MAX_RADIUS] = "For simple area-detection, max radius from receiver"

CONF_DEVTRACK_TIMEOUT, DEFAULT_DEVTRACK_TIMEOUT = "devtracker_nothome_timeout", 30
DOCS[
    CONF_DEVTRACK_TIMEOUT
] = "Timeout in seconds for setting devices as `Not Home` / `Away`."

CONF_ATTENUATION, DEFAULT_ATTENUATION = "attenuation", 3
DOCS[CONF_ATTENUATION] = "Factor for environmental signal attenuation."
CONF_REF_POWER, DEFAULT_REF_POWER = "ref_power", -55.0
DOCS[CONF_REF_POWER] = "Default RSSI for signal at 1 metre."

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
