/* Simulation Lab data access - talks to the simulator on the laptop (:8090/api). */
export interface Scenario {
  kind: string;
  source: "simulator" | "collector";
  category: string;
  title: string;
  what: string;
  expect: string;
  target_type: string | null;
  targets: string[];
  magnitude: number | null;
  duration_s: number | null;
}
export interface StoryStep { at: number; op: string; label: string; kind?: string; target?: string; test?: string; state?: "pending" | "done" | "error"; result?: { ok: boolean; detail?: string } }
export interface Story { id: string; title: string; minutes: number; blurb: string; steps: StoryStep[]; sla?: boolean }
export interface BoundaryTest { test: string; title: string; what: string; expect: string }
export interface Catalogue {
  sites: Record<string, { hosts: Record<string, string>; services: Record<string, string[]>; links: string[]; service_links: Record<string, string>; attacker_ips: string[]; tz: string }>;
  scenarios: Scenario[];
  boundary: BoundaryTest[];
  stories: Story[];
  ingest_base: string;
  collector_local: string;
}
export interface Fault { kind: string; target: string | null; magnitude: number; elapsed_s: number; remaining_s: number }
export interface TimelineItem { ts: number; kind: string; title: string; detail: string; ok: boolean }
export interface BoundaryResult { test: string; title: string; expect: string; status: number | null; detail: string; passed: boolean; error: boolean; ms: number; ts: number }
export interface SimState {
  now: number;
  sites: Record<string, { faults: Fault[]; failed_over: string[]; blocked_ips: string[]; sent_json: number; sent_syslog: number; push_errors: number; last_error: string | null }>;
  story: (Omit<Story, "minutes" | "blurb"> & { elapsed_s: number; finished: boolean }) | null;
  timeline: TimelineItem[];
  boundary: Record<string, BoundaryResult>;
}
export interface CollectorStatus {
  reachable: boolean;
  error?: string;
  collector_id?: string;
  ingest_base?: string;
  saas?: { reachable: boolean | null; last_error: string | null; fail_streak: number };
  outbox?: { depth: number; bytes: number; oldest_age_s: number };
  counters?: Record<string, number>;
  outage_until?: number | null;
  leak_field_armed?: boolean;
}
export interface LivePoint { t: number; n: number; h: Record<string, (number | null)[]>; l: Record<string, (number | boolean | null)[]>; s: Record<string, (number | boolean | null)[]> }
export interface FeedLine { id: number; ts: number; src: string; level: string; text: string; tag?: string | null }

export async function sim<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method: init?.method ?? "GET",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    body: init?.body ? JSON.stringify(init.body) : undefined,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return r.json();
}

export interface SlaCheck { id: string; title: string; target: number; measured: number | null; n: number; unit: string; pass: boolean | null; scenario: string | null; detail: string }
export interface SlaStatus {
  configured: boolean;
  url?: string;
  email?: string;
  reachable?: boolean;
  error?: string;
  targets: Record<string, number>;
  at?: number;
  checks?: SlaCheck[];
  collectors?: { collector_id: string; state: string; state_reason: string | null; buffer_depth: number | null; last_acked_seq: number | null; last_assigned_seq: number | null; last_hb_event_ts: string | null }[];
  stale_sources?: number;
  sources?: number;
  noise?: { incidents: number; alerts: number; reduction: number | null };
  recent_incidents?: { incident_id: string; title: string; priority: string; status: string; alert_count: number; opened_ts: string; confidence: number | null; rule: string | null }[];
  hops_ms?: Record<string, number | null>;
  minutes?: number;
}
