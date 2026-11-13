"""Tests for external identities using real Bermuda devices and adverts."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.backends.scanner import AdvertisementData

from custom_components.bermuda.bermuda_advert import BermudaAdvert
from custom_components.bermuda.bermuda_device import BermudaDevice
from custom_components.bermuda.device_tracker import BermudaDeviceTracker
from custom_components.bermuda.entity import BermudaEntity
from custom_components.bermuda.sensor import BermudaSensor, BermudaSensorAreaLastSeen
from custom_components.bermuda.const import (
    ADDR_TYPE_PRIVATE_BLE_DEVICE,
    CONF_ATTENUATION,
    CONF_MAX_VELOCITY,
    CONF_REF_POWER,
    CONF_SMOOTHING_SAMPLES,
)
from custom_components.bermuda.external_identity import (
    BermudaUnavailable,
    ExternalIdentityError,
    ExternalIdentityManager,
    _DATA_KEY,
    async_is_available,
)


class TestExternalIdentity(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.devices = {}
        self.coordinator = MagicMock()
        self.coordinator.devices = self.devices
        self.coordinator.options = {
            CONF_REF_POWER: -59,
            CONF_ATTENUATION: 2.0,
            CONF_MAX_VELOCITY: 3.0,
            CONF_SMOOTHING_SAMPLES: 5,
        }
        self.coordinator._get_or_create_device.side_effect = self.device
        self.owner = SimpleNamespace(domain="example", entry_id="one", async_on_unload=MagicMock())
        self.manager = ExternalIdentityManager(self.coordinator)
        for path, value in [
            ("custom_components.bermuda.external_identity.monotonic_time_coarse", 1000.0),
            ("custom_components.bermuda.bermuda_advert.monotonic_time_coarse", 1000.0),
            ("custom_components.bermuda.bermuda_device.monotonic_time_coarse", 1000.0),
            ("custom_components.bermuda.bermuda_device.ar.async_get", MagicMock()),
            ("custom_components.bermuda.bermuda_device.fr.async_get", MagicMock()),
            ("custom_components.bermuda.external_identity.async_call_later", MagicMock()),
            ("custom_components.bermuda.external_identity.async_dispatcher_send", None),
        ]:
            mock = patch(path, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def device(self, address):
        if address not in self.devices:
            self.devices[address] = BermudaDevice(address, self.coordinator)
        return self.devices[address]

    def advert(self, source, scanner_address, stamp, *, remote=True):
        scanner = MagicMock(spec=BermudaDevice)
        scanner.address = scanner_address
        scanner.name = scanner_address
        scanner.area_id = None
        scanner.area_name = None
        scanner.is_remote_scanner = remote
        scanner.last_seen = 0
        scanner.async_as_scanner_get_stamp.return_value = stamp
        data = AdvertisementData(None, {}, {}, [], -59, -70, ())
        advert = BermudaAdvert(source, data, self.coordinator.options, scanner)
        return advert, scanner, data

    async def test_real_adverts_are_independent_and_exclude_old_history(self):
        handle = self.manager.register(self.owner, "tag", "Tag")
        source = self.device("aa:bb:cc:dd:ee:ff")
        first, scanner1, data = self.advert(source, "11:22:33:44:55:66", 980.0)
        second, scanner2, _ = self.advert(source, "22:22:33:44:55:66", 980.0)
        usb, _, _ = self.advert(source, "33:22:33:44:55:66", 980.0, remote=False)
        source.adverts = {(source.address, a.scanner_address): a for a in (first, second, usb)}
        source_hist = list(first.hist_stamp)
        await handle.async_observe_address(source.address, 990.0)
        target = self.devices[handle.identity_address]
        self.assertFalse(target.adverts)
        scanner1.async_as_scanner_get_stamp.return_value = 995.0
        scanner2.async_as_scanner_get_stamp.return_value = 996.0
        first.update_advertisement(data, scanner1)
        second.update_advertisement(data, scanner2)
        self.manager.update()
        self.assertEqual(len(target.adverts), 2)
        clones = list(target.adverts.values())
        self.assertEqual({a.stamp for a in clones}, {995.0, 996.0})
        self.assertEqual({tuple(a.hist_stamp) for a in clones}, {(995.0,), (996.0,)})
        self.assertNotIn(980.0, clones[0].hist_stamp)
        self.assertEqual(first.hist_stamp[-1:], source_hist)
        self.assertIsNot(clones[0], first)
        self.assertIs(clones[0]._device, target)
        target.calculate_data()
        self.assertTrue(all(a.rssi_distance is not None for a in clones))
        replacement = MagicMock(spec=BermudaDevice)
        replacement.address = scanner1.address
        replacement.name = "Reconnected scanner"
        replacement.area_id = "kitchen"
        replacement.area_name = "Kitchen"
        replacement.is_remote_scanner = True
        replacement.last_seen = 999.0
        replacement.async_as_scanner_get_stamp.return_value = 999.0
        first.update_advertisement(data, replacement)
        self.manager.update()
        clone = target.adverts[(source.address, scanner1.address)]
        self.assertIs(clone.scanner_device, replacement)
        self.assertEqual((clone.name, clone.area_id, clone.area_name), ("Reconnected scanner", "kitchen", "Kitchen"))
        self.assertEqual(clone.hist_stamp, [999.0, 995.0])
        self.assertIn(980.0, first.hist_stamp)
        await handle.async_remove_address(source.address)
        self.assertFalse(target.adverts)
        self.assertIsNone(target.area_advert)
        self.assertEqual(target.last_seen, 0)

    async def test_collisions_invalid_fencing_and_capability(self):
        self.coordinator.hass.data = {_DATA_KEY: self.manager}
        self.assertTrue(async_is_available(self.coordinator.hass))
        handle = self.manager.register(self.owner, "tag", "Tag")
        other = self.manager.register(self.owner, "other", "Other")
        await handle.async_observe_address("aa:bb:cc:dd:ee:ff", 990.0)
        with self.assertRaises(ExternalIdentityError):
            await other.async_observe_address("aa:bb:cc:dd:ee:ff", 990.0)
        for stamp in (989.0, 1001.0, 819.0, float("nan"), True, "990"):
            with self.assertRaises(ExternalIdentityError):
                await handle.async_observe_address("aa:bb:cc:dd:ee:ff", stamp)
        await handle.async_remove_address("aa:bb:cc:dd:ee:ff")
        with self.assertRaises(ExternalIdentityError):
            await handle.async_observe_address("aa:bb:cc:dd:ee:ff", 989.0)
        self.manager.close()
        self.assertFalse(async_is_available(self.coordinator.hass))
        with self.assertRaises(BermudaUnavailable):
            await handle.async_observe_address("aa:bb:cc:dd:ee:ff", 1000.0)

    async def test_registry_identity_and_entity_state_on_removal(self):
        handle = self.manager.register(self.owner, "tag", "Tag")
        entry = SimpleNamespace(options={})
        with (
            patch("custom_components.bermuda.entity.ar.async_get"),
            patch("custom_components.bermuda.entity.dr.async_get"),
        ):
            entity = BermudaEntity(self.coordinator, entry, handle.identity_address)
            sensor = BermudaSensor(self.coordinator, entry, handle.identity_address)
            last_area = BermudaSensorAreaLastSeen(self.coordinator, entry, handle.identity_address)
            tracker = BermudaDeviceTracker(self.coordinator, entry, handle.identity_address)
        self.assertEqual(entity.device_info["identifiers"], {("bermuda", handle.identity_address)})
        self.assertEqual(entity.device_info["connections"], set())
        device = self.devices[handle.identity_address]
        device.area_name = "Office"
        device.area_last_seen = "Office"
        device.zone = "home"
        await handle.async_remove()
        self.assertIsNone(sensor.native_value)
        self.assertIsNone(last_area.native_value)
        self.assertFalse(tracker.is_connected)
        self.assertEqual(tracker.extra_state_attributes, {"scanner": None, "area": None})
        with (
            patch.object(BermudaSensor, "async_added_to_hass", new_callable=AsyncMock),
            patch.object(last_area, "async_get_last_sensor_data", new_callable=AsyncMock) as restore,
        ):
            await last_area.async_added_to_hass()
            restore.assert_not_awaited()
        self.assertIsNone(last_area.native_value)

    async def test_regular_and_private_device_info_unchanged(self):
        entry = SimpleNamespace(options={})
        device = self.device("aa:bb:cc:dd:ee:ff")
        with (
            patch("custom_components.bermuda.entity.ar.async_get"),
            patch("custom_components.bermuda.entity.dr.async_get"),
        ):
            entity = BermudaEntity(self.coordinator, entry, device.address)
        self.assertEqual(entity.device_info["connections"], {("bluetooth", "AA:BB:CC:DD:EE:FF")})
        with patch("custom_components.bermuda.bermuda_device.pble_coordinator.async_get_coordinator"):
            private = self.device("0123456789abcdef0123456789abcdef")
        self.assertEqual(private.address_type, ADDR_TYPE_PRIVATE_BLE_DEVICE)
        with (
            patch("custom_components.bermuda.entity.ar.async_get"),
            patch("custom_components.bermuda.entity.dr.async_get"),
        ):
            private_entity = BermudaEntity(self.coordinator, entry, private.address)
        self.assertEqual(private_entity.device_info["connections"], {("private_ble_device", private.address)})
        self.assertEqual(private_entity.device_info["identifiers"], {("private_ble_device", private.unique_id)})

    async def test_reregistration_and_owner_collision(self):
        handle = self.manager.register(self.owner, "tag", "Tag")
        identity = handle.identity_address
        await handle.async_remove()
        replacement = self.manager.register(self.owner, "tag", "Tag")
        self.assertEqual(replacement.identity_address, identity)
        self.owner.async_on_unload.call_args_list[0].args[0]()
        await replacement.async_remove()
        other_owner = SimpleNamespace(domain="example", entry_id="two", async_on_unload=MagicMock())
        with self.assertRaises(ExternalIdentityError):
            self.manager.register(other_owner, "tag", "Tag")
        with self.assertRaises(ExternalIdentityError):
            self.manager.register(self.owner, "x" * 129, "Tag")

    async def test_expiry_and_owner_unload(self):
        handle = self.manager.register(self.owner, "tag", "Tag")
        await handle.async_observe_address("aa:bb:cc:dd:ee:ff", 990.0)
        with patch("custom_components.bermuda.external_identity.monotonic_time_coarse", return_value=1170.0):
            handle._expire(None)
        self.assertEqual(self.manager.addresses, {})
        self.owner.async_on_unload.call_args.args[0]()
        with self.assertRaises(BermudaUnavailable):
            await handle.async_remove()
