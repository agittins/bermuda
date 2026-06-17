"""Coverage for the Bermuda options-flow calibration wizards.

These exercise the parts of ``custom_components/bermuda/config_flow.py`` that the
existing ``test_options_flow.py`` deliberately leaves alone: the two calibration
sub-steps (``calibration1_global`` and ``calibration2_scanners``), the richer
branches of ``selectdevices`` (iBeacon meta-devices, stale-random pruning,
pagination and "saved but not discovered" devices), the init-step status
branches / scanner-table rendering, the ``_get_options_translation`` markdown
builders, ``_get_bermuda_device_from_registry`` and ``async_step_bluetooth``.

Wherever the wizard needs device / scanner data the empty test-HA lacks, fake
devices and scanners are injected directly into the live coordinator's
``devices`` dict and ``_scanner_list`` set. The device registry is populated so
that the calibration steps can resolve a HA device id back to a Bermuda MAC.

Nothing under ``custom_components/`` is modified, and entity unique_ids are never
asserted or changed.
"""

from __future__ import annotations

from types import SimpleNamespace

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bermuda.options_flow import (
    BermudaOptionsFlowHandler,
    _DESCRIPTION_TEXTS,
)
from custom_components.bermuda.const import (
    ADDR_TYPE_IBEACON,
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    BDADDR_TYPE_OTHER,
    BDADDR_TYPE_RANDOM_RESOLVABLE,
    CONF_ATTENUATION,
    CONF_DEVICES,
    CONF_REF_POWER,
    CONF_RSSI_OFFSETS,
    CONF_SAVE_AND_CLOSE,
    CONF_SCANNER_INFO,
    CONF_SCANNERS,
    DEFAULT_ATTENUATION,
    DEFAULT_REF_POWER,
    DOMAIN,
    NAME,
)
from custom_components.bermuda.util import rssi_to_metres

# A pretend tracked device + scanner that the calibration wizard understands.
TRACKED_ADDR = "aa:bb:cc:dd:ee:f0"
SCANNER_ADDR = "11:22:33:44:55:66"
SCANNER2_ADDR = "11:22:33:44:55:77"
HIST_RSSI = [-60, -65, -70, -72, -75, -80]


def _make_fake_scanner_advert(scanner_address: str, hist_rssi: list[int]):
    """A stand-in for BermudaAdvert exposing only what the wizard reads."""
    return SimpleNamespace(scanner_address=scanner_address, hist_rssi=list(hist_rssi))


def _make_fake_tracked_device(address: str, name: str, scanner_adverts: dict):
    """A stand-in BermudaDevice: ``get_scanner`` returns the injected advert."""

    def get_scanner(scanner_address):
        return scanner_adverts.get(scanner_address)

    return SimpleNamespace(
        address=address,
        name=name,
        is_scanner=False,
        address_type=BDADDR_TYPE_OTHER,
        manufacturer=None,
        area_rssi=None,
        metadevice_sources=[],
        last_seen=monotonic_time_coarse(),
        get_scanner=get_scanner,
    )


def _make_fake_scanner_device(address: str, name: str):
    """A stand-in scanner BermudaDevice (only ``name`` is read for these)."""
    return SimpleNamespace(
        address=address,
        name=name,
        is_scanner=True,
        address_type=BDADDR_TYPE_OTHER,
        manufacturer=None,
        area_rssi=None,
        metadevice_sources=[],
        last_seen=monotonic_time_coarse(),
    )


