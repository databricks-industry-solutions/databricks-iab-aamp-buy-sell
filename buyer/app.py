"""AAMP buyer app (FastAPI).

Data + transaction surface:
  * GET  /healthz             — liveness; touches no database (boots even if asleep)
  * GET  /briefs              — list the buyer's campaign briefs
  * GET  /briefs/{brief_id}   — one brief
  * GET  /sellers[?capability=] — discover sellers from the registry
  * GET  /packages?capability= — discovery → connect that seller's /mcp/ → list_packages
  * GET  /pricing?capability=&product_id= — discovery → seller /mcp/ → get_pricing
  * GET  /briefs/{brief_id}/audience-plan — L3 planner: interests → validated IAB codes
  * POST /run/{brief_id}      — full L1/L2/L3 book-or-pass flow (synchronous JSON)

Demo / presenter surface (drive and watch a run):
  * GET  /                    — lightweight UI: pick a brief + identity profile, run it live
  * GET  /status              — buyer id + default (registry-trust) tier
  * POST /runs/{brief_id}[?tier=] — start a run in the background; returns {run_id}
  * GET  /runs/{run_id}/events — Server-Sent Events stream of the run's progress
  * GET  /runs/{run_id}       — final result / status of a run

The `tier` param (public/seat/agency/advertiser) is the per-run identity profile the
buyer presents to sellers; when omitted the tier falls back to the buyer's registry trust.

Run:
    uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from typing import Optional

# src/ layout: make buyer_agent importable without an editable install (Apps run
# `uvicorn app:app` from this dir with only requirements.txt installed).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from buyer_agent import db  # noqa: E402
from buyer_agent.config import get_settings  # noqa: E402
from buyer_agent.progress import hub  # noqa: E402
from buyer_agent.registry_client import SellerRef, get_registry_client  # noqa: E402
from buyer_agent.seller_client import SellerMCPClient, SellerMCPError  # noqa: E402

settings = get_settings()

app = FastAPI(title=f"AAMP Buyer — {settings.buyer_id}")

_UI_INDEX = os.path.join(os.path.dirname(__file__), "static", "index.html")


def _resolve_seller(capability: str) -> SellerRef:
    """Discover a reachable (trusted, non-decoy) seller for a capability, or 4xx."""
    sellers = get_registry_client(settings).discover_sellers(capability)
    if not sellers:
        raise HTTPException(status_code=404, detail=f"no seller advertises capability '{capability}'")
    reachable = [s for s in sellers if s.base_url]
    if not reachable:
        raise HTTPException(
            status_code=503,
            detail=(
                f"seller(s) for '{capability}' have no reachable URL; with the registry "
                f"app this comes from the seller's agent card url, otherwise set "
                f"AAMP_SELLER_URLS (e.g. '{capability}=https://<seller app url>')"
            ),
        )
    return reachable[0]


def _buyer_identity_args(tier: Optional[str] = None) -> dict:
    """The identity to present to the seller, gated to `tier` (the per-run profile), so
    /packages and /pricing reflect the same tier the /run flow uses. When `tier` is
    omitted it falls back to the buyer's registry trust."""
    from buyer_agent.identity import resolve_identity

    identity, _tier = resolve_identity(settings, tier=tier)
    return identity


@app.get("/healthz")
def healthz() -> dict:
    # No DB work: the app boots even if Lakebase is asleep or empty.
    return {"status": "ok", "buyer": settings.buyer_id, "database": settings.buyer_database}


@app.get("/briefs")
def list_briefs() -> list[dict]:
    return db.list_briefs(settings)


