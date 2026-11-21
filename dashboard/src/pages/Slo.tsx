import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cssVar, fmtMs } from "@/lib/format";
import type { Slo } from "@/lib/types";
import { EChart } from "@/charts/EChart";
import { KpiTile } from "@/components/KpiTile";
import { Panel, Skeleton } from "@/components/ui";

const LABEL: Record<string, string> = { dash: "Event → dashboard", alert: "Breach → alert on screen", offline: "Collector offline → shown" };
const HOPS: [string, string][] = [
  ["window_wait", "window wait"], ["seal", "seal + queue"], ["wire", "wire + ingest"], ["store", "store"], ["push", "push"], ["render", "render"],
];

export default function SloPage() {
  const theme = useStore((s) => s.theme);
  const live = useStore((s) => s.latency);
  const { data } = useQuery({ queryKey: ["slo"], queryFn: () => api<Slo>("/api/slo"), refetchInterval: 5000 });

  const hist = useMemo(() => {
    if (!data) return {};
    const buckets = Array.from({ length: 24 }, (_, i) => i + 1);
    return {
      tooltip: { trigger: "axis" },
      legend: { top: 0, right: 0 },
      xAxis: { type: "category", data: buckets.map((b) => `${((b - 1) * data.bucket_ms) / 1000}s`), axisLabel: { interval: 3 } },
      series: ["dash", "alert"].map((k, i) => ({
        type: "bar", name: k, stack: "h", data: buckets.map((b) => data.histogram.find((h) => h.kind === k && h.b === b)?.n ?? 0),
        itemStyle: { color: i ? "#f2994a" : "#14b8a6" },
        markLine: i === 0 ? { symbol: "none", data: [{ xAxis: `${(10 * data.bucket_ms) / 1000}s`, label: { formatter: "5 s target" }, lineStyle: { color: cssVar("--poor"), type: "dashed" } }] } : undefined,
      })),
    };
  }, [data, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  const scatter = useMemo(() => {
    if (!data) return {};
    return {
      tooltip: { trigger: "item", formatter: (p: { value: [string, number] }) => fmtMs(p.value[1]) },
      yAxis: { axisLabel: { formatter: (v: number) => fmtMs(v) } },
      series: ["dash", "alert"].map((k, i) => ({
        type: "scatter", name: k, symbolSize: 5, data: data.recent.filter((r) => r.kind === k).map((r) => [r.at, r.total_ms]), itemStyle: { color: i ? "#f2994a" : "#14b8a6", opacity: 0.75 },
        markLine: { symbol: "none", silent: true, data: [{ yAxis: data.targets[k], lineStyle: { color: i ? "#f2994a" : "#14b8a6", type: "dashed" }, label: { formatter: `${k} target`, fontSize: 10 } }] },
      })),
    };
  }, [data]);

  if (!data) return <div className="p-4"><Skeleton h={500} /></div>;
  const hops = HOPS.map(([k, l]) => [l, Number(data.hops_ms[k] ?? 0)] as const);
  const total = hops.reduce((a, [, v]) => a + v, 0) || 1;
  const colors = ["#5b6cf0", "#7c6cf0", "#9b8afb", "#14b8a6", "#3fb68b", "#f2c94c"];
  return (
    <div className="mx-auto max-w-[1680px] space-y-3 p-4">
      <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
        {(["dash", "alert", "offline"] as const).map((k) => {
          const v = data.kinds[k];
          const t = data.targets[k];
          return (
            <KpiTile
              key={k} label={`${LABEL[k]} · p95`} value={(v?.p95 ?? 0) / 1000} decimals={k === "offline" ? 0 : 2} unit="s"
              tone={!v ? "calm" : v.p95 <= t ? "good" : "bad"}
              spark={k === "dash" ? live.filter((x) => x.kind === "dash").map((x) => x.ms) : data.recent.filter((r) => r.kind === k).map((r) => r.total_ms).reverse()}
              sub={v ? `p50 ${fmtMs(v.p50)} · max ${fmtMs(v.max)} · n=${v.n} · target ${fmtMs(t)}` : "no samples yet"}
            />
          );
        })}
      </div>
      <Panel title="Where the time goes" subtitle="average per hop for dashboard updates — measured from the timestamps each hop stamps, not estimated">
        <div className="flex h-9 overflow-hidden rounded-md">
          {hops.map(([l, v], i) => (
            <div key={l} className="flex items-center justify-center overflow-hidden text-[11px] font-semibold whitespace-nowrap text-white" style={{ width: `${(v / total) * 100}%`, background: colors[i], minWidth: v ? 34 : 0 }} title={`${l}: ${fmtMs(v)}`}>
              {v / total > 0.06 ? fmtMs(v) : ""}
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-t2">
          {hops.map(([l, v], i) => (
            <span key={l}>
              <span className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ background: colors[i] }} />
              {l} <b className="tnum">{fmtMs(v)}</b>
            </span>
          ))}
        </div>
        <div className="mt-2 text-[11.5px] text-t3">
          Window wait dominates by design (5 s aggregation windows). Excluded from the SLO and counted separately: backlog replays {data.excluded.backlog ?? 0}, late revisions {data.excluded.revision ?? 0}, hidden or asleep browser tabs {data.excluded.client_stall ?? 0}.
        </div>
      </Panel>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Latency distribution" subtitle="last 15 min">
          <EChart option={hist} height={240} />
        </Panel>
        <Panel title="Every sample" subtitle="one dot per rendered update / alert">
          <EChart option={scatter} height={240} />
        </Panel>
      </div>
    </div>
  );
}
