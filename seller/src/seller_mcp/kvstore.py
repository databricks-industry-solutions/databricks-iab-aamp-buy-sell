"""LakebaseKVStore — the seller-agent SDK's PostgresBackend, minimally shimmed.

We reuse the SDK's `PostgresBackend` (keys/delete/exists + the higher-level
`get_package`/`list_packages`/`get_quote`/`set_order`… helpers) as-is, with three
small, deliberate adjustments:

  1. `get()` JSON-decodes the value. Every SDK backend serializes with json.dumps
     on `set`; SQLite and Redis `json.loads` on `get`, but the Postgres backend
     returns `row["value"]` raw, assuming asyncpg auto-decodes JSONB (it does not
     without a codec). We decode on read — restoring the same contract the
     SQLite/Redis backends already honor. Decode on READ only, never via a write
     codec (which would double-encode the SDK's `set`).

  2. `set()` does INSERTION-TIME TYPE CHECKING. The SDK's KV layer is intentionally
     untyped (`value: Any`, JSON-serialized — Redis-style), with validation left to
     the engines at read time (`Package(**data)`). We close that gap at the write
     boundary: a value written under a key whose prefix maps to an SDK model is
     validated against that model (and stored as its canonical dump) BEFORE it lands.
     A malformed object fails loudly at insert, not later at read. Keys without an
     SDK model (orders, accounts, sessions…) pass through unvalidated.

  3. `connect()` skips the SDK's DDL. The `seed/` job provisions and OWNS the
     kv_store table (SDK-spec); the app connects as a *different* principal and must
     not run CREATE TABLE/INDEX. Seed-first is the contract.

Point it at a seller's kv schema via the DSN (`...?options=-c search_path=kv_<slug>`).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel

from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.media_kit import Package
from ad_seller.models.quotes import DealBookingResponse, QuoteResponse
from ad_seller.storage.postgres_backend import PostgresBackend

# key prefix -> the SDK Pydantic model that validates values stored under it.
# Prefixes without an SDK model (order, account, session, agent, deal-state…) are
# stored unvalidated — we only type-check against "the models the SDK offers".
KEY_MODEL: dict[str, type[BaseModel]] = {
    "product": ProductDefinition,
    "package": Package,
    "quote": QuoteResponse,
    "deal": DealBookingResponse,
}


class LakebaseKVStore(PostgresBackend):
    """SDK PostgresBackend over a seed-provisioned Lakebase kv_store schema."""

    async def connect(self) -> None:
        import asyncpg

        # min_size=0 + a 30-min idle timeout: the pool drains to zero connections when
        # traffic stops, so the Autoscaling endpoint can still suspend/scale to zero;
        # under load, connections are reused (no per-request connect). This pool is
        # shared across requests (see db.shared_kv_store) — it is NOT torn down per call.
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
