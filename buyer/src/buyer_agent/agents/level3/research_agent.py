"""L3 Research Agent — discovers, opens, matches, and prices seller inventory.

Operates over the seller MCP (`discover_packages` → `open_package` →
`get_product_pricing`), matching products whose declared IAB audience codes intersect
the planner's resolved codes, then pricing them at the buyer's tier. It does not book
and never invents a price.
"""

from __future__ import annotations

from typing import Any

from crewai import Agent


def build_research_agent(llm: Any, tools: list[Any]) -> Agent:
    return Agent(
        role="Inventory Research Analyst",
        goal=(
            "Discover the seller's packages, open the promising ones, match products whose IAB "
            "audience codes intersect the campaign's resolved codes, and price the best matches "
            "at the buyer's tier."
        ),
        backstory=(
            "You are a meticulous inventory analyst. You use the seller-MCP tools to list "
            "packages, open them to see the products inside, and read each product's declared "
            "audience codes. You recommend products whose codes overlap the campaign's audience "
            "plan, and you always read the seller's price with get_product_pricing.\n\n"
            "You do not make booking decisions — you hand a priced, code-matched shortlist to "
            "the channel specialist. You NEVER estimate, assume, or fabricate a CPM; the only "
            "valid price is what get_product_pricing returns."
        ),
        llm=llm,
        tools=tools,
        allow_delegation=False,
        verbose=False,
    )
