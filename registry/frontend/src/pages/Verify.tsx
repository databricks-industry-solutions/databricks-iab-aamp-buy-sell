import { useEffect, useRef, useState } from "react";
import { api, RegisteredAgent, TRUST_LADDER, VerifyResult } from "../api";

type Line = { kind: "log" | "step" | "verdict"; text: string };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const DEMO_BUYER = "https://demo-buyer.example";

export default function Verify() {
  const [url, setUrl] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [running, setRunning] = useState(false);
  const [agents, setAgents] = useState<RegisteredAgent[]>([]);
  const termRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.listAgents().then(setAgents).catch(() => {}); }, []);
  useEffect(() => { termRef.current?.scrollTo(0, termRef.current.scrollHeight); }, [lines]);

  const stream = async (steps: Line[]) => {
    for (const s of steps) { setLines((p) => [...p, s]); await sleep(420); }
  };

  const verify = async (target: string) => {
    setRunning(true); setResult(null); setLines([]); setUrl(target);
    try {
      await stream([{ kind: "log", text: `resolving ${target}` }, { kind: "log", text: `→ checking local registry…` }]);
      const r = await api.verify(target);
      setResult(r);
      await stream([
        { kind: "step", text: r.agent_id ? `→ local hit: ${r.agent_id}` : `→ not local — fetched agent card` },
        { kind: "step", text: `→ trust status: ${r.trust_status}` },
        { kind: "step", text: `→ TRUST_TO_TIER_MAP[${r.trust_status}] → ${r.is_blocked ? "BLOCKED (None)" : r.max_access_tier}` },
        r.is_blocked
          ? { kind: "verdict", text: `✗ blocked — buyer is rejected with zero data access` }
          : { kind: "verdict", text: `✓ buyer may reach the ${r.max_access_tier} tier (ceiling)` },
      ]);
    } catch (e) {
      setLines((p) => [...p, { kind: "log", text: `✗ error: ${e}` }]);
    } finally {
      setRunning(false);
    }
  };

  const registerAndVerify = async (trust: string) => {
    setRunning(true); setResult(null); setLines([]); setUrl(DEMO_BUYER);
    try {
      await stream([{ kind: "log", text: `registering demo buyer (${trust}) → ${DEMO_BUYER}` }]);
      await api.register({ name: "Demo Buyer Agent", url: DEMO_BUYER, description: "demo", provider: { name: "Demo DSP" } } as any, "buyer", trust);
      api.listAgents().then(setAgents).catch(() => {});
      await verify(DEMO_BUYER);
      return;
    } catch (e) {
      setLines((p) => [...p, { kind: "log", text: `✗ error: ${e}` }]);
      setRunning(false);
    }
  };

  return (
    <div className="page deal-detail">
      <div className="page-head">
        <h1>Trust Verify</h1>
        <p className="page-sub">
          The call a seller makes before pricing a buyer: resolve an agent URL to its <strong>trust status</strong>,
          then map it through the SDK’s <span className="mono">TRUST_TO_TIER_MAP</span> to the maximum access tier
          that buyer can reach. Blocked agents are rejected outright.
        </p>
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="verify-bar">
          <div className="field">
            <label>Agent URL</label>
            <input value={url} placeholder="https://…/agent" onChange={(e) => setUrl(e.target.value)} />
          </div>
          <button className="btn primary" disabled={running || !url} onClick={() => verify(url)}>{running ? "Resolving…" : "Verify"}</button>
        </div>
        <div className="quickpicks">
          {agents.map((a) => (
            <button key={a.agent_id} onClick={() => verify(a.agent_card.url)}>{a.agent_card?.name ?? a.agent_id}</button>
          ))}
          <button onClick={() => registerAndVerify("approved")}>+ demo buyer (approved)</button>
          <button onClick={() => registerAndVerify("blocked")}>+ demo buyer (blocked)</button>
        </div>
      </div>

      <div className="split" style={{ height: 520 }}>
        <div className="pane left">
          <div className="pane-head">Resolution · governed Lakebase data<span className="mode">{running ? "resolving…" : "client-driven"}</span></div>
          <div className="term" ref={termRef}>
            {lines.length === 0 && !running && (
              <div className="ln idle"><span className="prompt">$</span>pick an agent above or paste a URL, then Verify.</div>
            )}
            {lines.map((l, i) => (
              <div key={i} className={`ln ${l.kind === "step" ? "panel-ln" : ""} ${l.kind === "verdict" ? "verdict-ln" : ""}`}>
                <span className="prompt">{l.kind === "verdict" ? "✓" : l.kind === "step" ? "→" : "$"}</span>{l.text}
              </div>
            ))}
            {running && <div className="ln"><span className="prompt">$</span><span className="cursor" /></div>}
          </div>
        </div>
        <div className="pane right">
          <div className="pane-head">Trust → tier ceiling</div>
          <div className="right-scroll">
            {result && (
              <div className="verdict-hero">
                <div className={`verdict-badge ${result.is_blocked ? "blocked" : "ok"}`}>
                  {result.is_blocked ? "⛔ BLOCKED" : `✓ ${result.max_access_tier?.toUpperCase()}`}
                </div>
                <div className="verdict-sub">trust <strong>{result.trust_status}</strong> → {result.is_blocked ? "rejected" : `max tier ${result.max_access_tier}`}</div>
              </div>
            )}
            <div className="ladder">
              {TRUST_LADDER.map((row) => (
                <div key={row.trust} className={`ladder-row ${result && result.trust_status === row.trust ? "hit" : ""}`}>
                  <span><span className={`badge ${row.trust}`}>{row.trust}</span></span>
                  <span className="arrow">→</span>
                  <span><span className="tier">{row.tier ?? "blocked"}</span> · <span style={{ color: "var(--muted)", fontSize: 12.5 }}>{row.note}</span></span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
