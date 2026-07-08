import { useEffect, useState } from "react";
import { api, RegisteredAgent, TRUST_OPTIONS } from "../api";

export default function Directory() {
  const [agents, setAgents] = useState<RegisteredAgent[] | null>(null);
  const [err, setErr] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [trustFilter, setTrustFilter] = useState("all");

  const load = () => {
    setErr("");
    api.listAgents().then(setAgents).catch((e) => setErr(String(e)));
  };
  useEffect(load, []);

  const all = agents ?? [];
  const rows = all.filter(
    (a) => (typeFilter === "all" || a.agent_type === typeFilter) && (trustFilter === "all" || a.trust_status === trustFilter)
  );
  const count = (p: (a: RegisteredAgent) => boolean) => all.filter(p).length;

  const setTrust = async (id: string, trust: string) => {
    await api.updateTrust(id, trust);
    load();
  };
  const remove = async (id: string) => {
    await api.remove(id);
    load();
  };

  if (err) return <div className="page"><div className="loading">Couldn’t load the registry: {err}</div></div>;
  if (!agents) return <div className="page"><div className="loading">Loading registry…</div></div>;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Agent Directory</h1>
        <p className="page-sub">
          Every agent registered with this AAMP registry — sellers advertise their cards here so buyers can
          discover them, and the registry tracks each agent’s <strong>trust status</strong>, which caps the
          access tier they can reach at any seller. Served fresh from Lakebase.
        </p>
      </div>

      <div className="kpis" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <Kpi label="Registered agents" value={String(all.length)} foot="In the local registry" spark="🗂️" />
        <Kpi label="Sellers" value={String(count((a) => a.agent_type === "seller"))} foot="Publishers in the directory" spark="🏢" />
        <Kpi label="Buyers" value={String(count((a) => a.agent_type === "buyer"))} foot="DSPs / agencies / advertisers" spark="🛒" />
        <Kpi label="Blocked" value={String(count((a) => a.trust_status === "blocked"))} foot="Hard-rejected agents" accent />
      </div>

      <div className="table-wrap">
        <div className="table-head">
          <h3>Directory</h3>
          <div className="filters">
            <select className="trust-select" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="all">All types</option>
              <option value="seller">Sellers</option>
              <option value="buyer">Buyers</option>
            </select>
            <select className="trust-select" value={trustFilter} onChange={(e) => setTrustFilter(e.target.value)}>
              <option value="all">All trust</option>
              {TRUST_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        </div>
        <table>
          <thead>
            <tr><th>Agent</th><th>Type</th><th>Channel</th><th>Trust</th><th>URL</th><th className="num">Seen</th><th>Set trust</th><th></th></tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td className="empty-row" colSpan={8}>No agents match. Register sellers via <span className="mono">make register-sellers</span>.</td></tr>
            ) : rows.map((a) => {
              const channels: string[] = a.agent_card?.inventory_types ?? [];
              return (
              <tr key={a.agent_id}>
                <td><strong>{a.agent_card?.name ?? a.agent_id}</strong><div className="mono" style={{ color: "var(--muted)" }}>{a.agent_id}</div></td>
                <td><span className={`chip ${a.agent_type}`}>{a.agent_type}</span></td>
                <td>{channels.length ? channels.map((c) => <span className="chip channel" key={c}>{c}</span>) : <span style={{ color: "var(--muted)" }}>—</span>}</td>
                <td><span className={`badge ${a.trust_status}`}>{a.trust_status}</span></td>
                <td className="mono" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.agent_card?.url}</td>
                <td className="num">{a.interaction_count ?? 0}×</td>
                <td>
                  <select className="trust-select" value={a.trust_status} onChange={(e) => setTrust(a.agent_id, e.target.value)}>
                    {TRUST_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td><div className="row-actions"><button className="link-btn" onClick={() => remove(a.agent_id)}>remove</button></div></td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Kpi({ label, value, foot, spark, accent }: { label: string; value: string; foot: string; spark?: string; accent?: boolean }) {
  return (
    <div className={`kpi${accent ? " accent" : ""}`}>
      {spark && <span className="spark">{spark}</span>}
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="foot">{foot}</div>
    </div>
  );
}
