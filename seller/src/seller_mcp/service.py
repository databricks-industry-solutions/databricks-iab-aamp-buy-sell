"""SellerService — the single deterministic logic layer.

Every buyer-facing operation lives here exactly once; the HTTP (REST) routes and
the MCP tools are both thin faces over these methods (no logic duplicated). It
wires the SDK engines (MediaKitService + PricingRulesEngine) over the kv store and
turns buyer-supplied identity into an SDK BuyerContext.

Discovery + pricing are pure SDK code paths. Quotes use the SDK quote models;
availability is the SDK QuoteAvailability stub (no ad server — design D11). Booking
auto-books (Phase 1) and persists an OpenDirect-shaped order to the kv store.

Tier seam: `resolve_tier_ceiling` is where the registry plugs in (verified trust ->
TRUST_TO_TIER_MAP); until then the ceiling is ADVERTISER and the buyer's revealed
identity drives the effective tier (anon->PUBLIC, agency_id->AGENCY, advertiser_id->ADVERTISER).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

from ad_seller.engines.media_kit_service import AudienceFilter
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine

from .media_kit import RuleAwareMediaKitService
from ad_seller.models.buyer_identity import AccessTier, BuyerContext
from ad_seller.models.core import DealType
from ad_seller.models.quotes import (
    DealBookingResponse,
    DealBookingStatus,
    QuoteAvailability,
    QuotePricing,
    QuoteProductInfo,
    QuoteResponse,
    QuoteStatus,
    QuoteTerms,
)

from .config import Settings, get_settings
from .db import fetch_audiences, fetch_pricing_rules, shared_kv_store
from .identity import build_buyer_context, public_context
from .pricing import build_pricing_engine

_DEAL_TYPE = {
    "PG": DealType.PROGRAMMATIC_GUARANTEED,
    "PD": DealType.PREFERRED_DEAL,
    "PA": DealType.PRIVATE_AUCTION,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _first_product_id(obj) -> Optional[str]:
    """First product_id found anywhere in a package view (placements/products vary)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "product_id" and isinstance(v, str):
                return v
            found = _first_product_id(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _first_product_id(v)
            if found:
                return found
    return None


class SellerService:
    def __init__(
        self,
        storage: LakebaseKVStore,
        pricing: PricingRulesEngine,
        media_kit: MediaKitService,
        settings: Settings,
    ) -> None:
        self.storage = storage
        self.pricing = pricing
        self.media_kit = media_kit
        self.settings = settings

    @classmethod
    async def create(cls, settings: Optional[Settings] = None) -> "SellerService":
        settings = settings or get_settings()
        # Reuse the shared, long-lived kv pool (transport); the pricing engine + catalog
        # below are still read FRESH from Lakebase on every create() — no data cache.
        storage = await shared_kv_store(settings)
        engine = build_pricing_engine(fetch_pricing_rules(settings), seller_org_id=settings.org_id)
        # Derive the seller's channel from its own products so identity rules that
        # are inventory-scoped match when pricing the (deterministic) package card.
        products = await storage.list_products()
        inventory_type = products[0]["inventory_type"] if products else None
        media_kit = RuleAwareMediaKitService(storage, engine, inventory_type=inventory_type)
        return cls(storage, engine, media_kit, settings)

    async def close(self) -> None:
        # No-op: the kv pool is shared/long-lived (managed in db.py), not torn down
        # per request. It drains itself after 30 min idle.
        return None

    # ----- identity / tier seam -----------------------------------------

    def resolve_tier_ceiling(self, **identity) -> AccessTier:
        """Ceiling the buyer's tier can reach. The registry overrides this with
        verified trust -> TRUST_TO_TIER_MAP; for now, no cap."""
        return AccessTier.ADVERTISER

    def make_context(
        self,
        *,
        seat_id: Optional[str] = None,
        agency_id: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        dsp_platform: Optional[str] = None,
    ) -> BuyerContext:
        # dsp_platform is identity metadata (which DSP the buyer trades through),
        # not a tier signal — it never elevates an anonymous buyer above PUBLIC.
        if not any([seat_id, agency_id, advertiser_id]):
            return public_context()
        ceiling = self.resolve_tier_ceiling(
            seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id
        )
        return build_buyer_context(
            seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id,
            dsp_platform=dsp_platform, max_access_tier=ceiling,
        )

    # ----- discovery -----------------------------------------------------

    async def list_packages(
        self,
        *,
        audience_id: Optional[str] = None,
        audience_type: str = "standard",
        featured_only: bool = False,
        seat_id: Optional[str] = None,
        agency_id: Optional[str] = None,
        advertiser_id: Optional[str] = None,
    ) -> list[dict]:
        ctx = self.make_context(seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)
        af = AudienceFilter(audience_type=audience_type, audience_id=audience_id) if audience_id else None
        if ctx.effective_tier == AccessTier.PUBLIC:
            views = await self.media_kit.list_packages_public(featured_only=featured_only, audience_filter=af)
        else:
            views = await self.media_kit.list_packages_authenticated(ctx, audience_filter=af)
        return [v.model_dump(mode="json") for v in views]

    async def get_package(
        self, package_id: str, *, seat_id=None, agency_id=None, advertiser_id=None
    ) -> Optional[dict]:
        ctx = self.make_context(seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)
        if ctx.effective_tier == AccessTier.PUBLIC:
            view = await self.media_kit.get_package_public(package_id)
        else:
            view = await self.media_kit.get_package_authenticated(package_id, ctx)
        return view.model_dump(mode="json") if view else None

    # ----- pricing -------------------------------------------------------

    async def get_pricing(
        self, product_id: str, *, volume: int = 0, seat_id=None, agency_id=None,
        advertiser_id=None, dsp_platform=None,
    ) -> Optional[dict]:
        product = await self.storage.get_product(product_id)
        if not product:
            return None
        ctx = self.make_context(
            seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id, dsp_platform=dsp_platform
        )
        decision = self.pricing.calculate_price(
            product_id=product_id,
            base_price=float(product["base_cpm"]),
            buyer_context=ctx,
            volume=volume,
            inventory_type=product.get("inventory_type"),
        )
        return decision.model_dump(mode="json")

    # ----- quote ---------------------------------------------------------

    async def request_quote(
        self,
        product_id: str,
        *,
        deal_type: str = "PD",
        impressions: Optional[int] = None,
        flight_start: Optional[str] = None,
        flight_end: Optional[str] = None,
        target_cpm: Optional[float] = None,
        seat_id=None, agency_id=None, advertiser_id=None, dsp_platform=None,
    ) -> dict:
        product = await self.storage.get_product(product_id)
        if not product:
            raise ValueError(f"unknown product {product_id}")
        min_imp = product.get("minimum_impressions")
        if impressions is not None and min_imp is not None and impressions < int(min_imp):
            raise ValueError(f"impressions {impressions} below product minimum {min_imp}")

        ctx = self.make_context(
            seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id, dsp_platform=dsp_platform
        )
        decision = self.pricing.calculate_price(
            product_id=product_id,
            base_price=float(product["base_cpm"]),
            buyer_context=ctx,
            deal_type=_DEAL_TYPE.get(deal_type, DealType.PREFERRED_DEAL),
            volume=impressions or 0,
            inventory_type=product.get("inventory_type"),
        )

        # SDK-faithful target_cpm (see ad_seller interfaces/api/main.py): the seller
        # computes its deterministic price; if the buyer offered a target CPM that
        # clears the floors (global + product + tier negotiation band), the seller
        # HONORS the buyer's offer. Deterministic floor check — not a negotiation.
        final_cpm = decision.final_price
        if target_cpm is not None:
            acceptable, _ = self.pricing.is_price_acceptable(
                offered_price=target_cpm,
                product_floor=float(product.get("floor_cpm", 0.0)),
                buyer_context=ctx,
            )
            if acceptable:
                final_cpm = target_cpm

        # SDK-faithful flight defaults: QuoteTerms.flight_start/end are required
        # strings, so default an open month-long window when the buyer omits them
        # (matches the SDK reference API: start=today, end=today+30d).
        now = _now()
        flight_start = flight_start or now.strftime("%Y-%m-%d")
        flight_end = flight_end or (now + timedelta(days=30)).strftime("%Y-%m-%d")

        quote = QuoteResponse(
            quote_id=_id("qt"),
            status=QuoteStatus.AVAILABLE,
            product=QuoteProductInfo(
                product_id=product_id, name=product.get("name", product_id),
                inventory_type=product.get("inventory_type"),
            ),
            pricing=QuotePricing(
                base_cpm=float(product["base_cpm"]),
                tier_discount_pct=round(decision.tier_discount * 100, 1),
                volume_discount_pct=round(decision.volume_discount * 100, 1),
                final_cpm=final_cpm,
                rationale=decision.rationale,
            ),
            terms=QuoteTerms(
                impressions=impressions, flight_start=flight_start, flight_end=flight_end,
                guaranteed=(deal_type == "PG"),
            ),
            availability=QuoteAvailability(),  # SDK stub (D11): no ad server
            deal_type=deal_type,
            buyer_tier=ctx.effective_tier.value,
            expires_at=(now + timedelta(hours=24)).isoformat(),
            seller_id=self.settings.org_id,
            created_at=now.isoformat(),
        )
        # set_quote -> kvstore.set validates against QuoteResponse at insert time
        await self.storage.set_quote(quote.quote_id, quote.model_dump(mode="json"))
        return quote.model_dump(mode="json")

    async def get_quote(self, quote_id: str) -> Optional[dict]:
        return await self.storage.get_quote(quote_id)

    # ----- rail 2: programmatic (PG Deal ID → SSP) ----------------------

    async def create_deal(self, quote_id: str, *, notes: Optional[str] = None) -> dict:
        """Rail 2, step 1 (Sell-Side Map): mint a PG **Deal ID** from an accepted quote.
        The deal_id is a supply-side construct the seller issues and hands to the buyer,
        who references it downstream (in its DSP) to transact the agreed terms. Marks the
        quote BOOKED so it can't be re-committed. Distribution to an SSP is the separate
        `distribute_deal_via_ssp` step. (SDK Deals API v1.0; net-new MCP per the map.)"""
        quote = await self.storage.get_quote(quote_id)
        if not quote:
            raise ValueError(f"unknown or expired quote {quote_id}")

        # Lazy expiry check (mirrors the SDK): a lapsed quote can't be booked.
        expires_at = quote.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at.rstrip("Z"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
            except ValueError:
                exp = None
            if exp and _now() > exp:
                quote["status"] = QuoteStatus.EXPIRED.value
                await self.storage.set_quote(quote_id, quote)
                raise ValueError(f"quote {quote_id} has expired — request a new quote")

        if quote.get("status") != QuoteStatus.AVAILABLE.value:
            raise ValueError(
                f"quote {quote_id} status is {quote.get('status')!r}, expected 'available'"
            )

        now = _now()
        deal_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"
        deal = DealBookingResponse(
            deal_id=deal_id,
            deal_type=quote["deal_type"],
            status=DealBookingStatus.PROPOSED,
            quote_id=quote_id,
            product=QuoteProductInfo(**quote["product"]),
            pricing=QuotePricing(**quote["pricing"]),
            terms=QuoteTerms(**quote["terms"]),
            buyer_tier=quote.get("buyer_tier", "public"),
            expires_at=(now + timedelta(days=30)).isoformat(),
            activation_instructions={
                "ttd": f"In The Trade Desk, create a new PMP deal with Deal ID: {deal_id}",
                "dv360": f"In DV360, add deal {deal_id} under Inventory > My Inventory > Deals",
                "xandr": f"In Xandr, navigate to Deals and enter Deal ID: {deal_id}",
            },
            openrtb_params={
                "id": deal_id,
                "bidfloor": quote["pricing"]["final_cpm"],
                "bidfloorcur": "USD",
                "at": 3 if quote["deal_type"] == "PA" else 1,
                "wseat": [],
            },
            created_at=now.isoformat(),
        )
        # set_deal -> kvstore.set validates against DealBookingResponse at insert time
        data = deal.model_dump(mode="json")
        await self.storage.set_deal(deal_id, data)
        # Commit point: bind the quote to the deal so it can't be re-committed.
        quote["status"] = QuoteStatus.BOOKED.value
        quote["deal_id"] = deal_id
        await self.storage.set_quote(quote_id, quote)
        return data

    async def distribute_deal_via_ssp(self, deal_id: str, *, ssp: str) -> dict:
        """Rail 2, step 2 (Sell-Side Map): activate a minted Deal ID in an SSP so the
        buyer's DSP can serve against it. The live SSP call is stubbed (no real SSP);
        we record an SSPDeal-shaped distribution (mirrors
        `ad_seller.clients.ssp_base.SSPDeal`) keyed off the deal, and return it."""
        deal = await self.storage.get_deal(deal_id)
        if not deal:
            raise ValueError(f"unknown deal {deal_id}")
        if not ssp:
            raise ValueError("ssp is required to distribute a deal")
        now = _now()
        # The SSP mints its own deal id; our PG deal_id is the seller-side reference.
        ssp_deal_id = f"{''.join(ch for ch in ssp.upper() if ch.isalnum())[:4] or 'SSP'}-{uuid.uuid4().hex[:10].upper()}"
        distribution = {  # SSPDeal-shaped (deal_id/status/ssp_name/cpm/currency)
            "deal_id": deal_id,
            "ssp_deal_id": ssp_deal_id,
            "ssp_name": ssp,
            "deal_type": "pmp",
            "status": "active",  # SSPDealStatus — stub: activated on distribution
            "cpm": deal.get("pricing", {}).get("final_cpm"),
            "currency": "USD",
            "distributed_at": now.isoformat(),
        }
        # `dealdist:` has no SDK model in the kv shim → stored unvalidated (not a
        # DealBookingResponse). One distribution per deal for the demo.
        await self.storage.set(f"dealdist:{deal_id}", distribution)
        return {"deal_id": deal_id, "distribution": distribution}

    async def get_deal(self, deal_id: str) -> Optional[dict]:
        deal = await self.storage.get_deal(deal_id)
        if deal:
            dist = await self.storage.get(f"dealdist:{deal_id}")
            if dist:
                deal["distribution"] = dist
        return deal

    async def list_deals(self) -> list[dict]:
        deals = await self.storage.list_deals()
        for d in deals:
            dist = await self.storage.get(f"dealdist:{d.get('deal_id')}")
            if dist:
                d["distribution"] = dist  # in-memory enrich (read-side only)
        return deals

    async def list_quotes(self) -> list[dict]:
        return await self.storage.list_quotes()

    # ----- booking (auto-books in Phase 1; OpenDirect-shaped order in kv) -----

    async def create_order(self, *, seat_id=None, agency_id=None, advertiser_id=None) -> dict:
        ctx = self.make_context(seat_id=seat_id, agency_id=agency_id, advertiser_id=advertiser_id)
        order = {
            "execution_order_id": _id("io"),
            "seller_organization_id": self.settings.org_id,
            "buyer_tier": ctx.effective_tier.value,
            "buyer_identity": {"seat_id": seat_id, "agency_id": agency_id, "advertiser_id": advertiser_id},
            "status": "DRAFT",
            "lines": [],
            "created_at": _now().isoformat(),
        }
        await self.storage.set_order(order["execution_order_id"], order)
        return order

    async def create_line(self, order_id: str, quote_id: str) -> dict:
        order = await self.storage.get_order(order_id)
        if not order:
            raise ValueError(f"unknown order {order_id}")
        quote = await self.storage.get_quote(quote_id)
        if not quote:
            raise ValueError(f"unknown or expired quote {quote_id}")
        line = {
            "proposal_line_id": _id("line"),
            "product_id": quote["product"]["product_id"],
            "deal_type": quote["deal_type"],
            "pricing": quote["pricing"],
            "delivery_goal": {"goal_type": "impressions", "goal_amount": quote["terms"].get("impressions")},
            "flight_start": quote["terms"].get("flight_start"),
            "flight_end": quote["terms"].get("flight_end"),
            "quote_id": quote_id,
        }
        order["lines"].append(line)
        order["status"] = "BOOKED"  # Phase 1 auto-books (D14)
        order["booked_at"] = _now().isoformat()
        await self.storage.set_order(order_id, order)
        return order

    async def submit_creative(
        self, order_id: str, *, asset_url: str, name: str = "creative", mimetype: str = "video/mp4"
    ) -> dict:
        order = await self.storage.get_order(order_id)
        if not order:
            raise ValueError(f"unknown order {order_id}")
        creative = {
            "creative_id": _id("cr"), "order_id": order_id, "name": name,
            "asset_url": asset_url, "mimetype": mimetype,
            "review_status": "APPROVED", "status": "READY",  # auto-approve (design D7)
            "created_at": _now().isoformat(),
        }
        await self.storage.set(f"creative:{creative['creative_id']}", creative)
        order.setdefault("creatives", []).append(creative["creative_id"])
        await self.storage.set_order(order_id, order)
        return creative

    async def list_orders(self) -> list[dict]:
        return await self.storage.list_orders()

    async def list_audiences(self) -> list[dict]:
        """The seller's audience catalog (names/sizes/IAB ids from aamp.audiences) —
        the kv package objects only carry segment-id codes; this resolves them."""
        return fetch_audiences(self.settings)

    # ----- pricing aggregates (one round-trip; parallel server-side) --------

    async def tier_spread(self, product_id: str, *, agency_id=None, advertiser_id=None) -> list[dict]:
        """The public→agency→advertiser price spread for one product, computed FRESH
        via the SDK pricing engine (parallel). Each row carries the full calculation
        (base, tier/volume discount, final) + the SDK rationale/applied_rules."""
        tiers = [
            ("public", {}),
            ("agency", {"agency_id": agency_id}),
            ("advertiser", {"agency_id": agency_id, "advertiser_id": advertiser_id}),
        ]
        decisions = await asyncio.gather(
            *[self.get_pricing(product_id, **ident) for _, ident in tiers]
        )
        return [
            {
                "tier": key,
                "final_price": d["final_price"],
                "base_price": d.get("base_price"),
                "tier_discount": d.get("tier_discount"),
                "volume_discount": d.get("volume_discount"),
                "rationale": d.get("rationale"),
                "applied_rules": d.get("applied_rules"),
            }
            for (key, _), d in zip(tiers, decisions)
        ]

    async def overview(self, *, agency_id=None, advertiser_id=None) -> dict:
        """One aggregated payload for the cockpit dashboard so the client makes a
        single round-trip instead of ~6. Queries run parallel server-side; data is
        read fresh (no cache). Returns packages, the bookings ledger, and the
        public→agency→advertiser tier spread for a flagship product."""
        packages, orders = await asyncio.gather(
            self.list_packages(agency_id=agency_id, advertiser_id=advertiser_id),
            self.list_orders(),
        )
        spread: list[dict] = []
        flagship: Optional[str] = None
        if packages:
            detail = await self.get_package(
                packages[0]["package_id"], agency_id=agency_id, advertiser_id=advertiser_id
            )
            flagship = _first_product_id(detail) or _first_product_id(packages[0])
            if flagship:
                spread = await self.tier_spread(flagship, agency_id=agency_id, advertiser_id=advertiser_id)
        return {"packages": packages, "orders": orders, "tier_spread": spread, "flagship_product": flagship}


@asynccontextmanager
async def service_scope(settings: Optional[Settings] = None) -> AsyncIterator["SellerService"]:
    """A fresh SellerService for the lifetime of ONE request — nothing is cached.

    Every entry re-reads `aamp.pricing_rules` and the catalog straight from
    Lakebase and rebuilds the pricing engine, so a price edited in the DB shows on
    the very next call. The CONNECTIONS are pooled and reused across requests (see
    db.shared_kv_store / fetch_pricing_rules) — only the data is fresh, not the
    socket. If the endpoint is scaled to zero, the first connect blocks while it
    wakes — the request waits, it does not fail. An empty schema yields empty results.
    """
    svc = await SellerService.create(settings)
    try:
        yield svc
    finally:
        await svc.close()
