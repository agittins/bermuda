"""General helper utilities for Bermuda."""

from __future__ import annotations

from functools import lru_cache


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
    octet_int = bytes.fromhex(octet)[0]
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
def mac_explode_formats(mac):
    """
    Take a formatted mac address and return the formats
    likely to be found in our device info, adverts etc.
    """
    return [
        mac,
        mac.replace(":", ""),
        mac.replace(":", "-"),
        mac.replace(":", "_"),
        mac.replace(":", "."),
    ]


@lru_cache(1024)
def rssi_to_metres(rssi, ref_power=None, attenuation=None):
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
    if ref_power is None:
        return False
        # ref_power = self.ref_power
    if attenuation is None:
        return False
        # attenuation= self.attenuation

    return 10 ** ((ref_power - rssi) / (10 * attenuation))


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
