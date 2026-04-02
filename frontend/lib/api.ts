const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json();
}

export const api = {
  health:        () => get<{ status: string; version: string }>("/health"),
  recentEvents:  () => get<{ count: number; events: WsEvent[] }>("/events/recent"),
  toolRegistry:  () => get<ToolRegistry>("/tools/registry"),
  toolNamespaces:() => get<ToolNamespace[]>("/tools/namespaces"),
  timeline:      (agentId?: string, limit = 20) =>
    get<TimelineData>(`/timeline${agentId ? `?agent_id=${encodeURIComponent(agentId)}&limit=${limit}` : `?limit=${limit}`}`),
  sessionTrace:  (sessionId: string) => get<TimelineSession>(`/timeline/${encodeURIComponent(sessionId)}`),
  graphOverview: (limit = 200) => get<GraphData>(`/graph/overview?limit=${limit}`),
  graphAgents:          () => get<GraphData>("/graph/agents"),
  graphAgentSessions:   (agentId: string) => get<GraphData>(`/graph/agent/${encodeURIComponent(agentId)}/sessions`),
  graphSessionResources:(sessionId: string) => get<GraphData>(`/graph/session/${encodeURIComponent(sessionId)}/resources`),
  graphAgent:    (agentId: string) => get<GraphData>(`/graph/agent/${encodeURIComponent(agentId)}`),
  graphSchema:   () => get<GraphSchema>("/graph/schema"),
  heatmap:       (buckets = 12, bucketMinutes = 5) =>
    get<HeatmapData>(`/heatmap?buckets=${buckets}&bucket_minutes=${bucketMinutes}`),
  riskSessions:  (limit = 20) => get<RiskSessionsData>(`/risk/sessions?limit=${limit}`),
  riskAgents:    () => get<RiskAgentsData>("/risk/agents"),
  memoryChain:   () => get<MemoryChainData>("/memory"),
  queryEvents:   (params: { type?: string; agent_id?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.type)     q.set("type",     params.type);
    if (params.agent_id) q.set("agent_id", params.agent_id);
    if (params.limit)    q.set("limit",    String(params.limit));
    return get<EventRecord[]>(`/events?${q.toString()}`);
  },
};

// ── Graph types ───────────────────────────────────────────────────────────────
// ── Timeline types ────────────────────────────────────────────────────────────
export interface TimelineEvent {
  id:        string;
  type:      string;
  name:      string;
  timestamp: string;
  parent_id: string | null;
  metadata:  Record<string, unknown>;
}

export interface TimelineSession {
  session_id:  string;
  agent_id:    string;
  event_count: number;
  start:       string;
  end:         string;
  duration_ms: number;
  events:      TimelineEvent[];
}

export interface TimelineData {
  sessions:     TimelineSession[];
  total_events: number;
}

export interface GraphNode {
  id:      string;
  label:   "Agent" | "Session" | "Tool" | "LLMModel" | "ExternalSystem" | "Namespace" | "Memory" | "VectorDB";
  display: string;
  data:    Record<string, unknown>;
}

export interface GraphEdge {
  id:     string;
  source: string;
  target: string;
  type:   string;
}

export interface GraphData {
  nodes:  GraphNode[];
  edges:  GraphEdge[];
  counts: { nodes: number; edges: number };
  error?: string;
}

export interface GraphSchema {
  node_labels: Record<string, number>;
  rel_types:   Record<string, number>;
  error?: string;
}

export interface ToolRegistry {
  total_tools:      number;
  total_namespaces: number;
  namespaces:       Record<string, ToolEntry[]>;
}

export interface ToolEntry {
  name:        string;
  namespace:   string;
  call_count:  number;
  error_count: number;
  error_rate:  number;
  agents:      string[];
  first_seen:  string | null;
  last_seen:   string | null;
}

// ── Heatmap types ─────────────────────────────────────────────────────────────
export interface HeatmapCell {
  count:       number;
  error_count: number;
  risk_score:  number;
}

export interface HeatmapRow {
  type:  string;
  cells: HeatmapCell[];
}

export interface HeatmapData {
  rows:           HeatmapRow[];
  bucket_labels:  string[];
  bucket_minutes: number;
  buckets:        number;
  window_start:   string;
  total_events:   number;
  max_risk:       number;
}

// ── Risk types ────────────────────────────────────────────────────────────────
export interface RiskResult {
  risk_score:  number;
  insight:     "normal" | "elevated_risk" | "critical_risk";
  confidence:  number;
  reasoning:   string[];
  stats:       Record<string, unknown>;
  thresholds:  { alert: number; critical: number };
}

export interface RiskSession extends RiskResult {
  session_id:  string;
  agent_id:    string;
  start_time:  string;
  event_count: number;
}

export interface RiskSessionsData {
  sessions: RiskSession[];
  summary:  { total: number; critical: number; elevated: number; normal: number };
}

export interface RiskAgent extends RiskResult {
  agent_id:      string;
  session_count: number;
  total_events:  number;
}

export interface RiskAgentsData {
  agents: RiskAgent[];
}

export interface MemoryEntry {
  id:        string;
  content:   string;
  hash:      string;
  parent_id: string | null;
  agent_id:  string;
  timestamp: string;
  active:    boolean;
}

export interface MemoryChainData {
  head_id: string | null;
  count:   number;
  chain:   MemoryEntry[];
}

export interface ToolNamespace {
  namespace:    string;
  tool_count:   number;
  total_calls:  number;
  total_errors: number;
  error_rate:   number;
  first_seen:   string | null;
  last_seen:    string | null;
  top_tools:    string[];
}

// ── REST event record (from GET /events) ──────────────────────────────────────
export interface EventRecord {
  id:          string;
  agent_id:    string;
  session_id:  string;
  type:        string;
  name:        string;
  timestamp:   string;
  metadata:    Record<string, unknown>;
  parent_id:   string | null;
  ingested_at: string;
}

// ── Shared event type (matches WebSocket + REST) ───────────────────────────────
export interface WsEvent {
  type: "tool_call" | "llm_call" | "memory" | "api_call" | "network" | string;
  timestamp: string;
  data: Record<string, unknown>;
}
