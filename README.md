# IAB AAMP Demo — Data Layer & Accelerator

Synthetic data for an **IAB AAMP** buyer/seller/registry demo on **Databricks Lakebase**, built on the official IAB AAMP SDKs. **Two single-channel sellers** — **Summit Streaming** (CTV) and **Beacon Broadcast** (Linear TV) — each with a coherent `audience · inventory · product · package · pricing` catalog, plus one **hero buyer** and a **registry**. The seeder generates it, validates it, and loads it as **two layers**: clean **relational** source-of-truth tables and the **SDK's per-seller `kv_store`**.

> Design docs (sell/buy/registry/maps + cartoon) live in the companion repo `iab-bootcamp`. This repo is the **buildable data layer + accelerator**.

## The three parties (separate Lakebase instances)
| Party | Instance | Database | Holds |
|---|---|---|---|
| Buyer | `<prefix>-buyer` | `ad_buyer` | briefs (with channel allocation), buyer identity, held api-key |
| Seller | `<prefix>-seller` | `ad_seller` | inventory, audiences, products, packages, pricing rules **+ per-seller `kv_store`** |
| Registry | `<prefix>-registry` | `agent_registry` | registry agents (directory + capabilities + trust), registry trust (→ tier) |

## Two storage layers
Both are written from the same generated objects:

1. **Relational source of truth** — schema **`aamp`**, proper **typed columns** (`TEXT/NUMERIC/INT/BOOLEAN`, native `TEXT[]`/`INT[]` arrays; `JSONB` only for genuinely nested fields like `placements`, `audience_capabilities`, `channels`). Sell-side tables carry a **`seller`** column. This is the human/governance view — query it, inspect it, govern it.
2. **SDK `kv_store`** — what the seller-agent actually reads. The SDK `StorageBackend` is a key-value table `kv_store(key TEXT, value JSONB)`; we materialize **one per seller** (`kv_summit`, `kv_beacon`) with the SDK's keys `product:{id}` / `package:{id}` and clean SDK-shaped values. *Two stores because the SDK hardcodes the table name `kv_store` and discovers via un-namespaced `keys("product:*")` — so each seller-agent needs its own store (separation **is** the filter).*

Plus per-seller **SDK csv_adapter inputs** (`out/sdk/<seller>/inventory.csv`+`audiences.csv`, pipe-delimited arrays).

