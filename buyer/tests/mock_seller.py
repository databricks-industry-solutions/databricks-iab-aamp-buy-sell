"""A faithful, dependency-free stand-in for a seller app's MCP surface.

TEST FIXTURE ONLY — this is not packaged, imported by the app, or deployed by the DAB.
It exists so the buyer's SellerMCPClient and crew flow can be tested offline (no seeded
Lakebase, no Databricks creds, no real seller process). Deployments use the real
`seller/` app.

It mirrors `seller/app.py`'s MCP wiring exactly — FastMCP(streamable_http_path="/")
mounted at "/mcp", same tool names and signatures — so the buyer's SellerMCPClient
exercises the real transport (incl. the /mcp/ trailing-slash redirect behavior),
tool discovery, argument passing, and result parsing. It does NOT replicate the
seller's SDK pricing/kv logic (that's the seller's concern, validated in-workspace);
it returns representative shapes and keeps just enough in-memory state to support a
quote -> order -> line round-trip.

Run standalone:  uvicorn tests.mock_seller:app --port 8400
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Mock AAMP Seller", streamable_http_path="/")

# in-memory state (one process, one test run)
_QUOTES: dict[str, dict] = {}
_ORDERS: dict[str, dict] = {}
_DEALS: dict[str, dict] = {}
_n = {"q": 0, "o": 0, "l": 0, "c": 0, "d": 0, "sd": 0}


def _next(kind: str) -> str:
    _n[kind] += 1
    return f"{kind}-{_n[kind]:04d}"


def _tier(seat_id, agency_id, advertiser_id) -> str:
    if advertiser_id:
        return "advertiser"
    if agency_id:
        return "agency"
    if seat_id:
        return "seat"
    return "public"


@mcp.tool()
async def list_packages(
    audience_id: Optional[str] = None,
    audience_type: str = "standard",
    featured_only: bool = False,
    seat_id: Optional[str] = None,
    agency_id: Optional[str] = None,
    advertiser_id: Optional[str] = None,
) -> list[dict]:
    tier = _tier(seat_id, agency_id, advertiser_id)
    pkgs = [
        {"package_id": "pkg-ctv-premium", "name": "Summit CTV Premium", "is_featured": True,
         "tier": tier, "products": ["prod-ctv-sports", "prod-ctv-food"]},
        {"package_id": "pkg-ctv-lifestyle", "name": "Summit CTV Lifestyle", "is_featured": False,
         "tier": tier, "products": ["prod-ctv-travel", "prod-ctv-food"]},
    ]
    return [p for p in pkgs if p["is_featured"]] if featured_only else pkgs


@mcp.tool()
async def get_package(
    package_id: str, seat_id: Optional[str] = None,
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
) -> Optional[dict]:
    if package_id != "pkg-ctv-premium":
        return None
    return {"package_id": package_id, "name": "Summit CTV Premium",
            "tier": _tier(seat_id, agency_id, advertiser_id),
            "products": ["prod-ctv-sports", "prod-ctv-food"]}


@mcp.tool()
async def get_pricing(
    product_id: str, volume: int = 0, seat_id: Optional[str] = None,
    agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
) -> Optional[dict]:
    base = {"prod-ctv-sports": 40.0, "prod-ctv-food": 38.0, "prod-ctv-travel": 36.0}.get(product_id)
    if base is None:
        return None
    tier = _tier(seat_id, agency_id, advertiser_id)
    discount = {"public": 0.0, "seat": 0.0, "agency": 0.10, "advertiser": 0.20}[tier]
    return {"product_id": product_id, "base_cpm": base, "tier_discount": discount,
            "volume_discount": 0.0, "final_price": round(base * (1 - discount), 2),
            "buyer_tier": tier, "rationale": f"{tier} tier"}


@mcp.tool()
async def request_quote(
    product_id: str, deal_type: str = "PD", impressions: Optional[int] = None,
    flight_start: Optional[str] = None, flight_end: Optional[str] = None,
    seat_id: Optional[str] = None, agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
) -> dict:
    price = await get_pricing(product_id, seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)
    if price is None:
        raise ValueError(f"unknown product {product_id}")
    quote = {
        "quote_id": _next("q"), "status": "AVAILABLE",
        "product": {"product_id": product_id, "name": product_id},
        "pricing": {"final_cpm": price["final_price"], "base_cpm": price["base_cpm"]},
        "terms": {"impressions": impressions, "flight_start": flight_start, "flight_end": flight_end},
        "deal_type": deal_type, "buyer_tier": price["buyer_tier"],
    }
    _QUOTES[quote["quote_id"]] = quote
    return quote


@mcp.tool()
async def create_order(
    seat_id: Optional[str] = None, agency_id: Optional[str] = None, advertiser_id: Optional[str] = None,
) -> dict:
    order = {"execution_order_id": _next("o"), "status": "DRAFT", "lines": [],
             "buyer_tier": _tier(seat_id, agency_id, advertiser_id)}
    _ORDERS[order["execution_order_id"]] = order
    return order


@mcp.tool()
async def create_line(order_id: str, quote_id: str) -> dict:
    order = _ORDERS.get(order_id)
    if not order:
        raise ValueError(f"unknown order {order_id}")
    quote = _QUOTES.get(quote_id)
    if not quote:
        raise ValueError(f"unknown or expired quote {quote_id}")
    order["lines"].append({"proposal_line_id": _next("l"), "quote_id": quote_id,
                           "product_id": quote["product"]["product_id"], "pricing": quote["pricing"]})
    order["status"] = "BOOKED"
    return order


@mcp.tool()
async def create_deal(quote_id: str, notes: Optional[str] = None) -> dict:
    # rail 2, step 1: mint a PG Deal ID from an accepted quote (marks it BOOKED)
    quote = _QUOTES.get(quote_id)
    if not quote:
        raise ValueError(f"unknown or expired quote {quote_id}")
    if quote["status"] != "AVAILABLE":
        raise ValueError(f"quote {quote_id} status is {quote['status']!r}, expected 'available'")
    deal = {"deal_id": _next("d").upper(), "quote_id": quote_id, "status": "PROPOSED",
            "deal_type": quote["deal_type"], "product": quote["product"],
            "pricing": quote["pricing"], "buyer_tier": quote["buyer_tier"]}
    quote["status"] = "BOOKED"
    quote["deal_id"] = deal["deal_id"]
    _DEALS[deal["deal_id"]] = deal
    return deal


@mcp.tool()
async def distribute_deal_via_ssp(deal_id: str, ssp: str) -> dict:
    # rail 2, step 2: activate the Deal ID in an SSP (stubbed distribution record)
    deal = _DEALS.get(deal_id)
    if not deal:
        raise ValueError(f"unknown deal {deal_id}")
    if not ssp:
        raise ValueError("ssp is required to distribute a deal")
    distribution = {"deal_id": deal_id, "ssp_deal_id": _next("sd").upper(), "ssp_name": ssp,
                    "status": "active", "cpm": deal["pricing"]["final_cpm"], "currency": "USD"}
    deal["distribution"] = distribution
    return {"deal_id": deal_id, "distribution": distribution}


@mcp.tool()
async def submit_creative(order_id: str, asset_url: str, name: str = "creative") -> dict:
    if order_id not in _ORDERS:
        raise ValueError(f"unknown order {order_id}")
    return {"creative_id": _next("c"), "order_id": order_id, "name": name,
            "asset_url": asset_url, "status": "READY", "review_status": "APPROVED"}


@mcp.tool()
async def list_orders() -> list[dict]:
    return list(_ORDERS.values())


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.mount("/mcp", mcp.streamable_http_app())
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="Mock AAMP Seller", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "seller": "mock"}
