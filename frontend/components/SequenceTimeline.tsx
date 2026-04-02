"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { api, TimelineSession, TimelineEvent } from "@/lib/api";

// ── Colours ───────────────────────────────────────────────────────────────────
const TYPE_COLOR: Record<string, string> = {
  tool_call: "#eab308",
  llm_call:  "#3b82f6",
  memory:    "#a855f7",
  vector_db: "#06b6d4",
  api_call:  "#22c55e",
  network:   "#f97316",
};
function typeColor(t: string) { return TYPE_COLOR[t] ?? "#6b7280"; }

// ── Cluster logic ─────────────────────────────────────────────────────────────
// Events that render within CLUSTER_PX of each other are grouped into a cluster
// to prevent visual overlap at any zoom level.
const CLUSTER_PX = 20;

interface Cluster {
  id:     string;
  x:      number;           // centre pixel
  events: TimelineEvent[];
}

function buildClusters(
  events: TimelineEvent[],
  xScale: d3.ScaleLinear<number, number>,
): Cluster[] {
  if (!events.length) return [];

  const withX = events
    .map((e) => ({ e, x: xScale(+new Date(e.timestamp)) }))
    .sort((a, b) => a.x - b.x);

  const result: Cluster[] = [];
  let bucket = [withX[0]];

  for (let i = 1; i < withX.length; i++) {
    // Compare against last item in current bucket
    if (withX[i].x - bucket[bucket.length - 1].x < CLUSTER_PX) {
      bucket.push(withX[i]);
    } else {
      result.push({
        id:     bucket[0].e.id,
        x:      bucket.reduce((s, b) => s + b.x, 0) / bucket.length,
        events: bucket.map((b) => b.e),
      });
      bucket = [withX[i]];
    }
  }
  result.push({
    id:     bucket[0].e.id,
    x:      bucket.reduce((s, b) => s + b.x, 0) / bucket.length,
    events: bucket.map((b) => b.e),
  });

  return result;
}

// Dominant colour in a cluster (most common type)
function clusterColor(events: TimelineEvent[]) {
  const freq: Record<string, number> = {};
  events.forEach((e) => { freq[e.type] = (freq[e.type] ?? 0) + 1; });
  const dominant = Object.entries(freq).sort((a, b) => b[1] - a[1])[0]?.[0];
  return typeColor(dominant ?? "");
}

// ── Component ─────────────────────────────────────────────────────────────────
interface Props { eventCount: number; }

