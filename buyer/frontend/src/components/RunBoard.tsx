import { RAIL_LABEL, Rail, fmtCpm, money } from "../api";
import { RunView, STAGES } from "../state";
import ChannelCard from "./ChannelCard";
import RunSummary from "./RunSummary";

// The live-run screen: compact brief strip, stage progression, persistent summary bar
// (identity / audience / allocation), side-by-side channel crews. When the run
// completes, the summary appends BELOW the channels — nothing swaps or jumps.
export default function RunBoard(props: {
  view: RunView;
  tier: string;
  onNewRun: () => void;
  onRerun: () => void;
  onOpenBookings?: () => void;
}) {
  const { view, tier, onNewRun, onRerun, onOpenBookings } = props;
  const rail = (view.rail?.rail as Rail) || null;
  const runOver = view.phase === "done" || view.phase === "error";

  return (
    <div className="runboard">
      {/* collapsed brief picker: name · tier · rail · re-run */}
      <div className="run-strip">
        <div className="rs-brief">
          <span className="rs-kicker">Campaign</span>
          <span className="rs-name">{view.briefName || view.briefId || "…"}</span>
        </div>
        <div className="rs-facts">
          <span className="rs-fact">
            {money(view.totalBudget)} {view.currency || ""}
          </span>
          <span className="rs-fact">max CPM {view.maxCpm != null ? fmtCpm(view.maxCpm) : "—"}</span>
          <span className={`badge tier ${view.tier || tier}`}>{view.tier || tier}</span>
          {rail && <span className={`rail-badge ${rail}`}>{RAIL_LABEL[rail]}</span>}
        </div>
        <div className="rs-actions">
          <button className="btn" onClick={onNewRun} disabled={!runOver}>
            New run
          </button>
          <button className="btn primary" onClick={onRerun} disabled={!runOver}>
            Re-run
          </button>
        </div>
      </div>

      {/* stage progression */}
      <div className="stage-rail">
        {STAGES.map(([key, label]) => {
          const st = view.stages[key] || "";
          return (
            <div key={key} className={`stage ${st}`}>
              <span className="s">{st === "done" ? "✓" : ""}</span>
              <span>{label}</span>
            </div>
          );
        })}
        {view.rail && <span className="rail-note">{view.rail.message}</span>}
        <span className="legend" style={{ marginLeft: "auto" }}>
          <span className="lvl l1">L1</span> Portfolio
          <span className="lvl l2">L2</span> Specialist
          <span className="lvl l3">L3</span> Research · Execution
        </span>
      </div>

      {/* run summary / error band */}
      {view.phase === "error" && (
        <div className="summary err">
          <h2>Run failed</h2>
          <div className="d-reason">{view.error}</div>
        </div>
      )}

      {/* persistent setup summary bar — no dead cards */}
      <div className="setup-bar">
        <div className="setup-cell">
          <div className="setup-label"><span className="lvl l1">L1</span> Presenting identity</div>
          {view.tier ? (
            <div className="pillrow">
              <span className={`badge tier ${view.tier}`}>{view.tier}</span>
              {(view.identityFields ?? []).length > 0 ? (
                view.identityFields!.map((f) => (
                  <span key={f} className="chip id-field">
                    {f}
                  </span>
                ))
              ) : (
                <span className="hint">anonymous (public) — no identity revealed</span>
              )}
            </div>
          ) : (
            <div className="hint">
              <span className="spinner" /> resolving tier…
            </div>
          )}
        </div>

        <div className="setup-cell">
          <div className="setup-label"><span className="lvl l3">L3</span> Audience plan · validated IAB codes</div>
          {view.segments == null ? (
            <div className="hint">
              <span className="spinner" /> resolving IAB codes…
            </div>
          ) : view.segments.length === 0 ? (
            <div className="hint">no interests in brief</div>
          ) : (
            view.segments.map((s) => (
              <div key={s.interest} className="seg">
                <span className="seg-interest">🎯 {s.interest}</span>
                {s.codes.length === 0 ? (
                  <span className="hint">no code</span>
                ) : (
                  s.codes.map((cd) => (
                    <span key={cd.code} className="chip code">
                      <b>{cd.code}</b>
                      {cd.name ? ` ${cd.name}` : ""}
                    </span>
                  ))
                )}
              </div>
            ))
          )}
        </div>

        <div className="setup-cell">
          <div className="setup-label"><span className="lvl l1">L1</span> Budget allocation</div>
          {view.allocations == null ? (
            <div className="hint">
              <span className="spinner" /> allocating…
            </div>
          ) : (
            <>
              {view.allocations.map((a) => (
                <div key={a.channel} className="alloc">
                  <div className="alloc-line">
                    <span className="ak">
                      {a.channel} · {a.budget_pct}%
                    </span>
                    <span className="av">{money(a.budget)}</span>
                  </div>
                  <div className="bar">
                    <i style={{ width: `${Math.min(100, a.budget_pct)}%` }} />
                  </div>
                  {a.rationale && <div className="hint alloc-why">{a.rationale}</div>}
                </div>
              ))}
              {view.portfolioNote && (
                <div className="portfolio-note">
                  <span className="lvl l1">L1</span> Portfolio Manager — {view.portfolioNote}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* channels side by side */}
      <div className="channels-head">
        <span>Channels — per-channel crews book or pass</span>
      </div>
      <div className="channels" style={{ gridTemplateColumns: `repeat(${Math.max(view.channelOrder.length, 1)}, minmax(0, 1fr))` }}>
        {view.channelOrder.length === 0 && (
          <div className="empty-note">Channels appear once the run starts…</div>
        )}
        {view.channelOrder.map((ch) => (
          <ChannelCard key={ch} c={view.channels[ch]} />
        ))}
      </div>

      {/* the wrap-up appends here when the run finishes — no page swap */}
      {view.phase === "done" && <RunSummary view={view} onOpenBookings={onOpenBookings} />}
    </div>
  );
}
