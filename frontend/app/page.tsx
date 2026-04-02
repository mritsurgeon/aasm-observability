"use client";

import { useEffect, useState, useCallback } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { ClientTime } from "@/components/ClientTime";
import { StatCard } from "@/components/StatCard";
import { AgentGraph } from "@/components/AgentGraph";
import { SequenceTimeline } from "@/components/SequenceTimeline";
import { RiskHeatmap } from "@/components/RiskHeatmap";
import {
  api,
  ToolNamespace,
  ToolEntry,
  RiskSession,
  EventRecord,
  MemoryEntry,
  WsEvent,
} from "@/lib/api";

type Tab = "overview" | "graph" | "tools" | "timeline" | "heatmap" | "risk" | "memory";

const TYPE_COLORS: Record<string, string> = {
  tool_call: "text-yellow-400",
  llm_call:  "text-blue-400",
  memory:    "text-purple-400",
  api_call:  "text-green-400",
  network:   "text-orange-400",
  vector_db: "text-cyan-400",
};

// ── Empty state ─────────────────────────────────────────────────────────────
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-80 text-center px-8">
      <div className="w-16 h-16 rounded-full bg-gray-800 border-2 border-dashed border-gray-600 flex items-center justify-center mb-4">
        <svg className="w-7 h-7 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
      </div>
      <h2 className="text-lg font-semibold text-gray-300 mb-2">No data yet</h2>
      <p className="text-sm text-gray-500 max-w-md mb-6">
        Connect an agent to start observing activity. ARSP captures tool calls, LLM interactions,
        memory writes, and API calls automatically.
      </p>
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 text-left max-w-lg w-full">
        <p className="text-xs text-gray-400 font-semibold mb-2 uppercase tracking-wide">Quick start</p>
        <pre className="text-xs text-green-400 font-mono leading-relaxed">{`pip install ./sdk

import arsp_sdk as arsp
arsp.init(
  agent_id="my-agent",
  endpoint="http://localhost:8000"
)

# Your existing agent code — no changes needed`}</pre>
      </div>
    </div>
  );
}

