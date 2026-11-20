import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cssVar, hhmmss } from "@/lib/format";
import { useStore } from "@/lib/store";
import type { Finding } from "@/lib/types";
import { EChart } from "@/charts/EChart";
import { KpiTile } from "@/components/KpiTile";
import { Bar, Panel, Provenance, Pseudo, Section, SevBadge, Skeleton, Tabs } from "@/components/ui";

export default function Security() {
  const theme = useStore((s) => s.theme);
  const [cat, setCat] = useState<"all" | "auth" | "threshold">("all");
  const { data } = useQuery({ queryKey: ["findings"], queryFn: () => api<{ findings: Finding[] }>("/api/security/findings"), refetchInterval: 5000 });
  const all = data?.findings ?? [];
  const rows = all.filter((f) => cat === "all" || f.category === cat);

  const timeline = useMemo(() => {
    const m = new Map<string, Record<string, number>>();
    all.forEach((f) => {
      const t = new Date(Math.floor(Date.parse(f.event_ts) / 60000) * 60000).toISOString();
      const o = m.get(t) ?? {};
      o[f.severity] = (o[f.severity] ?? 0) + 1;
      m.set(t, o);
    });
    const ts = [...m.keys()].sort();
    return {
      legend: { bottom: 0, icon: "roundRect", itemWidth: 10, itemHeight: 8 },
      grid: { bottom: 24 },
      series: ["low", "medium", "high", "critical"].map((s) => ({
        type: "bar", stack: "x", name: s, data: ts.map((t) => [t, m.get(t)![s] ?? 0]), itemStyle: { color: cssVar(`--${s}`) }, barCategoryGap: "20%",
      })),
    };
  }, [all, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  const attackers = useMemo(() => {
    const m = new Map<string, { n: number; risk: number; patterns: Set<string> }>();
    all.filter((f) => f.category === "auth").forEach((f) => {
      const o = m.get(f.src) ?? { n: 0, risk: 0, patterns: new Set<string>() };
      o.n++;
      o.risk = Math.max(o.risk, f.risk);
      o.patterns.add(f.pattern);
      m.set(f.src, o);
    });
    return [...m].sort((a, b) => b[1].risk - a[1].risk || b[1].n - a[1].n).slice(0, 8);
  }, [all]);

  if (!data) return <div className="p-4"><Skeleton h={500} /></div>;
  const crit = all.filter((f) => f.severity === "critical").length;
  const saf = all.filter((f) => f.pattern === "success_after_failures").length;
  return (
    <div className="mx-auto max-w-[1680px] space-y-4 p-4">
      <Section title="Security signals">
        <div className="grid grid-cols-12 gap-2.5">
          <div className="col-span-12 grid grid-cols-3 gap-2.5 lg:col-span-4 lg:grid-cols-1">
            <KpiTile label="Critical signals (30 min)" value={crit} tone={crit ? "bad" : "good"} sub="spray · success after failures" />
            <KpiTile label="Success after failures" value={saf} tone={saf ? "warn" : "good"} sub="possible account takeover" />
            <KpiTile label="Signals (30 min)" value={all.length} tone="calm" sub="edge L0 rules + isolation forest" />
          </div>
          <Panel className="col-span-12 lg:col-span-5" title="Signals over time" subtitle="per minute, by severity">
            <EChart option={timeline} height={330} />
          </Panel>
          <Panel className="col-span-12 lg:col-span-3" title="Top risky sources" subtitle="pseudonymous IPs — real addresses stay on-site">
            <div className="space-y-2">
              {attackers.map(([src, a]) => (
                <div key={src}>
                  <div className="flex items-center justify-between text-[12px]">
                    <Pseudo src={src} />
                    <span className="tnum font-semibold" style={{ color: a.risk > 0.85 ? "var(--critical)" : a.risk > 0.6 ? "var(--high)" : "var(--medium)" }}>
                      {a.risk.toFixed(2)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <Bar value={a.risk} max={1} color={a.risk > 0.85 ? "var(--critical)" : a.risk > 0.6 ? "var(--high)" : "var(--medium)"} />
                    <span className="shrink-0 text-[11px] text-t3">{[...a.patterns].join(", ").replace(/_/g, " ")}</span>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </Section>
      <Panel
        title="Signal explorer"
        right={<Tabs value={cat} onChange={setCat} options={[{ v: "all", label: "all" }, { v: "auth", label: "auth" }, { v: "threshold", label: "threshold" }]} />}
        pad={false}
      >
        <div className="max-h-[520px] overflow-auto">
          <table className="table">
            <thead>
              <tr>
                <th className="w-20">Time</th>
                <th className="w-24">Severity</th>
                <th>Pattern</th>
                <th>Source</th>
                <th className="w-16 text-right">Risk</th>
                <th>Detail</th>
                <th className="w-12">Layer</th>
                <th className="w-28">Provenance</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 200).map((f) => (
                <tr key={f.finding_id + f.event_ts}>
                  <td className="mono text-t3">{hhmmss(f.event_ts)}</td>
                  <td><SevBadge sev={f.severity} /></td>
                  <td className="font-medium">{f.pattern.replace(/_/g, " ")}</td>
                  <td><Pseudo src={f.src} /></td>
                  <td className="text-right tnum">{f.risk.toFixed(2)}</td>
                  <td className="text-t2">
                    {f.category === "auth"
                      ? `${f.fail_count ?? "?"} fails · ${f.distinct_users ?? "?"} users · ${f.window_s ?? 60}s`
                      : `${f.metric} ${f.value} > ${f.threshold}`}
                  </td>
                  <td className="mono text-t3">{f.layer}</td>
                  <td><Provenance payloadId={f.payload_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
