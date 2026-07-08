"""Pricing must be deterministic, identity-driven, and CONSISTENT between the
media-kit view (get_price_display, tier_discount only) and get_pricing/quote
(calculate_price, tier + rules + volume). The builder reconciles the seed's
rule discounts with the SDK default tier_discounts so each discount applies
exactly once. Numbers below use the ctv-sports package base 40.0 / product
base_cpms 40 (pre-roll) and 44 (mid-roll).
"""
from ad_seller.models.buyer_identity import AccessTier

from seller_mcp.identity import agency_context, advertiser_context, public_context
from seller_mcp.pricing import build_pricing_engine

RULES = [
    {"theme": "ctv-sports", "rule_id": "rule-ctv-sports-A", "rule_name": "public (A)",
     "priority": 0, "access_tier": "public", "agency_ids": [], "advertiser_ids": [],
     "holding_company_ids": [], "product_ids": [], "inventory_types": [],
     "base_price_override": None, "discount_percentage": 0.0, "price_floor": None,
     "price_ceiling": None, "volume_discounts": [], "negotiation_enabled": False,
     "max_negotiation_discount": 0.0, "valid_from": None, "valid_to": None, "is_active": True},
    {"theme": "ctv-sports", "rule_id": "rule-ctv-sports-B", "rule_name": "agency (B)",
     "priority": 0, "access_tier": "agency", "agency_ids": ["agcy-northstar"], "advertiser_ids": [],
     "holding_company_ids": [], "product_ids": [], "inventory_types": [],
     "base_price_override": None, "discount_percentage": 0.10, "price_floor": None,
     "price_ceiling": None, "volume_discounts": [], "negotiation_enabled": False,
     "max_negotiation_discount": 0.0, "valid_from": None, "valid_to": None, "is_active": True},
    {"theme": "ctv-sports", "rule_id": "rule-ctv-sports-C", "rule_name": "advertiser (C)",
     "priority": 0, "access_tier": "advertiser", "agency_ids": [], "advertiser_ids": ["adv-acme"],
     "holding_company_ids": [], "product_ids": [], "inventory_types": [],
     "base_price_override": None, "discount_percentage": 0.15, "price_floor": None,
     "price_ceiling": None, "volume_discounts": [], "negotiation_enabled": False,
     "max_negotiation_discount": 0.0, "valid_from": None, "valid_to": None, "is_active": True},
]


def _engine():
    return build_pricing_engine(RULES, seller_org_id="seller-summit")


def test_public_sees_a_range_not_exact():
    d = _engine().get_price_display(40.0)  # no context -> PUBLIC
    assert d["type"] == "range"
    assert d["display"] == "$32-$48 CPM"


def test_agency_exact_price_is_consistent_view_vs_quote():
    e = _engine()
    ctx = agency_context(agency_id="agcy-northstar")
    assert ctx.effective_tier is AccessTier.AGENCY
    # media-kit view
    assert e.get_price_display(40.0, ctx) == {
        "type": "exact", "price": 36.0, "currency": "USD", "negotiation_enabled": True
    }
    # get_pricing / quote path agrees (no double discount)
    assert e.calculate_price("prod-sports-preroll", 40.0, ctx).final_price == 36.0


def test_agency_volume_discount_rides_on_top():
    e = _engine()
    ctx = agency_context(agency_id="agcy-northstar")
    # 36.0 * (1 - 0.05) for the 5M default bracket
    assert e.calculate_price("prod-sports-preroll", 40.0, ctx, volume=5_000_000).final_price == 34.2


def test_advertiser_gets_the_deepest_rate():
    e = _engine()
    ctx = advertiser_context(agency_id="agcy-northstar", advertiser_id="adv-acme")
    assert ctx.effective_tier is AccessTier.ADVERTISER
    assert e.get_price_display(40.0, ctx)["price"] == 34.0          # 40 * 0.85
    assert e.calculate_price("prod-sports-midroll", 44.0, ctx).final_price == 37.4  # 44 * 0.85
    # 20M bracket = 15% on top of the 34.0 advertiser rate
    assert e.calculate_price("prod-sports-preroll", 40.0, ctx, volume=20_000_000).final_price == 28.9


def test_no_double_discount_rule_lifted_to_tier():
    e = _engine()
    agency_rule = next(r for r in e.config.rules if r.access_tier is AccessTier.AGENCY)
    assert agency_rule.discount_percentage == 0.0  # neutralized
    assert e.config.get_tier_config(AccessTier.AGENCY).tier_discount == 0.10  # carried by tier
