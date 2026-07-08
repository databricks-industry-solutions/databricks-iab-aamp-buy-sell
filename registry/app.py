"""The AAMP agent registry as ONE Databricks App: REST + MCP over a RegistryService.

Mirrors the seller app exactly in shape: no separate MCP server, the same operations
exposed two ways over one service, and STATELESS BY DESIGN (no DB work at startup, no
cache — every call opens a fresh `service_scope`). The registry is itself an agent:
it serves its own /.well-known/agent.json.

  * REST — `GET /registry/agents`, `GET /registry/agents/{id}`, `POST /registry/agents`
           (register), `POST /registry/agents/discover`, `POST /registry/verify`,
           `PUT /registry/agents/{id}/trust`, `DELETE /registry/agents/{id}`.
  * MCP  — `/mcp/` (streamable HTTP); each tool wraps the identical RegistryService method.

Run: uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

# src/ layout: make registry_mcp importable without an editable install.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from registry_mcp.config import get_settings  # noqa: E402
from registry_mcp.service import RegistryService, service_scope  # noqa: E402

settings = get_settings()


async def get_service() -> AsyncIterator[RegistryService]:
    """FastAPI dependency: a fresh, DB-backed RegistryService per request (no cache)."""
    async with service_scope(settings) as s:
        yield s


# ===========================================================================
# MCP surface — tools are thin wrappers over RegistryService (agent-to-agent)
# ===========================================================================
mcp = FastMCP(f"AAMP Registry — {settings.registry_org_id}", streamable_http_path="/")


@mcp.tool()
async def list_agents(agent_type: Optional[str] = None, trust_status: Optional[str] = None) -> list[dict]:
    """List registered agents (the directory). Filter by agent_type (buyer/seller/…)
    or trust_status (unknown/registered/approved/preferred/blocked)."""
    async with service_scope(settings) as s:
        return await s.list_agents(agent_type=agent_type, trust_status=trust_status)


@mcp.tool()
async def get_agent(agent_id: str) -> Optional[dict]:
    """Fetch one registered agent by its local registry id."""
    async with service_scope(settings) as s:
        return await s.get_agent(agent_id)


@mcp.tool()
async def discover_agent(agent_url: str) -> dict:
    """Resolve an agent by URL (SDK flow: local → fetch card → external registries →
    trust → register locally). Returns {agent, max_access_tier, is_blocked}."""
    async with service_scope(settings) as s:
        return await s.discover(agent_url)


@mcp.tool()
async def verify_agent(agent_url: str) -> dict:
    """Verify an agent's trust: returns {trust_status, max_access_tier, is_blocked}.
    This is the call a seller makes to map a buyer to its pricing tier ceiling."""
    async with service_scope(settings) as s:
        return await s.verify(agent_url)


@mcp.tool()
async def register_agent(
    agent_card: dict, agent_type: str = "buyer", trust_status: str = "unknown"
) -> dict:
    """Register (or update) an agent from a supplied A2A agent card."""
    async with service_scope(settings) as s:
        return await s.register(agent_card, agent_type=agent_type, trust_status=trust_status)


@mcp.tool()
async def update_trust(agent_id: str, trust_status: str, notes: Optional[str] = None) -> Optional[dict]:
    """Update an agent's trust status (e.g. approve, prefer, block)."""
    async with service_scope(settings) as s:
        return await s.update_trust(agent_id, trust_status=trust_status, notes=notes)


@mcp.tool()
async def remove_agent(agent_id: str) -> dict:
    """Remove an agent from the registry."""
    async with service_scope(settings) as s:
        return {"removed": await s.remove(agent_id)}


# ===========================================================================
# App + lifespan (no DB work at startup — boots even if Lakebase is asleep/empty)
# ===========================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # MCP is mounted at module load (below) so it precedes the SPA catch-all.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title=f"AAMP Registry — {settings.registry_org_id}", lifespan=lifespan)
# Mount the agent-to-agent MCP channel at module load — before the SPA catch-all.
app.mount("/mcp", mcp.streamable_http_app())


