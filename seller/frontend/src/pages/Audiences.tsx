import { useEffect, useState } from "react";
import { api, Audience } from "../api";
import { useApp } from "../App";

export default function Audiences() {
  const { seller } = useApp();
  const [auds, setAuds] = useState<Audience[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.listAudiences().then(setAuds).catch((e) => setErr(String(e)));
  }, []);

  if (err) return <div className="page"><div className="loading">Couldn’t load audiences: {err}</div></div>;
  if (!auds) return <div className="page"><div className="loading">Loading audiences…</div></div>;

  const totalReach = auds.reduce((s, a) => s + (a.size ?? 0), 0);
  return (
    <div className="page">
      <div className="page-head">
        <h1>Audiences</h1>
        <p className="page-sub">
          The audience segments {seller || "this seller"} can target — IAB Audience Taxonomy 1.1 segments carried by
          its inventory. {auds.length} segments · {(totalReach / 1e6).toFixed(1)}M total reach.
        </p>
      </div>

      {auds.length === 0 ? (
        <div className="loading">No audiences seeded.</div>
      ) : (
        <div className="pkg-grid">
          {auds.map((a) => (
            <div className="focus-card" key={a.id}>
              <div className="focus-chips"><span className="chip type standard">standard</span></div>
              <h3>{a.name}</h3>
              <div className="fc-meta big">{a.size != null ? (a.size / 1e6).toFixed(1) + "M" : "—"}</div>
              <div className="fc-taxonomy">est. reach · IAB taxonomy {a.iab_audience_taxonomy_id}</div>
              {a.description && <div className="fc-provider">{a.description}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
