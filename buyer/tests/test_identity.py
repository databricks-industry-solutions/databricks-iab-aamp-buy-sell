"""Deterministic tests for tier-gated identity (no DB/LLM).

This is the buyer half of the tiering demo: the identity the buyer presents to a seller
is gated to a tier — chosen per run (the demo profile selector) or, as a fallback, from
its registry trust. These pin the SDK-aligned trust→tier map, the cumulative reveal, and
the per-run override."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent import identity  # noqa: E402

_FULL = {"seat_id": "seat-northstar", "agency_id": "agcy-northstar", "advertiser_id": "adv-northstar"}


# ----- trust_status → tier (the live knob) ----------------------------------

def test_trust_status_maps_to_tier():
    # Matches the SDK's TRUST_TO_TIER_MAP — no AGENCY tier (APPROVED/PREFERRED → advertiser).
    assert identity.tier_from_trust({"trust_status": "UNKNOWN"}) == "public"
    assert identity.tier_from_trust({"trust_status": "REGISTERED"}) == "seat"
    assert identity.tier_from_trust({"trust_status": "APPROVED"}) == "advertiser"
    assert identity.tier_from_trust({"trust_status": "PREFERRED"}) == "advertiser"


def test_blocked_and_missing_collapse_to_public():
    assert identity.tier_from_trust({"trust_status": "BLOCKED"}) == "public"
    assert identity.tier_from_trust({}) == "public"
    assert identity.tier_from_trust(None) == "public"


def test_tier_ceiling_fallback_when_status_unknown():
    # If trust_status isn't a known token, fall back to the row's explicit tier_ceiling.
    assert identity.tier_from_trust({"tier_ceiling": "advertiser"}) == "advertiser"


def test_registry_max_access_tier_is_authoritative():
    # The registry app's /registry/verify returns an SDK tier directly; prefer it.
    assert identity.tier_from_trust(
        {"trust_status": "approved", "max_access_tier": "advertiser"}
    ) == "advertiser"
    # A blocked verify (null tier + is_blocked) collapses to public.
    assert identity.tier_from_trust(
        {"trust_status": "blocked", "max_access_tier": None, "is_blocked": True}
    ) == "public"


# ----- cumulative reveal mirrors the seller's claim logic -------------------

def test_public_reveals_nothing():
    assert identity.gate_identity(_FULL, "public") == {}


def test_seat_reveals_seat_only():
    assert identity.gate_identity(_FULL, "seat") == {"seat_id": "seat-northstar"}


def test_agency_reveals_seat_and_agency():
    assert identity.gate_identity(_FULL, "agency") == {
        "seat_id": "seat-northstar", "agency_id": "agcy-northstar"
    }


def test_advertiser_reveals_all():
    assert identity.gate_identity(_FULL, "advertiser") == _FULL


def test_missing_fields_are_omitted():
    # A buyer with only an agency_id, gated at advertiser, still only reveals what it has.
    partial = {"agency_id": "agcy-x"}
    assert identity.gate_identity(partial, "advertiser") == {"agency_id": "agcy-x"}


# ----- resolve_identity wires DB identity + live registry trust -------------

def test_resolve_identity_uses_registry_trust(monkeypatch):
    """No per-run tier → fall back to registry trust: APPROVED → advertiser → all revealed."""
    from buyer_agent import db
    from buyer_agent.config import Settings

    class _StubRegistry:
        def discover_sellers(self, capability=None):
            return []

        def verify_self(self):
            return {"trust_status": "APPROVED"}

    monkeypatch.setattr(db, "get_buyer_identity", lambda settings: dict(_FULL))
    args, tier = identity.resolve_identity(Settings(), registry=_StubRegistry())
    assert tier == "advertiser"
    assert args == _FULL


# ----- per-run profile override (the demo selector) -------------------------

def test_tier_override_wins_over_registry(monkeypatch):
    """An explicit tier is used verbatim and the registry is never consulted."""
    from buyer_agent import db
    from buyer_agent.config import Settings

    class _BoomRegistry:  # verify_self must not be called when a tier is given
        def verify_self(self):
            raise AssertionError("registry should not be consulted when tier is explicit")

    monkeypatch.setattr(db, "get_buyer_identity", lambda settings: dict(_FULL))
    args, tier = identity.resolve_identity(Settings(), tier="agency", registry=_BoomRegistry())
    assert tier == "agency"
    assert args == {"seat_id": "seat-northstar", "agency_id": "agcy-northstar"}


def test_invalid_tier_override_falls_back_to_registry(monkeypatch):
    from buyer_agent import db
    from buyer_agent.config import Settings

    class _StubRegistry:
        def verify_self(self):
            return {"trust_status": "REGISTERED"}

    monkeypatch.setattr(db, "get_buyer_identity", lambda settings: dict(_FULL))
    args, tier = identity.resolve_identity(Settings(), tier="platinum", registry=_StubRegistry())
    assert tier == "seat"


def test_normalize_tier():
    assert identity.normalize_tier("Advertiser") == "advertiser"
    assert identity.normalize_tier(" seat ") == "seat"
    assert identity.normalize_tier("") is None
    assert identity.normalize_tier(None) is None
    assert identity.normalize_tier("bogus") is None
