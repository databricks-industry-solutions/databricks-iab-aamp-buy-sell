"""L2 channel specialists — each manages its own hierarchical channel crew.

Two channels: Connected TV and Linear TV. Each specialist is the `manager_agent` of a
channel crew, coordinating the L3 research + execution workers and emitting the ranked
recommendation. They never fabricate a price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crewai import Agent

_NO_PRICE_RULE = (
    "You NEVER estimate, assume, or fabricate a CPM. Prices come only from the seller's "
    "get_pricing / request_quote responses. If a product has no price, say so."
)


@dataclass(frozen=True)
class ChannelSpec:
    """Per-channel variation: registry capability + specialist persona."""

    channel: str          # brief ChannelType token
    capability: str       # registry capability token
    role: str
    expertise: str


CTV = ChannelSpec(
    channel="CTV",
    capability="ctv",
    role="Connected TV Specialist",
    expertise=(
        "premium streaming / OTT inventory, household and device-graph targeting, and "
        "frequency management across screens"
    ),
)

LINEAR_TV = ChannelSpec(
    channel="LINEAR_TV",
    capability="linear_tv",
    role="Linear TV Specialist",
    expertise=(
        "national and local linear broadcast inventory, dayparts and program adjacencies, "
        "and reach against broad demographic audiences"
    ),
)

SPECS: dict[str, ChannelSpec] = {CTV.channel: CTV, LINEAR_TV.channel: LINEAR_TV}


def build_channel_specialist(spec: ChannelSpec, llm: Any) -> Agent:
    return Agent(
        role=spec.role,
        goal=(
            f"Secure the best {spec.channel} inventory for the campaign's audience and budget by "
            "coordinating your research and execution workers, and produce a single ranked "
            "recommendation backed by a real seller price."
        ),
        backstory=(
            f"You are a {spec.role.lower()} with deep knowledge of {spec.expertise}. You direct "
            "an inventory research analyst to discover, open, and price the seller's packages, "
            "then turn their findings into one recommended product for the campaign.\n\n"
            f"{_NO_PRICE_RULE}"
        ),
        llm=llm,
        allow_delegation=True,
        verbose=False,
    )
