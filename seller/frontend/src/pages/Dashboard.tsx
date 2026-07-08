import { useEffect, useState } from "react";
import { api, DealBooking, IDENTITIES, money, Order, PackageView } from "../api";

interface TierRow { key: string; label: string; cpm: number; }
const TIER_LABEL: Record<string, string> = Object.fromEntries(IDENTITIES.map((i) => [i.key, i.label]));

// One row of the unified bookings table — an OpenDirect order (rail 1) or a PG deal
// (rail 2), normalized for the list; `order`/`deal` keep the full record for the drawer.
interface BookingRow {
  kind: "order" | "deal";
  id: string;
  tier?: string;
  product?: string;
  cpm?: number | null;
  imps?: number;
  revenue?: number | null;
  status: string;
  ts?: string;
  order?: Order;
  deal?: DealBooking;
}

function orderRow(o: Order): BookingRow {
  const imps = (o.lines ?? []).reduce((n: number, l: any) => n + (l.delivery_goal?.goal_amount ?? 0), 0);
  const revenue = (o.lines ?? []).reduce(
    (n: number, l: any) => n + ((l.pricing?.final_cpm ?? 0) * (l.delivery_goal?.goal_amount ?? 0)) / 1000,
    0
  );
  return {
    kind: "order", id: o.execution_order_id, tier: o.buyer_tier,
    product: o.lines?.[0]?.product_id, cpm: o.lines?.[0]?.pricing?.final_cpm,
    imps, revenue: revenue || null, status: o.status,
    ts: o.booked_at ?? o.created_at, order: o,
  };
}

function dealRow(d: DealBooking): BookingRow {
  const cpm = d.pricing?.final_cpm ?? d.openrtb_params?.bidfloor;
  const imps = d.terms?.impressions ?? 0;
  return {
    kind: "deal", id: d.deal_id, tier: d.buyer_tier,
    product: d.product?.name ?? d.product?.product_id, cpm, imps,
    revenue: cpm != null && imps ? (cpm * imps) / 1000 : null,
    status: d.distribution ? "distributed" : d.status,
    ts: (d as any).created_at ?? d.expires_at, deal: d,
  };
}

