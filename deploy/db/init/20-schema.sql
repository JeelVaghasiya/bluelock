-- TrustLine regional store. One database per region (residency is physical).
-- Every table carries tenant_id; row-level security is applied in 30-rls.sql.
-- TimescaleDB is used when available (hypertables, compression); plain Postgres also works.
DO $$ BEGIN
  CREATE EXTENSION IF NOT EXISTS timescaledb;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'timescaledb not available - using plain tables';
END $$;

CREATE FUNCTION tl_hypertable(tbl regclass, col text, chunk interval) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    PERFORM create_hypertable(tbl, col, chunk_time_interval => chunk, if_not_exists => true);
  ELSE
    EXECUTE format('CREATE INDEX ON %s (%I DESC)', tbl, col);
  END IF;
END $$;

-- ---------- telemetry (metadata only) ----------
CREATE TABLE metrics (
  tenant_id    text NOT NULL,
  collector_id text NOT NULL,
  src          text NOT NULL,
  metric       text NOT NULL,
  event_ts     timestamptz NOT NULL,
  win_s        smallint NOT NULL,
  rev          smallint NOT NULL DEFAULT 0,
  n            int NOT NULL,
  min double precision, avg double precision, p50 double precision,
  p95 double precision, max double precision,
  unit         text NOT NULL,
  src_tz       text NOT NULL,
  collect_ts   timestamptz NOT NULL,
  recv_ts      timestamptz NOT NULL,
  stored_ts    timestamptz NOT NULL DEFAULT now(),
  payload_id   uuid NOT NULL,
  late         boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id, src, metric, event_ts)
);
SELECT tl_hypertable('metrics', 'event_ts', INTERVAL '1 hour');

CREATE TABLE findings (
  tenant_id      text NOT NULL,
  collector_id   text NOT NULL,
  finding_id     uuid NOT NULL,
  event_ts       timestamptz NOT NULL,
  breach_ts      timestamptz NOT NULL,
  collect_ts     timestamptz NOT NULL,
  recv_ts        timestamptz NOT NULL,
  src            text NOT NULL,
  src_tz         text NOT NULL,
  category       text NOT NULL,
  pattern        text NOT NULL,
  severity       text NOT NULL,
  risk           double precision NOT NULL,
  layer          text NOT NULL,
  metric         text, value double precision, threshold double precision,
  baseline       double precision, z double precision,
  fail_count     int, distinct_users int, window_s int,
  payload_id     uuid NOT NULL,
  PRIMARY KEY (tenant_id, finding_id, event_ts)
);
SELECT tl_hypertable('findings', 'event_ts', INTERVAL '1 day');

CREATE TABLE events (
  tenant_id    text NOT NULL,
  collector_id text NOT NULL,
  src          text NOT NULL,
  template_id  text NOT NULL,
  event_ts     timestamptz NOT NULL,
  win_s        smallint NOT NULL,
  category     text NOT NULL,
  level        text NOT NULL,
  template     text NOT NULL,
  count        int NOT NULL,
  redacted     int NOT NULL,
  src_tz       text NOT NULL,
  collect_ts   timestamptz NOT NULL,
  recv_ts      timestamptz NOT NULL,
  payload_id   uuid NOT NULL,
  PRIMARY KEY (tenant_id, src, template_id, event_ts)
);
SELECT tl_hypertable('events', 'event_ts', INTERVAL '1 day');

CREATE TABLE heartbeats (
  tenant_id     text NOT NULL,
  collector_id  text NOT NULL,
  event_ts      timestamptz NOT NULL,
  recv_ts       timestamptz NOT NULL,
  version       text, config_hash text,
  cpu double precision, mem_mb double precision,
  buffer_depth  int, buffer_bytes bigint,
  last_acked_seq bigint, last_assigned_seq bigint,
  redactions_total bigint, blocked_total bigint,
  dup_source_events bigint, late_beyond_horizon bigint,
  uptime_s bigint, saas_reachable boolean,
  PRIMARY KEY (tenant_id, collector_id, event_ts)
);
SELECT tl_hypertable('heartbeats', 'event_ts', INTERVAL '1 day');

-- ---------- fleet and topology ----------
CREATE TABLE collectors (
  tenant_id            text NOT NULL,
  collector_id         text NOT NULL,
  region               text NOT NULL,
  first_seen           timestamptz NOT NULL DEFAULT now(),
  last_seen            timestamptz,
  last_hb_event_ts     timestamptz,
  version              text,
  config_hash          text,
  expected_config_hash text,
  cert_serial          text,
  state                text NOT NULL DEFAULT 'online',
  state_reason         text,
  skew_ms              double precision,
  cpu                  double precision,
  mem_mb               double precision,
  buffer_depth         int,
  buffer_bytes         bigint,
  last_acked_seq       bigint,
  last_assigned_seq    bigint,
  redactions_total     bigint,
  blocked_total        bigint,
  PRIMARY KEY (tenant_id, collector_id)
);

CREATE TABLE topology (
  tenant_id  text PRIMARY KEY,
  nodes      jsonb NOT NULL,
  edges      jsonb NOT NULL,
  updated_ts timestamptz NOT NULL DEFAULT now()
);

