import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cssVar, fmtMetric, fmtMs, fmtNum, METRIC_LABEL, STATE_COLOR, ago } from "@/lib/format";
import type { IncidentDetail, Overview as OverviewT, OverviewSeries, Source, MetricQuery } from "@/lib/types";
import { classify } from "@/mock/world";
import { EChart } from "@/charts/EChart";
import { MetricChart } from "@/charts/MetricChart";
import { HexMap } from "@/components/HexMap";
import type { GroupBy } from "@/components/HexMap";
import { KpiTile } from "@/components/KpiTile";
import type { Tone } from "@/components/KpiTile";
import { Bar, Empty, Panel, PrioBadge, Pseudo, Section, Skeleton, Tabs } from "@/components/ui";

const RULE_LABEL: Record<string, string> = {
  email: "email", ipv4: "ip address", github_token: "github token", aws_access_key: "aws key", jwt: "jwt", password_kv: "password=",
  high_entropy: "high entropy", person_name: "person name", private_key: "private key", generic_secret: "generic secret",
};

function bucket<T extends { t: string }>(rows: T[], key: keyof T, val: keyof T) {
  const times = [...new Set(rows.map((r) => r.t))].sort();
  const keys = [...new Set(rows.map((r) => String(r[key])))];
  const m = new Map(rows.map((r) => [`${r.t}|${String(r[key])}`, Number(r[val])]));
  return { times, keys, get: (t: string, k: string) => m.get(`${t}|${k}`) ?? 0 };
}