export default function Dashboard() {
  const [pkgs, setPkgs] = useState<PackageView[] | null>(null);
  const [spread, setSpread] = useState<TierRow[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [deals, setDeals] = useState<DealBooking[]>([]);
  const [selected, setSelected] = useState<BookingRow | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    // ONE aggregated round-trip (queries run parallel server-side) instead of ~6.
    const adv = IDENTITIES.find((i) => i.key === "advertiser")!;
    api.overview(adv)
      .then((o) => {
        setPkgs(o.packages);
        setOrders(o.orders);
        setSpread(o.tier_spread.map((t) => ({ key: t.tier, label: TIER_LABEL[t.tier] ?? t.tier, cpm: t.final_price })));
      })
      .catch((e) => setErr(String(e)));
    api.listDeals().then(setDeals).catch(() => {}); // rail-2 rows are best-effort
  }, []);

  const booked = orders.filter((o) => o.status === "BOOKED");
  const gmv = booked.reduce((sum, o) => {
    for (const l of o.lines ?? []) {
      const cpm = l.pricing?.final_cpm ?? 0;
      const imp = l.delivery_goal?.goal_amount ?? 0;
      sum += (cpm * imp) / 1000;
    }
    return sum;
  }, 0);
  const maxCpm = Math.max(1, ...spread.map((s) => s.cpm));
  const rack = spread.find((s) => s.key === "public")?.cpm;
  const best = spread.find((s) => s.key === "advertiser")?.cpm;
  const savePct = rack && best ? Math.round(((rack - best) / rack) * 100) : 0;

  const rows: BookingRow[] = [...orders.map(orderRow), ...deals.map(dealRow)].sort((a, b) =>
    (b.ts ?? "").localeCompare(a.ts ?? "")
  );
  const dealRevenue = rows.filter((r) => r.kind === "deal").reduce((n, r) => n + (r.revenue ?? 0), 0);

  if (err) return <div className="page"><div className="loading">Couldn’t load dashboard: {err}</div></div>;
  if (!pkgs) return <div className="page"><div className="loading">Loading dashboard…</div></div>;

  return (
    <div className="page">
      <div className="page-head">
        <h1>Seller Dashboard</h1>
        <p className="page-sub">
          What this seller agent offers and has transacted — all served fresh from Lakebase, priced
          deterministically by buyer tier with no negotiation.
        </p>
      </div>

      <div className="kpis">
        <Kpi label="Packages offered" value={String(pkgs.length)} foot="Live in the media kit" spark="📦" />
        <Kpi label="Rack CPM (flagship)" value={rack != null ? money(rack) : "—"} foot="Anonymous / public rate" spark="🏷️" />
        <Kpi label="Best advertiser CPM" value={best != null ? money(best) : "—"} foot={`${savePct}% below rack for verified advertisers`} accent />
        <Kpi label="Direct IO revenue" value={money(gmv)} foot={`${booked.length} booked execution order${booked.length === 1 ? "" : "s"}`} spark="💰" />
        <Kpi label="PG deal revenue" value={money(dealRevenue)} foot={`${deals.length} Deal ID${deals.length === 1 ? "" : "s"} minted`} spark="🤝" />
      </div>

      <div className="charts" style={{ gridTemplateColumns: "1.3fr 1fr" }}>
        <div className="panel">
          <h3>Tier pricing spread<span className="hint">same flagship product, every buyer tier</span></h3>
          {spread.length === 0 ? (
            <div className="loading" style={{ padding: 24 }}>No priced product available.</div>
          ) : (
            spread.map((s) => (
              <div className="vbar-row" key={s.key}>
                <div className="vbar-label"><span className={`dot ${s.key}`} />{s.label}</div>
                <div className="vbar-track"><div className="vbar-fill" style={{ width: `${(s.cpm / maxCpm) * 100}%` }} /></div>
                <div className="vbar-count">{money(s.cpm)}</div>
              </div>
            ))
          )}
        </div>
        <div className="panel value-prop" style={{ alignSelf: "start" }}>
          <div className="vp-stat"><span className="vp-num">{savePct}%</span><span className="vp-cap">verified-advertiser discount</span></div>
          <div className="vp-copy">
            Pricing is fully deterministic: the rate on the card <strong>equals</strong> the quote — no
            negotiation, no surprises. A buyer’s tier (anonymous → agency → advertiser) is the only lever,
            and it’s applied the same way every time.
          </div>
        </div>
      </div>

      <div className="table-wrap">
        <div className="table-head">
          <h3>Bookings — Direct IO &amp; Programmatic<span className="hint"> click a row for full detail</span></h3>
        </div>
        <table>
          <thead>
            <tr>
              <th>Type</th><th>ID</th><th>Buyer tier</th><th>Product</th>
              <th className="num">CPM</th><th className="num">Impressions</th><th className="num">Revenue</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td className="empty-row" colSpan={8}>No bookings yet — when a buyer agent books against this seller (either rail) it’ll show up here.</td></tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className="rowlink" onClick={() => setSelected(r)}>
                  <td><span className={`rail-cell ${r.kind}`}>{r.kind === "order" ? "DIRECT IO" : "PG → SSP"}</span></td>
                  <td style={{ fontFamily: "var(--mono)" }}>{r.id}</td>
                  <td><span className={`badge ${r.tier ?? "public"}`}>{r.tier ?? "—"}</span></td>
                  <td>{r.product ?? "—"}</td>
                  <td className="num">{r.cpm != null ? money(r.cpm) : "—"}</td>
                  <td className="num">{r.imps ? r.imps.toLocaleString() : "—"}</td>
                  <td className="num">{r.revenue != null ? money(r.revenue) : "—"}</td>
                  <td><span className={`badge ${["BOOKED", "distributed"].includes(r.status) ? "booked" : "draft"}`}>{r.status}</span></td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {selected && <BookingDrawer row={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

// Right-hand detail drawer for one booking (order or deal) — every field the ledger
// row summarizes, straight off the seller's stored record.
function BookingDrawer({ row, onClose }: { row: BookingRow; onClose: () => void }) {
  const o = row.order;
  const d = row.deal;
  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <aside className="drawer">
        <div className="drawer-head">
          <span className={`rail-cell ${row.kind}`}>{row.kind === "order" ? "DIRECT IO" : "PG → SSP"}</span>
          <span className="drawer-id">{row.id}</span>
          <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="drawer-body">
          <DrawerRow k="Status" v={row.status} />
          <DrawerRow k="Buyer tier" v={row.tier ?? "public"} />
          {row.ts && <DrawerRow k={row.kind === "order" ? "Booked" : "Created"} v={new Date(row.ts).toLocaleString()} />}

          {o && (
            <>
              {o.buyer_identity && (
                <DrawerSection title="Buyer identity">
                  {Object.entries(o.buyer_identity as Record<string, string | null>)
                    .filter(([, v]) => v)
                    .map(([k, v]) => <DrawerRow key={k} k={k} v={String(v)} mono />)}
                </DrawerSection>
              )}
              {(o.lines ?? []).map((l: any, i: number) => (
                <DrawerSection key={i} title={`Line ${i + 1} — ${l.product_id ?? ""}`}>
                  <DrawerRow k="Deal type" v={l.deal_type ?? "—"} />
                  <DrawerRow k="CPM" v={l.pricing?.final_cpm != null ? money(l.pricing.final_cpm) : "—"} />
                  <DrawerRow k="Impression goal" v={l.delivery_goal?.goal_amount?.toLocaleString() ?? "—"} />
                  <DrawerRow k="Flight" v={l.flight_start ? `${l.flight_start} → ${l.flight_end}` : "—"} />
                  <DrawerRow k="Quote" v={l.quote_id ?? "—"} mono />
                  {l.pricing?.rationale && <div className="drawer-note">{l.pricing.rationale}</div>}
                </DrawerSection>
              ))}
              <DrawerSection title="Creatives">
                {(o.creatives ?? []).length === 0 ? (
                  <div className="drawer-note">none submitted</div>
                ) : (
                  (o.creatives ?? []).map((c) => <DrawerRow key={c} k="creative" v={c} mono />)
                )}
              </DrawerSection>
            </>
          )}

          {d && (
            <>
              <DrawerSection title="Deal terms">
                <DrawerRow k="Product" v={d.product?.name ?? d.product?.product_id ?? "—"} />
                <DrawerRow k="Deal type" v={d.deal_type} />
                <DrawerRow k="CPM" v={d.pricing?.final_cpm != null ? money(d.pricing.final_cpm) : "—"} />
                <DrawerRow k="Impressions" v={d.terms?.impressions?.toLocaleString() ?? "—"} />
                <DrawerRow k="Flight" v={d.terms?.flight_start ? `${d.terms.flight_start} → ${d.terms.flight_end}` : "—"} />
                <DrawerRow k="Quote" v={d.quote_id} mono />
                <DrawerRow k="OpenRTB bidfloor" v={d.openrtb_params?.bidfloor != null ? money(d.openrtb_params.bidfloor) : "—"} />
                <DrawerRow k="Expires" v={d.expires_at ? new Date(d.expires_at).toLocaleString() : "—"} />
                {(d.pricing as any)?.rationale && <div className="drawer-note">{(d.pricing as any).rationale}</div>}
              </DrawerSection>
              <DrawerSection title="SSP distribution">
                {d.distribution ? (
                  <>
                    <DrawerRow k="SSP" v={String(d.distribution.ssp_name)} />
                    <DrawerRow k="SSP deal id" v={String(d.distribution.ssp_deal_id)} mono />
                    <DrawerRow k="Status" v={String(d.distribution.status)} />
                    {d.distribution.distributed_at && (
                      <DrawerRow k="Distributed" v={new Date(String(d.distribution.distributed_at)).toLocaleString()} />
                    )}
                  </>
                ) : (
                  <div className="drawer-note">not distributed — the Deal ID exists but no SSP carries it yet</div>
                )}
              </DrawerSection>
            </>
          )}
        </div>
      </aside>
    </>
  );
}

function DrawerSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="drawer-section">
      <div className="drawer-section-title">{title}</div>
      {children}
    </div>
  );
}

function DrawerRow({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="drawer-row">
      <span className="dk">{k}</span>
      <span className={`dv${mono ? " mono" : ""}`}>{v}</span>
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
