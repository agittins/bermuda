"""Constants for Bermuda BLE Trilateration tests."""

from __future__ import annotations

import custom_components.bermuda.const

# from custom_components.bermuda.const import CONF_DEVICES
# from custom_components.bermuda.const import CONF_MAX_RADIUS


MOCK_CONFIG = {
    custom_components.bermuda.const.CONF_MAX_RADIUS: 20.0,
    custom_components.bermuda.const.CONF_MAX_VELOCITY: 3.0,
    custom_components.bermuda.const.CONF_DEVTRACK_TIMEOUT: 30,
    custom_components.bermuda.const.CONF_UPDATE_INTERVAL: 10.0,
    custom_components.bermuda.const.CONF_SMOOTHING_SAMPLES: 20,
    custom_components.bermuda.const.CONF_ATTENUATION: 3.0,
    custom_components.bermuda.const.CONF_REF_POWER: -55.0,
    custom_components.bermuda.const.CONF_DEVICES: ["EE:E8:37:9F:6B:54"],
}

MOCK_CONFIG = {}
