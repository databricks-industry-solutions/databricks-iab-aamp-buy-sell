"""Tier-gated identity — present only the identity the chosen profile permits.

The buyer holds a full seat/agency/advertiser identity in
`ad_buyer.aamp.buyer_identities`, and presents a cumulative subset of it based on a
tier. For the demo the tier is a per-run choice (the UI "present as" selector →
`resolve_identity(..., tier=...)`); with no explicit choice it falls back to the
buyer's registry trust — the registry app (`/registry/verify` → `max_access_tier`) or,
in the Lakebase fallback, `agent_registry.aamp.registry_trust` mapped via `TRUST_TO_TIER`.
The seller computes `effective_tier = min(claimed, its own ceiling)`, so the presented
identity drives the tier — which moves the seller's price under/over the brief's
`max_cpm`, i.e. it drives book-or-pass.

Cumulative reveal mirrors the seller's claim logic:
  public     → present nothing (anonymous)
  seat       → seat_id
  agency     → seat_id + agency_id
  advertiser → seat_id + agency_id + advertiser_id
"""

from __future__ import annotations

from typing import Optional

from .config import Settings

# trust_status → tier; matches the SDK's TRUST_TO_TIER_MAP (no AGENCY tier: APPROVED and
# PREFERRED both resolve to advertiser). BLOCKED → None (no deal). Used only by the
# Lakebase fallback path (DbRegistryClient); the registry app returns max_access_tier.
TRUST_TO_TIER: dict[str, Optional[str]] = {
    "UNKNOWN": "public",
    "REGISTERED": "seat",
    "APPROVED": "advertiser",
    "PREFERRED": "advertiser",
    "BLOCKED": None,
}

# Identity fields revealed at each tier (cumulative). `agency` has no trust mapping but
# remains a valid tier the demo selector can present.
_TIER_FIELDS: dict[str, tuple[str, ...]] = {
    "public": (),
    "seat": ("seat_id",),
    "agency": ("seat_id", "agency_id"),
    "advertiser": ("seat_id", "agency_id", "advertiser_id"),
}
_DEFAULT_TIER = "public"  # unknown/blocked/missing trust → reveal nothing (safe minimum)

# Tiers a caller may explicitly request (the demo profile selector).
VALID_TIERS: tuple[str, ...] = tuple(_TIER_FIELDS)


def normalize_tier(tier: Optional[str]) -> Optional[str]:
    """A valid tier label (lowercased), or None when unset/unrecognized."""
    if not tier:
        return None
    t = str(tier).strip().lower()
    return t if t in _TIER_FIELDS else None


def tier_from_trust(trust_row: Optional[dict]) -> str:
    """Resolve a registry trust/verify result to a tier label.

    Order of preference:
      1. `max_access_tier` — the registry app's authoritative SDK tier (HTTP path).
      2. `trust_status` → local `TRUST_TO_TIER` map (Lakebase fallback path).
      3. the row's explicit `tier_ceiling`, then public.
    A BLOCKED status (or `is_blocked`/null tier) collapses to public."""
    row = trust_row or {}
    tier = row.get("max_access_tier")
    if tier:
        return str(tier).lower()
    if row.get("is_blocked"):
        return _DEFAULT_TIER
    status = str(row.get("trust_status") or "").upper()
    if status in TRUST_TO_TIER:
        return TRUST_TO_TIER[status] or _DEFAULT_TIER
    return (row.get("tier_ceiling") or _DEFAULT_TIER)


def gate_identity(full: Optional[dict], tier: Optional[str]) -> dict:
    """Identity args to present to a seller at `tier`, drawn from the full identity row
    (None/missing fields omitted)."""
    full = full or {}
    fields = _TIER_FIELDS.get((tier or _DEFAULT_TIER).lower(), ())
    return {f: full[f] for f in fields if full.get(f)}


def resolve_identity(
    settings: Settings, *, tier: Optional[str] = None, registry=None
) -> tuple[dict, str]:
    """(presented identity args, effective tier) for this buyer.

    `tier` is an explicit per-run choice (the demo profile selector); when given it wins.
    Otherwise the tier falls back to the buyer's registry trust. Reads the full identity
    from the buyer DB."""
    from . import db

    full = db.get_buyer_identity(settings) or {}
    chosen = normalize_tier(tier)
    if chosen is None:
        from .registry_client import get_registry_client

        registry = registry or get_registry_client(settings)
        chosen = tier_from_trust(registry.verify_self())
    return gate_identity(full, chosen), chosen
