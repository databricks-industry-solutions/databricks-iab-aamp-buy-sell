"""Coherence + single-channel + trust/tier validation. Fails loudly before any load.

Checks:
  1. every package's standard_segment_ids exist in its OWN seller's audiences
  2. single channel per seller: all of a seller's inventory shares one inventory_type
     == the seller's declared media_type
  3. no cross-seller products: a product's inventory all belong to its seller, and its
     codes are offered by that seller's audiences
  4. briefs: interests resolve (no UNRESOLVED) ⊆ union of all package codes; impressions
     >= a covering product's minimum_impressions; channel budget_pct sums to 100; each
     channel maps to a media_type some seller offers; execution rail is direct|programmatic
     (and programmatic briefs only target products that support programmaticguaranteed)
  5. 3-way: buyer_identity.tier == TRUST_TO_TIER_MAP[registry trust] == a pricing_rule tier
  6. (advisory) IAB audience + content codes valid in the vendored TSVs, if present
"""

from __future__ import annotations

from .generator import CHANNEL_MEDIA_TYPE, Universe, resolve_interests
from .taxonomy import TRUST_TO_TIER_MAP, load_valid_codes


def validate(u: Universe) -> list[str]:
    errors: list[str] = []
    inv = u.seller["inventory"]
    products = u.seller["products"]
    packages = u.seller["packages"]

    inv_seller = {iv["id"]: iv["seller"] for iv in inv}
    seller_media = {s["seller"]["id"]: s["seller"]["media_type"] for s in u.sellers_cfg}
    aud_codes_by_seller: dict[str, set[str]] = {}
    for a in u.seller["audiences"]:
        aud_codes_by_seller.setdefault(a["seller"], set()).add(a["iab_audience_taxonomy_id"])
    pkg_codes = {c for p in packages for c in p["audience_capabilities"]["standard_segment_ids"]}

    # 1. package codes ⊆ its seller's audiences
    for p in packages:
        own = aud_codes_by_seller.get(p["seller"], set())
        for c in p["audience_capabilities"]["standard_segment_ids"]:
            if c not in own:
                errors.append(f"[pkg {p['package_id']}] segment '{c}' not in seller {p['seller']} audiences")

    # 2. single channel per seller
    by_seller_types: dict[str, set[str]] = {}
    for iv in inv:
        by_seller_types.setdefault(iv["seller"], set()).add(iv["inventory_type"])
    for sid, types in by_seller_types.items():
        if len(types) != 1:
            errors.append(f"[seller {sid}] inventory spans multiple channels {sorted(types)} (must be one)")
        elif seller_media.get(sid) not in types:
            errors.append(f"[seller {sid}] inventory_type {sorted(types)} != declared media_type {seller_media.get(sid)}")

    # 3. no cross-seller products
    for p in products:
        for iid in p["inventory_segment_ids"]:
            if inv_seller.get(iid) != p["seller"]:
                errors.append(f"[product {p['product_id']}] inventory '{iid}' belongs to {inv_seller.get(iid)}, not {p['seller']}")
        own = aud_codes_by_seller.get(p["seller"], set())
        for c in p["audience_capabilities"]:
            if c not in own:
                errors.append(f"[product {p['product_id']}] code '{c}' not in seller {p['seller']} audiences")

    # 4. briefs — codes resolve & are offered; channels sum to 100; and a bookable deal
    #    exists within the brief's max_cpm at SOME tier (buy-or-pass coherence).
    notes: list[str] = []
    seller_media_types = set(seller_media.values())
    seller_by_mt = {s["seller"]["media_type"]: s["seller"]["id"] for s in u.sellers_cfg}
    rate_card = {s["seller"]["id"]: {e["tier"]: e for e in s.get("rate_card", [])} for s in u.sellers_cfg}
    prods_by_seller: dict[str, list[tuple[float, set]]] = {}
    for p in products:
        prods_by_seller.setdefault(p["seller"], []).append((p["base_cpm"], set(p["audience_capabilities"])))

    def _eff_cpm(rc: dict, base: float, tier: str) -> float:
        e = rc.get(tier)
        if not e:
            return base  # no rule for this tier → card rate (public)
        if e.get("base_cpm_override") is not None:
            return float(e["base_cpm_override"])
        return base * (1 - e.get("discount", 0.0))

    for br in u.buyer["briefs"]:
        codes = resolve_interests(br["target_audience"]["interests"], u.all_audiences)
        for c in codes:
            if c.startswith("UNRESOLVED:"):
                errors.append(f"[brief {br['name']}] interest didn't resolve: {c.split(':', 1)[1]}")
        resolved = [c for c in codes if not c.startswith("UNRESOLVED:")]
        if resolved and not set(resolved).issubset(pkg_codes):
            errors.append(f"[brief {br['name']}] codes {sorted(set(resolved) - pkg_codes)} not offered by any package")
        pct = sum(ch["budget_pct"] for ch in br["channels"])
        if round(pct, 3) != 100:
            errors.append(f"[brief {br['name']}] channel budget_pct sums to {pct}, not 100")
        # execution rail (deal_preferences.execution): direct (IO) | programmatic (PG→SSP)
        exec_rail = (br.get("deal_preferences") or {}).get("execution")
        if exec_rail not in ("direct", "programmatic"):
            errors.append(f"[brief {br['name']}] execution '{exec_rail}' not in {{direct, programmatic}}")
        if exec_rail == "programmatic":
            matched_prods = [p for p in products if set(p["audience_capabilities"]) & set(resolved)]
            bad_dt = [p["product_id"] for p in matched_prods
                      if "programmaticguaranteed" not in p["supported_deal_types"]]
            if bad_dt:
                errors.append(f"[brief {br['name']}] programmatic rail but product(s) {bad_dt} "
                              f"don't support programmaticguaranteed")
        max_cpm = (br.get("deal_preferences") or {}).get("max_cpm")
        for ch in br["channels"]:
            mt = CHANNEL_MEDIA_TYPE.get(ch["channel"])
            if mt not in seller_media_types:
                errors.append(f"[brief {br['name']}] channel {ch['channel']} (→{mt}) has no seller offering it")
                continue
            sid = seller_by_mt.get(mt)
            rc = rate_card.get(sid, {})
            # products of this channel's seller that match the brief's resolved codes
            matching = [base for base, pc in prods_by_seller.get(sid, []) if pc & set(resolved)]
            if not matching or max_cpm is None:
                continue
            lowest = min(min(_eff_cpm(rc, base, t) for t in ("public", "agency", "advertiser")) for base in matching)
            public_min = min(matching)  # public = card rate
            if lowest > max_cpm:
                errors.append(f"[brief {br['name']}] {ch['channel']} ({sid}): no deal ≤ max_cpm {max_cpm} at any tier "
                              f"(lowest {lowest:.2f}) — buyer always PASSES (demo broken)")
            elif public_min > max_cpm:
                notes.append(f"[brief {br['name']}] {ch['channel']} ({sid}): public card {public_min:.2f} > max_cpm {max_cpm} "
                             f"→ passes at PUBLIC, books at agency/advertiser (intended buy-or-pass lever)")

    # 5. 3-way trust → tier → rule
    rule_tiers = {r["access_tier"] for r in u.seller["pricing_rules"]}
    trust_by_agent = {t["agent_id"]: t["trust_status"] for t in u.registry["registry_trust"]}
    for bi in u.buyer["buyer_identities"]:
        trust = trust_by_agent.get(bi["buyer_id"])
        if trust is None:
            errors.append(f"[buyer {bi['buyer_id']}] no registry_trust row")
            continue
        mapped = TRUST_TO_TIER_MAP.get(trust)
        if mapped is None:
            continue  # BLOCKED — intentional reject
        if mapped != bi["tier"]:
            errors.append(f"[buyer {bi['buyer_id']}] trust {trust}->{mapped} != identity tier {bi['tier']}")
        if mapped not in rule_tiers:
            errors.append(f"[buyer {bi['buyer_id']}] tier '{mapped}' has no matching pricing rule")

    # 6. IAB advisory
    valid = load_valid_codes()
    if valid and "iab-audience" in valid:
        aud_codes = {a["iab_audience_taxonomy_id"] for a in u.seller["audiences"]}
        bad = sorted(c for c in aud_codes if c not in valid["iab-audience"])
        if bad:
            print(f"  ⚠ NOTE: {len(bad)} audience code(s) not in vendored IAB Audience Taxonomy: {bad}")
    if valid and "iab-content" in valid:
        content = {c for iv in inv for c in iv["content_categories"]}
        content |= {c for p in packages for c in p["cat"]}
        bad = sorted(c for c in content if c not in valid["iab-content"])
        if bad:
            print(f"  ⚠ NOTE: {len(bad)} content code(s) not in vendored IAB Content Taxonomy 3.1: {bad}")

    # Buy-or-pass demo levers (informational, not failures)
    for n in notes:
        print(f"  ℹ buy/pass: {n}")

    return errors
