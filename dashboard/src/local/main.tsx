import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useMutation, useQuery } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";
import { AlertTriangle, ArrowRight, CheckCircle2, Database, Fingerprint, HardDrive, Link2, Lock, Search, ShieldCheck, ShieldX, Upload } from "lucide-react";
import "../styles/index.css";
import { get, isDemo } from "./data";
import type { EgressDetail, EgressRow, OutboxRow, RawRow, Status } from "./data";
import { cx, fmtNum } from "@/lib/format";
import { Kv, Panel, Redacted } from "@/components/ui";

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1, placeholderData: (p: unknown) => p } } });
type Tab = "proof" | "raw" | "buffer" | "reid";

const STATUS_COLOR: Record<string, string> = { acked: "var(--excellent)", sealed: "var(--low)", blocked: "var(--poor)", rejected: "var(--high)" };

/** Raw line with the secret spans the redactor found highlighted. */
function RawLine({ r }: { r: RawRow }) {
  const parts: React.ReactNode[] = [];
  let at = 0;
  [...(r.spans ?? [])].sort((a, b) => a[0] - b[0]).forEach(([s, e, rule], i) => {
    if (s > at) parts.push(<span key={`t${i}`}>{r.body.slice(at, s)}</span>);
    parts.push(
      <mark key={`m${i}`} title={rule} className="rounded px-0.5" style={{ background: "color-mix(in srgb, var(--poor) 22%, transparent)", color: "var(--poor)" }}>
        {r.body.slice(s, e)}
      </mark>,
    );
    at = Math.max(at, e);
  });
  parts.push(<span key="end">{r.body.slice(at)}</span>);
  return <div className="mono py-1 text-[11.5px] leading-relaxed break-all">{parts}</div>;
}

