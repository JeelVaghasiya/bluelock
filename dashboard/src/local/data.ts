/* Collector local UI data access (:9090, on-site only) + a demo fallback. */
export interface Status {
  site: string;
  collector_id: string;
  region: string;
  version: string;
  config_hash: string;
  ingest_base: string;
  saas: { reachable: boolean; last_ack_ts: number | null; last_error: string | null; fail_streak: number; next_try_in_s: number };
  outbox: { depth: number; bytes: number; retrying: number; last_acked_seq: number; oldest_age_s: number };
  counters: Record<string, number | Record<string, number>>;
  egress_counts: Record<string, number>;
  raw: { n: number; oldest: number | null };
  leak_field_armed: boolean;
  window_s: number;
}
export interface EgressRow {
  id: number;
  payload_id: string | null;
  seq: number | null;
  sha256: string | null;
  status: "sealed" | "acked" | "rejected" | "blocked" | string;
  detail: string | null;
  ts: number;
  bytes: number | null;
  records: number | null;
  redactions: number | null;
}
export interface RawRow {
  id: number;
  source: string;
  collect_ts: number;
  body: string;
  spans: [number, number, string][];
}
export interface EgressDetail extends EgressRow {
  json: Record<string, unknown> | null;
  raw_sources: RawRow[];
  history?: { id: number; status: string; detail: string | null; ts: number; entry_hash: string; prev_hash: string }[];
  entry_hash?: string;
  prev_hash?: string;
}
export interface OutboxRow {
  payload_id: string;
  seq: number;
  created_ts: number;
  bytes: number;
  records: number;
  kinds: string;
  status: string;
  attempts: number;
  last_error: string | null;
  ack_status: string | null;
}

const demo = import.meta.env.VITE_MOCK === "1" || new URLSearchParams(location.search).has("demo");
export const isDemo = () => demo;

