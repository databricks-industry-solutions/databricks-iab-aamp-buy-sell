// Typed client over the seller's REST surface (/api/v1/*). Same origin as the app,
// so the Databricks Apps proxy session covers auth in deployment; in dev, vite proxies
// to a local uvicorn. Identity (anon/agency/advertiser) threads into every call — the
// seller prices deterministically by the buyer's revealed tier.

export type TierKey = "public" | "agency" | "advertiser";

export interface Identity {
  key: TierKey;
  label: string;
  params: Record<string, string>; // seat_id / agency_id / advertiser_id
}

export const IDENTITIES: Identity[] = [
  { key: "public", label: "Anonymous", params: {} },
  { key: "agency", label: "Agency (Northstar)", params: { agency_id: "agcy-northstar" } },
  {
    key: "advertiser",
    label: "Advertiser (Northstar)",
    params: { agency_id: "agcy-northstar", advertiser_id: "adv-northstar" },
  },
];

export interface PackageView {
  package_id: string;
  name: string;
  price_range?: string;
  exact_price?: number | null;
  inventory_type?: string | null;
  [k: string]: any;
}

export interface PricingDecision {
  product_id: string;
  final_price: number;
  base_cpm?: number;
  tier_discount?: number;
  volume_discount?: number;
  buyer_tier?: string;
  rationale?: string;
  [k: string]: any;
}

export interface Quote {
  quote_id: string;
  status: string;
  pricing: { base_cpm: number; final_cpm: number; tier_discount_pct?: number; volume_discount_pct?: number; rationale?: string; [k: string]: any };
  product: { product_id: string; name?: string; [k: string]: any };
  buyer_tier?: string;
  deal_type?: string;
  [k: string]: any;
}

export interface Order {
  execution_order_id: string;
  status: string;
  buyer_tier?: string;
  lines: any[];
  creatives?: string[];
  created_at?: string;
  booked_at?: string;
  [k: string]: any;
}

export interface DealBooking {
  deal_id: string;
  deal_type: string;
  status: string;
  quote_id: string;
  pricing: { final_cpm: number; [k: string]: any };
  terms: { impressions?: number; flight_start?: string; flight_end?: string; [k: string]: any };
  buyer_tier?: string;
  expires_at?: string;
  activation_instructions: Record<string, string>;
  openrtb_params: Record<string, any>;
  distribution?: Record<string, any>;
  [k: string]: any;
}

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function jpost<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${url}: ${await r.text()}`);
  return r.json();
}

const qs = (params: Record<string, string>) => {
  const s = new URLSearchParams(params).toString();
  return s ? `?${s}` : "";
};

export const api = {
  agentCard: () => jget<any>("/.well-known/agent.json"),

  listPackages: (id: Identity, extra: Record<string, string> = {}) =>
    jget<PackageView[]>(`/api/v1/packages${qs({ ...id.params, ...extra })}`),

  getPackage: (packageId: string, id: Identity) =>
    jget<PackageView>(`/api/v1/packages/${encodeURIComponent(packageId)}${qs(id.params)}`),

  getPricing: (productId: string, id: Identity, volume = 0) =>
    jget<PricingDecision>(`/api/v1/pricing${qs({ product_id: productId, volume: String(volume), ...id.params })}`),

  requestQuote: (body: Record<string, any>) => jpost<Quote>("/api/v1/quotes", body),

  createOrder: (id: Identity) => jpost<Order>("/api/v1/orders", { ...id.params }),

  createLine: (orderId: string, quoteId: string) =>
    jpost<Order>(`/api/v1/orders/${encodeURIComponent(orderId)}/lines`, { quote_id: quoteId }),

  submitCreative: (orderId: string, assetUrl: string, name = "creative") =>
    jpost<any>(`/api/v1/orders/${encodeURIComponent(orderId)}/creatives`, { asset_url: assetUrl, name }),

  // Rail 2, step 1 — mint a PG Deal ID from an accepted quote.
  createDeal: (quoteId: string, notes?: string) =>
    jpost<DealBooking>("/api/v1/deals", { quote_id: quoteId, notes }),
  // Rail 2, step 2 — activate the Deal ID in an SSP (stubbed).
  distributeDealViaSsp: (dealId: string, ssp: string) =>
    jpost<{ deal_id: string; distribution: Record<string, any> }>(
      `/api/v1/deals/${encodeURIComponent(dealId)}/distribute`, { ssp }),

  listOrders: () => jget<Order[]>("/api/v1/orders"),
  listQuotes: () => jget<Quote[]>("/api/v1/quotes"),
  listDeals: () => jget<DealBooking[]>("/api/v1/deals"),

  // One aggregated round-trip for the dashboard (queries run parallel server-side).
  overview: (id: Identity) => jget<Overview>(`/api/v1/overview${qs(id.params)}`),

  // The seller's audience catalog (names/sizes/IAB ids).
  listAudiences: () => jget<Audience[]>("/api/v1/audiences"),

  // public→agency→advertiser price spread for one product (SDK rationale included).
  productPricing: (productId: string, id: Identity) =>
    jget<TierPrice[]>(`/api/v1/product-pricing${qs({ product_id: productId, ...id.params })}`),
};

export interface Overview {
  packages: PackageView[];
  orders: Order[];
  tier_spread: TierPrice[];
  flagship_product: string | null;
}

export interface TierPrice {
  tier: string;            // public | agency | advertiser
  final_price: number;
  base_price?: number;
  tier_discount?: number;  // fraction, e.g. 0.15
  volume_discount?: number;
  rationale?: string;
  applied_rules?: string[];
}

export interface Audience {
  id: string;
  name: string;
  description?: string;
  size?: number;
  segment_type?: string;
  status?: string;
  iab_audience_taxonomy_id?: string;
  [k: string]: any;
}

export interface Product {
  product_id: string;
  name?: string;
  ad_formats?: string[];
  device_types?: number[];
  [k: string]: any;
}

// The authenticated package view nests its products under `placements`; walk defensively.
export function collectProducts(detail: any): Product[] {
  const out: Product[] = [];
  const seen = new Set<string>();
  const walk = (o: any) => {
    if (!o) return;
    if (Array.isArray(o)) return o.forEach(walk);
    if (typeof o === "object") {
      if (typeof o.product_id === "string" && !seen.has(o.product_id)) {
        seen.add(o.product_id);
        out.push({ product_id: o.product_id, name: o.name ?? o.product_name, ad_formats: o.ad_formats, device_types: o.device_types });
      }
      Object.values(o).forEach(walk);
    }
  };
  walk(detail);
  return out;
}

// The package carries audience segment ids under audience_capabilities.standard_segment_ids.
export function collectAudienceIds(detail: any): string[] {
  const ids = new Set<string>();
  const walk = (o: any) => {
    if (!o) return;
    if (Array.isArray(o)) return o.forEach(walk);
    if (typeof o === "object") {
      if (Array.isArray(o.standard_segment_ids)) o.standard_segment_ids.forEach((x: any) => ids.add(String(x)));
      if (Array.isArray(o.contextual_segment_ids)) o.contextual_segment_ids.forEach((x: any) => ids.add(String(x)));
      Object.values(o).forEach(walk);
    }
  };
  walk(detail);
  return [...ids];
}

export const money = (n: number) => `$${n.toFixed(2)}`;
export const pct = (x?: number) => (x ? `${Math.round(x * 100)}%` : "0%");
