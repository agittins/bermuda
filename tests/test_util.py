"""Test util.py in Bermuda."""

from __future__ import annotations

# from homeassistant.core import HomeAssistant

from math import floor

from bleach import clean

from custom_components.bermuda import util


def test_mac_math_offset():
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", 2) == "aa:bb:cc:dd:ee:f1"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ef", -3) == "aa:bb:cc:dd:ee:ec"
    assert util.mac_math_offset("aa:bb:cc:dd:ee:ff", 2) is None
    assert util.mac_math_offset("clearly_not:a-mac_address", 2) == None
    assert util.mac_math_offset(None, 4) == None


def test_mac_norm():
    assert util.mac_norm("AA:bb:CC:88:Ff:00") == "aa:bb:cc:88:ff:00"
    assert util.mac_norm("Not_exactly-a-MAC:address") == "not_exactly-a-mac:address"
    assert util.mac_norm("aa_bb_CC_dd_ee_ff") == "aa:bb:cc:dd:ee:ff"
    assert util.mac_norm("aa-77-CC-dd-ee-ff") == "aa:77:cc:dd:ee:ff"


def test_mac_explode_formats():
    ex = util.mac_explode_formats("aa:bb:cc:77:ee:ff")
    assert "aa:bb:cc:77:ee:ff" in ex
    assert "aa-bb-cc-77-ee-ff" in ex
    for e in ex:
        assert len(e) in [12, 17]


def test_mac_redact():
    assert util.mac_redact("aa:bb:cc:77:ee:ff", "tEstMe") == "aa::tEstMe::ff"
    assert util.mac_redact("howdy::doody::friend", "PLEASENOE") == "ho::PLEASENOE::nd"


def test_rssi_to_metres():
    assert floor(util.rssi_to_metres(-50, -20, 2)) == 31
    assert floor(util.rssi_to_metres(-80, -20, 2)) == 1000


def test_clean_charbuf():
    assert util.clean_charbuf("a Normal string.") == "a Normal string."
    assert util.clean_charbuf("Broken\000String\000Fixed\000\000\000") == "Broken"
