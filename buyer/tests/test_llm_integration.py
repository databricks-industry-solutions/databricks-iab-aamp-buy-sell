"""CrewAI -> Databricks Model Serving (LIVE).

Marked `integration` (deselected by default): hits a real serving endpoint and needs
Databricks creds. Run locally with a profile:
    AAMP_DATABRICKS_PROFILE=<profile> python -m pytest tests/test_llm_integration.py -m integration -v
In a deployed app the same path works via the app's service principal (no profile)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent import llm  # noqa: E402
from buyer_agent.config import Settings  # noqa: E402

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    s = Settings()
    if not s.databricks_profile and not s.openai_base_url:
        pytest.skip("set AAMP_DATABRICKS_PROFILE to run the live Model Serving smoke test")
    return s


def test_model_serving_smoke():
    """Raw LLM.call round-trip (no agent) — proves auth + base_url + endpoint."""
    reply = llm.smoke(_settings())
    assert "OK" in reply.upper()


def test_one_agent_call():
    """One CrewAI agent through Model Serving — proves the agentic path end to end."""
    from crewai import Agent, Crew, Process, Task

    model = llm.build_llm(_settings())
    agent = Agent(
        role="Echo agent",
        goal="Reply with exactly the word OK",
        backstory="You answer as tersely as possible.",
        llm=model,
        verbose=False,
    )
    task = Task(description="Reply with exactly: OK", expected_output="The word OK", agent=agent)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, memory=False, verbose=False)
    result = crew.kickoff()
    assert "OK" in str(result).upper()


def test_audience_planner_resolves_seeded_interests():
    """L3 planner (live): the seeded brief interests resolve to VALID IAB codes.
    The taxonomy gate guarantees every emitted code is real; we also expect at least
    one code per interest and a sports-ish code for 'sports fans'."""
    from buyer_agent.agents.level3.audience_planner import plan_audiences
    from buyer_agent.taxonomy import audience_index

    idx = audience_index()
    if not idx.available:
        pytest.skip("Audience Taxonomy TSV not vendored (make taxonomies)")

    interests = ["sports fans", "auto intenders"]
    plan = plan_audiences(_settings(), interests=interests, brief_id="brief-q3-brandperf")

    assert [s.interest for s in plan.segments] == interests
    all_codes = plan.all_codes()
    assert all_codes, "planner returned no codes"
    assert all(idx.validate(c) for c in all_codes), f"invalid code leaked: {all_codes}"
    # 'sports fans' should land on a sports-tier code
    sports_seg = next(s for s in plan.segments if s.interest == "sports fans")
    assert any("Sport" in c.name for c in sports_seg.codes), sports_seg
