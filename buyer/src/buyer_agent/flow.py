"""The book-or-pass flow — wires L3 → L1 → L2/L3 crews and the deterministic gate.

run_brief(settings, brief_id):
  0. resolve_identity            → present only the identity the buyer's registry trust
                                    allows (public/agency/advertiser). The tiering knob:
                                    flipping the buyer's trust moves the tier, which moves
                                    the seller's price under/over max_cpm → book-or-pass. [registry]
  1. L3 audience_planner          → AudiencePlan (validated IAB codes).            [LLM]
  2. allocate_budget (L1 crew)    → per-channel dollar split (anchored to budget_pct).
  3. per channel:
     a. registry discover         → reachable, non-BLOCKED seller for that capability. [registry]
     b. channel crew (L2+L3)       → ChannelRecommendation (which product, real price). [LLM+MCP]
     c. book_or_pass (DETERMINISTIC): authoritative get_pricing → impressions =
        channel_budget ÷ final_cpm; book iff final_cpm ≤ max_cpm (and impressions ≥
        product minimum, enforced by the seller's request_quote), via
        request_quote → create_order → create_line. The buyer never submits a price,
        and the LLM can never book above the ceiling.

The seller's list_orders is the booking ledger — the buyer keeps no orders table.
The whole run is MLflow-traced when tracing is configured (mlflow.crewai.autolog()).
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Optional

# A milestone/step sink for the demo UI: emit(event_type, **fields). No-op by default.
Emit = Callable[..., None]


def _noop(*_a: Any, **_k: Any) -> None:
    return None


# The L3 research agent's CrewAI tools wrap seller-MCP calls under friendlier method
# names; surface the ACTUAL seller-MCP tool name so the UI reflects the real /mcp/ call.
# Each entry: crew_tool_name -> (seller_mcp_tool, phase, owner, level). Every seller-MCP
# read tool lives on the L3 research analyst.
_TOOL_META = {
    "discover_packages": ("list_packages", "discover", "Inventory Research Analyst", "L3"),
    "open_package": ("get_package", "open", "Inventory Research Analyst", "L3"),
    "get_product_pricing": ("get_pricing", "price", "Inventory Research Analyst", "L3"),
}

# The deterministic book-or-pass path (in execute_channel) calls these seller-MCP tools
# directly — the LLM never commits a buy. We surface them as steps of the L3 execution
# specialist so the write-side calls are visible alongside the research reads.
_EXECUTION_AGENT = "Campaign Execution Specialist"


def _trim(text: str, limit: int = 180) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _summarize_result(payload: Any, limit: int = 220) -> str:
    """A compact, legible view of what the seller returned — the `←` side of a trace.

    Lists (e.g. list_packages) collapse to a count + the first few names; dicts render
    as compact JSON; strings (a CrewAI tool observation) are trimmed as-is."""
    if payload is None:
        return "∅ no result"
    if isinstance(payload, str):
        return _trim(payload, limit)
    if isinstance(payload, list):
        names = [
            str(p.get("name") or p.get("package_id") or p.get("product_id") or "?")
            for p in payload if isinstance(p, dict)
        ]
        head = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
        return f"{len(payload)} item(s)" + (f": {head}" if head.strip(", …") else "")
    if isinstance(payload, dict):
        return _trim(json.dumps(payload, default=str, separators=(", ", ": ")), limit)
    return _trim(str(payload), limit)


def _json_safe(payload: Any) -> Any:
    """A JSON-serializable copy of a tool input/result for the UI's expandable trace
    (SSE events are json.dumps'd with no default). Strings that ARE JSON are parsed so
    the UI pretty-prints them; anything unserializable degrades to its str form."""
    if payload is None or payload == "":
        return None
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except ValueError:
            return payload
    try:
        return json.loads(json.dumps(payload, default=str))
    except (TypeError, ValueError):
        return str(payload)


def _step_agent(step: Any) -> Optional[str]:
    """Best-effort acting-agent role from a CrewAI step (version-dependent)."""
    ag = getattr(step, "agent", None)
    role = getattr(ag, "role", None) if ag is not None else None
    return str(role) if role else None


def _make_step_callback(
    emit: Emit, *, channel: str, lead_role: str = "Channel Specialist", lead_level: str = "L2"
) -> Callable[[Any], None]:
    """Adapt CrewAI's step_callback (AgentAction | AgentFinish) into UI `agent.step`
    events, attributing each step to the crew member that produced it. Tool calls are the
    legible signal — they map 1:1 to seller-MCP calls and belong to the L3 research analyst;
    a finish is the crew lead (`lead_role`) synthesizing its recommendation. The UI shows the
    raw seller-MCP tool name. Everything is best-effort and never raises into the crew."""

    def _cb(step: Any) -> None:
        try:
            role = _step_agent(step)
            tool = getattr(step, "tool", None)
            if tool:
                mcp_tool, phase, owner, level = _TOOL_META.get(
                    str(tool), (str(tool), "", "Inventory Research Analyst", "L3")
                )
                tool_input = getattr(step, "tool_input", "") or ""
                emit("agent.step", channel=channel, kind="tool", tool=mcp_tool,
                     agent=role or owner, level=level, phase=phase,
                     detail=_trim(tool_input, 140), data=_json_safe(tool_input))
                # The seller's response — the `←` return side of the trace (best-effort;
                # CrewAI attaches the tool observation to the step after it runs).
                result = getattr(step, "result", None)
                if result is not None:
                    emit("agent.step", channel=channel, kind="result", tool=mcp_tool,
                         agent=role or owner, level=level, phase=phase,
                         detail=_summarize_result(result), data=_json_safe(result))
                return
            output = getattr(step, "output", None)
            if output is not None:  # AgentFinish — the lead synthesizes the recommendation
                emit("agent.step", channel=channel, kind="finish",
                     agent=role or lead_role, level=lead_level, phase="recommend",
                     detail=_trim(output, 200), data=_json_safe(output))
        except Exception:
            pass

    return _cb

from .agents.level2.channel_specialists import SPECS, ChannelSpec
from .config import Settings
from .crews import PortfolioPlan, build_channel_crew, build_portfolio_crew
from .models import (
    AudiencePlan,
    ChannelAllocation,
    ChannelOutcome,
    ChannelRecommendation,
    RunResult,
)
from .registry_client import SellerRef, get_registry_client
from .seller_client import SellerMCPClient, run_sync


# ----- pure helpers (no LLM, no IO) -----------------------------------------

def _price_of(pricing: Optional[dict]) -> Optional[float]:
    """Authoritative CPM from a seller get_pricing response (key is `final_price`;
    quote responses use `pricing.final_cpm`). Returns None if absent."""
    if not pricing:
        return None
    if pricing.get("final_price") is not None:
        return float(pricing["final_price"])
    inner = pricing.get("pricing") or {}
    if inner.get("final_cpm") is not None:
        return float(inner["final_cpm"])
    return None


def _impressions(channel_budget: float, final_cpm: float) -> int:
    """Single-pass derivation: cost = impressions/1000 × CPM ⇒ impressions = budget/CPM×1000."""
    if final_cpm <= 0:
        return 0
    return int(math.floor((channel_budget / final_cpm) * 1000))


def book_or_pass(
    *,
    final_cpm: Optional[float],
    max_cpm: Optional[float],
    channel_budget: float,
    min_impressions: Optional[int] = None,
) -> tuple[str, Optional[int], str]:
    """The deterministic gate. Returns (decision, impressions, reason) where decision is
    'BOOK' or 'PASS'. Never books above the CPM ceiling or below a product minimum."""
    if final_cpm is None or final_cpm <= 0:
        return ("PASS", None, "no seller price available")
    imps = _impressions(channel_budget, final_cpm)
    if max_cpm is not None and final_cpm > max_cpm:
        return ("PASS", imps, f"final_cpm {final_cpm:.2f} > max_cpm {max_cpm:.2f}")
    if min_impressions is not None and imps < int(min_impressions):
        return ("PASS", imps, f"impressions {imps} < product minimum {int(min_impressions)}")
    ceiling = "no ceiling" if max_cpm is None else f"max_cpm {max_cpm:.2f}"
    return ("BOOK", imps, f"final_cpm {final_cpm:.2f} ≤ {ceiling}")


_DEFAULT_CAPABILITY = {"CTV": "ctv", "LINEAR_TV": "linear_tv"}


def _capability(channel: str) -> str:
    spec = SPECS.get(channel)
    return spec.capability if spec else _DEFAULT_CAPABILITY.get(channel, channel.lower())


def _deal_type(brief: dict) -> str:
    prefs = (brief.get("deal_preferences") or {}).get("preferred_deal_types") or []
    return "PG" if any("guaranteed" in str(p).lower() for p in prefs) else "PD"


def _execution_rail(brief: dict) -> str:
    """The brief's fulfilment rail (deal_preferences.execution, seeded):
    'direct' (Rail 1 — OpenDirect IO, the hero) | 'programmatic' (Rail 2 — PG Deal ID → SSP).
    Anything else falls back to direct."""
    rail = str((brief.get("deal_preferences") or {}).get("execution") or "direct").lower()
    return rail if rail in ("direct", "programmatic") else "direct"


def allocate_budget(brief: dict) -> list[ChannelAllocation]:
    """Deterministic dollar split from the brief's budget_pct (the spine the L1 crew justifies)."""
    total = float(brief.get("total_budget") or 0)
    out: list[ChannelAllocation] = []
    for ch in brief.get("channels") or []:
        channel = ch.get("channel", "")
        pct = float(ch.get("budget_pct") or 0)
        out.append(
            ChannelAllocation(
                channel=channel,
                capability=_capability(channel),
                budget_pct=pct,
                budget=round(total * pct / 100.0, 2),
            )
        )
    return out


# ----- tracing --------------------------------------------------------------

def enable_tracing(settings: Settings) -> bool:
    """Best-effort MLflow CrewAI autolog (Databricks tracking when configured). No-op on failure."""
    try:
        import mlflow

        if settings.mlflow_experiment:
            if settings.mlflow_tracking_uri:
                mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            mlflow.set_experiment(settings.mlflow_experiment)
        mlflow.crewai.autolog()
        return True
    except Exception:
        return False


# ----- channel execution (LLM crew + deterministic gate) --------------------

def execute_channel(
    settings: Settings,
    *,
    seller: SellerRef,
    spec: ChannelSpec,
    audience_codes: list[str],
    channel_budget: float,
    max_cpm: Optional[float],
    flight: tuple[Optional[str], Optional[str]],
    identity: dict,
    deal_type: str = "PD",
    execution_rail: str = "direct",
    ssp: Optional[str] = None,   # rail 2 target SSP; defaults to settings.default_ssp
    manager_llm: Any = None,
    worker_llm: Any = None,
    emit: Emit = _noop,
) -> ChannelOutcome:
    """Run one channel crew, then deterministically book-or-pass its recommendation."""
    from .llm import build_llm

    ssp = ssp or settings.default_ssp

    base = ChannelOutcome(
        channel=spec.channel,
        capability=spec.capability,
        decision="PASSED",
        seller_id=seller.agent_id,
        seller_name=seller.name,
        channel_budget=channel_budget,
    )

    if not seller.base_url:
        return base.model_copy(update={"decision": "NO_SELLER", "reason": "no reachable seller URL"})

    manager_llm = manager_llm or build_llm(settings, model=settings.manager_llm_model, temperature=0.3)
    worker_llm = worker_llm or build_llm(settings, model=settings.llm_model, temperature=0.2)

    emit("channel.researching", channel=spec.channel,
         message=f"{spec.role} crew is sourcing {spec.channel} inventory from {seller.name}")
    crew = build_channel_crew(
        spec,
        manager_llm,
        worker_llm,
        seller_base_url=seller.base_url,
        mcp_path=settings.seller_mcp_path,
        identity=identity,
        auth_token=settings.seller_auth_token(),
        audience_codes=audience_codes,
        channel_budget=channel_budget,
        max_cpm=max_cpm,
        flight=flight,
        step_callback=_make_step_callback(
            emit, channel=spec.channel, lead_role=spec.role, lead_level="L2"
        ),
    )
    result = crew.kickoff()
    rec = result.pydantic if isinstance(result.pydantic, ChannelRecommendation) else ChannelRecommendation()

    if not rec.product_id:
        emit("channel.recommended", channel=spec.channel, product_id="", product_name="",
             final_cpm=None, message="crew found no matching product")
        return base.model_copy(update={"decision": "NO_PRODUCT", "reason": "crew found no matching product"})

    emit("channel.recommended", channel=spec.channel, product_id=rec.product_id,
         product_name=rec.product_name, final_cpm=rec.final_cpm, deal_type=rec.deal_type,
         audience_codes=rec.audience_codes, rationale=rec.rationale)

    headers = settings.seller_mcp_headers() or None

    def _exec_call(tool: str, detail: str = "", phase: str = "book", data: Any = None) -> None:
        # Surface a deterministic seller-MCP call as an L3 execution-specialist tool step.
        emit("agent.step", channel=spec.channel, kind="tool", tool=tool,
             agent=_EXECUTION_AGENT, level="L3", phase=phase, detail=detail,
             data=_json_safe(data))

    def _exec_result(tool: str, payload: Any, phase: str = "book") -> None:
        # The seller's response for the call above — the `←` return side of the trace.
        emit("agent.step", channel=spec.channel, kind="result", tool=tool,
             agent=_EXECUTION_AGENT, level="L3", phase=phase, detail=_summarize_result(payload),
             data=_json_safe(payload))

    async def _price_and_maybe_book() -> dict:
        async with SellerMCPClient(seller.base_url, mcp_path=settings.seller_mcp_path, headers=headers) as c:
            _exec_call("get_pricing", detail=f"product {rec.product_id} (authoritative)", phase="gate",
                       data={"product_id": rec.product_id, **identity})
            pricing = await c.get_pricing(rec.product_id, **identity)
            _exec_result("get_pricing", pricing, phase="gate")
            final_cpm = _price_of(pricing)
            # The seller's rate-card math for this buyer's tier (base → discounts → final,
            # with the card rule that produced it) — surfaced so the UI can show the price
            # came off the SHARED rate card, not a negotiation.
            card = (
                {k: pricing.get(k) for k in
                 ("base_price", "tier_discount", "volume_discount", "final_price", "rationale")}
                if isinstance(pricing, dict) else None
            )
            decision, imps, reason = book_or_pass(
                final_cpm=final_cpm, max_cpm=max_cpm, channel_budget=channel_budget
            )
            if decision != "BOOK":
                return {"decision": "PASSED", "reason": reason, "final_cpm": final_cpm,
                        "impressions": imps, "card": card}
            # commit: quote → route rail (the brief's execution signal picks the fulfilment
            # path; the seller re-checks its own minimums either way)
            try:
                _exec_call("request_quote", detail=f"{deal_type} · {imps:,} imps",
                           data={"product_id": rec.product_id, "deal_type": deal_type,
                                 "impressions": imps, "flight_start": flight[0],
                                 "flight_end": flight[1], **identity})
                quote = await c.request_quote(
                    rec.product_id, deal_type=deal_type, impressions=imps,
                    flight_start=flight[0], flight_end=flight[1], **identity,
                )
                _exec_result("request_quote", quote)
                q_cpm = _price_of(quote) or final_cpm
                if execution_rail == "programmatic":
                    # rail 2: PG Deal ID → SSP (the buyer's DSP serves against the deal)
                    _exec_call("create_deal", detail=f"quote {quote['quote_id']} → PG Deal ID",
                               data={"quote_id": quote["quote_id"]})
                    deal = await c.create_deal(quote["quote_id"])
                    _exec_result("create_deal", deal)
                    deal_id = deal.get("deal_id")
                    _exec_call("distribute_deal_via_ssp", detail=f"deal {deal_id} → {ssp}",
                               data={"deal_id": deal_id, "ssp": ssp})
                    dist = await c.distribute_deal_via_ssp(deal_id, ssp)
                    _exec_result("distribute_deal_via_ssp", dist)
                    return {
                        "decision": "BOOKED", "reason": reason, "final_cpm": q_cpm, "impressions": imps,
                        "quote_id": quote["quote_id"], "deal_id": deal_id, "ssp": ssp, "card": card,
                        # per-DSP activation text (SDK DealBookingResponse) — the buyer's next step
                        "activation_instructions": deal.get("activation_instructions"),
                    }
                # rail 1 (hero): OpenDirect IO — order + line on the seller's own inventory
                _exec_call("create_order", data=dict(identity) or None)
                order = await c.create_order(**identity)
                _exec_result("create_order", order)
                order_id = order.get("execution_order_id") or order.get("order_id")
                _exec_call("create_line", detail=f"quote {quote['quote_id']} → order {order_id}",
                           data={"order_id": order_id, "quote_id": quote["quote_id"]})
                line = await c.create_line(order_id, quote["quote_id"])
                _exec_result("create_line", line)
                creative_args = {
                    "order_id": order_id,
                    "asset_url": f"https://cdn.demo/creatives/{rec.product_id}.mp4",
                    "name": f"{rec.product_name} creative",
                }
                _exec_call("submit_creative", detail=f"order {order_id} → creative READY",
                           data=creative_args)
                creative = await c.submit_creative(**creative_args)
                _exec_result("submit_creative", creative)
                return {
                    "decision": "BOOKED", "reason": reason, "final_cpm": q_cpm, "impressions": imps,
                    "quote_id": quote["quote_id"], "order_id": order_id,
                    "creative_id": creative.get("creative_id"),
                    "creative_status": creative.get("status"), "card": card,
                }
            except Exception as e:  # seller rejected the booking (e.g. below minimum)
                return {"decision": "PASSED", "reason": f"seller rejected booking: {e}",
                        "final_cpm": final_cpm, "impressions": imps, "card": card}

    res = run_sync(_price_and_maybe_book())
    emit("channel.priced", channel=spec.channel, product_id=rec.product_id,
         final_cpm=res.get("final_cpm"), max_cpm=max_cpm, card=res.get("card"))
    emit("channel.decision", channel=spec.channel, decision=res.get("decision"),
         reason=res.get("reason"), final_cpm=res.get("final_cpm"),
         impressions=res.get("impressions"), order_id=res.get("order_id"),
         deal_id=res.get("deal_id"), ssp=res.get("ssp"), execution_rail=execution_rail,
         creative_id=res.get("creative_id"), creative_status=res.get("creative_status"),
         product_id=rec.product_id, product_name=rec.product_name,
         seller_name=seller.name, channel_budget=channel_budget, card=res.get("card"),
         activation_instructions=res.get("activation_instructions"))
    return base.model_copy(update={"product_id": rec.product_id, **res})


# ----- top-level run ---------------------------------------------------------

def run_brief(
    settings: Settings, brief_id: str, *, tier: Optional[str] = None, emit: Emit = _noop
) -> RunResult:
    """The POST /run path: resolve tier → plan → allocate → per-channel book-or-pass.

    `tier` is the per-run identity profile (public/seat/agency/advertiser) the buyer
    presents; when omitted it falls back to the buyer's registry trust.
    `emit(event_type, **fields)` is an optional sink (the demo UI's progress hub) called
    at each milestone and on each crew step; it defaults to a no-op for the JSON API.
    """
    from . import db
    from .agents.level3.audience_planner import plan_audiences
    from .identity import resolve_identity

    enable_tracing(settings)

    brief = db.get_brief(settings, brief_id)
    if brief is None:
        raise ValueError(f"brief '{brief_id}' not found")

    max_cpm = (brief.get("deal_preferences") or {}).get("max_cpm")
    max_cpm = float(max_cpm) if max_cpm is not None else None
    emit("run.started", brief_id=brief_id, brief_name=brief.get("name"),
         total_budget=float(brief.get("total_budget") or 0), currency=brief.get("currency", "USD"),
         objectives=brief.get("objectives") or [],
         interests=(brief.get("target_audience") or {}).get("interests") or [],
         channels=[{"channel": c.get("channel"), "budget_pct": c.get("budget_pct")}
                   for c in (brief.get("channels") or [])],
         max_cpm=max_cpm)

    registry = get_registry_client(settings)
    # Tier-gated identity: the buyer presents only the identity the chosen profile allows
    # (per-run selector, else registry trust), which drives the seller's tier — and thus
    # book-or-pass.
    identity, buyer_tier = resolve_identity(settings, tier=tier, registry=registry)
    emit("identity.resolved", tier=buyer_tier, identity=identity)

    interests = (brief.get("target_audience") or {}).get("interests") or []
    emit("audience.planning", interests=interests)
    plan: AudiencePlan = plan_audiences(settings, interests=interests, brief_id=brief_id)
    codes = plan.all_codes()
    emit("audience.planned", codes=codes,
         segments=[{"interest": s.interest,
                    "codes": [{"code": c.code, "name": c.name} for c in s.codes]}
                   for s in plan.segments])

    allocations = allocate_budget(brief)
    _attach_rationale(settings, brief, allocations, emit=emit)
    emit("budget.allocated",
         allocations=[{"channel": a.channel, "budget_pct": a.budget_pct,
                       "budget": a.budget, "rationale": a.rationale} for a in allocations])

    flight = (brief.get("start_date"), brief.get("end_date"))
    deal_type = _deal_type(brief)
    execution_rail = _execution_rail(brief)
    emit("rail.routed", execution_rail=execution_rail,
         message=("Programmatic: PG Deal ID distributed to an SSP — the buyer's DSP serves against it"
                  if execution_rail == "programmatic"
                  else "Direct IO: OpenDirect order guaranteed on the seller's own inventory"))

    # Phase 1 (sequential, fast): resolve each channel's seller from the registry and emit
    # its channel.started / seller.discovered (or NO_SELLER). Phase 2 runs the surviving
    # channels' crews in PARALLEL — one L2 lead per channel, kicked off at the same time.
    outcomes: list[Optional[ChannelOutcome]] = [None] * len(allocations)
    runnable: list[tuple[int, ChannelAllocation, ChannelSpec, SellerRef]] = []
    for i, alloc in enumerate(allocations):
        emit("channel.started", channel=alloc.channel, capability=alloc.capability,
             budget=alloc.budget)
        spec = SPECS.get(alloc.channel)
        if spec is None:
            emit("channel.decision", channel=alloc.channel, decision="NO_SELLER",
                 reason="unsupported channel", channel_budget=alloc.budget)
            outcomes[i] = ChannelOutcome(
                channel=alloc.channel, capability=alloc.capability, decision="NO_SELLER",
                reason="unsupported channel", channel_budget=alloc.budget,
            )
            continue
        seller = _resolve_seller(registry, alloc.capability)
        if seller is None:
            emit("channel.decision", channel=alloc.channel, decision="NO_SELLER",
                 reason=f"no reachable seller for '{alloc.capability}'", channel_budget=alloc.budget)
            outcomes[i] = ChannelOutcome(
                channel=alloc.channel, capability=alloc.capability, decision="NO_SELLER",
                reason=f"no reachable seller for '{alloc.capability}'", channel_budget=alloc.budget,
            )
            continue
        emit("seller.discovered", channel=alloc.channel, seller_id=seller.agent_id,
             seller_name=seller.name)
        runnable.append((i, alloc, spec, seller))

    def _run_channel(item: tuple[int, ChannelAllocation, ChannelSpec, SellerRef]) -> tuple[int, ChannelOutcome]:
        idx, alloc, spec, seller = item
        # Each channel builds its own LLMs so parallel crews never share a client.
        outcome = execute_channel(
            settings, seller=seller, spec=spec, audience_codes=codes,
            channel_budget=alloc.budget, max_cpm=max_cpm, flight=flight,
            identity=identity, deal_type=deal_type, execution_rail=execution_rail, emit=emit,
        )
        return idx, outcome

    # Fan the channel crews out concurrently (thread-per-channel; the crews are IO-bound on
    # LLM + seller MCP, and the progress hub is thread-safe so events interleave per channel).
    if len(runnable) > 1:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(runnable)) as pool:
            for idx, outcome in pool.map(_run_channel, runnable):
                outcomes[idx] = outcome
    else:
        for item in runnable:
            idx, outcome = _run_channel(item)
            outcomes[idx] = outcome

    outcomes = [o for o in outcomes if o is not None]

    result = RunResult(
        brief_id=brief_id, buyer_id=brief.get("buyer_id") or settings.buyer_id,
        buyer_tier=buyer_tier, audience_plan=plan, allocations=allocations, outcomes=outcomes,
    )
    booked = result.booked()
    emit("run.completed", buyer_tier=buyer_tier,
         booked=len(booked), passed=len(outcomes) - len(booked),
         spend=round(sum((o.final_cpm or 0) * (o.impressions or 0) / 1000 for o in booked), 2),
         result=result.model_dump())
    return result


