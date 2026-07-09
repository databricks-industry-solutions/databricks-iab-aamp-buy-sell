"""Shared buyer data models (Pydantic).

`AudiencePlan` is the L3 audience_planner's structured output: per brief interest,
the validated IAB Audience Taxonomy 1.1 codes the downstream channel crews use to
filter each seller's packages (the sellers declare the same `standard_segment_ids`).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class TaxonomyCode(BaseModel):
    code: str
    name: str = ""


class AudienceSegment(BaseModel):
    interest: str                       # the brief's free-text term, e.g. "sports fans"
    audience_type: str = "standard"     # IAB standard taxonomy (what the sellers expose)
    codes: list[TaxonomyCode] = Field(default_factory=list)


class AudiencePlan(BaseModel):
    brief_id: Optional[str] = None
    segments: list[AudienceSegment] = Field(default_factory=list)
    notes: Optional[str] = None

    def all_codes(self) -> list[str]:
        return sorted({c.code for s in self.segments for c in s.codes})


# ---- crew outputs + per-channel transaction results ------------------------

class ChannelRecommendation(BaseModel):
    """The per-channel crew's structured output (L2 manager over L3 research/execution):
    the single product to pursue and why. The buyer never proposes a price — `final_cpm`
    is what the seller's get_pricing returned, echoed for the deterministic gate."""

    product_id: str = ""
    product_name: str = ""
    final_cpm: Optional[float] = None
    deal_type: str = "PD"
    audience_codes: list[str] = Field(default_factory=list)
    rationale: str = ""


class ChannelAllocation(BaseModel):
    """One channel's budget slice (anchored to the brief's budget_pct; the L1 crew
    justifies it rather than inventing it)."""

    channel: str                 # brief ChannelType, e.g. "CTV"
    capability: str              # registry capability token, e.g. "ctv"
    budget_pct: float = 0.0
    budget: float = 0.0
    rationale: str = ""


class ChannelOutcome(BaseModel):
    """The deterministic result of booking-or-passing one channel."""

    channel: str
    capability: str
    decision: str                # BOOKED | PASSED | NO_SELLER | NO_PRODUCT | NO_ACCESS
    reason: str = ""
    seller_id: Optional[str] = None
    seller_name: Optional[str] = None
    product_id: Optional[str] = None
    final_cpm: Optional[float] = None
    impressions: Optional[int] = None
    channel_budget: float = 0.0
    quote_id: Optional[str] = None
    order_id: Optional[str] = None   # rail 1 (direct IO): the OpenDirect execution order
    creative_id: Optional[str] = None      # rail 1: the submitted creative
    creative_status: Optional[str] = None  # rail 1: READY once auto-approved (design D7)
    card: Optional[dict] = None  # seller rate-card math at this buyer's tier (base → discounts → final + rationale)
    activation_instructions: Optional[dict] = None  # rail 2: per-DSP deal-activation text from the seller
    deal_id: Optional[str] = None    # rail 2 (programmatic): the PG Deal ID the seller minted
    ssp: Optional[str] = None        # rail 2: the SSP the deal was distributed to


class RunResult(BaseModel):
    """The POST /run response: the full L1→L2→L3 outcome for a brief."""

    brief_id: str
    buyer_id: str
    buyer_tier: str = "public"   # effective tier the buyer presented (gated by registry trust)
    audience_plan: AudiencePlan
    allocations: list[ChannelAllocation] = Field(default_factory=list)
    outcomes: list[ChannelOutcome] = Field(default_factory=list)

    def booked(self) -> list[ChannelOutcome]:
        return [o for o in self.outcomes if o.decision == "BOOKED"]
