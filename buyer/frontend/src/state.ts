// Run-state reducer: turns the buyer backend's SSE vocabulary (see
// buyer/src/buyer_agent/flow.py emits) into a renderable view model.
// Semantics mirror the legacy presenter (buyer/static/index.html) exactly:
// tool steps carry a pending "← awaiting seller…" return line that the next
// `result` event fills; phases never regress; PORTFOLIO steps go to the L1 note.

export const STAGES: [string, string][] = [
  ["identity", "Identity"],
  ["audience", "Audience"],
  ["budget", "Budget"],
  ["channels", "Channels"],
  ["done", "Done"],
];

export const PHASES = ["discover", "open", "price", "recommend", "gate", "book"] as const;
export const PHASE_LABEL: Record<string, string> = {
  discover: "Discover",
  open: "Open",
  price: "Price",
  recommend: "Recommend",
  gate: "Gate",
  book: "Book",
};

// Per-channel L2 lead persona (matches the seller-side ChannelSpec.role).
export const LEAD: Record<string, string> = {
  CTV: "Connected TV Specialist",
  LINEAR_TV: "Linear TV Specialist",
};

export type AgentKey = "lead" | "research" | "execution";

export interface TraceRow {
  id: number;
  kind: "tool" | "finish" | "return";
  tool?: string;
  agent?: string;
  level: string;
  detail?: string;
  ret?: string | null; // the seller's ← return payload (summary line)
  retPending?: boolean;
  data?: unknown; // full → input (click a row to inspect)
  retData?: unknown; // full ← output (click a row to inspect)
}

export interface Rec {
  productId: string;
  productName?: string;
  finalCpm?: number | null;
  audienceCodes?: string[];
}

export interface RateCard {
  base_price?: number | null;
  tier_discount?: number | null; // fraction, e.g. 0.2
  volume_discount?: number | null;
  final_price?: number | null;
  rationale?: string | null;
}

export interface Priced {
  finalCpm?: number | null;
  maxCpm?: number | null;
  card?: RateCard | null; // the seller's shared rate-card math behind finalCpm
}

export interface Decision {
  decision: string;
  reason?: string;
  finalCpm?: number | null;
  impressions?: number | null;
  orderId?: string;
  dealId?: string;
  ssp?: string;
  executionRail?: string;
  creativeId?: string;
  creativeStatus?: string;
  productId?: string;
  productName?: string;
  sellerName?: string;
  channelBudget?: number | null;
  card?: RateCard | null;
  activationInstructions?: Record<string, string> | null; // per-DSP deal activation text
}

export interface ChannelState {
  channel: string;
  budget: number | null;
  seller?: string;
  statusLabel: string;
  spinning: boolean;
  phases: Record<string, "active" | "done">;
  steps: TraceRow[];
  nextId: number;
  lastToolId: number | null;
  activeAgent: AgentKey | null;
  rec?: Rec | null; // null = crew found no matching product
  priced?: Priced;
  decision?: Decision;
}

export interface Segment {
  interest: string;
  codes: { code: string; name?: string }[];
}

export interface Allocation {
  channel: string;
  budget_pct: number;
  budget: number;
  rationale?: string;
}

export interface RunView {
  phase: "idle" | "starting" | "running" | "done" | "error";
  briefId?: string;
  briefName?: string;
  totalBudget?: number;
  currency?: string;
  maxCpm?: number | null;
  interests?: string[];
  stages: Record<string, "" | "active" | "done">;
  tier?: string;
  identityFields?: string[];
  segments?: Segment[];
  allocations?: Allocation[];
  portfolioNote?: string;
  rail?: { rail: string; message: string };
  channelOrder: string[];
  channels: Record<string, ChannelState>;
  summary?: { booked: number; passed: number; spend: number; tier: string };
  error?: string;
}

export const initialRun: RunView = { phase: "idle", stages: {}, channelOrder: [], channels: {} };

export type Action =
  | { type: "reset" }
  | { type: "starting"; briefId: string }
  | { type: "start-failed"; message: string }
  | { type: "event"; ev: Record<string, any> };

function newChannel(channel: string, budget: number | null = null): ChannelState {
  return {
    channel,
    budget,
    statusLabel: "waiting…",
    spinning: true,
    phases: {},
    steps: [],
    nextId: 1,
    lastToolId: null,
    activeAgent: null,
  };
}

