import { useEffect, useState } from "react";
import { Bookings as BookingsData, fetchBookings, fmtCpm, fmtInt } from "../api";

// Cross-seller bookings dashboard: every OpenDirect order (Rail 1) and PG deal
// (Rail 2) pulled live from each seller's own MCP ledger — the buyer's single
// view of everything it has committed to, across all sellers and runs.
export default function Bookings() {
  const [data, setData] = useState<BookingsData | null>(null);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    fetchBookings()
      .then((d) => {
        setData(d);
        setError(undefined);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  return (
    <div className="bookings">
      <div className="bk-head">
        <div>
          <h2>Bookings</h2>
          <p className="bk-sub">
            Everything this buyer has committed to, read live from each seller's own ledger
            (<code>list_orders</code> · <code>list_deals</code> over MCP).
          </p>
        </div>
        <button className="bk-refresh" onClick={load} disabled={loading}>
          {loading ? "loading…" : "↻ Refresh"}
        </button>
      </div>

      {error && <div className="bk-error">{error}</div>}
      {data?.errors?.map((e, i) => (
        <div key={i} className="bk-error">
          {e.seller_name || "seller"}: {e.error}
        </div>
      ))}

      <section>
        <h3>
          <span className="rail-chip direct">DIRECT IO</span> OpenDirect orders — guaranteed on the seller's inventory
        </h3>
        {!data || data.orders.length === 0 ? (
          <div className="bk-empty">No orders yet — run a Direct IO brief and they'll land here.</div>
        ) : (
          <div className="bk-tablewrap">
            <table className="bk-table">
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Seller</th>
                  <th>Product</th>
                  <th>Deal type</th>
                  <th>Booked CPM</th>
                  <th>Impressions</th>
                  <th>Flight</th>
                  <th>Creatives</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.orders.map((o) => {
                  const line = o.lines?.[0];
                  return (
                    <tr key={o.execution_order_id}>
                      <td className="mono">{o.execution_order_id}</td>
                      <td>
                        {o.seller_name}
                        {o.capability && <span className="bk-cap">{o.capability}</span>}
                      </td>
                      <td className="mono">{line?.product_id || "—"}</td>
                      <td>{line?.deal_type || "—"}</td>
                      <td className="mono">{fmtCpm(line?.pricing?.final_cpm)}</td>
                      <td className="mono">{fmtInt(line?.delivery_goal?.goal_amount)}</td>
                      <td className="mono">
                        {line?.flight_start ? `${line.flight_start} → ${line.flight_end}` : "—"}
                      </td>
                      <td>{o.creatives?.length ? `${o.creatives.length} READY` : "—"}</td>
                      <td>
                        <span className={`bk-status ${String(o.status || "").toLowerCase()}`}>{o.status}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h3>
          <span className="rail-chip pg">PG → SSP</span> Programmatic deals — Deal IDs distributed to SSPs
        </h3>
        {!data || data.deals.length === 0 ? (
          <div className="bk-empty">No PG deals yet — run a programmatic brief and they'll land here.</div>
        ) : (
          <div className="bk-tablewrap">
            <table className="bk-table">
              <thead>
                <tr>
                  <th>Deal ID</th>
                  <th>Seller</th>
                  <th>Product</th>
                  <th>Booked CPM</th>
                  <th>Impressions</th>
                  <th>SSP</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {data.deals.map((d) => (
                  <tr key={d.deal_id}>
                    <td className="mono">{d.deal_id}</td>
                    <td>
                      {d.seller_name}
                      {d.capability && <span className="bk-cap">{d.capability}</span>}
                    </td>
                    <td className="mono">{d.product?.product_id || d.product?.name || "—"}</td>
                    <td className="mono">{fmtCpm(d.pricing?.final_cpm ?? d.openrtb_params?.bidfloor)}</td>
                    <td className="mono">{fmtInt(d.terms?.impressions)}</td>
                    <td>{d.distribution ? `${d.distribution.ssp_name} · ${d.distribution.ssp_deal_id}` : "not distributed"}</td>
                    <td>
                      <span className={`bk-status ${d.distribution ? "booked" : "proposed"}`}>
                        {d.distribution ? "LIVE IN SSP" : d.status || "proposed"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