export function SequenceTimeline({ eventCount }: Props) {
  const svgRef                    = useRef<SVGSVGElement>(null);
  const [sessions, setSessions]   = useState<TimelineSession[]>([]);
  const [selected, setSelected]   = useState<TimelineEvent | null>(null);
  const [selCluster, setSelCluster] = useState<TimelineEvent[] | null>(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const lastCountRef              = useRef(-1);

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await api.timeline(undefined, 15);
      setSessions(data.sessions);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Refresh on new WS events (1.5 s debounce)
  useEffect(() => {
    if (eventCount === lastCountRef.current) return;
    lastCountRef.current = eventCount;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
  }, [eventCount, load]);

  // ── D3 render ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current || sessions.length === 0) return;

    const container = svgRef.current.parentElement!;
    const W      = Math.max(container.clientWidth || 900, 600);
    const ROW_H  = 90;          // taller rows for breathing room
    const PAD_L  = 200;
    const PAD_R  = 32;
    const PAD_T  = 44;
    const PAD_B  = 48;
    const H      = PAD_T + sessions.length * ROW_H + PAD_B;

    const allTs = sessions.flatMap((s) =>
      s.events.map((e) => new Date(e.timestamp).getTime())
    );
    const tMin = d3.min(allTs) ?? 0;
    const tMax = d3.max(allTs) ?? tMin + 1;
    // Add 2% padding on each side of the time axis so dots aren't clipped
    const tSpan = tMax - tMin || 1;

    const xScale = d3.scaleLinear()
      .domain([tMin - tSpan * 0.02, tMax + tSpan * 0.02])
      .range([PAD_L, W - PAD_R]);

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", W).attr("height", H);
    svg.append("rect").attr("width", W).attr("height", H).attr("fill", "#030712");

    // X-axis
    const xAxis = d3.axisBottom(xScale)
      .ticks(Math.max(4, Math.floor((W - PAD_L - PAD_R) / 120)))
      .tickFormat((d) => {
        const dt = new Date(d as number);
        return [dt.getHours(), dt.getMinutes(), dt.getSeconds()]
          .map((n) => String(n).padStart(2, "0"))
          .join(":");
      });

    svg.append("g")
      .attr("transform", `translate(0,${H - PAD_B})`)
      .call(xAxis)
      .call((g) => {
        g.select(".domain").attr("stroke", "#374151");
        g.selectAll(".tick line").attr("stroke", "#374151");
        g.selectAll(".tick text").attr("fill", "#6b7280").attr("font-size", "10px");
      });

    // Rows
    sessions.forEach((sess, rowIdx) => {
      const cy = PAD_T + rowIdx * ROW_H + ROW_H / 2;

      // Row background
      svg.append("rect")
        .attr("x", 0).attr("y", PAD_T + rowIdx * ROW_H)
        .attr("width", W).attr("height", ROW_H)
        .attr("fill", rowIdx % 2 === 0 ? "#0a0f1a" : "#060b14");

      // Row label — two lines: agent (top), session (bottom)
      const labelG = svg.append("g");
      labelG.append("text")
        .attr("x", PAD_L - 10).attr("y", cy - 6)
        .attr("text-anchor", "end")
        .attr("font-size", "10px").attr("fill", "#60a5fa")
        .attr("font-family", "monospace")
        .text(sess.agent_id.slice(0, 16));
      labelG.append("text")
        .attr("x", PAD_L - 10).attr("y", cy + 8)
        .attr("text-anchor", "end")
        .attr("font-size", "9px").attr("fill", "#4b5563")
        .attr("font-family", "monospace")
        .text(sess.session_id.slice(0, 10) + "…");
      if (sess.duration_ms > 0) {
        labelG.append("text")
          .attr("x", PAD_L - 10).attr("y", cy + 22)
          .attr("text-anchor", "end")
          .attr("font-size", "8px").attr("fill", "#374151")
          .attr("font-family", "monospace")
          .text(sess.duration_ms < 1000
            ? `${sess.duration_ms} ms`
            : `${(sess.duration_ms / 1000).toFixed(1)} s`);
      }

      // Guide line
      svg.append("line")
        .attr("x1", PAD_L).attr("x2", W - PAD_R)
        .attr("y1", cy).attr("y2", cy)
        .attr("stroke", "#1f2937").attr("stroke-dasharray", "3 5");

      // Build clusters for this session
      const clusters = buildClusters(sess.events, xScale);

      // Build id→pixel map for parent connectors (use original timestamps)
      const xOf = new Map<string, number>();
      sess.events.forEach((e) => xOf.set(e.id, xScale(+new Date(e.timestamp))));

      // Parent connectors (drawn under dots)
      sess.events.forEach((e) => {
        if (!e.parent_id) return;
        const px  = xOf.get(e.parent_id);
        const ex2 = xOf.get(e.id);
        if (px == null || ex2 == null) return;
        svg.append("line")
          .attr("x1", px).attr("y1", cy)
          .attr("x2", ex2).attr("y2", cy)
          .attr("stroke", typeColor(e.type))
          .attr("stroke-width", 1)
          .attr("stroke-opacity", 0.3);
      });

      // Render clusters
      clusters.forEach((cluster) => {
        const cx  = cluster.x;
        const isSingle = cluster.events.length === 1;
        const col = isSingle ? typeColor(cluster.events[0].type) : clusterColor(cluster.events);

        if (isSingle) {
          const ev = cluster.events[0];
          svg.append("circle")
            .attr("cx", cx).attr("cy", cy).attr("r", 7)
            .attr("fill", col).attr("fill-opacity", 0.18)
            .attr("stroke", col).attr("stroke-width", 1.8)
            .style("cursor", "pointer")
            .on("click", () => { setSelected(ev); setSelCluster(null); })
            .on("mouseenter", function () {
              d3.select(this).attr("r", 10).attr("fill-opacity", 0.45);
            })
            .on("mouseleave", function () {
              d3.select(this).attr("r", 7).attr("fill-opacity", 0.18);
            });
        } else {
          // Cluster bubble
          const g = svg.append("g").style("cursor", "pointer")
            .on("click", () => { setSelCluster(cluster.events); setSelected(null); })
            .on("mouseenter", function () {
              d3.select(this).select("circle").attr("r", 16).attr("fill-opacity", 0.5);
            })
            .on("mouseleave", function () {
              d3.select(this).select("circle").attr("r", 13).attr("fill-opacity", 0.3);
            });

          g.append("circle")
            .attr("cx", cx).attr("cy", cy).attr("r", 13)
            .attr("fill", col).attr("fill-opacity", 0.3)
            .attr("stroke", col).attr("stroke-width", 1.8)
            .attr("stroke-dasharray", "4 2");

          g.append("text")
            .attr("x", cx).attr("y", cy - 1)
            .attr("text-anchor", "middle")
            .attr("dominant-baseline", "central")
            .attr("font-size", "8px").attr("fill", "white")
            .attr("font-weight", "bold").attr("font-family", "monospace")
            .text(`+${cluster.events.length}`);
        }
      });
    });

    // Legend
    const legendY = H - PAD_B + 20;
    let lx = PAD_L;
    Object.entries(TYPE_COLOR).forEach(([type, color]) => {
      svg.append("circle").attr("cx", lx + 5).attr("cy", legendY).attr("r", 4).attr("fill", color);
      svg.append("text")
        .attr("x", lx + 13).attr("y", legendY + 4)
        .attr("font-size", "9px").attr("fill", "#6b7280").attr("font-family", "monospace")
        .text(type);
      lx += type.length * 6 + 26;
    });

    // Cluster legend
    svg.append("circle")
      .attr("cx", lx + 5).attr("cy", legendY).attr("r", 6)
      .attr("fill", "#6b7280").attr("fill-opacity", 0.3)
      .attr("stroke", "#6b7280").attr("stroke-dasharray", "3 2");
    svg.append("text")
      .attr("x", lx + 5).attr("y", legendY + 4)
      .attr("text-anchor", "middle").attr("font-size", "7px").attr("fill", "white").attr("font-weight", "bold")
      .text("+N");
    svg.append("text")
      .attr("x", lx + 15).attr("y", legendY + 4)
      .attr("font-size", "9px").attr("fill", "#6b7280").attr("font-family", "monospace")
      .text("cluster");
  }, [sessions]);

  // ── Render ─────────────────────────────────────────────────────────────────
  if (loading) return (
    <div className="flex items-center justify-center h-64 text-gray-500 text-sm gap-2">
      <span className="animate-spin text-blue-400">⟳</span> Loading timeline…
    </div>
  );

  if (error) return (
    <div className="flex flex-col items-center justify-center h-64 gap-2 text-sm">
      <span className="text-red-400">{error}</span>
      <button onClick={load} className="text-blue-400 hover:underline text-xs">Retry</button>
    </div>
  );

  if (sessions.length === 0) return (
    <div className="flex flex-col items-center justify-center h-64 text-gray-500 text-sm gap-1">
      <span className="text-xl">⏱</span>
      <p>No execution traces yet.</p>
      <p className="text-xs text-gray-600">Traces appear as agents send events.</p>
    </div>
  );

  return (
    <div className="relative w-full">
      <div className="flex items-center gap-4 mb-3 text-xs text-gray-500">
        <span>{sessions.length} sessions</span>
        <span>{sessions.reduce((s, sess) => s + sess.event_count, 0)} events</span>
        <button onClick={load} className="ml-auto text-blue-400 hover:underline">Refresh</button>
      </div>

      <div className="w-full overflow-x-auto rounded-lg bg-gray-950 border border-gray-800">
        <svg ref={svgRef} className="w-full" />
      </div>

      {/* Single-event detail */}
      {selected && (
        <div className="mt-3 bg-gray-900 border border-gray-700 rounded-lg p-3 text-xs">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full" style={{ background: typeColor(selected.type) }} />
              <span className="font-mono font-semibold" style={{ color: typeColor(selected.type) }}>
                {selected.type}
              </span>
              <span className="text-gray-300 font-mono">{selected.name}</span>
            </div>
            <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-gray-300">×</button>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-gray-400 font-mono">
            <span className="text-gray-600">id</span>       <span className="truncate">{selected.id}</span>
            <span className="text-gray-600">timestamp</span><span>{new Date(selected.timestamp).toLocaleTimeString()}</span>
            {selected.parent_id && (
              <><span className="text-gray-600">parent</span><span className="truncate">{selected.parent_id}</span></>
            )}
          </div>
          {Object.keys(selected.metadata).length > 0 && (
            <pre className="mt-2 text-[10px] text-gray-500 bg-gray-800 rounded p-2 overflow-auto max-h-28">
              {JSON.stringify(selected.metadata, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* Cluster detail — lists all events in the cluster */}
      {selCluster && (
        <div className="mt-3 bg-gray-900 border border-gray-700 rounded-lg p-3 text-xs">
          <div className="flex items-center justify-between mb-2">
            <span className="text-gray-300 font-semibold">
              Cluster — {selCluster.length} concurrent events
            </span>
            <button onClick={() => setSelCluster(null)} className="text-gray-500 hover:text-gray-300">×</button>
          </div>
          <div className="space-y-1.5 max-h-56 overflow-y-auto">
            {selCluster.map((ev) => (
              <button
                key={ev.id}
                onClick={() => { setSelected(ev); setSelCluster(null); }}
                className="w-full flex items-center gap-2 text-left px-2 py-1.5 rounded hover:bg-gray-800 transition-colors"
              >
                <span className="w-2 h-2 rounded-full flex-shrink-0"
                  style={{ background: typeColor(ev.type) }} />
                <span className="font-mono font-semibold flex-shrink-0"
                  style={{ color: typeColor(ev.type) }}>{ev.type}</span>
                <span className="text-gray-300 font-mono truncate">{ev.name}</span>
                <span className="text-gray-600 font-mono ml-auto flex-shrink-0">
                  {new Date(ev.timestamp).toLocaleTimeString()}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
