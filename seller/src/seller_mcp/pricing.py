"""Build the SDK PricingRulesEngine from the seed's `pricing_rules` rows.

Reconciliation of the double-discount (see seller/README.md):
the SDK applies `tier_discount` (in `calculate_price` AND the media-kit
`get_price_display`) and, separately, a matching rule's `discount_percentage`
(in `calculate_price` only). The seed encodes the per-identity discount on the
RULE, but the SDK default tiers ALSO carry it (agency 0.10 / advertiser 0.15),
and the media-kit view reads ONLY `tier_discount`. To make the package view,
get_pricing, and the quote all agree — and to apply the discount exactly once —
we lift each rule's discount onto its tier's `tier_discount`, then zero the rule
discount. Volume discounts still ride on top via the engine's default schedule.
"""

from __future__ import annotations

from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.pricing_tiers import PricingRule, TieredPricingConfig


def build_pricing_engine(
    rule_rows: list[dict],
    *,
    seller_org_id: str,
    reconcile_double_discount: bool = True,
) -> PricingRulesEngine:
    """Construct a PricingRulesEngine from seeded `pricing_rules` rows.

    The config uses the SDK's default tiers (PUBLIC ranges; SEAT/AGENCY/ADVERTISER
    exact + volume discounts on AGENCY/ADVERTISER), with each tier's discount set
    from the seed rule that targets it. Seed rows carry a non-SDK `seller` column
    (the relational partition key) which we drop; everything else maps 1:1 onto
    PricingRule, whose enums the seed now emits correctly.
    """
    rules = [PricingRule(**{k: v for k, v in r.items() if k != "seller"}) for r in rule_rows]
    config = TieredPricingConfig(seller_organization_id=seller_org_id, rules=rules)

    if reconcile_double_discount:
        # Lift each rule's discount onto its tier (the value the seed intends and
        # the media-kit view reads), taking the max if several rules share a tier.
        per_tier_discount: dict = {}
        for rule in rules:
            if rule.access_tier is not None and rule.discount_percentage > 0:
                prev = per_tier_discount.get(rule.access_tier, 0.0)
                per_tier_discount[rule.access_tier] = max(prev, rule.discount_percentage)
        for tier, discount in per_tier_discount.items():
            config.get_tier_config(tier).tier_discount = discount
        # Tiers the seed does NOT rule pay rack: zero the SDK's default tier discounts
        # (SEAT ships with 5% off) so the seed is the single source of discount truth —
        # identification alone unlocks exact prices, not a discount.
        from ad_seller.models.pricing_tiers import AccessTier

        for tier in (AccessTier.SEAT, AccessTier.AGENCY, AccessTier.ADVERTISER):
            if tier not in per_tier_discount:
                config.get_tier_config(tier).tier_discount = 0.0
        # Neutralize rule discounts so they never double-apply in calculate_price.
        for rule in rules:
            rule.discount_percentage = 0.0

    return PricingRulesEngine(config)
