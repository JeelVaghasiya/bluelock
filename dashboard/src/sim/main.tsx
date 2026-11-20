import { StrictMode, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";
import {
  Activity, AppWindow, BookOpen, CheckCircle2, Clock, Cloud, Cpu, EyeOff, FlaskConical, Gauge, Laptop, Link2, Network, Play, Radio,
  Server, ShieldAlert, ShieldCheck, Square, Swords, Trash2, Truck, Wrench, X, XCircle, Zap,
} from "lucide-react";
import "../styles/index.css";
import { cx, fmtNum } from "@/lib/format";
import { sim } from "./api";
import type { BoundaryResult, Catalogue, CollectorStatus, FeedLine, LivePoint, Scenario, SimState, SlaCheck, SlaStatus, Story } from "./api";

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 0, placeholderData: (p: unknown) => p } } });
type Tab = "scenarios" | "stories" | "sla" | "boundary" | "live";
const SITE = "acme";

const CAT: Record<string, { color: string; icon: ReactNode }> = {
  Infrastructure: { color: "#4f8ff7", icon: <Server size={15} /> },
  Network: { color: "#14b8a6", icon: <Network size={15} /> },
  Application: { color: "#6366f1", icon: <AppWindow size={15} /> },
  Security: { color: "#ef4444", icon: <ShieldAlert size={15} /> },
  Privacy: { color: "#8b5cf6", icon: <EyeOff size={15} /> },
  Delivery: { color: "#f59e0b", icon: <Truck size={15} /> },
};
const DEFAULT_TARGET: Record<string, string> = { host: "api-01", link: "uplink-1", svc: "api", rule: "github_token" };

const store = {
  get: (k: string) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* private mode */
    }
  },
};

// ------------------------------------------------------------------ live data hooks
function useLive() {
  const [pts, setPts] = useState<LivePoint[]>([]);
  useEffect(() => {
    let since = 0;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const r = await sim<{ points: LivePoint[] }>(`/live?site=${SITE}&since=${since}`);
        if (r.points.length) {
          since = r.points[r.points.length - 1].t;
          setPts((p) => [...p, ...r.points].slice(-300));
        }
      } catch {
        /* simulator restarting */
      }
      if (!stop) timer = window.setTimeout(tick, 1000);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);
  return pts;
}

function useFeed() {
  const [lines, setLines] = useState<FeedLine[]>([]);
  useEffect(() => {
    let after = 0;
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const r = await sim<FeedLine[]>(`/feed?site=${SITE}&after=${after}`);
        if (r.length) {
          after = r[r.length - 1].id;
          setLines((p) => [...r.reverse(), ...p].slice(0, 160));
        }
      } catch {
        /* ignore */
      }
      if (!stop) timer = window.setTimeout(tick, 1000);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);
  return lines;
}

