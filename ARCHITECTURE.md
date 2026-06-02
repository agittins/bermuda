# Bermuda — Architecture

Bermuda is a **calculated** Home Assistant integration: it has no external API.
Its data source is Home Assistant's own passive Bluetooth stack
(`bluetooth_adapters`, `private_ble_device`, `device_tracker`). It turns the BLE
advertisements those scanners ("proxies") receive into per-device distance and
area estimates via RSSI smoothing and a closest-scanner trilateration heuristic.

- **Integration type:** `device` · **IoT class:** `calculated` · single config entry
  (`unique_id == DOMAIN`).
- **Platforms:** `sensor`, `device_tracker`, `number`.

## Data flow

```
HA Bluetooth stack (passive scanning)
        │  adverts + scanner registry
        ▼
BermudaDataUpdateCoordinator._async_update_data_internal()   (coordinator.py)
   1. gather adverts            → BermudaAdvert per (device, scanner)   (bermuda_advert.py)
   2. update metadevices        → iBeacon / IRK / Private-BLE handling
   3. per-device calculate_data → distance smoothing                    (distance_filter.py)
   4. area refresh              → closest-scanner race w/ hysteresis     (trilateration.py)
   5. periodic pruning
        │  coordinator.devices[address] state
        ▼
Entities (CoordinatorEntity)  read coordinator.devices, never compute I/O
   sensor.py · device_tracker.py · number.py   (base: entity.py)
```

The coordinator stores all state on `self.devices` (a `dict[address, BermudaDevice]`);
`coordinator.data` is unused (`DataUpdateCoordinator[None]`). Entities are created
dynamically as devices/scanners appear, via the `SIGNAL_DEVICE_NEW` /
`SIGNAL_SCANNERS_CHANGED` dispatcher signals.

## Module map

| File | Role |
|---|---|
| `__init__.py` | Setup/unload of the config entry, `dump_devices` service, device-removal. |
| `coordinator.py` | The update orchestrator: advert ingest, scanner & metadevice management, registry-change handling, pruning, redaction, the dump service. |
| `bermuda_device.py` | `BermudaDevice` — internal state for one tracked device or scanner (address typing, names, area/floor, beacon ids). |
| `bermuda_advert.py` | `BermudaAdvert` — one (device, scanner) relationship; advert history + `calculate_data()`. |
| `distance_filter.py` | **Pure** distance-smoothing maths (velocity/anti-teleport filter, minimum-hugging average). |
| `trilateration.py` | `AreaTests` + the closest-scanner race with hysteresis (`refresh_area_by_min_distance`). |
| `manufacturers.py` | Bluetooth SIG UUID → manufacturer name loading and opinionated lookup. |
| `bermuda_irk.py` | IRK / resolvable-private-address resolution for Private BLE devices. |
| `entity.py` | `BermudaEntity` / `BermudaGlobalEntity` bases (unique_id, device_info, rate-limiting). |
| `sensor.py` · `number.py` · `device_tracker.py` | Entity platforms. |
| `config_flow.py` | `BermudaFlowHandler` (config) + `BermudaOptionsFlowHandler` (options + calibration wizards). |
| `diagnostics.py` | `async_get_config_entry_diagnostics` (redacted dump + manager diagnostics). |
| `const.py` | All constants (no logic). `util.py` | Pure helpers (mac formatting, rssi→metres). |
| `log_spam_less.py` | Rate-limited logging wrapper. |

## `unique_id` scheme (do NOT change without migration)

All entity `unique_id`s derive from `BermudaDevice.unique_id` (the normalised MAC).
Suffixes: device_tracker & area sensor = base (no suffix); `_floor`, `_scanner`,
`_rssi`, `_range`, `_area_switch_reason`, `_area_last_seen`, `_ref_power`; per-scanner
range = `{base}_{scanner.address_wifi_mac or scanner.address}_range` (+ `_range_raw`).
Four global sensors use fixed literals (`BERMUDA_GLOBAL_*`). Scanner devices are
pinned to the ESPHome/Shelly **wifi MAC** (not the BLE MAC). iBeacon ids are
`{uuid}_{major}_{minor}`; IRK ids are the 32-char key. These are frozen by
`tests/test_unique_id_regression.py` and map 1:1 to the `translation_key`s in
`strings.json` / `icons.json`.

## Extension points

- **Add a sensor type:** add a `BermudaSensor` subclass in `sensor.py` with a new
  `_attr_translation_key`, a `unique_id` suffix, and a `strings.json`/`icons.json`
  entry; instantiate it in `async_setup_entry`. Mirror the suffix in the regression test.
- **Add a platform:** create `<platform>.py` with `async_setup_entry`, register it in
  `const.PLATFORMS`, and add its `device_new` dispatcher subscription.
- **Tune the algorithms:** edit the `AREA_*` / smoothing constants in `const.py`;
  `tests/test_distance_smoothing_characterization.py` and
  `tests/test_area_selection_characterization.py` pin the current behaviour.

## Testing

Run with the project virtualenv: `.venv/bin/python -m pytest tests/`. Coverage is
configured in `setup.cfg`. The `tests/test_*_characterization.py` files capture the
current behaviour of the experience-tuned BLE algorithms so they can be refactored
safely; `tests/test_unique_id_regression.py` guards entity identity.
