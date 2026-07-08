// Types + tiny helpers for the buyer backend API (consume-only; see buyer/app.py).

export interface BriefChannel {
  channel: string;
  budget_pct: number;
  format_prefs?: string[];
}

export interface DealPreferences {
  max_cpm?: number | null;
  execution?: string; // direct | programmatic
  preferred_deal_types?: string[];
}

export interface Brief {
  brief_id: string;
  name?: string;
  buyer_id?: string;
  total_budget?: number;
  currency?: string;
  objectives?: string[];
  start_date?: string;
  end_date?: string;
  target_audience?: { interests?: string[] };
  channels?: BriefChannel[];
  deal_preferences?: DealPreferences;
}

export interface Status {
  buyer_id: string;
  tier: string;
  identity: Record<string, unknown>;
  tiers: string[];
}

export type Rail = "direct" | "programmatic";

/** The brief's fulfilment rail: direct (Rail 1 — OpenDirect IO) | programmatic (Rail 2 — PG deal → SSP). */
export function railOf(b: Brief): Rail {
  const r = String(b.deal_preferences?.execution ?? "direct").toLowerCase();
  return r === "programmatic" ? "programmatic" : "direct";
}

export const RAIL_LABEL: Record<Rail, string> = {
  direct: "DIRECT IO",
  programmatic: "PG → SSP",
};

export function money(n: number | null | undefined): string {
  if (n == null) return "—";
  return "$" + Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export function fmtCpm(n: number | null | undefined): string {
  if (n == null) return "—";
  return "$" + Number(n).toFixed(2);
}

export function fmtInt(n: number | null | undefined): string {
  if (n == null) return "—";
  return Number(n).toLocaleString();
}

export async function fetchBriefs(): Promise<Brief[]> {
  const r = await fetch("/briefs");
  if (!r.ok) throw new Error(`GET /briefs → ${r.status}`);
  return r.json();
}

export async function fetchStatus(): Promise<Status> {
  const r = await fetch("/status");
  if (!r.ok) throw new Error(`GET /status → ${r.status}`);
  return r.json();
}

export async function startRun(briefId: string, tier: string): Promise<{ run_id: string }> {
  const r = await fetch(`/runs/${encodeURIComponent(briefId)}?tier=${encodeURIComponent(tier)}`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`POST /runs → ${r.status}`);
  return r.json();
}

// One seller's ledger records, tagged with the seller they live on (see GET /bookings).
export interface BookedOrder {
  execution_order_id: string;
  status?: string;
  buyer_tier?: string;
  lines?: {
    product_id?: string;
    deal_type?: string;
    pricing?: { final_cpm?: number | null; base_cpm?: number | null };
    delivery_goal?: { goal_amount?: number | null };
    flight_start?: string;
    flight_end?: string;
  }[];
  creatives?: string[];
  created_at?: string;
  booked_at?: string;
  seller_name?: string;
  capability?: string;
}

export interface BookedDeal {
  deal_id: string;
  status?: string;
  quote_id?: string;
  product?: { product_id?: string; name?: string };
  pricing?: { final_cpm?: number | null };
  openrtb_params?: { bidfloor?: number | null };
  terms?: { impressions?: number | null };
  distribution?: { ssp_name?: string; ssp_deal_id?: string };
  created_at?: string;
  seller_name?: string;
  capability?: string;
}

export interface Bookings {
  orders: BookedOrder[];
  deals: BookedDeal[];
  errors: { seller_name?: string; error: string }[];
}

export async function fetchBookings(): Promise<Bookings> {
  const r = await fetch("/bookings");
  if (!r.ok) throw new Error(`GET /bookings → ${r.status}`);
  return r.json();
}