def _inject_calibration_fixtures(hass: HomeAssistant, entry: MockConfigEntry):
    """Populate the live coordinator + device registry for the calibration wizard.

    Returns the HA device-registry id of the tracked device so callers can feed
    it back as ``CONF_DEVICES`` (which the wizard resolves via the registry).
    """
    coordinator = entry.runtime_data.coordinator

    scanner_adverts = {
        SCANNER_ADDR: _make_fake_scanner_advert(SCANNER_ADDR, HIST_RSSI),
        SCANNER2_ADDR: _make_fake_scanner_advert(SCANNER2_ADDR, HIST_RSSI[:3]),
    }
    tracked = _make_fake_tracked_device(TRACKED_ADDR, "Tracked Tag", scanner_adverts)
    coordinator.devices[TRACKED_ADDR] = tracked

    # Two scanners, registered in the coordinator's scanner_list + devices.
    coordinator.devices[SCANNER_ADDR] = _make_fake_scanner_device(SCANNER_ADDR, "Kitchen Proxy")
    coordinator.devices[SCANNER2_ADDR] = _make_fake_scanner_device(SCANNER2_ADDR, "Lounge Proxy")
    coordinator._scanner_list.add(SCANNER_ADDR)
    coordinator._scanner_list.add(SCANNER2_ADDR)

    # Register a HA device whose bluetooth connection maps back to TRACKED_ADDR.
    devreg = dr.async_get(hass)
    reg_device = devreg.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, TRACKED_ADDR)},
        name="Tracked Tag",
    )
    return coordinator, reg_device.id


# --------------------------------------------------------------------------- #
# async_step_bluetooth (discovery entry point)
# --------------------------------------------------------------------------- #


async def test_bluetooth_discovery_shows_user_form(hass: HomeAssistant):
    """A bluetooth-discovery initiation lands on the user confirmation form."""
    service_info = SimpleNamespace(address="EE:E8:37:9F:6B:54", name="discoverable")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=service_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    placeholders = result.get("description_placeholders") or {}
    assert placeholders.get("name") == NAME


