import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, collectProducts, IDENTITIES, money, pct, TierPrice } from "../api";
import { useApp } from "../App";

const TIER_LABEL: Record<string, string> = { public: "Public / rack", agency: "Agency", advertiser: "Advertiser" };

export default function ProductDetail() {
  const { packageId, productId } = useParams();
  const { identity } = useApp();
  const [rows, setRows] = useState<TierPrice[]>([]);
  const [name, setName] = useState(productId ?? "");
  const [invType, setInvType] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    setErr(""); setRows([]);
    const adv = IDENTITIES.find((i) => i.key === "advertiser")!; // full params → all 3 tiers computed
    (async () => {
      try {
        setRows(await api.productPricing(productId!, adv));
        try {
          const detail = await api.getPackage(packageId!, adv);
          const p = collectProducts(detail).find((x) => x.product_id === productId);
          if (p?.name) setName(p.name);
          if (detail.inventory_type) setInvType(String(detail.inventory_type));
        } catch { /* name is best-effort */ }
      } catch (e) { setErr(String(e)); }
    })();
  }, [packageId, productId]);

  if (err) return <div className="page"><div className="loading">Couldn’t load pricing: {err}</div></div>;

  return (
    <div className="page deal-detail">
      <div className="breadcrumbs">
        <Link to="/media-kit">Media Kit</Link><span className="sep">/</span>
        <Link to={`/media-kit/${packageId}`}>{packageId}</Link><span className="sep">/</span>
        <span className="current">{name}</span>
      </div>

      <div className="focus-header">
        <div>
          <div className="focus-chips">{invType && <span className="chip channel">{invType}</span>}</div>
          <h1>{name}</h1>
          <p className="page-sub">
            Deterministic, no-negotiation pricing straight from the SDK engine — the rate on this card is exactly
            what each tier is quoted. Volume breaks ride on top of the tier rate.
          </p>
        </div>
        <Link className="btn primary" to="/dashboard" style={{ alignSelf: "center", textDecoration: "none" }}>
          View bookings
        </Link>
      </div>

      {rows.length === 0 ? (
        <div className="loading">Pricing the tiers…</div>
      ) : (
        <div className="pkg-grid">
          {rows.map((r) => (
            <div className={`panel${r.tier === identity.key ? " " : ""}`} key={r.tier}
                 style={r.tier === identity.key ? { borderColor: "var(--brick)", boxShadow: "0 0 0 3px rgba(255,54,33,0.10)" } : undefined}>
              <div className="panel-top" style={{ padding: "0 0 12px" }}>
                <span className={`badge ${r.tier}`}>{TIER_LABEL[r.tier] ?? r.tier}</span>
                {r.tier === identity.key && <span className="status-pill pass" style={{ marginLeft: "auto" }}>current</span>}
              </div>
              <div className="pkg-price" style={{ marginBottom: 10 }} title="Final CPM this tier is quoted — rack, minus the tier discount, minus any volume break. Deterministic; equals the quote.">
                <span className="blended">{money(r.final_price)}</span><span className="blended-label">CPM</span>
              </div>
              <div className="kv2" title="Rack rate — the product's published base CPM before any tier or volume adjustment (the rate-card headline).">
                <span className="k">rack / base ⓘ</span><span className="v">{money(r.base_price ?? 0)}</span></div>
              <div className="kv2" title="Tier discount — the pre-agreed reduction for this identity tier (seat 5% / agency 10% / advertiser 15%). The codified result of a prior negotiation, not live haggling.">
                <span className="k">tier discount ⓘ</span><span className="v">−{pct(r.tier_discount)}</span></div>
              <div className="kv2" title="Volume discount — additional break for larger impression commitments (SDK volume schedule), applied on top of the tier rate.">
                <span className="k">volume discount ⓘ</span><span className="v">−{pct(r.volume_discount)}</span></div>
              <div className="kv2" style={{ borderTop: "1px solid var(--line)", marginTop: 6, paddingTop: 8 }}
                   title={`${money(r.base_price ?? 0)} rack − ${pct(r.tier_discount)} tier − ${pct(r.volume_discount)} volume = ${money(r.final_price)}`}>
                <span className="k" style={{ fontWeight: 700, color: "var(--ink)" }}>final CPM</span><span className="v" style={{ color: "var(--brick)" }}>{money(r.final_price)}</span>
              </div>
              {r.rationale && <div className="fc-taxonomy" style={{ marginTop: 12, lineHeight: 1.5 }}>{r.rationale}</div>}
              {r.applied_rules && r.applied_rules.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {r.applied_rules.map((rule, i) => <span className="badge-soft" key={i}>{rule}</span>)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