Rows mirror **exact SDK schemas** (inventory.csv / audiences.csv headers; `Product`/`Package`/`PricingRule` models). Model-validated fields use SDK enum values (`status="active"`, `rate_type="cpm"`, `supported_deal_types=["programmaticguaranteed","preferreddeal"]`). `volume_discounts=[]` (the SDK engine's default schedule applies); availability is the SDK quote stub (no ad server).

## Quickstart (local — no Lakebase needed)
```bash
pip install -r requirements.txt
python -m seed validate            # coherence: single-channel sellers, no cross-seller products, 3-way trust/tier
python -m seed preview             # generate + validate + write inspection + SDK CSVs to out/
open out/seller/packages.csv       # exactly what will be loaded
```

## Load to Lakebase
```bash
export AAMP_SELLER_DSN=postgresql://...  AAMP_BUYER_DSN=postgresql://...  AAMP_REGISTRY_DSN=postgresql://...
python -m seed seed                # relational tables + per-seller kv_store + SDK CSVs
python -m seed kv                  # (re)materialize ONLY the SDK kv_store (relational already loaded)
python -m seed reset               # DROP the aamp + kv_* schemas (keep instances)
```

## Customer self-deploy

**One command — full stack (recommended):**
```bash
databricks auth login --host https://YOUR-workspace.cloud.databricks.com --profile YOUR-profile
make all PREFIX=aamp PROFILE=YOUR-profile
```
One `make all` does everything: bootstraps the venv, builds the three frontends, vendors the
IAB taxonomy TSVs (auto-downloaded from the public SDK repo), deploys the DAB (3 Lakebase
instances + 4 apps + seed job), tunes endpoints to 0.5–1 CU, registers each app's service
principal as a Postgres role, grants buyer→registry/seller app access, seeds both layers,
starts the apps, registers the sellers into the registry, and wires the buyer to the live
registry URL. Prereqs: `python3`, `node`/`npm`, and an authenticated Databricks CLI profile.
The granular paths below cover data-layer-only deploys.

**Option A — data layer only, config-driven (`provision_lakebase: true`):**
```bash
pip install -r requirements.txt
databricks auth login --host https://YOUR-workspace.cloud.databricks.com
cp config.example.yaml config.yaml        # set workspace_profile, prefix
python -m seed up --plan                  # show what it WILL provision + seed (no changes)
python -m seed up                         # provisions 3 Lakebase instances + databases, then seeds
```
`seed up` reads `config.yaml`: if `provision_lakebase: true` it creates `<prefix>-buyer/seller/registry` (+ databases) via the Databricks SDK, mints credentials at runtime, then seeds. Idempotent. Set `provision_lakebase: false` to bring your own instances (fill in `dsn:`).

**Option B — DAB (also deploys the apps):**
```bash
cp config.example.yaml config.yaml
databricks bundle deploy -t dev           # 3 Lakebase instances + apps
databricks bundle run seed_job            # seed both sellers (relational + kv_store)
```

## Sellers are data, not code
`seed/sellers/*.yaml` defines each seller (channel, audiences, inventory, products, packages, rate card); `seed/buyers.yaml` defines the hero buyer + briefs. Add a seller YAML → it's seeded. Each seller is **single-channel** (the validator enforces it) and its capability token (`ctv`/`linear_tv`) is what the buyer's channel discovery matches in the registry.

## Coherence guarantee
Nothing loads unless `validate` passes: package codes ∈ their seller's audiences · **single channel per seller** · **no product/package crosses sellers** · brief codes ⊆ package codes · brief impressions ≥ product minimum · brief channel `budget_pct` sums to 100 · **3-way** `registry trust → tier → rate card`. (IAB audience + content codes are checked against the vendored TSVs, advisory.)

## Demoing tiers — one buyer, flip its trust
Trust is global-per-buyer in the registry. Flip the hero buyer's `registry_trust.trust_status` (`UNKNOWN → APPROVED → PREFERRED` = public → agency → advertiser) and its pricing changes at **both** sellers — each applying **its own rate card** (agency = `discount_percentage`; advertiser = `base_price_override` + floor/ceiling). Public view = connect with no api-key.

## Layout
```
seed/
  sellers/*.yaml    one single-channel seller per file (Summit CTV, Beacon Linear)
  buyers.yaml       hero buyer + briefs (CTV+Linear channel allocation)
  taxonomy.py       TRUST_TO_TIER_MAP (mirrors SDK) + optional IAB TSV validation
  generator.py      sellers + buyers → coherent seller/buyer/registry rows (seller column; SDK-exact)
  validate.py       single-channel + no-cross-seller + 3-way trust/tier (fail loudly)
  load.py           relational tables + per-seller kv_store + SDK CSVs
  cli.py            preview / validate / seed / kv / up / reset / down
databricks.yml      DAB: 3 Lakebase instances + apps + seed_job
config.example.yaml customer config
```

## License & third-party

This repository is licensed under the [Databricks License](LICENSE.md).

Built on **IAB Tech Lab**'s [`seller-agent` SDK](https://github.com/IABTechLab/seller-agent)
(`ad_seller_system`, Apache-2.0) — installed from GitHub at deploy time, not vendored here.
The seller apps implement AAMP (Agentic Advertising Management Protocols) sell-side flows on
top of it: OpenDirect 2.1, AdCOM, and the IAB Audience 1.1 / Content 3.1 taxonomies.

The IAB taxonomy TSVs themselves are **not redistributed** in this repo — the build downloads
them from the public SDK repo on first use (any `make render`/`deploy`/`all`, or `make
taxonomies`); set `SDK_DIR=path/to/seller-agent` to vendor from a local clone instead.

Frontends are Vite + React; Python services are FastAPI + FastMCP + CrewAI. All third-party
packages are used under their own licenses (Apache-2.0 / MIT / BSD) as declared in
`*/frontend/package-lock.json` and the `pyproject.toml` files.

Company and platform names that appear in demo copy (e.g. Magnite, The Trade Desk, DV360,
Xandr) are trademarks of their respective owners, used only to illustrate industry roles;
no affiliation or endorsement is implied. All sellers, buyers, products, and prices in the
seed data are fictional.
