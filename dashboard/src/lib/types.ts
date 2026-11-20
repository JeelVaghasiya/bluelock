export type HealthState = "excellent" | "good" | "fair" | "poor";
export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type Kind = "host" | "svc" | "link" | "ip" | "user" | "other";

export interface Signal {
  state: HealthState;
  value: number;
  stat: string;
  reason: string;
  ts?: string;
}

export interface Source {
  src: string;
  kind: Kind;
  role: string;
  collector_id: string | null;
  state: HealthState;
  stale: boolean;
  reason: string;
  signals: Record<string, Signal>;
  since: string;
  last_data_ts?: string;
}

export interface IncidentRow {
  incident_id: string;
  status: "open" | "acknowledged" | "resolved";
  priority: "P1" | "P2" | "P3" | "P4";
  severity: Severity;
  title: string;
  root_cause_src: string | null;
  affected?: string[];
  alert_count: number;
  breach_ts?: string;
  opened_ts: string;
  alert_ts?: string;
  resolved_ts?: string | null;
  confidence?: number;
  rule?: string;
}

export interface EvidenceSignal {
  src: string;
  metric: string;
  stat: string;
  value: number | null;
  baseline: number | null;
  z: number | null;
  layer: string;
  onset_ts: string;
  weight: number;
  category?: string;
  pattern?: string | null;
}

export interface TraceStep {
  step: string;
  layer?: string;
  detail?: string;
  chosen?: string;
  rejected?: { src: string; why: string }[];
  rule?: string;
  matched?: string[];
  members?: number;
}

export interface Evidence {
  incident: string;
  priority: string;
  title: string;
  root_cause: string;
  root_role?: string;
  affected: string[];
  signals: EvidenceSignal[];
  layers_agreeing: string[];
  persistence: number;
  rule: string;
  confidence: number;
  confidence_parts: { magnitude: number; agreement: number; persistence: number };
  action: {
    key: string;
    text: string;
    reversible: boolean;
    rollback_key?: string;
    requires_role: string;
    max_blast_radius?: number;
    executable: boolean;
  };
  why: string;
  trace: TraceStep[];
  suppressed: { alert_id?: string; src: string; metric?: string; reason: string }[];
  noise: { raw_alerts: number; incidents: number };
  blast_radius: number;
}

export interface Remediation {
  action_id: string;
  incident_id: string;
  action_key: string;
  params: { target?: string };
  policy: { checks?: { check: string; ok: boolean; detail: string }[]; ok?: boolean };
  state: "proposed" | "refused" | "approved" | "rejected" | "dispatched" | "executed" | "verified" | "failed" | "rolled_back";
  collector_id?: string;
  proposed_by?: string;
  proposed_ts: string;
  approved_by?: string | null;
  approved_ts?: string | null;
  dispatched_ts?: string | null;
  executed_ts?: string | null;
  verified_ts?: string | null;
  rollback_of?: string | null;
  result_code?: string | null;
}

export interface IncidentDetail {
  incident: IncidentRow & { evidence: Evidence };
  alerts: { alert_id: string; src: string; metric: string; layer: string; z: number | null; onset_ts: string; suppressed_reason: string | null }[];
  remediations: Remediation[];
  audit: { actor: string; role: string; action: string; detail: Record<string, unknown>; at: string }[];
}

export interface Collector {
  tenant_id?: string;
  collector_id: string;
  region: string;
  state: "online" | "offline" | string;
  state_reason?: string;
  last_hb_event_ts?: string;
  version?: string;
  config_hash?: string;
  expected_config_hash?: string | null;
  cpu?: number;
  mem_mb?: number;
  buffer_depth?: number;
  buffer_bytes?: number;
  last_acked_seq?: number;
  last_assigned_seq?: number;
  redactions_total?: number;
  blocked_total?: number;
  skew_ms?: number | null;
  cert_serial?: string;
}

export interface Overview {
  states: Record<HealthState, number>;
  states_by_kind: { state: HealthState; kind: string; n: number }[];
  incidents: IncidentRow[];
  noise: { raw_alerts_24h: number; incidents_24h: number; reduction: number };
  privacy: {
    redactions_24h: number;
    payloads_24h: number;
    records_24h: number;
    by_rule: { rule: string; n: number }[];
    lines_24h?: number;
    templates_24h?: number;
    blocked_total?: number;
  };
  slo: Record<string, { n: number; p50: number; p95: number; max: number; target: number }>;
  fleet: { collector_id: string; state: string; buffer_depth: number; last_hb_event_ts: string }[];
  findings_24h: number;
}

export interface OverviewSeries {
  step_s: number;
  redactions: { t: string; rule: string; n: number }[];
  security: { t: string; severity: Severity; n: number }[];
  events: { t: string; level: string; n: number }[];
  payloads: { t: string; payloads: number; records: number }[];
  latency: { t: string; kind: string; p95: number }[];
  latest: { src: string; metric: string; p95: number; avg: number }[];
  disk_forecast: { src: string; avg: number }[];
}

export interface MetricQuery {
  src: string;
  metric: string;
  stat: string;
  points: { t: string; v: number; rev?: number }[];
  band: { median: number; lo: number; hi: number } | null;
  thresholds: { excellent: number; good: number; fair: number; lower_is_worse?: boolean; unit?: string } | null;
  alerts: { onset_ts: string; z: number; incident_id: string }[];
}

export interface Topology {
  nodes: { src: string; kind: Kind; role: string; state?: HealthState }[];
  edges: { dependent: string; dependency: string; kind: string }[];
}

export interface EventRow {
  src: string;
  template_id: string;
  event_ts: string;
  category: string;
  level: string;
  template: string;
  count: number;
  redacted: number;
  payload_id: string;
  collector_id: string;
}

export interface Finding {
  finding_id: string;
  event_ts: string;
  breach_ts: string;
  src: string;
  category: string;
  pattern: string;
  severity: Severity;
  risk: number;
  layer: string;
  metric?: string | null;
  value?: number | null;
  threshold?: number | null;
  fail_count?: number | null;
  distinct_users?: number | null;
  window_s?: number | null;
  payload_id: string;
}

export interface Slo {
  targets: Record<string, number>;
  kinds: Record<string, { n: number; p50: number; p95: number; max: number }>;
  excluded: Record<string, number>;
  hops_ms: Record<string, number | null>;
  histogram: { kind: string; b: number; n: number }[];
  recent: { kind: string; total_ms: number; at: string }[];
  bucket_ms: number;
}

export interface Me {
  email: string;
  tenant: string;
  role: "tenant-admin" | "tenant-viewer" | "platform-ops";
  tenant_name?: string;
  region?: string;
  regions_connected?: string[];
}

export interface WsEnvelope {
  v: 1;
  type: string;
  tenant?: string;
  region?: string;
  id?: string | null;
  timing?: Record<string, string | boolean | undefined>;
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any
}
