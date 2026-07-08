"""Insertion-time type checking at the storage write boundary.

The SDK's KV layer is intentionally untyped (Redis-style `value: Any`). LakebaseKVStore
closes that gap on `set()`: a value under a key whose prefix maps to an SDK model is
validated against that model BEFORE it lands. These are hermetic — no DB: validation
runs before any pool access, so a malformed object raises ValidationError, while a
valid one passes validation and only then trips "not connected".
"""
import pytest
from pydantic import ValidationError

from seller_mcp.kvstore import KEY_MODEL, LakebaseKVStore

# minimal valid media_kit.Package (required: package_id, name, layer, base_price, floor_price)
GOOD_PACKAGE = {
    "package_id": "pkg-x", "name": "X", "layer": "curated",
    "base_price": 10.0, "floor_price": 5.0,
}


@pytest.fixture
def store() -> LakebaseKVStore:
    return LakebaseKVStore("postgresql://unused/db")  # never connected


def test_key_model_only_covers_sdk_models():
    assert set(KEY_MODEL) == {"product", "package", "quote", "deal"}


async def test_set_rejects_malformed_modeled_value(store):
    with pytest.raises(ValidationError):
        await store.set("package:bad", {"package_id": "x"})  # missing name/layer/prices


async def test_set_accepts_valid_value_then_reaches_storage(store):
    # validation passes -> proceeds to super().set() -> RuntimeError (no pool) proves
    # the value cleared insertion-time validation.
    with pytest.raises(RuntimeError):
        await store.set("package:ok", GOOD_PACKAGE)


async def test_unmodeled_key_skips_validation(store):
    # no SDK model for `order:` -> no validation, straight to storage (RuntimeError).
    with pytest.raises(RuntimeError):
        await store.set("order:1", {"whatever": True})
