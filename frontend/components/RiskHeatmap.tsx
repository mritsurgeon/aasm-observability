"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { api, HeatmapData } from "@/lib/api";

interface Props { eventCount: number; }

interface SelectedCell {
  type: string;
  bucket: string;
  count: number;
  error_count: number;
  risk_score: number;
}

export function RiskHeatmap({ eventCount }: Props) {
  const svgRef                      = useRef<SVGSVGElement>(null);
  const [data, setData]             = useState<HeatmapData | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [bucketMins, setBucketMins] = useState(5);
  const [selected, setSelected]     = useState<SelectedCell | null>(null);
  const lastCountRef                = useRef(-1);

  const load = useCallback(async (bm = bucketMins) => {
    try {
      setError(null);
      const d = await api.heatmap(12, bm);
      setData(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [bucketMins]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (eventCount === lastCountRef.current) return;
    lastCountRef.current = eventCount;
    const t = setTimeout(() => load(), 2000);
    return () => clearTimeout(t);
  }, [eventCount, load]);

  // ── D3 render ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current || !data || data.rows.length === 0) return;

    const container = svgRef.current.parentElement!;
    const W       = container.clientWidth || 800;
    const PAD_L   = 88;
    const PAD_R   = 16;
    const PAD_T   = 28;
    const PAD_B   = 36;
    const ROWS    = data.rows.length;
    const COLS    = data.buckets;
    const CELL_H  = 40;
    const CELL_W  = Math.max(24, Math.floor((W - PAD_L - PAD_R) / COLS));
    const H       = PAD_T + ROWS * CELL_H + PAD_B;

    const colorScale = d3.scaleSequential()
      .domain([0, Math.max(data.max_risk, 0.01)])
      .interpolator(d3.interpolateRgb("#1f2937", "#ef4444"));

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", W).attr("height", H);
    svg.append("rect").attr("width", W).attr("height", H).attr("fill", "#030712");

    // Column labels (time buckets)
    data.bucket_labels.forEach((lbl, ci) => {
      if (ci % Math.ceil(COLS / 8) !== 0) return;
      svg.append("text")
        .attr("x", PAD_L + ci * CELL_W + CELL_W / 2)
        .attr("y", PAD_T - 6)
        .attr("text-anchor", "middle")
        .attr("font-size", "9px")
        .attr("fill", "#4b5563")
        .text(lbl);
    });

    // Rows
    data.rows.forEach((row, ri) => {
      const cy = PAD_T + ri * CELL_H;

      // Row label
      svg.append("text")
        .attr("x", PAD_L - 6).attr("y", cy + CELL_H / 2 + 4)
        .attr("text-anchor", "end")
        .attr("font-size", "10px")
        .attr("fill", "#6b7280")
        .attr("font-family", "monospace")
        .text(row.type);

      // Cells
      row.cells.forEach((cell, ci) => {
        const cx   = PAD_L + ci * CELL_W;
        const fill = cell.count === 0 ? "#0d1117" : colorScale(cell.risk_score);

        const rect = svg.append("rect")
          .attr("x", cx + 1).attr("y", cy + 1)
          .attr("width", CELL_W - 2).attr("height", CELL_H - 2)
          .attr("fill", fill)
          .attr("rx", 2)
          .style("cursor", "pointer");

        if (cell.count > 0) {
          svg.append("text")
            .attr("x", cx + CELL_W / 2).attr("y", cy + CELL_H / 2 + 4)
            .attr("text-anchor", "middle")
            .attr("font-size", "9px")
            .attr("fill", cell.risk_score > 0.4 ? "#fca5a5" : "#6b7280")
            .attr("pointer-events", "none")
            .text(cell.count);
        }

        // Click handler — all cells (incl empty) are clickable for context
        rect.on("click", () => {
          setSelected({
            type:        row.type,
            bucket:      data.bucket_labels[ci],
            count:       cell.count,
            error_count: cell.error_count,
            risk_score:  cell.risk_score,
          });
        });

        // Hover highlight
        rect.on("mouseenter", function () {
          d3.select(this).attr("stroke", "#9ca3af").attr("stroke-width", 1.5);
        }).on("mouseleave", function () {
          d3.select(this).attr("stroke", null);
        });
      });
    });

    // Color legend
    const legendW = 120;
    const legendX = W - PAD_R - legendW;
    const legendY = H - PAD_B + 10;
    const defs = svg.append("defs");
    const grad = defs.append("linearGradient").attr("id", "hm-grad");
    grad.append("stop").attr("offset", "0%").attr("stop-color", "#1f2937");
    grad.append("stop").attr("offset", "100%").attr("stop-color", "#ef4444");
    svg.append("rect")
      .attr("x", legendX).attr("y", legendY).attr("width", legendW).attr("height", 8)
      .attr("fill", "url(#hm-grad)").attr("rx", 2);
    svg.append("text").attr("x", legendX).attr("y", legendY + 20)
      .attr("font-size", "9px").attr("fill", "#4b5563").text("low risk");
    svg.append("text").attr("x", legendX + legendW).attr("y", legendY + 20)
      .attr("text-anchor", "end").attr("font-size", "9px").attr("fill", "#4b5563").text("high risk");

  }, [data]);

  // ── Render ─────────────────────────────────────────────────────────────────
  const bucketOptions = [1, 5, 10, 15, 30];

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-gray-500 text-sm gap-2">
      <span className="animate-spin text-blue-400">⟳</span> Loading heatmap…
    </div>
  );

  if (error) return (
    <div className="flex flex-col items-center justify-center h-64 gap-2 text-sm">
      <span className="text-red-400">{error}</span>
      <button onClick={() => load()} className="text-blue-400 hover:underline text-xs">Retry</button>
    </div>
  );

  const hasActivity = data && data.total_events > 0;

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex items-center gap-4 text-xs text-gray-500">
        <span>Bucket size:</span>
        {bucketOptions.map((m) => (
          <button key={m}
            onClick={() => { setBucketMins(m); load(m); }}
            className={`px-2 py-0.5 rounded border transition-colors ${
              bucketMins === m
                ? "border-blue-500 text-blue-400 bg-blue-950"
                : "border-gray-700 text-gray-500 hover:border-gray-500"
            }`}>
            {m}m
          </button>
        ))}
        <span className="ml-auto">
          {data?.total_events ?? 0} events · last {(data?.buckets ?? 12) * bucketMins}m
        </span>
        <button onClick={() => load()} className="text-blue-400 hover:underline">Refresh</button>
      </div>

      {!hasActivity ? (
        <div className="flex flex-col items-center justify-center h-48 text-gray-500 text-sm gap-1">
          <span className="text-xl">▦</span>
          <p>No events in the last {(data?.buckets ?? 12) * bucketMins} minutes.</p>
        </div>
      ) : (
        <div className="w-full overflow-x-auto rounded-lg bg-gray-950 border border-gray-800">
          <svg ref={svgRef} className="w-full" />
        </div>
      )}

      {/* Cell detail panel */}
      {selected && (
        <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 text-xs">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="font-mono font-semibold text-yellow-400">{selected.type}</span>
              <span className="text-gray-500">@ {selected.bucket}</span>
            </div>
            <button onClick={() => setSelected(null)} className="text-gray-500 hover:text-gray-300 text-base">×</button>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-gray-800 rounded p-2 text-center">
              <div className="text-xl font-bold text-gray-200">{selected.count}</div>
              <div className="text-gray-500 mt-0.5">events</div>
            </div>
            <div className="bg-gray-800 rounded p-2 text-center">
              <div className={`text-xl font-bold ${selected.error_count > 0 ? "text-red-400" : "text-gray-400"}`}>
                {selected.error_count}
              </div>
              <div className="text-gray-500 mt-0.5">errors</div>
            </div>
            <div className="bg-gray-800 rounded p-2 text-center">
              <div className={`text-xl font-bold ${
                selected.risk_score >= 0.7 ? "text-red-400" :
                selected.risk_score >= 0.4 ? "text-yellow-400" : "text-green-400"
              }`}>
                {(selected.risk_score * 100).toFixed(1)}%
              </div>
              <div className="text-gray-500 mt-0.5">risk score</div>
            </div>
          </div>
          {selected.count > 0 && (
            <p className="mt-2 text-gray-600 font-mono">
              error rate: {selected.count > 0 ? ((selected.error_count / selected.count) * 100).toFixed(1) : 0}%
            </p>
          )}
        </div>
      )}
    </div>
  );
}
