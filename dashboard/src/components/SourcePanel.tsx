import { useEffect } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Activity, BellRing, ScrollText, X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago, fmtMetric, METRIC_LABEL, STATE_COLOR } from "@/lib/format";
import type { HealthState, Source } from "@/lib/types";
import { MetricChart } from "@/charts/MetricChart";
import { Pseudo, PrioBadge, StateBadge } from "./ui";

interface Detail {
  health: Source;
  transitions: { old_state: HealthState | null; new_state: HealthState; reason: string; at: string }[];
  latest: { metric: string; avg: number; p95: number; unit: string }[];
  incidents: { incident_id: string; title: string; priority: string; status: string }[];
}

export function SourcePanel() {
  const panel = useStore((s) => s.panel);
  const close = () => useStore.getState().openPanel(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const id = panel?.kind === "source" ? panel.id : null;
  const { data } = useQuery({
    queryKey: ["source", id],
    queryFn: () => api<Detail>(`/api/sources/${encodeURIComponent(id!)}`),
    enabled: !!id,
    refetchInterval: 5000,
  });
  if (!id) return null;
  const h = data?.health;
  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/10" onClick={close} />
      <aside className="slide-in fixed top-0 right-0 bottom-0 z-40 flex w-[560px] max-w-full flex-col border-l border-line bg-panel shadow-2xl">
        <header className="flex items-center gap-3 border-b border-line px-4 py-3">
          <div className="h-9 w-1.5 rounded-full" style={{ background: h ? STATE_COLOR[h.state] : "var(--stale)" }} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Pseudo src={id} role={h?.role} />
              {h && <StateBadge state={h.state} stale={h.stale} />}
            </div>
            <div className="mt-1 truncate text-[12px] text-t2">{h?.reason ?? "…"}</div>
          </div>
          <button onClick={close} className="rounded p-1.5 text-t3 hover:bg-panel-2 hover:text-t1">
            <X size={16} />
          </button>
        </header>
        <div className="flex gap-2 border-b border-line px-4 py-2 text-[12px]">
          <Link to={`/metrics?src=${encodeURIComponent(id)}`} onClick={close} className="inline-flex items-center gap-1 text-accent hover:underline">
            <Activity size={13} /> View metrics
          </Link>
          <span className="text-t3">·</span>
          <Link to={`/events?src=${encodeURIComponent(id)}`} onClick={close} className="inline-flex items-center gap-1 text-accent hover:underline">
            <ScrollText size={13} /> Related events
          </Link>
          {data?.incidents[0] && (
            <>
              <span className="text-t3">·</span>
              <Link to={`/incidents/${data.incidents[0].incident_id}`} onClick={close} className="inline-flex items-center gap-1 text-accent hover:underline">
                <BellRing size={13} /> Open incident
              </Link>
            </>
          )}
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {h && (
            <div className="grid grid-cols-3 gap-2">
              {Object.entries(h.signals).map(([m, s]) => (
                <div key={m} className="rounded-md border border-line p-2.5">
                  <div className="text-[11px] text-t3">
                    {METRIC_LABEL[m] ?? m} <span className="text-t3">({s.stat})</span>
                  </div>
                  <div className="mt-0.5 text-[17px] font-semibold tnum" style={{ color: STATE_COLOR[s.state] }}>
                    {fmtMetric(m, s.value)}
                  </div>
                </div>
              ))}
            </div>
          )}
          {h &&
            Object.keys(h.signals)
              .filter((m) => m !== "disk_hours_to_full" && m !== "collector")
              .map((m) => (
                <div key={m}>
                  <div className="mb-1 text-[12px] font-semibold">
                    {METRIC_LABEL[m] ?? m} <span className="font-normal text-t3">· band = learned baseline (median ± 3·MAD)</span>
                  </div>
                  <MetricChart src={id} metric={m} height={130} />
                </div>
              ))}
          {data && data.incidents.length > 0 && (
            <div>
              <div className="mb-1.5 text-[12px] font-semibold">Incidents involving this component</div>
              {data.incidents.map((i) => (
                <Link key={i.incident_id} to={`/incidents/${i.incident_id}`} onClick={close} className="flex items-center gap-2 rounded px-1 py-1.5 hover:bg-panel-2">
                  <PrioBadge p={i.priority} />
                  <span className="truncate">{i.title}</span>
                  <span className="ml-auto text-[11px] text-t3">{i.status}</span>
                </Link>
              ))}
            </div>
          )}
          {data && (
            <div>
              <div className="mb-1.5 text-[12px] font-semibold">State history</div>
              <div className="space-y-1">
                {data.transitions.slice(0, 12).map((t, i) => (
                  <div key={i} className="flex items-center gap-2 text-[12px]">
                    <span className="w-20 shrink-0 text-t3">{ago(t.at)}</span>
                    <StateBadge state={t.new_state} size="sm" />
                    <span className="truncate text-t2">{t.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
