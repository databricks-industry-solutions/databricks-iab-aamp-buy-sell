"""LakebaseKVStore — the seller-agent SDK's PostgresBackend, minimally shimmed for
Lakebase. Identical approach to the seller component (the proven solution):

  1. `get()` JSON-decodes the value. Every SDK backend serializes with json.dumps on
     `set`; the SQLite/Redis backends json.loads on `get`, but the Postgres backend
     returns `row["value"]` raw, assuming asyncpg auto-decodes JSONB (it does not
     without a codec). We decode on READ only — restoring the same contract the
     SQLite/Redis backends already honor. (Never via a write codec — would double-encode.)

  2. `set()` does INSERTION-TIME TYPE CHECKING against the SDK model the key prefix
     maps to — a value written under a typed key is validated (and stored as its
     canonical dump) BEFORE it lands. Keys without a model (the url index) pass through.

  3. `connect()` skips the SDK's DDL. The `seed/` job provisions and OWNS the kv_store
     table; the app connects as a *different* principal (its service principal) and
     must not run CREATE TABLE/INDEX. Seed-first is the contract.

Point it at the registry kv schema via the DSN (`...?options=-c search_path=kv_registry`).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel

from ad_seller.models.agent_registry import RegisteredAgent
from ad_seller.storage.postgres_backend import PostgresBackend

# key prefix -> the SDK Pydantic model that validates values stored under it.
# `agent:{id}` holds a RegisteredAgent; `agent_url_index:{hash}` holds a plain
# agent-id string (no model → stored unvalidated).
KEY_MODEL: dict[str, type[BaseModel]] = {
    "agent": RegisteredAgent,
}


class LakebaseKVStore(PostgresBackend):
    """SDK PostgresBackend over a seed-provisioned Lakebase kv_store schema."""

    async def connect(self) -> None:
        import asyncpg

        # min_size=0 + 30-min idle: the pool drains to zero when idle so the endpoint
        # can scale to zero; under load connections are reused. Shared across requests
        # (see db.shared_kv_store) — not torn down per call.
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn, min_size=0, max_size=self._pool_max,
            max_inactive_connection_lifetime=1800.0,
        )
        # No DDL: the seed owns/creates the table + index (SDK-spec). Seed-first.

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        model = KEY_MODEL.get(key.split(":", 1)[0])
        if model is not None and isinstance(value, dict):
            # Validate against the SDK model and store its canonical dump. Raises
            # pydantic.ValidationError at insert time on a malformed object.
            value = model.model_validate(value).model_dump(mode="json")
        await super().set(key, value, ttl)

    async def get(self, key: str) -> Optional[Any]:
        value = await super().get(key)  # SDK handles TTL; returns raw JSONB text
        return json.loads(value) if isinstance(value, str) else value
