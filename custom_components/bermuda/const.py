"""Constants for Bermuda BLE Triangulation."""
# Base component constants
NAME = "Bermuda BLE Triangulation"
DOMAIN = "bermuda"
DOMAIN_DATA = f"{DOMAIN}_data"
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
#PLATFORMS = [BINARY_SENSOR, SENSOR, SWITCH]
PLATFORMS = [ SENSOR ]

# Configuration and options
CONF_ENABLED = "enabled"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVTRACKERS = "device_trackers"

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
