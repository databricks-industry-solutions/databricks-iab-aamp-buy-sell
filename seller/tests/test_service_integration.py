"""End-to-end SellerService against a seeded Lakebase (deterministic pricing,
card==quote, advertiser cheapest, discover->price->quote->book).

Run:  pytest -m integration   (needs creds + a seeded DB)
Prereq: ad_seller_dev seeded for the current generator (relational + kv_summit).
Locks the SDK-faithful demo numbers so they can't silently drift.
"""
import os

import pytest

from seller_mcp.config import Settings
from seller_mcp.service import SellerService

pytestmark = pytest.mark.integration

AGENCY = dict(agency_id="agcy-northstar")
ADVERTISER = dict(agency_id="agcy-northstar", advertiser_id="adv-northstar")


@pytest.fixture
async def svc():
    s = Settings(seller="summit", seller_database="ad_seller_dev",
                 databricks_profile=os.environ.get("AAMP_TEST_PROFILE"))
    service = await SellerService.create(s)
    yield service
    await service.close()


def _premium(pkgs):
    return next(p for p in pkgs if p["package_id"] == "pkg-ctv-premium")


async def test_public_sees_range_no_placements(svc):
    prem = _premium(await svc.list_packages())
    assert prem["price_range"] == "$32-$48 CPM"
    assert "exact_price" not in prem  # public view has no exact price / placements


async def test_card_equals_quote_and_advertiser_is_cheapest(svc):
    # card (media-kit exact_price) == quote (get_pricing) for authenticated tiers
    ag_card = _premium(await svc.list_packages(**AGENCY))["exact_price"]
    adv_card = _premium(await svc.list_packages(**ADVERTISER))["exact_price"]
    ag_quote = (await svc.get_pricing("prod-ctv-sports", **AGENCY))["final_price"]
    adv_quote = (await svc.get_pricing("prod-ctv-sports", **ADVERTISER))["final_price"]
    assert ag_card == ag_quote == 36.0
    assert adv_card == adv_quote == 32.0
    # SDK-faithful: advertiser (most identity) is cheapest
    assert adv_quote < ag_quote < 40.0


async def test_volume_discount_rides_on_top(svc):
    # 20M bracket = 15% off the agency rate
    assert (await svc.get_pricing("prod-ctv-sports", volume=20_000_000, **AGENCY))["final_price"] == 30.6


async def test_quote_then_book_then_creative(svc):
    q = await svc.request_quote(
        "prod-ctv-sports", deal_type="PD", impressions=1_000_000,
        flight_start="2026-03-01", flight_end="2026-03-31", **ADVERTISER,
    )
    assert q["pricing"]["final_cpm"] == 32.0
    assert q["availability"]["estimated_fill_rate"] == 0.95  # SDK stub (D11)

    order = await svc.create_order(**ADVERTISER)
    booked = await svc.create_line(order["execution_order_id"], q["quote_id"])
    assert booked["status"] == "BOOKED"
    assert booked["lines"][0]["product_id"] == "prod-ctv-sports"

    cr = await svc.submit_creative(order["execution_order_id"], asset_url="https://cdn/ad.mp4")
    assert cr["status"] == "READY"
    assert any(o["execution_order_id"] == order["execution_order_id"] for o in await svc.list_orders())


async def test_flight_dates_default_when_omitted(svc):
    # SDK-faithful: QuoteTerms requires flight start/end, so the seller fills an
    # open month-long window when the buyer omits them.
    q = await svc.request_quote("prod-ctv-sports", impressions=1_000_000, **ADVERTISER)
    assert q["terms"]["flight_start"] and q["terms"]["flight_end"]
    assert q["pricing"]["final_cpm"] == 32.0


async def test_target_cpm_honored_above_floor_rejected_below(svc):
    floor = float((await svc.storage.get_product("prod-ctv-sports"))["floor_cpm"])
    computed = (await svc.get_pricing("prod-ctv-sports", **ADVERTISER))["final_price"]  # 32.0
    assert floor < computed  # otherwise the demo product is mis-seeded

    # An offer between the floor and the computed price clears all floors -> honored.
    offer = round((floor + computed) / 2, 2)
    q = await svc.request_quote(
        "prod-ctv-sports", impressions=1_000_000, target_cpm=offer,
        flight_start="2026-03-01", flight_end="2026-03-31", **ADVERTISER,
    )
    assert q["pricing"]["final_cpm"] == offer

    # An offer below the global floor is rejected -> the deterministic rate stands.
    q2 = await svc.request_quote(
        "prod-ctv-sports", impressions=1_000_000, target_cpm=0.01,
        flight_start="2026-03-01", flight_end="2026-03-31", **ADVERTISER,
    )
    assert q2["pricing"]["final_cpm"] == computed


async def test_dsp_platform_accepted(svc):
    # dsp_platform is carried as buyer-identity metadata; it must not change the price.
    q = await svc.request_quote(
        "prod-ctv-sports", impressions=1_000_000, dsp_platform="ttd",
        flight_start="2026-03-01", flight_end="2026-03-31", **ADVERTISER,
    )
    assert q["pricing"]["final_cpm"] == 32.0


async def test_create_deal_then_double_create_fails(svc):
    # Rail 2, step 1: mint a PG Deal ID from an accepted quote.
    q = await svc.request_quote(
        "prod-ctv-sports", deal_type="PG", impressions=1_000_000,
        flight_start="2026-03-01", flight_end="2026-03-31", **ADVERTISER,
    )
    deal = await svc.create_deal(q["quote_id"])
    assert deal["deal_id"].startswith("DEMO-")
    assert deal["status"] == "proposed"  # DealBookingStatus.PROPOSED
    assert deal["quote_id"] == q["quote_id"]
    assert deal["openrtb_params"]["bidfloor"] == 32.0
    assert "ttd" in deal["activation_instructions"]

    # The quote is now BOOKED — minting again is rejected.
    with pytest.raises(ValueError):
        await svc.create_deal(q["quote_id"])

    # Retrievable + shows in the deals ledger.
    assert (await svc.get_deal(deal["deal_id"]))["deal_id"] == deal["deal_id"]
    assert any(d["deal_id"] == deal["deal_id"] for d in await svc.list_deals())


async def test_distribute_deal_via_ssp(svc):
    # Rail 2, step 2: activate the minted Deal ID in an SSP (stubbed).
    q = await svc.request_quote(
        "prod-ctv-sports", deal_type="PG", impressions=1_000_000,
        flight_start="2026-03-01", flight_end="2026-03-31", **ADVERTISER,
    )
    deal = await svc.create_deal(q["quote_id"])
    res = await svc.distribute_deal_via_ssp(deal["deal_id"], ssp="FreeWheel")
    dist = res["distribution"]
    assert dist["ssp_name"] == "FreeWheel"
    assert dist["ssp_deal_id"].startswith("FREE-")
    assert dist["status"] == "active"
    assert dist["cpm"] == 32.0
    # get_deal now carries the distribution.
    assert (await svc.get_deal(deal["deal_id"]))["distribution"]["ssp_name"] == "FreeWheel"

    # Distributing an unknown deal errors.
    with pytest.raises(ValueError):
        await svc.distribute_deal_via_ssp("DEMO-DOESNOTEXIST", ssp="Magnite")
