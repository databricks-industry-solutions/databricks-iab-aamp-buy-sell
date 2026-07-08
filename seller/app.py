"""One Databricks App per seller: UI + REST + MCP, all over a single SellerService.

There is NO separate MCP server. This FastAPI app exposes the same deterministic
operations two ways:
  * REST  — `GET/POST /api/v1/*` + `/.well-known/agent.json` (the UI and HTTP clients)
  * MCP   — `/mcp` (streamable HTTP) for buyer *agents*; each tool is a thin wrapper
            over the identical SellerService method (logic lives in one place).

STATELESS BY DESIGN. The app holds NO database connection and NO cached catalog or
pricing between requests. Nothing touches Lakebase at startup — the app boots even
if the instance is scaled to zero or empty. Every REST call and every MCP tool opens
a fresh `service_scope`, which re-reads pricing rules + catalog straight from the DB,
does its work, and tears the connection down. A price edited in Lakebase therefore
shows on the very next call; an asleep instance just makes the first call wait.

Parameterized by AAMP_SELLER (the DAB deploys one app per seller). Run:
    uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

# src/ layout: make seller_mcp importable without an editable install (Apps run
# `uvicorn app:app` from this dir with only requirements.txt installed).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from ad_seller.models.agent_registry import (  # noqa: E402
    AgentAuthentication,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
)

from seller_mcp.config import get_settings  # noqa: E402
from seller_mcp.service import SellerService, service_scope  # noqa: E402

settings = get_settings()


async def get_service() -> AsyncIterator[SellerService]:
    """FastAPI dependency: a fresh, DB-backed SellerService per request, closed
    when the request ends. This is the REST face of `service_scope` — no caching."""
    async with service_scope(settings) as s:
        yield s


# ===========================================================================
# MCP surface — tools are thin wrappers over SellerService (buyer agents)
# ===========================================================================
# streamable_http_path="/" so the sub-app serves at the mount root (mounting at
# "/mcp" then exposes it cleanly at /mcp, no /mcp -> /mcp/ redirect that breaks clients).
mcp = FastMCP(f"AAMP Seller — {settings.org_id}", streamable_http_path="/")


@mcp.tool()
async def list_packages(
    audience_id: Optional[str] = None,
    audience_type: str = "standard",
    featured_only: bool = False,
    seat_id: Optional[str] = None,
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
) -> list[dict]:
    """Discover packages. Anonymous buyers see price ranges and capability summaries;
    identified buyers (seat/agency/advertiser) see their exact rate, the products
    inside, and full audience segments."""
    async with service_scope(settings) as s:
        return await s.list_packages(
            audience_id=audience_id, audience_type=audience_type, featured_only=featured_only,
            seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id,
        )


@mcp.tool()
async def get_package(
    package_id: str, seat_id: Optional[str] = None,
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
) -> Optional[dict]:
    """Open a package; authenticated buyers see its constituent products (placements)."""
    async with service_scope(settings) as s:
        return await s.get_package(
            package_id, seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id
        )


@mcp.tool()
async def get_pricing(
    product_id: str, volume: int = 0, seat_id: Optional[str] = None,
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    dsp_platform: Optional[str] = None,
) -> Optional[dict]:
    """Deterministic price for a product at the buyer's tier (+ optional volume)."""
    async with service_scope(settings) as s:
        return await s.get_pricing(
            product_id, volume=volume, seat_id=seat_id, agency_id=agency_id,
            advertiser_id=advertiser_id, dsp_platform=dsp_platform,
        )


@mcp.tool()
async def request_quote(
    product_id: str, deal_type: str = "PD", impressions: Optional[int] = None,
    flight_start: Optional[str] = None, flight_end: Optional[str] = None,
    target_cpm: Optional[float] = None,
    seat_id: Optional[str] = None, agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    dsp_platform: Optional[str] = None,
) -> dict:
    """Lock a bookable quote (price + flight + impressions). If target_cpm is given and
    clears the floor, the seller honors the buyer's offered price. Flight defaults to a
    month-long window when omitted. Availability is the SDK stub."""
    async with service_scope(settings) as s:
        return await s.request_quote(
            product_id, deal_type=deal_type, impressions=impressions,
            flight_start=flight_start, flight_end=flight_end, target_cpm=target_cpm,
            seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id, dsp_platform=dsp_platform,
        )