// ------------------------------------------------------------------ small pieces
function Spark({ data, color, max, h = 34, warn }: { data: (number | null)[]; color: string; max?: number; h?: number; warn?: number }) {
  const vals = data.map((v) => (v === null || v === undefined || Number.isNaN(v) ? null : v));
  const nums = vals.filter((v): v is number => v !== null);
  if (nums.length < 2) return <div style={{ height: h }} />;
  const hi = max ?? Math.max(...nums) * 1.15;
  const lo = max !== undefined ? 0 : Math.min(...nums) * 0.85;
  const span = hi - lo || 1;
  const n = vals.length;
  const y = (v: number) => h - ((Math.min(v, hi) - lo) / span) * (h - 2) - 1;
  let d = "";
  vals.forEach((v, i) => {
    if (v === null) return;
    d += `${d ? "L" : "M"}${((i / (n - 1)) * 100).toFixed(2)},${y(v).toFixed(2)}`;
  });
  return (
    <svg viewBox={`0 0 100 ${h}`} preserveAspectRatio="none" className="w-full" style={{ height: h }}>
      {warn !== undefined && warn < hi && <line x1="0" x2="100" y1={y(warn)} y2={y(warn)} stroke="var(--poor)" strokeDasharray="2 2" strokeWidth="0.6" vectorEffect="non-scaling-stroke" opacity="0.7" />}
      <path d={`${d} L100,${h} L0,${h} Z`} fill={color} opacity="0.12" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Card({ title, subtitle, right, children, className, pad = true }: { title?: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={cx("panel flex min-w-0 flex-col", className)}>
      {(title || right) && (
        <header className="flex items-center gap-2 px-3.5 pt-3 pb-2">
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold">{title}</div>
            {subtitle && <div className="text-[11.5px] text-t3">{subtitle}</div>}
          </div>
          {right}
        </header>
      )}
      <div className={cx("min-w-0 flex-1", pad && "px-3.5 pb-3.5")}>{children}</div>
    </section>
  );
}

const n0 = (v: number) => (Math.abs(v) < 1e4 ? Math.round(v).toLocaleString() : fmtNum(v, 1));

const ago = (ts: number) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`;
};

// ------------------------------------------------------------------ pipeline strip
function Pipeline({ col, pts, state }: { col?: CollectorStatus; pts: LivePoint[]; state?: SimState }) {
  const rate = pts.length ? pts[pts.length - 1].n : 0;
  const st = state?.sites[SITE];
  const devicesOk = st ? st.push_errors === 0 || !st.last_error : false;
  const colOk = !!col?.reachable;
  const outage = col?.outage_until ? Math.max(0, Math.round(col.outage_until - Date.now() / 1000)) : 0;
  const saasOk = colOk && col?.saas?.reachable !== false && !outage;
  const c = col?.counters ?? {};
  const node = (icon: ReactNode, title: string, sub: string, ok: boolean, stats: [string, ReactNode][]) => (
    <div className={cx("relative z-10 flex min-w-[220px] flex-1 items-center gap-3 rounded-xl border bg-panel p-3", ok ? "border-line" : "border-poor/60")}>
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg" style={{ background: ok ? "color-mix(in srgb, var(--excellent) 16%, transparent)" : "color-mix(in srgb, var(--poor) 18%, transparent)", color: ok ? "var(--excellent)" : "var(--poor)" }}>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[13px] font-semibold">
          {title}
          <span className={cx("h-1.5 w-1.5 rounded-full", ok ? "live-dot bg-excellent" : "bg-poor")} />
        </div>
        <div className="truncate text-[11px] text-t3">{sub}</div>
        <div className="mt-1 flex gap-3">
          {stats.map(([k, v]) => (
            <div key={k} className="min-w-0">
              <div className="text-[10px] tracking-wide text-t3 uppercase">{k}</div>
              <div className="text-[14px] font-bold tnum">{v}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
  const pipe = (ok: boolean, label: string, hot = false) => (
    <div className="relative flex w-16 shrink-0 flex-col items-center justify-center lg:w-24">
      <div className={cx("text-center text-[10px] font-semibold", ok ? (hot ? "text-boundary" : "text-t3") : "text-poor")}>{label}</div>
      <div className={cx("sim-pipe mt-1 h-1 w-full rounded-full", ok ? (hot ? "bg-boundary/30" : "bg-accent/25") : "bg-poor/40")}>
        {ok && <span className={cx("sim-dot", hot && "hot")} />}
      </div>
    </div>
  );
  return (
    <div className="flex items-stretch overflow-x-auto px-4 py-3">
      {node(<Radio size={20} />, "Simulated devices", "5 hosts · 2 uplinks · 3 services · sshd", devicesOk, [
        ["records/s", rate],
        ["faults", st?.faults.length ?? 0],
        ["push errors", st?.push_errors ?? 0],
      ])}
      {pipe(devicesOk && colOk, "raw · on-site")}
      {node(<Cpu size={20} />, `Collector ${col?.collector_id ?? ""}`, colOk ? "redact · pseudonymise · aggregate" : "not reachable on :9090", colOk, [
        ["raw in", n0(c.raw_in ?? 0)],
        ["redacted", <span className="text-redact">{n0(c.redactions_total ?? 0)}</span>],
        ["blocked", <span className={cx((c.blocked_total ?? 0) > 0 && "text-poor")}>{c.blocked_total ?? 0}</span>],
        ["buffer", <span className={cx((col?.outbox?.depth ?? 0) > 0 && "text-fair")}>{col?.outbox?.depth ?? 0}</span>],
      ])}
      {pipe(saasOk, outage ? `OUTAGE ${outage}s` : "mTLS 1.3 · metadata", true)}
      {node(<Cloud size={20} />, "TrustLine SaaS", col?.ingest_base ?? "…", saasOk, [
        ["acked", n0(c.acked ?? 0)],
        ["state", outage ? "simulated outage" : col?.saas?.reachable ? "reachable" : col?.saas?.reachable === false ? "unreachable" : "—"],
      ])}
    </div>
  );
}

// ------------------------------------------------------------------ scenarios
function ScenarioCard({ s, active }: { s: Scenario; active?: { remaining_s: number; target: string | null }[] }) {
  const qcl = useQueryClient();
  const cat = CAT[s.category] ?? CAT.Application;
  const [target, setTarget] = useState(s.targets.includes(DEFAULT_TARGET[s.target_type ?? ""]) ? DEFAULT_TARGET[s.target_type ?? ""] : s.targets[0] ?? "");
  const [mag, setMag] = useState<number>(s.magnitude ?? 1);
  const [dur, setDur] = useState<number>(s.duration_s ?? 0);
  const fire = useMutation({
    mutationFn: () => sim<Record<string, unknown>>("/inject", { method: "POST", body: { site: SITE, kind: s.kind, target: target || undefined, magnitude: s.magnitude !== null ? mag : undefined, duration_s: s.duration_s ? dur : undefined } }),
    onSuccess: () => {
      toast.success(`${s.title} fired${target ? ` on ${target}` : ""}`, { description: s.expect });
      qcl.invalidateQueries({ queryKey: ["state"] });
    },
    onError: (e) => toast.error(`${s.title} failed`, { description: String((e as Error).message) }),
  });
  const isActive = (active?.length ?? 0) > 0;
  const tunable = s.magnitude !== null && s.magnitude > 0 && !["secret_in_log", "success_after_failures"].includes(s.kind);
  const step = s.magnitude && s.magnitude < 1 ? 0.01 : s.magnitude && s.magnitude < 10 ? 0.5 : 1;
  return (
    <div className={cx("sim-card panel relative flex flex-col overflow-hidden p-3.5", isActive && "sim-active")} style={{ ["--cat" as string]: cat.color }}>
      <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: cat.color }} />
      <div className="flex items-start gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${cat.color} 16%, transparent)`, color: cat.color }}>
          {cat.icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13.5px] font-semibold">{s.title}</span>
            {s.source === "collector" && <span className="rounded bg-boundary/15 px-1.5 text-[10px] font-semibold text-boundary">collector</span>}
            {isActive && (
              <span className="ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-bold text-white" style={{ background: cat.color }}>
                <Zap size={10} /> LIVE {active![0].remaining_s > 0 ? `${active![0].remaining_s}s` : ""}
              </span>
            )}
          </div>
          <div className="mono text-[10.5px] text-t3">{s.kind}</div>
        </div>
      </div>
      <p className="mt-2 text-[12px] leading-snug text-t2">{s.what}</p>
      <div className="mt-2 rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-[11.5px] leading-snug text-t2">
        <span className="font-semibold text-t1">Expect in TrustLine: </span>
        {s.expect}
      </div>
      <div className="mt-auto space-y-2 pt-3">
        {s.targets.length > 0 && (
          <label className="flex items-center gap-2 text-[11.5px]">
            <span className="w-16 text-t3">{s.target_type === "rule" ? "secret" : s.target_type}</span>
            <select className="input h-7 flex-1 py-0 text-[12px]" value={target} onChange={(e) => setTarget(e.target.value)}>
              {s.targets.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
        )}
        {tunable && (
          <label className="flex items-center gap-2 text-[11.5px]">
            <span className="w-16 text-t3">magnitude</span>
            <input type="range" className="flex-1" min={step} max={(s.magnitude ?? 1) * 3} step={step} value={mag} onChange={(e) => setMag(Number(e.target.value))} style={{ accentColor: cat.color }} />
            <span className="mono w-12 text-right">{mag}</span>
          </label>
        )}
        {!!s.duration_s && (
          <label className="flex items-center gap-2 text-[11.5px]">
            <span className="w-16 text-t3">duration</span>
            <input type="range" className="flex-1" min={5} max={Math.max(900, s.duration_s * 2)} step={5} value={dur} onChange={(e) => setDur(Number(e.target.value))} style={{ accentColor: cat.color }} />
            <span className="mono w-12 text-right">{dur}s</span>
          </label>
        )}
        <button className="btn h-8 w-full justify-center font-semibold text-white" style={{ background: cat.color, borderColor: cat.color }} disabled={fire.isPending} onClick={() => fire.mutate()}>
          <Zap size={14} /> {fire.isPending ? "Firing…" : "Fire"}
        </button>
      </div>
    </div>
  );
}