# ===========================================================================
# REST surface — same RegistryService methods
# ===========================================================================
@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "registry": settings.registry_org_id, "kv_schema": settings.kv_schema}


@app.get("/registry/agents")
async def api_list_agents(
    agent_type: Optional[str] = None,
    trust_status: Optional[str] = None,
    s: RegistryService = Depends(get_service),
):
    return await s.list_agents(agent_type=agent_type, trust_status=trust_status)


@app.get("/registry/agents/{agent_id}")
async def api_get_agent(agent_id: str, s: RegistryService = Depends(get_service)):
    agent = await s.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


class RegisterBody(BaseModel):
    agent_card: dict
    agent_type: str = "buyer"
    trust_status: str = "unknown"


@app.post("/registry/agents")
async def api_register(body: RegisterBody, s: RegistryService = Depends(get_service)):
    try:
        return await s.register(
            body.agent_card, agent_type=body.agent_type, trust_status=body.trust_status
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class UrlBody(BaseModel):
    agent_url: str


@app.post("/registry/agents/discover")
async def api_discover(body: UrlBody, s: RegistryService = Depends(get_service)):
    return await s.discover(body.agent_url)


@app.post("/registry/verify")
async def api_verify(body: UrlBody, s: RegistryService = Depends(get_service)):
    return await s.verify(body.agent_url)


class TrustBody(BaseModel):
    trust_status: str
    notes: Optional[str] = None


@app.put("/registry/agents/{agent_id}/trust")
async def api_update_trust(agent_id: str, body: TrustBody, s: RegistryService = Depends(get_service)):
    try:
        agent = await s.update_trust(agent_id, trust_status=body.trust_status, notes=body.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent


@app.delete("/registry/agents/{agent_id}")
async def api_remove(agent_id: str, s: RegistryService = Depends(get_service)):
    return {"removed": await s.remove(agent_id)}


def _public_base(request: Request) -> str:
    """The registry's externally-reachable base URL (behind the Databricks Apps proxy)."""
    for env in ("AAMP_PUBLIC_URL", "DATABRICKS_APP_URL"):
        if os.environ.get(env):
            return os.environ[env].rstrip("/")
    fwd_host = request.headers.get("x-forwarded-host")
    if fwd_host:
        proto = request.headers.get("x-forwarded-proto", "https")
        return f"{proto}://{fwd_host}"
    return str(request.base_url).rstrip("/")


@app.get("/.well-known/agent.json")
async def agent_card(request: Request) -> dict:
    """The registry's own A2A discovery card (it is an agent too)."""
    base = _public_base(request)
    return {
        "name": settings.registry_name,
        "description": "IAB AAMP agent registry — directory, discovery, and trust verification.",
        "url": base,
        "version": "1.0.0",
        "provider": {"name": settings.registry_org_id, "url": base},
        "capabilities": {"protocols": ["mcp", "a2a"], "streaming": True},
        "authentication": {"schemes": ["api_key"]},
        "mcp": {"endpoint": f"{base}/mcp/"},
        "skills": [
            {"id": "directory", "name": "Agent directory", "description": "List/get registered agents", "tags": ["registry"]},
            {"id": "discovery", "name": "Agent discovery", "description": "Resolve an agent by URL", "tags": ["registry", "a2a"]},
            {"id": "verification", "name": "Trust verification", "description": "Verify trust → access-tier ceiling", "tags": ["trust"]},
        ],
    }


# ===========================================================================
# Static frontend — the built React console (mounted LAST so /registry, /mcp,
# /healthz, /.well-known all win). SPA catch-all serves index.html for client routes.
# ===========================================================================
_DIST = Path(__file__).parent / "frontend" / "dist"
_RESERVED = ("registry/", "mcp", "healthz", ".well-known/")

if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    async def _spa_root() -> FileResponse:
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    async def _spa(full_path: str) -> FileResponse:
        if full_path.startswith(_RESERVED):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
