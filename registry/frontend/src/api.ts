// Typed client over the registry's REST surface (/registry/*). Same origin as the app.

export interface AgentCard { name: string; url: string; description?: string; provider?: { name: string }; [k: string]: any; }
export interface RegisteredAgent {
  agent_id: string;
  agent_card: AgentCard;
  agent_type: string;       // buyer | seller | tool_provider | data_provider | other
  trust_status: string;     // unknown | registered | approved | preferred | blocked
  registry_sources: any[];
  registered_at?: string;
  last_seen?: string | null;
  interaction_count?: number;
  notes?: string | null;
}
export interface VerifyResult {
  agent_url: string;
  agent_id: string | null;
  trust_status: string;
  max_access_tier: string | null;
  is_blocked: boolean;
}

// The SDK's TRUST_TO_TIER_MAP (models/agent_registry.py) — shown as the tier ladder.
export const TRUST_LADDER: { trust: string; tier: string | null; note: string }[] = [
  { trust: "unknown", tier: "public", note: "Never seen — price ranges only" },
  { trust: "registered", tier: "seat", note: "In the AAMP registry — exact prices" },
  { trust: "approved", tier: "advertiser", note: "Operator-approved — full access" },
  { trust: "preferred", tier: "advertiser", note: "Strategic partner — best rates" },
  { trust: "blocked", tier: null, note: "Hard reject — zero data access" },
];

export const TRUST_OPTIONS = ["unknown", "registered", "approved", "preferred", "blocked"];

async function jget<T>(url: string): Promise<T> {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function jsend<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${url}: ${await r.text()}`);
  return r.json();
}

const qs = (params: Record<string, string>) => {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v));
  const s = new URLSearchParams(clean).toString();
  return s ? `?${s}` : "";
};

export const api = {
  agentCard: () => jget<AgentCard>("/.well-known/agent.json"),
  listAgents: (agent_type = "", trust_status = "") =>
    jget<RegisteredAgent[]>(`/registry/agents${qs({ agent_type, trust_status })}`),
  getAgent: (id: string) => jget<RegisteredAgent>(`/registry/agents/${encodeURIComponent(id)}`),
  verify: (agent_url: string) => jsend<VerifyResult>("POST", "/registry/verify", { agent_url }),
  discover: (agent_url: string) => jsend<any>("POST", "/registry/agents/discover", { agent_url }),
  register: (agent_card: AgentCard, agent_type: string, trust_status: string) =>
    jsend<RegisteredAgent>("POST", "/registry/agents", { agent_card, agent_type, trust_status }),
  updateTrust: (id: string, trust_status: string, notes?: string) =>
    jsend<RegisteredAgent>("PUT", `/registry/agents/${encodeURIComponent(id)}/trust`, { trust_status, notes }),
  remove: (id: string) => jsend<{ removed: boolean }>("DELETE", `/registry/agents/${encodeURIComponent(id)}`),
};
