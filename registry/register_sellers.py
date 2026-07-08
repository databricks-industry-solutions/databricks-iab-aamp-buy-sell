#!/usr/bin/env python3
"""Dynamically register the live seller apps into the registry — the seed→app handshake.

WHY client-side: Databricks Apps sit behind an OAuth proxy, so the registry app can't
do an unauthenticated server-side GET of a seller's /.well-known/agent.json (the SDK's
resolve_agent_access path). Instead this runs LOCALLY (with the deployer's token):
  1. resolve each seller app's public URL,
  2. fetch its agent card (authenticated),
  3. POST it to the registry's faithful `register_agent` endpoint (POST /registry/agents).
The registry stores it via the SDK's AgentRegistryService verbatim (idempotent by URL).

Usage:  python registry/register_sellers.py <prefix> [profile] [trust_status]
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

SELLERS = ["summit", "beacon"]
_SEED_SELLERS = Path(__file__).resolve().parent.parent / "seed" / "sellers"


def _decoys() -> list[dict]:
    """The seed's BLOCKED decoy sellers (single source of truth: seed/sellers/*.yaml).

    Decoys have no running app, so — unlike real sellers — we can't fetch a live agent
    card; we synthesize a minimal one from the seed. Registering them (trust=blocked)
    into the registry KV keeps the MCP directory complete, so a buyer discovering over
    MCP still sees (and trust-rejects) them, exactly as the relational directory showed.
    """
    import yaml  # provided by the seed toolchain

    out: list[dict] = []
    for slug in SELLERS:
        matches = sorted(_SEED_SELLERS.glob(f"{slug}-*.yaml"))
        if not matches:
            continue
        cfg = yaml.safe_load(matches[0].read_text()) or {}
        decoy = (cfg.get("registry") or {}).get("decoy")
        if not decoy:
            continue
        media_type = (cfg.get("seller") or {}).get("media_type", "")
        url = f"https://{decoy['domain']}"
        out.append({
            "card": {
                "name": decoy["name"],
                "description": f"{decoy['name']} — discovered, then trust-rejected (decoy, no live server).",
                "url": url,
                "version": "1.0.0",
                "provider": {"name": decoy["name"], "url": url},
                "inventory_types": [media_type] if media_type else [],
            },
            "trust": str(decoy.get("trust_level", "BLOCKED")).lower(),
        })
    return out


def _buyers() -> list[dict]:
    """The seed's buyers (seed/buyers.yaml) with their seeded trust.

    The deployed buyer verifies ITSELF against the registry by its own app URL
    (`POST /registry/verify {agent_url}`), so the KV must hold a buyer agent whose
    card URL is the LIVE buyer app URL — the seed's relational row only carries a
    placeholder domain. Trust comes from the seed (APPROVED → agency tier), which is
    the "flip the buyer's trust to move the price" demo lever.
    """
    import yaml  # provided by the seed toolchain

    cfg_path = _SEED_SELLERS.parent / "buyers.yaml"
    if not cfg_path.exists():
        return []
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    return list(cfg.get("buyers") or [])


def _req(url: str, auth: dict, *, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(auth)
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers=headers)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def main(prefix: str, profile: str | None, trust: str = "registered") -> int:
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=profile) if profile and profile != "DEFAULT" else WorkspaceClient()
    auth = w.config.authenticate()  # {"Authorization": "Bearer <token>"}

    reg_url = w.apps.get(name=f"{prefix}-registry-app").url.rstrip("/")
    for slug in SELLERS:
        s_url = w.apps.get(name=f"{prefix}-seller-{slug}").url.rstrip("/")
        card = _req(f"{s_url}/.well-known/agent.json", auth)
        res = _req(
            f"{reg_url}/registry/agents",
            auth,
            body={"agent_card": card, "agent_type": "seller", "trust_status": trust},
        )
        print(f"  • registered seller-{slug}: agent_id={res.get('agent_id')} "
              f"trust={res.get('trust_status')} url={card.get('url')}")

    # Register the BLOCKED decoys too, so the KV directory (what a buyer discovers over
    # MCP) mirrors the seeded relational directory — the buyer discovers, then rejects them.
    decoys = _decoys()
    for d in decoys:
        try:
            res = _req(
                f"{reg_url}/registry/agents",
                auth,
                body={"agent_card": d["card"], "agent_type": "seller", "trust_status": d["trust"]},
            )
            print(f"  • registered decoy {d['card']['name']}: agent_id={res.get('agent_id')} "
                  f"trust={res.get('trust_status')} url={d['card']['url']}")
        except Exception as e:  # noqa: BLE001 — a decoy failing must not break the deploy
            print(f"  • decoy {d['card']['name']}: register FAILED ({type(e).__name__}: {e})")

    # Register the buyer(s) by their LIVE app URL with the seeded trust, so the deployed
    # buyer's verify_self (by URL) resolves to the seeded tier instead of unknown→public.
    buyers = _buyers()
    for b in buyers:
        try:
            b_url = w.apps.get(name=f"{prefix}-buyer-app").url.rstrip("/")
            res = _req(
                f"{reg_url}/registry/agents",
                auth,
                body={
                    "agent_card": {
                        "name": b.get("name", b["id"]),
                        "description": f"{b.get('name', b['id'])} — the demo's hero buyer agent.",
                        "url": b_url,
                        "version": "1.0.0",
                        "provider": {"name": b.get("name", b["id"]), "url": b_url},
                    },
                    "agent_type": "buyer",
                    "trust_status": str(b.get("trust", "UNKNOWN")).lower(),
                },
            )
            print(f"  • registered buyer {b['id']}: agent_id={res.get('agent_id')} "
                  f"trust={res.get('trust_status')} url={b_url}")
        except Exception as e:  # noqa: BLE001 — a buyer entry failing must not break the deploy
            print(f"  • buyer {b['id']}: register FAILED ({type(e).__name__}: {e})")

    print(f"✓ {len(SELLERS)} seller(s) + {len(decoys)} decoy(s) + {len(buyers)} buyer(s) "
          f"registered into {reg_url}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python registry/register_sellers.py <prefix> [profile] [trust_status]")
        sys.exit(2)
    profile = sys.argv[2] if len(sys.argv) > 2 else None
    trust = sys.argv[3] if len(sys.argv) > 3 else "registered"
    sys.exit(main(sys.argv[1], profile, trust))
