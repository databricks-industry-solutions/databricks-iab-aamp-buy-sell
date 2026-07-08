import { Brief, RAIL_LABEL, Status, fmtCpm, money, railOf } from "../api";

// Full-width brief gallery + "Present as" tier selector. Collapses away once a run
// starts (RunBoard shows the compact strip instead).
export default function BriefPicker(props: {
  briefs: Brief[];
  status: Status | null;
  selected: string | null;
  onSelect: (id: string) => void;
  tier: string;
  onTier: (t: string) => void;
  onRun: () => void;
  running: boolean;
  loadError?: string;
}) {
  const { briefs, status, selected, onSelect, tier, onTier, onRun, running, loadError } = props;
  const tiers = status?.tiers ?? ["public", "seat", "agency", "advertiser"];

  return (
    <div className="picker">
      <div className="page-head">
        <h1>Campaign Console</h1>
        <p className="page-sub">
          Pick a campaign brief and the identity tier to present. The hierarchical crew plans
          audiences, allocates budget, then books or passes each channel against live seller
          prices — at the tier you present.
        </p>
      </div>

      <div className="run-config panel">
        <div className="tier-select">
          <span className="field-label">Present as</span>
          <div className="tier-pills">
            {tiers.map((t) => (
              <button
                key={t}
                className={`tier-pill ${t} ${t === tier ? "active" : ""}`}
                onClick={() => onTier(t)}
              >
                {t}
              </button>
            ))}
          </div>
          {status && (
            <span className="tier-default">registry-trust default: {status.tier}</span>
          )}
        </div>
        {tier === "public" && (
          <div className="tier-warning">
            ⚠ Public tier reveals no identity — sellers hide trust-gated products and quote
            rate-card prices. Higher tiers (seat → agency → advertiser) unlock more inventory
            and better pricing.
          </div>
        )}
        <button className="btn primary run-btn" disabled={!selected || running} onClick={onRun}>
          {running ? "Starting…" : "Run campaign"}
        </button>
      </div>

      {loadError ? (
        <div className="empty-note">Failed to load briefs: {loadError}</div>
      ) : briefs.length === 0 ? (
        <div className="empty-note">Loading briefs…</div>
      ) : (
        <div className="brief-grid">
          {briefs.map((b) => {
            const rail = railOf(b);
            const maxCpm = b.deal_preferences?.max_cpm;
            const interests = b.target_audience?.interests ?? [];
            return (
              <div
                key={b.brief_id}
                className={`brief-card ${selected === b.brief_id ? "sel" : ""}`}
                onClick={() => onSelect(b.brief_id)}
              >
                <div className="brief-top">
                  <span className={`rail-badge ${rail}`}>{RAIL_LABEL[rail]}</span>
                  <span className="brief-id">{b.brief_id}</span>
                </div>
                <h3>{b.name || b.brief_id}</h3>
                <div className="brief-facts">
                  <div className="fact">
                    <span className="fk">Budget</span>
                    <span className="fv">
                      {money(b.total_budget)} {b.currency || "USD"}
                    </span>
                  </div>
                  <div className="fact">
                    <span className="fk">Flight</span>
                    <span className="fv">
                      {b.start_date && b.end_date ? `${b.start_date} → ${b.end_date}` : "—"}
                    </span>
                  </div>
                  <div className="fact">
                    <span className="fk">Max CPM</span>
                    <span className="fv">{maxCpm != null ? fmtCpm(maxCpm) : "—"}</span>
                  </div>
                </div>
                <div className="brief-channels">
                  {(b.channels ?? []).map((c) => (
                    <span key={c.channel} className="chip channel">
                      {c.channel} {c.budget_pct}%
                    </span>
                  ))}
                </div>
                {interests.length > 0 && (
                  <div className="brief-interests">
                    🎯 {interests.join(", ")}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
