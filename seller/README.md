# `seller/` — deterministic AAMP seller (UI + REST + MCP)

**Status:** ✅ built (CTV `summit` + Linear `beacon`) · built on the IAB **`seller-agent`** SDK.

A **deterministic, agent-free** seller: discovery, pricing, quoting, and OpenDirect
booking are pure SDK code paths. One **Databricks App per seller** serves three faces
over a single `SellerService` (logic lives in one place):

| Face | Path | Consumer |
|---|---|---|
| REST | `GET/POST /api/v1/*`, `/.well-known/agent.json`, `/healthz` | the UI + HTTP/interop |
| MCP | `/mcp` (streamable HTTP) | buyer **agents** |
| UI | `/` | humans (served from the same app; built later) |

## How it uses the SDK (and the few deltas)

Models (`Package`, `ProductDefinition`, `PricingRule`, `QuoteResponse`, `BuyerContext`,
`AccessTier`, `TRUST_TO_TIER_MAP`) and engines (`MediaKitService`, `PricingRulesEngine`,
`AudienceFilter`) are used **verbatim**. The SDK is installed `--no-deps` (the deterministic
seller doesn't need the `crewai` agent stack — see `requirements.txt`). Three deltas:

- **`kvstore.LakebaseKVStore`** — the SDK's own `PostgresBackend`, shimmed: (1) JSON-decode
  on read (the PG backend returns raw JSONB text — SQLite/Redis backends `json.loads`; this
  restores parity); (2) **insertion-time type checking** — `set()` validates against the SDK
  model for the key prefix (`product`/`package`/`quote`/`deal`) before storing; (3) skip
  connect-time DDL (the `seed/` job owns the table). Catalog comes from the seller's
  `kv_<slug>.kv_store` (SDK-format `product:`/`package:` keys); runtime state (quotes/orders/
  creatives) lives in the same store.
- **`pricing.build_pricing_engine`** — builds `TieredPricingConfig` from `aamp.pricing_rules`,
  lifting each rule's % onto its SDK tier and neutralizing the rule % so the tier discount
  isn't double-counted (the seed encodes it on both).
- **`media_kit.RuleAwareMediaKitService`** — prices the authenticated card via the same
  `calculate_price` the quote uses, so **the card already shows the buyer's deterministic
  rate (card == quote)**.

## Pricing model (deterministic — no negotiation)

More identity revealed ⇒ better rate (SDK-faithful): **public** sees a range (MSRP),
**agency** a tier discount, **advertiser** the deepest (a flat pre-set CPM). The buyer's
effective rate shows on their card *and* equals the quote. Summit example (base $40):
public `$32–$48` / quote $40 · agency $36 · advertiser $32 · agency @20M impressions $30.60.
Availability is the SDK `QuoteAvailability` stub (no ad server). Booking auto-books (Phase 1).

## Contract (reads / writes)

- **Reads** `kv_<slug>.kv_store` (catalog, seed-materialized SDK objects) + `aamp.pricing_rules`
  (this seller's partition). **Seed-first**: the `seed/` job provisions these — the app never
  runs DDL. *(Seed change made for this: `materialize_kv` now creates the kv_store with
  `expires_at DOUBLE PRECISION` + `idx_kv_expires` + grants, matching the SDK backend exactly.)*
- **MCP tools / REST**: `list_packages` · `get_package` · `get_pricing` · `request_quote` ·
  `create_order` → `create_line` · `submit_creative` · `list_orders`.
- **Identity/tier seam**: `SellerService.resolve_tier_ceiling` is where the `registry/` component
  plugs in (verified trust → `TRUST_TO_TIER_MAP`). Today the buyer's revealed identity drives
  the tier (ADVERTISER requires both `agency_id` + `advertiser_id` per the SDK).

## Run locally

```bash
cd seller && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps "ad_seller_system @ git+https://github.com/IABTechLab/seller-agent.git@0b463ba"
pip install -e .                       # the seller_mcp package (for tests)

# point at a seeded Lakebase DB (see the root README to seed one)
export AAMP_SELLER=summit AAMP_DATABRICKS_PROFILE=<profile> AAMP_SELLER_DATABASE=<db>
uvicorn app:app --host 127.0.0.1 --port 8123
curl localhost:8123/api/v1/packages
```

## Test

```bash
pytest -m "not integration"     # hermetic: pricing reconciliation + insertion validation
pytest -m integration           # end-to-end vs a seeded Lakebase (needs creds)
```

## Deploy (DAB)

One app per seller from this source; `AAMP_SELLER` selects the publisher (`app.yaml`
defaults to `summit`, overridden per app for `beacon`). The app's service principal must be
granted the `aamp-seller` Lakebase instance. See `../databricks.yml` (`apps.seller_summit_app`,
`apps.seller_beacon_app`).

**Don't redefine the schema —
consume `seed/`'s.**
