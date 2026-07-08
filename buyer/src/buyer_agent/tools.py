"""CrewAI tools for the buyer agents.

  * TaxonomyLookupTool — the audience-code gate.
  * Seller-MCP read tools — discover/open/price packages over a seller's /mcp/, wrapping
    `seller_client.SellerMCPClient`. These are read-only on purpose: the money calls
    (request_quote → create_order → create_line) run deterministically in `flow.py`
    behind the book-or-pass gate, so the LLM can never commit a booking above the
    ceiling. Identity (seat/agency/advertiser) is baked into each tool so the seller
    reveals the buyer's tier.
"""

from __future__ import annotations

import json
from typing import Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from .seller_client import SellerMCPClient, run_sync
from .taxonomy import audience_index


class _TaxonomyQuery(BaseModel):
    query: str = Field(
        ...,
        description="A free-text interest or audience description, e.g. 'sports fans' or 'auto intenders'.",
    )


class TaxonomyLookupTool(BaseTool):
    """The gate that stops the LLM inventing audience codes: it can only choose from
    codes this tool returns out of the real IAB Audience Taxonomy 1.1."""

    name: str = "iab_audience_taxonomy_lookup"
    description: str = (
        "Search the IAB Audience Taxonomy 1.1 for REAL audience codes matching a free-text "
        "interest. Returns up to 8 candidate {code, name} pairs as JSON. You MUST only use "
        "codes returned by this tool — never invent or guess a code."
    )
    args_schema: type[BaseModel] = _TaxonomyQuery

    def _run(self, query: str) -> str:
        idx = audience_index()
        if not idx.available:
            return ("TAXONOMY_UNAVAILABLE: the IAB Audience Taxonomy TSV is not vendored "
                    "(run `make taxonomies`). Propose well-known codes cautiously.")
        hits = idx.search(query, limit=8)
        if not hits:
            return f"No taxonomy matches for '{query}'. Try a simpler or broader term."
        return json.dumps(hits)


# ---- seller-MCP read tools (bound to one seller + the buyer's identity) ------

class _SellerTool(BaseTool):
    """Base for seller-MCP tools: holds the connection + identity, bridges async."""

    base_url: str
    mcp_path: str = "/mcp/"
    seat_id: Optional[str] = None
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None
    auth_token: Optional[str] = None

    def _client(self) -> SellerMCPClient:
        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else None
        return SellerMCPClient(self.base_url, mcp_path=self.mcp_path, headers=headers)

    def _ident(self) -> dict:
        return {"seat_id": self.seat_id, "agency_id": self.agency_id, "advertiser_id": self.advertiser_id}


class _DiscoverArgs(BaseModel):
    audience_id: Optional[str] = Field(None, description="Optional IAB audience code to filter packages by.")
    featured_only: bool = Field(False, description="Only featured packages.")


class DiscoverPackagesTool(_SellerTool):
    name: str = "discover_packages"
    description: str = (
        "List the seller's packages (optionally filtered by an IAB audience code). Returns JSON; "
        "authenticated buyers see exact rates and the products inside each package."
    )
    args_schema: type[BaseModel] = _DiscoverArgs

    def _run(self, audience_id: Optional[str] = None, featured_only: bool = False) -> str:
        async def _go():
            async with self._client() as c:
                return await c.list_packages(audience_id=audience_id, featured_only=featured_only, **self._ident())
        return json.dumps(run_sync(_go()))


class _OpenArgs(BaseModel):
    package_id: str = Field(..., description="The package_id to open.")


class OpenPackageTool(_SellerTool):
    name: str = "open_package"
    description: str = "Open one package by id to see its constituent products (placements) and audience codes."
    args_schema: type[BaseModel] = _OpenArgs

    def _run(self, package_id: str) -> str:
        async def _go():
            async with self._client() as c:
                return await c.get_package(package_id, **self._ident())
        return json.dumps(run_sync(_go()))


class _PricingArgs(BaseModel):
    product_id: str = Field(..., description="The product_id to price.")
    volume: int = Field(0, description="Optional impression volume for volume pricing.")


class ProductPricingTool(_SellerTool):
    name: str = "get_product_pricing"
    description: str = (
        "Get the seller's deterministic price for a product at the buyer's tier. Returns JSON with "
        "final_cpm. This is the ONLY source of price — never estimate a CPM yourself."
    )
    args_schema: type[BaseModel] = _PricingArgs

    def _run(self, product_id: str, volume: int = 0) -> str:
        async def _go():
            async with self._client() as c:
                return await c.get_pricing(product_id, volume=volume, **self._ident())
        return json.dumps(run_sync(_go()))


def seller_read_tools(
    base_url: str,
    *,
    mcp_path: str = "/mcp/",
    identity: Optional[dict] = None,
    auth_token: Optional[str] = None,
) -> list[BaseTool]:
    """The research agent's seller-MCP toolset, bound to one seller + the buyer identity."""
    ident = identity or {}
    common = dict(
        base_url=base_url,
        mcp_path=mcp_path,
        seat_id=ident.get("seat_id"),
        agency_id=ident.get("agency_id"),
        advertiser_id=ident.get("advertiser_id"),
        auth_token=auth_token,
    )
    return [DiscoverPackagesTool(**common), OpenPackageTool(**common), ProductPricingTool(**common)]
