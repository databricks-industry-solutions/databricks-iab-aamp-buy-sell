"""Registry tests.

(1) Hermetic insertion-time type checking at the kv write boundary (no DB —
    validation runs before any pool access), mirroring the seller.
(2) The full registry flow over the SDK's in-memory SQLite backend, proving we use
    AgentRegistryService faithfully: register → list/get → verify (trust→tier) →
    block → remove. The SQLite backend exercises the SDK code paths with no Lakebase.
"""
import pytest
from pydantic import ValidationError

from ad_seller.registry import AgentRegistryService
from ad_seller.storage.sqlite_backend import SQLiteBackend

from registry_mcp.config import Settings
from registry_mcp.kvstore import KEY_MODEL, LakebaseKVStore
from registry_mcp.service import RegistryService

GOOD_CARD = {
    "name": "Seller", "description": "d", "url": "https://seller.example",
    "provider": {"name": "Seller"},
}


# ---- hermetic kv type-check (no DB) --------------------------------------
@pytest.fixture
def store() -> LakebaseKVStore:
    return LakebaseKVStore("postgresql://unused/db")  # never connected


def test_key_model_only_covers_agent():
    assert set(KEY_MODEL) == {"agent"}


async def test_set_rejects_malformed_agent(store):
    with pytest.raises(ValidationError):
        await store.set("agent:bad", {"agent_id": "x"})  # missing agent_card


async def test_unmodeled_key_skips_validation(store):
    # the url index holds a plain agent-id string — no model → straight to storage.
    with pytest.raises(RuntimeError):
        await store.set("agent_url_index:abc", "agent-1")


# ---- registry flow over SQLite (hermetic, real SDK code paths) -----------
@pytest.fixture
async def svc():
    storage = SQLiteBackend(":memory:")
    await storage.connect()
    registry = AgentRegistryService(storage)
    service = RegistryService(storage, registry, Settings())
    yield service
    await service.close()


async def test_register_list_get(svc):
    ra = await svc.register(GOOD_CARD, agent_type="seller", trust_status="registered")
    assert ra["agent_type"] == "seller" and ra["trust_status"] == "registered"
    agents = await svc.list_agents(agent_type="seller")
    assert len(agents) == 1
    got = await svc.get_agent(ra["agent_id"])
    assert got["agent_card"]["url"] == "https://seller.example"


async def test_register_is_idempotent_by_url(svc):
    a = await svc.register(GOOD_CARD, agent_type="seller", trust_status="registered")
    b = await svc.register(GOOD_CARD, agent_type="seller", trust_status="approved")
    assert a["agent_id"] == b["agent_id"]                 # same URL → updated, not duplicated
    assert len(await svc.list_agents()) == 1


async def test_verify_registered_maps_to_seat(svc):
    await svc.register(GOOD_CARD, agent_type="seller", trust_status="registered")
    v = await svc.verify("https://seller.example")
    assert v["trust_status"] == "registered"
    assert v["max_access_tier"] == "seat"      # TRUST_TO_TIER_MAP[REGISTERED]
    assert v["is_blocked"] is False


async def test_block_then_verify_is_blocked(svc):
    ra = await svc.register(GOOD_CARD, agent_type="seller", trust_status="registered")
    await svc.update_trust(ra["agent_id"], trust_status="blocked")
    v = await svc.verify("https://seller.example")
    assert v["is_blocked"] is True
    assert v["max_access_tier"] is None        # blocked → no access


async def test_remove(svc):
    ra = await svc.register(GOOD_CARD)
    assert await svc.remove(ra["agent_id"]) is True
    assert await svc.get_agent(ra["agent_id"]) is None