function Json({ v }: { v: unknown }) {
  const text = JSON.stringify(v, null, 2);
  const parts = text.split(/(<[^>"]+>)/g);
  return (
    <pre className="mono max-h-[460px] overflow-auto rounded-md bg-panel-2 p-3 text-[11.5px] leading-relaxed">
      {parts.map((p, i) => (p.startsWith("<") ? <Redacted key={i} text={p} /> : <span key={i}>{p}</span>))}
    </pre>
  );
}

/** Blocked entries never got a payload_id (nothing was sealed) — key them by entry id. */
const keyOf = (r: EgressRow) => r.payload_id ?? `entry:${r.id}`;

/** One row per payload: newest status wins, counts come from the sealed entry. */
function byPayload(rows: EgressRow[]): EgressRow[] {
  const m = new Map<string, EgressRow>();
  for (const r of rows) {
    const k = keyOf(r);
    const p = m.get(k);
    if (!p) m.set(k, { ...r });
    else {
      p.records = Math.max(p.records ?? 0, r.records ?? 0);
      p.redactions = Math.max(p.redactions ?? 0, r.redactions ?? 0);
      p.seq = p.seq ?? r.seq;
    }
  }
  return [...m.values()];
}

const n0 = (v: number) => (Math.abs(v) < 1e4 ? Math.round(v).toLocaleString() : fmtNum(v, 1));

function Proof() {
  const [sel, setSel] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["egress"], queryFn: () => get<EgressRow[]>("/local/egress?limit=160"), refetchInterval: 2000 });
  const rows = useMemo(() => byPayload(list.data ?? []), [list.data]);
  const id = sel ?? (rows[0] ? keyOf(rows[0]) : null);
  const det = useQuery({ queryKey: ["egress", id], queryFn: () => get<EgressDetail>(id!.startsWith("entry:") ? `/local/egress/entry/${id!.slice(6)}` : `/local/egress/${id}`), enabled: id !== null, refetchInterval: 4000 });
  const verify = useMutation({
    mutationFn: () => get<{ ok: boolean; checked: number; broken_at: number | null; why?: string }>("/local/egress/verify", { method: "POST" }),
    onSuccess: (r) => (r.ok ? toast.success(`Hash chain intact — ${fmtNum(r.checked, 0)} entries verified`) : toast.error(`Chain broken at entry ${r.broken_at}: ${r.why}`)),
  });
  const leak = useMutation({
    mutationFn: () => get("/local/debug/leak-field", { method: "POST" }),
    onSuccess: () => toast("Leak test armed", { description: "the next record carries a raw field — the policy stage must BLOCK it" }),
  });
  const d = det.data;
  const status = d?.history?.length ? d.history[d.history.length - 1].status : d?.status;
  return (
    <div className="grid grid-cols-[360px_1fr] gap-3">
      <Panel
        title="Egress log"
        subtitle="every payload that left (or was stopped), hash-chained"
        right={
          <button className="btn h-7" onClick={() => verify.mutate()}>
            <Link2 size={13} /> Verify chain
          </button>
        }
        pad={false}
      >
        <div className="max-h-[calc(100vh-230px)] overflow-y-auto">
          {rows.map((e) => (
            <button key={keyOf(e)} onClick={() => setSel(keyOf(e))} className={cx("flex w-full items-center gap-2 border-l-2 px-3 py-1.5 text-left hover:bg-panel-2", id === keyOf(e) ? "border-boundary bg-panel-2" : "border-transparent")}>
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: STATUS_COLOR[e.status] ?? "var(--stale)" }} />
              <span className="min-w-0 flex-1">
                <span className="mono block truncate text-[11.5px]">{e.payload_id ? `${e.payload_id.slice(0, 18)}…` : <span className="text-poor">blocked before sealing · {e.detail}</span>}</span>
                <span className="block truncate text-[11px] text-t3">
                  {e.status} · seq {e.seq ?? "—"} · {e.records ?? 0} rec · {e.redactions ?? 0} redacted
                </span>
              </span>
              <span className="text-[11px] text-t3">{new Date(e.ts * 1000).toLocaleTimeString([], { hour12: false })}</span>
            </button>
          ))}
        </div>
        <div className="border-t border-line p-2">
          <button className="btn w-full justify-center text-poor" onClick={() => leak.mutate()}>
            <AlertTriangle size={13} /> Arm leak test (inject a raw field)
          </button>
        </div>
      </Panel>
      {d ? (
        <div className="min-w-0 space-y-3">
          <div className="panel flex flex-wrap items-center gap-3 p-3">
            <span className="rounded-full px-2.5 py-0.5 text-[12px] font-bold text-white" style={{ background: STATUS_COLOR[status ?? ""] ?? "var(--stale)" }}>
              {status?.toUpperCase()}
            </span>
            <span className="mono text-[12px]">{d.payload_id ?? `egress entry #${d.id} (never sealed)`}</span>
            {d.detail && <span className="text-[12px] text-t2">{d.detail}</span>}
            <span className="ml-auto mono text-[11px] text-t3" title="SHA-256 of the exact gzip body sent — compare with the SaaS Trust Center lookup">
              {d.sha256 ? `sha256 ${d.sha256.slice(0, 24)}…` : "no bytes sent"}
            </span>
          </div>
          <div className="grid gap-3 xl:grid-cols-[1fr_auto_1fr]">
            <Panel title={<span className="inline-flex items-center gap-1.5"><HardDrive size={14} /> Raw on-site (never leaves)</span>} subtitle="red = detected secret / PII spans">
              <div className="max-h-[460px] divide-y divide-line overflow-auto">
                {d.raw_sources.map((r) => <RawLine key={r.id} r={r} />)}
                {!d.raw_sources.length && <div className="py-3 text-[12px] text-t3">metrics-only payload (no raw lines referenced)</div>}
              </div>
            </Panel>
            <div className="hidden items-center xl:flex">
              <div className="flex flex-col items-center gap-1 text-[10.5px] font-semibold text-boundary">
                <ArrowRight size={22} />
                redact
                <br />
                pseudonymise
                <br />
                aggregate
              </div>
            </div>
            <Panel title={<span className="inline-flex items-center gap-1.5"><Upload size={14} /> Exactly what left (trustline.v1)</span>} subtitle="‹secret› redacted · ‹u:/ip:/h:› HMAC pseudonyms">
              {d.json ? <Json v={d.json} /> : status === "blocked" ? <div className="rounded-md bg-poor/10 p-3 text-[12.5px] text-poor">Nothing left the site — this payload was blocked: {d.detail}</div> : <div className="text-[12px] text-t3">payload body not retained</div>}
            </Panel>
          </div>
          {d.history && (
            <Panel title="Chain entries for this payload">
              {d.history.map((h) => (
                <div key={h.id} className="mono flex gap-3 py-0.5 text-[11.5px]">
                  <span className="w-14 text-t3">#{h.id}</span>
                  <span className="w-16" style={{ color: STATUS_COLOR[h.status] }}>{h.status}</span>
                  <span className="text-t3">prev {h.prev_hash.slice(0, 12)} → entry {h.entry_hash.slice(0, 12)}</span>
                  <span className="ml-auto text-t3">{h.detail}</span>
                </div>
              ))}
            </Panel>
          )}
        </div>
      ) : (
        <div className="panel flex items-center justify-center p-10 text-t3">No payloads yet</div>
      )}
    </div>
  );
}

