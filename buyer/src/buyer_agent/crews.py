"""CrewAI crews — the L1 portfolio crew and the per-channel L2/L3 crews.

Both use `Process.hierarchical` with a `manager_agent`:

  * `build_portfolio_crew` — L1 portfolio_manager (manager) + L2 specialists (workers).
    Emits the per-channel budget split + guidance, anchored to the brief's budget_pct
    (the manager justifies the split rather than inventing one). flow.py uses the
    deterministic dollar amounts; this crew supplies the rationale.
  * `build_channel_crew` — L2 specialist (manager) + L3 research_agent (seller-MCP tools)
    + L3 execution_agent (recommendation assembler). Emits a `ChannelRecommendation`.

The crews only discover/rank/recommend. The money calls (quote → order → line) and the
book-or-pass gate are deterministic in flow.py — the LLM never commits a buy.
"""

from __future__ import annotations

from typing import Any, Optional

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field

from .agents.level1.portfolio_manager import build_portfolio_manager
from .agents.level2.channel_specialists import SPECS, ChannelSpec, build_channel_specialist
from .agents.level3.execution_agent import build_execution_agent
from .agents.level3.research_agent import build_research_agent
from .models import ChannelRecommendation
from .tools import seller_read_tools


# ---- L1 portfolio crew ------------------------------------------------------

class _AllocOut(BaseModel):
    channel: str = ""
    budget_pct: float = 0.0
    rationale: str = ""


class PortfolioPlan(BaseModel):
    """Structured output of the L1 budget-allocation task."""

    allocations: list[_AllocOut] = Field(default_factory=list)


def build_portfolio_crew(
    manager_llm: Any,
    specialist_llm: Any,
    *,
    brief: dict,
    step_callback: Optional[Any] = None,
) -> Crew:
    """L1 hierarchical crew: portfolio_manager allocates + coordinates the L2 specialists."""
    manager = build_portfolio_manager(manager_llm)
    channels = brief.get("channels") or []
    specialists = [
        build_channel_specialist(SPECS[ch["channel"]], specialist_llm)
        for ch in channels
        if ch.get("channel") in SPECS
    ]

    lines = "\n".join(
        f"  - {c.get('channel')}: budget_pct={c.get('budget_pct')}" for c in channels
    ) or "  (none)"
    objectives = ", ".join(brief.get("objectives") or []) or "reach"

    allocation_task = Task(
        description=(
            f"Campaign '{brief.get('name')}' — total budget ${float(brief.get('total_budget') or 0):,.0f} "
            f"{brief.get('currency', 'USD')}, objectives: {objectives}.\n"
            f"The brief specifies this channel split (HONOR it — validate and justify, do not change it):\n"
            f"{lines}\n\n"
            "For each channel, restate its budget_pct and give a one-sentence rationale tying it to the "
            "campaign objectives and the channel's strengths. Do not invent channels or change the split."
        ),
        expected_output=(
            "A PortfolioPlan: one allocation per channel, each with the channel token, its budget_pct "
            "(unchanged from the brief), and a short rationale."
        ),
        agent=manager,
        output_pydantic=PortfolioPlan,
    )

    return Crew(
        agents=specialists,
        tasks=[allocation_task],
        process=Process.hierarchical,
        manager_agent=manager,
        memory=False,
        verbose=False,
        step_callback=step_callback,
    )


# ---- per-channel L2/L3 crew -------------------------------------------------

def build_channel_crew(
    spec: ChannelSpec,
    manager_llm: Any,
    worker_llm: Any,
    *,
    seller_base_url: str,
    mcp_path: str,
    identity: dict,
    auth_token: Optional[str],
    audience_codes: list[str],
    channel_budget: float,
    max_cpm: Optional[float],
    flight: tuple[Optional[str], Optional[str]],
    step_callback: Optional[Any] = None,
) -> Crew:
    """L2 specialist (manager) + L3 research/execution (workers), bound to one seller."""
    research_tools = seller_read_tools(
        seller_base_url, mcp_path=mcp_path, identity=identity, auth_token=auth_token
    )
    manager = build_channel_specialist(spec, manager_llm)
    research = build_research_agent(worker_llm, research_tools)
    execution = build_execution_agent(worker_llm)

    codes = ", ".join(audience_codes) or "(no specific codes — match broadly)"
    ceiling = f"${max_cpm:.2f} CPM" if max_cpm is not None else "(none)"

    research_task = Task(
        description=(
            f"Find the best {spec.channel} product for this campaign on the connected seller.\n"
            f"- Target IAB audience codes: {codes}\n"
            f"- Channel budget: ${channel_budget:,.0f}\n"
            f"- Flight: {flight[0]} to {flight[1]}\n"
            f"- Buyer's CPM guidance (do NOT submit a price): {ceiling}\n\n"
            "Steps:\n"
            "1. Call discover_packages (optionally pass one of the target codes as audience_id).\n"
            "2. open_package on the promising packages to see the products inside and their codes.\n"
            "3. Prefer products whose declared audience codes intersect the target codes.\n"
            "4. Call get_product_pricing on your top candidates to read the seller's real final price.\n"
            "Return a short ranked shortlist: product_id, name, final price (from the tool), and which "
            "target codes it matches. NEVER estimate a price — only report prices the tool returned."
        ),
        expected_output=(
            "A ranked shortlist of 1-3 products with: product_id, product name, final CPM (from "
            "get_product_pricing), and the matched audience codes."
        ),
        agent=research,
    )

    recommendation_task = Task(
        description=(
            f"From the research shortlist, select the SINGLE best {spec.channel} product to pursue. "
            "Echo the seller's quoted final CPM exactly (never your own estimate). Choose a deal_type "
            "of 'PG' (programmatic guaranteed) or 'PD' (preferred deal). Produce the structured "
            "recommendation. If the shortlist is empty, return an empty product_id."
        ),
        expected_output=(
            "A ChannelRecommendation: product_id, product_name, final_cpm (from the seller), deal_type, "
            "audience_codes (matched), and a one-line rationale."
        ),
        agent=manager,
        context=[research_task],
        output_pydantic=ChannelRecommendation,
    )

    return Crew(
        agents=[research, execution],
        tasks=[research_task, recommendation_task],
        process=Process.hierarchical,
        manager_agent=manager,
        memory=False,
        verbose=False,
        step_callback=step_callback,
    )
