"""The channel crew + deterministic book-or-pass, end to end (LIVE).

Marked `integration` (deselected by default): runs the real L2/L3 channel crew through
Databricks Model Serving and the real MCP transport against the faithful mock seller, so
it needs Databricks creds (AAMP_DATABRICKS_PROFILE). The mock returns a CTV card
($40 public, −10% agency, −20% advertiser), so this exercises the tiering behavior:

    AAMP_DATABRICKS_PROFILE=<profile> python -m pytest \
        tests/test_flow_integration.py -m integration -v

The booking guardrail is asserted as an invariant (book ⇒ final_cpm ≤ max_cpm), so the
assertions hold no matter which product the LLM picks. The agency-tier case additionally
asserts a positive booking, since every agency-tier CTV price is ≤ the $38 ceiling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent import flow  # noqa: E402
from buyer_agent.agents.level2.channel_specialists import CTV  # noqa: E402
from buyer_agent.config import Settings  # noqa: E402
from buyer_agent.registry_client import SellerRef  # noqa: E402
from buyer_agent.seller_client import SellerMCPClient  # noqa: E402

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    s = Settings()
    if not s.databricks_profile and not s.openai_base_url:
        pytest.skip("set AAMP_DATABRICKS_PROFILE to run the live channel-crew flow test")
    return s


def _seller(base_url: str) -> SellerRef:
    return SellerRef(
        agent_id="seller-summit", name="Summit Streaming (mock)", capability="ctv",
        trust_level="VERIFIED", base_url=base_url, registry_url="https://summitstreaming.example",
    )


def _assert_guardrail(outcome, max_cpm: float):
    """The deterministic invariant the demo rests on: a booking is never above the ceiling."""
    if outcome.decision == "BOOKED":
        assert outcome.final_cpm is not None and outcome.final_cpm <= max_cpm
        assert outcome.order_id and outcome.quote_id
        assert outcome.impressions and outcome.impressions > 0
    elif outcome.decision == "PASSED":
        assert outcome.final_cpm is None or outcome.final_cpm > max_cpm or "rejected" in outcome.reason


def test_channel_books_at_agency_tier(seller_base_url):
    """Agency tier: every CTV price (sports 36 / food 34.2 / travel 32.4) ≤ max_cpm 38 → BOOK.
    Proves the crew picks a product and the deterministic path commits quote→order→line."""
    settings = _settings()
    max_cpm = 38.0
    outcome = flow.execute_channel(
        settings,
        seller=_seller(seller_base_url),
        spec=CTV,
        audience_codes=["607"],
        channel_budget=120_000,
        max_cpm=max_cpm,
        flight=("2026-07-01", "2026-09-30"),
        identity={"seat_id": "seat-northstar", "agency_id": "agcy-northstar", "advertiser_id": None},
        deal_type="PG",
    )
    assert outcome.decision == "BOOKED", outcome
    _assert_guardrail(outcome, max_cpm)
    # agency tier = 10% off the seeded base cards {40, 38, 36}
    assert outcome.final_cpm in {36.0, 34.2, 32.4}, outcome

    # the seller's order ledger is the source of truth — confirm the line landed
    async def _orders():
        async with SellerMCPClient(seller_base_url) as c:
            return await c.list_orders()

    from buyer_agent.seller_client import run_sync
    orders = run_sync(_orders())
    assert any(o.get("execution_order_id") == outcome.order_id for o in orders), orders


def test_channel_guardrail_holds_at_public_tier(seller_base_url):
    """Public tier: sports $40 > 38 (pass) but food/travel ≤ 38 (book). Whatever the LLM
    picks, the guardrail invariant must hold."""
    settings = _settings()
    max_cpm = 38.0
    outcome = flow.execute_channel(
        settings,
        seller=_seller(seller_base_url),
        spec=CTV,
        audience_codes=["607"],
        channel_budget=120_000,
        max_cpm=max_cpm,
        flight=("2026-07-01", "2026-09-30"),
        identity={"seat_id": None, "agency_id": None, "advertiser_id": None},
        deal_type="PD",
    )
    assert outcome.decision in {"BOOKED", "PASSED"}, outcome
    _assert_guardrail(outcome, max_cpm)
