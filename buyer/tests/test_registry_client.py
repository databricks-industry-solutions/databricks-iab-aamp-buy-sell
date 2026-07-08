"""Deterministic tests for HttpRegistryClient mapping (no network).

Stub the HTTP layer and assert the buyer maps the registry app's agent payloads to
SellerRefs: channel comes from the card's `inventory_types`, the reachable URL from the
card's `url`, BLOCKED sellers are skipped, and verify-by-URL flows through."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent.config import Settings  # noqa: E402
from buyer_agent.registry_client import (  # noqa: E402
    DbRegistryClient,
    HttpRegistryClient,
    get_registry_client,
)


def _agent(agent_id, name, url, inv, trust):
    return {
        "agent_id": agent_id,
        "trust_status": trust,
        "agent_card": {"name": name, "url": url, "inventory_types": inv},
    }


_AGENTS = [
    _agent("ag-summit", "summit", "https://summit.example.app", ["ctv"], "registered"),
    _agent("ag-beacon", "beacon", "https://beacon.example.app", ["linear_tv"], "approved"),
    _agent("ag-decoy", "decoy", "https://decoy.example.app", ["ctv"], "blocked"),
]


def _client(monkeypatch, *, agents=_AGENTS, verify=None):
    c = HttpRegistryClient(Settings(registry_url="https://registry.example.app", registry_token=""))
    monkeypatch.setattr(c, "_get", lambda path, params=None: agents)
    monkeypatch.setattr(c, "_post", lambda path, body: verify or {})
    return c


def test_factory_picks_http_when_registry_url_set():
    assert isinstance(get_registry_client(Settings(registry_url="https://r.example")), HttpRegistryClient)
    assert isinstance(get_registry_client(Settings()), DbRegistryClient)


def test_discover_filters_by_capability_from_card(monkeypatch):
    c = _client(monkeypatch)
    ctv = c.discover_sellers("ctv")
    assert [s.agent_id for s in ctv] == ["ag-summit"]  # beacon is linear, decoy is blocked
    assert ctv[0].base_url == "https://summit.example.app"
    assert ctv[0].capability == "ctv"


def test_discover_skips_blocked(monkeypatch):
    c = _client(monkeypatch)
    assert all(s.agent_id != "ag-decoy" for s in c.discover_sellers())


def test_discover_all_when_no_capability(monkeypatch):
    c = _client(monkeypatch)
    got = {s.agent_id for s in c.discover_sellers()}
    assert got == {"ag-summit", "ag-beacon"}  # blocked decoy excluded


def test_verify_self_returns_registry_verdict(monkeypatch):
    verdict = {"trust_status": "approved", "max_access_tier": "advertiser", "is_blocked": False}
    c = _client(monkeypatch, verify=verdict)
    c.settings = Settings(registry_url="https://registry.example.app", buyer_url="https://buyer.example.app")
    assert c.verify_self() == verdict


def test_verify_self_none_without_buyer_url(monkeypatch):
    c = _client(monkeypatch, verify={"trust_status": "approved"})
    # No buyer_url and no app-url env → cannot resolve identity → None (caller → public).
    monkeypatch.delenv("AAMP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_URL", raising=False)
    assert c.verify_self() is None
