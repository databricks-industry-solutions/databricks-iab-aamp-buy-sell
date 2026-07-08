# `buyer/` — agentic AAMP buyer

An agentic media-buying app: a CrewAI L1/L2/L3 hierarchy (portfolio manager →
channel specialists → audience/research/execution workers) that reads campaign
briefs from Lakebase, discovers sellers from the registry, plans audiences against
the IAB Audience Taxonomy, and runs a deterministic **book-or-pass** transaction
over each seller's MCP surface. Two channels: CTV and Linear TV.

## Run / test
```
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000          # then open http://localhost:8000/ for the demo UI
python -m pytest -q                                  # hermetic suite (book-or-pass, taxonomy, mock-seller client)
AAMP_DATABRICKS_PROFILE=<profile> python -m pytest -m integration -q   # live Model Serving + crew flow
```
Connecting to Lakebase requires Databricks credentials: set `AAMP_DATABRICKS_PROFILE`
to a CLI profile locally (the app uses its service principal when deployed), or set
`AAMP_BUYER_DSN` / `AAMP_REGISTRY_DSN` to point at your own Postgres.

## Demo UI (`/`)
A single-page presenter surface (`static/index.html`, no build step) so a human can
drive a run and an audience can watch the crew work — no `curl` required:
- **Pick a brief** (loaded from `/briefs`) and hit **Run campaign**.
- A **live timeline** streams over Server-Sent Events (`GET /runs/{run_id}/events`):
  identity tier → audience plan (IAB codes) → budget split → per-channel cards that
  fill in as the crew discovers the seller, calls each MCP tool
  (`discover_packages`/`open_package`/`get_product_pricing`), recommends a product,
  reads the authoritative price, and **books or passes** against `max_cpm`.
- A **"Present as" selector** picks the identity tier (public/seat/agency/advertiser) the
  buyer presents for the run; the header badge reflects the current choice.

## HTTP surface
- `GET /healthz` — liveness (no database).
- `GET /briefs`, `GET /briefs/{brief_id}` — campaign briefs.
- `GET /sellers[?capability=]` — seller discovery from the registry.
- `GET /packages?capability=[&tier=]`, `GET /pricing?capability=&product_id=[&tier=]` —
  discovery → seller MCP, presenting the identity gated to `tier`.
- `GET /briefs/{brief_id}/audience-plan` — resolve a brief's interests to IAB codes.
- `POST /run/{brief_id}[?tier=]` — full L1/L2/L3 book-or-pass flow (synchronous JSON).
- `POST /runs/{brief_id}[?tier=]` + `GET /runs/{run_id}/events` + `GET /runs/{run_id}` —
  the background run + SSE progress stream the demo UI uses.
- `GET /status` — buyer id, default tier, and the selectable identity profiles.

## What it reads / calls
- **Reads** Lakebase `ad_buyer.aamp`: `briefs` (a CTV+Linear `channels` allocation,
  `max_cpm`, `interests`, flight, `total_budget`) and `buyer_identities`.
