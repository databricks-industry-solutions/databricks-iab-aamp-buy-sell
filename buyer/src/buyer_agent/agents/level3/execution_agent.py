"""L3 Execution Agent — assembles the booking recommendation.

The money calls (request_quote → create_order → create_line) are not done by the LLM
here — they run deterministically in `flow.py` behind the book-or-pass gate, so the
agent can never commit a buy above the ceiling. This agent's job is to finalize which
product to recommend and the deal type, citing the seller's real price.
"""

from __future__ import annotations

from typing import Any

from crewai import Agent


def build_execution_agent(llm: Any, tools: list[Any] | None = None) -> Agent:
    return Agent(
        role="Campaign Execution Specialist",
        goal=(
            "Turn the research shortlist into a single, well-formed booking recommendation: the "
            "chosen product_id, its real seller price (final_cpm), the deal type, and the matched "
            "audience codes."
        ),
        backstory=(
            "You are an ad-operations specialist who prepares bookings with precision. You take "
            "the research analyst's priced, code-matched shortlist and select the one product to "
            "recommend, restating the seller's quoted final_cpm exactly.\n\n"
            "The final commit (quote → order → line) and the buy-or-pass guardrail are enforced "
            "downstream by the system, not by you. You NEVER estimate, assume, or fabricate a "
            "CPM — you only echo the price the seller returned."
        ),
        llm=llm,
        tools=tools or [],
        allow_delegation=False,
        verbose=False,
    )
