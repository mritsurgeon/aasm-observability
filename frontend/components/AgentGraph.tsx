"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactFlow, {
  Node, Edge, Background, Controls, MiniMap,
  useNodesState, useEdgesState, MarkerType, Handle, Position,
} from "reactflow";
import "reactflow/dist/style.css";
import { api, GraphNode, GraphEdge } from "@/lib/api";

// ── Colours per node label ─────────────────────────────────────────────────────
const LABEL_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  Agent:          { border: "#3b82f6", bg: "#1e3a5f", text: "#93c5fd" },
  Session:        { border: "#6b7280", bg: "#1f2937", text: "#d1d5db" },
  Tool:           { border: "#eab308", bg: "#422006", text: "#fde047" },
  LLMModel:       { border: "#22c55e", bg: "#052e16", text: "#86efac" },
  ExternalSystem: { border: "#f97316", bg: "#1c0a00", text: "#fdba74" },
  Namespace:      { border: "#8b5cf6", bg: "#2e1065", text: "#c4b5fd" },
  Memory:         { border: "#d946ef", bg: "#4a044e", text: "#f5d0fe" },
  VectorDB:       { border: "#06b6d4", bg: "#164e63", text: "#cffafe" },
};

function fallback() { return { border: "#6b7280", bg: "#1f2937", text: "#d1d5db" }; }

const ICONS: Record<string, string> = {
  Agent: "◎", Session: "⊙", Tool: "⚙", LLMModel: "✦",
  ExternalSystem: "⊕", Namespace: "⊞", Memory: "◈", VectorDB: "⬡",
};

// ── Key metadata to show inline per node type ──────────────────────────────────
function getKeyMeta(label: string, data: Record<string, unknown>): string {
  switch (label) {
    case "LLMModel": {
      const parts: string[] = [];
      if (data.provider) parts.push(String(data.provider));
      const pt = Number(data.last_prompt_tokens  ?? 0);
      const ct = Number(data.last_completion_tokens ?? 0);
      if (pt + ct > 0) parts.push(`${(pt + ct).toLocaleString()} tok`);
      return parts.join(" · ");
    }
    case "Tool": {
      const parts: string[] = [];
      if (data.framework) parts.push(String(data.framework));
      else if (data.namespace && data.namespace !== data.name)
        parts.push(String(data.namespace));
      return parts.join(" · ");
    }
    case "VectorDB": {
      const parts: string[] = [];
      if (data.db_type)   parts.push(String(data.db_type));
      if (data.collection) parts.push(String(data.collection).slice(0, 16));
      return parts.join(" · ");
    }
    case "ExternalSystem": {
      if (data.last_method && data.last_status)
        return `${data.last_method} ${data.last_status}`;
      return String(data.host ?? "").slice(0, 24);
    }
    case "Session":
      return String(data.agent_id ?? "").slice(0, 18);
    default:
      return "";
  }
}

// Number of extra detail fields available (shown as "+N" badge)
function extraCount(label: string, data: Record<string, unknown>): number {
  const SKIP = new Set(["label", "display"]);
  const ALWAYS: Record<string, string[]> = {
    LLMModel:       ["name", "provider", "last_prompt_tokens", "last_completion_tokens", "framework", "last_seen"],
    Tool:           ["name", "namespace", "description", "framework", "last_input", "last_seen"],
    VectorDB:       ["name", "db_type", "collection", "last_op", "last_seen"],
    ExternalSystem: ["host", "last_method", "last_status", "last_seen"],
  };
  const known = new Set(ALWAYS[label] ?? []);
  return Object.keys(data).filter((k) => !SKIP.has(k) && !known.has(k) && data[k] != null && data[k] !== "").length;
}