-- ---------- health ----------
CREATE TABLE health_states (
  tenant_id  text NOT NULL,
  src        text NOT NULL,
  kind       text,
  role       text,
  collector_id text,
  state      text NOT NULL CHECK (state IN ('excellent','good','fair','poor')),
  stale      boolean NOT NULL DEFAULT false,
  reason     text,
  signals    jsonb NOT NULL DEFAULT '{}',
  since      timestamptz NOT NULL DEFAULT now(),
  last_data_ts timestamptz,
  updated_ts timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, src)
);

CREATE TABLE health_transitions (
  id         bigserial PRIMARY KEY,
  tenant_id  text NOT NULL,
  src        text NOT NULL,
  old_state  text,
  new_state  text NOT NULL,
  reason     text,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON health_transitions (tenant_id, src, at DESC);

-- ---------- AIOps ----------
CREATE TABLE alerts (
  tenant_id   text NOT NULL,
  alert_id    uuid PRIMARY KEY,
  src         text NOT NULL,
  metric      text,
  layer       text NOT NULL,
  z           double precision,
  score       double precision,
  value       double precision,
  baseline    double precision,
  onset_ts    timestamptz NOT NULL,
  last_ts     timestamptz NOT NULL,
  incident_id text,
  suppressed_reason text
);
CREATE INDEX ON alerts (tenant_id, onset_ts DESC);

CREATE TABLE incidents (
  tenant_id      text NOT NULL,
  incident_id    text NOT NULL,
  status         text NOT NULL DEFAULT 'open',
  priority       text NOT NULL,
  severity       text NOT NULL,
  title          text NOT NULL,
  root_cause_src text,
  affected       jsonb NOT NULL DEFAULT '[]',
  evidence       jsonb NOT NULL DEFAULT '{}',
  alert_count    int NOT NULL DEFAULT 1,
  breach_ts      timestamptz,
  opened_ts      timestamptz NOT NULL DEFAULT now(),
  alert_ts       timestamptz,
  updated_ts     timestamptz NOT NULL DEFAULT now(),
  resolved_ts    timestamptz,
  PRIMARY KEY (tenant_id, incident_id)
);

CREATE TABLE remediations (
  tenant_id     text NOT NULL,
  action_id     uuid PRIMARY KEY,
  incident_id   text NOT NULL,
  action_key    text NOT NULL,
  params        jsonb NOT NULL DEFAULT '{}',
  policy        jsonb NOT NULL DEFAULT '{}',
  state         text NOT NULL,
  collector_id  text,
  proposed_by   text,
  proposed_ts   timestamptz NOT NULL DEFAULT now(),
  approved_by   text,
  approved_ts   timestamptz,
  dispatched_ts timestamptz,
  executed_ts   timestamptz,
  verified_ts   timestamptz,
  rollback_of   uuid,
  result_code   text
);

CREATE TABLE baselines (
  tenant_id  text NOT NULL,
  key        text NOT NULL,
  state      jsonb NOT NULL,
  updated_ts timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, key)
);

-- ---------- delivery, audit, measurement ----------
CREATE TABLE received_payloads (
  payload_id   uuid PRIMARY KEY,
  tenant_id    text NOT NULL,
  collector_id text NOT NULL,
  seq          bigint NOT NULL,
  sha256       text NOT NULL,
  records      int NOT NULL,
  redactions   int NOT NULL DEFAULT 0,
  redactions_by_rule jsonb NOT NULL DEFAULT '{}',
  received_at  timestamptz NOT NULL,
  stored_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, collector_id, seq)
);
CREATE INDEX ON received_payloads (tenant_id, received_at DESC);

CREATE TABLE ingest_audit (
  id           bigserial PRIMARY KEY,
  tenant_id    text NOT NULL,
  collector_id text,
  payload_id   uuid,
  seq          bigint,
  sha256       text,
  region       text NOT NULL,
  result       text NOT NULL,
  detail       text,
  attempt      int,
  lane         text,
  attempt_ts   timestamptz,
  received_at  timestamptz NOT NULL DEFAULT now(),
  skew_ms      double precision
);
CREATE INDEX ON ingest_audit (tenant_id, payload_id);
CREATE INDEX ON ingest_audit (tenant_id, received_at DESC);

CREATE TABLE delivery_counters (
  tenant_id     text NOT NULL,
  collector_id  text NOT NULL,
  js_duplicates bigint NOT NULL DEFAULT 0,
  db_conflicts  bigint NOT NULL DEFAULT 0,
  gaps_open     int NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, collector_id)
);

CREATE TABLE latency_samples (
  tenant_id  text NOT NULL,
  kind       text NOT NULL,        -- dash | alert | offline
  ref_id     text,
  lane       text,
  event_ts   timestamptz,
  collect_ts timestamptz,
  sent_ts    timestamptz,
  recv_ts    timestamptz,
  stored_ts  timestamptz,
  push_ts    timestamptz,
  render_ts  timestamptz,
  total_ms   double precision NOT NULL,
  excluded   text,                 -- null | backlog | revision
  at         timestamptz NOT NULL DEFAULT now()
);
SELECT tl_hypertable('latency_samples', 'at', INTERVAL '1 day');

CREATE TABLE thresholds_override (
  tenant_id  text PRIMARY KEY,
  doc        jsonb NOT NULL,
  updated_by text,
  updated_ts timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id        bigserial PRIMARY KEY,
  tenant_id text NOT NULL,
  actor     text NOT NULL,
  role      text,
  action    text NOT NULL,
  target    text,
  detail    jsonb NOT NULL DEFAULT '{}',
  at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON audit_log (tenant_id, at DESC);
