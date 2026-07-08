"""The buyer's SellerMCPClient against a faithful mock seller.

Covers the buyer-side contract: trailing-slash connect, tool discovery, identity
arg passing (tier reveal), result parsing (list + dict returns), and the full
quote -> order -> line booking round-trip."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent.registry_client import SellerRef  # noqa: E402
from buyer_agent.seller_client import SellerMCPClient, SellerMCPError  # noqa: E402

_EXPECTED_TOOLS = {
    "list_packages", "get_package", "get_pricing", "request_quote",
    "create_order", "create_line", "submit_creative", "list_orders",
    "create_deal", "distribute_deal_via_ssp",
}


async def test_connect_and_discover_tools(seller_base_url):
    async with SellerMCPClient(seller_base_url) as c:
        tools = set(await c.list_tools())
    assert _EXPECTED_TOOLS <= tools, f"missing tools: {_EXPECTED_TOOLS - tools}"


async def test_list_packages_list_return(seller_base_url):
    async with SellerMCPClient(seller_base_url) as c:
        pkgs = await c.list_packages(agency_id="agcy-northstar")
        featured = await c.list_packages(featured_only=True)
    assert isinstance(pkgs, list) and pkgs and pkgs[0]["tier"] == "agency"
    assert all(p["is_featured"] for p in featured)


async def test_get_pricing_tier_reveal(seller_base_url):
    async with SellerMCPClient(seller_base_url) as c:
        public = await c.get_pricing("prod-ctv-sports")
        agency = await c.get_pricing("prod-ctv-sports", agency_id="agcy-northstar")
        advertiser = await c.get_pricing("prod-ctv-sports", advertiser_id="adv-northstar")
        missing = await c.get_pricing("prod-nope")
    assert public["final_price"] == 40.0 and public["buyer_tier"] == "public"
    assert agency["final_price"] == 36.0           # 10% off → falls under the $38 ceiling
    assert advertiser["final_price"] == 32.0
    assert missing is None


async def test_quote_order_line_roundtrip(seller_base_url):
    async with SellerMCPClient(seller_base_url) as c:
        quote = await c.request_quote("prod-ctv-sports", impressions=500000, agency_id="agcy-northstar")
        order = await c.create_order(agency_id="agcy-northstar")
        booked = await c.create_line(order["execution_order_id"], quote["quote_id"])
        orders = await c.list_orders()
    assert quote["status"] == "AVAILABLE"
    assert booked["status"] == "BOOKED" and booked["lines"][0]["quote_id"] == quote["quote_id"]
    assert any(o["execution_order_id"] == order["execution_order_id"] for o in orders)


async def test_quote_deal_ssp_roundtrip(seller_base_url):
    """Rail 2: quote → create_deal (PG Deal ID) → distribute_deal_via_ssp."""
    async with SellerMCPClient(seller_base_url) as c:
        quote = await c.request_quote("prod-ctv-sports", impressions=500000, agency_id="agcy-northstar")
        deal = await c.create_deal(quote["quote_id"])
        dist = await c.distribute_deal_via_ssp(deal["deal_id"], "FreeWheel")
    assert deal["quote_id"] == quote["quote_id"] and deal["deal_id"]
    assert dist["deal_id"] == deal["deal_id"]
    assert dist["distribution"]["ssp_name"] == "FreeWheel"
    assert dist["distribution"]["status"] == "active"


async def test_deal_requires_available_quote(seller_base_url):
    """A quote can only be committed once — re-minting a deal from a BOOKED quote fails."""
    async with SellerMCPClient(seller_base_url) as c:
        quote = await c.request_quote("prod-ctv-sports", impressions=100000, agency_id="agcy-northstar")
        await c.create_deal(quote["quote_id"])
        with pytest.raises(SellerMCPError):
            await c.create_deal(quote["quote_id"])


async def test_tool_error_surfaces(seller_base_url):
    async with SellerMCPClient(seller_base_url) as c:
        with pytest.raises(SellerMCPError):
            await c.create_line("o-nope", "q-nope")


def test_for_seller_requires_url():
    unreachable = SellerRef(agent_id="seller-x", name="X", capability="ctv",
                            trust_level="APPROVED", base_url=None, registry_url="https://x.example")
    with pytest.raises(SellerMCPError):
        SellerMCPClient.for_seller(unreachable)
