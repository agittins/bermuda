"""
Tests for the micro-location RF fingerprinting engine and store.

The engine maths (Fingerprint, FingerprintMatcher) is pure Python and tested
directly. FingerprintStore is exercised against Home Assistant's Store via the
``hass`` fixture from pytest-homeassistant-custom-component.
"""

from __future__ import annotations

from custom_components.bermuda.location_fingerprints import (
    Fingerprint,
    FingerprintMatcher,
    FingerprintStore,
)

# ---------------------------------------------------------------------------
# Fingerprint (de)serialisation
# ---------------------------------------------------------------------------


def test_fingerprint_roundtrip():
    """A fingerprint survives a to_dict/from_dict round-trip intact."""
    fingerprint = Fingerprint(
        name="Key hook",
        device_address="aa:bb",
        vector={"s1": 1.0, "s2": 3.0},
        vector_std={"s1": 0.1},
        rssi_vector={"s1": -55.0},
        area_id="kitchen",
        floor_id="ground",
        sample_count=7,
    )
    restored = Fingerprint.from_dict(fingerprint.to_dict())
    assert restored.id == fingerprint.id
    assert restored.name == "Key hook"
    assert restored.device_address == "aa:bb"
    assert restored.vector == {"s1": 1.0, "s2": 3.0}
    assert restored.vector_std == {"s1": 0.1}
    assert restored.rssi_vector == {"s1": -55.0}
    assert restored.area_id == "kitchen"
    assert restored.floor_id == "ground"
    assert restored.sample_count == 7


def test_fingerprint_from_dict_tolerates_missing_keys():
    """from_dict fills sensible defaults and coerces types."""
    fingerprint = Fingerprint.from_dict({"name": "x", "device_address": "aa", "vector": {"s1": "2"}})
    assert fingerprint.vector == {"s1": 2.0}
    assert fingerprint.sample_count == 1
    assert isinstance(fingerprint.id, str) and fingerprint.id


# ---------------------------------------------------------------------------
# FingerprintMatcher
# ---------------------------------------------------------------------------


def _hook() -> Fingerprint:
    return Fingerprint(
        name="Key hook", device_address="d", vector={"s1": 1.0, "s2": 5.0}, vector_std={"s1": 0.1, "s2": 0.1}
    )


def _drawer() -> Fingerprint:
    return Fingerprint(
        name="Drawer", device_address="d", vector={"s1": 5.0, "s2": 1.0}, vector_std={"s1": 0.1, "s2": 0.1}
    )


def test_score_identical_is_near_zero():
    matcher = FingerprintMatcher()
    assert matcher.score_one({"s1": 1.0, "s2": 5.0}, _hook()) < 0.01


def test_score_missing_and_extra_scanner_penalised():
    matcher = FingerprintMatcher()
    fingerprint = _hook()
    baseline = matcher.score_one({"s1": 1.0, "s2": 5.0}, fingerprint)
    missing = matcher.score_one({"s1": 1.0}, fingerprint)  # s2 calibrated but now silent
    extra = matcher.score_one({"s1": 1.0, "s2": 5.0, "s3": 2.0}, fingerprint)  # heard a proxy the spot lacks
    assert missing > baseline
    assert extra > baseline


def test_score_empty_vector_returns_reject_distance():
    matcher = FingerprintMatcher()
    empty = Fingerprint(name="x", device_address="d", vector={})
    assert matcher.score_one({}, empty) == matcher.reject_distance


def test_match_picks_nearest_and_accepts():
    matcher = FingerprintMatcher()
    result = matcher.match({"s1": 1.1, "s2": 4.8}, [_hook(), _drawer()])
    assert result is not None
    assert result.name == "Key hook"
    assert result.accepted is True
    assert 0 < result.confidence <= 1
    assert result.scores[0][1] == "Key hook"  # best-first ordering
    assert result.second_score is not None and result.second_score > result.score


