import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, FunnelChart, LineChart, PieChart, SankeyChart, ScatterChart, HeatmapChart } from "echarts/charts";
import {
  GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, TooltipComponent,
  VisualMapComponent, DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";
import { useStore } from "@/lib/store";
import { cssVar } from "@/lib/format";

echarts.use([
  LineChart, BarChart, PieChart, SankeyChart, FunnelChart, ScatterChart, HeatmapChart, GridComponent, TooltipComponent,
  LegendComponent, MarkAreaComponent, MarkLineComponent, MarkPointComponent, VisualMapComponent, DataZoomComponent,
  CanvasRenderer,
]);

/** Categorical series palette (original, colour-blind-checked order). */
export const PALETTE = ["#4f8ff7", "#f2994a", "#9b8afb", "#e86a92", "#3fb68b", "#f2c94c", "#56ccf2", "#c77ddb", "#8d9bb0"];

export function baseOption(): EChartsCoreOption {
  const t2 = cssVar("--text-2");
  const t3 = cssVar("--text-3");
  const line = cssVar("--border");
  return {
    color: PALETTE,
    animationDuration: 500,
    animationDurationUpdate: 400,
    textStyle: { fontFamily: "Inter Variable, sans-serif", color: t2, fontSize: 11 },
    grid: { left: 8, right: 12, top: 18, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: cssVar("--panel"),
      borderColor: cssVar("--border-strong"),
      textStyle: { color: cssVar("--text-1"), fontSize: 12 },
      axisPointer: { type: "line", lineStyle: { color: t3, type: "dashed" } },
      confine: true,
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: line } },
      axisTick: { show: false },
      axisLabel: { color: t3, fontSize: 10.5, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: t3, fontSize: 10.5 },
      splitLine: { lineStyle: { color: line, type: [3, 3] } },
      axisLine: { show: false },
    },
  };
}

function merge(a: Record<string, unknown>, b: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...a };
  for (const [k, v] of Object.entries(b)) {
    const av = out[k];
    out[k] =
      v && typeof v === "object" && !Array.isArray(v) && av && typeof av === "object" && !Array.isArray(av)
        ? merge(av as Record<string, unknown>, v as Record<string, unknown>)
        : v;
  }
  return out;
}

export function EChart({
  option,
  height = 180,
  className,
  base = true,
  onClick,
}: {
  option: EChartsCoreOption;
  height?: number | string;
  className?: string;
  base?: boolean;
  onClick?: (p: { name?: string; seriesName?: string; data?: unknown; value?: unknown }) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const theme = useStore((s) => s.theme);

  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  // Animate only the first draw. Later data refreshes update in place: no replayed draw-in
  // animation, and series are replaced only when the set of series itself changes.
  const drawn = useRef(false);
  const lastSig = useRef("");
  const lastJson = useRef("");
  const lastTheme = useRef(theme);
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const opt = (base ? merge(baseOption() as Record<string, unknown>, option as Record<string, unknown>) : option) as Record<string, unknown>;
    const json = JSON.stringify(opt);
    if (drawn.current && json === lastJson.current && lastTheme.current === theme) return; // nothing changed
    lastJson.current = json;
    lastTheme.current = theme;
    const series = (Array.isArray(opt.series) ? opt.series : opt.series ? [opt.series] : []) as Record<string, unknown>[];
    const sig = series.map((s) => `${s.type}:${s.id ?? s.name ?? ""}`).join("|");
    const structural = !drawn.current || sig !== lastSig.current;
    lastSig.current = sig;
    c.setOption({ ...opt, animation: !drawn.current } as EChartsCoreOption, structural ? { replaceMerge: ["series"] } : { lazyUpdate: true });
    drawn.current = true;
  }, [option, theme, base]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !onClick) return;
    c.on("click", onClick as never);
    return () => {
      c.off("click");
    };
  }, [onClick]);

  return <div ref={el} className={className} style={{ height, width: "100%" }} />;
}
