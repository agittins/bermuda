# Bermuda — Architecture

Bermuda is a **calculated** Home Assistant integration: it has no external API.
Its data source is Home Assistant's own passive Bluetooth stack
(`bluetooth_adapters`, `private_ble_device`, `device_tracker`). It turns the BLE
advertisements those scanners ("proxies") receive into per-device distance and
area estimates via RSSI smoothing and a closest-scanner trilateration heuristic.

- **Integration type:** `device` · **IoT class:** `calculated` · single config entry
  (`unique_id == DOMAIN`).
- **Platforms:** `sensor`, `device_tracker`, `number`, `select`.

## Data flow

```
HA Bluetooth stack (passive scanning)
        │  adverts + scanner registry
        ▼
BermudaDataUpdateCoordinator._async_update_data_internal()   (coordinator.py)
   1. gather adverts            → BermudaAdvert per (device, scanner)   (bermuda_advert.py)
   2. update metadevices        → iBeacon / IRK / Private-BLE handling
   3. per-device calculate_data → distance smoothing                    (distance_filter.py)
   4. area refresh              → score-based, mobility-aware arbitration (trilateration.py)
   4a. presence-entity override → triggered HA entity wins area on dist.  (area_entity.py)
   4b. micro-location refine    → sub-area RF fingerprint match           (coordinator_microlocation.py)
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
| `coordinator.py` | The update orchestrator: advert ingest, registry-change handling, redaction, the dump service. Composed with the three mixins below. |
| `coordinator_scanners.py` | Coordinator mixin: HA scanner roster sync + the `scanner_without_area` repair issue. |
| `coordinator_metadevices.py` | Coordinator mixin: iBeacon / Private-BLE metadevice management. |
| `pruning.py` | Stale-device purge (quota + per-address-type TTLs), called from the update cycle. |
| `redaction.py` | MAC/IRK/name redaction engine shared by diagnostics and `dump_devices`. |
| `bermuda_device.py` | `BermudaDevice` — internal state for one tracked device or scanner (address typing, names, area/floor, beacon ids, the ESPresense-style `category` fingerprint, InPlay IN100 `0x0505` telemetry decode). |
| `bermuda_advert.py` | `BermudaAdvert` — one (device, scanner) relationship; advert history + `calculate_data()`. |
| `distance_filter.py` | **Pure** smoothing maths (velocity/anti-teleport filter, minimum-hugging average, MAD). |
| `trilateration.py` | Score-based, mobility-aware area arbitration with adaptive hysteresis + the explicit `Unknown` outcome (`refresh_area_by_min_distance`, `AreaTests`). |
| `area_entity.py` | Presence-entity area overrides: HA entities whose area, while *on*, competes with BLE at a per-entity "virtual distance" (`BermudaAreaEntityManager`), applied as a coordinator post-pass over the BLE result. |
| `location_fingerprints.py` | **Pure** RF-fingerprint engine (`Fingerprint`, `FingerprintMatcher`) + `Store`-backed `FingerprintStore` for sub-area micro-locations. |
| `coordinator_microlocation.py` | Coordinator mixin: per-cycle fingerprint matching with hysteresis, calibration, and the micro-location/config **services** (the MCP-friendly API). |
| `intents.py` | Voice/Assist intents (`BermudaWhereIs`, `BermudaCalibrateLocation`, `BermudaListLocations`) so micro-locations are reachable from MCP. |
| `manufacturers.py` | Bluetooth SIG UUID → manufacturer name loading and opinionated lookup. |
| `bermuda_irk.py` | IRK / resolvable-private-address resolution for Private BLE devices. |
| `entity.py` | `BermudaEntity` / `BermudaGlobalEntity` bases (unique_id, device_info, rate-limiting). |
| `sensor.py` · `number.py` · `device_tracker.py` · `select.py` | Entity platforms (`select.py` = per-device mobility mode). |
| `sensor_entities.py` · `sensor_global.py` | Sensor entity classes: 13 per-device classes, 4 global counters. |
| `options_text.py` | Inline en/fr UI text for the options flow (dynamic markdown outside HA's translation schema). |
| `config_flow.py` | `BermudaFlowHandler` (config) + registers the options flow and the subentry flows. |
| `options_flow.py` | `BermudaOptionsFlowHandler` — menu, sectioned global options, device/category tracking, area-entity wizard. |
| `subentry_flow.py` | Config subentry flows: per-scanner RSSI calibration, and per-device enrolment (name / ref_power / away timeout). |
| `private_enrol.py` | IRK enrolment helper: validate a pasted IRK and drive the `private_ble_device` config flow (shared by the options step and the `enrol_private_device` service). |
| `diagnostics.py` | `async_get_config_entry_diagnostics` (redacted dump + manager diagnostics). |
| `system_health.py` | System Health page info callback (proxy/device counts). |
| `const.py` | All constants (no logic). `util.py` | Pure helpers (mac formatting, rssi→metres, resolvable-address test). |
| `log_spam_less.py` | Rate-limited logging wrapper. |
| `quality_scale.yaml` | Quality Scale self-assessment / roadmap (metadata, not yet declared in the manifest). |

## `unique_id` scheme (do NOT change without migration)

All entity `unique_id`s derive from `BermudaDevice.unique_id` (the normalised MAC).
Suffixes: device_tracker & area sensor = base (no suffix); `_floor`, `_scanner`,
`_rssi`, `_range`, `_area_switch_reason`, `_area_last_seen`, `_ref_power`,
`_micro_location`, `_mobility`; per-scanner
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

Run with the project virtualenv: `.venv/bin/python -m pytest tests/`. Pytest and
coverage are configured in `pyproject.toml` (single source — do not add a
`pytest.ini`, it would silently override that section); the coverage gate enforces
≥ 95 %. The `tests/test_*_characterization.py` files capture the
current behaviour of the experience-tuned BLE algorithms so they can be refactored
safely; `tests/test_unique_id_regression.py` guards entity identity.
