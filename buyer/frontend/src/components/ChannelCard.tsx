import { useEffect, useRef, useState } from "react";
import { fmtCpm, fmtInt, money } from "../api";
import { ChannelState, LEAD, PHASES, PHASE_LABEL } from "../state";

function Lvl({ level }: { level: string }) {
  return <span className={`lvl ${level.toLowerCase()}`}>{level}</span>;
}

const pretty = (x: unknown): string =>
  typeof x === "string" ? x : JSON.stringify(x, null, 2);

// One channel's live crew: L2 lead + L3 workers, phase track, MCP trace feed,
// recommendation and the big-type decision tile.
export default function ChannelCard({ c }: { c: ChannelState }) {
  const lead = LEAD[c.channel] || "Channel Specialist";
  const feedRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState<Record<number, boolean>>({});

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [c.steps]);

  const d = c.decision;
  const gateNote =
    c.priced?.finalCpm != null && c.priced?.maxCpm != null
      ? c.priced.finalCpm <= c.priced.maxCpm
        ? `≤ max ${fmtCpm(c.priced.maxCpm)} ✓`
        : `> max ${fmtCpm(c.priced.maxCpm)} ✗`
      : "";

  return (
    <div className="ch-card">
      <div className="ch-head">
        <Lvl level="L2" />
        <span className="ch-name">{c.channel}</span>
        <span className="ch-status">
          {c.spinning && <span className="spinner" />}
          {c.statusLabel}
        </span>
      </div>

      <div className="ch-facts">
        <div className="fact">
          <span className="fk">Seller</span>
          <span className="fv">{c.seller || "—"}</span>
        </div>
        <div className="fact">
          <span className="fk">Budget</span>
          <span className="fv">{money(c.budget)}</span>
        </div>
      </div>

      <div className="crew">
        <div className={`agent ${c.activeAgent === "lead" ? "active" : ""}`}>
          <Lvl level="L2" />
          <span className="an">{lead}</span>
          <span className="role">directs the crew</span>
          <span className="pulse" />
        </div>
        <div className="crew-workers">
          <div className={`agent ${c.activeAgent === "research" ? "active" : ""}`}>
            <Lvl level="L3" />
            <span className="an">Research Analyst</span>
            <span className="role">calls seller MCP</span>
            <span className="pulse" />
          </div>
          <div className={`agent ${c.activeAgent === "execution" ? "active" : ""}`}>
            <Lvl level="L3" />
            <span className="an">Execution Specialist</span>
            <span className="role">assembles the buy</span>
            <span className="pulse" />
          </div>
        </div>
      </div>

      <div className="phases">
        {PHASES.map((p) => (
          <span key={p} className={`ph ${c.phases[p] || ""}`}>
            {PHASE_LABEL[p]}
          </span>
        ))}
      </div>

      {c.capMessage && <div className="cap-warning">⚠ {c.capMessage}</div>}

      <div className="feed" ref={feedRef}>
        {c.steps.length === 0 && <div className="empty-note small">waiting for the crew…</div>}
        {c.steps.map((s) => {
          const inspectable = s.data != null || s.retData != null;
          const isOpen = inspectable && !!open[s.id];
          return (
            <div
              key={s.id}
              className={`step${inspectable ? " expandable" : ""}${isOpen ? " open" : ""}`}
              onClick={inspectable ? () => setOpen((o) => ({ ...o, [s.id]: !o[s.id] })) : undefined}
              title={inspectable ? "Click to inspect the full input/output" : undefined}
            >
              <Lvl level={s.level} />
              <span className="ic">{s.kind === "tool" ? "🔧" : s.kind === "finish" ? "🧠" : "↩︎"}</span>
              <span className="tx">
                {s.kind === "tool" && (
                  <span className="callline">
                    <span className="toolchip">{s.tool || "tool"}</span>
                    <span className="call">MCP call</span>
                    {inspectable && <span className="inspect">{isOpen ? "▾" : "▸"} {"{…}"}</span>}
                  </span>
                )}
                {s.kind === "finish" && (
                  <span className="callline">
                    <b>drafting recommendation</b>
                    {inspectable && <span className="inspect">{isOpen ? "▾" : "▸"} {"{…}"}</span>}
                  </span>
                )}
                {s.kind !== "return" && (
                  <span className="rn">
                    {s.agent || (s.kind === "tool" ? "Research Analyst" : "Specialist")}
                    {s.detail ? ` — ${s.detail}` : ""}
                  </span>
                )}
                {s.kind === "tool" && s.retPending && (
                  <span className="retline pending">
                    <span className="arrow">←</span> awaiting seller…
                  </span>
                )}
                {s.ret != null && (
                  <span className="retline">
                    <span className="arrow">←</span> {s.ret}
                  </span>
                )}
                {isOpen && (
                  <span className="json-panels" onClick={(e) => e.stopPropagation()}>
                    {s.data != null && (
                      <span className="json-block">
                        <span className="json-label">→ input</span>
                        <pre className="json-view input">{pretty(s.data)}</pre>
                      </span>
                    )}
                    {s.retData != null && (
                      <span className="json-block">
                        <span className="json-label">← output</span>
                        <pre className="json-view output">{pretty(s.retData)}</pre>
                      </span>
                    )}
                  </span>
                )}
              </span>
            </div>
          );
        })}
      </div>

      {c.rec !== undefined && (
        <div className="rec">
          {c.rec === null ? (
            <span className="hint">no matching product found</span>
          ) : (
            <>
              Recommended <b>{c.rec.productName || c.rec.productId}</b>
              {c.rec.finalCpm != null && <> · crew CPM {fmtCpm(c.rec.finalCpm)}</>}
              {(c.rec.audienceCodes ?? []).map((x) => (
                <span key={x} className="chip code">
                  {x}
                </span>
              ))}
              {c.priced?.finalCpm != null && (
                <div className="rec-price">
                  Seller's authoritative price: <b>{fmtCpm(c.priced.finalCpm)}</b>{" "}
                  <span className="hint">{gateNote}</span>
                </div>
              )}
              {c.priced?.card?.base_price != null && (
                <div className="ratecard">
                  <span className="rc-tag">shared rate card</span>
                  <span className="rc-math">
                    {fmtCpm(c.priced.card.base_price)} card
                    {(c.priced.card.tier_discount ?? 0) > 0 && (
                      <> − {Math.round((c.priced.card.tier_discount ?? 0) * 100)}% tier</>
                    )}
                    {(c.priced.card.volume_discount ?? 0) > 0 && (
                      <> − {Math.round((c.priced.card.volume_discount ?? 0) * 100)}% volume</>
                    )}
                    {" → "}
                    <b>{fmtCpm(c.priced.card.final_price ?? c.priced.finalCpm)}</b>
                  </span>
                  {c.priced.card.rationale && (
                    <span className="rc-why">{c.priced.card.rationale}</span>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {d && (
        <div className={`decision ${d.decision}`}>
          <div className="d-tag">{d.decision}</div>
          <div className="d-nums">
            {d.finalCpm != null && (
              <div className="d-num">
                <span className="n">{fmtCpm(d.finalCpm)}</span>
                <span className="l">CPM</span>
              </div>
            )}
            {d.impressions != null && (
              <div className="d-num">
                <span className="n">{fmtInt(d.impressions)}</span>
                <span className="l">impressions</span>
              </div>
            )}
          </div>
          {(d.orderId || d.dealId) && (
            <div className="d-artifact">
              {d.orderId && <>order {d.orderId}</>}
              {d.dealId && (
                <>
                  PG deal {d.dealId}
                  {d.ssp ? ` → SSP ${d.ssp}` : ""}
                </>
              )}
              {d.finalCpm != null && <> · booked at {fmtCpm(d.finalCpm)}</>}
            </div>
          )}
          {d.reason && <div className="d-reason">{d.reason}</div>}
        </div>
      )}
    </div>
  );
}
