/* Deterministic simulated tenant for VITE_MOCK=1 / "demo data" mode.
 *
 * Every value is a pure function of (source, metric, time) plus a fault schedule, so history is
 * available for any range without storing it, and the picture matches what the real backend
 * produces: pseudonymous codes only, health from the same thresholds, incidents with evidence.
 */
import type {
  Investigation,
  Collector, EventRow, Evidence, Finding, HealthState, IncidentDetail, IncidentRow, Kind, MetricQuery,
  Overview, OverviewSeries, Remediation, Slo, Source, Topology,
} from "@/lib/types";

// ------------------------------------------------------------------ helpers
function fnv(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}
const code = (prefix: string, name: string) =>
  `${prefix}:${(fnv(name + "#a").toString(16).padStart(8, "0") + fnv(name + "#b").toString(16).padStart(8, "0")).slice(0, 12)}`;
function rnd(seed: number): number {
  // mulberry32 single step
  let t = (seed + 0x6d2b79f5) | 0;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const gauss = (seed: number) => {
  const u = Math.max(1e-9, rnd(seed));
  const v = rnd(seed ^ 0x9e3779b9);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
};
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const iso = (ms: number) => new Date(ms).toISOString();
const WIN = 5000;
const winOf = (ms: number) => Math.floor(ms / WIN) * WIN;

// ------------------------------------------------------------------ the site
interface Node {
  name: string;
  src: string;
  kind: Kind;
  role: string;
  collector: string;
}
const COLLECTORS = [
  { id: "col-ac01", label: "Mumbai DC" },
  { id: "col-ac02", label: "Pune branch" },
];
const defs: [string, Kind, string, string][] = [];
const add = (names: string[], kind: Kind, role: string, col: string) => names.forEach((n) => defs.push([n, kind, role, col]));
add(["web-01", "web-02", "web-03", "web-04", "web-05", "web-06"], "host", "web", "col-ac01");
add(["api-01", "api-02", "api-03", "api-04"], "host", "api", "col-ac01");
add(["db-01", "db-02", "db-03"], "host", "db", "col-ac01");
add(["cache-01", "cache-02"], "host", "cache", "col-ac01");
add(["worker-01", "worker-02", "worker-03", "worker-04", "worker-05"], "host", "worker", "col-ac02");
add(["edge-01", "edge-02", "edge-03"], "host", "edge", "col-ac02");
add(["pos-01", "pos-02", "pos-03", "pos-04"], "host", "pos", "col-ac02");
add(["web", "api", "auth", "checkout"], "svc", "service", "col-ac01");
add(["db", "cache", "queue"], "svc", "datastore", "col-ac01");
add(["uplink-1", "uplink-2"], "link", "uplink", "col-ac01");
add(["wan-pune"], "link", "wan", "col-ac02");

const prefixOf: Record<Kind, string> = { host: "h", svc: "svc", link: "link", ip: "ip", user: "u", other: "h" };
export const NODES: Node[] = defs.map(([name, kind, role, collector]) => ({
  name, kind, role, collector, src: code(prefixOf[kind], name),
}));
const byName = Object.fromEntries(NODES.map((n) => [n.name, n]));
const S = (name: string) => byName[name].src;

const EDGES: [string, string, string][] = [
  ["web", "api", "depends_on"], ["checkout", "api", "depends_on"], ["api", "db", "depends_on"],
  ["api", "cache", "depends_on"], ["api", "auth", "depends_on"], ["api", "uplink-1", "routes_via"],
  ["checkout", "queue", "depends_on"], ["web", "uplink-1", "routes_via"], ["auth", "db", "depends_on"],
  ["web", "web-01", "runs_on"], ["web", "web-02", "runs_on"], ["api", "api-01", "runs_on"], ["api", "api-02", "runs_on"],
  ["db", "db-01", "runs_on"], ["db", "db-02", "runs_on"], ["cache", "cache-01", "runs_on"], ["queue", "worker-03", "runs_on"],
];

export const METRICS: Record<Kind, string[]> = {
  host: ["cpu", "mem", "disk"],
  svc: ["error_rate", "rtt", "up"],
  link: ["rtt", "loss", "jitter"],
  ip: [], user: [], other: [],
};
const UNIT: Record<string, string> = { cpu: "%", mem: "%", disk: "%", rtt: "ms", loss: "%", jitter: "ms", error_rate: "ratio", up: "ratio" };

export const THRESHOLDS: Record<string, { stat: string; excellent: number; good: number; fair: number; lower_is_worse?: boolean; unit?: string }> = {
  cpu: { stat: "p95", excellent: 60, good: 80, fair: 90, unit: "%" },
  mem: { stat: "avg", excellent: 70, good: 80, fair: 90, unit: "%" },
  disk: { stat: "max", excellent: 70, good: 80, fair: 90, unit: "%" },
  rtt: { stat: "p95", excellent: 50, good: 100, fair: 200, unit: "ms" },
  loss: { stat: "avg", excellent: 0.5, good: 1, fair: 3, unit: "%" },
  jitter: { stat: "avg", excellent: 10, good: 20, fair: 40, unit: "ms" },
  up: { stat: "min", excellent: 1.0, good: 0.99, fair: 0.95, lower_is_worse: true },
  error_rate: { stat: "avg", excellent: 0.01, good: 0.02, fair: 0.05 },
};

export function classify(metric: string, v: number): HealthState {
  const t = THRESHOLDS[metric];
  if (!t) return "excellent";
  if (t.lower_is_worse) return v >= t.excellent ? "excellent" : v >= t.good ? "good" : v >= t.fair ? "fair" : "poor";
  return v < t.excellent ? "excellent" : v < t.good ? "good" : v < t.fair ? "fair" : "poor";
}

// ------------------------------------------------------------------ faults
type FaultKind = "latency_ramp" | "memory_leak" | "login_spray" | "cpu_spike" | "packet_loss" | "service_errors";
interface Fault {
  key: string;
  kind: FaultKind;
  target: string; // node name
  start: number;
  end: number;
  incident: string;
}
const PERIOD = 420_000;
const SCHEDULE: { kind: FaultKind; target: string; at: number; dur: number }[] = [
  { kind: "latency_ramp", target: "uplink-1", at: 0, dur: 170_000 },
  { kind: "memory_leak", target: "worker-03", at: 190_000, dur: 140_000 },
  { kind: "login_spray", target: "auth", at: 345_000, dur: 45_000 },
];
const ANCHOR = winOf(Date.now()) - 95_000; // page load lands ~95 s into a link degradation
const injected: Fault[] = [];
const endedEarly = new Map<string, number>();

function faultsAt(ms: number): Fault[] {
  const out: Fault[] = [];
  const cycle = Math.floor((ms - ANCHOR) / PERIOD);
  for (const c of [cycle - 1, cycle]) {
    SCHEDULE.forEach((f, i) => {
      const start = ANCHOR + c * PERIOD + f.at;
      const key = `${f.kind}-${c}`;
      const end = Math.min(start + f.dur, endedEarly.get(key) ?? Infinity);
      if (ms >= start && ms < end + 60_000) out.push({ key, kind: f.kind, target: f.target, start, end, incident: incId(c, i) });
    });
  }
  for (const f of injected) {
    const end = Math.min(f.end, endedEarly.get(f.key) ?? Infinity);
    if (ms >= f.start && ms < end + 60_000) out.push({ ...f, end });
  }
  return out;
}
const incId = (cycle: number, i: number) => `inc-${String(41 + (cycle + 3) * 3 + i).padStart(4, "0")}`;
const active = (f: Fault, ms: number) => ms >= f.start && ms < f.end;
const prog = (f: Fault, ms: number) => clamp((ms - f.start) / (f.end - f.start), 0, 1);

// ------------------------------------------------------------------ values
const BASE: Record<string, (n: Node, seed: number) => number> = {
  cpu: (n, s) => ({ web: 38, api: 46, db: 52, cache: 24, worker: 41, edge: 29, pos: 18 }[n.role] ?? 30) + (s % 7) * 2,
  mem: (n, s) => ({ db: 64, cache: 58 }[n.role] ?? 48) + (s % 5) * 2,
  disk: (n, s) => (n.name === "db-02" ? 83 : 40 + (s % 9) * 3),
  rtt: (n) => (n.kind === "link" ? (n.name === "wan-pune" ? 34 : 22) : ({ web: 85, api: 48, auth: 31, checkout: 120, db: 6, cache: 2, queue: 9 }[n.name] ?? 40)),
  loss: () => 0.12,
  jitter: (n) => (n.name === "wan-pune" ? 5.5 : 3),
  error_rate: (n) => ({ checkout: 0.006, api: 0.004 }[n.name] ?? 0.002),
  up: () => 1,
};

export function value(n: Node, metric: string, ms: number): number {
  const w = winOf(ms);
  const s = fnv(n.name + metric);
  const idx = w / WIN;
  const diurnal = Math.sin(((w / 1000) % 1800) / 1800 * Math.PI * 2 + (s % 10));
  const noise = gauss(s ^ idx);
  let v = BASE[metric]?.(n, s) ?? 0;
  switch (metric) {
    case "cpu": v += diurnal * 6 + noise * 3.2; break;
    case "mem": v += diurnal * 1.5 + noise * 0.8; break;
    case "disk": v += ((w - ANCHOR) / 3_600_000) * (n.name === "db-02" ? 1.1 : 0.05) + noise * 0.05; break;
    case "rtt": v *= 1 + diurnal * 0.05 + noise * 0.06; break;
    case "loss": v = Math.abs(v + noise * 0.06); break;
    case "jitter": v = Math.abs(v + noise * 0.6); break;
    case "error_rate": v = Math.abs(v * (1 + noise * 0.25)); break;
    case "up": v = 1; break;
  }
  for (const f of faultsAt(w)) {
    if (!active(f, w)) continue;
    const p = prog(f, w);
    const t = (w - f.start) / 1000;
    if (f.kind === "latency_ramp") {
      if (n.name === f.target && metric === "rtt") v += Math.min(190, t * 1.45);
      if (n.name === f.target && metric === "jitter") v += Math.min(30, t * 0.22);
      if (n.name === f.target && metric === "loss") v += t > 60 ? Math.min(2.4, (t - 60) * 0.03) : 0;
      if ((n.name === "api" || n.name === "checkout") && metric === "error_rate" && t > 65) v += Math.min(0.085, (t - 65) * 0.0016);
      if ((n.name === "web" || n.name === "checkout") && metric === "rtt" && t > 75) v += Math.min(260, (t - 75) * 3.2);
      if ((n.name === "api-01" || n.name === "api-02") && metric === "cpu" && t > 70) v += Math.min(30, (t - 70) * 0.5);
    } else if (f.kind === "memory_leak") {
      if (n.name === f.target && metric === "mem") v += p * 44;
      if (n.name === f.target && metric === "cpu" && p > 0.6) v += (p - 0.6) * 60;
      if (n.name === "queue" && metric === "rtt" && p > 0.7) v += (p - 0.7) * 200;
    } else if (f.kind === "cpu_spike") {
      if (n.name === f.target && metric === "cpu") v += 52 * Math.min(1, t / 10);
    } else if (f.kind === "packet_loss") {
      if (n.name === f.target && metric === "loss") v += 4.5 * Math.min(1, t / 15);
    } else if (f.kind === "service_errors") {
      if (n.name === f.target && metric === "error_rate") v += 0.09 * Math.min(1, t / 20);
    }
  }
  if (metric === "cpu" || metric === "mem" || metric === "disk") v = clamp(v, 0.5, 99.6);
  if (metric === "error_rate") v = clamp(v, 0, 1);
  return Math.round(v * 1000) / 1000;
}

const STAT_NOISE: Record<string, number> = { cpu: 1.18, rtt: 1.22 };
function stat(metric: string, v: number, which: string): number {
  if (which === "p95" || which === "max") return Math.round(v * (STAT_NOISE[metric] ?? 1) * 100) / 100;
  if (which === "min" && metric === "up") return v;
  return v;
}

function hoursToFull(n: Node, ms: number): number | null {
  if (n.kind !== "host") return null;
  const now = value(n, "disk", ms);
  const before = value(n, "disk", ms - 600_000);
  const rate = (now - before) / (600 / 3600);
  if (rate <= 0.01) return null;
  return Math.round(((100 - now) / rate) * 10) / 10;
}

// ------------------------------------------------------------------ health
export function sourceAt(n: Node, ms: number): Source {
  const signals: Source["signals"] = {};
  let worst: HealthState = "excellent";
  let reason = "all signals within thresholds";
  const rank: Record<HealthState, number> = { excellent: 0, good: 1, fair: 2, poor: 3 };
  for (const m of METRICS[n.kind]) {
    const th = THRESHOLDS[m];
    const v = stat(m, value(n, m, ms), th.stat);
    const st = classify(m, v);
    const unit = th.unit ?? "";
    const disp = m === "error_rate" ? v.toFixed(3) : `${v.toFixed(v >= 100 ? 0 : 1)}${unit}`;
    const r = `${m} ${th.stat} ${disp} (${st})`;
    signals[m] = { state: st, value: v, stat: th.stat, reason: r, ts: iso(winOf(ms)) };
    if (rank[st] > rank[worst]) {
      worst = st;
      reason = r;
    }
  }
  const h = hoursToFull(n, ms);
  if (h !== null && h < 24) {
    signals.disk_hours_to_full = { state: "poor", value: h, stat: "forecast", reason: `disk full in ${h.toFixed(1)}h < 24h (poor)` };
    if (rank.poor > rank[worst]) {
      worst = "poor";
      reason = signals.disk_hours_to_full.reason;
    }
  }
  const inc = openIncidentsAt(ms).find((i) => i.root === n.name || i.affected.includes(n.name));
  if (inc && rank[worst] < rank.fair) {
    worst = "fair";
    reason = `open incident ${inc.id} (${reason})`;
  }
  return {
    src: n.src, kind: n.kind, role: n.kind === "host" ? n.role : n.name, collector_id: n.collector, state: worst,
    stale: false, reason, signals, since: iso(winOf(ms) - 60_000), last_data_ts: iso(winOf(ms)),
  };
}

// ------------------------------------------------------------------ incidents
interface Inc {
  id: string;
  fault: Fault;
  root: string;
  affected: string[];
  opened: number;
  resolved: number | null;
}
function incidentFor(f: Fault): Inc {
  const delay = f.kind === "login_spray" ? 8_000 : 25_000;
  const affected =
    f.kind === "latency_ramp" ? ["api", "checkout", "web", "api-01", "api-02"] : f.kind === "memory_leak" ? ["queue"] : [];
  const root = f.kind === "login_spray" ? "auth" : f.target;
  const quiet = f.kind === "login_spray" ? 20_000 : 45_000;
  return { id: f.incident, fault: f, root, affected, opened: f.start + delay, resolved: f.end < Infinity ? f.end + quiet : null };
}
function incidentsUntil(ms: number): Inc[] {
  const seen = new Map<string, Inc>();
  for (let t = ms - 2 * PERIOD; t <= ms; t += 5_000) {
    for (const f of faultsAt(t)) {
      const inc = incidentFor(f);
      if (inc.opened <= ms) seen.set(inc.id, inc);
    }
  }
  return [...seen.values()].sort((a, b) => b.opened - a.opened);
}
function openIncidentsAt(ms: number): Inc[] {
  return incidentsUntil(ms).filter((i) => i.opened <= ms && (i.resolved === null || i.resolved > ms));
}

const remediations = new Map<string, Remediation[]>();
const auditTrail: { actor: string; role: string; action: string; target: string | null; detail: Record<string, unknown>; at: string }[] = [];
const acked = new Set<string>();

function evidenceFor(inc: Inc, ms: number): Evidence {
  const f = inc.fault;
  const rn = byName[inc.root];
  const short = (s: string) => `${s.split(":")[0]}·${s.split(":")[1].slice(0, 4)}`;
  if (f.kind === "latency_ramp") {
    const rtt = value(byName[f.target], "rtt", Math.min(ms, f.end - 1));
    const sig = [
      { src: S(f.target), metric: "rtt", stat: "p95", value: rtt * 1.2, baseline: 22.4, z: 7.4, layer: "L1", onset_ts: iso(f.start + 10_000), weight: 0.46 },
      { src: S("api"), metric: "error_rate", stat: "avg", value: 0.081, baseline: 0.004, z: 4.8, layer: "L1", onset_ts: iso(f.start + 94_000), weight: 0.3 },
      { src: S("web"), metric: "rtt", stat: "p95", value: 410, baseline: 86, z: 3.6, layer: "L1", onset_ts: iso(f.start + 101_000), weight: 0.24 },
    ];
    return {
      incident: inc.id, priority: "P2", title: `Upstream link degradation on link·${S(f.target).slice(5, 9)}`, root_cause: S(f.target),
      root_role: "uplink", affected: inc.affected.map(S), signals: sig, layers_agreeing: ["L1", "L2", "L3"], persistence: 1,
      rule: "upstream_link_degradation", confidence: 0.91, confidence_parts: { magnitude: 0.46, agreement: 0.3, persistence: 0.2 },
      action: { key: "failover_uplink", text: `Check link ${short(S(f.target))} or fail over to the secondary uplink`, reversible: true, rollback_key: "failback_uplink", requires_role: "tenant-admin", max_blast_radius: 6, executable: true },
      why: `Link ${short(S(f.target))} rtt p95 rose 84 s before 5 dependent component(s) degraded`,
      trace: [
        { step: "detect", layer: "L1", detail: `${short(S(f.target))} rtt z=7.4 vs baseline 22.4 (persistence 100%)` },
        { step: "detect", layer: "L1", detail: `${short(S("api"))} error_rate z=4.8 vs baseline 0.004 (persistence 67%)` },
        { step: "corroborate", layer: "L2", detail: "Half-Space Trees score 0.94 > p99 0.81" },
        { step: "corroborate", layer: "L3", detail: "autoencoder reconstruction error above p99; rtt explains 71%" },
        { step: "correlate", detail: "3 alert(s) on 3 component(s) within +/-120 s and graph distance <= 2", members: 3 },
        { step: "root_cause", chosen: S(f.target), rejected: [{ src: S("api"), why: `depends on ${S(f.target)}; onset +84 s` }, { src: S("web"), why: `depends on ${S("api")}; onset +91 s` }] },
        { step: "playbook", rule: "upstream_link_degradation", matched: ["root_kind=link", "root_metric=rtt", "impacted=error_rate", "impacted=rtt"] },
        { step: "policy", detail: "reversible=yes, blast radius 5 <= 6, requires tenant-admin" },
      ],
      suppressed: [
        { src: S("api"), metric: "error_rate", reason: `absorbed into ${inc.id}` },
        { src: S("web"), metric: "rtt", reason: `absorbed into ${inc.id}` },
        { src: S("api-01"), metric: "cpu", reason: `absorbed into ${inc.id}` },
        { src: S("checkout"), metric: "rtt", reason: "persistence 1/2 of last 3 windows" },
      ],
      noise: { raw_alerts: 7, incidents: 1 }, blast_radius: 5,
    };
  }
  if (f.kind === "memory_leak") {
    return {
      incident: inc.id, priority: "P3", title: `Memory leak on host·${S(f.target).slice(2, 6)}`, root_cause: S(f.target), root_role: rn.role,
      affected: [S("queue")], layers_agreeing: ["L1", "L3"], persistence: 1, rule: "memory_leak", confidence: 0.78,
      confidence_parts: { magnitude: 0.38, agreement: 0.2, persistence: 0.2 },
      signals: [
        { src: S(f.target), metric: "mem", stat: "avg", value: value(rn, "mem", ms), baseline: 49.5, z: 6.1, layer: "L1", onset_ts: iso(f.start + 20_000), weight: 0.72 },
        { src: S("queue"), metric: "rtt", stat: "p95", value: 42, baseline: 9, z: 3.3, layer: "L1", onset_ts: iso(f.start + 98_000), weight: 0.28 },
      ],
      action: { key: "restart_service", text: `Restart the leaking service on ${short(S(f.target))} during low traffic`, reversible: false, rollback_key: "none", requires_role: "tenant-admin", max_blast_radius: 3, executable: true },
      why: `${short(S(f.target))} mem rose steadily for ${Math.round((Math.min(ms, f.end) - f.start) / 1000)} s with no matching traffic increase`,
      trace: [
        { step: "detect", layer: "L1", detail: `${short(S(f.target))} mem z=6.1 vs baseline 49.5 (persistence 100%)` },
        { step: "corroborate", layer: "L3", detail: "autoencoder reconstruction error above p99; mem explains 83%" },
        { step: "correlate", detail: "2 alert(s) on 2 component(s) within +/-120 s and graph distance <= 2", members: 2 },
        { step: "root_cause", chosen: S(f.target), rejected: [{ src: S("queue"), why: `depends on ${S(f.target)}; onset +78 s` }] },
        { step: "playbook", rule: "memory_leak", matched: ["root_kind=host", "root_metric=mem", "trend=rising"] },
        { step: "policy", detail: "reversible=no (restart), blast radius 2 <= 3, requires tenant-admin" },
      ],
      suppressed: [{ src: S("queue"), metric: "rtt", reason: `absorbed into ${inc.id}` }], noise: { raw_alerts: 3, incidents: 1 }, blast_radius: 2,
    };
  }
  const ip = code("ip", "203.0.113.77");
  return {
    incident: inc.id, priority: "P1", title: "Login attack (spray) on svc·" + S("auth").slice(4, 8), root_cause: ip, root_role: "external ip",
    affected: [S("auth")], layers_agreeing: ["L0"], persistence: 1, rule: "auth_attack", confidence: 0.86,
    confidence_parts: { magnitude: 0.48, agreement: 0.1, persistence: 0.2 },
    signals: [{ src: ip, metric: "login_spray", stat: "count", value: 46, baseline: null, z: null, layer: "L0", onset_ts: iso(f.start + 3_000), weight: 1, category: "auth", pattern: "spray" }],
    action: { key: "block_source_ip", text: `Block source ${short(ip)} at the edge for 1 h`, reversible: true, rollback_key: "unblock_source_ip", requires_role: "tenant-admin", max_blast_radius: 1, executable: true },
    why: `${short(ip)} failed logins for 23 distinct users in 60 s (risk 0.96, isolation forest + rules)`,
    trace: [
      { step: "detect", layer: "L0", detail: `spray from ${short(ip)}: 46 fails, 23 users, risk 0.96` },
      { step: "correlate", detail: "1 alert(s) on 1 component(s)", members: 1 },
      { step: "root_cause", chosen: ip, rejected: [] },
      { step: "playbook", rule: "auth_attack", matched: ["category=auth", "pattern=spray"] },
      { step: "policy", detail: "reversible=yes, blast radius 1 <= 1, requires tenant-admin" },
    ],
    suppressed: [], noise: { raw_alerts: 4, incidents: 1 }, blast_radius: 1,
  };
}

function incidentRow(inc: Inc, ms: number): IncidentRow & { evidence: Evidence } {
  const ev = evidenceFor(inc, ms);
  const open = inc.resolved === null || inc.resolved > ms;
  return {
    incident_id: inc.id, status: !open ? "resolved" : acked.has(inc.id) ? "acknowledged" : "open",
    priority: ev.priority as IncidentRow["priority"], severity: ev.priority === "P1" ? "critical" : ev.priority === "P2" ? "high" : "medium",
    title: ev.title, root_cause_src: ev.root_cause, affected: ev.affected, alert_count: ev.noise.raw_alerts,
    breach_ts: iso(inc.fault.start + 10_000), opened_ts: iso(inc.opened), alert_ts: iso(inc.opened + 900),
    resolved_ts: open ? null : iso(inc.resolved!), confidence: ev.confidence, rule: ev.rule, evidence: ev,
  };
}

// ------------------------------------------------------------------ events / security / privacy
const TEMPLATES = [
  { t: "GET /api/v<num>/orders/<num> <num> <num>ms", level: "info", cat: "access", w: 40 },
  { t: "POST /api/v<num>/checkout user=<u:code> status=<num>", level: "info", cat: "access", w: 22 },
  { t: "Accepted password for <u:code> from <ip:code> port <num>", level: "info", cat: "auth", w: 6 },
  { t: "Failed password for <u:code> from <ip:code> port <num>", level: "warning", cat: "auth", w: 3 },
  { t: "connection to <h:code> timed out after <num>ms", level: "error", cat: "app", w: 2 },
  { t: "using credentials token=<secret:github_token>", level: "warning", cat: "app", w: 1 },
  { t: "retrying upstream <svc:code> attempt <num>/<num>", level: "warning", cat: "app", w: 3 },
  { t: "slow query took <num>ms on <h:code>", level: "warning", cat: "db", w: 2 },
  { t: "payment gateway responded <num> for order <uuid>", level: "error", cat: "app", w: 1 },
  { t: "contact <person> at <u:code> about invoice <num>", level: "info", cat: "app", w: 1 },
  { t: "aws key <secret:aws_access_key> loaded for backup job", level: "warning", cat: "app", w: 1 },
];
const RULES = ["email", "ipv4", "github_token", "aws_access_key", "jwt", "password_kv", "high_entropy", "person_name", "private_key"];
const RULE_W = [30, 26, 6, 3, 9, 7, 8, 9, 1];

function levelBoost(ms: number): number {
  return faultsAt(ms).some((f) => active(f, ms) && f.kind === "latency_ramp" && ms - f.start > 65_000) ? 4 : 1;
}

function eventsAt(ms: number): EventRow[] {
  const w = winOf(ms);
  const out: EventRow[] = [];
  TEMPLATES.forEach((tp, i) => {
    let c = Math.round(tp.w * (0.6 + rnd(w / WIN + i * 977) * 0.8));
    if (tp.level === "error") c *= levelBoost(w);
    if (tp.cat === "auth" && tp.level === "warning" && faultsAt(w).some((f) => f.kind === "login_spray" && active(f, w))) c += 40;
    if (c <= 0) return;
    const red = (tp.t.match(/<(u|ip|h|svc|secret|person)/g) || []).length * c;
    out.push({
      src: tp.cat === "auth" ? S("auth") : tp.cat === "db" ? S("db") : S(["web", "api", "checkout"][i % 3]),
      template_id: (fnv(tp.t) >>> 0).toString(16).slice(0, 8), event_ts: iso(w), category: tp.cat, level: tp.level,
      template: tp.t, count: c, redacted: red, payload_id: uuidish(w + i), collector_id: "col-ac01",
    });
  });
  return out;
}
function uuidish(n: number): string {
  const h = (k: number) => (fnv(String(n) + k) >>> 0).toString(16).padStart(8, "0");
  return `0193${h(1).slice(0, 4)}-${h(2).slice(0, 4)}-7${h(3).slice(0, 3)}-8${h(4).slice(0, 3)}-${h(5)}${h(6).slice(0, 4)}`;
}

function findingsBetween(t0: number, t1: number): Finding[] {
  const out: Finding[] = [];
  for (let w = winOf(t0); w <= t1; w += WIN) {
    const r = rnd(w / WIN + 31337);
    const spray = faultsAt(w).find((f) => f.kind === "login_spray" && active(f, w));
    const add = (pattern: string, severity: Finding["severity"], risk: number, ipn: string, extra: Partial<Finding> = {}) =>
      out.push({
        finding_id: uuidish(w + pattern.length), event_ts: iso(w), breach_ts: iso(w - 3000), src: code("ip", ipn), category: "auth",
        pattern, severity, risk, layer: "L0", fail_count: 8 + Math.round(r * 30), distinct_users: pattern === "spray" ? 14 + Math.round(r * 12) : 1,
        window_s: 60, payload_id: uuidish(w), ...extra,
      });
    if (spray && (w / WIN) % 2 === 0) add("spray", "critical", 0.96, "203.0.113.77");
    if (r > 0.9) add("brute_force", "high", 0.71, "198.51.100." + Math.floor(r * 200));
    else if (r > 0.82) add("brute_force", "medium", 0.52, "192.0.2." + Math.floor(r * 90));
    else if (r < 0.025) add("success_after_failures", "critical", 0.93, "198.51.100.23");
    else if (r < 0.1)
      out.push({
        finding_id: uuidish(w + 7), event_ts: iso(w), breach_ts: iso(w - 15000), src: S(["api", "web", "checkout"][Math.floor(r * 30) % 3]),
        category: "threshold", pattern: "threshold_breach", severity: "low", risk: 0.3, layer: "T", metric: "rtt", value: 210, threshold: 200, payload_id: uuidish(w),
      });
  }
  return out.reverse();
}

// ------------------------------------------------------------------ public mock API
let demoStart = Date.now();
export const mock = {
  me: () => ({ email: "admin@acme.test", tenant: "acme", role: "tenant-admin", tenant_name: "Acme Retail", region: "ap-south", regions_connected: ["ap-south", "eu-west"] }),

  topology(ms = Date.now()): Topology {
    return {
      nodes: NODES.map((n) => ({ src: n.src, kind: n.kind, role: n.kind === "host" ? n.role : n.name, state: sourceAt(n, ms).state })),
      edges: EDGES.map(([a, b, k]) => ({ dependent: S(a), dependency: S(b), kind: k })),
    };
  },

  sources(ms = Date.now()): { sources: Source[] } {
    return { sources: NODES.map((n) => sourceAt(n, ms)) };
  },

  source(src: string, ms = Date.now()) {
    const n = NODES.find((x) => x.src === src);
    if (!n) throw new Error("unknown source");
    const transitions = [] as { old_state: string | null; new_state: string; reason: string; at: string }[];
    let prev: HealthState | null = null;
    for (let t = ms - 30 * 60_000; t <= ms; t += 15_000) {
      const s = sourceAt(n, t);
      if (s.state !== prev) transitions.push({ old_state: prev, new_state: s.state, reason: s.reason, at: iso(t) });
      prev = s.state;
    }
    return {
      health: sourceAt(n, ms), transitions: transitions.reverse().slice(0, 30),
      latest: METRICS[n.kind].map((m) => ({ metric: m, event_ts: iso(winOf(ms)), avg: value(n, m, ms), p95: stat(m, value(n, m, ms), "p95"), max: stat(m, value(n, m, ms), "max"), unit: UNIT[m] })),
      incidents: incidentsUntil(ms).filter((i) => i.root === n.name || i.affected.includes(n.name)).slice(0, 5).map((i) => {
        const r = incidentRow(i, ms);
        return { incident_id: r.incident_id, title: r.title, priority: r.priority, status: r.status };
      }),
    };
  },

  metricQuery(src: string, metric: string, minutes = 15, statName?: string, ms = Date.now()): MetricQuery {
    const n = NODES.find((x) => x.src === src) ?? NODES[0];
    const th = THRESHOLDS[metric];
    const st = statName ?? th?.stat ?? "avg";
    const step = minutes <= 15 ? WIN : minutes <= 60 ? 15_000 : 60_000;
    const pts = [];
    for (let t = winOf(ms - minutes * 60_000); t <= ms - WIN; t += step) pts.push({ t: iso(t), v: stat(metric, value(n, metric, t), st) });
    const base = pts.slice(0, Math.max(10, Math.floor(pts.length / 2))).map((p) => p.v).sort((a, b) => a - b);
    const med = base[Math.floor(base.length / 2)] ?? 0;
    const mad = base.map((v) => Math.abs(v - med)).sort((a, b) => a - b)[Math.floor(base.length / 2)] ?? 0;
    const wdt = 3 * (1.4826 * mad + Math.max(0.01, Math.abs(med) * 0.01));
    const alerts = incidentsUntil(ms).filter((i) => i.root === n.name || i.affected.includes(n.name))
      .map((i) => ({ onset_ts: iso(i.opened), z: 6.2, incident_id: i.id })).filter((a) => Date.parse(a.onset_ts) > ms - minutes * 60_000);
    return { src, metric, stat: st, points: pts, band: { median: med, lo: med - wdt, hi: med + wdt }, thresholds: th ?? null, alerts };
  },

  overview(ms = Date.now()): Overview {
    const srcs = NODES.map((n) => sourceAt(n, ms));
    const states = { excellent: 0, good: 0, fair: 0, poor: 0 } as Record<HealthState, number>;
    const byKind = new Map<string, number>();
    srcs.forEach((s) => {
      states[s.state]++;
      byKind.set(`${s.state}|${s.kind}`, (byKind.get(`${s.state}|${s.kind}`) ?? 0) + 1);
    });
    const incs = openIncidentsAt(ms).map((i) => incidentRow(i, ms));
    const hrs = (ms - demoStart) / 3_600_000 + 6;
    const red = Math.round(1284 + hrs * 210);
    return {
      states, states_by_kind: [...byKind].map(([k, n]) => ({ state: k.split("|")[0] as HealthState, kind: k.split("|")[1], n })),
      incidents: incs, noise: { raw_alerts_24h: 61 + Math.floor((ms - demoStart) / 30000), incidents_24h: 8 + Math.floor((ms - demoStart) / PERIOD) * 3, reduction: 0 },
      privacy: {
        redactions_24h: red, payloads_24h: Math.round(hrs * 3600), records_24h: Math.round(hrs * 3600 * 38),
        by_rule: RULES.map((r, i) => ({ rule: r, n: Math.round((red * RULE_W[i]) / 100) })).sort((a, b) => b.n - a.n),
        lines_24h: Math.round(hrs * 3600 * 82), templates_24h: 214, blocked_total: 2,
      },
      slo: {
        dash: { n: 512, p50: 820 + rnd(winOf(ms)) * 150, p95: 1460 + rnd(winOf(ms) + 1) * 300, max: 2900, target: 10000 },
        alert: { n: 12, p50: 2100, p95: 3900 + rnd(winOf(ms) + 2) * 500, max: 5200, target: 15000 },
        offline: { n: 2, p50: 47000, p95: 49000, max: 49000, target: 60000 },
      },
      fleet: COLLECTORS.map((c) => ({ collector_id: c.id, state: "online", buffer_depth: 0, last_hb_event_ts: iso(winOf(ms)) })),
      findings_24h: 342 + Math.floor((ms - demoStart) / 12000),
    };
  },

  overviewSeries(minutes = 60, ms = Date.now()): OverviewSeries {
    const step = Math.max(10, minutes) * 1000;
    const t0 = Math.floor((ms - minutes * 60_000) / step) * step;
    const redactions: OverviewSeries["redactions"] = [];
    const security: OverviewSeries["security"] = [];
    const events: OverviewSeries["events"] = [];
    const payloads: OverviewSeries["payloads"] = [];
    const latency: OverviewSeries["latency"] = [];
    const fs = findingsBetween(t0, ms);
    for (let t = t0; t < ms; t += step) {
      const k = t / step;
      const spray = faultsAt(t).some((f) => f.kind === "login_spray" && active(f, t));
      RULES.forEach((r, i) => {
        const base = (RULE_W[i] / 100) * 9 * (step / 10_000);
        const n = Math.round(base * (0.55 + rnd(k * 13 + i) * 0.9) * (r === "ipv4" && spray ? 3 : 1) * levelBoost(t) ** (r === "high_entropy" ? 0.5 : 0));
        redactions.push({ t: iso(t), rule: r, n });
      });
      (["critical", "high", "medium", "low"] as const).forEach((sev) => {
        const n = fs.filter((f) => f.severity === sev && Date.parse(f.event_ts) >= t && Date.parse(f.event_ts) < t + step).length;
        security.push({ t: iso(t), severity: sev, n: n + (sev === "low" ? Math.round(rnd(k + 5) * 2) : 0) });
      });
      const ev = eventsAt(t);
      for (const lv of ["info", "warning", "error"]) events.push({ t: iso(t), level: lv, n: ev.filter((e) => e.level === lv).reduce((a, e) => a + e.count, 0) * (step / WIN) });
      payloads.push({ t: iso(t), payloads: step / 1000, records: Math.round((step / 1000) * (36 + rnd(k) * 6)) });
      latency.push({ t: iso(t), kind: "dash", p95: 1200 + rnd(k + 99) * 500 + (levelBoost(t) > 1 ? 250 : 0) });
      latency.push({ t: iso(t), kind: "alert", p95: 3200 + rnd(k + 77) * 900 });
    }
    const latest = NODES.flatMap((n) => METRICS[n.kind].map((m) => ({ src: n.src, metric: m, p95: stat(m, value(n, m, ms), "p95"), avg: value(n, m, ms) })));
    const disk = NODES.filter((n) => n.kind === "host").map((n) => ({ src: n.src, avg: hoursToFull(n, ms) ?? 999 })).sort((a, b) => a.avg - b.avg).slice(0, 5);
    return { step_s: step / 1000, redactions, security, events, payloads, latency, latest, disk_forecast: disk };
  },

  events(minutes = 15, ms = Date.now()): { events: EventRow[] } {
    const out: EventRow[] = [];
    for (let t = winOf(ms) - WIN; t > ms - minutes * 60_000 && out.length < 400; t -= WIN) out.push(...eventsAt(t));
    return { events: out };
  },

  facets(ms = Date.now()) {
    const ev = this.events(60, ms).events;
    const f = (k: keyof EventRow) => {
      const m = new Map<string, number>();
      ev.forEach((e) => m.set(String(e[k]), (m.get(String(e[k])) ?? 0) + e.count));
      return [...m].map(([value, n]) => ({ value, n })).sort((a, b) => b.n - a.n);
    };
    return { level: f("level"), category: f("category"), src: f("src") };
  },

  findings(ms = Date.now()) {
    return { findings: findingsBetween(ms - 30 * 60_000, ms).slice(0, 300) };
  },

  incidents(ms = Date.now()) {
    return { incidents: incidentsUntil(ms).map((i) => { const { evidence: _e, ...row } = incidentRow(i, ms); return row; }) };
  },

  incident(id: string, ms = Date.now()): IncidentDetail {
    const inc = incidentsUntil(ms).find((i) => i.id === id);
    if (!inc) throw new Error("no such incident");
    const row = incidentRow(inc, ms);
    advanceRemediations(ms);
    return {
      incident: row,
      alerts: row.evidence.signals.map((s, i) => ({ alert_id: `${id}-a${i}`, src: s.src, metric: s.metric, layer: s.layer, z: s.z, onset_ts: s.onset_ts, suppressed_reason: i ? `absorbed into ${id}` : null })),
      remediations: remediations.get(id) ?? [],
      audit: [
        { actor: "aiops", role: "system", action: "incident.opened", detail: { confidence: row.confidence }, at: row.opened_ts },
        ...auditTrail.filter((a) => a.target === id).map((a) => ({ actor: a.actor, role: a.role, action: a.action, detail: a.detail, at: a.at })),
        ...(row.resolved_ts ? [{ actor: "aiops", role: "system", action: "incident.resolved", detail: { why: "all signals normal for 120 s" }, at: row.resolved_ts }] : []),
      ],
    };
  },

  incidentOp(id: string, op: string) {
    if (op === "ack") acked.add(id);
    if (op === "resolve") {
      const inc = incidentsUntil(Date.now()).find((i) => i.id === id);
      if (inc) endedEarly.set(inc.fault.key, Date.now() - 60_000);
    }
    auditTrail.push({ actor: "admin@acme.test", role: "tenant-admin", action: `incident.${op}`, target: id, detail: {}, at: iso(Date.now()) });
    return { incident_id: id, status: op === "ack" ? "acknowledged" : "resolved" };
  },

  investigations: new Map<string, Investigation>(),

  /** Demo-mode stand-in for the in-region deterministic investigator (same shape as the API). */
  investigate(id: string): Investigation {
    const t0 = Date.now();
    const ev = this.incident(id).incident.evidence;
    const sigs = [...ev.signals].sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0)).slice(0, 6);
    const top = sigs[0];
    const pending = (remediations.get(id) ?? []).find((r) => r.state === "proposed");
    const queued = ev.action.executable ? (pending ? { action_id: pending.action_id, state: "proposed", note: "already awaiting approval" } : { action_id: this.propose(id).action_id, state: "proposed" }) : null;
    const inv: Investigation = {
      incident_id: id, engine: "deterministic", model: null, fallback_reason: "demo mode", requires_approval: true, policy_note: null, queued,
      diagnosis: {
        summary: `${ev.title}. The origin is most likely ${ev.root_cause}` + (top ? `: ${top.metric} is ${top.value} against a baseline of ${top.baseline} (z=${top.z}, weight ${top.weight})` : "") +
          `. ${ev.affected.length} dependent component(s) degraded after it; ${ev.layers_agreeing.length} detection layer(s) agree (${ev.layers_agreeing.join(", ")}), confidence ${ev.confidence}.` +
          (ev.action.executable ? ` Recommended: ${ev.action.key} - queued for approval.` : " No safe automatic action; investigate manually."),
        root_cause: ev.root_cause, root_cause_reasoning: ev.why,
        citations: sigs.map((s) => ({ src: s.src, metric: s.metric, value: s.value, baseline: s.baseline, z: s.z, weight: s.weight, role: s.src === ev.root_cause ? "root_cause" : "impacted" })),
        layers_agreeing: ev.layers_agreeing, confidence: ev.confidence,
        recommended_action: ev.action.executable ? ev.action.key : "investigate", action_rationale: ev.action.text,
        rollback_plan: ev.action.reversible && ev.action.rollback_key && ev.action.rollback_key !== "none" ? `${ev.action.rollback_key} restores the previous state` : "not reversible - approve only with a manual recovery plan",
        risk: ev.action.reversible ? "low" : "medium",
        caveats: ev.layers_agreeing.length < 2 ? ["only one detection layer agrees - treat as a lead, not a conclusion"] : [],
      },
      trace: [
        { step: "tool", tool: "get_incident_context", input: { incident_id: id }, ok: true, ms: 3.1, result_preview: `{"confidence": ${ev.confidence}, "signals": ${ev.signals.length}}` },
        { step: "tool", tool: "query_topology", input: { source_id: ev.root_cause }, ok: true, ms: 1.4, result_preview: `{"dependents": ${JSON.stringify(ev.affected.slice(0, 3))}}` },
        { step: "tool", tool: "fetch_playbook_rule", input: { rule_name: ev.rule }, ok: true, ms: 0.4, result_preview: `{"action": {"key": "${ev.action.key}"}}` },
      ],
      usage: { requests: 0 }, ms: Date.now() - t0 + 40, at: iso(Date.now()), data_sent: "none (ran in-region)",
    };
    this.investigations.set(id, inv);
    auditTrail.push({ actor: "admin@acme.test", role: "tenant-admin", action: "investigation.completed", target: id, detail: { engine: "deterministic" }, at: iso(Date.now()) });
    return inv;
  },

  propose(id: string) {
    const d = this.incident(id);
    const ev = d.incident.evidence;
    const checks = [
      { check: "reversible", ok: true, detail: `rollback: ${ev.action.rollback_key ?? "none"}` },
      { check: "blast_radius", ok: ev.blast_radius <= (ev.action.max_blast_radius ?? 99), detail: `${ev.blast_radius} <= ${ev.action.max_blast_radius}` },
      { check: "approver_role", ok: true, detail: `requires ${ev.action.requires_role} to approve` },
      { check: "collector_known", ok: true, detail: "col-ac01" },
      { check: "local_allowlist", ok: true, detail: "enforced again on site by the collector" },
    ];
    const r: Remediation = {
      action_id: uuidish(Date.now()), incident_id: id, action_key: ev.action.key, params: { target: ev.root_cause }, policy: { checks, ok: true },
      state: "proposed", collector_id: "col-ac01", proposed_by: "admin@acme.test", proposed_ts: iso(Date.now()),
    };
    remediations.set(id, [...(remediations.get(id) ?? []), r]);
    auditTrail.push({ actor: "admin@acme.test", role: "tenant-admin", action: "remediation.proposed", target: id, detail: { action_key: r.action_key }, at: r.proposed_ts });
    return { action_id: r.action_id, state: "proposed", checks };
  },

  remediationOp(aid: string, op: string) {
    for (const [iid, list] of remediations) {
      const r = list.find((x) => x.action_id === aid);
      if (!r) continue;
      const now = Date.now();
      if (op === "approve") Object.assign(r, { state: "approved", approved_by: "admin@acme.test", approved_ts: iso(now) });
      if (op === "reject") r.state = "rejected";
      if (op === "rollback") r.state = "rolled_back";
      auditTrail.push({ actor: "admin@acme.test", role: "tenant-admin", action: `remediation.${r.state}`, target: iid, detail: { action_id: aid, action_key: r.action_key }, at: iso(now) });
      return { action_id: aid, state: r.state };
    }
    throw new Error("no such action");
  },

  fleet(ms = Date.now()): { collectors: Collector[]; scope: string } {
    const up = (ms - demoStart) / 1000;
    return {
      scope: "acme",
      collectors: COLLECTORS.map((c, i) => ({
        tenant_id: "acme", collector_id: c.id, region: "ap-south", state: "online", state_reason: "heartbeat ok", last_hb_event_ts: iso(winOf(ms)),
        version: "0.1.0", config_hash: i ? "4c1d0e9a2b7f" : "9f3c1a7e5b20", expected_config_hash: i ? "4c1d0e9a2b7f" : "9f3c1a7e5b20",
        cpu: 3.1 + rnd(winOf(ms) + i) * 2, mem_mb: 92 + i * 11, buffer_depth: 0, buffer_bytes: 0,
        last_acked_seq: 18234 + Math.floor(up) - i * 6000, last_assigned_seq: 18234 + Math.floor(up) - i * 6000,
        redactions_total: 48211 + Math.floor(up * 3.4) - i * 20000, blocked_total: 2 - i, skew_ms: 38 + i * 21, cert_serial: i ? "5a1e9c" : "3f8b20",
      })),
    };
  },

  delivery(ms = Date.now()) {
    const up = Math.floor((ms - demoStart) / 1000);
    return {
      ledger: COLLECTORS.map((c, i) => ({
        collector_id: c.id, min_seq: 1, max_seq: 18234 + up - i * 6000, received: 18234 + up - i * 6000, gaps: 0,
        js_duplicates: 25 - i * 20, db_conflicts: 0, gaps_open: 0, last_assigned_seq: 18234 + up - i * 6000, last_acked_seq: 18234 + up - i * 6000, buffer_depth: 0,
        last_received: iso(ms - 600),
      })),
      recent: Array.from({ length: 20 }, (_, k) => ({
        payload_id: uuidish(winOf(ms) - k * 1000), seq: 18234 + up - k, result: k === 7 ? "duplicate" : "stored", lane: k > 15 ? "backlog" : "live",
        attempt: k === 7 ? 2 : 1, received_at: iso(ms - k * 1000), skew_ms: 30 + rnd(k) * 20,
      })),
    };
  },

  isolation() {
    return {
      tenant: "acme", db_role: "tl_api", role_bypasses_rls: false, force_rls: true,
      sql: "SELECT tenant_id, count(*) FROM metrics WHERE tenant_id <> current_setting('app.tenant_id') GROUP BY 1",
      other_tenant_rows: 0, rows_visible_total: 1_284_330, rows_own_tenant: 1_284_330, rows_without_tenant_context: 0,
      policies: [
        { tablename: "events", policyname: "tenant_isolation", qual: "(tenant_id = current_setting('app.tenant_id'::text, true))" },
        { tablename: "incidents", policyname: "tenant_isolation", qual: "(tenant_id = current_setting('app.tenant_id'::text, true))" },
        { tablename: "metrics", policyname: "tenant_isolation", qual: "(tenant_id = current_setting('app.tenant_id'::text, true))" },
      ],
      pass: true, ms: 7.4,
    };
  },

  residency() {
    return {
      regions: [
        { region: "ap-south", connected: true, tenants: [{ tenant_id: "acme", metrics: 1_284_330, events: 402_118, findings: 9_381 }, { tenant_id: "other", metrics: null }] },
        { region: "eu-west", connected: true, tenants: [{ tenant_id: "other", metrics: null }] },
      ],
      home_regions: { acme: "ap-south" },
    };
  },

  audit(pid: string) {
    return {
      payload_id: pid, home_region: "ap-south", metric_rows: 31,
      attempts: [
        { result: "stored", attempt: 1, lane: "live", received_at: iso(Date.now() - 4000), sha256: "9f3c" + pid.replace(/-/g, "").slice(0, 60), region: "ap-south", seq: 18190 },
        { result: "duplicate", attempt: 2, lane: "live", received_at: iso(Date.now() - 3000), sha256: "9f3c" + pid.replace(/-/g, "").slice(0, 60), region: "ap-south", seq: 18190 },
      ],
      stored: { payload_id: pid, seq: 18190, records: 38, redactions: 4, received_at: iso(Date.now() - 4000) },
    };
  },

  slo(ms = Date.now()): Slo {
    const recent = Array.from({ length: 240 }, (_, i) => {
      const alert = i % 20 === 0;
      const r = rnd(winOf(ms) / 1000 + i);
      return { kind: alert ? "alert" : "dash", total_ms: alert ? 2400 + r * 2600 : 650 + r * 900 + (r > 0.95 ? 1400 : 0), at: iso(ms - i * 3750) };
    });
    const hist: Slo["histogram"] = [];
    for (const kind of ["dash", "alert"]) {
      const b = new Map<number, number>();
      recent.filter((r) => r.kind === kind).forEach((r) => b.set(Math.floor(r.total_ms / 500) + 1, (b.get(Math.floor(r.total_ms / 500) + 1) ?? 0) + 1));
      [...b].sort((x, y) => x[0] - y[0]).forEach(([bb, n]) => hist.push({ kind, b: bb, n }));
    }
    const pct = (xs: number[], p: number) => { const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * s.length))] ?? 0; };
    const kinds: Slo["kinds"] = {};
    for (const kind of ["dash", "alert"]) {
      const xs = recent.filter((r) => r.kind === kind).map((r) => r.total_ms);
      kinds[kind] = { n: xs.length, p50: pct(xs, 0.5), p95: pct(xs, 0.95), max: Math.max(...xs) };
    }
    kinds.offline = { n: 2, p50: 46800, p95: 48900, max: 48900 };
    return {
      targets: { dash: 10000, alert: 15000, offline: 60000 }, kinds, excluded: { backlog: 41, revision: 7 },
      hops_ms: { window_wait: 2520, seal: 140, wire: 95, store: 38, push: 12, render: 44 }, histogram: hist, recent, bucket_ms: 500,
    };
  },

  thresholds() {
    return { defaults: THRESHOLDS, file_overrides: {}, tenant_overrides: {}, updated_by: null };
  },

  auditLog() {
    return {
      audit: [
        ...auditTrail.map((a, i) => ({ id: 1000 + i, ...a })).reverse(),
        { id: 3, actor: "admin@acme.test", role: "tenant-admin", action: "trust.isolation_test", target: null, detail: { other_rows: 0, unset_rows: 0 }, at: iso(demoStart - 600_000) },
        { id: 2, actor: "aiops", role: "system", action: "incident.resolved", target: "inc-0040", detail: { why: "auto: all signals normal for 120 s" }, at: iso(demoStart - 900_000) },
        { id: 1, actor: "collector:col-ac01", role: "system", action: "remediation.executed", target: "inc-0039", detail: { action_key: "failover_uplink" }, at: iso(demoStart - 1_400_000) },
      ],
    };
  },

  scenarios() {
    return {
      scenarios: [
        { kind: "latency_ramp", label: "Upstream latency ramp", target: "uplink-1" },
        { kind: "cpu_spike", label: "CPU saturation", target: "api-03" },
        { kind: "packet_loss", label: "Packet loss burst", target: "wan-pune" },
        { kind: "service_errors", label: "Checkout error spike", target: "checkout" },
        { kind: "memory_leak", label: "Memory leak", target: "worker-02" },
        { kind: "login_spray", label: "Password spray", target: "auth" },
      ],
      status: { site: "acme", active: faultsAt(Date.now()).filter((f) => active(f, Date.now())).map((f) => ({ kind: f.kind, target: f.target })) },
    };
  },

  inject(kind: string) {
    const sc = this.scenarios().scenarios.find((s) => s.kind === kind);
    if (!sc) throw new Error("unknown scenario");
    const start = Date.now();
    injected.push({ key: `inj-${start}`, kind: kind as FaultKind, target: sc.target, start, end: start + 150_000, incident: `inc-${String(900 + injected.length).padStart(4, "0")}` });
    return { ok: true, kind, target: sc.target };
  },

  clear() {
    const now = Date.now();
    injected.forEach((f) => endedEarly.set(f.key, now));
    return { ok: true };
  },

  reset() {
    demoStart = Date.now();
  },
};

