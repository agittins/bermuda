"""Test Bermuda BLE Trilateration setup process."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda import (
    SERVICE_DUMP_DEVICES,
    SERVICE_ENROL_PRIVATE_DEVICE,
    async_migrate_entry,
    async_remove_config_entry_device,
)
from custom_components.bermuda.const import (
    CONF_IRK,
    CONF_RSSI_OFFSET,
    CONF_RSSI_OFFSETS,
    CONF_SCANNER,
    DOMAIN,
    SUBENTRY_TYPE_CALIBRATION,
    IrkTypes,
)
from custom_components.bermuda.coordinator import BermudaDataUpdateCoordinator

from .const import MOCK_CONFIG
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry

# from pytest_homeassistant_custom_component.common import AsyncMock


# We can pass fixtures as defined in conftest.py to tell pytest to use the fixture
# for a given test. We can also leverage fixtures and mocks that are available in
# Home Assistant using the pytest_homeassistant_custom_component plugin.
# Assertions allow you to verify that the return value of whatever is on the left
# side of the assertion matches with the right side.
async def test_setup_unload_and_reload_entry(
    hass: HomeAssistant, bypass_get_data, setup_bermuda_entry: MockConfigEntry
):
    """Test entry setup and unload."""

    # Reload the entry and assert that the data from above is still there
    assert await hass.config_entries.async_reload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.LOADED

    assert set(IrkTypes.unresolved()) == {
        IrkTypes.ADDRESS_NOT_EVALUATED.value,
        IrkTypes.NO_KNOWN_IRK_MATCH.value,
        IrkTypes.NOT_RESOLVABLE_ADDRESS.value,
    }

    # Unload the entry and verify that the data has been removed
    assert await hass.config_entries.async_unload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.NOT_LOADED


async def test_setup_entry_exception(hass, error_on_get_data):
    """Test ConfigEntryNotReady when API raises an exception during entry setup."""
    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")

    assert config_entry is not None

    # In this case we are testing the condition where async_setup_entry raises
    # ConfigEntryNotReady using the `error_on_get_data` fixture which simulates
    # an error.

    # Hmmm... this doesn't seem to be how this works. The super's _async_refresh might
    # handle exceptions, in which it then sets self.last_update_status, which is what
    # async_setup_entry checks in order to raise ConfigEntryNotReady, but I don't think
    # anything will "catch" our over-ridded async_refresh's exception.
    #  with pytest.raises(ConfigEntryNotReady):
    #     assert await async_setup_entry(hass, config_entry)


# --------------------------------------------------------------------------- #
# dump_devices service
# --------------------------------------------------------------------------- #


async def test_dump_devices_service_returns_dict(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """The dump_devices service returns a dict, keyed by device address."""
    result = await hass.services.async_call(DOMAIN, SERVICE_DUMP_DEVICES, {}, blocking=True, return_response=True)
    assert isinstance(result, dict)
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    assert set(result.keys()) == set(coordinator.devices.keys())


async def test_dump_devices_service_with_params(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """The addresses/configured_devices/redact params are accepted and handled."""
    result = await hass.services.async_call(
        DOMAIN,
        SERVICE_DUMP_DEVICES,
        {"addresses": "AA:BB:CC:DD:EE:FF", "configured_devices": True, "redact": True},
        blocking=True,
        return_response=True,
    )
    assert isinstance(result, dict)


async def test_dump_devices_service_raises_when_not_loaded(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """With no loaded config entries, the service raises ServiceValidationError."""
    assert await hass.config_entries.async_unload(setup_bermuda_entry.entry_id)
    assert setup_bermuda_entry.state == ConfigEntryState.NOT_LOADED

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_DUMP_DEVICES, {}, blocking=True, return_response=True)


# --------------------------------------------------------------------------- #
# enrol_private_device service
# --------------------------------------------------------------------------- #


async def test_enrol_private_service_success(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """A successful enrolment (empty error string) does not raise."""
    with patch("custom_components.bermuda.async_enrol_private_device", return_value=""):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ENROL_PRIVATE_DEVICE,
            {CONF_IRK: "0123456789abcdef0123456789abcdef"},
            blocking=True,
        )


async def test_enrol_private_service_error_raises(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """A non-empty error string from async_enrol_private_device raises ServiceValidationError."""
    with (
        patch("custom_components.bermuda.async_enrol_private_device", return_value="irk_not_valid"),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ENROL_PRIVATE_DEVICE,
            {CONF_IRK: "not-a-valid-irk"},
            blocking=True,
        )


# --------------------------------------------------------------------------- #
# async_migrate_entry: duplicate-subentry skip branch
# --------------------------------------------------------------------------- #


async def test_migrate_entry_skips_existing_calibration_subentry(hass: HomeAssistant):
    """A scanner that already has a calibration subentry is not duplicated on migration."""
    entry = MockConfigEntry(domain=DOMAIN, version=1, options={CONF_RSSI_OFFSETS: {"AA:BB": 3.0}})
    entry.add_to_hass(hass)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={CONF_SCANNER: "AA:BB", CONF_RSSI_OFFSET: 9.0},
            subentry_type=SUBENTRY_TYPE_CALIBRATION,
            title="AA:BB",
            unique_id="AA:BB",
        ),
    )

    assert await async_migrate_entry(hass, entry)

    subs = [se for se in entry.subentries.values() if se.subentry_type == SUBENTRY_TYPE_CALIBRATION]
    assert len(subs) == 1
    # The pre-existing subentry's data is untouched (no duplicate/overwrite).
    assert subs[0].data[CONF_RSSI_OFFSET] == 9.0


# --------------------------------------------------------------------------- #
# async_remove_config_entry_device: KeyError defensive branch
# --------------------------------------------------------------------------- #


class _AlwaysContainsButRaisesMapping:
    """A mapping whose __contains__ says True but __getitem__ always raises KeyError.

    Used only to deterministically exercise the defensive `except KeyError` branch
    in async_remove_config_entry_device, since the real coordinator.devices dict
    cannot be made to disagree with itself between the containment check and the
    lookup under normal (non-racy) conditions.
    """

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: object):
        raise KeyError(key)


async def test_remove_config_entry_device_keyerror_branch_logs_and_returns_true(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
):
    """The defensive except-KeyError branch logs a warning but still allows removal."""
    coordinator = SimpleNamespace(devices=_AlwaysContainsButRaisesMapping())
    config_entry = SimpleNamespace(runtime_data=SimpleNamespace(coordinator=coordinator))
    device_entry = SimpleNamespace(identifiers={(DOMAIN, "aa:bb:cc:dd:ee:ff")}, name="Test Device")

    with caplog.at_level("WARNING"):
        result = await async_remove_config_entry_device(hass, config_entry, device_entry)

    assert result is True
    assert "Failed to locate device entry for aa:bb:cc:dd:ee:ff" in caplog.text
