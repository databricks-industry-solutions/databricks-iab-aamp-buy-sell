"""Build the SDK's `BuyerContext` from buyer-supplied identity + a tier ceiling.

In the full system the tier ceiling comes from the registry (trust -> tier via
TRUST_TO_TIER_MAP); the buyer's *claimed* tier comes from the identity fields it
reveals (seat -> agency -> advertiser). The seller's effective tier is the min of
the two. For the seller-only build the ceiling is passed in (the registry slots in
later); the MCP tools construct the context from their identity params.
"""

from __future__ import annotations

from typing import Optional

from ad_seller.models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity


def public_context() -> BuyerContext:
    """An anonymous buyer — PUBLIC tier (ranges only, no segment lists)."""
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


def build_buyer_context(
    *,
    seat_id: Optional[str] = None,
    agency_id: Optional[str] = None,
    agency_name: Optional[str] = None,
    agency_holding_company: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    advertiser_name: Optional[str] = None,
    dsp_platform: Optional[str] = None,
    max_access_tier: AccessTier = AccessTier.ADVERTISER,
    authenticated: bool = True,
) -> BuyerContext:
    """Build an authenticated BuyerContext. `max_access_tier` is the registry
    ceiling; the claimed tier is derived by the SDK from which ids are present,
    and `effective_tier = min(claimed, ceiling)`. `dsp_platform` (ttd/dv360/…) is
    the SDK's buyer-identity metadata — carried, not a pricing lever."""
    identity = BuyerIdentity(
        seat_id=seat_id,
        agency_id=agency_id,
        agency_name=agency_name,
        agency_holding_company=agency_holding_company,
        advertiser_id=advertiser_id,
        advertiser_name=advertiser_name,
        dsp_platform=dsp_platform,
    )
    return BuyerContext(
        identity=identity,
        is_authenticated=authenticated,
        max_access_tier=max_access_tier,
    )


def agency_context(*, agency_id: str, agency_name: Optional[str] = None) -> BuyerContext:
    """Convenience: an authenticated agency buyer (claims AGENCY)."""
    return build_buyer_context(
        agency_id=agency_id, agency_name=agency_name, max_access_tier=AccessTier.ADVERTISER
    )


def advertiser_context(
    *, advertiser_id: str, agency_id: Optional[str] = None,
    advertiser_name: Optional[str] = None,
) -> BuyerContext:
    """Convenience: an authenticated advertiser buyer (claims ADVERTISER)."""
    return build_buyer_context(
        agency_id=agency_id, advertiser_id=advertiser_id, advertiser_name=advertiser_name,
        max_access_tier=AccessTier.ADVERTISER,
    )
