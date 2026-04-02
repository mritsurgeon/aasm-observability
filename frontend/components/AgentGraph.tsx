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

const REL_COLORS: Record<string, string> = {
  RUNS:         "#3b82f6",
  CALLS:        "#eab308",
  CONNECTS_TO:  "#f97316",
  IN_NAMESPACE: "#8b5cf6",
  FOLLOWS:      "#374151",
  WRITES:       "#d946ef",
  QUERIES:      "#06b6d4",
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
      if (data.db_type)    parts.push(String(data.db_type));
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

// Number of extra detail fields (shown as "+N" badge)
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
  const label       = data.label as string;
  const display     = data.display as string;
  const colors      = LABEL_COLORS[label] || fallback();
  const keyMeta     = getKeyMeta(label, data);
  const extras      = extraCount(label, data);
  const isExpanded  = !!data.isExpanded;
  const isLoading   = !!data.isLoading;
  const isDrillable = label === "Agent" || label === "Session";

  return (
    <div
      style={{
        border: `2px solid ${isExpanded ? colors.border : colors.border + "99"}`,
        background: colors.bg,
        borderRadius: 8,
        boxShadow: isExpanded ? `0 0 14px ${colors.border}44` : "none",
      }}
      className="px-3 py-2 text-xs min-w-[120px] max-w-[190px] cursor-pointer select-none"
    >
      <Handle type="target" position={Position.Top}    style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0 }} />

      <div className="flex items-center gap-1.5 mb-0.5">
        <span
          style={{ color: colors.border }}
          className={`text-sm leading-none flex-shrink-0 ${isLoading ? "animate-spin" : ""}`}
        >
          {isLoading ? "⟳" : ICONS[label] || "○"}
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

      <div className="text-gray-200 font-mono truncate text-[11px]" title={display}>
        {display.length > 24 ? display.slice(0, 22) + "…" : display}
      </div>

      {keyMeta && (
        <div
          className="text-[9px] font-mono mt-1 truncate"
          style={{ color: colors.border + "cc" }}
          title={keyMeta}
        >
          {keyMeta}
        </div>
      )}

      {isDrillable && (
        <div
          className="mt-1.5 text-[9px] font-mono text-center leading-none"
          style={{ color: colors.border + "77" }}
        >
          {isLoading ? "fetching…" : isExpanded ? "⊖ collapse" : "⊕ expand"}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { graphNode: GraphNodeComponent };

// ── Layout helpers ─────────────────────────────────────────────────────────────

function toRfEdge(e: GraphEdge): Edge {
  return {
    id:         e.id,
    source:     e.source,
    target:     e.target,
    label:      e.type,
    labelStyle: { fill: "#6b7280", fontSize: 9 },
    style: {
      stroke:          REL_COLORS[e.type] || "#6b7280",
      strokeWidth:     1.5,
      strokeDasharray: e.type === "FOLLOWS" ? "4 4" : "none",
    },
    markerEnd: { type: MarkerType.ArrowClosed, color: REL_COLORS[e.type] || "#6b7280" },
  };
}

/**
 * Spread N children evenly on a downward semicircle around a parent position.
 * All children appear below the parent, preventing upward layout explosions.
 */
function spreadAround(
  center: { x: number; y: number },
  count: number,
  radius = 300,
): Array<{ x: number; y: number }> {
  if (count === 0) return [];
  if (count === 1) return [{ x: center.x, y: center.y + radius }];
  return Array.from({ length: count }, (_, i) => {
    const angle = (Math.PI / (count + 1)) * (i + 1); // 0 → π left-to-right
    return {
      x: center.x + Math.cos(Math.PI - angle) * radius,
      y: center.y + Math.sin(angle) * radius,
    };
  });
}

// ── Orphan detection ───────────────────────────────────────────────────────────

/**
 * BFS from collapsingId: find all descendants that would become orphaned
 * (every path to them passes through nodes already being removed).
 * collapsingId itself is NOT included in the result.
 */
function findOrphanedDescendants(collapsingId: string, allEdges: Edge[]): Set<string> {
  const toRemove = new Set<string>();
  const queue    = [collapsingId];

  while (queue.length > 0) {
    const parentId = queue.shift()!;
    for (const edge of allEdges.filter((e) => e.source === parentId)) {
      const childId = edge.target;
      if (toRemove.has(childId)) continue;
      const survivingParents = allEdges.filter(
        (e) => e.target === childId && e.source !== parentId && !toRemove.has(e.source),
      );
      if (survivingParents.length === 0) {
        toRemove.add(childId);
        queue.push(childId);
      }
    }
  }
  return toRemove;
}

// ── Detail panel ───────────────────────────────────────────────────────────────
const SKIP_KEYS = new Set(["label", "display"]);

const FIELD_LABELS: Record<string, string> = {
  agent_id:               "agent",
  session_id:             "session",
  last_seen:              "last seen",
  first_seen:             "first seen",
  name:                   "name",
  namespace:              "namespace",
  provider:               "provider",
  framework:              "framework",
  last_prompt_tokens:     "prompt tokens",
  last_completion_tokens: "completion tokens",
  description:            "description",
  last_input:             "last input",
  db_type:                "db type",
  collection:             "collection",
  last_op:                "last operation",
  host:                   "host",
  last_method:            "method",
  last_status:            "status",
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

interface FieldDef   { key: string; highlight?: boolean }
interface SectionDef { title?: string; fields: FieldDef[] }

function getNodeSections(label: string, data: Record<string, unknown>): {
  badge?: string; sections: SectionDef[];
} {
  switch (label) {
    case "LLMModel":
      return {
        badge: String(data.name || ""),
        sections: [
          { title: "Model",       fields: [{ key: "name", highlight: true }, { key: "provider" }, { key: "framework" }] },
          { title: "Token usage", fields: [{ key: "last_prompt_tokens" }, { key: "last_completion_tokens" }] },
          { fields: [{ key: "last_seen" }] },
        ],
      };
    case "Tool":
      return {
        badge: String(data.namespace || ""),
        sections: [
          { title: "Tool info",      fields: [{ key: "name", highlight: true }, { key: "namespace" }, { key: "framework" }, { key: "description" }] },
          { title: "Last execution", fields: [{ key: "last_input" }, { key: "last_seen" }] },
        ],
      };
    case "VectorDB":
      return {
        badge: String(data.db_type || ""),
        sections: [
          { title: "Database",   fields: [{ key: "name", highlight: true }, { key: "db_type" }, { key: "collection" }] },
          { title: "Last query", fields: [{ key: "last_op" }, { key: "last_seen" }] },
        ],
      };
    case "ExternalSystem":
      return {
        badge: String(data.last_status || ""),
        sections: [{ fields: [{ key: "host", highlight: true }, { key: "last_method" }, { key: "last_status" }, { key: "last_seen" }] }],
      };
    default: {
      const allKeys = Object.keys(data).filter((k) => !SKIP_KEYS.has(k) && data[k] != null && data[k] !== "");
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
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b border-gray-700"
        style={{ borderLeftColor: colors.border, borderLeftWidth: 3 }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span style={{ color: colors.border }} className="text-sm flex-shrink-0">{ICONS[label] ?? "○"}</span>
          <span style={{ color: colors.text }} className="font-semibold uppercase tracking-wide flex-shrink-0">{label}</span>
          {badge && (
            <span
              className="px-1.5 py-0.5 rounded font-mono text-[10px] truncate"
              style={{ background: colors.border + "22", color: colors.text, border: `1px solid ${colors.border}55` }}
            >
              {badge}
            </span>
          )}
        </div>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-base leading-none flex-shrink-0 ml-2">×</button>
      </div>

      <div className="p-3 space-y-3 max-h-[440px] overflow-y-auto">
        {sections.map((sec, si) => {
          const renderable = sec.fields.filter(({ key }) => { const v = data[key]; return v != null && v !== ""; });
          if (!renderable.length) return null;
          return (
            <div key={si}>
              {sec.title && (
                <p className="text-[9px] uppercase tracking-widest text-gray-600 mb-1.5 font-semibold">{sec.title}</p>
              )}
              <div className="space-y-1.5">
                {renderable.map(({ key, highlight }) => (
                  <div key={key} className="flex gap-2 min-w-0">
                    <span className="text-gray-500 font-mono w-32 flex-shrink-0 text-[11px]">
                      {FIELD_LABELS[key] ?? key}
                    </span>
                    <span
                      className={`font-mono break-all min-w-0 text-[11px] ${highlight ? "text-gray-100 font-semibold" : "text-gray-300"}`}
                      title={formatVal(data[key])}
                    >
                      {formatVal(data[key])}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Visible graph computation ──────────────────────────────────────────────────
function computeVisibleGraph(
  allNodes:      Node[],
  allEdges:      Edge[],
  activeFilters: Set<string>,
  activePath:    Set<string>,
  expandedNodes: Set<string>,
  loadingNodes:  Set<string>,
): { visibleNodes: Node[]; visibleEdges: Edge[] } {
  let visibleIds: Set<string>;
  if (activeFilters.size > 0) {
    const primary = new Set(
      allNodes
        .filter((n) => activeFilters.has((n.data as Record<string, unknown>).label as string))
        .map((n) => n.id),
    );
    const neighbors = new Set<string>();
    allEdges.forEach((e) => {
      if (primary.has(e.source)) neighbors.add(e.target);
      if (primary.has(e.target)) neighbors.add(e.source);
    });
    visibleIds = new Set([...primary, ...neighbors]);
  } else {
    visibleIds = new Set(allNodes.map((n) => n.id));
  }

  const visibleNodes = allNodes
    .filter((n) => visibleIds.has(n.id))
    .map((n) => {
      const dimmed = activePath.size > 0 && !activePath.has(n.id);
      return {
        ...n,
        data: { ...n.data, isExpanded: expandedNodes.has(n.id), isLoading: loadingNodes.has(n.id) },
        style: { ...(n.style ?? {}), opacity: dimmed ? 0.18 : 1, transition: "opacity 0.3s ease-out" },
      };
    });

  const visibleEdges = allEdges
    .filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target))
    .map((e) => {
      const dimmed = activePath.size > 0 && (!activePath.has(e.source) || !activePath.has(e.target));
      return {
        ...e,
        style: { ...(e.style ?? {}), opacity: dimmed ? 0.07 : 1, transition: "opacity 0.3s ease-out" },
      };
    });

  return { visibleNodes, visibleEdges };
}

// ── Component ──────────────────────────────────────────────────────────────────
interface Props {
  /** Pass latest WS event count so graph refreshes when new agents arrive */
  eventCount: number;
}

export function AgentGraph({ eventCount }: Props) {
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([]);
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([]);
  const [selectedNode, setSelectedNode]      = useState<Node | null>(null);
  const [loading, setLoading]                = useState(true);
  const [error, setError]                    = useState<string | null>(null);
  const [activeFilters, setActiveFilters]    = useState<Set<string>>(new Set());
  const [activePath, setActivePath]          = useState<Set<string>>(new Set());
  const [expandedNodes, setExpandedNodes]    = useState<Set<string>>(new Set());
  const [loadingNodes, setLoadingNodes]      = useState<Set<string>>(new Set());
  const [dataVersion, setDataVersion]        = useState(0);

  const lastCountRef = useRef(-1);
  const allNodesRef  = useRef<Node[]>([]);
  const allEdgesRef  = useRef<Edge[]>([]);

  // ── Sync internal refs → ReactFlow state ──────────────────────────────────
  const syncVisible = useCallback(() => {
    const { visibleNodes, visibleEdges } = computeVisibleGraph(
      allNodesRef.current, allEdgesRef.current,
      activeFilters, activePath, expandedNodes, loadingNodes,
    );
    setRfNodes(visibleNodes);
    setRfEdges(visibleEdges);
  }, [activeFilters, activePath, expandedNodes, loadingNodes, setRfNodes, setRfEdges]);

  useEffect(() => {
    if (allNodesRef.current.length > 0) syncVisible();
  }, [dataVersion, syncVisible]);

  // ── Level 0: load only :Agent nodes ───────────────────────────────────────
  const loadAgents = useCallback(async () => {
    try {
      setError(null);
      const data = await api.graphAgents();
      if (data.error) { setError(data.error); return; }

      const existingIds = new Set(allNodesRef.current.map((n) => n.id));
      const agentCount  = allNodesRef.current.filter(
        (n) => (n.data as Record<string, unknown>).label === "Agent",
      ).length;

      const newNodes = data.nodes
        .filter((n) => !existingIds.has(n.id))
        .map((n, i): Node => ({
          id:       n.id,
          type:     "graphNode",
          position: { x: (agentCount + i) * 320, y: 0 },
          data:     { ...n.data, label: n.label, display: n.display },
        }));

      if (newNodes.length > 0 || allNodesRef.current.length === 0) {
        allNodesRef.current = [...allNodesRef.current, ...newNodes];
        setDataVersion((v) => v + 1);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAgents(); }, [loadAgents]);

  // Debounced refresh on new WS events — merges new agents, leaves expanded subgraph intact
  useEffect(() => {
    if (eventCount === lastCountRef.current) return;
    lastCountRef.current = eventCount;
    const t = setTimeout(loadAgents, 1500);
    return () => clearTimeout(t);
  }, [eventCount, loadAgents]);

  // ── Node click: drill-down or collapse ────────────────────────────────────
  const handleNodeClick = useCallback(
    async (_evt: React.MouseEvent, clickedNode: Node) => {
      const data   = clickedNode.data as Record<string, unknown>;
      const label  = data.label as string;
      const nodeId = clickedNode.id;

      setSelectedNode(clickedNode);

      // ── AGENT ──────────────────────────────────────────────────────────────
      if (label === "Agent") {
        if (expandedNodes.has(nodeId)) {
          const orphans = findOrphanedDescendants(nodeId, allEdgesRef.current);
          allNodesRef.current = allNodesRef.current.filter((n) => !orphans.has(n.id));
          allEdgesRef.current = allEdgesRef.current.filter(
            (e) => !orphans.has(e.source) && !orphans.has(e.target),
          );
          setExpandedNodes((prev) => {
            const next = new Set(prev); next.delete(nodeId);
            orphans.forEach((id) => next.delete(id)); return next;
          });
          setActivePath(new Set());
          setDataVersion((v) => v + 1);
          return;
        }

        const agentId = data.agent_id as string | undefined;
        if (!agentId) return;

        setLoadingNodes((prev) => new Set([...prev, nodeId]));
        try {
          const result    = await api.graphAgentSessions(agentId);
          const parentPos = allNodesRef.current.find((n) => n.id === nodeId)?.position
            ?? clickedNode.position;

          const newSessions = result.nodes.filter((n) => n.label === "Session");
          const spreadPos   = spreadAround(parentPos, newSessions.length, 300);
          const existingIds = new Set(allNodesRef.current.map((n) => n.id));

          // Spawn new nodes at parent position — CSS transition animates the settle
          const newRfNodes: Node[] = newSessions
            .filter((n) => !existingIds.has(n.id))
            .map((n): Node => ({
              id: n.id, type: "graphNode",
              position: { ...parentPos },
              style: { transition: "transform 0.45s ease-out" },
              data: { ...n.data, label: n.label, display: n.display },
            }));

          const existingEdgeIds = new Set(allEdgesRef.current.map((e) => e.id));
          const newRfEdges = result.edges.filter((e) => !existingEdgeIds.has(e.id)).map(toRfEdge);

          allNodesRef.current = [...allNodesRef.current, ...newRfNodes];
          allEdgesRef.current = [...allEdgesRef.current, ...newRfEdges];
          setExpandedNodes((prev) => new Set([...prev, nodeId]));
          setDataVersion((v) => v + 1);

          // Re-heat: settle nodes to spread positions after one paint frame
          const spreadMap = new Map(
            newSessions.filter((n) => !existingIds.has(n.id)).map((n, i) => [n.id, spreadPos[i]]),
          );
          setTimeout(() => {
            allNodesRef.current = allNodesRef.current.map((n) =>
              spreadMap.has(n.id) ? { ...n, position: spreadMap.get(n.id)! } : n,
            );
            setDataVersion((v) => v + 1);
          }, 60);
        } catch (e) {
          console.error("Failed to fetch sessions:", e);
        } finally {
          setLoadingNodes((prev) => { const next = new Set(prev); next.delete(nodeId); return next; });
        }

      // ── SESSION ────────────────────────────────────────────────────────────
      } else if (label === "Session") {
        if (expandedNodes.has(nodeId)) {
          const orphans = findOrphanedDescendants(nodeId, allEdgesRef.current);
          allNodesRef.current = allNodesRef.current.filter((n) => !orphans.has(n.id));
          allEdgesRef.current = allEdgesRef.current.filter(
            (e) => !orphans.has(e.source) && !orphans.has(e.target),
          );
          setExpandedNodes((prev) => {
            const next = new Set(prev); next.delete(nodeId);
            orphans.forEach((id) => next.delete(id)); return next;
          });
          setActivePath(new Set());
          setDataVersion((v) => v + 1);
          return;
        }

        const sessionId = data.session_id as string | undefined;
        if (!sessionId) return;

        setLoadingNodes((prev) => new Set([...prev, nodeId]));
        try {
          const result    = await api.graphSessionResources(sessionId);
          const parentPos = allNodesRef.current.find((n) => n.id === nodeId)?.position
            ?? clickedNode.position;

          const resources   = result.nodes.filter((n) => n.label !== "Session");
          const spreadPos   = spreadAround(parentPos, resources.length, 280);
          const existingIds = new Set(allNodesRef.current.map((n) => n.id));

          const newRfNodes: Node[] = resources
            .filter((n) => !existingIds.has(n.id))
            .map((n): Node => ({
              id: n.id, type: "graphNode",
              position: { ...parentPos },
              style: { transition: "transform 0.45s ease-out" },
              data: { ...n.data, label: n.label, display: n.display },
            }));

          const existingEdgeIds = new Set(allEdgesRef.current.map((e) => e.id));
          const newRfEdges = result.edges.filter((e) => !existingEdgeIds.has(e.id)).map(toRfEdge);

          allNodesRef.current = [...allNodesRef.current, ...newRfNodes];
          allEdgesRef.current = [...allEdgesRef.current, ...newRfEdges];
          setExpandedNodes((prev) => new Set([...prev, nodeId]));

          // Trace path: Agent → Session → [all resource children]
          const agentEdge = allEdgesRef.current.find((e) => e.target === nodeId);
          const childIds  = allEdgesRef.current.filter((e) => e.source === nodeId).map((e) => e.target);
          const tracePath = new Set<string>([nodeId, ...childIds]);
          if (agentEdge) tracePath.add(agentEdge.source);
          setActivePath(tracePath);

          setDataVersion((v) => v + 1);

          // Re-heat: settle resource nodes to spread positions
          const spreadMap = new Map(
            resources.filter((n) => !existingIds.has(n.id)).map((n, i) => [n.id, spreadPos[i]]),
          );
          setTimeout(() => {
            allNodesRef.current = allNodesRef.current.map((n) =>
              spreadMap.has(n.id) ? { ...n, position: spreadMap.get(n.id)! } : n,
            );
            setDataVersion((v) => v + 1);
          }, 60);
        } catch (e) {
          console.error("Failed to fetch resources:", e);
        } finally {
          setLoadingNodes((prev) => { const next = new Set(prev); next.delete(nodeId); return next; });
        }

      // ── LEAF NODE: update trace path context ──────────────────────────────
      } else {
        const parentEdge = allEdgesRef.current.find((e) => e.target === nodeId);
        if (parentEdge) {
          const parentLabel = (
            allNodesRef.current.find((n) => n.id === parentEdge.source)
              ?.data as Record<string, unknown> | undefined
          )?.label as string | undefined;

          if (parentLabel === "Session") {
            const sessionId  = parentEdge.source;
            const agentEdge  = allEdgesRef.current.find((e) => e.target === sessionId);
            const siblingIds = allEdgesRef.current.filter((e) => e.source === sessionId).map((e) => e.target);
            const tracePath  = new Set<string>([sessionId, nodeId, ...siblingIds]);
            if (agentEdge) tracePath.add(agentEdge.source);
            setActivePath(tracePath);
          }
        }
      }
    },
    [expandedNodes],
  );

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
    setActivePath(new Set());
  }, []);

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm gap-2">
        <span className="animate-spin text-blue-400">⟳</span> Loading agents…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-sm">
        <span className="text-red-400">{error}</span>
        <button onClick={loadAgents} className="text-blue-400 hover:underline text-xs">Retry</button>
      </div>
    );
  }

  if (allNodesRef.current.length === 0) {
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
      {/* Node type filter buttons */}
      <div className="absolute top-3 left-3 z-10 flex flex-wrap gap-2 text-xs max-w-[60%]">
        {(["Agent","Session","Tool","LLMModel","VectorDB","ExternalSystem","Memory","Namespace"] as const).map((label) => {
          const c       = LABEL_COLORS[label];
          const count   = rfNodes.filter((n) => (n.data as Record<string, unknown>).label === label).length;
          if (!count && activeFilters.size === 0) return null;
          const isActive = activeFilters.has(label);
          return (
            <button
              key={label}
              title={`${isActive ? "Remove" : "Filter to"} ${label} nodes`}
              style={{
                borderColor: c.border,
                color: isActive ? c.text : "#9ca3af",
                opacity: activeFilters.size > 0 && !isActive ? 0.45 : 1,
              }}
              className="px-2 py-0.5 rounded border bg-gray-900/80 font-mono cursor-pointer transition-all hover:opacity-100 select-none"
              onClick={() => {
                setActiveFilters((prev) => {
                  const next = new Set(prev);
                  if (next.has(label)) next.delete(label); else next.add(label);
                  return next;
                });
              }}
            >
              {ICONS[label]} {count} {label}
            </button>
          );
        })}
        {(activeFilters.size > 0 || activePath.size > 0) && (
          <button
            className="px-2 py-0.5 rounded border border-gray-600 text-gray-500 bg-gray-900/80 font-mono hover:border-gray-400 hover:text-gray-300 transition-colors"
            onClick={() => { setActiveFilters(new Set()); setActivePath(new Set()); }}
          >
            ✕ reset
          </button>
        )}
      </div>

      <ReactFlow
        nodes={rfNodes} edges={rfEdges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        nodeTypes={nodeTypes}
        fitView fitViewOptions={{ padding: 0.2 }}
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

      <div className="absolute bottom-3 left-3 z-10 text-[10px] text-gray-600 font-mono space-x-3">
        <span>{allNodesRef.current.length} nodes · {allEdgesRef.current.length} edges loaded</span>
        <span className="text-gray-700">· click agent/session to expand · click again to collapse</span>
      </div>
    </div>
  );
}