function RawVault() {
  const [q, setQ] = useState("");
  const [secrets, setSecrets] = useState(true);
  const { data } = useQuery({ queryKey: ["raw", q, secrets], queryFn: () => get<RawRow[]>(`/local/raw?limit=200&secrets=${secrets}${q ? `&q=${encodeURIComponent(q)}` : ""}`), refetchInterval: 3000 });
  return (
    <Panel title="Raw vault" subtitle="original lines, kept on-site for the retention window (default 24 h) — the SaaS cannot read this">
      <div className="mb-2 flex gap-2">
        <input className="input flex-1" placeholder="search raw text" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="flex items-center gap-2 text-[12px]">
          <input type="checkbox" checked={secrets} onChange={(e) => setSecrets(e.target.checked)} /> only lines with secrets
        </label>
      </div>
      <div className="max-h-[calc(100vh-260px)] divide-y divide-line overflow-auto">
        {(data ?? []).map((r) => (
          <div key={r.id} className="flex gap-3">
            <span className="mono w-14 shrink-0 py-1 text-[11px] text-t3">{r.source}</span>
            <RawLine r={r} />
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Buffer({ s }: { s?: Status }) {
  const { data } = useQuery({ queryKey: ["outbox"], queryFn: () => get<OutboxRow[]>("/local/outbox?limit=60"), refetchInterval: 2000 });
  return (
    <div className="grid gap-3 lg:grid-cols-[340px_1fr]">
      <Panel title="Store-and-forward">
        <Kv k="SaaS reachable" v={s?.saas.reachable ? <span className="text-excellent">yes</span> : <span className="text-poor">no — buffering</span>} />
        <Kv k="buffered payloads" v={s?.outbox.depth ?? 0} />
        <Kv k="buffered bytes" v={fmtNum(s?.outbox.bytes ?? 0, 1)} />
        <Kv k="oldest pending" v={`${s?.outbox.oldest_age_s ?? 0} s`} />
        <Kv k="retrying" v={s?.outbox.retrying ?? 0} />
        <Kv k="last acked seq" v={s?.outbox.last_acked_seq ?? 0} mono />
        <Kv k="fail streak" v={s?.saas.fail_streak ?? 0} />
        {s?.saas.last_error && <div className="mt-2 rounded bg-poor/10 p-2 text-[11.5px] text-poor">{s.saas.last_error}</div>}
        <div className="mt-2 text-[11.5px] text-t3">Live lane sends new data first; backlog replays oldest-first at 50/s. Retries are byte-identical and deduplicated by payload_id.</div>
      </Panel>
      <Panel title="Outbox" pad={false}>
        <table className="table">
          <thead>
            <tr>
              <th>Seq</th>
              <th>Payload</th>
              <th>Kinds</th>
              <th className="text-right">Records</th>
              <th>Status</th>
              <th className="text-right">Attempts</th>
              <th>Ack</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((o) => (
              <tr key={o.payload_id}>
                <td className="mono">{o.seq}</td>
                <td className="mono text-t2">{o.payload_id.slice(0, 18)}…</td>
                <td className="text-t2">{o.kinds}</td>
                <td className="text-right tnum">{o.records}</td>
                <td style={{ color: o.status === "pending" ? "var(--fair)" : "var(--excellent)" }}>{o.status}</td>
                <td className="text-right tnum">{o.attempts}</td>
                <td className="text-t3">{o.ack_status ?? o.last_error ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function Reidentify() {
  const [q, setQ] = useState("");
  const { data } = useQuery({ queryKey: ["reid", q], queryFn: () => get<{ code: string; real: string; prefix: string }[] | { code: string; real: string }>(`/local/reidentify?${q.includes(":") ? `code=${encodeURIComponent(q)}` : `q=${encodeURIComponent(q)}`}`) });
  const rows = Array.isArray(data) ? data : data ? [data] : [];
  return (
    <Panel title="Re-identify" subtitle="map a pseudonym seen in the SaaS back to the real name — possible only here, with the site's HMAC key">
      <div className="mb-3 flex gap-2">
        <Search size={16} className="mt-2 text-t3" />
        <input className="input mono flex-1" placeholder="paste a code like h:9c1e2f8a0b3d, or search a name" value={q} onChange={(e) => setQ(e.target.value.trim())} />
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Pseudonym (what the SaaS sees)</th>
            <th>Real value (on-site only)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code}>
              <td className="mono"><Fingerprint size={12} className="mr-1 inline text-pseudo" />{r.code}</td>
              <td className="mono font-semibold text-boundary">{r.real}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function App() {
  const [tab, setTab] = useState<Tab>("proof");
  const [theme, setTheme] = useState<"light" | "dark">("dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const st = useQuery({ queryKey: ["status"], queryFn: () => get<Status>("/local/status"), refetchInterval: 2000 });
  const s = st.data;
  const c = (s?.counters ?? {}) as Record<string, number>;
  const tabs = useMemo(
    () => [
      { v: "proof" as Tab, label: "Egress proof", icon: <ShieldCheck size={15} /> },
      { v: "raw" as Tab, label: "Raw vault", icon: <Database size={15} /> },
      { v: "buffer" as Tab, label: "Buffer", icon: <HardDrive size={15} /> },
      { v: "reid" as Tab, label: "Re-identify", icon: <Fingerprint size={15} /> },
    ],
    [],
  );
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-8 shrink-0 items-center justify-center gap-2 bg-boundary text-[12px] font-bold text-white">
        <Lock size={13} /> ON-SITE · collector {s?.collector_id ?? "…"} · site {s?.site ?? "…"} · this page is bound to localhost and never reachable from the SaaS
        {isDemo() && <span className="rounded bg-white/25 px-1.5">DEMO DATA</span>}
      </div>
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-4">
        <span className="text-[15px] font-bold">TrustLine Collector</span>
        <nav className="flex gap-1">
          {tabs.map((t) => (
            <button key={t.v} onClick={() => setTab(t.v)} className={cx("inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[12.5px] font-medium", tab === t.v ? "bg-panel-3 text-t1" : "text-t3 hover:text-t1")}>
              {t.icon} {t.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-[12px]">
          {st.isError && <span className="text-poor">collector not reachable</span>}
          {s && (
            <span className="inline-flex items-center gap-1.5" style={{ color: s.saas.reachable ? "var(--excellent)" : "var(--poor)" }}>
              {s.saas.reachable ? <CheckCircle2 size={14} /> : <ShieldX size={14} />} SaaS {s.saas.reachable ? "reachable" : "unreachable"} · {s.region}
            </span>
          )}
          <button className="btn h-7" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>
      <div className="grid shrink-0 grid-cols-3 gap-2 border-b border-line bg-panel-2 px-4 py-2 md:grid-cols-6">
        {[
          ["raw lines in", c.raw_in], ["redactions", c.redactions_total], ["identities coded", c.identities_coded],
          ["payloads sealed", c.payloads_sealed], ["blocked at egress", c.blocked_total], ["acked by SaaS", c.acked],
        ].map(([k, v]) => (
          <div key={String(k)}>
            <div className="text-[11px] text-t3">{k}</div>
            <div className={cx("text-[17px] font-bold tnum", k === "redactions" && "text-redact", k === "blocked at egress" && Number(v) > 0 && "text-poor")}>{n0(Number(v ?? 0))}</div>
          </div>
        ))}
      </div>
      {/* redundancy removed before anything leaves the site: what came in vs what had to be sent */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-b border-line bg-panel px-4 py-1.5 text-[12px]">
        <span className="font-semibold text-t2">Data reduction</span>
        {[
          ["samples accepted", c.samples_in],
          ["duplicate deliveries", (c.dup_source_events ?? 0) + (c.dup_samples ?? 0)],
          ["failed verification", c.dropped_invalid],
          ["noise dropped", c.dropped_noise],
          ["unchanged windows kept on site", c.suppressed_unchanged],
          ["records sent", c.records_out],
        ].map(([k, v]) => (
          <span key={String(k)} className="text-t3">
            {k} <b className="tnum text-t1">{n0(Number(v ?? 0))}</b>
          </span>
        ))}
        {Number(c.records_out) > 0 && (
          <span className="ml-auto font-semibold text-excellent">
            {(Number(c.raw_in ?? 0) / Number(c.records_out)).toFixed(1)}× fewer records than raw input
          </span>
        )}
      </div>
      <main className="min-h-0 flex-1 overflow-y-auto bg-bg p-4">
        {tab === "proof" && <Proof />}
        {tab === "raw" && <RawVault />}
        {tab === "buffer" && <Buffer s={s} />}
        {tab === "reid" && <Reidentify />}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <App />
      <Toaster position="bottom-right" richColors />
    </QueryClientProvider>
  </StrictMode>,
);
