"""RegistryService — a thin, faithful wrapper over the SDK's AgentRegistryService.

All registry logic (registration, URL-indexed lookup, external-registry trust
verification, the resolve_agent_access flow, TRUST_TO_TIER_MAP tiering) lives in the
SDK and is used VERBATIM — no deviations. This layer only:
  * builds the SDK service over the Lakebase kv store + AAMP registry client, and
  * converts SDK models <-> JSON dicts for the REST/MCP faces.

Stateless, like the seller: `service_scope` opens a fresh DB-backed service per
request and tears it down after — nothing is cached, so a trust change written to
Lakebase shows on the very next call, and an asleep instance just makes the first
request wait.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from ad_seller.clients.agent_registry_client import AAMPRegistryClient
from ad_seller.models.agent_registry import (
    AgentCard,
    AgentType,
    RegisteredAgent,
    TrustStatus,
)
from ad_seller.registry import AgentRegistryService

from .config import Settings, get_settings
from .db import shared_kv_store


def _agent_json(agent: Optional[RegisteredAgent]) -> Optional[dict]:
    return agent.model_dump(mode="json") if agent else None


class RegistryService:
    def __init__(self, storage: LakebaseKVStore, registry: AgentRegistryService, settings: Settings) -> None:
        self.storage = storage
        self.registry = registry
        self.settings = settings

    @classmethod
    async def create(cls, settings: Optional[Settings] = None) -> "RegistryService":
        settings = settings or get_settings()
        # Reuse the shared, long-lived kv pool (transport); registry reads are still
        # served fresh from Lakebase on every call (no data cache).
        storage = await shared_kv_store(settings)
        clients = [AAMPRegistryClient(registry_url=settings.aamp_registry_url)]
        registry = AgentRegistryService(storage, registry_clients=clients)
        return cls(storage, registry, settings)

    async def close(self) -> None:
        # No-op: the kv pool is shared/long-lived (managed in db.py); drains after idle.
        return None

    # ----- registration / directory -------------------------------------

    async def register(
        self,
        agent_card: dict,
        *,
        agent_type: str = AgentType.BUYER.value,
        trust_status: str = TrustStatus.UNKNOWN.value,
    ) -> dict:
        """Register (or update) an agent from a supplied A2A card. Used to enroll the
        seller apps directly (their cards are fetched authenticated, client-side, then
        handed here) — `resolve_agent_access` is the alternative for reachable URLs."""
        agent = await self.registry.register_agent(
            agent_card=AgentCard(**agent_card),
            agent_type=AgentType(agent_type),
            trust_status=TrustStatus(trust_status),
        )
        return agent.model_dump(mode="json")

    async def discover(self, agent_url: str) -> dict:
        """Resolve an agent by URL (SDK 5-step flow: local → fetch card → external
        registries → trust → register). Returns the verdict the seller enforces."""
        agent, tier = await self.registry.resolve_agent_access(agent_url)
        return {
            "agent": _agent_json(agent),
            "max_access_tier": tier.value if tier is not None else None,
            "is_blocked": bool(agent and agent.is_blocked),
        }

    async def verify(self, agent_url: str) -> dict:
        """The seller's trust check: returns the agent's trust status + tier ceiling.
        Same SDK resolution as discover, framed for the §7.1 tiering call."""
        agent, tier = await self.registry.resolve_agent_access(agent_url)
        return {
            "agent_url": agent_url,
            "agent_id": agent.agent_id if agent else None,
            "trust_status": agent.trust_status.value if agent else TrustStatus.UNKNOWN.value,
            "max_access_tier": tier.value if tier is not None else None,
            "is_blocked": bool(agent and agent.is_blocked),
        }

    async def list_agents(
        self, *, agent_type: Optional[str] = None, trust_status: Optional[str] = None
    ) -> list[dict]:
        agents = await self.registry.list_agents(
            agent_type=AgentType(agent_type) if agent_type else None,
            trust_status=TrustStatus(trust_status) if trust_status else None,
        )
        return [a.model_dump(mode="json") for a in agents]

    async def get_agent(self, agent_id: str) -> Optional[dict]:
        return _agent_json(await self.registry.get_agent(agent_id))

    async def update_trust(
        self, agent_id: str, *, trust_status: str, notes: Optional[str] = None
    ) -> Optional[dict]:
        return _agent_json(
            await self.registry.update_trust_status(agent_id, TrustStatus(trust_status), notes)
        )

    async def remove(self, agent_id: str) -> bool:
        return await self.registry.remove_agent(agent_id)


@asynccontextmanager
async def service_scope(settings: Optional[Settings] = None) -> AsyncIterator["RegistryService"]:
    """A fresh RegistryService for the lifetime of ONE request — nothing cached.

    Every entry connects to Lakebase, does its work, and disconnects. A trust status
    edited in the DB shows on the next call; a scaled-to-zero instance just makes the
    first call wait; an empty registry returns empty lists.
    """
    svc = await RegistryService.create(settings)
    try:
        yield svc
    finally:
        await svc.close()
