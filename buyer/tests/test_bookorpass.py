"""Deterministic tests for the book-or-pass gate + flow helpers (no LLM, no IO).

These pin the demo's core money mechanic: the buyer books iff the seller's real CPM is
within the brief's max_cpm, derives impressions = budget ÷ CPM, never invents a price,
and honors the seeded channel split. They run by default (unmarked)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent import flow  # noqa: E402


# ----- book_or_pass: the seeded tiering scenario (max_cpm = 38) --------------

def test_pass_when_above_ceiling():
    decision, imps, reason = flow.book_or_pass(final_cpm=40.0, max_cpm=38.0, channel_budget=120_000)
    assert decision == "PASS"
    assert "40.00" in reason and "38.00" in reason
    assert imps == int((120_000 / 40.0) * 1000)  # impressions still derived for the report


def test_book_when_below_ceiling():
    decision, imps, reason = flow.book_or_pass(final_cpm=36.0, max_cpm=38.0, channel_budget=120_000)
    assert decision == "BOOK"
    assert imps == int((120_000 / 36.0) * 1000)


def test_book_at_exact_ceiling():
    # final_cpm == max_cpm books (≤, not <)
    decision, _, _ = flow.book_or_pass(final_cpm=38.0, max_cpm=38.0, channel_budget=100_000)
    assert decision == "BOOK"


def test_pass_when_no_price():
    decision, imps, reason = flow.book_or_pass(final_cpm=None, max_cpm=38.0, channel_budget=120_000)
    assert decision == "PASS" and imps is None
    assert "no seller price" in reason


def test_pass_when_below_product_minimum():
    decision, imps, reason = flow.book_or_pass(
        final_cpm=36.0, max_cpm=38.0, channel_budget=120_000, min_impressions=10**12
    )
    assert decision == "PASS"
    assert "minimum" in reason


def test_no_ceiling_books():
    decision, _, _ = flow.book_or_pass(final_cpm=99.0, max_cpm=None, channel_budget=50_000)
    assert decision == "BOOK"


# ----- impressions math -----------------------------------------------------

def test_impressions_cpm_formula():
    # cost = impressions/1000 * cpm; budget 120k at $36 CPM ≈ 3.33M impressions
    assert flow._impressions(120_000, 36.0) == 3_333_333
    assert flow._impressions(1_000, 0) == 0


# ----- allocate_budget honors the seeded channel split ----------------------

def test_allocate_budget_split():
    brief = {
        "total_budget": 1_000_000,
        "channels": [
            {"channel": "CTV", "budget_pct": 60},
            {"channel": "LINEAR_TV", "budget_pct": 40},
        ],
    }
    allocs = flow.allocate_budget(brief)
    by = {a.channel: a for a in allocs}
    assert by["CTV"].budget == 600_000 and by["CTV"].capability == "ctv"
    assert by["LINEAR_TV"].budget == 400_000 and by["LINEAR_TV"].capability == "linear_tv"
    assert sum(a.budget_pct for a in allocs) == 100


# ----- price extraction handles both seller shapes --------------------------

def test_price_of_pricing_and_quote_shapes():
    assert flow._price_of({"final_price": 36.0}) == 36.0          # get_pricing shape
    assert flow._price_of({"pricing": {"final_cpm": 32.0}}) == 32.0  # quote shape
    assert flow._price_of(None) is None
    assert flow._price_of({}) is None


# ----- capability + deal-type mapping ---------------------------------------

def test_capability_map():
    assert flow._capability("CTV") == "ctv"
    assert flow._capability("LINEAR_TV") == "linear_tv"


def test_deal_type_from_brief_preferences():
    assert flow._deal_type({"deal_preferences": {"preferred_deal_types": ["programmaticguaranteed"]}}) == "PG"
    assert flow._deal_type({"deal_preferences": {"preferred_deal_types": ["preferreddeal"]}}) == "PD"
    assert flow._deal_type({}) == "PD"


def test_execution_rail_from_brief_preferences():
    # the ROUTE RAIL chooser: brief deal_preferences.execution → direct (rail 1) | programmatic (rail 2)
    assert flow._execution_rail({"deal_preferences": {"execution": "programmatic"}}) == "programmatic"
    assert flow._execution_rail({"deal_preferences": {"execution": "direct"}}) == "direct"
    assert flow._execution_rail({"deal_preferences": {"execution": "PROGRAMMATIC"}}) == "programmatic"
    assert flow._execution_rail({"deal_preferences": {"execution": "weird"}}) == "direct"
    assert flow._execution_rail({}) == "direct"
