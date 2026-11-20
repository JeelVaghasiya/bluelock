import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cx, hhmmss, shortSrc } from "@/lib/format";
import type { EventRow } from "@/lib/types";
import { EChart } from "@/charts/EChart";
import { Panel, Provenance, Redacted, Skeleton } from "@/components/ui";

type Facets = Record<"level" | "category" | "src", { value: string; n: number }[]>;
const LEVEL_COLOR: Record<string, string> = { error: "var(--poor)", warning: "var(--fair)", info: "var(--low)", debug: "var(--text-3)" };

export default function Events() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const filters = { level: params.get("level"), category: params.get("category"), src: params.get("src") };
  const ev = useQuery({
    queryKey: ["events", q, filters],
    queryFn: () => api<{ events: EventRow[] }>("/api/events", { query: { q, ...filters } }),
    refetchInterval: 5000,
  });
  const facets = useQuery({ queryKey: ["events", "facets"], queryFn: () => api<Facets>("/api/events/facets"), refetchInterval: 15_000 });
  const toggle = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (p.get(k) === v) p.delete(k);
    else p.set(k, v);
    setParams(p);
  };
  const rows = ev.data?.events ?? [];
  const hist = useMemo(() => {
    const m = new Map<string, Record<string, number>>();
    rows.forEach((e) => {
      const t = new Date(Math.floor(Date.parse(e.event_ts) / 30000) * 30000).toISOString();
      const o = m.get(t) ?? {};
      o[e.level] = (o[e.level] ?? 0) + e.count;
      m.set(t, o);
    });
    const ts = [...m.keys()].sort();
    return {
      grid: { top: 8, bottom: 0 },
      series: ["info", "warning", "error"].map((lv) => ({
        type: "bar", stack: "e", name: lv, data: ts.map((t) => [t, m.get(t)![lv] ?? 0]), itemStyle: { color: { info: "#60a5fa", warning: "#f5a524", error: "#e5484d" }[lv] }, barCategoryGap: "15%",
      })),
    };
  }, [rows]);

  return (
    <div className="mx-auto grid max-w-[1680px] grid-cols-[230px_1fr] gap-3 p-4">
      <Panel title="Facets" subtitle="last hour">
        {(["level", "category", "src"] as const).map((k) => (
          <div key={k} className="mb-3">
            <div className="mb-1 text-[11px] font-semibold tracking-wide text-t3 uppercase">{k}</div>
            {(facets.data?.[k] ?? []).slice(0, 8).map((f) => (
              <label key={f.value} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-[12px] hover:bg-panel-2">
                <input type="checkbox" checked={params.get(k) === f.value} onChange={() => toggle(k, f.value)} />
                <span className={cx("min-w-0 flex-1 truncate", k === "src" && "mono")} style={k === "level" ? { color: LEVEL_COLOR[f.value] } : undefined}>
                  {k === "src" ? shortSrc(f.value) : f.value}
                </span>
                <span className="tnum text-t3">{f.n.toLocaleString()}</span>
              </label>
            ))}
          </div>
        ))}
      </Panel>
      <div className="min-w-0 space-y-3">
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="search templates — e.g. timeout, password, checkout"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <Panel title="Volume" subtitle="templated log events (counts per 30 s)">
          {ev.data ? <EChart option={hist} height={110} /> : <Skeleton h={110} />}
        </Panel>
        <Panel title="Log patterns" subtitle="values were replaced on-site: ‹secret› redacted · ‹u:/ip:/h:› pseudonyms · ‹num› masked" pad={false}>
          <div className="max-h-[560px] overflow-auto">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-20">Time</th>
                  <th className="w-16">Level</th>
                  <th>Template</th>
                  <th className="w-16 text-right">Count</th>
                  <th className="w-20 text-right">Redacted</th>
                  <th className="w-28">Source</th>
                  <th className="w-28">Provenance</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 250).map((e, i) => (
                  <tr key={`${e.template_id}-${e.event_ts}-${i}`}>
                    <td className="mono text-t3">{hhmmss(e.event_ts)}</td>
                    <td className="font-medium" style={{ color: LEVEL_COLOR[e.level] }}>{e.level}</td>
                    <td className="max-w-0 truncate"><Redacted text={e.template} /></td>
                    <td className="text-right tnum">{e.count}</td>
                    <td className="text-right tnum text-redact">{e.redacted || ""}</td>
                    <td className="mono text-t2">{shortSrc(e.src)}</td>
                    <td><Provenance payloadId={e.payload_id} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </div>
  );
}
