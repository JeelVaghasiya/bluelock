import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { METRIC_LABEL, shortSrc } from "@/lib/format";
import type { Source } from "@/lib/types";
import { MetricChart } from "@/charts/MetricChart";
import { PALETTE } from "@/charts/EChart";
import { Panel, Pseudo, Skeleton, StateBadge } from "@/components/ui";

const BY_KIND: Record<string, string[]> = {
  host: ["cpu", "mem", "disk", "load"],
  svc: ["error_rate", "rtt", "up"],
  link: ["rtt", "loss", "jitter"],
  device: ["net_util", "if_errors", "drops", "cpu"],
};
// what matters differs by what a component IS: a database is read by query time, a SAN by I/O latency
const BY_ROLE: Record<string, Record<string, string[]>> = {
  svc: {
    db: ["query_ms", "qps", "db_conns", "repl_lag", "cache_hit", "error_rate"],
    cache: ["cache_hit", "evictions", "req_rate", "error_rate"],
    queue: ["queue_depth", "consumer_lag", "req_rate", "error_rate"],
    web: ["req_rate", "apdex", "rtt", "error_rate"],
    api: ["req_rate", "apdex", "rtt", "error_rate"],
    other: ["req_rate", "apdex", "rtt", "error_rate"],
    compute: ["req_rate", "error_rate", "up"],
  },
  device: {
    storage: ["io_latency", "iops", "io_bytes", "queue_depth", "capacity"],
    network: ["net_util", "if_errors", "drops", "sessions", "cpu"],
  },
};

export default function Metrics() {
  const [params, setParams] = useSearchParams();
  const { data } = useQuery({ queryKey: ["sources"], queryFn: () => api<{ sources: Source[] }>("/api/sources"), refetchInterval: 5000 });
  const [filter, setFilter] = useState("");
  const sources = useMemo(() => (data?.sources ?? []).filter((s) => !filter || s.role.includes(filter) || s.src.includes(filter)), [data, filter]);
  const sel = params.get("src") ?? sources.find((s) => s.state === "poor")?.src ?? sources[0]?.src;
  const src = (data?.sources ?? []).find((s) => s.src === sel);
  if (!data) return <div className="p-4"><Skeleton h={500} /></div>;
  const metrics = src ? BY_ROLE[src.kind]?.[src.role] ?? BY_KIND[src.kind] ?? Object.keys(src.signals) : [];
  return (
    <div className="mx-auto grid max-w-[1680px] grid-cols-[260px_1fr] gap-3 p-4">
      <Panel title="Components" pad={false} className="max-h-[calc(100vh-120px)]">
        <div className="px-3 pb-2">
          <input className="input w-full" placeholder="filter by role or code" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="overflow-y-auto">
          {sources.map((s) => (
            <button
              key={s.src}
              onClick={() => setParams({ src: s.src })}
              className={`flex w-full items-center justify-between gap-2 border-l-2 px-3 py-1.5 text-left hover:bg-panel-2 ${s.src === sel ? "border-accent bg-panel-2" : "border-transparent"}`}
            >
              <span className="min-w-0">
                <span className="mono block text-[12px]">{shortSrc(s.src)}</span>
                <span className="block truncate text-[11px] text-t3">{s.role} · {s.collector_id}</span>
              </span>
              <StateBadge state={s.state} size="sm" />
            </button>
          ))}
        </div>
      </Panel>
      <div className="min-w-0 space-y-3">
        {src && (
          <div className="flex items-center gap-3">
            <Pseudo src={src.src} role={src.role} />
            <StateBadge state={src.state} stale={src.stale} />
            <span className="truncate text-t2">{src.reason}</span>
          </div>
        )}
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {src &&
            metrics.map((m, i) => (
              <Panel key={`${src.src}-${m}`} title={METRIC_LABEL[m] ?? m} subtitle="band = learned baseline · dashed = health thresholds">
                <MetricChart src={src.src} metric={m} height={210} color={PALETTE[i % PALETTE.length]} />
              </Panel>
            ))}
        </div>
      </div>
    </div>
  );
}
