# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
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

### Removed
- Dead code: unused `DOMAIN_DATA` and `DOCS` constants, stale `binary_sensor`/`switch` bytecode, never-enabled verbose area logging, and the legacy flake8/isort config superseded by ruff

### Tests
- Add a `unique_id` regression snapshot pinning every entity `unique_id`, device-registry identity, translation-key mapping and the device-removal suffix handling
- Add characterization tests for distance smoothing and area selection (behaviour frozen before refactor)
- Add a comprehensive test suite across coordinator, config flow, devices, IRK, entities, redaction, manufacturers and helpers — raising coverage from 48% to 92% (29 → 322 tests)

### Docs
- Add `ARCHITECTURE.md`

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