@app.get("/briefs/{brief_id}")
def get_brief(brief_id: str) -> dict:
    brief = db.get_brief(settings, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    return brief


@app.get("/briefs/{brief_id}/audience-plan")
def audience_plan(brief_id: str) -> dict:
    """L3 audience planner: resolve the brief's free-text interests to validated IAB
    Audience Taxonomy codes (the LLM can only pick codes the taxonomy tool returns)."""
    brief = db.get_brief(settings, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    interests = (brief.get("target_audience") or {}).get("interests") or []
    # Lazy import: keep the crewai/LLM stack off the boot path of the data endpoints.
    from buyer_agent.agents.level3.audience_planner import plan_audiences

    return plan_audiences(settings, interests=interests, brief_id=brief_id).model_dump()


@app.post("/run/{brief_id}")
def run(brief_id: str, tier: Optional[str] = None) -> dict:
    """Run the full hierarchical flow for a brief: L3 audience plan → L1 budget allocation
    → per-channel L2/L3 crew → deterministic book-or-pass over each seller's MCP. The
    seller's list_orders is the booking ledger; this returns the per-channel decisions.

    `tier` (public/seat/agency/advertiser) is the identity profile the buyer presents."""
    # Lazy import: keep the crewai/LLM stack off the boot path of the data endpoints.
    from buyer_agent.flow import run_brief

    try:
        return run_brief(settings, brief_id, tier=tier).model_dump()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ----- demo / presenter UI ---------------------------------------------------

@app.get("/", include_in_schema=False)
def ui() -> FileResponse:
    """The presenter UI: the built React console when frontend/dist exists, else the
    legacy single-file page."""
    if _DIST.exists():
        return FileResponse(_DIST / "index.html")
    return FileResponse(_UI_INDEX)


@app.get("/status")
def status() -> dict:
    """Buyer id, the default (registry-trust) tier, and the identity profiles a presenter
    can choose from — drives the header badge and the profile selector."""
    from buyer_agent.identity import VALID_TIERS, resolve_identity

    try:
        identity, tier = resolve_identity(settings)
    except Exception:
        identity, tier = {}, "public"
    return {
        "buyer_id": settings.buyer_id,
        "tier": tier,
        "identity": identity,
        "tiers": list(VALID_TIERS),
    }


def _run_worker(run_id: str, brief_id: str, tier: Optional[str]) -> None:
    """Execute the full flow in a background thread, streaming progress into the hub."""
    from buyer_agent.flow import run_brief

    emit = hub.emitter(run_id)
    try:
        result = run_brief(settings, brief_id, tier=tier, emit=emit)
        hub.finish(run_id, result=result.model_dump())
    except Exception as e:  # surface the failure to the watching UI
        emit("run.error", message=str(e))
        hub.finish(run_id, error=str(e))


@app.post("/runs/{brief_id}")
def start_run(brief_id: str, tier: Optional[str] = None) -> dict:
    """Kick off a run in the background and return its run_id; the UI then subscribes
    to GET /runs/{run_id}/events to follow along. `tier` is the identity profile to
    present (public/seat/agency/advertiser)."""
    if db.get_brief(settings, brief_id) is None:
        raise HTTPException(status_code=404, detail="brief not found")
    run_id = hub.create(brief_id=brief_id)
    threading.Thread(target=_run_worker, args=(run_id, brief_id, tier), daemon=True).start()
    return {"run_id": run_id, "brief_id": brief_id}


@app.get("/runs/{run_id}")
def run_status(run_id: str) -> dict:
    """Final result / current status of a run (for late joiners or polling fallback)."""
    st = hub.status(run_id)
    if st is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return st


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """Stream a run's progress as Server-Sent Events until it completes (15-min cap)."""

    async def _stream():
        cursor, started = 0, time.monotonic()
        yield ": connected\n\n"  # open the stream immediately
        while True:
            events, done, exists = hub.snapshot(run_id, cursor)
            if not exists:
                yield f"event: error\ndata: {json.dumps({'message': 'unknown run'})}\n\n"
                return
            for ev in events:
                yield f"data: {json.dumps(ev)}\n\n"
            cursor += len(events)
            if done:
                yield "event: done\ndata: {}\n\n"
                return
            if time.monotonic() - started > 900:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(0.35)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/sellers")
def discover_sellers(capability: Optional[str] = None) -> list[dict]:
    """Sellers from the registry (optionally filtered to one capability), minus
    BLOCKED decoys, with reachable base_url resolved via the config map."""
    sellers = get_registry_client(settings).discover_sellers(capability)
    return [asdict(s) for s in sellers]


@app.get("/packages")
async def list_packages(capability: str, featured_only: bool = False, tier: Optional[str] = None) -> list[dict]:
    """Discover the seller for a channel, connect its MCP, and list packages as this
    buyer (the `tier` profile drives the tier the seller reveals)."""
    seller = _resolve_seller(capability)
    try:
        async with SellerMCPClient.for_seller(
            seller, mcp_path=settings.seller_mcp_path, headers=settings.seller_mcp_headers() or None
        ) as client:
            return await client.list_packages(featured_only=featured_only, **_buyer_identity_args(tier))
    except SellerMCPError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/pricing")
async def get_pricing(capability: str, product_id: str, volume: int = 0, tier: Optional[str] = None) -> dict:
    """Discover the seller for a channel and get a deterministic price for a product
    at the given identity `tier`."""
    seller = _resolve_seller(capability)
    try:
        async with SellerMCPClient.for_seller(
            seller, mcp_path=settings.seller_mcp_path, headers=settings.seller_mcp_headers() or None
        ) as client:
            price = await client.get_pricing(product_id, volume=volume, **_buyer_identity_args(tier))
    except SellerMCPError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if price is None:
        raise HTTPException(status_code=404, detail=f"product '{product_id}' not found at seller")
    return price


@app.get("/bookings")
async def bookings() -> dict:
    """Cross-seller booking ledger — no crew, pure reads. Every OpenDirect order
    (rail 1) and PG deal (rail 2) held by each registered seller, pulled from that
    seller's own MCP ledger tools (list_orders / list_deals) and tagged with the
    seller it lives on. Powers the console's Bookings dashboard."""
    sellers = [s for s in get_registry_client(settings).discover_sellers(None) if s.base_url]
    seen: set[str] = set()
    orders: list[dict] = []
    deals: list[dict] = []
    errors: list[dict] = []
    for seller in sellers:
        if seller.base_url in seen:  # one ledger per seller, however many capabilities
            continue
        seen.add(seller.base_url)
        tag = {"seller_id": seller.agent_id, "seller_name": seller.name, "capability": seller.capability}
        try:
            async with SellerMCPClient.for_seller(
                seller, mcp_path=settings.seller_mcp_path, headers=settings.seller_mcp_headers() or None
            ) as client:
                for o in await client.call("list_orders") or []:
                    orders.append({**o, **tag})
                for d in await client.call("list_deals") or []:
                    deals.append({**d, **tag})
        except SellerMCPError as e:  # one seller down shouldn't blank the whole ledger
            errors.append({**tag, "error": str(e)})
    orders.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    deals.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return {"orders": orders, "deals": deals, "errors": errors}


# ===========================================================================
# Static frontend — the built React console (mounted LAST so /briefs, /runs,
# /status etc. all win). The SPA catch-all serves index.html for client-side
# routes; unknown api paths 404 instead of returning HTML. When frontend/dist
# is absent, the legacy static/index.html keeps serving from `ui()` above.
# ===========================================================================
_DIST = Path(__file__).parent / "frontend" / "dist"
_RESERVED = ("briefs", "run", "runs", "status", "sellers", "packages", "pricing", "bookings", "healthz")

if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str):
        if full_path.startswith(_RESERVED):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")  # client-side route → SPA shell
