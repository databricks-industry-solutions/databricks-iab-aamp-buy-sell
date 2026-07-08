import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, money, PackageView } from "../api";
import { useApp } from "../App";

export default function MediaKit() {
  const { identity } = useApp();
  const [pkgs, setPkgs] = useState<PackageView[] | null>(null);
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    setPkgs(null);
    setErr("");
    api.listPackages(identity).then(setPkgs).catch((e) => setErr(String(e)));
  }, [identity.key]);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Media Kit</h1>
        <p className="page-sub">
          The published catalog of packages. Prices are deterministic and tier-aware — the
          price shown here is exactly what <strong>{identity.label}</strong> would be quoted (no
          negotiation). Switch the buyer in the top bar to see the same inventory reprice.
        </p>
      </div>

      {err && <div className="loading">Couldn’t load packages: {err}</div>}
      {!pkgs && !err && <div className="loading">Loading packages…</div>}
      {pkgs && pkgs.length === 0 && <div className="loading">No packages seeded.</div>}

      {pkgs && pkgs.length > 0 && (
        <>
          <div className="ratecard-head">
            <h2>Rate Card</h2>
            <span>
              rack CPM → your tier rate · deterministic, no negotiation.
              {identity.key === "public"
                ? " Anonymous sees the public range — identify the buyer to see exact rates."
                : ` Showing ${identity.label} rates. Open a package for the per-product breakdown.`}
            </span>
          </div>
          <div className="pkg-grid">
            {pkgs.map((p) => {
              const productCount = Array.isArray(p.placements) ? p.placements.length : null;
              return (
                <Link className="pkg-card" key={p.package_id} to={`/media-kit/${p.package_id}`}>
                  <div className="pkg-card-top">
                    <h3>{p.name}</h3>
                    {p.inventory_type && <span className="chip channel">{p.inventory_type}</span>}
                  </div>
                  <p className="pkg-tagline">{p.tagline ?? p.description ?? "Premium AAMP inventory package."}</p>
                  {p.exact_price != null ? (
                    <div className="pkg-price" title={`Your ${identity.label} rate — the package rack CPM adjusted by your identity tier. Deterministic; equals the quote you'd be issued (no negotiation).`}>
                      <span className="blended">{money(p.exact_price)}</span>
                      <span className="blended-label">CPM · your rate ⓘ</span>
                    </div>
                  ) : (
                    <div className="pkg-price" title="Public rate card shows a ±20% range around the rack CPM. Identify the buyer (top-right) to see the exact tier rate.">
                      <span className="blended" style={{ fontSize: 20 }}>{p.price_range ?? "—"}</span>
                      <span className="blended-label">public range ⓘ</span>
                    </div>
                  )}
                  <div className="pkg-foot">
                    <span>{productCount != null ? `${productCount} product${productCount === 1 ? "" : "s"}` : p.package_id}</span>
                    <span style={{ color: "var(--brick-dim)", fontWeight: 600 }}>open →</span>
                  </div>
                </Link>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
