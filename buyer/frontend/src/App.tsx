import { useEffect, useReducer, useRef, useState } from "react";
import { Brief, Status, fetchBriefs, fetchStatus, startRun } from "./api";
import Bookings from "./components/Bookings";
import BriefPicker from "./components/BriefPicker";
import RunBoard from "./components/RunBoard";
import { initialRun, reducer } from "./state";

// AAMP Buyer Console — Variant A: the seller/registry light "brick" design system
// with an unmistakable navy BUY SIDE identity band. Single screen: pick a brief,
// present a tier, watch the hierarchical crew book or pass live over SSE.
export default function App() {
  const [briefs, setBriefs] = useState<Brief[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState<string>();
  const [selected, setSelected] = useState<string | null>(null);
  const [tier, setTier] = useState("advertiser");
  const [view, dispatch] = useReducer(reducer, initialRun);
  const [tab, setTab] = useState<"how" | "console" | "bookings">("how");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    Promise.all([fetchBriefs(), fetchStatus()])
      .then(([b, s]) => {
        setBriefs(b);
        setStatus(s);
        if (!s.tiers.includes("advertiser") && s.tiers.length) setTier(s.tiers[s.tiers.length - 1]);
      })
      .catch((e) => setLoadError(String(e)));
    return () => {
      esRef.current?.close();
    };
  }, []);

  const subscribe = (runId: string) => {
    esRef.current?.close();
    const es = new EventSource(`/runs/${runId}/events`);
    es.onmessage = (ev) => {
      try {
        dispatch({ type: "event", ev: JSON.parse(ev.data) });
      } catch {
        /* ignore malformed lines */
      }
    };
    es.addEventListener("done", () => {
      es.close();
      if (esRef.current === es) esRef.current = null;
    });
    es.addEventListener("error", () => {});
    esRef.current = es;
  };

  const run = async (briefId: string) => {
    dispatch({ type: "starting", briefId });
    try {
      const { run_id } = await startRun(briefId, tier);
      subscribe(run_id);
    } catch (e) {
      dispatch({ type: "start-failed", message: String(e) });
    }
  };

  const newRun = () => {
    esRef.current?.close();
    esRef.current = null;
    dispatch({ type: "reset" });
  };

  const running = view.phase === "starting" || view.phase === "running";

  return (
    <div className="shell">
      {/* Variant A identity: navy BUY SIDE band above the family topbar */}
      <div className="side-band">
        <span className="side-chip">BUY SIDE</span>
        <span className="side-copy">
          AAMP Buyer Agent · presents identity to sellers · books against live prices
        </span>
        <span className="side-buyer">
          {status ? (
            <>
              buyer <b>{status.buyer_id}</b> · presenting <b>{view.tier || tier}</b>
            </>
          ) : (
            "connecting…"
          )}
        </span>
      </div>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">B</span>
          <div className="brand-text">
            <span className="brand-title">AAMP Buyer Console</span>
            <span className="brand-sub">hierarchical media-buying crew · live demo</span>
          </div>
        </div>
        <nav className="mainnav">
          <button className={tab === "how" ? "active" : ""} onClick={() => setTab("how")}>
            Step by step
          </button>
          <button className={tab === "console" ? "active" : ""} onClick={() => setTab("console")}>
            Campaign Console
          </button>
          <button className={tab === "bookings" ? "active" : ""} onClick={() => setTab("bookings")}>
            Bookings
          </button>
        </nav>
        <div className="top-legend">
          <span className="lvl l1">L1</span> Portfolio
          <span className="lvl l2">L2</span> Channel Specialist
          <span className="lvl l3">L3</span> Research · Execution
        </div>
      </header>

      <main className="content">
        <div className="page">
          {tab === "how" ? (
            // The interactive spec walkthrough, served verbatim (public/walkthrough.html)
            // — the presentation's framing: what happens, where, when, why, before GO.
            <iframe className="walk-frame" src="/walkthrough.html" title="An Agentic Media Buy, step by step" />
          ) : tab === "bookings" ? (
            <Bookings />
          ) : view.phase === "idle" ? (
            <BriefPicker
              briefs={briefs}
              status={status}
              selected={selected}
              onSelect={setSelected}
              tier={tier}
              onTier={setTier}
              onRun={() => selected && run(selected)}
              running={running}
              loadError={loadError}
            />
          ) : (
            <RunBoard
              view={view}
              tier={tier}
              onNewRun={newRun}
              onRerun={() => (view.briefId || selected) && run(view.briefId || selected!)}
              onOpenBookings={() => setTab("bookings")}
            />
          )}
        </div>
      </main>
    </div>
  );
}