function ensureChannel(view: RunView, channel: string, budget: number | null = null): RunView {
  const existing = view.channels[channel];
  if (existing) {
    if (budget != null && existing.budget == null) {
      return {
        ...view,
        channels: { ...view.channels, [channel]: { ...existing, budget } },
      };
    }
    return view;
  }
  return {
    ...view,
    channelOrder: [...view.channelOrder, channel],
    channels: { ...view.channels, [channel]: newChannel(channel, budget) },
  };
}

function withChannel(
  view: RunView,
  channel: string,
  fn: (c: ChannelState) => ChannelState,
  budget: number | null = null
): RunView {
  const v = ensureChannel(view, channel, budget);
  const c = v.channels[channel];
  return { ...v, channels: { ...v.channels, [channel]: fn(c) } };
}

// Mark `key` active and everything before it done (idempotent, never regresses).
function setPhase(phases: Record<string, "active" | "done">, key?: string) {
  if (!key) return phases;
  const idx = PHASES.indexOf(key as (typeof PHASES)[number]);
  if (idx < 0) return phases;
  const next = { ...phases };
  PHASES.forEach((p, i) => {
    if (i < idx) next[p] = "done";
    else if (i === idx && next[p] !== "done") next[p] = "active";
  });
  return next;
}

function phaseDone(phases: Record<string, "active" | "done">, key: string) {
  return { ...phases, [key]: "done" as const };
}

function setStages(view: RunView, entries: Record<string, "" | "active" | "done">): RunView {
  return { ...view, stages: { ...view.stages, ...entries } };
}

// Which crew member is acting: exact lead-role match, else level, else role keywords.
function agentKeyOf(channel: string, ev: Record<string, any>): AgentKey | null {
  const agent = String(ev.agent ?? "");
  if (agent && agent === LEAD[channel]) return "lead";
  if (ev.level === "L2") return "lead";
  if (/research/i.test(agent)) return "research";
  if (/execution/i.test(agent)) return "execution";
  return null;
}

// Trace feed: each MCP call is a row (the → request); the seller's response arrives
// as a `result` event and attaches as the ← return line under that same row.
function addStep(c: ChannelState, ev: Record<string, any>): ChannelState {
  if (ev.kind === "result") return addResult(c, ev);
  const isTool = ev.kind === "tool";
  const row: TraceRow = {
    id: c.nextId,
    kind: isTool ? "tool" : "finish",
    tool: ev.tool,
    agent: ev.agent,
    level: ev.level || (isTool ? "L3" : "L2"),
    detail: ev.detail,
    retPending: isTool,
    data: ev.data,
  };
  return {
    ...c,
    steps: [...c.steps, row],
    nextId: c.nextId + 1,
    lastToolId: isTool ? row.id : null,
  };
}

// Attach the seller's response to the most recent pending tool row (else standalone).
function addResult(c: ChannelState, ev: Record<string, any>): ChannelState {
  const targetId = c.lastToolId;
  const target = targetId != null ? c.steps.find((s) => s.id === targetId) : undefined;
  if (target && target.retPending) {
    return {
      ...c,
      steps: c.steps.map((s) =>
        s.id === targetId
          ? { ...s, ret: ev.detail ?? "∅", retPending: false, retData: ev.data }
          : s
      ),
    };
  }
  const row: TraceRow = {
    id: c.nextId,
    kind: "return",
    level: ev.level || "L3",
    ret: ev.detail ?? "∅",
    retData: ev.data,
  };
  return { ...c, steps: [...c.steps, row], nextId: c.nextId + 1 };
}