@mcp.tool()
async def create_deal(quote_id: str, notes: Optional[str] = None) -> dict:
    """Rail 2, step 1: mint a PG Deal ID from an accepted quote (a supply-side reference
    handed to the buyer) + OpenRTB params; marks the quote booked. Distribution to an SSP
    is the separate distribute_deal_via_ssp step."""
    async with service_scope(settings) as s:
        return await s.create_deal(quote_id, notes=notes)


@mcp.tool()
async def distribute_deal_via_ssp(deal_id: str, ssp: str) -> dict:
    """Rail 2, step 2: activate a minted Deal ID in an SSP (FreeWheel / Magnite / …) so
    the buyer's DSP can serve against it. The live SSP call is stubbed; returns the
    SSPDeal-shaped distribution record."""
    async with service_scope(settings) as s:
        return await s.distribute_deal_via_ssp(deal_id, ssp=ssp)


@mcp.tool()
async def create_order(
    seat_id: Optional[str] = None, agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
) -> dict:
    """Open an OpenDirect insertion order for this buyer."""
    async with service_scope(settings) as s:
        return await s.create_order(seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)


@mcp.tool()
async def create_line(order_id: str, quote_id: str) -> dict:
    """Add a line from a quote and book the order (auto-books in Phase 1)."""
    async with service_scope(settings) as s:
        return await s.create_line(order_id, quote_id)


@mcp.tool()
async def submit_creative(order_id: str, asset_url: str, name: str = "creative") -> dict:
    """Attach a creative (metadata + URL) to a booked order; auto-approved -> READY."""
    async with service_scope(settings) as s:
        return await s.submit_creative(order_id, asset_url=asset_url, name=name)


@mcp.tool()
async def list_orders() -> list[dict]:
    """List this seller's orders (the bookings ledger)."""
    async with service_scope(settings) as s:
        return await s.list_orders()


@mcp.tool()
async def list_deals() -> list[dict]:
    """List this seller's booked deals (Deals API commit records)."""
    async with service_scope(settings) as s:
        return await s.list_deals()


@mcp.tool()
async def list_quotes() -> list[dict]:
    """List this seller's quotes (issued, booked, expired)."""
    async with service_scope(settings) as s:
        return await s.list_quotes()


# ===========================================================================
# App + lifespan (build the service; run the MCP session manager)
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # No DB work at startup: the app boots even if Lakebase is asleep or empty.
    # The MCP transport is mounted at module load (below) so it's registered BEFORE
    # the SPA catch-all; here we only run its session manager.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title=f"AAMP Seller — {settings.org_id}", lifespan=lifespan)
# Mount the buyer-agent MCP channel at module load — must precede the SPA catch-all
# (registered at the end of this file) or the greedy catch-all would shadow /mcp.
app.mount("/mcp", mcp.streamable_http_app())


# ===========================================================================
# REST surface — same SellerService methods (UI + HTTP interop)
# ===========================================================================
@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "seller": settings.org_id, "kv_schema": settings.kv_schema}


@app.get("/api/v1/packages")
async def api_list_packages(
    audience_id: Optional[str] = None, audience_type: str = "standard", featured_only: bool = False,
    seat_id: Optional[str] = None, agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    s: SellerService = Depends(get_service),
):
    return await s.list_packages(
        audience_id=audience_id, audience_type=audience_type, featured_only=featured_only,
        seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id,
    )


@app.get("/api/v1/packages/{package_id}")
async def api_get_package(
    package_id: str, seat_id: Optional[str] = None,
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    s: SellerService = Depends(get_service),
):
    pkg = await s.get_package(package_id, seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="package not found")
    return pkg