def _resolve_seller(registry, capability: str) -> Optional[SellerRef]:
    sellers = [s for s in registry.discover_sellers(capability) if s.base_url]
    return sellers[0] if sellers else None


def _attach_rationale(
    settings: Settings, brief: dict, allocations: list[ChannelAllocation], *, emit: Emit = _noop
) -> None:
    """Run the L1 portfolio crew (best-effort) to justify the seeded split; merge its
    rationale into the deterministic allocations. Dollar amounts stay deterministic."""
    try:
        from .llm import build_llm

        crew = build_portfolio_crew(
            build_llm(settings, model=settings.manager_llm_model, temperature=0.3),
            build_llm(settings, model=settings.llm_model, temperature=0.2),
            brief=brief,
            step_callback=_make_step_callback(
                emit, channel="PORTFOLIO", lead_role="Portfolio Manager", lead_level="L1"
            ),
        )
        result = crew.kickoff()
        plan = result.pydantic if isinstance(result.pydantic, PortfolioPlan) else PortfolioPlan()
        by_channel = {a.channel: a.rationale for a in plan.allocations}
        for alloc in allocations:
            if by_channel.get(alloc.channel):
                alloc.rationale = by_channel[alloc.channel]
    except Exception:
        pass  # hierarchy is illustrative; the deterministic split is the source of truth