export async function get<T>(path: string, init?: RequestInit): Promise<T> {
  if (demo) return demoRoute(path) as T;
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

// ------------------------------------------------------------------ demo data
const RAW_LINES = [
  "Mar 03 10:42:17 web-01 sshd[811]: Failed password for alice from 203.0.113.77 port 51234 ssh2",
  "2026-11-21T10:42:17Z api-01 app: using credentials token=ghp_9fT2kLmQ8xZr4VbN1sPq7WcEy3HdJa6UoRiK",
  "2026-11-21T10:42:18Z api-02 app: GET /api/v2/orders/88213 200 34ms user=bob@acme.example",
  "2026-11-21T10:42:18Z db-01 pg: slow query took 812ms on db-01.acme.local",
  "2026-11-21T10:42:19Z worker-03 app: aws key AKIAIOSFODNN7EXAMPLE loaded for backup job",
  "2026-11-21T10:42:19Z api-01 app: contact Priya Sharma at priya.s@acme.example about invoice 1182",
];
const SPANS: [number, number, string][][] = [[], [[52, 92, "github_token"]], [], [], [[44, 64, "aws_access_key"]], []];
const now = () => Date.now() / 1000;
const pid = (n: number) => `0193a1c2-${(4096 + n).toString(16)}-7c3e-8a1b-${(1e11 + n * 7919).toString(16).slice(0, 12)}`;
function demoRoute(path: string): unknown {
  const t = now();
  if (path.startsWith("/local/status"))
    return {
      site: "acme", collector_id: "col-ac01", region: "ap-south", version: "0.1.0", config_hash: "9f3c1a7e5b20", ingest_base: "https://nginx-ap:8443",
      saas: { reachable: true, last_ack_ts: t - 0.6, last_error: null, fail_streak: 0, next_try_in_s: 0 },
      outbox: { depth: 0, bytes: 0, retrying: 0, last_acked_seq: 18234, oldest_age_s: 0 },
      counters: { raw_in: 412_880, parsed: 410_102, redactions_total: 48_211, identities_coded: 91_032, records_out: 182_400, payloads_sealed: 18_236, blocked_total: 2, acked: 18_234, duplicates_acked: 25, late_revisions: 7, findings: 342 },
      egress_counts: { sealed: 2, acked: 18_234, blocked: 2, rejected: 0 }, raw: { n: 412_880, oldest: t - 86_400 }, leak_field_armed: false, window_s: 5,
    } satisfies Status;
  if (path.startsWith("/local/egress/verify")) return { ok: true, broken_at: null, checked: 18_238, head: "a41f09c2e7d5b3f1" };
  if (path.match(/^\/local\/egress\/(entry\/)?[^?]+/)) {
    const n = 3;
    return {
      id: 18_238, payload_id: pid(n), seq: 18_234, sha256: "9f3c2b1d7e6a5f4c3b2a1908f7e6d5c4b3a29180f7e6d5c4b3a2918070f6e5d4", status: "acked", detail: null, ts: t - 1, bytes: 1_812, records: 38, redactions: 4,
      entry_hash: "a41f09c2e7d5b3f1c0d9e8f7a6b5c4d3", prev_hash: "77be10d3f2a9c8b7e6d5c4b3a2918070",
      json: {
        schema: "trustline.v1", payload_id: pid(n), collector_id: "col-ac01", seq: 18_234, region: "ap-south", sent_ts: new Date().toISOString(), redactions: 4,
        redactions_by_rule: { github_token: 1, aws_access_key: 1, email: 2 },
        records: [
          { type: "event", src: "h:9c1e2f8a0b3d", template: "Failed password for <u:7d21a9c0e4f1> from <ip:3fa1c09e7d22> port <num>", level: "warning", category: "auth", count: 1, redacted: 0 },
          { type: "event", src: "svc:8b20aa41c7e5", template: "using credentials token=<secret:github_token>", level: "warning", category: "app", count: 1, redacted: 1 },
          { type: "metric", src: "link:3fa1c09e7d22", metric: "rtt", min: 21.2, avg: 24.8, p50: 23.9, p95: 31.5, max: 33.0, unit: "ms", n: 5, rev: 0 },
        ],
      },
      raw_sources: RAW_LINES.map((b, i) => ({ id: 900 + i, source: i === 0 ? "syslog" : "http_push", collect_ts: t - 3, body: b, spans: SPANS[i] })),
      history: [
        { id: 18_237, status: "sealed", detail: null, ts: t - 1.4, entry_hash: "77be10d3f2a9", prev_hash: "1c0a9b8e7d6f" },
        { id: 18_238, status: "acked", detail: "stored", ts: t - 1, entry_hash: "a41f09c2e7d5", prev_hash: "77be10d3f2a9" },
      ],
    };
  }
  if (path.startsWith("/local/egress"))
    return Array.from({ length: 40 }, (_, i) => ({
      id: 18_238 - i, payload_id: pid(3 + i), seq: i === 6 ? null : 18_234 - i, sha256: (1e15 + i * 7_919_191).toString(16), status: i === 6 ? "blocked" : i === 0 ? "acked" : "acked",
      detail: i === 6 ? "extra_forbidden: metric.raw_msg" : "stored", ts: t - i * 1.02, bytes: 1_700 + ((i * 37) % 300), records: 30 + (i % 9), redactions: i % 4,
    }));
  if (path.startsWith("/local/raw"))
    return RAW_LINES.flatMap((b, i) => [{ id: 900 + i, source: "http_push", collect_ts: t - i, body: b, spans: SPANS[i] }]).filter((r) => !path.includes("secrets=true") || r.spans.length);
  if (path.startsWith("/local/outbox"))
    return Array.from({ length: 15 }, (_, i) => ({ payload_id: pid(3 + i), seq: 18_234 - i, created_ts: t - i, bytes: 1_800, records: 38, kinds: "metric,event", status: "acked", attempts: 1, last_error: null, ack_status: "stored" }));
  if (path.startsWith("/local/reidentify")) {
    const all = [
      { code: "h:9c1e2f8a0b3d", real: "web-01", prefix: "h" }, { code: "u:7d21a9c0e4f1", real: "alice", prefix: "u" }, { code: "ip:3fa1c09e7d22", real: "203.0.113.77", prefix: "ip" },
      { code: "link:3fa1c09e7d22", real: "uplink-1", prefix: "link" }, { code: "svc:8b20aa41c7e5", real: "api", prefix: "svc" },
    ];
    const q = new URLSearchParams(path.split("?")[1] ?? "");
    const code = q.get("code");
    if (code) return all.find((x) => x.code === code.replace(/[<>]/g, "")) ?? { code, real: "(unknown on this site)", prefix: "" };
    return all.filter((x) => !q.get("q") || x.code.includes(q.get("q")!) || x.real.includes(q.get("q")!));
  }
  if (path.startsWith("/local/actions")) return [];
  if (path.startsWith("/local/debug/leak-field")) return { armed: true };
  return {};
}