// ── Custom node component ──────────────────────────────────────────────────────
function GraphNodeComponent({ data }: { data: Record<string, unknown> }) {
  const label   = data.label as string;
  const display = data.display as string;
  const colors  = LABEL_COLORS[label] || fallback();
  const keyMeta = getKeyMeta(label, data);
  const extras  = extraCount(label, data);

  return (
    <div
      style={{ border: `2px solid ${colors.border}`, background: colors.bg, borderRadius: 8 }}
      className="px-3 py-2 text-xs min-w-[120px] max-w-[190px] cursor-pointer select-none"
    >
      <Handle type="target" position={Position.Top}    style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />

      {/* Header row: icon + label + extras badge */}
      <div className="flex items-center gap-1.5 mb-0.5">
        <span style={{ color: colors.border }} className="text-sm leading-none flex-shrink-0">
          {ICONS[label] || "○"}
        </span>
        <span style={{ color: colors.text }} className="font-semibold text-[10px] uppercase tracking-wide">
          {label}
        </span>
        {extras > 0 && (
          <span
            className="ml-auto text-[9px] font-mono px-1 rounded"
            style={{ background: colors.border + "33", color: colors.border }}
          >
            +{extras}
          </span>
        )}
      </div>

      {/* Display name */}
      <div className="text-gray-200 font-mono truncate text-[11px]" title={display}>
        {display.length > 24 ? display.slice(0, 22) + "…" : display}
      </div>

      {/* Key metadata preview */}
      {keyMeta && (
        <div
          className="text-[9px] font-mono mt-1 truncate"
          style={{ color: colors.border + "cc" }}
          title={keyMeta}
        >
          {keyMeta}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { graphNode: GraphNodeComponent };

// ── Layout ─────────────────────────────────────────────────────────────────────
function computeLayout(apiNodes: GraphNode[], apiEdges: GraphEdge[]): { nodes: Node[]; edges: Edge[] } {
  const posMap = new Map<string, { x: number; y: number }>();

  const agents = apiNodes.filter((n) => n.label === "Agent");

  agents.forEach((agent, i) => {
    const cx = i * 1500;
    posMap.set(agent.id, { x: cx, y: 0 });

    const runEdges = apiEdges.filter((e) => e.source === agent.id && e.type === "RUNS");
    const sessions = apiNodes.filter((n) => runEdges.some((re) => re.target === n.id));

    sessions.forEach((session, j) => {
      const sAngle  = (j / Math.max(sessions.length, 1)) * 2 * Math.PI;
      const sRadius = sessions.length === 1 ? 0 : 350;
      const sx = cx + Math.cos(sAngle) * sRadius;
      const sy = Math.sin(sAngle) * sRadius + (sessions.length === 1 ? 250 : 0);
      posMap.set(session.id, { x: sx, y: sy });

      const callEdges = apiEdges.filter((e) => e.source === session.id);
      const children  = apiNodes.filter((n) => callEdges.some((ce) => ce.target === n.id));

      children.forEach((child, k) => {
        const cAngle  = sAngle + ((k - (children.length - 1) / 2) * 0.4);
        const cRadius = 280;
        const cx2     = sx + Math.cos(cAngle) * cRadius;
        const cy2     = sy + Math.sin(cAngle) * cRadius;
        posMap.set(child.id, { x: cx2, y: cy2 });

        if (child.label === "Tool") {
          const nsEdges = apiEdges.filter((e) => e.source === child.id && e.type === "IN_NAMESPACE");
          const nss     = apiNodes.filter((n) => nsEdges.some((ne) => ne.target === n.id));
          nss.forEach((ns) => posMap.set(ns.id, { x: cx2, y: cy2 + 160 }));
        }
      });
    });
  });

  const orphans = apiNodes.filter((n) => !posMap.has(n.id));
  orphans.forEach((node, i) =>
    posMap.set(node.id, { x: (i % 5) * 200, y: Math.floor(i / 5) * 150 - 500 })
  );

  const nodes: Node[] = apiNodes.map((n) => ({
    id:       n.id,
    type:     "graphNode",
    position: posMap.get(n.id) ?? { x: 0, y: 0 },
    data:     { ...n.data, label: n.label, display: n.display },
  }));

  const REL_COLORS: Record<string, string> = {
    RUNS:         "#3b82f6",
    CALLS:        "#eab308",
    CONNECTS_TO:  "#f97316",
    IN_NAMESPACE: "#8b5cf6",
    FOLLOWS:      "#374151",
    WRITES:       "#d946ef",
    QUERIES:      "#06b6d4",
  };

  const edges: Edge[] = apiEdges.map((e) => ({
    id:         e.id,
    source:     e.source,
    target:     e.target,
    label:      e.type,
    labelStyle: { fill: "#6b7280", fontSize: 9 },
    style:      {
      stroke:          REL_COLORS[e.type] || "#6b7280",
      strokeWidth:     1.5,
      strokeDasharray: e.type === "FOLLOWS" ? "4 4" : "none",
    },
    markerEnd: { type: MarkerType.ArrowClosed, color: REL_COLORS[e.type] || "#6b7280" },
  }));

  return { nodes, edges };
}

// ── Detail panel ───────────────────────────────────────────────────────────────
const SKIP_KEYS = new Set(["label", "display"]);

const FIELD_LABELS: Record<string, string> = {
  agent_id:                  "agent",
  session_id:                "session",
  last_seen:                 "last seen",
  first_seen:                "first seen",
  name:                      "name",
  namespace:                 "namespace",
  provider:                  "provider",
  framework:                 "framework",
  last_prompt_tokens:        "prompt tokens",
  last_completion_tokens:    "completion tokens",
  description:               "description",
  last_input:                "last input",
  db_type:                   "db type",
  collection:                "collection",
  last_op:                   "last operation",
  host:                      "host",
  last_method:               "method",
  last_status:               "status",
};

function formatVal(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") {
    if (/^\d{4}-\d{2}-\d{2}T/.test(v)) {
      try { return new Date(v).toLocaleString(); } catch { return v; }
    }
    return v;
  }
  if (typeof v === "number") return v.toLocaleString();
  return JSON.stringify(v);
}

// Sections to render per label type
interface FieldDef { key: string; highlight?: boolean }
interface SectionDef { title?: string; fields: FieldDef[] }

function getNodeSections(label: string, data: Record<string, unknown>): {
  badge?: string;
  sections: SectionDef[];
} {
  switch (label) {
    case "LLMModel":
      return {
        badge: String(data.name || ""),
        sections: [
          {
            title: "Model",
            fields: [
              { key: "name",     highlight: true },
              { key: "provider" },
              { key: "framework" },
            ],
          },
          {
            title: "Token usage",
            fields: [
              { key: "last_prompt_tokens" },
              { key: "last_completion_tokens" },
            ],
          },
          { fields: [{ key: "last_seen" }] },
        ],
      };

    case "Tool":
      return {
        badge: String(data.namespace || ""),
        sections: [
          {
            title: "Tool info",
            fields: [
              { key: "name",        highlight: true },
              { key: "namespace" },
              { key: "framework" },
              { key: "description" },
            ],
          },
          {
            title: "Last execution",
            fields: [
              { key: "last_input" },
              { key: "last_seen" },
            ],
          },
        ],
      };

    case "VectorDB":
      return {
        badge: String(data.db_type || ""),
        sections: [
          {
            title: "Database",
            fields: [
              { key: "name",       highlight: true },
              { key: "db_type" },
              { key: "collection" },
            ],
          },
          {
            title: "Last query",
            fields: [
              { key: "last_op" },
              { key: "last_seen" },
            ],
          },
        ],
      };

    case "ExternalSystem":
      return {
        badge: String(data.last_status || ""),
        sections: [
          {
            fields: [
              { key: "host",        highlight: true },
              { key: "last_method" },
              { key: "last_status" },
              { key: "last_seen" },
            ],
          },
        ],
      };

    default: {
      const allKeys = Object.keys(data)
        .filter((k) => !SKIP_KEYS.has(k) && data[k] != null && data[k] !== "");
      return { sections: [{ fields: allKeys.map((k) => ({ key: k })) }] };
    }
  }
}

function DetailPanel({ node, onClose }: { node: Node | null; onClose: () => void }) {
  if (!node) return null;
  const data   = node.data as Record<string, unknown>;
  const label  = data.label as string;
  const colors = LABEL_COLORS[label] || fallback();
  const { badge, sections } = getNodeSections(label, data);

  return (
    <div className="absolute top-3 right-3 z-10 w-80 bg-gray-900 border border-gray-700 rounded-lg shadow-2xl text-xs overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b border-gray-700"
        style={{ borderLeftColor: colors.border, borderLeftWidth: 3 }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span style={{ color: colors.border }} className="text-sm flex-shrink-0">
            {ICONS[label] ?? "○"}
          </span>
          <span style={{ color: colors.text }} className="font-semibold uppercase tracking-wide flex-shrink-0">
            {label}
          </span>
          {badge && (
            <span
              className="px-1.5 py-0.5 rounded font-mono text-[10px] truncate"
              style={{ background: colors.border + "22", color: colors.text, border: `1px solid ${colors.border}55` }}
            >
              {badge}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-base leading-none flex-shrink-0 ml-2">
          ×
        </button>
      </div>

      {/* Sections */}
      <div className="p-3 space-y-3 max-h-[440px] overflow-y-auto">
        {sections.map((sec, si) => {
          const renderable = sec.fields.filter(({ key }) => {
            const v = data[key];
            return v != null && v !== "";
          });
          if (!renderable.length) return null;

          return (
            <div key={si}>
              {sec.title && (
                <p className="text-[9px] uppercase tracking-widest text-gray-600 mb-1.5 font-semibold">
                  {sec.title}
                </p>
              )}
              <div className="space-y-1.5">
                {renderable.map(({ key, highlight }) => {
                  const v = data[key];
                  return (
                    <div key={key} className="flex gap-2 min-w-0">
                      <span className="text-gray-500 font-mono w-32 flex-shrink-0 text-[11px]">
                        {FIELD_LABELS[key] ?? key}
                      </span>
                      <span
                        className={`font-mono break-all min-w-0 text-[11px] ${
                          highlight ? "text-gray-100 font-semibold" : "text-gray-300"
                        }`}
                        title={formatVal(v)}
                      >
                        {formatVal(v)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Component ──────────────────────────────────────────────────────────────────
interface Props {
  /** Pass latest WS event count so graph refreshes when new data arrives */
  eventCount: number;
}

export function AgentGraph({ eventCount }: Props) {
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode]      = useState<Node | null>(null);
  const [loading, setLoading]                = useState(true);
  const [error, setError]                    = useState<string | null>(null);
  const [counts, setCounts]                  = useState({ nodes: 0, edges: 0 });
  const lastCountRef                          = useRef(-1);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await api.graphOverview();
      if (data.error) { setError(data.error); return; }
      const { nodes, edges } = computeLayout(data.nodes, data.edges);
      setRfNodes(nodes);
      setRfEdges(edges);
      setCounts(data.counts);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [setRfNodes, setRfEdges]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (eventCount === lastCountRef.current) return;
    lastCountRef.current = eventCount;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
  }, [eventCount, load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm gap-2">
        <span className="animate-spin text-blue-400">⟳</span> Loading graph…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-sm">
        <span className="text-red-400">{error}</span>
        <button onClick={load} className="text-blue-400 hover:underline text-xs">Retry</button>
      </div>
    );
  }

  if (rfNodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-500 text-sm gap-2">
        <span className="text-2xl">◎</span>
        <p>No agents observed yet.</p>
        <p className="text-xs text-gray-600">Connect an agent with the SDK to see the live graph.</p>
      </div>
    );
  }

  return (
    <div className="relative w-full h-full">
      {/* Node type counts */}
      <div className="absolute top-3 left-3 z-10 flex flex-wrap gap-2 text-xs max-w-[60%]">
        {(["Agent","Session","Tool","LLMModel","VectorDB","ExternalSystem","Memory","Namespace"] as const).map((label) => {
          const c     = LABEL_COLORS[label];
          const count = rfNodes.filter((n) => (n.data as Record<string, unknown>).label === label).length;
          if (!count) return null;
          return (
            <span
              key={label}
              style={{ borderColor: c.border, color: c.text }}
              className="px-2 py-0.5 rounded border bg-gray-900/80 font-mono"
            >
              {ICONS[label]} {count} {label}
            </span>
          );
        })}
      </div>

      <ReactFlow
        nodes={rfNodes} edges={rfEdges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeClick={(_, node) => setSelectedNode(node)}
        onPaneClick={() => setSelectedNode(null)}
        nodeTypes={nodeTypes}
        fitView fitViewOptions={{ padding: 0.15 }}
        minZoom={0.15} maxZoom={3}
      >
        <Background color="#374151" gap={20} />
        <Controls />
        <MiniMap
          nodeColor={(n) => LABEL_COLORS[(n.data as Record<string, unknown>).label as string]?.border ?? "#6b7280"}
          style={{ background: "#111827", border: "1px solid #374151" }}
        />
      </ReactFlow>

      <DetailPanel node={selectedNode} onClose={() => setSelectedNode(null)} />

      <div className="absolute bottom-3 left-3 z-10 text-[10px] text-gray-600 font-mono">
        {counts.nodes} nodes · {counts.edges} edges
      </div>
    </div>
  );
}
