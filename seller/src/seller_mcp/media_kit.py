"""RuleAwareMediaKitService — the SDK MediaKitService with a deterministic,
identity-aware authenticated price.

The SDK's `_to_authenticated_view` prices the card via `get_price_display`, which
only applies the tier percentage and is blind to PricingRules (per-identity rates,
flat overrides). That makes the discovery card disagree with the quote. Our demo is
fully DETERMINISTIC with NO negotiation: a buyer must see their effective rate on
the card itself, identical to what they'd be quoted. So we recompute the
authenticated `exact_price` with the SAME `calculate_price` the quote uses — the
card already shows the buyer's pre-set rate. Public stays a range (unknown buyer,
no identity-specific rate). We also clear `negotiation_enabled` — nothing is negotiated.
"""

from __future__ import annotations

from typing import Optional

from ad_seller.engines.media_kit_service import MediaKitService
from ad_seller.models.buyer_identity import BuyerContext
from ad_seller.models.media_kit import AuthenticatedPackageView, Package


class RuleAwareMediaKitService(MediaKitService):
    def __init__(self, storage, pricing_engine, *, inventory_type: Optional[str] = None) -> None:
        super().__init__(storage, pricing_engine)
        # the seller's channel (e.g. "ctv"/"linear"); lets identity rules that are
        # inventory-scoped match when pricing the package card.
        self._inventory_type = inventory_type

    def _to_authenticated_view(
        self, package: Package, buyer_context: BuyerContext
    ) -> AuthenticatedPackageView:
        view = super()._to_authenticated_view(package, buyer_context)
        decision = self._pricing.calculate_price(
            product_id=package.package_id,
            base_price=package.base_price,
            buyer_context=buyer_context,
            inventory_type=self._inventory_type,
        )
        # Card shows the buyer's deterministic effective rate (== the quote).
        view.exact_price = decision.final_price
        view.price_range = f"${decision.final_price:.0f} CPM"
        view.negotiation_enabled = False  # deterministic demo: nothing is negotiated
        return view
