import { Link, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Check, CheckCircle2, CircleDashed, CornerUpLeft, Cpu, Eye, EyeOff, GitBranch, Lightbulb, Radar, Shield, Undo2, X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago, cx, fmtMetric, hhmmss, METRIC_LABEL, shortSrc } from "@/lib/format";
import type { IncidentDetail, Remediation } from "@/lib/types";
import { MetricChart } from "@/charts/MetricChart";
import { Panel, PrioBadge, Pseudo, Skeleton } from "@/components/ui";

const STEP_ICON: Record<string, React.ReactNode> = {
  detect: <Radar size={14} />,
  corroborate: <Cpu size={14} />,
  correlate: <GitBranch size={14} />,
  root_cause: <Lightbulb size={14} />,
  playbook: <Shield size={14} />,
  policy: <Shield size={14} />,
};

function ConfidenceRing({ value }: { value: number }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <svg width="92" height="92" viewBox="0 0 92 92">
      <circle cx="46" cy="46" r={r} fill="none" stroke="var(--panel-3)" strokeWidth="9" />
      <circle
        cx="46" cy="46" r={r} fill="none" stroke="var(--accent)" strokeWidth="9" strokeLinecap="round"
        strokeDasharray={`${c * value} ${c}`} transform="rotate(-90 46 46)" style={{ transition: "stroke-dasharray 800ms ease" }}
      />
      <text x="46" y="51" textAnchor="middle" fontSize="20" fontWeight="700" fill="var(--text-1)">
        {Math.round(value * 100)}%
      </text>
    </svg>
  );
}

const FLOW: { key: Remediation["state"] | "policy"; label: string; hint: string }[] = [
  { key: "proposed", label: "Proposed", hint: "playbook action for the root cause" },
  { key: "policy", label: "Policy check", hint: "reversible · blast radius · role" },
  { key: "approved", label: "Approved", hint: "human in the loop (tenant-admin)" },
  { key: "dispatched", label: "Pulled by collector", hint: "outbound mTLS — no inbound port on site" },
  { key: "executed", label: "Executed on-site", hint: "local allow-list enforced again" },
  { key: "verified", label: "Verified", hint: "root cause healthy for 3 windows" },
];
const ORDER = ["proposed", "policy", "approved", "dispatched", "executed", "verified"];