function handleEvent(view: RunView, ev: Record<string, any>): RunView {
  switch (ev.type) {
    case "run.started": {
      let v: RunView = {
        ...view,
        phase: "running",
        briefId: ev.brief_id,
        briefName: ev.brief_name || ev.brief_id,
        totalBudget: ev.total_budget,
        currency: ev.currency || "USD",
        maxCpm: ev.max_cpm ?? null,
        interests: ev.interests || [],
      };
      v = setStages(v, { identity: "active" });
      for (const c of ev.channels || []) v = ensureChannel(v, c.channel, null);
      return v;
    }
    case "identity.resolved": {
      const identity = ev.identity || {};
      const fields = Object.keys(identity).filter((k) => identity[k]);
      return setStages(
        { ...view, tier: ev.tier || "public", identityFields: fields },
        { identity: "done", audience: "active" }
      );
    }
    case "audience.planned":
      return setStages(
        { ...view, segments: ev.segments || [] },
        { audience: "done", budget: "active" }
      );
    case "budget.allocated": {
      let v: RunView = { ...view, allocations: ev.allocations || [] };
      for (const a of ev.allocations || []) v = ensureChannel(v, a.channel, a.budget);
      return setStages(v, { budget: "done", channels: "active" });
    }
    case "rail.routed":
      return { ...view, rail: { rail: ev.execution_rail, message: ev.message || "" } };
    case "channel.started":
      return withChannel(
        view,
        ev.channel,
        (c) => ({ ...c, statusLabel: "discovering seller…", spinning: true }),
        ev.budget ?? null
      );
    case "seller.discovered":
      return withChannel(view, ev.channel, (c) => ({
        ...c,
        seller: ev.seller_name || ev.seller_id || "—",
        statusLabel: "sourcing inventory…",
      }));
    case "channel.researching":
      return withChannel(view, ev.channel, (c) => ({
        ...c,
        statusLabel: "crew researching…",
        activeAgent: "lead",
      }));
    case "agent.step": {
      if (ev.channel === "PORTFOLIO") {
        // L1 portfolio rationale surfaces in the allocation panel, not a channel card.
        return ev.detail ? { ...view, portfolioNote: ev.detail } : view;
      }
      return withChannel(view, ev.channel, (c) => {
        let next = addStep(c, ev);
        const key = agentKeyOf(ev.channel, ev);
        if (key) next = { ...next, activeAgent: key };
        if (ev.phase) next = { ...next, phases: setPhase(next.phases, ev.phase) };
        if (ev.kind === "tool")
          next = { ...next, statusLabel: `${ev.agent || "analyst"} · ${ev.tool}` };
        return next;
      });
    }
    case "channel.recommended":
      return withChannel(view, ev.channel, (c) => ({
        ...c,
        phases: setPhase(phaseDone(c.phases, "price"), "recommend"),
        activeAgent: "lead",
        statusLabel: ev.product_id ? "pricing & gate…" : c.statusLabel,
        rec: ev.product_id
          ? {
              productId: ev.product_id,
              productName: ev.product_name,
              finalCpm: ev.final_cpm,
              audienceCodes: ev.audience_codes || [],
            }
          : null,
      }));
    case "channel.priced":
      return withChannel(view, ev.channel, (c) => ({
        ...c,
        phases: setPhase(phaseDone(c.phases, "recommend"), "gate"),
        priced: { finalCpm: ev.final_cpm, maxCpm: ev.max_cpm, card: ev.card },
      }));
    case "channel.decision":
      return withChannel(
        view,
        ev.channel,
        (c) => {
          let phases = phaseDone(c.phases, "gate");
          if (ev.decision === "BOOKED") phases = phaseDone(phases, "book");
          return {
            ...c,
            statusLabel: ev.decision,
            spinning: false,
            activeAgent: null,
            phases,
            decision: {
              decision: ev.decision,
              reason: ev.reason,
              finalCpm: ev.final_cpm,
              impressions: ev.impressions,
              orderId: ev.order_id,
              dealId: ev.deal_id,
              ssp: ev.ssp,
              executionRail: ev.execution_rail,
              creativeId: ev.creative_id,
              creativeStatus: ev.creative_status,
              productId: ev.product_id,
              productName: ev.product_name,
              sellerName: ev.seller_name,
              channelBudget: ev.channel_budget,
              card: ev.card,
              activationInstructions: ev.activation_instructions,
            },
          };
        },
        ev.channel_budget ?? null
      );
    case "run.completed":
      return setStages(
        {
          ...view,
          phase: "done",
          summary: {
            booked: ev.booked || 0,
            passed: ev.passed || 0,
            spend: ev.spend || 0,
            tier: ev.buyer_tier || "public",
          },
        },
        { channels: "done", done: "done" }
      );
    case "run.error":
      return { ...view, phase: "error", error: ev.message || "unknown error" };
    default:
      return view;
  }
}

export function reducer(view: RunView, action: Action): RunView {
  switch (action.type) {
    case "reset":
      return initialRun;
    case "starting":
      return { ...initialRun, phase: "starting", briefId: action.briefId };
    case "start-failed":
      return { ...initialRun, phase: "error", error: action.message };
    case "event":
      return handleEvent(view, action.ev);
  }
}
