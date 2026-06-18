# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-06-18

### Security
- Personal device names (user-set names and BLE `local_name`s such as "Jan's iPhone") are now redacted in the `dump_devices` output and diagnostics — the address-only redactor never washed them
- A bare 32-hex run (an IRK key or iBeacon UUID with no separators) is now masked by a generic fallback, so an IRK can no longer slip through even when it is not a top-level device entry
- IRK key material is truncated in the remaining DEBUG/ERROR logs (resolver error paths and private-BLE callback registration), so pasting logs into an issue no longer discloses the full key
- A centralized logging filter now masks any standalone 32-hex secret (e.g. a full IRK) in *every* log record — a safety net behind the targeted truncations above (ported from philbert/ble-trilateration)

### Added
- **Enrol a private device by IRK.** A guided front-end to Home Assistant's `private_ble_device` integration so an iPhone, Apple Watch or other privacy device can be tracked by its Identity Resolving Key — exposed both as an options-menu step (**Configure → Enrol a private device (IRK)**, with an in-form guide on extracting the IRK from the iOS Keychain "Remote IRK" field or Android `bt_config.conf`) and a `bermuda.enrol_private_device` service (`irk` + optional `name`). The pasted key (hex or iOS base64) is validated and handed to `private_ble_device`, which stays the single source of truth for the 16-byte / in-range checks; Bermuda then tracks the resulting metadevice automatically. This is the architecturally honest analogue of ESPresense's pairing-based enrolment: Bermuda's Bluetooth proxies are passive scanners and cannot bond to harvest an IRK the way ESPresense firmware does, so the HA-native path (resolve a known IRK) is used instead. New `private_enrol.py`.
- **ESPresense-style device tracking.** Every device now carries a `category` fingerprint (iBeacon / IRK / vendor — Apple, Garmin, Samsung, Google, Amazfit, Microsoft / named / random / public), so you can **track whole categories at once** ("track all iBeacons", "track all named devices") via a multi-select in the device step, plus an **exclusion denylist** — instead of ticking each address (a device is tracked if explicitly selected *or* its category is selected, *and* it is not excluded). Per-device **enrolment** is a new config subentry: pick a discovered device and give it its own name, reference power (rssi@1m) and away timeout — the subentry is authoritative at startup (the per-device `ref_power` number entity stays for live, in-session tweaks), the name slots into `make_name` below an explicit Home Assistant rename, and the timeout overrides the global one per device. New `BermudaDevice.category` + curated `VENDOR_CATEGORIES`; `CONF_TRACK_CATEGORIES` / `CONF_EXCLUDE_DEVICES`; a `device` config subentry flow. Inspired by ESPresense.
- **Mobility-aware area resolution** (ported/adapted from philbert/ble-trilateration): each tracked device gets a `Mobility Type` select (moving/stationary) that tunes the RSSI conditioning and area-switch hysteresis. RSSI is now robustly conditioned (MAD outlier clamp + mobility-aware EMA) before distance is computed, area arbitration is score-based with an adaptive fast/slow-lane hysteresis (a clear winner switches at once; a marginal one must persist via dwell or a recent majority), and when the evidence is weak or sustained-ambiguous the area is reported as the explicit **`Unknown`** (distinct from `not_home`). New `select.py` platform; the area-selection internals moved to a stateful, fully-tested rewrite of `trilateration.py` (`distance_filter.median_abs_deviation` extracted as a pure helper). Thanks @philbert.
- **Micro-locations (sub-area RF fingerprinting) + an MCP-friendly service/intent API** (ported and adapted from belikh/bermuda2): name and calibrate spots by example ("my keys are on the key hook") and Bermuda reports `Key hook` vs `Sidetable drawer` as a `Micro-location` sensor and an attribute on the Area sensor — finer than the nearest-scanner Area, and purely additive (the Area logic is unchanged). Reachable without the options menu via services (`bermuda.calibrate_location`, `where_is`, `list_locations`, `remove_location`, `rename_location`, `track_device`, `untrack_device`, `set_global_calibration`, `set_scanner_offset`, `get_config`) and voice/Assist intents (`BermudaCalibrateLocation`, `BermudaWhereIs`, `BermudaListLocations`), so MCP clients, automations and the conversation agent can configure and query it. New `location_fingerprints.py` (pure engine + `Store`-backed persistence), `coordinator_microlocation.py` (coordinator mixin), `intents.py`; full FR translations. Thanks @belikh and @agittins.
- **Area presence-entity overrides** (ported/adapted from knoop7/bermuda-intent): configure Home Assistant entities (motion, occupancy, contact, ...) whose area, while the entity is *on*, competes with BLE at a per-entity "virtual distance" — a triggered presence sensor can reinforce or override the BLE-derived area (smaller distance = stronger override), and can also resolve an otherwise `Unknown`/`not_home` device. Configured via a new two-step **Area Presence Entities** options wizard (entities + global default, then per-entity distances grouped by area); applied as a post-pass over the score-based area result, so it composes with the mobility-aware arbitration. New `area_entity.py` manager + `device.apply_area_override()`. Thanks @knoop7. (knoop7's AI intents — including an arbitrary-code-execution `BermudaExecute` — were deliberately **not** ported, on security grounds.)
- **InPlay IN100 / DFRobot Fermion telemetry** (ported/adapted from kamilzierke/bermuda): when a tracked device broadcasts the InPlay manufacturer payload (company id `0x0505`), Bermuda decodes its 5-byte telemetry block and exposes **supply voltage (VCC)**, **temperature** and an **ADC voltage** as sensors. Unlike the source fork, the three sensors are **created only for devices actually detected as IN100** (gated by a dedicated `SIGNAL_DEVICE_IN100_NEW` dispatch + a `create_in100_done` flag), so ordinary BLE devices are never cluttered with empty telemetry entities. New `BermudaDevice._parse_in100_telemetry` + `BermudaSensorIn100{Vcc,Temperature,AdcVoltage}` with EN/FR names. Thanks @kamilzierke.
- `system_health.py`: surfaces proxy/device counts on the System Health page
- Per-scanner distance sensors now expose `available` and go unavailable when their proxy leaves the roster
- The nearest-scanner sensor exposes the scanner's Home Assistant `entity_id` as a `scanner_entity_id` attribute, so automations can reach the scanner device's labels/attributes (ported from upstream #374, thanks @ashabala)
- `quality_scale.yaml`: a self-assessment / roadmap toward the Home Assistant Quality Scale (manifest tier declaration deferred pending `hassfest` validation)

### Fixed
- Area selection no longer raises `ValueError` (`min([])`) and aborts the whole cycle when the incumbent scanner has a populated distance but an empty interval history (reachable via metadevice `set_ref_power` propagation)
- Demoting a scanner that is not in the roster can no longer raise `KeyError` and abort the scanner rebuild (`set.discard` instead of `set.remove`)
- Stale-device pruning subtracts metadevice "keepers" *before* computing the quota shortfall, so the over-quota backstop prunes enough instead of silently under-pruning exactly when it matters (busy area / BLE-MAC churn)
- BLE address-type classification uses the correct top-two-bits test (`>> 2 == 0b01`), shared between the IRK manager and the device model; static-random addresses (first nibble C–F) are no longer misclassified as resolvable-format
- Global options are bounds-checked (`vol.Range`): attenuation can no longer be 0 (division by zero in the distance model), and update interval / smoothing samples / velocity / radius can no longer be zero or negative and destabilise the loops
- The options device selector tolerates a malformed (hand-edited) config carrying a non-string entry instead of crashing the flow
- A single not-yet-ready Bluetooth proxy no longer blocks per-scanner distance entities for every other proxy — only that proxy is skipped until it reports its wifi MAC (unique_ids unchanged)
- Corrected a copy-pasted log message in `device_tracker_created` and several `%2f` → `%.2f` format strings
- The area/floor/scanner text sensors no longer carry an inert custom `device_class`, so their state changes now appear in the Home Assistant logbook/history (ported from upstream #753, thanks @mdrobnak)

### Changed
- **Config/options flow overhaul.** The options menu is now fully translated (it was a dict of hardcoded English labels); the global options form is grouped into collapsible **sections** (distance model / device tracking / RSSI smoothing); device selection is a single searchable selector instead of ~200 lines of hand-rolled pagination + text filter; and **per-scanner RSSI calibration moved to a config subentry flow** (add/edit/remove one offset per scanner) with a `v1 → v2` entry migration (`options[rssi_offsets]` → subentries, mirrored back at runtime so the advert read-path is unchanged). Removed the `_ugly_token_hack` and the hand-built calibration result tables. `options_flow.py` 772 → 357 lines. FR + EN fully translated; el/nb/nl/pt fall back to English for the reworked strings
- Area arbitration is now RSSI-score-based with adaptive, mobility-aware hysteresis (replacing the "closest scanner wins + percentage-difference" race), and can report the explicit `Unknown` area — see the mobility-aware entry under Added. This changes which area a device reports in marginal/ambiguous cases; the `unique_id`s of existing entities are unchanged
- Point integration metadata (manifest `codeowners`/`documentation`/`issue_tracker`, the startup banner and the config-flow help URLs) at the `foXaCe/bermuda` fork
- The Area Switch Reason sensor now shows the concise switch reason as its state, with the full `AreaTests` dump moved to a `diagnostic` attribute (ported from upstream #753, thanks @mdrobnak)

### Removed
- Dead commented-out code (a `button_created` plumbing stub and its unused `create_button_done` flag, an obsolete `async_call_update_entry`, and stale entity-property blocks in `number.py`/`entity.py`/`sensor.py`) and a stale `pylint: disable`
- Fixed the `ADRESS_NOT_EVALUATED` typo (now `ADDRESS_NOT_EVALUATED`)

### Tests
- 116 new tests (325 → 441) covering: human-name and 32-hex IRK redaction, the `min([])` guard, the pruning quota/keeper ordering, the address-type bit logic, `scanner_list_del` idempotency, global-option bounds validation, `system_health`, per-scanner `available`, the `scanner_entity_id` attribute, the area-sensor logbook fix, the concise area-switch reason, the full micro-location engine/services/intents/sensors, the area presence-entity manager + override logic + two-step options wizard, and the IN100 telemetry decode (incl. signed temperature, short/stale payloads) + gated sensor creation (unit + end-to-end). Coverage rises to 94% (`location_fingerprints.py` 100%; `area_entity.py` 98%; `options_flow.py` 99%; `sensor.py`/`sensor_entities.py` 98%; `pruning.py` 72% → 82%). Also pinned the trilateration characterization tests' clock so a slow suite can't age their adverts out (a pre-existing flake).

## [0.9.4] - 2026-06-03

### Fixed
- Per-call rate-limit interval no longer mutates the entity's configured default (a one-off interval used to persist for every later read)
- Remove a dead OUI→manufacturer lookup that could never match (the Bluetooth SIG tables are keyed by 16-bit company IDs, not 24-bit OUI prefixes)
- **Security:** IRK cryptographic key material no longer leaks into diagnostics — keys are shown as stable labels (`IRK_0`…) instead of their raw hex value (the MAC-only redactor never washed them)
- Update bookkeeping stamps now advance even if an update cycle raises, so a failed cycle no longer makes every incoming advert spawn a redundant background update or defeat the skip-already-processed optimisation; the advert-triggered path now records `last_update_success` too
- Options-flow scanner calibration no longer crashes the flow when the free-form scanner-info editor has a missing key or a non-numeric value (defaults safely)
- Stale-device pruning no longer drops a user-tracked device or a scanner just because it is a stale, non-most-recent metadevice source, nor a source that another metadevice still keeps as its most-recent
- Parse the large (~192KB) manufacturer YAML off the event loop (`async_add_executor_job`) instead of blocking it
- Restored `area_last_seen` no longer turns a `None` into the literal string `"None"`
- `peak_retreat_velocity` guards against `None` entries in the distance/stamp history (consistent with the rest of the smoothing module)
- `get_scanner` picks the most-recent matching advert deterministically even when a matched advert has a zero/None stamp
- Options flow: a configured device that is no longer being discovered is now still offered (labelled "(saved)") in the device selector, instead of silently disappearing — saving the form previously dropped it from the tracked devices
- IRK: a non-resolvable-format address is now classified `NOT_RESOLVABLE_ADDRESS` instead of being masked as `NO_KNOWN_IRK_MATCH` by the post-loop fallback (avoids needlessly re-testing it)
- Remove an unused `_timestamp_cutoff` variable in the advert-gather path

### Changed
- Type the coordinator as `DataUpdateCoordinator[None]`
- Isolate the private bluetooth-manager API behind a single guarded method and switch scanner enumeration to the public `bluetooth.async_current_scanners()` API, so a future Home Assistant change degrades diagnostics gracefully instead of breaking integration load
- Extract the distance-smoothing maths into a pure, unit-tested `distance_filter` module
- Extract the area-selection (trilateration) race into a dedicated `trilateration` module
- Extract Bluetooth manufacturer-id loading/lookup into a `manufacturers` module
- Extract MAC redaction into a `redaction` module and stale-device pruning into a `pruning` module
- Move the options/calibration flow into `options_flow.py` (config_flow.py 792 → 83 lines)
- Split `sensor.py` (580 → 171 lines) into `sensor_entities.py` (per-device) and `sensor_global.py` (hub-wide), keeping `sensor.py` as the platform entry point
- Decompose the coordinator god-object from ~1684 to ~1230 lines across the above modules
- Centralise experience-tuned constants (area hysteresis thresholds, distance-smoothing timing) in `const.py`
- Fix the diagnostics module docstring (was "WLED")
- French translations: typographic polish (non-breaking spaces before units and double punctuation, em dashes), reference the submit button as « Valider » (its actual HA French label), `AREA`→`ZONE` wording consistency, and a faux-ami fix (`retourner`→`renvoyer`)
- Log the startup banner once per Home Assistant process instead of on every entry setup/reload

### Removed
- Dead code: unused `DOMAIN_DATA` and `DOCS` constants, stale `binary_sensor`/`switch` bytecode, never-enabled verbose area logging, and the legacy flake8/isort config superseded by ruff

### Tests
- Add a `unique_id` regression snapshot pinning every entity `unique_id`, device-registry identity, translation-key mapping and the device-removal suffix handling
- Add characterization tests for distance smoothing and area selection (behaviour frozen before refactor)
- Add a comprehensive test suite across coordinator, config flow, devices, IRK, entities, redaction, manufacturers and helpers — raising coverage from 48% to 93% (29 → 325 tests)

### Docs
- Add `ARCHITECTURE.md`

### Dependencies
- Bump the Home Assistant requirement to >= 2026.5.4
- Bump ruff to 0.15.15, black to 26.5.1, pip to >= 26.1.2
- Bump release-drafter to 7.3.1 and softprops/action-gh-release to 3.0.0

## [0.9.3] - 2026-05-31

### Fixed
- Prevent `KeyError` aborting the update cycle when the device pruner queued the same address twice (a stale IRK source matching the per-device pruning criteria)

### Changed
- Release workflow now triggers on `v*` tag pushes and publishes the GitHub release itself, so tagging is enough to ship

## [0.9.2] - 2026-05-17

### Fixed
- Propagate naming, manufacturer and beacon fields from source devices to metadevices (the previous dict-iteration path never ran)
- Reference-power calibration changes now refresh sensors immediately (the cache-busting window was never triggered)
- Per-scanner range sensors now expose their `area_id` / `area_name` attributes
- `redact_data` fully redacts strings containing multiple addresses, and keeps the original casing when nothing is redacted
- Guard against a missing RSSI value in the advert raw-distance calculation
- Add the missing `not_loaded` translation raised by the `dump_devices` service when no entry is loaded
- Advert-triggered updates now run as a config-entry-bound background task, cancelled cleanly on unload
- Align the `services.yaml` `configured_devices` default with the code

### Changed
- Migrate the options flow off the deprecated `OptionsFlowWithConfigEntry`
- Update bundled GitHub Actions and Python dependencies (Dependabot)
- Align CI with Home Assistant 2026.5 (Python 3.14, `serialx`, `aioesphomeapi`, stricter test teardown)
- Remove unused stub platforms and dead code

## [0.9.1] - 2026-02-13

### Fixed
- Prevent event loop blocking in update cycle by making `_async_update_data_internal()` truly async with yield points (`asyncio.sleep(0)`) between heavy processing phases and every 20 devices in the calculate loop
- `async_handle_advert` callback now schedules update via `async_create_task` instead of blocking the event loop
- Add cycle duration monitoring (warning >500ms, error >2s) to detect slow update cycles

## [0.9.0] - 2026-02-13

### Added
- Complete translation system with French (fr) support for all config/options flow strings
- `strings.json` as translation source of truth
- `icons.json` for entity and service icon translations (Gold Quality Scale rule)
- Dynamic UI text translations (EN/FR) for options flow tables and descriptions

### Changed
- Replace hardcoded English text in options flow with translation system
- Move dynamic description texts to inline dict (hassfest schema compliance)
- Use `{github_url}` placeholder instead of hardcoded URL in config step
- Add `PARALLEL_UPDATES = 0` to sensor, device_tracker, and number platforms (Silver rule)
- Use `@dataclass(slots=True)` on BermudaData for memory optimization
- Add French words to codespell ignore list
- Clean up excessive debug logging across codebase

### Fixed
- `sensor.py`: Use `_attr_translation_key` instead of `self.name` for i18n-safe comparisons
- `config_flow.py`: Use proper error dict pattern for scanner record errors
- `test_bermuda_advert.py`: Fix fixture options using wrong key names for constants
- CI: Remove invalid `options.description_text` section from translation files (hassfest)

### Dependencies
- Bump actions/checkout from 4 to 6
- Bump actions/setup-python from 5.6.0 to 6.2.0
- Bump softprops/action-gh-release from 2.3.2 to 2.5.0
- Bump release-drafter/release-drafter from 6.1.0 to 6.2.0
- Bump sigstore/gh-action-sigstore-python from 3.0.1 to 3.2.0
- Bump ruff from 0.12.7 to 0.15.0
- Bump colorlog from 6.9.0 to 6.10.1
- Bump pre-commit from 4.2.0 to 4.5.1
- Bump black from 25.1.0 to 26.1.0
- Bump pip from 25.2 to 26.0.1
- Bump reorder-python-imports in workflows