@app.get("/api/v1/pricing")
async def api_get_pricing(
    product_id: str, volume: int = 0, seat_id: Optional[str] = None,
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    dsp_platform: Optional[str] = None,
    s: SellerService = Depends(get_service),
):
    p = await s.get_pricing(
        product_id, volume=volume, seat_id=seat_id, agency_id=agency_id,
        advertiser_id=advertiser_id, dsp_platform=dsp_platform,
    )
    if p is None:
        raise HTTPException(status_code=404, detail="product not found")
    return p


class QuoteRequestBody(BaseModel):
    product_id: str
    deal_type: str = "PD"
    impressions: Optional[int] = None
    flight_start: Optional[str] = None
    flight_end: Optional[str] = None
    target_cpm: Optional[float] = None
    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    dsp_platform: Optional[str] = None


@app.post("/api/v1/quotes")
async def api_request_quote(body: QuoteRequestBody, s: SellerService = Depends(get_service)):
    try:
        return await s.request_quote(**body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/quotes/{quote_id}")
async def api_get_quote(quote_id: str, s: SellerService = Depends(get_service)):
    q = await s.get_quote(quote_id)
    if q is None:
        raise HTTPException(status_code=404, detail="quote not found or expired")
    return q


class DealBookingBody(BaseModel):
    quote_id: str
    notes: Optional[str] = None


@app.post("/api/v1/deals")
async def api_create_deal(body: DealBookingBody, s: SellerService = Depends(get_service)):
    """Rail 2, step 1: mint a PG Deal ID from an accepted quote."""
    try:
        return await s.create_deal(body.quote_id, notes=body.notes)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


class DistributeBody(BaseModel):
    ssp: str


@app.post("/api/v1/deals/{deal_id}/distribute")
async def api_distribute_deal(deal_id: str, body: DistributeBody, s: SellerService = Depends(get_service)):
    """Rail 2, step 2: activate the Deal ID in an SSP (stubbed)."""
    try:
        return await s.distribute_deal_via_ssp(deal_id, ssp=body.ssp)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/v1/deals")
async def api_list_deals(s: SellerService = Depends(get_service)):
    return await s.list_deals()


@app.get("/api/v1/quotes")
async def api_list_quotes(s: SellerService = Depends(get_service)):
    """All quotes (the live activity feed reads this alongside deals + orders)."""
    return await s.list_quotes()


@app.get("/api/v1/deals/{deal_id}")
async def api_get_deal(deal_id: str, s: SellerService = Depends(get_service)):
    d = await s.get_deal(deal_id)
    if d is None:
        raise HTTPException(status_code=404, detail="deal not found")
    return d


class OrderBody(BaseModel):
    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None


@app.post("/api/v1/orders")
async def api_create_order(body: OrderBody, s: SellerService = Depends(get_service)):
    return await s.create_order(**body.model_dump())


class LineBody(BaseModel):
    quote_id: str


@app.post("/api/v1/orders/{order_id}/lines")
async def api_create_line(order_id: str, body: LineBody, s: SellerService = Depends(get_service)):
    try:
        return await s.create_line(order_id, body.quote_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CreativeBody(BaseModel):
    asset_url: str
    name: str = "creative"


@app.post("/api/v1/orders/{order_id}/creatives")
async def api_submit_creative(order_id: str, body: CreativeBody, s: SellerService = Depends(get_service)):
    try:
        return await s.submit_creative(order_id, asset_url=body.asset_url, name=body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/orders")
async def api_list_orders(s: SellerService = Depends(get_service)):
    return await s.list_orders()


@app.get("/api/v1/overview")
async def api_overview(
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    s: SellerService = Depends(get_service),
):
    """Aggregated dashboard payload — one round-trip; queries run parallel server-side."""
    return await s.overview(agency_id=agency_id, advertiser_id=advertiser_id)


@app.get("/api/v1/audiences")
async def api_list_audiences(s: SellerService = Depends(get_service)):
    """The seller's audience catalog (names/sizes/IAB ids from aamp.audiences)."""
    return await s.list_audiences()


@app.get("/api/v1/product-pricing")
async def api_product_pricing(
    product_id: str, agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
    s: SellerService = Depends(get_service),
):
    """public→agency→advertiser price spread for one product (parallel; SDK rationale)."""
    return await s.tier_spread(product_id, agency_id=agency_id, advertiser_id=advertiser_id)


def _public_base(request: Request) -> str:
    """The app's externally-reachable base URL. Databricks Apps sit behind a proxy, so
    the in-process Host is localhost:8000. Prefer an explicit override, then the
    DATABRICKS_APP_URL the runtime injects, then the proxy's X-Forwarded-* headers,
    then the request host."""
    for env in ("AAMP_PUBLIC_URL", "DATABRICKS_APP_URL"):
        if os.environ.get(env):
            return os.environ[env].rstrip("/")
    fwd_host = request.headers.get("x-forwarded-host")
    if fwd_host:
        proto = request.headers.get("x-forwarded-proto", "https")
        return f"{proto}://{fwd_host}"
    return str(request.base_url).rstrip("/")


@app.get("/.well-known/agent.json")
async def agent_card(request: Request) -> dict:
    """A2A discovery card — a valid SDK AgentCard so the registry can ingest it verbatim."""
    base = _public_base(request)
    # Declare the seller's CHANNEL (inventory_types, e.g. ctv / linear_tv) so registry
    # discovery is channel-aware — the buyer matches its channel to a seller's capability
    # (Registry spec §discovery). Sourced from the seller's own seeded products; falls back
    # to the configured channel(s) if Lakebase is asleep (the card must still render).
    channel: list[str] = []
    try:
        async with service_scope(settings) as s:
            products = await s.storage.list_products()
            channel = sorted({p["inventory_type"] for p in products if p.get("inventory_type")})
    except Exception:  # noqa: BLE001 — card must render even if the catalog read fails
        channel = []
    if not channel:
        channel = settings.inventory_type_list
    # Per the IAB AgentCard spec, `name` is the human-readable agent/publisher name.
    card = AgentCard(
        name=settings.display_name,
        description=f"AAMP seller agent for {settings.display_name}",
        url=base,
        version="1.0.0",
        provider=AgentProvider(name=settings.display_name, url=base),
        capabilities=AgentCapabilities(protocols=["mcp", "opendirect21"], streaming=True),
        authentication=AgentAuthentication(schemes=["api_key"]),
        skills=[
            AgentSkill(id="discover", name="Package discovery",
                       description="Browse packages; identified buyers see exact rates + placements.",
                       tags=["discovery", "media-kit"]),
            AgentSkill(id="pricing", name="Tiered pricing",
                       description="Deterministic per-tier price for a product (+ optional volume).",
                       tags=["pricing"]),
            AgentSkill(id="quote", name="Quote + book",
                       description="Lock a bookable quote and book an OpenDirect execution order.",
                       tags=["quote", "opendirect"]),
        ],
        inventory_types=channel,
        supported_deal_types=["PG", "PD"],
    )
    d = card.model_dump(mode="json")
    # Convenience pointer (not an AgentCard field): the registry/clients read it to
    # reach the MCP surface. Trailing slash avoids the /mcp→/mcp/ internal-host 307.
    d["mcp"] = {"endpoint": f"{base}/mcp/"}
    return d


# ===========================================================================
# Static frontend — the built React cockpit (mounted LAST so /api, /mcp,
# /.well-known, /healthz all win). The SPA catch-all serves index.html for
# client-side routes; unknown api/mcp paths 404 instead of returning HTML.
# ===========================================================================
_DIST = Path(__file__).parent / "frontend" / "dist"
_RESERVED = ("api/", "mcp", "healthz", ".well-known/")

if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    async def _spa_root() -> FileResponse:
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    async def _spa(full_path: str) -> FileResponse:
        if full_path.startswith(_RESERVED):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")  # client-side route → SPA shell
