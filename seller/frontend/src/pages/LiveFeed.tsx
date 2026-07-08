import { useCallback, useEffect, useRef, useState } from "react";
import { api, DealBooking, money, Order, Quote } from "../api";

// The seller's live activity feed: quotes, deals, and orders as they land — so when
// a buyer agent transacts against this seller, you watch it happen in real time.

type Kind = "quote" | "deal" | "order";
interface Ev { id: string; ts: number; kind: Kind; title: string; detail: string; status?: string; }

const KIND_LABEL: Record<Kind, string> = { quote: "Quote", deal: "Deal", order: "Order" };
const POLL_MS = 3000;

function parseTs(s?: string): number {
  if (!s) return 0;
  const t = Date.parse(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
  return Number.isNaN(t) ? 0 : t;
}

function ago(ms: number): string {
  if (!ms) return "—";
  const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function toEvents(quotes: Quote[], deals: DealBooking[], orders: Order[]): Ev[] {
  const evs: Ev[] = [];
  for (const q of quotes) {
    evs.push({
      id: `q-${q.quote_id}`, ts: parseTs(q.created_at as string), kind: "quote",
      title: q.quote_id,
      detail: `${q.product?.name ?? q.product?.product_id ?? "product"} · ${money(q.pricing?.final_cpm ?? 0)} CPM · ${q.buyer_tier ?? "?"}`,
      status: q.status,
    });
  }
  for (const d of deals) {
    const bidfloor = d.openrtb_params?.bidfloor ?? d.pricing?.final_cpm ?? 0;
    const dist = d.distribution;
    evs.push({
      id: `d-${d.deal_id}`, ts: parseTs(d.created_at as string), kind: "deal",
      title: d.deal_id,
      detail: `${d.deal_type} · bidfloor ${money(bidfloor)}` +
        (dist ? ` · SSP ${dist.ssp_name} (${dist.ssp_deal_id})` : " · not distributed"),
      status: dist ? "distributed" : d.status,
    });
  }
  for (const o of orders) {
    evs.push({
      id: `o-${o.execution_order_id}`, ts: parseTs((o.booked_at ?? o.created_at) as string), kind: "order",
      title: o.execution_order_id,
      detail: `${o.lines?.length ?? 0} line(s)${o.lines?.[0]?.pricing?.final_cpm ? ` · ${money(o.lines[0].pricing.final_cpm)} CPM` : ""}`,
      status: o.status,
    });
  }
  return evs.sort((a, b) => b.ts - a.ts);
}

export default function LiveFeed() {
  const [events, setEvents] = useState<Ev[]>([]);
  const [live, setLive] = useState(true);
  const [lastSync, setLastSync] = useState<number>(0);
  const [err, setErr] = useState("");
  const seen = useRef<Set<string>>(new Set());
  const [fresh, setFresh] = useState<Set<string>>(new Set());

  const poll = useCallback(async () => {
    try {
      const [quotes, deals, orders] = await Promise.all([
        api.listQuotes().catch(() => [] as Quote[]),
        api.listDeals().catch(() => [] as DealBooking[]),
        api.listOrders().catch(() => [] as Order[]),
      ]);
      const evs = toEvents(quotes, deals, orders);
      // Flag rows we haven't seen before so they can flash in.
      const newIds = new Set<string>();
      for (const e of evs) if (!seen.current.has(e.id)) newIds.add(e.id);
      evs.forEach((e) => seen.current.add(e.id));
      setEvents(evs);
      setErr("");
      setLastSync(Date.now());
      if (newIds.size) {
        setFresh(newIds);
        setTimeout(() => setFresh(new Set()), 1500);
      }
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    poll(); // initial
    if (!live) return;
    const t = setInterval(poll, POLL_MS);
    return () => clearInterval(t);
  }, [live, poll]);

  // Re-render the "Xs ago" labels on a light cadence.
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const counts = {
    quote: events.filter((e) => e.kind === "quote").length,
    deal: events.filter((e) => e.kind === "deal").length,
    order: events.filter((e) => e.kind === "order").length,
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>Live Feed</h1>
        <p className="page-sub">
          Quotes, deals, and orders as they land — when a buyer agent transacts against this seller,
          it shows up here in real time. Polls every {POLL_MS / 1000}s.
        </p>
      </div>

      <div className="feed-bar">
        <div className="feed-stats">
          <span className="feed-stat"><b>{counts.quote}</b> quotes</span>
          <span className="feed-stat"><b>{counts.deal}</b> deals</span>
          <span className="feed-stat"><b>{counts.order}</b> orders</span>
        </div>
        <div className="feed-controls">
          <span className="feed-sync">{err ? `error: ${err}` : `synced ${ago(lastSync)}`}</span>
          <button className={`live-toggle ${live ? "on" : ""}`} onClick={() => setLive((v) => !v)}>
            <span className="live-dot" /> {live ? "Live" : "Paused"}
          </button>
        </div>
      </div>

      {events.length === 0 ? (
        <div className="loading">
          No activity yet — point a buyer agent at this seller and its quotes/deals/orders
          will stream in here.
        </div>
      ) : (
        <div className="feed">
          {events.map((e) => (
            <div className={`feed-event ${fresh.has(e.id) ? "fresh" : ""}`} key={e.id}>
              <span className={`feed-dot ${e.kind}`} />
              <div className="feed-main">
                <div className="feed-line1">
                  <span className={`feed-kind ${e.kind}`}>{KIND_LABEL[e.kind]}</span>
                  <span className="feed-title">{e.title}</span>
                  {e.status && <span className="feed-status">{e.status}</span>}
                </div>
                <div className="feed-detail">{e.detail}</div>
              </div>
              <span className="feed-time">{ago(e.ts)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