// ── Event detail panel ───────────────────────────────────────────────────────
function EventDetail({ event, onClose }: { event: WsEvent | EventRecord; onClose: () => void }) {
  const data      = "data" in event ? event.data : event;
  const metadata  = (data.metadata ?? data) as Record<string, unknown>;
  const type      = event.type;
  const name      = (data.name ?? (event as EventRecord).name ?? "—") as string;
  const agentId   = (data.agent_id ?? (event as EventRecord).agent_id ?? "—") as string;
  const sessionId = (data.session_id ?? (event as EventRecord).session_id ?? "") as string;

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 text-xs">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={`font-mono font-semibold ${TYPE_COLORS[type] || "text-gray-400"}`}>{type}</span>
          <span className="text-gray-300 font-mono">{name}</span>
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-base leading-none">×</button>
      </div>
      <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[11px] mb-3">
        <span className="text-gray-500 font-mono">agent</span>
        <span className="text-gray-300 font-mono">{agentId}</span>
        {sessionId && (
          <>
            <span className="text-gray-500 font-mono">session</span>
            <span className="text-gray-400 font-mono truncate">{sessionId}</span>
          </>
        )}
        <span className="text-gray-500 font-mono">time</span>
        <span className="text-gray-400 font-mono">
          <ClientTime date={event.timestamp} />
        </span>
      </div>
      {Object.keys(metadata).length > 0 && (
        <pre className="text-[10px] text-gray-400 bg-gray-800 rounded p-2 overflow-auto max-h-48 font-mono">
          {JSON.stringify(metadata, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ── Tools tab ────────────────────────────────────────────────────────────────
function ToolDetail({ tool, events }: { tool: ToolEntry; events: EventRecord[] }) {
  const toolEvs   = events.filter((e) => e.name === tool.name);
  const successPct = tool.call_count > 0
    ? ((tool.call_count - tool.error_count) / tool.call_count) * 100
    : 100;

  const durSamples = toolEvs
    .map((e) => e.metadata.duration_ms as number)
    .filter((d) => typeof d === "number" && d > 0);
  const avgLatency = durSamples.length > 0
    ? durSamples.reduce((s, d) => s + d, 0) / durSamples.length
    : null;

  const barColor =
    successPct >= 90 ? "bg-green-500" : successPct >= 70 ? "bg-yellow-500" : "bg-red-500";
  const textColor =
    successPct >= 90 ? "text-green-400" : successPct >= 70 ? "text-yellow-400" : "text-red-400";

  return (
    <div className="px-4 py-3 bg-gray-800/40 border-t border-gray-700/50 space-y-3">
      {/* Success rate */}
      <div>
        <div className="flex justify-between text-[10px] mb-1">
          <span className="text-gray-500">Success rate</span>
          <span className={textColor}>{successPct.toFixed(1)}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-gray-700">
          <div className={`h-full rounded-full transition-all ${barColor}`}
            style={{ width: `${successPct}%` }} />
        </div>
      </div>

      {/* Latency */}
      {avgLatency !== null && (
        <div className="flex gap-4 text-[10px]">
          <span className="text-gray-500">Avg latency</span>
          <span className="text-gray-300 font-mono">
            {avgLatency < 1000 ? `${avgLatency.toFixed(0)} ms` : `${(avgLatency / 1000).toFixed(2)} s`}
          </span>
        </div>
      )}

      {/* Latest executions */}
      {toolEvs.length > 0 ? (
        <div>
          <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1.5">Latest executions</p>
          <div className="space-y-1.5">
            {toolEvs.slice(0, 3).map((ev) => {
              const err = ev.metadata.error;
              const inp = ev.metadata.input;
              const out = ev.metadata.output;
              const dur = ev.metadata.duration_ms as number | undefined;
              return (
                <div key={ev.id} className="bg-gray-900 border border-gray-700/50 rounded p-2 text-[10px]">
                  <div className="flex items-center gap-3 mb-1">
                    <span className={err ? "text-red-400" : "text-green-400"}>
                      {err ? "✗ failed" : "✓ success"}
                    </span>
                    {dur && <span className="text-gray-600 font-mono">{dur} ms</span>}
                    <span className="text-gray-700 ml-auto font-mono">
                      {new Date(ev.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  {inp && (
                    <div className="text-gray-500">
                      <span className="text-gray-600">in: </span>
                      <span className="text-gray-300 font-mono break-all">
                        {String(inp).slice(0, 120)}{String(inp).length > 120 ? "…" : ""}
                      </span>
                    </div>
                  )}
                  {out && (
                    <div className="text-gray-500 mt-0.5">
                      <span className="text-gray-600">out: </span>
                      <span className="text-gray-400 font-mono break-all">
                        {String(out).slice(0, 120)}{String(out).length > 120 ? "…" : ""}
                      </span>
                    </div>
                  )}
                  {err && (
                    <div className="text-red-400 mt-0.5 font-mono">
                      err: {String(err).slice(0, 100)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <p className="text-[10px] text-gray-600 italic">No recent execution records found.</p>
      )}
    </div>
  );
}

function ToolsTab() {
  const [namespaces, setNamespaces]         = useState<ToolNamespace[]>([]);
  const [registry, setRegistry]             = useState<Record<string, ToolEntry[]>>({});
  const [openNs, setOpenNs]                 = useState<string | null>(null);
  const [openTool, setOpenTool]             = useState<string | null>(null);
  const [loading, setLoading]               = useState(true);
  const [error, setError]                   = useState<string | null>(null);
  const [toolEvents, setToolEvents]         = useState<EventRecord[]>([]);
  const [toolEventsLoaded, setToolEventsLoaded] = useState(false);

  useEffect(() => {
    Promise.all([api.toolNamespaces(), api.toolRegistry()])
      .then(([ns, reg]) => {
        setNamespaces(ns);
        setRegistry(reg.namespaces);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Lazy-load tool events on first namespace open
  useEffect(() => {
    if (!openNs || toolEventsLoaded) return;
    api.queryEvents({ type: "tool_call", limit: 200 })
      .then((evs) => setToolEvents(evs))
      .catch(() => {})
      .finally(() => setToolEventsLoaded(true));
  }, [openNs, toolEventsLoaded]);

  if (loading) return <div className="text-gray-500 text-sm p-8">Loading tool registry…</div>;
  if (error)   return <div className="text-red-400 text-sm p-8">{error}</div>;
  if (namespaces.length === 0) return (
    <div className="space-y-4"><h1 className="text-lg font-semibold">Tool Registry</h1><EmptyState /></div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Tool Registry</h1>
        <span className="text-xs text-gray-500">
          {namespaces.reduce((s, n) => s + n.tool_count, 0)} tools · {namespaces.length} namespaces
        </span>
      </div>

      <div className="space-y-2">
        {namespaces.map((ns) => {
          const tools  = registry[ns.namespace] || [];
          const isOpen = openNs === ns.namespace;
          return (
            <div key={ns.namespace} className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden">
              {/* Namespace header */}
              <button
                onClick={() => setOpenNs(isOpen ? null : ns.namespace)}
                className="w-full px-4 py-3 flex items-center gap-4 text-left hover:bg-gray-800/50 transition-colors"
              >
                <span className="text-purple-400 font-mono font-semibold w-32 truncate">{ns.namespace}</span>
                <span className="text-xs text-gray-500">{ns.tool_count} tools</span>
                <span className="text-xs text-yellow-400">{ns.total_calls.toLocaleString()} calls</span>
                {ns.total_errors > 0 && (
                  <span className="text-xs text-red-400">
                    {ns.total_errors} errors ({(ns.error_rate * 100).toFixed(1)}%)
                  </span>
                )}
                <span className="ml-auto text-gray-600 text-xs">{isOpen ? "▲" : "▼"}</span>
              </button>

              {isOpen && (
                <div className="border-t border-gray-700 divide-y divide-gray-800">
                  {tools.map((t) => {
                    const toolKey  = `${ns.namespace}::${t.name}`;
                    const isToolOpen = openTool === toolKey;
                    const successPct = t.call_count > 0
                      ? ((t.call_count - t.error_count) / t.call_count) * 100
                      : 100;
                    const badgeColor =
                      successPct >= 90 ? "bg-green-900 text-green-400 border-green-800"
                      : successPct >= 70 ? "bg-yellow-900 text-yellow-400 border-yellow-800"
                      : "bg-red-900 text-red-400 border-red-800";

                    return (
                      <div key={t.name}>
                        <button
                          onClick={() => setOpenTool(isToolOpen ? null : toolKey)}
                          className={`w-full px-4 py-2.5 flex items-center gap-4 text-xs text-left transition-colors ${
                            isToolOpen ? "bg-gray-800/60" : "hover:bg-gray-800/30"
                          }`}
                        >
                          <span className="font-mono text-yellow-300 flex-1 truncate">{t.name}</span>
                          {/* Success badge */}
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono ${badgeColor}`}>
                            {successPct.toFixed(0)}% ok
                          </span>
                          <span className="text-gray-500">{t.call_count} calls</span>
                          {t.error_count > 0 && (
                            <span className="text-red-400">{t.error_count} err</span>
                          )}
                          <span className="text-gray-600 hidden md:block">
                            {t.agents.slice(0, 2).join(", ")}
                            {t.agents.length > 2 ? ` +${t.agents.length - 2}` : ""}
                          </span>
                          {t.last_seen && (
                            <span className="text-gray-700 hidden lg:block font-mono">
                              {new Date(t.last_seen).toLocaleTimeString()}
                            </span>
                          )}
                          <span className="text-gray-600 flex-shrink-0">{isToolOpen ? "▲" : "▼"}</span>
                        </button>

                        {isToolOpen && (
                          <ToolDetail tool={t} events={toolEvents} />
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Risk tab ─────────────────────────────────────────────────────────────────
const RISK_FACTOR_META: Array<{
  match: RegExp;
  icon: string;
  color: string;
  weight: number;
}> = [
  { match: /external contact/i,  icon: "🌐", color: "text-orange-400", weight: 0.36 },
  { match: /error rate/i,        icon: "⚠️",  color: "text-red-400",    weight: 0.30 },
  { match: /high event volume/i, icon: "📊", color: "text-yellow-400", weight: 0.15 },
  { match: /namespace/i,         icon: "🔄", color: "text-cyan-400",   weight: 0.10 },
  { match: /llm/i,               icon: "🤖", color: "text-blue-400",   weight: 0.08 },
  { match: /memory/i,            icon: "💾", color: "text-purple-400", weight: 0.05 },
  { match: /off.hours/i,         icon: "🌙", color: "text-indigo-400", weight: 0.05 },
];

function parseFactor(text: string) {
  return RISK_FACTOR_META.find((m) => m.match.test(text))
    ?? { icon: "•", color: "text-gray-400", weight: 0 };
}

function RiskScoreBar({ score }: { score: number }) {
  const pct   = Math.min(score * 100, 100);
  const color = score >= 0.75 ? "#ef4444" : score >= 0.45 ? "#eab308" : "#22c55e";
  return (
    <div className="w-full h-1.5 rounded-full bg-gray-700 overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

function RiskTab() {
  const [data, setData]         = useState<{ sessions: RiskSession[]; summary: Record<string, number> } | null>(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.riskSessions(20)
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const insightBg = (i: string) =>
    i === "critical_risk" ? "bg-red-950 border-red-800"
    : i === "elevated_risk" ? "bg-yellow-950 border-yellow-800"
    : "bg-gray-900 border-gray-700";

  const insightLabel = (i: string) =>
    i === "critical_risk"
      ? <span className="px-2 py-0.5 rounded border text-[10px] font-mono uppercase tracking-wide bg-red-950 border-red-700 text-red-400">Critical</span>
      : i === "elevated_risk"
      ? <span className="px-2 py-0.5 rounded border text-[10px] font-mono uppercase tracking-wide bg-yellow-950 border-yellow-700 text-yellow-400">Elevated</span>
      : <span className="px-2 py-0.5 rounded border text-[10px] font-mono uppercase tracking-wide bg-green-950 border-green-800 text-green-400">Normal</span>;

  if (loading) return (
    <div className="text-gray-500 text-sm p-8 flex gap-2">
      <span className="animate-spin text-blue-400">⟳</span> Loading risk data…
    </div>
  );

  if (error) return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Risk Intelligence</h1>
      <div className="text-red-400 text-sm p-4 bg-red-950/20 border border-red-900 rounded-lg">
        {error}
        <button onClick={load} className="ml-3 text-blue-400 hover:underline text-xs">Retry</button>
      </div>
    </div>
  );

  if (!data || data.sessions.length === 0) return (
    <div className="space-y-4"><h1 className="text-lg font-semibold">Risk Intelligence</h1><EmptyState /></div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Risk Intelligence</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{data.summary.total} sessions analysed</span>
          <button onClick={load} className="text-xs text-blue-400 hover:underline">Refresh</button>
        </div>
      </div>

      {/* Summary pills */}
      <div className="flex gap-3 text-xs">
        {data.summary.critical > 0 && (
          <span className="px-3 py-1 rounded-full bg-red-950 border border-red-800 text-red-400 font-semibold">
            🔴 {data.summary.critical} critical
          </span>
        )}
        {data.summary.elevated > 0 && (
          <span className="px-3 py-1 rounded-full bg-yellow-950 border border-yellow-800 text-yellow-400 font-semibold">
            🟡 {data.summary.elevated} elevated
          </span>
        )}
        <span className="px-3 py-1 rounded-full bg-gray-800 border border-gray-700 text-green-400 font-semibold">
          🟢 {data.summary.normal} normal
        </span>
      </div>

      {/* Session list */}
      <div className="space-y-2">
        {data.sessions.map((s) => {
          const isExpanded = expanded === s.session_id;
          return (
            <div key={s.session_id} className={`border rounded-lg text-xs ${insightBg(s.insight)}`}>
              <button
                className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-white/5 transition-colors rounded-lg"
                onClick={() => setExpanded(isExpanded ? null : s.session_id)}
              >
                {/* Score */}
                <span className="font-bold text-sm w-12 flex-shrink-0 font-mono text-white">
                  {(s.risk_score * 100).toFixed(0)}%
                </span>
                {insightLabel(s.insight)}
                <span className="text-gray-300 font-mono">{s.agent_id}</span>
                <span className="text-gray-600 font-mono ml-auto hidden sm:block">{s.session_id.slice(0, 16)}…</span>
                <span className="text-gray-500 flex-shrink-0">{s.event_count} evts</span>
                <span className="text-gray-600 ml-2">{isExpanded ? "▲" : "▼"}</span>
              </button>

              {isExpanded && (
                <div className="px-4 pb-4 border-t border-white/10 pt-3 space-y-3">
                  {/* Score bar */}
                  <div>
                    <div className="flex justify-between text-[10px] text-gray-500 mb-1">
                      <span>Risk score</span>
                      <span className="font-mono">{(s.risk_score * 100).toFixed(1)}% · confidence {(s.confidence * 100).toFixed(0)}%</span>
                    </div>
                    <RiskScoreBar score={s.risk_score} />
                  </div>

                  {/* Factor badges — each reasoning item gets an icon + color */}
                  {s.reasoning.length > 0 && (
                    <div>
                      <p className="text-[10px] text-gray-600 uppercase tracking-wide mb-1.5">Triggered factors</p>
                      <div className="flex flex-wrap gap-2">
                        {s.reasoning.map((r, i) => {
                          const f = parseFactor(r);
                          return (
                            <span
                              key={i}
                              className={`inline-flex items-center gap-1 px-2 py-1 rounded-full border border-white/10 bg-white/5 font-mono text-[10px] ${f.color}`}
                            >
                              <span>{f.icon}</span>
                              <span>{r}</span>
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Stats breakdown */}
                  {s.stats && (
                    <div>
                      <p className="text-[10px] text-gray-600 uppercase tracking-wide mb-1.5">Event breakdown</p>
                      <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
                        {Object.entries(s.stats).map(([k, v]) => {
                          if (typeof v !== "number" || v === 0) return null;
                          const displayKey = k.replace(/_/g, " ");
                          const isHighRisk = (k === "error_rate" && (v as number) > 0.2)
                            || (k === "api_calls" && (v as number) > 2);
                          return (
                            <div
                              key={k}
                              className={`rounded px-2 py-1.5 text-center border ${
                                isHighRisk
                                  ? "bg-red-950/50 border-red-800/50"
                                  : "bg-gray-800 border-gray-700"
                              }`}
                            >
                              <div className={`text-sm font-bold font-mono ${isHighRisk ? "text-red-400" : "text-gray-200"}`}>
                                {k === "error_rate" ? `${((v as number) * 100).toFixed(1)}%` : v}
                              </div>
                              <div className="text-[9px] text-gray-500 mt-0.5">{displayKey}</div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  <div className="text-[10px] text-gray-600 font-mono">
                    started: {new Date(s.start_time).toLocaleString()}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Memory tab ───────────────────────────────────────────────────────────────
interface MemoryRow {
  id:        string;
  timestamp: string;
  agent_id:  string;
  key:       string;
  payload:   string;
  source:    "chain" | "event";
  active?:   boolean;
}

function MemoryTab() {
  const [rows, setRows]         = useState<MemoryRow[]>([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState<string | null>(null);
  const [selected, setSelected] = useState<MemoryRow | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.memoryChain().catch(() => ({ head_id: null, count: 0, chain: [] })),
      api.queryEvents({ type: "memory", limit: 100 }).catch(() => [] as EventRecord[]),
    ])
      .then(([chain, pgEvents]) => {
        const chainRows: MemoryRow[] = chain.chain.map((e: MemoryEntry) => ({
          id:        e.id,
          timestamp: e.timestamp,
          agent_id:  e.agent_id,
          key:       "memory-chain",
          payload:   e.content,
          source:    "chain" as const,
          active:    e.active,
        }));
        const eventRows: MemoryRow[] = (pgEvents as EventRecord[]).map((e) => ({
          id:        e.id,
          timestamp: e.timestamp,
          agent_id:  e.agent_id,
          key:       e.name,
          payload:   Object.keys(e.metadata).length > 0
            ? JSON.stringify(e.metadata)
            : "(no payload)",
          source:    "event" as const,
        }));

        // Deduplicate by id, sort newest first
        const seen = new Set<string>();
        const merged: MemoryRow[] = [];
        [...chainRows, ...eventRows]
          .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
          .forEach((r) => { if (!seen.has(r.id)) { seen.add(r.id); merged.push(r); } });
        setRows(merged);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return (
    <div className="text-gray-500 text-sm p-8 flex gap-2">
      <span className="animate-spin text-blue-400">⟳</span> Loading memory writes…
    </div>
  );

  if (error) return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Memory Writes</h1>
      <div className="text-red-400 text-sm p-4 bg-red-950/20 border border-red-900 rounded-lg">
        {error}
        <button onClick={load} className="ml-3 text-blue-400 hover:underline text-xs">Retry</button>
      </div>
    </div>
  );

  if (rows.length === 0) return (
    <div className="space-y-4"><h1 className="text-lg font-semibold">Memory Writes</h1><EmptyState /></div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Memory Writes</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{rows.length} entries</span>
          <button onClick={load} className="text-xs text-blue-400 hover:underline">Refresh</button>
        </div>
      </div>

      <div className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden">
        {/* Table header */}
        <div className="grid grid-cols-[8rem_8rem_6rem_1fr_5rem] gap-2 px-4 py-2 border-b border-gray-700 text-[10px] text-gray-500 uppercase tracking-wide font-semibold">
          <span>Timestamp</span>
          <span>Agent ID</span>
          <span>Key / NS</span>
          <span>Payload / Value</span>
          <span className="text-right">Source</span>
        </div>

        {/* Rows */}
        <div className="divide-y divide-gray-800 max-h-[600px] overflow-y-auto">
          {rows.map((row) => {
            const isSelected = selected?.id === row.id;
            return (
              <div key={row.id}>
                <button
                  onClick={() => setSelected(isSelected ? null : row)}
                  className={`w-full grid grid-cols-[8rem_8rem_6rem_1fr_5rem] gap-2 px-4 py-2.5 text-xs text-left transition-colors ${
                    isSelected ? "bg-gray-800" : "hover:bg-gray-800/50"
                  }`}
                >
                  <span className="text-gray-600 font-mono truncate">
                    {new Date(row.timestamp).toLocaleTimeString()}
                  </span>
                  <span className="text-blue-400 font-mono truncate">{row.agent_id.slice(0, 14)}</span>
                  <span className="text-purple-400 font-mono truncate">{row.key}</span>
                  <span className="text-gray-400 font-mono truncate">
                    {row.payload.slice(0, 80)}{row.payload.length > 80 ? "…" : ""}
                  </span>
                  <div className="flex justify-end items-center gap-1">
                    {row.source === "chain" && (
                      <span className={`px-1.5 py-0.5 rounded border text-[9px] font-mono ${
                        row.active === false
                          ? "bg-gray-800 border-gray-700 text-gray-600"
                          : "bg-purple-950 border-purple-800 text-purple-400"
                      }`}>
                        {row.active === false ? "inactive" : "chain"}
                      </span>
                    )}
                    {row.source === "event" && (
                      <span className="px-1.5 py-0.5 rounded border text-[9px] font-mono bg-blue-950 border-blue-800 text-blue-400">
                        sdk
                      </span>
                    )}
                  </div>
                </button>

                {/* Expanded payload */}
                {isSelected && (
                  <div className="px-4 pb-3 bg-gray-800/40 border-t border-gray-700/50">
                    <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[11px] mb-2">
                      <span className="text-gray-500 font-mono">id</span>
                      <span className="text-gray-600 font-mono truncate">{row.id}</span>
                      <span className="text-gray-500 font-mono">agent</span>
                      <span className="text-gray-300 font-mono">{row.agent_id}</span>
                      <span className="text-gray-500 font-mono">time</span>
                      <span className="text-gray-400 font-mono">{new Date(row.timestamp).toLocaleString()}</span>
                    </div>
                    <pre className="text-[10px] text-gray-400 bg-gray-900 rounded p-2 overflow-auto max-h-40 font-mono whitespace-pre-wrap break-all">
                      {row.payload}
                    </pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}


// ── Dashboard ────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const [tab, setTab] = useState<Tab>("overview");
  const { events: wsEvents, connected } = useWebSocket();
  const [seedEvents, setSeedEvents]     = useState<WsEvent[]>([]);
  const [seedLoaded, setSeedLoaded]     = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<WsEvent | null>(null);

  // Seed overview with REST data so it shows even before new WS events arrive
  useEffect(() => {
    api.recentEvents()
      .then((d) => { if (d.events?.length) setSeedEvents(d.events); })
      .catch(() => {})
      .finally(() => setSeedLoaded(true));
  }, []);

  // Prefer live WS events; fall back to REST seed while WS catches up
  const events  = wsEvents.length > 0 ? wsEvents : seedEvents;
  const hasData = events.length > 0;
  const overviewLoading = !seedLoaded && wsEvents.length === 0;

  const tabs: { id: Tab; label: string }[] = [
    { id: "overview",  label: "Overview" },
    { id: "graph",     label: "Graph" },
    { id: "tools",     label: "Tools" },
    { id: "timeline",  label: "Timeline" },
    { id: "heatmap",   label: "Heatmap" },
    { id: "risk",      label: "Risk" },
    { id: "memory",    label: "Memory" },
  ];

  // Derived metrics from combined events
  const toolCallEvents = events.filter((e) => e.type === "tool_call");
  const llmCallEvents  = events.filter((e) => e.type === "llm_call");
  const errorEvents    = events.filter((e) => (e.data?.metadata as Record<string, unknown>)?.error);
  const agentIds       = new Set(events.map((e) => e.data?.agent_id).filter(Boolean));
  const sessionIds     = new Set(events.map((e) => e.data?.session_id).filter(Boolean));

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Top bar */}
      <header className="relative border-b border-gray-800 bg-gray-900 px-6 py-3 flex items-center gap-4">
        <div className="absolute left-1/2 -translate-x-1/2 items-center flex hidden md:flex">
          <h1 className="text-2xl font-black text-white tracking-wider uppercase drop-shadow-sm">AI Command Center</h1>
        </div>

        <div className="flex items-center gap-2">
          <div className="w-6 h-6 bg-blue-600 rounded flex items-center justify-center">
            <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </div>
          <span className="font-bold text-white">ARSP</span>
          <span className="text-gray-500 text-sm hidden sm:block">Agent Observability Platform</span>
        </div>

        <div className="ml-auto flex items-center gap-3 text-xs">
          <div className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
            <span className="text-gray-500">{connected ? "live" : "reconnecting…"}</span>
          </div>
          {hasData && (
            <>
              <span className="text-gray-600">|</span>
              <span className="text-gray-500">{events.length} events</span>
              {errorEvents.length > 0 && (
                <span className="text-red-400">{errorEvents.length} errors</span>
              )}
            </>
          )}
        </div>
      </header>

      {/* Tab bar */}
      <nav className="border-b border-gray-800 bg-gray-900 px-4 flex gap-0.5">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => { setTab(t.id); setSelectedEvent(null); }}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 whitespace-nowrap transition-colors ${
              tab === t.id
                ? "border-blue-500 text-blue-400"
                : "border-transparent text-gray-500 hover:text-gray-300"
            }`}>
            {t.label}
          </button>
        ))}
      </nav>

      {/* Content */}
      <main className="flex-1 p-6 max-w-screen-2xl mx-auto w-full">

        {/* OVERVIEW */}
        {tab === "overview" && (
          <div className="space-y-6">
            {overviewLoading ? (
              <div className="flex items-center justify-center h-40 gap-2 text-gray-500 text-sm">
                <span className="animate-spin text-blue-400">⟳</span> Loading event data…
              </div>
            ) : hasData ? (
              <>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <StatCard label="Active Agents"  value={agentIds.size}         accent="blue" />
                  <StatCard label="Sessions"        value={sessionIds.size}       accent="blue" />
                  <StatCard label="Tool Calls"      value={toolCallEvents.length} accent="yellow" />
                  <StatCard label="LLM Calls"       value={llmCallEvents.length}  accent="green" />
                </div>

                {/* Live event stream */}
                <div className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden">
                  <div className="px-4 py-2 border-b border-gray-700 flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-gray-500"}`} />
                    <span className="text-sm font-medium text-gray-300">Live Event Stream</span>
                    <span className="text-xs text-gray-600 ml-2">click any row for details</span>
                    <span className="ml-auto text-xs text-gray-500">{events.length} total</span>
                  </div>
                  <div className="divide-y divide-gray-800 max-h-96 overflow-y-auto">
                    {[...events].reverse().slice(0, 100).map((ev, i) => {
                      const hasError = !!(ev.data?.metadata as Record<string, unknown>)?.error;
                      const isSelected = selectedEvent === ev;
                      return (
                        <button
                          key={i}
                          onClick={() => setSelectedEvent(isSelected ? null : ev)}
                          className={`w-full px-4 py-2 flex items-center gap-3 text-xs text-left transition-colors ${
                            isSelected ? "bg-gray-700/60" : "hover:bg-gray-800/60"
                          }`}
                        >
                          <span className={`font-mono font-semibold w-20 flex-shrink-0 ${TYPE_COLORS[ev.type] || "text-gray-400"}`}>
                            {ev.type}
                          </span>
                          <span className="text-gray-300 font-mono flex-1 truncate">
                            {(ev.data?.name as string) || (ev.data?.agent_id as string) || "—"}
                          </span>
                          {hasError && <span className="text-red-500 text-[10px] flex-shrink-0">ERR</span>}
                          <span className="text-gray-600 flex-shrink-0 hidden sm:block">
                            {(ev.data?.agent_id as string)?.slice(0, 14)}
                          </span>
                          <span className="text-gray-700 flex-shrink-0 font-mono">
                            <ClientTime date={ev.timestamp} />
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Event detail panel */}
                {selectedEvent && (
                  <EventDetail event={selectedEvent} onClose={() => setSelectedEvent(null)} />
                )}
              </>
            ) : (
              <EmptyState />
            )}
          </div>
        )}

        {/* GRAPH */}
        {tab === "graph" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h1 className="text-lg font-semibold">Agent Graph</h1>
              <span className="text-xs text-gray-500">{agentIds.size} agents observed</span>
            </div>
            <div className="h-[640px] border border-gray-700 rounded-lg overflow-hidden bg-gray-900">
              <AgentGraph eventCount={events.length} />
            </div>
          </div>
        )}

        {/* TOOLS */}
        {tab === "tools" && <ToolsTab />}

        {/* TIMELINE */}
        {tab === "timeline" && (
          <div className="space-y-4">
            <h1 className="text-lg font-semibold">Sequence Timeline</h1>
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 min-h-64">
              <SequenceTimeline eventCount={events.length} />
            </div>
          </div>
        )}

        {/* HEATMAP */}
        {tab === "heatmap" && (
          <div className="space-y-4">
            <h1 className="text-lg font-semibold">Activity Heatmap</h1>
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 min-h-64">
              <RiskHeatmap eventCount={events.length} />
            </div>
          </div>
        )}

        {/* RISK */}
        {tab === "risk" && <RiskTab />}

        {/* MEMORY */}
        {tab === "memory" && <MemoryTab />}

      </main>
    </div>
  );
}