/** remediation lifecycle in demo mode: approved -> dispatched (2 s) -> executed (5 s) -> verified (15 s) */
function advanceRemediations(ms: number) {
  for (const [iid, list] of remediations) {
    for (const r of list) {
      const at = r.approved_ts ? Date.parse(r.approved_ts) : 0;
      if (r.state === "approved" && ms - at > 2000) Object.assign(r, { state: "dispatched", dispatched_ts: iso(at + 2000) });
      if (r.state === "dispatched" && ms - at > 5000) {
        Object.assign(r, { state: "executed", executed_ts: iso(at + 5000), result_code: "executed" });
        const inc = incidentsUntil(ms).find((i) => i.id === iid);
        if (inc) endedEarly.set(inc.fault.key, at + 5000);
        auditTrail.push({ actor: "collector:col-ac01", role: "system", action: "remediation.executed", target: iid, detail: { action_key: r.action_key }, at: iso(at + 5000) });
      }
      if (r.state === "executed" && ms - at > 15000) {
        Object.assign(r, { state: "verified", verified_ts: iso(at + 15000) });
        auditTrail.push({ actor: "aiops", role: "system", action: "remediation.verified", target: iid, detail: { action_key: r.action_key }, at: iso(at + 15000) });
      }
    }
  }
}
export function tickMock(ms = Date.now()) {
  advanceRemediations(ms);
}
