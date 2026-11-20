import { useState } from "react";
import { useSearchParams } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Database, KeyRound, Lock, Play, Search, Server, ShieldCheck, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { cx, fmtNum, hhmmss } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { Kv, Panel, Section } from "@/components/ui";

interface Isolation {
  tenant: string; db_role: string; role_bypasses_rls: boolean; force_rls: boolean; sql: string; other_tenant_rows: number;
  rows_visible_total: number; rows_own_tenant: number; rows_without_tenant_context: number;
  policies: { tablename: string; policyname: string; qual: string }[]; pass: boolean; ms: number;
}
interface Residency {
  regions: { region: string; connected: boolean; tenants: { tenant_id: string; metrics: number | null; events?: number; findings?: number }[] }[];
  home_regions: Record<string, string>;
}
interface AuditRes {
  payload_id: string; home_region: string; metric_rows: number;
  attempts: { result: string; attempt: number; lane: string; received_at: string; sha256: string; region: string; seq: number }[];
  stored: { seq: number; records: number; redactions?: number; received_at: string } | null;
}

/** The boundary diagram: the only arrow that crosses the trust line is metadata over mTLS. */
function Boundary({ o }: { o?: Overview }) {
  const hop = (icon: React.ReactNode, title: string, sub: string, tone = "var(--accent)") => (
    <div className="flex w-[150px] flex-col items-center gap-1 rounded-lg border border-line bg-panel p-2.5 text-center shadow-sm">
      <span className="flex h-8 w-8 items-center justify-center rounded-full" style={{ background: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}>{icon}</span>
      <div className="text-[12px] font-semibold">{title}</div>
      <div className="text-[11px] leading-tight text-t3">{sub}</div>
    </div>
  );
  const arrow = (label: string, hot = false) => (
    <div className="flex min-w-[70px] flex-1 flex-col items-center">
      <div className="text-[10.5px] whitespace-nowrap text-t3">{label}</div>
      <div className={cx("relative my-1 h-0.5 w-full", hot ? "bg-boundary" : "bg-line-strong")}>
        <span className="absolute -top-[3px] -right-0.5 h-0 w-0 border-y-4 border-l-[7px] border-y-transparent" style={{ borderLeftColor: hot ? "var(--boundary)" : "var(--border-strong)" }} />
        <span className="absolute -top-[3px] h-2 w-2 animate-[ping_2s_linear_infinite] rounded-full bg-accent-2/60" style={{ left: "10%" }} />
      </div>
    </div>
  );
  return (
    <div className="overflow-x-auto">
      <div className="flex min-w-[900px] items-stretch gap-0">
        <div className="flex items-center gap-1 rounded-l-xl border border-dashed border-line-strong bg-panel-2 p-3">
          {hop(<Server size={16} />, "Site sources", "logs · metrics · syslog", "var(--text-2)")}
          {arrow("raw, on-site")}
          {hop(<KeyRound size={16} />, "Collector", `redact · pseudonymise · aggregate · ${fmtNum(o?.privacy.redactions_24h ?? 0)} redactions`, "var(--redact)")}
        </div>
        <div className="relative flex w-[150px] flex-col items-center justify-center">
          <div className="absolute inset-y-0 left-1/2 w-1 -translate-x-1/2 rounded-full bg-boundary" />
          <div className="relative z-10 rounded-md bg-boundary px-2 py-1 text-[11px] font-bold text-white shadow">TRUST LINE</div>
          <div className="relative z-10 mt-2 w-full">{arrow("", true)}</div>
          <div className="relative z-10 mt-1 rounded bg-panel px-1.5 text-center text-[10.5px] font-medium text-boundary">mTLS 1.3 · metadata only</div>
        </div>
        <div className="flex flex-1 items-center gap-1 rounded-r-xl border border-line bg-accent-soft p-3">
          {hop(<Lock size={16} />, "nginx ingest", "client cert → tenant; residency check")}
          {arrow("schema allow-list")}
          {hop(<ShieldCheck size={16} />, "Queue + workers", "dedupe · health · AIOps")}
          {arrow("RLS per tenant")}
          {hop(<Database size={16} />, "Regional store", "home region only")}
        </div>
      </div>
    </div>
  );
}

export default function Trust() {
  const me = useStore((s) => s.me);
  const [params] = useSearchParams();
  const [pid, setPid] = useState(params.get("payload") ?? "");
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/overview"), refetchInterval: 5000 });
  const res = useQuery({ queryKey: ["residency"], queryFn: () => api<Residency>("/api/trust/residency"), refetchInterval: 15_000 });
  const iso = useMutation({ mutationFn: () => api<Isolation>("/api/trust/isolation-test", { method: "POST" }) });
  const aud = useQuery({
    queryKey: ["trust-audit", params.get("payload") ?? pid],
    queryFn: () => api<AuditRes>("/api/trust/audit", { query: { payload_id: pid } }),
    enabled: /^[0-9a-f-]{36}$/i.test(pid),
  });
  const r = iso.data;
  return (
    <div className="mx-auto max-w-[1680px] space-y-4 p-4">
      <Section title="Trust boundary">
        <Boundary o={ov.data} />
        <div className="mt-3 grid grid-cols-2 gap-2 text-[12px] md:grid-cols-4">
          {[
            ["Raw fields received", "0", "text-excellent"],
            ["Redactions (24h)", fmtNum(ov.data?.privacy.redactions_24h ?? 0), "text-redact"],
            ["Payloads (24h)", fmtNum(ov.data?.privacy.payloads_24h ?? 0), "text-t1"],
            ["Blocked at egress", String(ov.data?.privacy.blocked_total ?? 0), "text-t1"],
          ].map(([k, v, c]) => (
            <div key={k} className="panel p-3">
              <div className="text-t3">{k}</div>
              <div className={cx("text-[22px] font-bold tnum", c)}>{v}</div>
            </div>
          ))}
        </div>
      </Section>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Tenant isolation test" subtitle="runs a deliberately cross-tenant query as YOUR database role">
          <button className="btn btn-primary" onClick={() => iso.mutate()} disabled={iso.isPending}>
            <Play size={14} /> {iso.isPending ? "Running…" : "Run isolation test"}
          </button>
          {r && (
            <div className="rise mt-3 space-y-2">
              <div className={cx("flex items-center gap-2 rounded-md px-3 py-2 text-[14px] font-bold", r.pass ? "bg-excellent/12 text-excellent" : "bg-poor/12 text-poor")}>
                {r.pass ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
                {r.pass ? "PASS — other tenants' rows are invisible" : "FAIL"}
                <span className="ml-auto text-[11px] font-normal text-t3">{r.ms} ms</span>
              </div>
              <pre className="mono overflow-x-auto rounded-md bg-panel-2 p-2.5 text-[11.5px]">{r.sql}</pre>
              <Kv k="rows from other tenants" v={<b className="text-excellent">{r.other_tenant_rows}</b>} />
              <Kv k="rows with no tenant context (fails closed)" v={<b className="text-excellent">{r.rows_without_tenant_context}</b>} />
              <Kv k="rows visible = rows of own tenant" v={`${fmtNum(r.rows_visible_total, 1)} = ${fmtNum(r.rows_own_tenant, 1)}`} />
              <Kv k="database role" v={<span className="mono">{r.db_role} (BYPASSRLS: {String(r.role_bypasses_rls)}, FORCE RLS: {String(r.force_rls)})</span>} />
              <div className="text-[11.5px] text-t3">Policy: <span className="mono">{r.policies[0]?.qual}</span></div>
            </div>
          )}
        </Panel>

        <Panel title="Data residency" subtitle={`your home region: ${me?.region ?? "—"}`}>
          <div className="grid grid-cols-2 gap-3">
            {(res.data?.regions ?? []).map((g) => {
              const own = g.tenants.find((t) => t.tenant_id === me?.tenant);
              return (
                <div key={g.region} className={cx("rounded-lg border p-3", own ? "border-accent bg-accent-soft" : "border-line")}>
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{g.region}</span>
                    <span className="text-[11px] text-t3">{g.connected ? "connected" : "offline"}</span>
                  </div>
                  <div className="mt-2 text-[12px]">
                    {own ? (
                      <>
                        <div className="text-[22px] font-bold tnum">{fmtNum(own.metrics ?? 0, 1)}</div>
                        <div className="text-t3">your metric rows stored here</div>
                      </>
                    ) : (
                      <>
                        <div className="text-[22px] font-bold text-t3 tnum">0</div>
                        <div className="text-t3">of your rows here {g.tenants.length ? "· other tenants exist (hidden)" : ""}</div>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 text-[12px] text-t2">A payload sent to the wrong region is rejected with <span className="mono">403 residency_denied</span> before any byte is stored.</div>
        </Panel>
      </div>

      <Panel title="Payload provenance lookup" subtitle="paste a payload_id from the collector's on-site Egress Proof — the SHA-256 must match">
        <div className="flex gap-2">
          <input className="input mono flex-1" placeholder="0193a1c2-…" value={pid} onChange={(e) => setPid(e.target.value.trim())} />
          <button className="btn" onClick={() => aud.refetch()}>
            <Search size={14} /> Look up
          </button>
        </div>
        {aud.data && (
          <div className="rise mt-3 grid gap-3 md:grid-cols-2">
            <div>
              <Kv k="home region" v={aud.data.home_region} />
              <Kv k="stored seq" v={aud.data.stored?.seq ?? "—"} mono />
              <Kv k="records" v={aud.data.stored?.records ?? "—"} />
              <Kv k="metric rows from this payload" v={aud.data.metric_rows} />
            </div>
            <div className="space-y-1.5">
              {aud.data.attempts.map((a, i) => (
                <div key={i} className="rounded-md border border-line p-2 text-[12px]">
                  <div className="flex justify-between">
                    <span className={cx("font-semibold", a.result === "stored" ? "text-excellent" : "text-low")}>{a.result}</span>
                    <span className="text-t3">attempt {a.attempt} · {a.lane} · {hhmmss(a.received_at)}</span>
                  </div>
                  <div className="mono mt-0.5 truncate text-[11px] text-t3">sha256 {a.sha256}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}
