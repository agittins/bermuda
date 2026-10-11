"""General helper utilities for Bermuda."""

from __future__ import annotations

import math
from functools import lru_cache

from .const import DEFAULT_ATTENUATION, DEFAULT_REF_POWER, DISTANCE_INFINITE


@lru_cache(64)
def mac_math_offset(mac, offset=0) -> str | None:
    """
    Perform addition/subtraction on a MAC address.

    With a MAC address in xx:xx:xx:xx:xx:xx format,
    add the offset (which may be negative) to the
    last octet, and return the full new MAC.
    If the resulting octet is outside of 00-FF then
    the function returns None.
    """
    if mac is None:
        return None
    octet = mac[-2:]
    try:
        octet_int = bytes.fromhex(octet)[0]
    except ValueError:
        return None
    if 0 <= (octet_new := octet_int + offset) <= 255:
        return f"{mac[:-3]}:{(octet_new):02x}"
    return None


@lru_cache(1024)
def mac_norm(mac: str) -> str:
    """
    Format the mac address string for entry into dev reg.

    What is returned is always lowercased, regardless of
    detected form.
    If mac is an identifiable MAC-address, it's returned
    in the xx:xx:xx:xx:xx:xx form.

    This is copied from the HA device_registry's
    format_mac, but with a bigger lru cache and some
    tweaks, since we're often dealing with many addresses.
    """
    to_test = mac

    if len(to_test) == 17:
        if to_test.count(":") == 5:
            return to_test.lower()
        if to_test.count("-") == 5:
            return to_test.replace("-", ":").lower()
        if to_test.count("_") == 5:
            return to_test.replace("_", ":").lower()

    elif len(to_test) == 14 and to_test.count(".") == 2:
        to_test = to_test.replace(".", "")

    if len(to_test) == 12:
        # no : included
        return ":".join(to_test.lower()[i : i + 2] for i in range(0, 12, 2))

    # Not sure how formatted, return original
    return mac.lower()


@lru_cache(2048)
def mac_explode_formats(mac: str) -> set[str]:
    """
    Take a formatted mac address and return the formats
    likely to be found in our device info, adverts etc
    by replacing ":" with each of "", "-", "_", ".".
    """
    altmacs = set()
    altmacs.add(mac)
    for newsep in ["", "-", "_", "."]:
        altmacs.add(mac.replace(":", newsep))
    return altmacs


def mac_redact(mac: str, tag: str | None = None) -> str:
    """Remove the centre octets of a MAC and optionally replace with a tag."""
    if tag is None:
        tag = ":"
    return f"{mac[:2]}::{tag}::{mac[-2:]}"


def rssi_to_metres(rssi, ref_power=None, attenuation=None) -> float:
    """
    Convert instant rssi value to a distance in metres.

    Based on the information from
    https://mdpi-res.com/d_attachment/applsci/applsci-10-02003/article_deploy/applsci-10-02003.pdf?version=1584265508

    attenuation:    a factor representing environmental attenuation
                    along the path. Will vary by humidity, terrain etc.
    ref_power:      db. measured rssi when at 1m distance from rx. The will
                    be affected by both receiver sensitivity and transmitter
                    calibration, antenna design and orientation etc.
    """
    # Every caller stores the result as a distance (its history, the area
    # election, the calibration tables), so this always returns one. A
    # sentinel would be read as a number - False is 0 m, the nearest scanner
    # there is - and None breaks the velocity arithmetic. The inputs are
    # checked here, before the cache: an unhashable stored option (a list)
    # would make the cache itself raise, and the cache would key True and 1.0
    # together although they are read differently.
    if not usable_number(ref_power):
        # Missing or NaN (an option saved before the forms checked it): the
        # default, rather than a NaN distance in every history.
        ref_power = DEFAULT_REF_POWER
    if not usable_number(attenuation) or attenuation <= 0:
        # Zero would divide by zero (and abort the whole update cycle); a
        # negative or NaN factor gives nonsense distances.
        attenuation = DEFAULT_ATTENUATION
    if not usable_number(rssi):
        # No signal to go on: as far as Bermuda ever reports.
        return float(DISTANCE_INFINITE)
    return _distance(float(rssi), float(ref_power), float(attenuation))


@lru_cache(1024)
def _distance(rssi: float, ref_power: float, attenuation: float) -> float:
    """rssi_to_metres for checked, finite floats (attenuation above 0)."""
    try:
        distance = 10 ** ((ref_power - rssi) / (10 * attenuation))
    except OverflowError:
        # A tiny attenuation (0.001, saved before the forms checked it) can
        # push the power past a float on a weak signal.
        return float(DISTANCE_INFINITE)
    return distance if math.isfinite(distance) else float(DISTANCE_INFINITE)


def usable_number(value) -> bool:
    """
    Whether ``value`` is a finite number rssi_to_metres can use as it is.

    Not a bool, not a numeric string, and not an int too large for a float
    (math.isfinite raises OverflowError on one). Shared with the options forms,
    so a form shows a stored value the way the distance maths reads it.
    """
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@lru_cache(256)
def clean_charbuf(instring: str | None) -> str:
    """
    Some people writing C on bluetooth devices seem to
    get confused between char arrays, strings and such. This
    function takes a potentially dodgy charbuf from a bluetooth
    device and cleans it of leading/trailing cruft
    and returns what's left, up to the first null, if any.

    If given None it returns an empty string.
    Characters trimmed are space, tab, CR, LF, NUL.
    """
    if instring is not None:
        return instring.strip(" \t\r\n\x00").split("\0")[0]
    return ""
