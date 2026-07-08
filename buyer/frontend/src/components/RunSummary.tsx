import { ReactNode, useState } from "react";
import { RAIL_LABEL, Rail, fmtCpm, fmtInt, money } from "../api";
import { RunView } from "../state";

const DSP_NAME: Record<string, string> = { ttd: "The Trade Desk", dv360: "DV360", xandr: "Xandr" };

interface NextDrawer {
  title: ReactNode;
  body: ReactNode;
}

// End-of-run summary — appended BELOW the crew trace when the flow finishes (no
// page swap): totals, then one card per channel with the booked artifacts and the
// shared-rate-card math behind every price. "What's next" actions open a fixed
// right-hand drawer, so nothing in the grid reflows.
export default function RunSummary(props: { view: RunView; onOpenBookings?: () => void }) {
  const { view, onOpenBookings } = props;
  const rail = (view.rail?.rail as Rail) || null;
  const channels = view.channelOrder.map((ch) => view.channels[ch]).filter(Boolean);
  const totalImps = channels.reduce((n, c) => n + (c.decision?.impressions ?? 0), 0);
  const [drawer, setDrawer] = useState<NextDrawer | null>(null);

  return (
    <div className="runsummary">
      <div className="sum-head">
        <div className="sum-title">
          <h2>Campaign complete</h2>
          {view.summary?.tier && <span className={`badge tier ${view.summary.tier}`}>{view.summary.tier}</span>}
          {rail && <span className={`rail-badge ${rail}`}>{RAIL_LABEL[rail]}</span>}
        </div>
        <div className="sum-head-actions">
          <button
            className="next-btn"
            onClick={() =>
              setDrawer({
                title: (
                  <>
                    Optimize mid-flight <span className="soon">coming soon</span>
                  </>
                ),
                body: (
                  <p>
                    The L1 Portfolio Manager re-allocates budget between channels from live pacing
                    data — the crew keeps working after the buy.
                  </p>
                ),
              })
            }
          >
            🔁 Optimize mid-flight <span className="soon">coming soon</span>
          </button>
          {onOpenBookings && (
            <button className="btn" onClick={onOpenBookings}>
              Bookings ledger →
            </button>
          )}
        </div>
      </div>

      {view.summary && (
        <div className="stats">
          <div className="stat">
            <div className="n booked">{view.summary.booked}</div>
            <div className="l">channels booked</div>
          </div>
          <div className="stat">
            <div className="n passed">{view.summary.passed}</div>
            <div className="l">passed</div>
          </div>
          <div className="stat">
            <div className="n">{money(view.summary.spend)}</div>
            <div className="l">committed spend</div>
          </div>
          <div className="stat">
            <div className="n">{fmtInt(totalImps)}</div>
            <div className="l">impressions</div>
          </div>
          <div className="stat">
            <div className="n">{totalImps ? fmtCpm((view.summary.spend / totalImps) * 1000) : "—"}</div>
            <div className="l">blended CPM</div>
          </div>
        </div>
      )}

      <div className="sum-channels">
        {channels.map((c) => {
          const d = c.decision;
          if (!d) return null;
          const booked = d.decision === "BOOKED";
          const spend = d.finalCpm != null && d.impressions != null ? (d.finalCpm * d.impressions) / 1000 : null;
          return (
            <div key={c.channel} className={`sum-card ${booked ? "booked" : "passed"}`}>
              <div className="sum-card-head">
                <span className="ch-name">{c.channel}</span>
                <span className={`d-tag ${d.decision}`}>{d.decision}</span>
              </div>
              <div className="sum-facts">
                <span className="sk">Seller</span>
                <span className="sv">{d.sellerName || c.seller || "—"}</span>
                <span className="sk">Product</span>
                <span className="sv">{d.productName || d.productId || "—"}</span>
                <span className="sk">Booked CPM</span>
                <span className="sv">{fmtCpm(d.finalCpm)}</span>
                <span className="sk">Impressions</span>
                <span className="sv">{fmtInt(d.impressions)}</span>
                <span className="sk">Spend</span>
                <span className="sv">
                  {spend != null ? money(spend) : "—"}
                  {d.channelBudget != null && <span className="hint"> / {money(d.channelBudget)} budget</span>}
                </span>
              </div>

              {d.card?.base_price != null && (
                <div className="ratecard">
                  <span className="rc-tag">shared rate card</span>
                  <span className="rc-math">
                    {fmtCpm(d.card.base_price)} card
                    {(d.card.tier_discount ?? 0) > 0 && <> − {Math.round((d.card.tier_discount ?? 0) * 100)}% tier</>}
                    {(d.card.volume_discount ?? 0) > 0 && (
                      <> − {Math.round((d.card.volume_discount ?? 0) * 100)}% volume</>
                    )}
                    {" → "}
                    <b>{fmtCpm(d.card.final_price ?? d.finalCpm)}</b>
                  </span>
                </div>
              )}

              {booked ? (
                <>
                  <div className="sum-facts artifacts">
                    {d.orderId && (
                      <>
                        <span className="sk">OpenDirect IO</span>
                        <span className="sv mono">{d.orderId}</span>
                        <span className="sk">Creative</span>
                        <span className="sv mono">
                          {d.creativeId || "—"}
                          {d.creativeStatus === "READY" && <span className="ok-note"> · approved ✓</span>}
                        </span>
                      </>
                    )}
                    {d.dealId && (
                      <>
                        <span className="sk">PG Deal ID</span>
                        <span className="sv mono">{d.dealId}</span>
                        <span className="sk">SSP</span>
                        <span className="sv">{d.ssp || "—"}</span>
                      </>
                    )}
                  </div>

                  {/* what a media desk does next with this artifact — opens the drawer */}
                  <div className="sum-next">
                    {d.orderId && (
                      <>
                        <button
                          className="next-btn"
                          onClick={() =>
                            setDrawer({
                              title: (
                                <>
                                  Insertion order <span className="soon">coming soon</span>
                                </>
                              ),
                              body: (
                                <p>
                                  The OpenDirect IO document for order{" "}
                                  <span className="mono">{d.orderId}</span> — line, flight,{" "}
                                  {fmtInt(d.impressions)} impression goal at {fmtCpm(d.finalCpm)},
                                  signatures — ready for ops &amp; finance.
                                </p>
                              ),
                            })
                          }
                        >
                          📄 View insertion order
                        </button>
                        <button
                          className="next-btn"
                          onClick={() =>
                            setDrawer({
                              title: (
                                <>
                                  Delivery pacing <span className="soon">coming soon</span>
                                </>
                              ),
                              body: (
                                <p>
                                  Once the flight starts: impressions delivered vs the{" "}
                                  {fmtInt(d.impressions)} goal, daily pacing, projected over/under-
                                  delivery — with makegoods if the seller under-delivers.
                                </p>
                              ),
                            })
                          }
                        >
                          📈 Track delivery
                        </button>
                      </>
                    )}
                    {d.dealId && (
                      <button
                        className="next-btn"
                        onClick={() =>
                          setDrawer({
                            title: "Activate in your DSP",
                            body: (
                              <>
                                <p>
                                  The seller's SSP{d.ssp ? ` (${d.ssp})` : ""} already carries Deal ID{" "}
                                  <span className="mono">{d.dealId}</span>. Enter it in your DSP to
                                  start bidding:
                                </p>
                                {d.activationInstructions ? (
                                  <ul className="np-list">
                                    {Object.entries(d.activationInstructions).map(([k, v]) => (
                                      <li key={k}>
                                        <b>{DSP_NAME[k] ?? k}</b>
                                        <span>{v}</span>
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p>Your DSP's deal library takes the Deal ID as-is.</p>
                                )}
                              </>
                            ),
                          })
                        }
                      >
                        ⚡ Activate
                      </button>
                    )}
                  </div>
                </>
              ) : (
                d.reason && <div className="d-reason">{d.reason}</div>
              )}
            </div>
          );
        })}
      </div>

      {drawer && (
        <>
          <div className="drawer-overlay" onClick={() => setDrawer(null)} />
          <aside className="drawer">
            <div className="drawer-head">
              <span className="drawer-title">{drawer.title}</span>
              <button className="drawer-close" onClick={() => setDrawer(null)} aria-label="Close">
                ✕
              </button>
            </div>
            <div className="drawer-body">{drawer.body}</div>
          </aside>
        </>
      )}
    </div>
  );
}
