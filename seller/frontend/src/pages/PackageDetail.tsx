import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Audience, collectAudienceIds, collectProducts, money, PackageView, Product } from "../api";
import { useApp } from "../App";

type PricedProduct = Product & { price?: number };

export default function PackageDetail() {
  const { packageId } = useParams();
  const { identity } = useApp();
  const authed = identity.key !== "public";
  const [pkg, setPkg] = useState<PackageView | null>(null);
  const [products, setProducts] = useState<PricedProduct[]>([]);
  const [auds, setAuds] = useState<Audience[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    setPkg(null); setProducts([]); setAuds([]); setErr("");
    (async () => {
      try {
        const detail = await api.getPackage(packageId!, identity);
        setPkg(detail);
        if (identity.key !== "public") {
          const prods = collectProducts(detail);
          setProducts(prods);
          // price each product at the current buyer's tier (parallel)
          const priced = await Promise.all(
            prods.map(async (p) => ({ ...p, price: (await api.getPricing(p.product_id, identity)).final_price }))
          );
          setProducts(priced);
          const ids = new Set(collectAudienceIds(detail));
          if (ids.size) {
            const all = await api.listAudiences();
            setAuds(all.filter((a) => ids.has(String(a.iab_audience_taxonomy_id))));
          }
        }
      } catch (e) { setErr(String(e)); }
    })();
  }, [packageId, identity.key]);

  if (err) return <div className="page"><div className="loading">Couldn’t load package: {err}</div></div>;
  if (!pkg) return <div className="page"><div className="loading">Loading package…</div></div>;

  const caps = pkg.audience_capabilities ?? {};
  return (
    <div className="page">
      <div className="breadcrumbs">
        <Link to="/media-kit">Media Kit</Link><span className="sep">/</span>
        <span className="current">{pkg.name}</span>
      </div>

      <div className="focus-header">
        <div>
          <div className="focus-chips">
            {pkg.inventory_type && <span className="chip channel">{pkg.inventory_type}</span>}
            {pkg.is_featured && <span className="chip type agentic">featured</span>}
          </div>
          <h1>{pkg.name}</h1>
          <p className="page-sub">{pkg.description ?? pkg.tagline ?? "Premium AAMP inventory package."}</p>
        </div>
        <div className="focus-price">
          {pkg.exact_price != null ? (
            <><div className="fp-big">{money(pkg.exact_price)}</div><div className="fp-label">CPM · {identity.label}</div></>
          ) : (
            <><div className="fp-big" style={{ fontSize: 24 }}>{pkg.price_range ?? "—"}</div><div className="fp-label">range · anonymous</div></>
          )}
        </div>
      </div>

      {!authed ? (
        <div className="panel">
          <div className="narration" style={{ marginTop: 0 }}>
            You’re browsing anonymously — packages show a <strong>price range</strong> and capability summary only.
            Switch the buyer (top-right) to <strong>Agency</strong> or <strong>Advertiser</strong> to see the products
            inside this kit and exact, deterministic pricing.
          </div>
          <h3 style={{ marginTop: 18 }}>Audience capabilities</h3>
          <div className="tier-spread" style={{ maxWidth: 460 }}>
            <span className="tlabel">Standard (taxonomy {caps.standard_taxonomy_version ?? "1.1"})</span><span /><span className="tcpm">{caps.supports_standard ? "✓" : "—"}</span>
            <span className="tlabel">Contextual (taxonomy {caps.contextual_taxonomy_version ?? "3.1"})</span><span /><span className="tcpm">{caps.supports_contextual ? "✓" : "—"}</span>
            <span className="tlabel">Agentic</span><span /><span className="tcpm">{caps.supports_agentic ? "✓" : "—"}</span>
          </div>
        </div>
      ) : (
        <>
          <h3 style={{ margin: "8px 0 14px" }}>Products in this kit <span className="page-sub" style={{ fontSize: 13 }}>· your tier’s CPM shown</span></h3>
          <div className="pkg-grid">
            {products.map((p) => (
              <Link className="pkg-card" key={p.product_id} to={`/media-kit/${pkg.package_id}/product/${p.product_id}`}>
                <div className="pkg-card-top"><h3>{p.name ?? p.product_id}</h3>{p.ad_formats?.[0] && <span className="chip channel">{p.ad_formats[0]}</span>}</div>
                <p className="pkg-tagline">{p.product_id}</p>
                <div className="pkg-price">
                  {p.price != null ? <><span className="blended">{money(p.price)}</span><span className="blended-label">CPM · your rate</span></> : <span className="blended-label">view pricing →</span>}
                </div>
              </Link>
            ))}
          </div>

          <h3 style={{ margin: "26px 0 14px" }}>Audiences <span className="page-sub" style={{ fontSize: 13 }}>· segments this kit can target</span></h3>
          {auds.length === 0 ? (
            <div className="loading" style={{ padding: 24 }}>No resolvable audience segments.</div>
          ) : (
            <div className="pkg-grid">
              {auds.map((a) => (
                <div className="focus-card" key={a.id}>
                  <div className="focus-chips"><span className="chip type standard">standard</span></div>
                  <h3>{a.name}</h3>
                  <div className="fc-meta big">{a.size != null ? (a.size / 1e6).toFixed(1) + "M" : "—"}</div>
                  <div className="fc-taxonomy">reach · IAB {a.iab_audience_taxonomy_id}</div>
                  {a.description && <div className="fc-provider">{a.description}</div>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
