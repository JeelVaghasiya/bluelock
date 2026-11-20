import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cssVar, fmtMetric } from "@/lib/format";
import { RANGE_MIN, useStore } from "@/lib/store";
import type { MetricQuery } from "@/lib/types";
import { EChart } from "./EChart";
import { Skeleton } from "@/components/ui";

/** Timeseries with the learned baseline band, threshold lines and anomaly markers. */
export function MetricChart({
  src, metric, height = 160, minutes, showThresholds = true, color = "#4f8ff7", area = true,
}: {
  src: string; metric: string; height?: number; minutes?: number; showThresholds?: boolean; color?: string; area?: boolean;
}) {
  const range = useStore((s) => s.range);
  const theme = useStore((s) => s.theme);
  const mins = minutes ?? RANGE_MIN[range];
  const { data } = useQuery({
    queryKey: ["metric", src, metric, mins],
    queryFn: () =>
      api<MetricQuery>("/api/metrics/query", {
        query: { src, metric, from: new Date(Date.now() - mins * 60_000).toISOString(), step: mins <= 15 ? 5 : mins <= 60 ? 15 : 60 },
      }),
    refetchInterval: 5000,
  });

  const option = useMemo(() => {
    if (!data) return {};
    const pts = data.points.map((p) => [p.t, p.v]);
    const th = data.thresholds;
    const lines =
      showThresholds && th
        ? (["good", "fair"] as const).map((k) => ({
            yAxis: th[k],
            lineStyle: { color: cssVar(k === "fair" ? "--poor" : "--fair"), type: "dashed", width: 1, opacity: 0.8 },
            label: { show: true, position: "insideEndTop", formatter: k === "fair" ? "poor" : "fair", color: cssVar("--text-3"), fontSize: 10 },
          }))
        : [];
    const series: Record<string, unknown>[] = [
      {
        type: "line",
        name: metric,
        data: pts,
        showSymbol: false,
        smooth: 0.25,
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        areaStyle: area ? { color: { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: color + "55" }, { offset: 1, color: color + "05" }] } } : undefined,
        markLine: lines.length ? { symbol: "none", silent: true, data: lines } : undefined,
        markPoint: data.alerts.length
          ? {
              symbol: "pin",
              symbolSize: 26,
              itemStyle: { color: cssVar("--poor") },
              label: { fontSize: 9, color: "#fff", formatter: "!" },
              data: data.alerts.map((a) => {
                const nearest = pts.reduce((best, p) => (Math.abs(Date.parse(String(p[0])) - Date.parse(a.onset_ts)) < Math.abs(Date.parse(String(best[0])) - Date.parse(a.onset_ts)) ? p : best), pts[0] ?? [a.onset_ts, 0]);
                return { coord: nearest, name: a.incident_id };
              }),
            }
          : undefined,
      },
    ];
    const nonNeg = data.points.every((p) => p.v >= 0);
    const lo = data.band ? (nonNeg ? Math.max(0, data.band.lo) : data.band.lo) : 0;
    if (data.band) {
      series.push(
        { type: "line", name: "band-lo", data: pts.map((p) => [p[0], lo]), stack: "band", lineStyle: { opacity: 0 }, showSymbol: false, silent: true, tooltip: { show: false } },
        {
          type: "line", name: "baseline band", data: pts.map((p) => [p[0], data.band!.hi - lo]), stack: "band", lineStyle: { opacity: 0 }, showSymbol: false, silent: true,
          areaStyle: { color: cssVar("--text-3"), opacity: theme === "dark" ? 0.14 : 0.1 }, tooltip: { show: false },
        },
      );
    }
    return {
      series,
      tooltip: { valueFormatter: (v: number) => fmtMetric(metric, v) },
      yAxis: { axisLabel: { formatter: (v: number) => fmtMetric(metric, v) }, scale: metric !== "error_rate", min: nonNeg && metric !== "rtt" ? 0 : undefined },
    };
  }, [data, metric, showThresholds, color, area, theme]);

  if (!data) return <Skeleton h={height} />;
  return <EChart option={option} height={height} />;
}