- **Discovers sellers** from the **registry app** when `AAMP_REGISTRY_URL` is set (real
  seller URLs + channels from each seller's agent card); otherwise from Lakebase
  `agent_registry.aamp`.
- **Calls** each seller's MCP server (`/mcp/`). With the registry app the reachable URL is
  the seller's agent-card `url`; in the Lakebase fallback it comes from `AAMP_SELLER_URLS`.
- **Agentic** via Databricks Model Serving (MLflow-traced when configured): L1
  `portfolio_manager`, L2 channel specialists, L3 `audience_planner` / `research` /
  `execution`.
- **Deterministic transaction:** discover → `get_pricing` → `request_quote` → book.
  Book-or-pass only, no negotiation; `max_cpm` is a guardrail and the buyer never
  submits a price.

## Identity profile (the tiering demo)
The buyer holds a full `seat/agency/advertiser` identity in `buyer_identities` and presents
a **cumulative subset** of it based on a tier:

| tier | reveals | seller effect |
|------|---------|---------------|
| `public` | nothing (anonymous) | card/list price |
| `seat` | `seat_id` | exact price, no negotiation |
| `agency` | `seat_id` + `agency_id` | agency rate |
| `advertiser` | `seat_id` + `agency_id` + `advertiser_id` | best rate / overrides |

For demos the tier is a **per-run choice** — the UI "Present as" selector (or `?tier=` on
`POST /runs/{brief_id}`, `/run`, `/packages`, `/pricing`). The seller then computes
`effective_tier = min(claimed, its ceiling)`, so presenting a higher tier moves its price
under/over the brief's `max_cpm` → book-or-pass. This lets a presenter show all four
outcomes back-to-back without mutating any shared state.

When no `tier` is supplied, the buyer falls back to its **registry trust** via
`POST /registry/verify` (`max_access_tier`), mapped through `TRUST_TO_TIER` in the Lakebase
fallback. That map matches the SDK's `TRUST_TO_TIER_MAP` (`unknown→public`, `registered→seat`,
`approved`/`preferred`→`advertiser`, `blocked`→none) — note there is **no trust status that
yields `agency`**; `agency` is reachable only via the per-run selector.

## Registry integration
When `AAMP_REGISTRY_URL` points at the deployed registry app, the buyer discovers sellers
from it (instead of reading the registry's Lakebase tables):
- **Seller discovery** — `GET /registry/agents?agent_type=seller`; each seller's channel
  comes from its agent-card `inventory_types` (e.g. `ctv`, `linear_tv`) and its reachable
  URL from the card `url`. The bundled sellers advertise `inventory_types` (summit=`ctv`,
  beacon=`linear_tv`, set via `AAMP_INVENTORY_TYPES`), so the buyer matches a campaign's
  channel to the right seller.
- **Wiring `AAMP_REGISTRY_URL`** — the registry app's URL only exists after it's deployed,
  so `buyer/render_apps.py` resolves it (via the deployer profile) and bakes it into the
  rendered `app.yaml`. On a first-ever bring-up the registry app doesn't exist yet, so the
  buyer renders with the Lakebase fallback; `make all` finishes with `make wire-buyer`,
  which re-renders + redeploys the buyer once the registry is up. Re-running `make deploy`
  later picks up the URL automatically.

## IAB taxonomy codes
The L3 `audience_planner` turns a brief's free-text `target_audience.interests` into
IAB Audience Taxonomy codes, and `TaxonomyLookupTool` validates each proposed code
against the TSVs in `../resources/taxonomies/` — so the LLM can't hallucinate a code,
and resolution lands on codes the sellers' packages actually declare
(`standard_segment_ids`). Set `AAMP_TAXONOMY_DIR` to override the TSV location; the
deploy packages a copy under `buyer/resources/`.

## Module layout
- `config.py` — env-driven settings (buyer + registry Lakebase, seller-URL map, Model Serving, MLflow).
- `db.py` — Lakebase credential minting + reads of `ad_buyer.aamp` and `agent_registry.aamp`.
- `registry_client.py` — seller discovery (`discover_sellers`) + this buyer's trust
  (`verify_self`); `HttpRegistryClient` (registry app) or `DbRegistryClient` (Lakebase).
- `seller_client.py` — `SellerMCPClient` over each seller's `/mcp/` + the `run_sync` async bridge.
- `taxonomy.py` / `tools.py` — the IAB taxonomy gate + the CrewAI tools (taxonomy lookup + seller-MCP reads).
- `llm.py` — CrewAI `LLM` bound to Databricks Model Serving (OpenAI-compatible, token minted per run).
- `agents/` (`level1`/`level2`/`level3`) + `crews.py` — the hierarchical L1/L2/L3 crews.
- `flow.py` — `run_brief`: plan → allocate → per-channel crew → deterministic book-or-pass (emits live progress).
- `identity.py` — tier-gated identity (registry trust → which identity fields to present).
- `progress.py` — thread-safe in-process run hub buffering progress events for the SSE stream.
- `static/index.html` — the presenter UI (brief picker + live crew timeline).
- `app.py` — the FastAPI surface.
