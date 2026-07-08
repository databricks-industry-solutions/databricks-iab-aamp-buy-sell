# `registry/` — AAMP agent registry (REST + MCP)

**Status:** ✅ built · directory + discovery + trust verification over the seller-agent SDK's `AgentRegistryService` (used verbatim — no deviations).

## What it is
One stateless Databricks App exposing the SDK's registry two ways over a single
`RegistryService` (same shape as `seller/`):
- **REST** — `GET /registry/agents` (filter `agent_type`/`trust_status`), `GET /registry/agents/{id}`,
  `POST /registry/agents` (register from an A2A card), `POST /registry/agents/discover` (resolve a URL),
  `POST /registry/verify` (trust → tier ceiling), `PUT /registry/agents/{id}/trust`, `DELETE /registry/agents/{id}`,
  `GET /.well-known/agent.json`, `GET /healthz`.
- **MCP** — `/mcp/` (streamable HTTP): `list_agents`, `get_agent`, `discover_agent`, `verify_agent`,
  `register_agent`, `update_trust`, `remove_agent` — thin wrappers over the same methods.

## SDK fidelity
- Logic is the SDK's `AgentRegistryService` (register / `resolve_agent_access` / `update_trust_status` /
  `TRUST_TO_TIER_MAP`) verbatim; `RegistryService` only converts SDK models ↔ JSON.
- Storage is the SDK `PostgresBackend` with the *same* Lakebase shim the seller uses
  (`LakebaseKVStore`: decode-on-read + insertion type-check + no-DDL). Agents persist at
  `agent:{id}` (+ `agent_url_index:{hash}`) in `kv_registry.kv_store` on the registry Lakebase project.

## Trust model
`TrustStatus` → max `AccessTier` via `TRUST_TO_TIER_MAP`:
`unknown→PUBLIC, registered→SEAT, approved→ADVERTISER, preferred→ADVERTISER, blocked→None (reject)`.
A seller verifies a buyer → gets the ceiling → prices at `min(ceiling, claimed_tier)`.

## Deploy (part of the one-command install)
`make all PREFIX=<p> PROFILE=<prof>` deploys the 2 sellers + registry, seeds, grants the app SPs
Postgres roles, starts the apps, and **dynamically registers the live sellers** into the registry
(`make register-sellers`: fetches each seller's `/.well-known/agent.json` authenticated client-side —
the apps sit behind the Databricks OAuth proxy — and POSTs the card to `register_agent`).
Deployed as `databricks.yml` `apps.registry_app` (`./build/registry`, rendered from `./registry`).

## Open items
- **API-key auth not wired** (buyer identity is self-asserted). The SDK ships `ApiKeyService`
  (`ad_seller/auth`) + `ApiKeyRecord`; the seed creates `ad_buyer.aamp.api_keys`. Faithful fix = wire
  `get_api_key_record` so identity comes from a validated key. (Joe's R3/D10.)
- **Seller→registry `verify` not auto-called** — the seller's `resolve_tier_ceiling()` is still stubbed
  to ADVERTISER; the seam (`BuyerContext.max_access_tier`) exists but isn't yet fed by the registry.