function Stepper({ r }: { r: Remediation }) {
  const reached = r.state === "rolled_back" ? 5 : r.state === "failed" ? 4 : ORDER.indexOf(r.state === "proposed" ? "policy" : r.state);
  const ts: Record<string, string | null | undefined> = {
    proposed: r.proposed_ts, policy: r.proposed_ts, approved: r.approved_ts, dispatched: r.dispatched_ts, executed: r.executed_ts, verified: r.verified_ts,
  };
  return (
    <ol className="relative space-y-3 pl-1">
      {FLOW.map((s, i) => {
        const done = i <= reached && r.state !== "rejected" && r.state !== "refused";
        const current = i === reached + 1 && !["verified", "rejected", "refused", "rolled_back", "failed"].includes(r.state);
        return (
          <li key={s.key} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span
                className={cx("flex h-6 w-6 items-center justify-center rounded-full border-2 text-[11px] transition-colors duration-500", done ? "border-accent bg-accent text-white" : current ? "border-accent text-accent" : "border-line-strong text-t3")}
              >
                {done ? <Check size={13} /> : current ? <CircleDashed size={13} className="animate-spin" /> : i + 1}
              </span>
              {i < FLOW.length - 1 && <span className={cx("mt-1 h-4 w-0.5", done ? "bg-accent" : "bg-line")} />}
            </div>
            <div className="min-w-0 pb-1">
              <div className={cx("text-[12.5px] font-semibold", !done && !current && "text-t3")}>{s.label}</div>
              <div className="text-[11.5px] text-t3">
                {s.hint}
                {done && ts[s.key] ? ` · ${hhmmss(ts[s.key]!)}` : ""}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default function IncidentPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const me = useStore((s) => s.me);
  const openPanel = useStore((s) => s.openPanel);
  const { data, isError, error } = useQuery({ queryKey: ["incident", id], queryFn: () => api<IncidentDetail>(`/api/incidents/${id}`), refetchInterval: 2500 });
  const refresh = () => qc.invalidateQueries({ queryKey: ["incident", id] });
  const act = useMutation({
    mutationFn: (v: { path: string; label: string }) => api<{ state?: string; status?: string; checks?: unknown[] }>(v.path, { method: "POST" }),
    onSuccess: (r, v) => {
      toast.success(`${v.label}: ${r.state ?? r.status ?? "ok"}`);
      refresh();
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
    onError: (e: Error, v) => toast.error(`${v.label} failed`, { description: e.message }),
  });

  if (isError) return <div className="p-6 text-poor">{(error as Error).message}</div>;
  if (!data) return <div className="p-4"><Skeleton h={600} /></div>;
  const inc = data.incident;
  const ev = inc.evidence;
  const admin = me?.role === "tenant-admin";
  const rem = data.remediations[data.remediations.length - 1];
  const chartSignals = ev.signals.filter((s) => s.category !== "auth" && METRIC_LABEL[s.metric]).slice(0, 2);
  const steps = ev.trace;
  const rc = steps.find((s) => s.step === "root_cause");

  return (
    <div className="mx-auto max-w-[1680px] space-y-3 p-4">
      <Link to="/incidents" className="inline-flex items-center gap-1 text-[12px] text-t3 hover:text-t1">
        <ArrowLeft size={13} /> Incidents
      </Link>
      <div className="panel flex flex-wrap items-center gap-3 p-4">
        <PrioBadge p={inc.priority} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[18px] font-bold">{inc.title}</h1>
          <div className="text-[12px] text-t3">
            <span className="mono">{inc.incident_id}</span> · opened {ago(inc.opened_ts)} · first breach {hhmmss(inc.breach_ts)} · {inc.alert_count} alerts correlated ·{" "}
            <b className={inc.status === "resolved" ? "text-excellent" : "text-poor"}>{inc.status}</b>
          </div>
        </div>
        {inc.status === "open" && (
          <button className="btn" onClick={() => act.mutate({ path: `/api/incidents/${id}/ack`, label: "Acknowledge" })}>
            <Eye size={14} /> Acknowledge
          </button>
        )}
        {inc.status !== "resolved" && admin && (
          <button className="btn" onClick={() => act.mutate({ path: `/api/incidents/${id}/resolve`, label: "Resolve" })}>
            <CheckCircle2 size={14} /> Resolve
          </button>
        )}
      </div>

      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-12 space-y-3 xl:col-span-8">
          <Panel title="What happened">
            <div className="flex items-start gap-4">
              <ConfidenceRing value={ev.confidence} />
              <div className="min-w-0 flex-1 space-y-2">
                <p className="text-[15px] leading-snug font-medium">{ev.why}.</p>
                <div className="flex flex-wrap items-center gap-2 text-[12px]">
                  <span className="text-t3">Root cause</span>
                  <button onClick={() => openPanel({ kind: "source", id: ev.root_cause })}>
                    <Pseudo src={ev.root_cause} role={ev.root_role} />
                  </button>
                  <span className="text-t3">· impacted</span>
                  {ev.affected.slice(0, 6).map((a) => (
                    <button key={a} onClick={() => openPanel({ kind: "source", id: a })}>
                      <Pseudo src={a} />
                    </button>
                  ))}
                </div>
                <div className="text-[12px] text-t3">
                  Layers agreeing: {ev.layers_agreeing.map((l) => <span key={l} className="mono mr-1 rounded bg-accent-soft px-1.5 py-px text-accent">{l}</span>)} · blast radius {ev.blast_radius} · rule <span className="mono">{ev.rule}</span>
                </div>
              </div>
            </div>
          </Panel>

          {chartSignals.length > 0 && (
            <div className={cx("grid gap-3", chartSignals.length > 1 ? "md:grid-cols-2" : "")}>
              {chartSignals.map((s, i) => (
                <Panel key={s.src + s.metric} title={<span className="inline-flex items-center gap-2">{METRIC_LABEL[s.metric] ?? s.metric} <Pseudo src={s.src} /></span>} subtitle={`z = ${s.z ?? "—"} vs baseline ${fmtMetric(s.metric, s.baseline)}`}>
                  <MetricChart src={s.src} metric={s.metric} height={170} color={i ? "#f2994a" : "#e05e8f"} />
                </Panel>
              ))}
            </div>
          )}

          <Panel title="Evidence" subtitle="each signal's weight = |z| / Σ|z| (security: risk)" pad={false}>
            <table className="table">
              <thead>
                <tr>
                  <th>Signal</th>
                  <th>Component</th>
                  <th className="text-right">Value</th>
                  <th className="text-right">Baseline</th>
                  <th className="text-right">z</th>
                  <th>Layer</th>
                  <th>Onset</th>
                  <th className="w-40">Weight</th>
                </tr>
              </thead>
              <tbody>
                {ev.signals.map((s) => (
                  <tr key={s.src + s.metric}>
                    <td className="font-medium">{METRIC_LABEL[s.metric] ?? s.metric.replace(/_/g, " ")} <span className="text-t3">{s.stat}</span></td>
                    <td><Pseudo src={s.src} /></td>
                    <td className="text-right tnum">{fmtMetric(s.metric, s.value)}</td>
                    <td className="text-right tnum text-t3">{fmtMetric(s.metric, s.baseline)}</td>
                    <td className="text-right tnum font-semibold">{s.z ?? "—"}</td>
                    <td className="mono">{s.layer}</td>
                    <td className="mono text-t3">{hhmmss(s.onset_ts)}</td>
                    <td>
                      <div className="flex items-center gap-2">
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel-3">
                          <div className="h-full rounded-full bg-accent transition-[width] duration-700" style={{ width: `${s.weight * 100}%` }} />
                        </div>
                        <span className="w-9 text-right tnum text-[12px]">{Math.round(s.weight * 100)}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <div className="grid gap-3 md:grid-cols-2">
            <Panel title="Reasoning trace" subtitle="every step is reproducible from stored signals">
              <ol className="space-y-2.5">
                {steps.map((s, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">{STEP_ICON[s.step] ?? <Radar size={14} />}</span>
                    <div className="min-w-0 text-[12.5px]">
                      <div className="font-semibold capitalize">
                        {s.step.replace("_", " ")} {s.layer && <span className="mono ml-1 rounded bg-panel-3 px-1 text-[10.5px] font-normal text-t2">{s.layer}</span>}
                      </div>
                      <div className="text-t2">
                        {s.detail ??
                          (s.step === "root_cause" ? <>chose <span className="mono">{shortSrc(s.chosen)}</span> — the member with no anomalous dependency</> : null) ??
                          null}
                        {s.step === "playbook" && (
                          <>
                            rule <span className="mono">{s.rule}</span> matched {s.matched?.join(", ")}
                          </>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </Panel>
            <div className="space-y-3">
              <Panel title="Rejected root-cause hypotheses">
                {rc?.rejected?.length ? (
                  <div className="space-y-1.5">
                    {rc.rejected.map((r) => (
                      <div key={r.src} className="flex items-start gap-2 text-[12px]">
                        <X size={14} className="mt-0.5 shrink-0 text-poor" />
                        <Pseudo src={r.src} />
                        <span className="text-t2">{r.why.replace(/(svc|link|h|ip):[0-9a-f]{12}/g, (m) => shortSrc(m))}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-[12px] text-t3">single-component incident</div>
                )}
              </Panel>
              <Panel title="Why we did NOT alert" subtitle="suppressed and absorbed alerts — the noise you didn't see">
                <div className="space-y-1.5">
                  {ev.suppressed.map((s, i) => (
                    <div key={i} className="flex items-center gap-2 text-[12px]">
                      <EyeOff size={13} className="shrink-0 text-t3" />
                      <Pseudo src={s.src} />
                      <span className="text-t2">{s.metric}</span>
                      <span className="ml-auto truncate text-t3">{s.reason}</span>
                    </div>
                  ))}
                  {!ev.suppressed.length && <div className="text-[12px] text-t3">nothing suppressed</div>}
                </div>
                <div className="mt-2 rounded-md bg-accent-soft px-2.5 py-1.5 text-[12px] font-medium text-accent">
                  {ev.noise.raw_alerts} raw alerts → 1 incident
                </div>
              </Panel>
            </div>
          </div>
        </div>

        <div className="col-span-12 space-y-3 xl:col-span-4">
          <Panel title="Confidence" subtitle="0.5·magnitude + 0.3·agreement + 0.2·persistence">
            <div className="flex h-4 overflow-hidden rounded-full bg-panel-3">
              <div className="bg-[#5b6cf0]" style={{ width: `${ev.confidence_parts.magnitude * 100}%` }} title="magnitude" />
              <div className="bg-[#14b8a6]" style={{ width: `${ev.confidence_parts.agreement * 100}%` }} title="agreement" />
              <div className="bg-[#f2994a]" style={{ width: `${ev.confidence_parts.persistence * 100}%` }} title="persistence" />
            </div>
            <div className="mt-2 grid grid-cols-3 text-[11.5px]">
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-[#5b6cf0]" />magnitude {ev.confidence_parts.magnitude}</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-[#14b8a6]" />agreement {ev.confidence_parts.agreement}</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-[#f2994a]" />persistence {ev.confidence_parts.persistence}</span>
            </div>
          </Panel>

          <Panel title="Recommended action" subtitle={`playbook · requires ${ev.action.requires_role}`}>
            <div className="rounded-md border border-accent/30 bg-accent-soft p-3">
              <div className="text-[14px] font-semibold">{ev.action.text}</div>
              <div className="mt-1 text-[11.5px] text-t2">
                <span className="mono">{ev.action.key}</span> · {ev.action.reversible ? `reversible (${ev.action.rollback_key})` : "not reversible"} · max blast radius {ev.action.max_blast_radius ?? "—"}
              </div>
            </div>
            <div className="mt-3">
              {!rem && ev.action.executable && inc.status !== "resolved" && (
                <button className="btn btn-primary w-full justify-center" disabled={act.isPending} onClick={() => act.mutate({ path: `/api/incidents/${id}/remediations`, label: "Propose" })}>
                  Propose remediation
                </button>
              )}
              {!ev.action.executable && <div className="text-[12px] text-t3">This rule recommends investigation only — nothing to execute.</div>}
              {rem && (
                <div className="space-y-3">
                  <Stepper r={rem} />
                  {rem.policy?.checks && (
                    <div className="space-y-1 rounded-md border border-line p-2.5">
                      {rem.policy.checks.map((c) => (
                        <div key={c.check} className="flex items-center gap-2 text-[11.5px]">
                          {c.ok ? <Check size={13} className="text-excellent" /> : <X size={13} className="text-poor" />}
                          <span className="font-medium">{c.check.replace(/_/g, " ")}</span>
                          <span className="ml-auto truncate text-t3">{c.detail}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    {rem.state === "proposed" && admin && (
                      <>
                        <button className="btn btn-primary flex-1 justify-center" onClick={() => act.mutate({ path: `/api/remediations/${rem.action_id}/approve`, label: "Approve" })}>
                          <Check size={14} /> Approve
                        </button>
                        <button className="btn flex-1 justify-center" onClick={() => act.mutate({ path: `/api/remediations/${rem.action_id}/reject`, label: "Reject" })}>
                          <X size={14} /> Reject
                        </button>
                      </>
                    )}
                    {rem.state === "proposed" && !admin && <div className="text-[12px] text-t3">Waiting for a tenant-admin to approve.</div>}
                    {["executed", "verified", "failed"].includes(rem.state) && admin && ev.action.reversible && (
                      <button className="btn flex-1 justify-center" onClick={() => act.mutate({ path: `/api/remediations/${rem.action_id}/rollback`, label: "Rollback" })}>
                        <Undo2 size={14} /> Roll back
                      </button>
                    )}
                    {rem.state === "rolled_back" && (
                      <div className="inline-flex items-center gap-1.5 text-[12px] text-t2">
                        <CornerUpLeft size={13} /> rolled back — inverse action dispatched
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </Panel>

          <Panel title="Audit trail" subtitle="append-only, per tenant">
            <div className="space-y-2">
              {data.audit.map((a, i) => (
                <div key={i} className="flex gap-2 text-[12px]">
                  <span className="mono w-16 shrink-0 text-t3">{hhmmss(a.at)}</span>
                  <div className="min-w-0">
                    <div className="font-medium">{a.action}</div>
                    <div className="truncate text-t3">{a.actor}</div>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