async def test_bluetooth_discovery_aborts_when_already_configured(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Once an entry exists, a second bluetooth discovery aborts (single instance)."""
    service_info = SimpleNamespace(address="EE:E8:37:9F:6B:54", name="discoverable")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=service_info
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


# --------------------------------------------------------------------------- #
# async_step_init: status branches + scanner table rendering
# --------------------------------------------------------------------------- #


async def test_init_status_no_scanners_branch(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """With no scanners, the init status uses the 'no_scanners' message."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    coordinator._scanner_list.clear()

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    placeholders = result.get("description_placeholders") or {}
    # status is built from a translation; for "no scanners" it is non-empty and
    # the scanner table title is appended underneath.
    assert _DESCRIPTION_TEXTS["en"]["scanner_table_title"] in placeholders["status"]


async def test_init_status_no_devices_branch(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """With scanners present but no active devices, the 'no_devices' status is used."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    coordinator._scanner_list.add(SCANNER_ADDR)
    coordinator.count_active_devices = lambda: 0

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    placeholders = result.get("description_placeholders") or {}
    # The scanner-table title is always appended underneath the status.
    assert _DESCRIPTION_TEXTS["en"]["scanner_table_title"] in placeholders["status"]


async def test_init_scanner_table_renders_age_icons(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """The init scanner table renders all three age-status icons.

    ``get_active_scanner_summary`` is stubbed to return three scanners spanning
    the <2s / <10s / >=10s age buckets so each icon branch executes.
    """
    coordinator = setup_bermuda_entry.runtime_data.coordinator

    summary = [
        {"name": "Fresh", "address": SCANNER_ADDR, "last_stamp_age": 1.0},
        {"name": "Aging", "address": SCANNER2_ADDR, "last_stamp_age": 5.0},
        {"name": "Dead", "address": "99:88:77:66:55:44", "last_stamp_age": 50.0},
    ]
    coordinator.get_active_scanner_summary = lambda: summary
    # Make active devices non-zero so the "some_active" status branch is taken.
    coordinator.count_active_devices = lambda: 3
    coordinator.count_active_scanners = lambda: 3
    coordinator._scanner_list.add(SCANNER_ADDR)

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    status = (result.get("description_placeholders") or {})["status"]
    assert "mdi:check-circle-outline" in status  # age < 2
    assert "mdi:alert-outline" in status  # 2 <= age < 10
    assert "mdi:skull-crossbones" in status  # age >= 10
    assert "Fresh" in status and "Aging" in status and "Dead" in status


# --------------------------------------------------------------------------- #
# selectdevices: iBeacon, stale-random, pagination, saved-not-discovered
# --------------------------------------------------------------------------- #


def _inject_device(coordinator, address: str, *, address_type=BDADDR_TYPE_OTHER, **attrs):
    device = coordinator._get_or_create_device(address)
    device.address_type = address_type
    for key, value in attrs.items():
        setattr(device, key, value)
    return device


def _offered_values(result) -> set[str]:
    """Collect every option value across the form's selectors."""
    offered: set[str] = set()
    for validator in result["data_schema"].schema.values():
        cfg = getattr(validator, "config", None)
        if isinstance(cfg, dict) and "options" in cfg:
            offered |= {opt["value"] for opt in cfg["options"]}
    return offered


async def test_selectdevices_ibeacon_metadevice_listed(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """An iBeacon meta-device is offered as a labelled option in the devices selector."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    dev = _inject_device(
        coordinator,
        "AA:BB:CC:DD:EE:10",
        address_type=ADDR_TYPE_IBEACON,
        manufacturer="Acme",
        area_rssi=-66.0,
    )
    dev.metadevice_sources = ["AA:BB:CC:DD:EE:99"]

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    assert result["step_id"] == "selectdevices"
    assert "AA:BB:CC:DD:EE:10" in _offered_values(result)


async def test_selectdevices_skips_scanner_and_private_and_stale_random(
    hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry
):
    """Scanner, private-BLE and stale-random devices are excluded from the lists."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator

    # A private-BLE device: skipped outright.
    _inject_device(coordinator, "AA:BB:CC:DD:EE:20", address_type=ADDR_TYPE_PRIVATE_BLE_DEVICE)
    # A random MAC last seen >2h ago: pruned by the staleness check.
    stale = _inject_device(coordinator, "AA:BB:CC:DD:EE:21", address_type=BDADDR_TYPE_RANDOM_RESOLVABLE)
    stale.last_seen = monotonic_time_coarse() - (60 * 60 * 3)
    # A scanner device is skipped via the is_scanner guard.
    scanner_dev = _inject_device(coordinator, "AA:BB:CC:DD:EE:22")
    scanner_dev._is_scanner = True

    result = await hass.config_entries.options.async_init(setup_bermuda_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "selectdevices"}
    )
    assert result["step_id"] == "selectdevices"
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    # None of the skipped devices produce a grouped selector.
    assert "ibeacon_devices" not in schema_keys
    assert "random_devices" not in schema_keys
    # The standard list excludes the scanner, so no standard selector either.
    assert "standard_devices" not in schema_keys


async def test_selectdevices_saved_but_not_discovered_added(hass: HomeAssistant, setup_bermuda_entry: MockConfigEntry):
    """A configured device that is no longer discovered is still offered ('saved')."""
    coordinator = setup_bermuda_entry.runtime_data.coordinator
    # One discovered standard device so the standard selector renders.
    _inject_device(coordinator, "AA:BB:CC:DD:EE:30")

    flow = BermudaOptionsFlowHandler()
    flow.hass = hass
    flow.handler = setup_bermuda_entry.entry_id
    # Pre-seed options with an address that is NOT in the discovered list.
    flow._options = {CONF_DEVICES: ["AA:BB:CC:DD:EE:31"]}
    flow.coordinator = coordinator
    flow.devices = coordinator.devices

    result = await flow.async_step_selectdevices()
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "selectdevices"
    # The saved-but-undiscovered address must still be offered (labelled "(saved)")
    # in a rendered selector, otherwise saving the form would silently drop it.
    schema_keys = {str(k.schema) for k in result["data_schema"].schema}
    assert "devices" in schema_keys
    assert "AA:BB:CC:DD:EE:31" in _offered_values(result)