export default function Overview() {
  const nav = useNavigate();
  const theme = useStore((s) => s.theme);
  const openPanel = useStore((s) => s.openPanel);
  const [groupBy, setGroupBy] = useState<GroupBy>("role");
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => api<OverviewT>("/api/overview"), refetchInterval: 5000 });
  const series = useQuery({ queryKey: ["series", 60], queryFn: () => api<OverviewSeries>("/api/series/overview", { query: { minutes: 60 } }), refetchInterval: 10_000 });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api<{ sources: Source[] }>("/api/sources"), refetchInterval: 5000 });
  const top = ov.data?.incidents[0];
  const topDetail = useQuery({ queryKey: ["incident", top?.incident_id], queryFn: () => api<IncidentDetail>(`/api/incidents/${top!.incident_id}`), enabled: !!top, refetchInterval: 10_000 });

  const o = ov.data;
  const s = series.data;
  const srcs = sources.data?.sources ?? [];

  // ---------------------------------------------------------------- KPI values
  const total = o ? Object.values(o.states).reduce((a, b) => a + b, 0) : 0;
  const healthyPct = o && total ? ((o.states.excellent + o.states.good) / total) * 100 : 100;
  const openInc = o?.incidents.filter((i) => i.status !== "resolved") ?? [];
  const worstPrio = openInc.reduce((a, i) => (i.priority < a ? i.priority : a), "P9");
  const incTone: Tone = worstPrio === "P1" ? "bad" : worstPrio === "P2" ? "bad" : worstPrio === "P3" ? "warn" : "good";
  const dashP95 = o?.slo.dash?.p95 ?? null;
  const dashTarget = o?.slo.dash?.target ?? 10000;
  const alertTarget = o?.slo.alert?.target ?? 15000;
  const redSpark = useMemo(() => (s ? bucket(s.redactions, "rule", "n").times.map((t) => s.redactions.filter((r) => r.t === t).reduce((a, r) => a + r.n, 0)) : []), [s]);
  const secSpark = useMemo(() => (s ? bucket(s.security, "severity", "n").times.map((t) => s.security.filter((r) => r.t === t).reduce((a, r) => a + r.n, 0)) : []), [s]);
  const latSpark = useMemo(() => s?.latency.filter((l) => l.kind === "dash").map((l) => l.p95) ?? [], [s]);
  const paySpark = useMemo(() => s?.payloads.map((p) => p.records) ?? [], [s]);
  const errSpark = useMemo(() => s?.events.filter((e) => e.level === "error").map((e) => e.n) ?? [], [s]);

  // ---------------------------------------------------------------- charts
  const securityOpt = useMemo(() => {
    if (!s) return {};
    const b = bucket(s.security, "severity", "n");
    const order = ["low", "medium", "high", "critical"].filter((k) => b.keys.includes(k));
    return {
      legend: { bottom: 0, icon: "roundRect", itemWidth: 10, itemHeight: 8, textStyle: { fontSize: 11 } },
      grid: { bottom: 26 },
      series: order.map((k) => ({
        type: "bar", name: k, stack: "s", barCategoryGap: "18%", data: b.times.map((t) => [t, b.get(t, k)]),
        itemStyle: { color: cssVar(`--${k}`), borderRadius: k === "critical" ? [2, 2, 0, 0] : 0 },
      })),
    };
  }, [s, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  const redactOpt = useMemo(() => {
    if (!s) return {};
    const b = bucket(s.redactions, "rule", "n");
    const keys = [...b.keys].sort((x, y) => b.times.reduce((a, t) => a + b.get(t, y), 0) - b.times.reduce((a, t) => a + b.get(t, x), 0));
    return {
      legend: { bottom: 0, type: "scroll", icon: "roundRect", itemWidth: 10, itemHeight: 8, textStyle: { fontSize: 11 }, formatter: (n: string) => RULE_LABEL[n] ?? n },
      grid: { bottom: 26 },
      series: keys.map((k, i) => ({
        type: "line", name: k, stack: "r", smooth: 0.3, showSymbol: false, lineStyle: { width: 0 },
        areaStyle: { opacity: 0.88, color: ["#7c6cf0", "#e05e8f", "#4f8ff7", "#f2994a", "#3fb68b", "#f2c94c", "#56ccf2", "#c77ddb", "#8d9bb0"][i % 9] },
        data: b.times.map((t) => [t, b.get(t, k)]),
      })),
    };
  }, [s]);

  const findings = useQuery({ queryKey: ["findings"], queryFn: () => api<{ findings: { pattern: string; severity: string }[] }>("/api/security/findings"), refetchInterval: 10_000 });
  const patterns = useMemo(() => {
    const m = new Map<string, number>();
    (findings.data?.findings ?? []).forEach((f) => m.set(f.pattern, (m.get(f.pattern) ?? 0) + 1));
    return [...m].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
  }, [findings.data]);
  const donutOpt = useMemo(
    () => ({
      tooltip: { trigger: "item" },
      series: [
        {
          type: "pie", radius: ["58%", "86%"], center: ["50%", "50%"], avoidLabelOverlap: true, padAngle: 1.5,
          itemStyle: { borderRadius: 3, borderColor: cssVar("--panel"), borderWidth: 1 },
          label: { show: true, position: "center", formatter: () => `${patterns.reduce((a, p) => a + p.value, 0)}`, fontSize: 20, fontWeight: 700, color: cssVar("--text-1") },
          data: patterns.map((p, i) => ({ ...p, itemStyle: { color: ["#e5484d", "#f97316", "#f5a524", "#60a5fa", "#9b8afb"][i % 5] } })),
        },
      ],
      xAxis: { show: false }, yAxis: { show: false },
    }),
    [patterns, theme], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const funnelOpt = useMemo(() => {
    const p = o?.privacy;
    if (!p) return {};
    const lines = p.lines_24h ?? p.records_24h * 2;
    const data = [
      { name: "Log lines seen on-site", value: lines, real: lines },
      { name: "Grouped into templates", value: lines * 0.62, real: p.templates_24h ?? 0 },
      { name: "Sensitive fields redacted", value: lines * 0.36, real: p.redactions_24h },
      { name: "Metadata records sent", value: lines * 0.2, real: p.records_24h },
      { name: "Raw fields sent", value: lines * 0.06, real: 0 },
    ];
    const colors = ["#5b6cf0", "#7c6cf0", "#a06cf0", "#14b8a6", "#22a06b"];
    return {
      tooltip: { trigger: "item", formatter: (x: { name: string; data: { real: number } }) => `${x.name}: <b>${fmtNum(x.data.real, 1)}</b>` },
      series: [
        {
          type: "funnel", left: 4, right: 4, top: 4, bottom: 4, minSize: "34%", sort: "none", gap: 3,
          label: { position: "inside", color: "#fff", fontSize: 11.5, fontWeight: 600, formatter: (x: { name: string; data: { real: number } }) => `${x.name}  ${fmtNum(x.data.real, 1)}` },
          itemStyle: { borderWidth: 0, borderRadius: 4 },
          data: data.map((d, i) => ({ ...d, itemStyle: { color: colors[i] } })),
        },
      ],
      xAxis: { show: false }, yAxis: { show: false },
    };
  }, [o]);

  const anomaly = topDetail.data?.incident.evidence?.signals?.find((x) => !x.category || x.category !== "auth");
  const fallbackLink = srcs.find((x) => x.kind === "link");
  const anomalySrc = anomaly?.src ?? fallbackLink?.src;
  const anomalyMetric = anomaly?.metric ?? "rtt";

  const diskSrc = s?.disk_forecast[0]?.src;
  const disk = useQuery({
    queryKey: ["metric", diskSrc, "disk", 60],
    queryFn: () => api<MetricQuery>("/api/metrics/query", { query: { src: diskSrc!, metric: "disk", stat: "max", from: new Date(Date.now() - 3_600_000).toISOString(), step: 60 } }),
    enabled: !!diskSrc,
    refetchInterval: 30_000,
  });
  const diskOpt = useMemo(() => {
    const pts = disk.data?.points ?? [];
    if (pts.length < 3) return {};
    const xs = pts.map((p) => Date.parse(p.t));
    const ys = pts.map((p) => p.v);
    const n = xs.length;
    const mx = xs.reduce((a, b) => a + b, 0) / n;
    const my = ys.reduce((a, b) => a + b, 0) / n;
    const slope = xs.reduce((a, x, i) => a + (x - mx) * (ys[i] - my), 0) / (xs.reduce((a, x) => a + (x - mx) ** 2, 0) || 1);
    const last = xs[n - 1];
    const fut = Array.from({ length: 10 }, (_, i) => last + (i + 1) * 15 * 60_000);
    const proj = fut.map((t) => [new Date(t).toISOString(), Math.min(100, ys[n - 1] + slope * (t - last))]);
    return {
      tooltip: { valueFormatter: (v: number) => `${v.toFixed(1)}%` },
      yAxis: { min: (v: { min: number }) => Math.floor(v.min - 3), max: 100, axisLabel: { formatter: "{value}%" } },
      series: [
        { type: "line", name: "disk used", data: pts.map((p) => [p.t, p.v]), showSymbol: false, lineStyle: { color: "#9b8afb", width: 1.6 }, areaStyle: { color: "#9b8afb", opacity: 0.22 } },
        {
          type: "line", name: "forecast", data: [[pts[n - 1].t, ys[n - 1]], ...proj], showSymbol: false, lineStyle: { color: "#9b8afb", type: "dashed", width: 1.4 },
          areaStyle: { color: "#9b8afb", opacity: 0.08 },
          markLine: { symbol: "none", silent: true, data: [{ yAxis: 90, lineStyle: { color: cssVar("--poor"), type: "dashed" }, label: { formatter: "poor", position: "insideEndTop", fontSize: 10, color: cssVar("--text-3") } }] },
        },
      ],
    };
  }, [disk.data, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  const topList = useMemo(() => {
    const rows = (s?.latest ?? []).filter((r) => r.metric === "rtt").sort((a, b) => b.p95 - a.p95).slice(0, 7);
    const roleOf = new Map(srcs.map((x) => [x.src, x.role]));
    return rows.map((r) => ({ ...r, role: roleOf.get(r.src), state: classify("rtt", r.p95) }));
  }, [s, srcs]);

  const noise = o?.noise;
  const raw = noise?.raw_alerts_24h ?? 0;
  const incs = noise?.incidents_24h ?? 0;
  const reduction = raw ? (1 - incs / raw) * 100 : 0;

  if (!o) return <div className="p-4"><Skeleton h={600} /></div>;

  return (
    <div className="mx-auto max-w-[1680px] space-y-4 p-4">
      {/* ================================================================ performance */}
      <Section title="Health & Performance">
        <div className="grid grid-cols-12 gap-2.5">
          <div className="col-span-12 grid grid-cols-2 gap-2.5 md:col-span-3 md:grid-cols-1">
            <KpiTile
              label="Open incidents" value={openInc.length} tone={incTone} spark={errSpark}
              sub={openInc.length ? `${worstPrio} highest · ${openInc[0].title.slice(0, 34)}` : "all clear · error log rate shown"}
              onClick={() => nav(openInc[0] ? `/incidents/${openInc[0].incident_id}` : "/incidents")}
            />
            <KpiTile
              label="Healthy components (%)" value={healthyPct} decimals={1} unit="%" spark={paySpark}
              tone={healthyPct >= 90 ? "good" : healthyPct >= 75 ? "warn" : "bad"}
              sub={`${o.states.poor} poor · ${o.states.fair} fair · ${total} monitored`}
              onClick={() => nav("/infrastructure")}
            />
          </div>
          <Panel
            className="col-span-12 md:col-span-6"
            title="Components by health"
            subtitle="pseudonymous IDs only — real names stay on-site"
            right={
              <Tabs
                value={groupBy}
                onChange={setGroupBy}
                options={[
                  { v: "role", label: "role" }, { v: "collector_id", label: "site" }, { v: "kind", label: "kind" }, { v: "state", label: "health" },
                ]}
              />
            }
          >
            <HexMap sources={srcs} groupBy={groupBy} onSelect={(x) => openPanel({ kind: "source", id: x.src })} dark={theme === "dark"} />
          </Panel>
          <div className="col-span-12 flex flex-col gap-2.5 md:col-span-3">
            <Panel title="Slowest paths (latency p95)" subtitle="links and services, last window">
              <div className="space-y-1.5 pt-1">
                {topList.map((r) => (
                  <button key={r.src} onClick={() => openPanel({ kind: "source", id: r.src })} className="grid w-full grid-cols-[60px_1fr] items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-panel-2">
                    <span className="mono text-right text-[12px] font-semibold tnum" style={{ color: STATE_COLOR[r.state] }}>{fmtMetric("rtt", r.p95)}</span>
                    <div className="min-w-0">
                      <div className="flex items-center justify-between gap-2 text-[11.5px]">
                        <Pseudo src={r.src} className="border-0 bg-transparent px-0" />
                        <span className="truncate text-t3">{r.role}</span>
                      </div>
                      <Bar value={r.p95} max={topList[0]?.p95 ?? 1} color={STATE_COLOR[r.state]} />
                    </div>
                  </button>
                ))}
                {!topList.length && <Empty>No latency data yet</Empty>}
              </div>
            </Panel>
            <Panel title="Ingest throughput" subtitle="metadata records / bucket">
              <EChart
                height={92}
                option={{
                  grid: { top: 6, bottom: 0, left: 0, right: 0 },
                  xAxis: { show: false }, yAxis: { show: false },
                  series: [{ type: "bar", data: (s?.payloads ?? []).map((p) => [p.t, p.records]), itemStyle: { color: "#4f8ff7", borderRadius: [2, 2, 0, 0] }, barCategoryGap: "25%" }],
                }}
              />
            </Panel>
          </div>
        </div>
      </Section>

      {/* ================================================================ security */}
      <Section title="Security">
        <div className="grid grid-cols-12 gap-2.5">
          <Panel className="col-span-12 lg:col-span-5" title="Security signals" subtitle="edge detections (L0) by severity" right={<Link to="/security" className="text-[11.5px] text-accent">open ›</Link>}>
            {s ? <EChart option={securityOpt} height={200} /> : <Skeleton h={200} />}
          </Panel>
          <Panel className="col-span-12 lg:col-span-4" title="Logs with sensitive data" subtitle="redacted on-site before transmit, by rule">
            {s ? <EChart option={redactOpt} height={200} /> : <Skeleton h={200} />}
          </Panel>
          <Panel className="col-span-12 lg:col-span-3" title="Findings by pattern" subtitle="last 30 min">
            <div className="grid grid-cols-[110px_1fr] items-center gap-2">
              <EChart option={donutOpt} height={120} base={false} />
              <table className="w-full text-[11.5px]">
                <tbody>
                  {patterns.slice(0, 5).map((p, i) => (
                    <tr key={p.name}>
                      <td className="py-0.5">
                        <span className="mr-1.5 inline-block h-2 w-2 rounded-sm" style={{ background: ["#e5484d", "#f97316", "#f5a524", "#60a5fa", "#9b8afb"][i % 5] }} />
                        {p.name.replace(/_/g, " ")}
                      </td>
                      <td className="text-right font-semibold tnum">{p.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-2">
              <KpiTile
                className="min-h-[86px]" label="Security signals (24h)" value={o.findings_24h} spark={secSpark}
                tone={patterns.some((p) => p.name === "success_after_failures" || p.name === "spray") ? "warn" : "calm"} onClick={() => nav("/security")}
              />
            </div>
          </Panel>
        </div>
      </Section>

      {/* ================================================================ privacy & trust */}
      <Section title="Privacy & Trust">
        <div className="grid grid-cols-12 gap-2.5">
          <Panel className="col-span-12 lg:col-span-5" title="Privacy pipeline" subtitle="what happened to every log line before it left the site">
            {o ? <EChart option={funnelOpt} height={210} base={false} /> : <Skeleton h={210} />}
          </Panel>
          <div className="col-span-12 grid grid-cols-2 gap-2.5 lg:col-span-4">
            <KpiTile label="Redactions (24h)" value={o.privacy.redactions_24h} tone="calm" spark={redSpark} format={(v) => fmtNum(Math.round(v))} sub={`${o.privacy.by_rule.length} rule types fired`} onClick={() => nav("/trust")} />
            <KpiTile
              label="Event → screen p95" value={(dashP95 ?? 0) / 1000} decimals={2} unit="s" spark={latSpark}
              tone={dashP95 === null ? "calm" : dashP95 <= dashTarget ? "brand" : "bad"} sub={`measured, target ≤ ${dashTarget / 1000} s`} onClick={() => nav("/slo")}
            />
            <KpiTile label="Raw fields received" value={0} tone="good" sub="schema allow-list + egress guard" onClick={() => nav("/trust")} />
            <KpiTile
              label="Alert latency p95" value={(o.slo.alert?.p95 ?? 0) / 1000} decimals={1} unit="s"
              tone={(o.slo.alert?.p95 ?? 0) <= alertTarget ? "brand" : "bad"} sub={`breach → on screen, target ≤ ${alertTarget / 1000} s`} onClick={() => nav("/slo")}
            />
          </div>
          <Panel className="col-span-12 lg:col-span-3" title="Collectors" subtitle="outbound-only, mTLS" right={<Link to="/fleet" className="text-[11.5px] text-accent">fleet ›</Link>}>
            <div className="space-y-2 pt-1">
              {o.fleet.map((c) => (
                <div key={c.collector_id} className="rounded-md border border-line p-2.5">
                  <div className="flex items-center justify-between">
                    <span className="mono font-semibold">{c.collector_id}</span>
                    <span className="inline-flex items-center gap-1 text-[11.5px] font-medium" style={{ color: c.state === "online" ? "var(--excellent)" : "var(--poor)" }}>
                      {c.state === "online" ? "●" : "■"} {c.state}
                    </span>
                  </div>
                  <div className="mt-1 flex justify-between text-[11.5px] text-t3">
                    <span>buffer {c.buffer_depth ?? 0}</span>
                    <span>heartbeat {ago(c.last_hb_event_ts)}</span>
                  </div>
                </div>
              ))}
              {!o.fleet.length && <Empty>No collector has connected yet</Empty>}
            </div>
          </Panel>
        </div>
      </Section>

      {/* ================================================================ AIOps */}
      <Section title="AIOps">
        <div className="grid grid-cols-12 gap-2.5">
          <Panel
            className="col-span-12 lg:col-span-6"
            title={anomalySrc ? <span className="inline-flex items-center gap-2">{METRIC_LABEL[anomalyMetric] ?? anomalyMetric} anomalies <Pseudo src={anomalySrc} /></span> : "Anomalies"}
            subtitle="grey band = learned baseline; pins = incident onset"
          >
            {anomalySrc ? <MetricChart src={anomalySrc} metric={anomalyMetric} height={200} color="#e05e8f" /> : <Empty>Waiting for data</Empty>}
          </Panel>
          <Panel className="col-span-12 lg:col-span-3" title="Disk forecast" subtitle={diskSrc ? <span>fastest-filling host <Pseudo src={diskSrc} /> · {fmtMetric("disk_hours_to_full", s?.disk_forecast[0]?.avg ?? null)}</span> : "linear projection"}>
            {disk.data ? <EChart option={diskOpt} height={200} /> : <Skeleton h={200} />}
          </Panel>
          <Panel className="col-span-12 lg:col-span-3" title="Noise reduction" subtitle="raw alerts correlated into incidents (24h)">
            <div className="flex h-[200px] flex-col justify-center gap-3">
              <div className="flex items-end justify-around">
                <div className="text-center">
                  <div className="kpi-number text-[38px] text-t2">{raw}</div>
                  <div className="text-[11.5px] text-t3">raw alerts</div>
                </div>
                <ArrowRight className="mb-5 text-t3" />
                <div className="text-center">
                  <div className="kpi-number text-[38px] text-accent">{incs}</div>
                  <div className="text-[11.5px] text-t3">incidents</div>
                </div>
              </div>
              <div className="mx-auto rounded-full bg-accent-soft px-3 py-1 text-[13px] font-bold text-accent">−{reduction.toFixed(0)}% noise</div>
              <div className="text-center text-[11.5px] text-t3">persistence · hysteresis · warm-up · graph correlation</div>
            </div>
          </Panel>
          <Panel className="col-span-12" title="Open incidents" subtitle="root cause, confidence and next action — click for the evidence" pad={false}>
            {openInc.length ? (
              <table className="table">
                <thead>
                  <tr>
                    <th className="w-12">Prio</th>
                    <th>Incident</th>
                    <th>Root cause</th>
                    <th className="w-28">Confidence</th>
                    <th className="w-20">Alerts</th>
                    <th className="w-28">Opened</th>
                    <th className="w-8" />
                  </tr>
                </thead>
                <tbody>
                  {openInc.map((i) => (
                    <tr key={i.incident_id} className="cursor-pointer" onClick={() => nav(`/incidents/${i.incident_id}`)}>
                      <td><PrioBadge p={i.priority} /></td>
                      <td>
                        <div className="font-medium">{i.title}</div>
                        <div className="mono text-[11px] text-t3">{i.incident_id} · {i.status}</div>
                      </td>
                      <td><Pseudo src={i.root_cause_src} /></td>
                      <td>
                        <div className="flex items-center gap-2">
                          <Bar value={i.confidence ?? 0} max={1} color="var(--accent)" />
                          <span className="tnum text-[12px]">{Math.round((i.confidence ?? 0) * 100)}%</span>
                        </div>
                      </td>
                      <td className="tnum">{i.alert_count}</td>
                      <td className="text-t2">{ago(i.opened_ts)}</td>
                      <td><Sparkles size={14} className="text-accent" /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No open incidents — {fmtMs(dashP95)} p95 to screen</Empty>
            )}
          </Panel>
        </div>
      </Section>
      <div className="pb-2 text-center text-[11px] text-t3">
        TrustLine · privacy-first observability · raw telemetry never leaves the site
      </div>
    </div>
  );
}