function Scenarios({ cat, state }: { cat: Catalogue; state?: SimState }) {
  const [filter, setFilter] = useState<string>(store.get("sim_cat") ?? "All");
  const cats = ["All", ...Object.keys(CAT).filter((c) => cat.scenarios.some((s) => s.category === c))];
  const faults = state?.sites[SITE]?.faults ?? [];
  const list = cat.scenarios.filter((s) => filter === "All" || s.category === filter);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {cats.map((c) => (
          <button
            key={c}
            onClick={() => {
              setFilter(c);
              store.set("sim_cat", c);
            }}
            className={cx("inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-[12px] font-medium", filter === c ? "border-transparent text-white" : "border-line text-t2 hover:text-t1")}
            style={filter === c ? { background: CAT[c]?.color ?? "var(--accent)" } : undefined}
          >
            {CAT[c]?.icon} {c}
            <span className="opacity-70">{c === "All" ? cat.scenarios.length : cat.scenarios.filter((s) => s.category === c).length}</span>
          </button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
        {list.map((s) => (
          <ScenarioCard key={s.kind} s={s} active={faults.filter((f) => f.kind === s.kind)} />
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ stories
function useStoryControls() {
  const qcl = useQueryClient();
  const start = useMutation({
    mutationFn: (id: string) => sim(`/stories/${id}/start`, { method: "POST" }),
    onSuccess: () => qcl.invalidateQueries({ queryKey: ["state"] }),
    onError: (e) => toast.error(String((e as Error).message)),
  });
  const stop = useMutation({ mutationFn: () => sim("/stories/stop", { method: "POST" }), onSuccess: () => qcl.invalidateQueries({ queryKey: ["state"] }) });
  return { start, stop };
}

function RunCard({ run }: { run: NonNullable<SimState["story"]> }) {
  const { stop } = useStoryControls();
  const total = Math.max(1, ...run.steps.map((s) => s.at));
  return (
        <Card
          title={<span className="inline-flex items-center gap-2">{!run.finished && <span className="live-dot h-2 w-2 rounded-full bg-excellent" />}{run.title}</span>}
          subtitle={run.finished ? "finished" : `running · T+${Math.round(run.elapsed_s)} s`}
          right={!run.finished && <button className="btn h-7" onClick={() => stop.mutate()}><Square size={12} /> Stop</button>}
        >
          <div className="relative mb-3 h-1.5 rounded-full bg-panel-3">
            <div className="absolute inset-y-0 left-0 rounded-full bg-accent transition-[width] duration-1000" style={{ width: `${Math.min(100, (run.elapsed_s / total) * 100)}%` }} />
          </div>
          <div className="grid gap-1.5 md:grid-cols-2">
            {run.steps.map((s, i) => (
              <div key={i} className={cx("flex items-start gap-2 rounded-md px-2 py-1.5 text-[12px]", s.state === "pending" ? "text-t3" : "bg-panel-2")}>
                {s.state === "done" ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-excellent" /> : s.state === "error" ? <XCircle size={14} className="mt-0.5 shrink-0 text-poor" /> : <Clock size={14} className="mt-0.5 shrink-0" />}
                <span className="mono w-14 shrink-0 text-t3">T+{s.at}s</span>
                <span className="min-w-0">
                  <span className={cx(s.state !== "pending" && "text-t1")}>{s.label}</span>
                  {s.result?.detail && <span className="block truncate text-[11px] text-t3">{s.result.detail}</span>}
                </span>
              </div>
            ))}
          </div>
        </Card>
  );
}

function Stories({ cat, state }: { cat: Catalogue; state?: SimState }) {
  const run = state?.story;
  const { start } = useStoryControls();
  return (
    <div className="space-y-3">
      {run && <RunCard run={run} />}
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {cat.stories.filter((s: Story) => !s.sla).map((s: Story) => (
          <div key={s.id} className="panel flex flex-col p-3.5">
            <div className="flex items-center gap-2">
              <BookOpen size={16} className="text-accent" />
              <span className="text-[14px] font-semibold">{s.title}</span>
              <span className="ml-auto text-[11px] text-t3">~{s.minutes} min · {s.steps.length} steps</span>
            </div>
            <p className="mt-1.5 text-[12px] text-t2">{s.blurb}</p>
            <ol className="mt-2 space-y-0.5 text-[11.5px] text-t3">
              {s.steps.slice(0, 6).map((st, i) => (
                <li key={i} className="flex gap-2">
                  <span className="mono w-12 shrink-0">T+{st.at}s</span>
                  <span className="truncate">{st.label}</span>
                </li>
              ))}
              {s.steps.length > 6 && <li className="pl-14">+{s.steps.length - 6} more</li>}
            </ol>
            <button className="btn btn-primary mt-auto h-8 justify-center" style={{ marginTop: 12 }} disabled={start.isPending} onClick={() => start.mutate(s.id)}>
              <Play size={13} /> {run && !run.finished && run.id === s.id ? "Restart" : "Run story"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ SLA benchmarks
const fmtMs = (v: number | null | undefined) => (v == null ? "—" : v >= 10_000 ? `${(v / 1000).toFixed(1)} s` : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);

function Verdict({ pass }: { pass: boolean | null }) {
  return (
    <span className={cx("rounded px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-wide", pass === true ? "bg-excellent/15 text-excellent" : pass === false ? "bg-poor/15 text-poor" : "bg-panel-3 text-t3")}>
      {pass === true ? "pass" : pass === false ? "fail" : "pending"}
    </span>
  );
}

function SaasLink({ s }: { s?: SlaStatus }) {
  const qcl = useQueryClient();
  const [url, setUrl] = useState(s?.url ?? "");
  const [email, setEmail] = useState(s?.email ?? "admin@acme.test");
  const [password, setPassword] = useState("");
  const save = useMutation({
    mutationFn: () => sim("/saas", { method: "POST", body: { url, email, password: password || null } }),
    onSuccess: () => {
      toast.success("SaaS linked");
      qcl.invalidateQueries({ queryKey: ["sla"] });
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });
  return (
    <Card title={<span className="inline-flex items-center gap-2"><Link2 size={15} /> Link the SaaS dashboard</span>} subtitle="the Lab logs in as a tenant user and reads the SaaS's own measurements (read-only)">
      <form
        className="grid gap-2 md:grid-cols-[2fr_1fr_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <input className="input h-8" placeholder="https://xxxx.trycloudflare.com" value={url} onChange={(e) => setUrl(e.target.value)} />
        <input className="input h-8" placeholder="admin@acme.test" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="input h-8" type="password" placeholder="password (default: DEMO_PASSWORD)" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        <button className="btn btn-primary h-8 justify-center" disabled={!url || save.isPending}>Connect</button>
      </form>
      {s?.error && <div className="mt-2 text-[12px] text-poor">{s.error}</div>}
      <div className="mt-2 text-[11.5px] text-t3">
        Tip: start the site with <span className="mono">native.py site --ingest … --saas https://xxxx.trycloudflare.com</span> to link it automatically.
      </div>
    </Card>
  );
}

function CheckTile({ c, story, onRun, busy }: { c: SlaCheck; story?: Story; onRun: (id: string) => void; busy: boolean }) {
  const pct = c.measured != null && c.unit === "ms" ? Math.min(100, (c.measured / c.target) * 100) : null;
  return (
    <div className="panel flex flex-col p-3.5">
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1 text-[13px] font-semibold">{c.title}</span>
        <Verdict pass={c.pass} />
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-[26px] font-bold tabular-nums">{c.unit === "ms" ? fmtMs(c.measured) : (c.measured ?? "—")}</span>
        <span className="text-[12px] text-t3">target {c.unit === "ms" ? `≤ ${fmtMs(c.target)}` : `${c.target} ${c.unit}`}</span>
      </div>
      {pct != null && (
        <div className="mt-1.5 h-1.5 rounded-full bg-panel-3">
          <div className={cx("h-full rounded-full", c.pass !== false ? "bg-excellent" : "bg-poor")} style={{ width: `${pct}%` }} />
        </div>
      )}
      <p className="mt-2 text-[11.5px] text-t2">{c.detail}</p>
      {story && (
        <button className="btn mt-auto h-7 justify-center" style={{ marginTop: 10 }} disabled={busy} onClick={() => onRun(story.id)}>
          <Play size={12} /> Run: {story.title.split(" ->")[0]}
        </button>
      )}
    </div>
  );
}

function Sla({ cat, state }: { cat: Catalogue; state?: SimState }) {
  const q = useQuery({ queryKey: ["sla"], queryFn: () => sim<SlaStatus>("/sla?minutes=15"), refetchInterval: 5000 });
  const { start } = useStoryControls();
  const s = q.data;
  const run = state?.story;
  const slaStories = cat.stories.filter((x: Story) => x.sla);
  const byId: Record<string, Story> = Object.fromEntries(cat.stories.map((x: Story) => [x.id, x]));
  const passed = s?.checks?.filter((c) => c.pass === true).length ?? 0;
  return (
    <div className="space-y-3">
      {(!s?.configured || s.reachable === false) && <SaasLink key={s?.url ?? "new"} s={s} />}
      {s?.configured && s.reachable && (
        <div className="flex flex-wrap items-center gap-3 text-[12px] text-t3">
          <span className="inline-flex items-center gap-1.5">
            <Cloud size={13} className="text-excellent" /> measured by the SaaS at <span className="mono text-t2">{s.url}</span> as {s.email} · last {s.minutes} min
          </span>
          <span className="ml-auto font-semibold text-t1">
            {passed}/{s.checks?.length ?? 0} SLAs passing
          </span>
        </div>
      )}
      {run && run.id.startsWith("sla_") && <RunCard run={run} />}
      {s?.checks && (
        <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
          {s.checks.map((c) => (
            <CheckTile key={c.id} c={c} story={c.scenario ? byId[c.scenario] : undefined} onRun={(id) => start.mutate(id)} busy={start.isPending} />
          ))}
        </div>
      )}
      {s?.checks && (
        <div className="grid gap-3 lg:grid-cols-3">
          <Card title="Collectors, as the SaaS sees them" subtitle="heartbeat every 5 s · silent >45 s → offline">
            <div className="space-y-1.5">
              {(s.collectors ?? []).map((c) => (
                <div key={c.collector_id} className="flex items-center gap-2 text-[12px]">
                  <span className={cx("h-2 w-2 rounded-full", c.state === "online" ? "bg-excellent" : c.state === "offline" ? "bg-poor" : "bg-fair")} />
                  <span className="mono">{c.collector_id}</span>
                  <span className={cx("font-semibold", c.state === "offline" ? "text-poor" : "text-t1")}>{c.state}</span>
                  <span className="ml-auto truncate text-t3">{c.state_reason ?? `acked ${c.last_acked_seq ?? "—"}/${c.last_assigned_seq ?? "—"}`}</span>
                </div>
              ))}
              <div className="pt-1 text-[12px] text-t2">
                <b className={cx((s.stale_sources ?? 0) > 0 ? "text-fair" : "text-t1")}>{s.stale_sources}</b> of {s.sources} sources STALE
              </div>
            </div>
          </Card>
          <Card title="Alert-storm control" subtitle="raw alerts vs incidents opened, last 15 min">
            <div className="flex items-baseline gap-2">
              <span className="text-[26px] font-bold tabular-nums">{s.noise?.alerts ?? 0}</span>
              <span className="text-[12px] text-t3">alerts →</span>
              <span className="text-[26px] font-bold tabular-nums">{s.noise?.incidents ?? 0}</span>
              <span className="text-[12px] text-t3">incidents</span>
              {s.noise?.reduction != null && <span className="ml-auto text-[13px] font-semibold text-excellent">−{Math.round(s.noise.reduction * 100)}% noise</span>}
            </div>
            <div className="mt-2 space-y-1">
              {(s.recent_incidents ?? []).slice(0, 4).map((i) => (
                <div key={i.incident_id} className="flex items-center gap-2 text-[11.5px]">
                  <span className="mono text-t3">{i.priority}</span>
                  <span className="truncate">{i.title}</span>
                  <span className="ml-auto shrink-0 text-t3">{i.alert_count} alerts</span>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Where the latency goes" subtitle="average per hop, live lane (ms)">
            <div className="space-y-1">
              {Object.entries(s.hops_ms ?? {}).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2 text-[12px]">
                  <span className="w-24 text-t3">{k.replace("_", " ")}</span>
                  <div className="h-1.5 flex-1 rounded-full bg-panel-3">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(100, ((v ?? 0) / 5000) * 100)}%` }} />
                  </div>
                  <span className="mono w-16 text-right">{v == null ? "—" : Math.round(v)}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        {slaStories.map((x: Story) => (
          <div key={x.id} className="panel flex flex-col p-3.5">
            <div className="flex items-center gap-2">
              <Gauge size={16} className="text-accent" />
              <span className="text-[14px] font-semibold">{x.title}</span>
              <span className="ml-auto text-[11px] text-t3">~{x.minutes} min</span>
            </div>
            <p className="mt-1.5 text-[12px] text-t2">{x.blurb}</p>
            <button className="btn btn-primary mt-auto h-8 justify-center" style={{ marginTop: 12 }} disabled={start.isPending} onClick={() => start.mutate(x.id)}>
              <Play size={13} /> {run && !run.finished && run.id === x.id ? "Restart" : "Run"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ trust boundary
function Boundary({ cat, state }: { cat: Catalogue; state?: SimState }) {
  const qcl = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const runOne = async (t: string) => {
    setBusy(t);
    try {
      const r = await sim<BoundaryResult>(`/boundary/${t}`, { method: "POST" });
      (r.passed ? toast.success : toast.error)(`${r.title}: ${r.passed ? "handled as designed" : r.error ? "could not run" : "UNEXPECTED"}`, { description: r.detail.slice(0, 160) });
    } catch (e) {
      toast.error(String((e as Error).message));
    }
    setBusy(null);
    qcl.invalidateQueries({ queryKey: ["state"] });
  };
  const runAll = async () => {
    for (const b of cat.boundary) await runOne(b.test);
  };
  const res = state?.boundary ?? {};
  const passed = Object.values(res).filter((r) => r.passed).length;
  return (
    <div className="space-y-3">
      <Card
        title={<span className="inline-flex items-center gap-2"><Swords size={15} className="text-poor" /> Attack the SaaS ingest from this laptop</span>}
        subtitle={<>Every request goes to <span className="mono">{cat.ingest_base}</span> and must be refused (or, for a replay, deduplicated). The collector's own pipeline is untouched.</>}
        right={
          <button className="btn btn-danger h-8" disabled={!!busy} onClick={runAll}>
            <Play size={13} /> Run all {cat.boundary.length}
          </button>
        }
      >
        <div className="flex items-center gap-3 text-[12px]">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel-3">
            <div className="h-full rounded-full bg-excellent transition-[width]" style={{ width: `${(passed / cat.boundary.length) * 100}%` }} />
          </div>
          <span className="tnum font-semibold">{passed}/{cat.boundary.length} defended</span>
        </div>
      </Card>
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {cat.boundary.map((b) => {
          const r = res[b.test];
          return (
            <div key={b.test} className={cx("panel flex flex-col p-3.5", r && (r.passed ? "border-excellent/50" : "border-poor/60"))}>
              <div className="flex items-center gap-2">
                <span className="text-[13.5px] font-semibold">{b.title}</span>
                {r && (
                  <span className={cx("ml-auto rounded-full px-2 py-0.5 text-[10.5px] font-bold text-white", r.passed ? "bg-excellent" : "bg-poor")}>
                    {r.passed ? (b.test === "replay" ? "DEDUPED" : "REFUSED") : r.error ? "ERROR" : "ACCEPTED!"}
                  </span>
                )}
              </div>
              <div className="mt-1 text-[12px] text-t2">{b.what}</div>
              <div className="mt-1 text-[11.5px] text-t3">expected: <span className="text-t1">{b.expect}</span></div>
              {r && (
                <div className="mono mt-2 rounded-md bg-panel-2 p-2 text-[11px] break-all text-t2">
                  <span className={cx("font-bold", r.passed ? "text-excellent" : "text-poor")}>{r.status ?? "no HTTP"}</span> · {r.ms} ms · {ago(r.ts)}
                  <div className="mt-0.5 line-clamp-3">{r.detail}</div>
                </div>
              )}
              <button className="btn mt-auto h-8 justify-center" style={{ marginTop: 10 }} disabled={!!busy} onClick={() => runOne(b.test)}>
                <Swords size={13} /> {busy === b.test ? "Attacking…" : "Run attack"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ live devices
function Live({ cat, pts, state }: { cat: Catalogue; pts: LivePoint[]; state?: SimState }) {
  const site = cat.sites[SITE];
  const feed = useFeed();
  const faults = state?.sites[SITE]?.faults ?? [];
  const hit = (name: string) => faults.filter((f) => f.target === name);
  const series = <K extends "h" | "l" | "s">(k: K, name: string, i: number) => pts.map((p) => (p[k][name]?.[i] as number | null) ?? null);
  const last = <K extends "h" | "l" | "s">(k: K, name: string, i: number) => pts.length ? (pts[pts.length - 1][k][name]?.[i] as number | boolean | null) : null;
  const tile = (name: string, sub: string, rows: { label: string; data: (number | null)[]; color: string; max?: number; warn?: number; fmt: (v: number) => string }[], extra?: ReactNode) => {
    const f = hit(name);
    return (
      <div key={name} className={cx("panel p-3", f.length > 0 && "sim-active")} style={f.length ? { ["--cat" as string]: "var(--poor)" } : undefined}>
        <div className="flex items-center gap-2">
          <span className="mono text-[13px] font-semibold">{name}</span>
          <span className="text-[11px] text-t3">{sub}</span>
          {extra}
          {f.map((x) => (
            <span key={x.kind} className="ml-auto rounded-full bg-poor px-2 py-0.5 text-[10px] font-bold text-white">{x.kind}</span>
          ))}
        </div>
        <div className="mt-2 space-y-1.5">
          {rows.map((r) => {
            const v = r.data.length ? r.data[r.data.length - 1] : null;
            return (
              <div key={r.label} className="grid grid-cols-[52px_1fr_62px] items-center gap-2">
                <span className="text-[11px] text-t3">{r.label}</span>
                <Spark data={r.data} color={r.color} max={r.max} warn={r.warn} />
                <span className="mono text-right text-[12px] tnum">{v === null ? "—" : r.fmt(v)}</span>
              </div>
            );
          })}
        </div>
      </div>
    );
  };
  return (
    <div className="grid gap-3 2xl:grid-cols-[1fr_440px]">
      <div className="space-y-3">
        <div className="text-[12px] font-semibold tracking-wide text-t3 uppercase">Hosts</div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Object.entries(site.hosts).map(([h, role]) =>
            tile(h, role, [
              { label: "cpu", data: series("h", h, 0), color: "#4f8ff7", max: 100, warn: 85, fmt: (v) => `${v.toFixed(0)}%` },
              { label: "mem", data: series("h", h, 1), color: "#8b5cf6", max: 100, warn: 90, fmt: (v) => `${v.toFixed(1)}%` },
              { label: "disk", data: series("h", h, 2), color: "#f59e0b", max: 100, warn: 90, fmt: (v) => `${v.toFixed(1)}%` },
            ]),
          )}
        </div>
        <div className="text-[12px] font-semibold tracking-wide text-t3 uppercase">Uplinks & services</div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {site.links.map((l) =>
            tile(l, state?.sites[SITE]?.failed_over.includes(l) ? "failed over" : "probe", [
              { label: "rtt", data: series("l", l, 0), color: "#14b8a6", fmt: (v) => `${v.toFixed(0)} ms` },
              { label: "loss", data: series("l", l, 1), color: "#ef4444", max: 20, fmt: (v) => `${v.toFixed(1)}%` },
            ], last("l", l, 2) === false ? <span className="rounded bg-poor/15 px-1.5 text-[10px] font-bold text-poor">DOWN</span> : null),
          )}
          {Object.keys(site.services).map((s) =>
            tile(s, `service · via ${site.service_links[s] ?? "lan"}`, [
              { label: "errors", data: series("s", s, 0), color: "#ef4444", max: 0.5, warn: 0.05, fmt: (v) => `${(v * 100).toFixed(1)}%` },
              { label: "latency", data: series("s", s, 1), color: "#6366f1", fmt: (v) => `${v.toFixed(0)} ms` },
            ]),
          )}
        </div>
      </div>
      <Card title="Raw device stream" subtitle="what the collector receives - real names, secrets and all (never leaves the site)" pad={false}>
        <div className="mono max-h-[calc(100vh-330px)] min-h-[300px] overflow-y-auto px-3.5 pb-3 text-[11px] leading-relaxed">
          {feed.map((l) => (
            <div key={l.id} className={cx("rise flex gap-2 border-b border-line/50 py-0.5", l.tag === "attack" && "text-poor", l.level === "error" && "text-fair")}>
              <span className="w-[92px] shrink-0 truncate text-t3">{l.src}</span>
              <span className="min-w-0 break-all">{l.text}</span>
            </div>
          ))}
          {!feed.length && <div className="py-6 text-center text-t3">waiting for the simulator…</div>}
        </div>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ right rail
function Rail({ state }: { state?: SimState }) {
  const qcl = useQueryClient();
  const st = state?.sites[SITE];
  const clear = useMutation({
    mutationFn: (q: { kind?: string; target?: string | null }) => {
      const p = new URLSearchParams({ site: SITE });
      if (q.kind) p.set("kind", q.kind);
      if (q.target) p.set("target", q.target);
      return sim(`/clear?${p}`, { method: "POST" });
    },
    onSuccess: () => qcl.invalidateQueries({ queryKey: ["state"] }),
  });
  const kindColor: Record<string, string> = { inject: "var(--accent)", boundary: "var(--excellent)", collector: "var(--boundary)", story: "var(--redact)", clear: "var(--text-3)", action: "var(--excellent)" };
  const kindIcon: Record<string, ReactNode> = { inject: <Zap size={12} />, boundary: <ShieldCheck size={12} />, collector: <Cpu size={12} />, story: <BookOpen size={12} />, clear: <Trash2 size={12} />, action: <Wrench size={12} /> };
  return (
    <aside className="flex min-h-0 flex-col gap-3">
      <Card
        title={<span className="inline-flex items-center gap-1.5"><Activity size={14} /> Active faults</span>}
        right={st?.faults.length ? <button className="btn h-7 text-poor" onClick={() => clear.mutate({})}><Trash2 size={12} /> Clear all</button> : undefined}
      >
        {!st?.faults.length && <div className="py-2 text-[12px] text-t3">All quiet - the devices are healthy.</div>}
        <div className="space-y-2">
          {st?.faults.map((f, i) => {
            const total = f.elapsed_s + Math.max(0, f.remaining_s);
            return (
              <div key={i} className="rise rounded-md border border-line bg-panel-2 p-2">
                <div className="flex items-center gap-2 text-[12px]">
                  <span className="live-dot h-1.5 w-1.5 rounded-full bg-poor" />
                  <span className="font-semibold">{f.kind}</span>
                  {f.target && <span className="mono text-t3">{f.target}</span>}
                  <span className="ml-auto mono text-[11px] text-t3">{f.remaining_s > 0 ? `${f.remaining_s}s left` : "one-shot"}</span>
                  <button title="stop" className="text-t3 hover:text-poor" onClick={() => clear.mutate({ kind: f.kind, target: f.target })}>
                    <X size={13} />
                  </button>
                </div>
                {total > 0 && (
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-panel-3">
                    <div className="h-full rounded-full bg-poor transition-[width] duration-1000" style={{ width: `${Math.min(100, (f.elapsed_s / total) * 100)}%` }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {!!(st?.failed_over.length || st?.blocked_ips.length) && (
          <div className="mt-2 space-y-1 border-t border-line pt-2 text-[11.5px]">
            <div className="font-semibold text-excellent">Remediations in effect</div>
            {st?.failed_over.map((l) => <div key={l} className="mono text-t2">traffic failed over off {l}</div>)}
            {st?.blocked_ips.map((ip) => <div key={ip} className="mono text-t2">blocked {ip}</div>)}
          </div>
        )}
      </Card>
      <Card title={<span className="inline-flex items-center gap-1.5"><Clock size={14} /> Timeline</span>} subtitle="everything fired from this laptop, plus remediations the SaaS sent back" className="min-h-0 flex-1" pad={false}>
        <div className="max-h-[calc(100vh-420px)] min-h-[200px] space-y-0 overflow-y-auto px-3.5 pb-3">
          {state?.timeline.map((t, i) => (
            <div key={`${t.ts}-${i}`} className="relative flex gap-2.5 border-l border-line py-1.5 pl-3">
              <span className="absolute top-2 -left-[9px] flex h-[17px] w-[17px] items-center justify-center rounded-full text-white" style={{ background: t.ok ? kindColor[t.kind] ?? "var(--text-3)" : "var(--poor)" }}>
                {kindIcon[t.kind]}
              </span>
              <div className="min-w-0 flex-1 pl-1">
                <div className="flex gap-2 text-[12px]">
                  <span className="min-w-0 truncate font-medium">{t.title}</span>
                  <span className="ml-auto shrink-0 text-[10.5px] text-t3">{ago(t.ts)}</span>
                </div>
                {t.detail && <div className="truncate text-[11px] text-t3">{t.detail}</div>}
              </div>
            </div>
          ))}
          {!state?.timeline.length && <div className="py-3 text-[12px] text-t3">Nothing fired yet.</div>}
        </div>
      </Card>
    </aside>
  );
}

// ------------------------------------------------------------------ app
function App() {
  const [tab, setTabState] = useState<Tab>((new URLSearchParams(location.search).get("tab") as Tab) || (store.get("sim_tab") as Tab) || "scenarios");
  const setTab = (t: Tab) => {
    setTabState(t);
    store.set("sim_tab", t);
  };
  const [theme, setTheme] = useState<"light" | "dark">((store.get("sim_theme") as "light" | "dark") || "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    store.set("sim_theme", theme);
  }, [theme]);
  const cat = useQuery({ queryKey: ["catalogue"], queryFn: () => sim<Catalogue>("/catalogue"), staleTime: Infinity, retry: 3 });
  const state = useQuery({ queryKey: ["state"], queryFn: () => sim<SimState>("/state"), refetchInterval: 1000 });
  const col = useQuery({ queryKey: ["collector"], queryFn: () => sim<CollectorStatus>("/collector"), refetchInterval: 2000 });
  const pts = useLive();
  const clearAll = useMutation({ mutationFn: () => sim(`/clear?site=${SITE}`, { method: "POST" }), onSuccess: () => toast("All faults cleared") });
  const nFaults = state.data?.sites[SITE]?.faults.length ?? 0;
  const tabs = useMemo(
    () => [
      { v: "scenarios" as Tab, label: "Scenarios", icon: <Zap size={15} /> },
      { v: "stories" as Tab, label: "Stories", icon: <BookOpen size={15} /> },
      { v: "sla" as Tab, label: "SLA benchmarks", icon: <Gauge size={15} /> },
      { v: "boundary" as Tab, label: "Trust boundary", icon: <Swords size={15} /> },
      { v: "live" as Tab, label: "Live devices", icon: <Activity size={15} /> },
    ],
    [],
  );
  return (
    <div className="flex h-full flex-col bg-bg">
      <style>{SIM_CSS}</style>
      <div className="flex h-8 shrink-0 items-center justify-center gap-2 text-[12px] font-bold text-white" style={{ background: "linear-gradient(90deg,#7c3aed,#4f46e5)" }}>
        <Laptop size={13} /> LAPTOP · SITE {SITE.toUpperCase()} · SIMULATION LAB - drives the fake devices, the collector and attacks on the SaaS
      </div>
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-4">
        <span className="inline-flex items-center gap-2 text-[15px] font-bold">
          <FlaskConical size={18} className="text-redact" /> TrustLine Lab
        </span>
        <nav className="flex gap-1">
          {tabs.map((t) => (
            <button key={t.v} onClick={() => setTab(t.v)} className={cx("inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[12.5px] font-medium", tab === t.v ? "bg-panel-3 text-t1" : "text-t3 hover:text-t1")}>
              {t.icon} {t.label}
              {t.v === "scenarios" && nFaults > 0 && <span className="rounded-full bg-poor px-1.5 text-[10px] font-bold text-white">{nFaults}</span>}
              {t.v === "stories" && state.data?.story && !state.data.story.finished && <span className="live-dot h-1.5 w-1.5 rounded-full bg-excellent" />}
            </button>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2 text-[12px]">
          {state.isError && <span className="text-poor">simulator not reachable</span>}
          <button className="btn h-7" onClick={() => clearAll.mutate()}>
            <Trash2 size={12} /> Reset devices
          </button>
          <button className="btn h-7" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>
      <div className="shrink-0 border-b border-line bg-panel-2">
        <Pipeline col={col.data} pts={pts} state={state.data} />
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-4 xl:grid-cols-[1fr_340px]">
        <main className="min-w-0">
          {!cat.data ? (
            <div className="panel p-10 text-center text-t3">{cat.isError ? `Cannot reach the simulator API: ${(cat.error as Error).message}` : "Loading the scenario catalogue…"}</div>
          ) : (
            <>
              {tab === "scenarios" && <Scenarios cat={cat.data} state={state.data} />}
              {tab === "stories" && <Stories cat={cat.data} state={state.data} />}
              {tab === "sla" && <Sla cat={cat.data} state={state.data} />}
              {tab === "boundary" && <Boundary cat={cat.data} state={state.data} />}
              {tab === "live" && <Live cat={cat.data} pts={pts} state={state.data} />}
            </>
          )}
        </main>
        <Rail state={state.data} />
      </div>
    </div>
  );
}

const SIM_CSS = `
.sim-card { transition: transform .15s ease, box-shadow .2s ease; }
.sim-card:hover { transform: translateY(-1px); box-shadow: 0 6px 20px -10px color-mix(in srgb, var(--cat) 60%, transparent); }
.sim-active { box-shadow: 0 0 0 1.5px var(--cat), 0 0 22px -6px var(--cat); animation: simGlow 2s ease-in-out infinite; }
@keyframes simGlow { 50% { box-shadow: 0 0 0 1.5px var(--cat), 0 0 34px -4px var(--cat); } }
.sim-pipe { position: relative; overflow: hidden; }
.sim-dot { position: absolute; top: -1px; left: -10%; width: 18px; height: 6px; border-radius: 3px; background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: simFlow 1.3s linear infinite; }
.sim-dot.hot { background: var(--boundary); box-shadow: 0 0 8px var(--boundary); }
@keyframes simFlow { to { left: 100%; } }
`;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <App />
      <Toaster position="bottom-right" richColors theme="dark" />
    </QueryClientProvider>
  </StrictMode>,
);
