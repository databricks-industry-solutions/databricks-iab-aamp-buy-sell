"""Seller YAMLs + buyers.yaml -> one coherent universe across seller / buyer / registry.

Two single-channel sellers (CTV, Linear TV); NO themes. Sell-side rows carry a
`seller` column. Rows mirror the SDK schemas field-for-field (inventory.csv /
audiences.csv headers; flow_state Product, media_kit Package, pricing_tiers
PricingRule). Products never cross sellers. The generated objects are the source
that load.py writes as (1) relational tables and (2) the SDK kv_store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .taxonomy import TRUST_TO_TIER_MAP

_STOP = {"the", "a", "an", "in", "of", "and", "&", "to", "for", "with", "on"}
_SIZES = {"ctv": "1920x1080", "linear_tv": "1920x1080", "display": "300x250",
          "native": "1x1", "audio": "1x1", "dooh": "1920x1080"}

# buyer-side ChannelType -> media_type (mirrors SDK buyer-agent _CHANNEL_MEDIA_TYPE_MAP).
CHANNEL_MEDIA_TYPE = {
    "CTV": "ctv", "DISPLAY": "display", "AUDIO": "audio",
    "NATIVE": "native", "DOOH": "dooh", "LINEAR_TV": "linear_tv",
}

SELLERS_DIR = Path(__file__).resolve().parent / "sellers"
BUYERS_FILE = Path(__file__).resolve().parent / "buyers.yaml"


def _tokens(s: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if w and w not in _STOP}


def resolve_interests(interests: list[str], audiences: list[dict]) -> list[str]:
    """Free-text brief interests -> IAB audience codes via token overlap on name + keywords."""
    out: list[str] = []
    for term in interests:
        ti = _tokens(term)
        best, best_n = None, 0
        for a in audiences:
            at = _tokens(a["name"])
            for kw in a.get("keywords", []):
                at |= _tokens(kw)
            n = len(ti & at)
            if n > best_n:
                best, best_n = a, n
        out.append(best["iab"] if best else f"UNRESOLVED:{term}")
    return out


@dataclass
class Universe:
    seller: dict[str, list[dict]] = field(default_factory=dict)
    buyer: dict[str, list[dict]] = field(default_factory=dict)
    registry: dict[str, list[dict]] = field(default_factory=dict)
    sellers_cfg: list[dict] = field(default_factory=list)   # raw seller configs (for validation)
    buyers_cfg: dict = field(default_factory=dict)
    all_audiences: list[dict] = field(default_factory=list)  # raw audience dicts (name+keywords) for resolve

    def tables(self) -> dict[str, dict[str, list[dict]]]:
        return {"seller": self.seller, "buyer": self.buyer, "registry": self.registry}


def seller_slug(seller_id: str) -> str:
    """seller-summit -> summit  (used for the per-seller kv schema name kv_<slug>)."""
    return seller_id.split("-", 1)[-1]


def load_sellers(path: str | Path = SELLERS_DIR) -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(Path(path).glob("*.yaml"))]


def load_buyers(path: str | Path = BUYERS_FILE) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _rate_card_summary(s: dict) -> dict:
    cpms = [p["base_cpm"] for p in s["products"]]
    return {
        "currency": "USD", "channel": s["seller"]["media_type"], "rate_type": "CPM",
        "card_cpm_low": min(cpms), "card_cpm_high": max(cpms),
        "tiers": [rc["tier"] for rc in s.get("rate_card", [])],
    }


def generate(sellers: list[dict], buyers_cfg: dict) -> Universe:
    hero = buyers_cfg["buyers"][0]
    inventory, audiences, products, packages, pricing_rules = [], [], [], [], []
    registry_agents: list[dict] = []
    all_audiences: list[dict] = []

    for s in sellers:
        sd = s["seller"]
        sid, mt, devs = sd["id"], sd["media_type"], sd["device_types"]
        aud_by_id = {a["id"]: a for a in s["audiences"]}
        inv_by_id = {iv["id"]: iv for iv in s["inventory"]}
        all_audiences.extend(s["audiences"])

        for a in s["audiences"]:
            audiences.append({
                "seller": sid, "id": a["id"], "name": a["name"],
                "description": a.get("description", f"{a['name']} segment"),
                "size": a["size"], "segment_type": a.get("segment_type", "FIRST_PARTY"),
                "status": "ACTIVE", "iab_audience_taxonomy_id": a["iab"],
            })

        for iv in s["inventory"]:
            inventory.append({
                "seller": sid, "id": iv["id"], "name": iv["name"], "parent_id": "",
                "status": "ACTIVE", "sizes": iv.get("sizes", _SIZES.get(mt, "1x1")),
                "ad_formats": iv.get("ad_formats", ["video"]),
                "device_types": iv.get("device_types", devs),
                "inventory_type": mt, "content_categories": iv["content"],
                "floor_price_cpm": iv["floor_cpm"], "currency": "USD",
                "geo_targets": iv.get("geo", ["US"]), "description": iv["name"],
            })

        for p in s["products"]:
            codes = sorted({aud_by_id[aid]["iab"] for aid in p["audiences"]})
            floor = min(inv_by_id[i]["floor_cpm"] for i in p["inventory"])
            content = sorted({c for i in p["inventory"] for c in inv_by_id[i]["content"]})
            products.append({
                "seller": sid, "product_id": p["id"], "name": p["name"], "description": p["name"],
                "inventory_type": mt, "inventory_segment_ids": p["inventory"],
                "supported_deal_types": ["programmaticguaranteed", "preferreddeal"],  # SDK DealType enum values
                "supported_pricing_models": ["cpm"],  # SDK PricingModel enum value
                "base_cpm": p["base_cpm"], "floor_cpm": floor, "currency": "USD",
                "minimum_impressions": p.get("minimum_impressions", 10000),
                "maximum_impressions": p.get("maximum_impressions"),
                "audience_targeting": {"standard_segment_ids": codes},
                "content_targeting": {"iab_content": content},
                "ad_product_targeting": None,
                "audience_capabilities": codes,  # SDK flow_state: list[str] of IAB codes
            })
        prod_by_id = {p["product_id"]: p for p in products if p["seller"] == sid}

        for pk in s["packages"]:
            prods = [prod_by_id[pid] for pid in pk["products"]]
            segs = sorted({c for pr in prods for c in pr["audience_capabilities"]})
            cats = sorted({c for pr in prods for c in pr["content_targeting"]["iab_content"]})
            placements = [{
                "product_id": pr["product_id"], "product_name": pr["name"],
                "ad_formats": inv_by_id[pr["inventory_segment_ids"][0]].get("ad_formats", ["video"]),
                "device_types": devs, "weight": 1.0,
            } for pr in prods]
            packages.append({
                "seller": sid, "package_id": pk["id"], "name": pk["name"], "description": pk["name"],
                "layer": "curated", "status": "active", "placements": placements,  # PackageLayer.CURATED / PackageStatus.ACTIVE = "active"
                "cat": cats, "cattax": 7,  # 7 = IAB Content Taxonomy 3.1
                "audience_capabilities": {"standard_segment_ids": segs, "standard_taxonomy_version": "1.1"},
                "device_types": sorted(set(devs)),
                "ad_formats": sorted({f for pl in placements for f in pl["ad_formats"]}),
                "geo_targets": ["US"],
                "base_price": pk["base_price"], "floor_price": round(min(pr["floor_cpm"] for pr in prods), 2),
                "rate_type": "cpm", "currency": "USD", "tags": [mt, sd["channel"].lower()],  # PricingModel.CPM = "cpm"
                "is_featured": pk.get("featured", False),
            })

        for i, rc in enumerate(s.get("rate_card", [])):
            tier = rc["tier"]
            pricing_rules.append({
                "seller": sid, "rule_id": f"rule-{sid}-{tier}",
                "rule_name": f"{sd['name']} {tier} rate", "priority": i, "access_tier": tier,
                "agency_ids": [hero["agency_id"]] if tier == "agency" and hero.get("agency_id") else [],
                "advertiser_ids": [hero["advertiser_id"]] if tier == "advertiser" and hero.get("advertiser_id") else [],
                "holding_company_ids": [], "product_ids": [], "inventory_types": [mt],
                "base_price_override": rc.get("base_cpm_override"),
                "discount_percentage": rc.get("discount", 0.0),
                "price_floor": rc.get("floor"), "price_ceiling": rc.get("ceiling"),
                "volume_discounts": [],  # empty → SDK engine default schedule
                "negotiation_enabled": False, "max_negotiation_discount": 0.0,
                "valid_from": None, "valid_to": None, "is_active": True,
            })

        reg = s.get("registry", {})
        registry_agents.append({
            "agent_id": sid, "name": sd["name"], "type": "seller", "url": f"https://{sd['domain']}",
            "capabilities": [mt], "trust_level": reg.get("trust_level", "APPROVED"),
            "taxonomies": ["iab-audience-1.1", "iab-content-3.1"], "rate_card": _rate_card_summary(s),
        })
        decoy = reg.get("decoy")
        if decoy:
            registry_agents.append({
                "agent_id": decoy["id"], "name": decoy["name"], "type": "seller",
                "url": f"https://{decoy['domain']}", "capabilities": [mt],
                "trust_level": decoy.get("trust_level", "BLOCKED"), "taxonomies": [], "rate_card": None,
            })

    # ---------------- BUYER ----------------
    buyer_identities, api_keys, registry_trust = [], [], []
    for b in buyers_cfg["buyers"]:
        trust = b.get("trust", "UNKNOWN")
        tier = TRUST_TO_TIER_MAP.get(trust)
        buyer_identities.append({
            "buyer_id": b["id"], "name": b["name"], "seat_id": b.get("seat_id"),
            "agency_id": b.get("agency_id"), "agency_holding_company": b.get("holding_company"),
            "advertiser_id": b.get("advertiser_id"), "tier": tier,
        })
        api_keys.append({
            "key_id": f"key-{b['id']}", "api_key": f"ask_demo_{b['id']}", "buyer_id": b["id"],
            "seat_id": b.get("seat_id"), "agency_id": b.get("agency_id"),
            "advertiser_id": b.get("advertiser_id"), "name": b["name"],
        })
        registry_trust.append({"agent_id": b["id"], "trust_status": trust, "tier_ceiling": tier})
        registry_agents.append({
            "agent_id": b["id"], "name": b["name"], "type": "buyer", "url": f"https://{b['id']}.example",
            "capabilities": [], "trust_level": trust, "taxonomies": [], "rate_card": None,
        })

    briefs = []
    for br in buyers_cfg["briefs"]:
        briefs.append({
            "brief_id": br["id"], "name": br["name"], "buyer_id": br["buyer"],
            "objectives": br.get("objectives", ["reach"]),
            "total_budget": br["total_budget"], "currency": "USD",
            "start_date": br["flight"][0], "end_date": br["flight"][1],
            "target_audience": {"geo": ["US"], "interests": br["interests"]},
            "kpis": br.get("kpis", {}), "channels": br["channels"],
            "deal_preferences": {  # SDK CampaignBrief.deal_preferences — the buy-or-pass ceiling
                "max_cpm": br.get("max_cpm"),
                "preferred_deal_types": br.get("preferred_deal_types", ["programmaticguaranteed", "preferreddeal"]),
                "execution": br.get("execution", "direct"),  # fulfilment rail: direct (IO) | programmatic (PG→SSP)
            },
        })

    return Universe(
        seller={"inventory": inventory, "audiences": audiences, "products": products,
                "packages": packages, "pricing_rules": pricing_rules},
        buyer={"buyer_identities": buyer_identities, "briefs": briefs, "api_keys": api_keys},
        registry={"registry_agents": registry_agents, "registry_trust": registry_trust},
        sellers_cfg=sellers, buyers_cfg=buyers_cfg, all_audiences=all_audiences,
    )