def test_match_ambiguous_is_less_confident_than_clear():
    matcher = FingerprintMatcher()
    ambiguous = matcher.match({"s1": 3.0, "s2": 3.0}, [_hook(), _drawer()])
    clear = matcher.match({"s1": 1.0, "s2": 5.0}, [_hook(), _drawer()])
    assert ambiguous.confidence < clear.confidence


def test_match_far_is_rejected():
    matcher = FingerprintMatcher()
    result = matcher.match({"s1": 40.0, "s2": 40.0}, [_hook()])
    assert result.accepted is False


def test_match_single_candidate_has_no_second():
    matcher = FingerprintMatcher()
    result = matcher.match({"s1": 1.0, "s2": 5.0}, [_hook()])
    assert result.second_score is None
    assert result.accepted is True
    assert result.confidence > 0


def test_match_accepts_on_margin_when_outside_accept_band():
    """A best that's past accept_distance can still win on a clear margin."""
    matcher = FingerprintMatcher(accept_distance=0.5, reject_distance=10.0, min_margin=0.4)
    result = matcher.match({"s1": 1.8, "s2": 5.2}, [_hook(), _drawer()])
    assert result.name == "Key hook"
    assert result.score > matcher.accept_distance  # past the easy band...
    assert result.accepted is True  # ...but the margin carries it


def test_match_empty_inputs_return_none():
    matcher = FingerprintMatcher()
    assert matcher.match({}, [_hook()]) is None
    assert matcher.match({"s1": 1.0}, []) is None


# ---------------------------------------------------------------------------
# FingerprintStore (requires hass for the Store helper)
# ---------------------------------------------------------------------------


async def test_store_load_empty(hass):
    store = FingerprintStore(hass)
    await store.async_load()
    assert store.loaded is True
    assert store.list() == []


async def test_store_add_list_find_rename_remove(hass):
    store = FingerprintStore(hass)
    await store.async_load()
    fingerprint = Fingerprint(name="Key hook", device_address="aa:bb", vector={"s1": 1.0})
    store.add(fingerprint)

    assert store.list("aa:bb") == [fingerprint]
    assert store.list("somewhere-else") == []
    assert store.get(fingerprint.id) is fingerprint
    assert store.find_by_name("aa:bb", "KEY hook") is fingerprint  # case-insensitive
    assert store.find_by_name("aa:bb", "nope") is None

    assert store.rename(fingerprint.id, "Coat hook") is True
    assert fingerprint.name == "Coat hook"
    assert store.rename("does-not-exist", "x") is False

    assert store.remove(fingerprint.id) is True
    assert store.remove(fingerprint.id) is False
    assert store.list() == []


async def test_store_persists_across_reload(hass):
    store = FingerprintStore(hass)
    await store.async_load()
    fingerprint = Fingerprint(
        name="Hook", device_address="aa:bb", vector={"s1": 1.0, "s2": 2.0}, vector_std={"s1": 0.1}
    )
    store.add(fingerprint)
    # Persist immediately (the real store debounces) then load into a fresh store.
    await store._store.async_save(store._data_to_save())  # noqa: SLF001 - test seam

    reloaded = FingerprintStore(hass)
    await reloaded.async_load()
    loaded = reloaded.get(fingerprint.id)
    assert loaded is not None
    assert loaded.name == "Hook"
    assert loaded.vector == {"s1": 1.0, "s2": 2.0}
    assert reloaded.list("aa:bb") == [loaded]


async def test_store_load_skips_malformed_rows(hass):
    store = FingerprintStore(hass)
    await store._store.async_save(  # noqa: SLF001 - seed bad + good rows
        {"fingerprints": [{"bad": "row"}, {"name": "ok", "device_address": "d", "vector": {"s1": 1.0}}]}
    )
    await store.async_load()
    saved = store.list()
    assert len(saved) == 1
    assert saved[0].name == "ok"
