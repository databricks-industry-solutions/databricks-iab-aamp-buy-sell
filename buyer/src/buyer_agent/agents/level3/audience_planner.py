"""L3 audience_planner — free-text brief interests → validated IAB audience codes.

The LLM proposes codes only via `TaxonomyLookupTool` (the gate), and we run a final
hard validation pass against the taxonomy so any invented code is dropped and names
are normalized from the TSV. Output is a structured `AudiencePlan` the channel crews
use to filter each seller's packages by `standard_segment_ids`.
"""

from __future__ import annotations

from typing import Optional

from ...config import Settings
from ...llm import build_llm
from ...models import AudiencePlan, AudienceSegment, TaxonomyCode
from ...taxonomy import audience_index
from ...tools import TaxonomyLookupTool


def build_audience_planner(llm, tools):
    from crewai import Agent

    return Agent(
        role="IAB Audience Planner",
        goal=(
            "Translate each free-text interest from a campaign brief into one or more REAL "
            "IAB Audience Taxonomy 1.1 codes, using ONLY codes returned by the taxonomy lookup tool. "
            "If no candidate is an exact match, choose the nearest one the tool returns — "
            "never leave an interest unresolved."
        ),
        backstory=(
            "You are a programmatic media planner who lives in the IAB Audience Taxonomy. You map "
            "marketer language ('sports fans', 'auto intenders') to the exact standard segment codes "
            "sellers expose, so deals target the right audience. You never invent a code — you look it up."
        ),
        tools=tools,
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )


def _task_description(interests: list[str]) -> str:
    bullets = "\n".join(f"  - {i}" for i in interests) or "  (none)"
    return (
        "Resolve each of these campaign-brief interests to IAB Audience Taxonomy 1.1 codes:\n"
        f"{bullets}\n\n"
        "For EACH interest: call the taxonomy lookup tool with the interest text, then choose the "
        "single best-matching candidate code (occasionally two if clearly distinct). Use ONLY codes "
        "the tool returns; if none is an exact match, pick the closest candidate it returns — never "
        "leave an interest without a code. Produce one segment per interest with its chosen code(s)."
    )


def plan_audiences(
    settings: Settings,
    *,
    interests: list[str],
    brief_id: Optional[str] = None,
) -> AudiencePlan:
    """Run the audience planner over a brief's interests and return a validated plan."""
    from crewai import Crew, Process, Task

    if not interests:
        return AudiencePlan(brief_id=brief_id, segments=[], notes="no interests in brief")

    agent = build_audience_planner(build_llm(settings), [TaxonomyLookupTool()])
    task = Task(
        description=_task_description(interests),
        expected_output=(
            "An AudiencePlan: one segment per interest, each with the interest text and a list of "
            "chosen IAB codes ({code, name})."
        ),
        agent=agent,
        output_pydantic=AudiencePlan,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, memory=False, verbose=False)
    result = crew.kickoff()
    raw = result.pydantic if isinstance(result.pydantic, AudiencePlan) else AudiencePlan()
    return _validate(raw, brief_id=brief_id, interests=interests)


def _validate(plan: AudiencePlan, *, brief_id: Optional[str], interests: list[str]) -> AudiencePlan:
    """Hard gate: keep only codes that exist in the taxonomy; normalize names from the TSV.
    Guarantees a segment per requested interest even if the LLM merged/dropped some."""
    idx = audience_index()
    by_interest: dict[str, AudienceSegment] = {}
    for seg in plan.segments:
        codes: list[TaxonomyCode] = []
        for c in seg.codes:
            code = str(c.code).strip()
            if not code:
                continue
            if not idx.available:
                codes.append(TaxonomyCode(code=code, name=c.name or ""))
            elif idx.validate(code):
                codes.append(TaxonomyCode(code=code, name=idx.name(code) or c.name or ""))
            # else: invented code → dropped
        by_interest[seg.interest.strip().lower()] = AudienceSegment(
            interest=seg.interest, audience_type=seg.audience_type or "standard", codes=codes
        )

    segments = [
        by_interest.get(i.strip().lower(), AudienceSegment(interest=i, codes=[]))
        for i in interests
    ]
    note = None if idx.available else "taxonomy TSV unavailable — codes unvalidated"
    return AudiencePlan(brief_id=brief_id, segments=segments, notes=plan.notes or note)
