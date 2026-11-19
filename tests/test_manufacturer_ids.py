"""Tests for loading the Bluetooth SIG manufacturer name files."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from custom_components.bermuda import coordinator as coordinator_module
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

COMPONENT_DIR = Path(coordinator_module.__file__).parent
DATA_DIR = COMPONENT_DIR / "manufacturer_identification"


@pytest.fixture(name="skip_yaml_data_load", autouse=True)
def skip_yaml_data_load():
    """Override the autouse fixture in conftest: these tests run the real loader."""
    yield


def _coordinator(hass):
    """Lightweight stand-in for the coordinator, used as ``self`` for the real method."""
    # The loader finds the files under <config>/custom_components/bermuda/, so point the
    # test instance's config dir at the checkout that holds custom_components/.
    hass.config.config_dir = str(COMPONENT_DIR.parents[1])
    return SimpleNamespace(hass=hass, member_uuids={}, company_uuids={}, _waitingfor_load_manufacturer_ids=True)


async def test_load_manufacturer_ids(hass):
    """The loaded mappings match a plain safe_load of the bundled files."""
    coordinator = _coordinator(hass)

    await BermudaDataUpdateCoordinator.async_load_manufacturer_ids(coordinator)

    with (DATA_DIR / "member_uuids.yaml").open(encoding="utf-8") as f:
        expected_members = {m["uuid"]: m["name"] for m in yaml.safe_load(f)["uuids"]}
    with (DATA_DIR / "company_identifiers.yaml").open(encoding="utf-8") as f:
        expected_companies = {c["value"]: c["name"] for c in yaml.safe_load(f)["company_identifiers"]}

    assert coordinator.member_uuids == expected_members
    assert coordinator.company_uuids == expected_companies
    assert coordinator.company_uuids[76] == "Apple, Inc."
    assert coordinator._waitingfor_load_manufacturer_ids is False


async def test_load_manufacturer_ids_parses_off_the_event_loop(hass, monkeypatch):
    """Parsing ~220KB of yaml takes hundreds of ms, so it must not run on the event loop."""
    threads = []
    original = yaml.load

    def _recording_load(*args, **kwargs):
        threads.append(threading.current_thread())
        return original(*args, **kwargs)

    # yaml.safe_load() also goes through yaml.load(), so this sees either style of call.
    monkeypatch.setattr(yaml, "load", _recording_load)

    await BermudaDataUpdateCoordinator.async_load_manufacturer_ids(_coordinator(hass))

    assert len(threads) == 2
    assert all(thread.ident != hass.loop_thread_id for thread in threads)


async def test_load_manufacturer_ids_clears_waiting_flag_on_error(hass, monkeypatch):
    """A failure reading the (optional) files still clears the waiting flag."""

    def _broken_load(*args, **kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(yaml, "load", _broken_load)
    coordinator = _coordinator(hass)

    with pytest.raises(OSError, match="unreadable"):
        await BermudaDataUpdateCoordinator.async_load_manufacturer_ids(coordinator)

    assert coordinator._waitingfor_load_manufacturer_ids is False
