-- Tenant isolation in the database itself.
-- FORCE makes even the table owner obey the policy; app roles are NOBYPASSRLS;
-- current_setting(..., true) returns NULL when unset, so a forgotten SET fails CLOSED.
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['metrics','findings','events','heartbeats','collectors','topology','health_states',
   'health_transitions','alerts','incidents','remediations','received_payloads','ingest_audit','delivery_counters',
   'latency_samples','thresholds_override','baselines','audit_log'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
  EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
     USING (tenant_id = current_setting('app.tenant_id', true))
     WITH CHECK (tenant_id = current_setting('app.tenant_id', true))$p$, t);
 END LOOP; END $$;

GRANT SELECT, INSERT, UPDATE ON ingest_audit, collectors, received_payloads, delivery_counters TO tl_ingest;
GRANT SELECT, UPDATE ON remediations TO tl_ingest;               -- /v1/actions marks dispatched
GRANT SELECT ON thresholds_override TO tl_ingest;                -- /v1/config
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tl_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tl_api;
GRANT INSERT, UPDATE ON incidents, remediations, thresholds_override, audit_log, latency_samples TO tl_api;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO tl_ingest, tl_worker, tl_api;

-- Cross-tenant views that deliberately bypass RLS, each returning only non-sensitive
-- aggregates. Owned by the superuser (SECURITY DEFINER); callable only by tl_api / tl_worker.

-- Fleet for platform-ops: collector health only, no telemetry.
CREATE FUNCTION fleet_overview() RETURNS SETOF collectors
  LANGUAGE sql SECURITY DEFINER SET search_path = public
  AS $$ SELECT * FROM collectors $$;
REVOKE ALL ON FUNCTION fleet_overview() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fleet_overview() TO tl_api;

-- Residency proof: row counts per tenant in THIS regional database.
CREATE FUNCTION residency_counts() RETURNS TABLE(tenant_id text, metrics bigint, events bigint, findings bigint)
  LANGUAGE sql SECURITY DEFINER SET search_path = public
  AS $$
    SELECT t.tenant_id,
           (SELECT count(*) FROM metrics m WHERE m.tenant_id = t.tenant_id),
           (SELECT count(*) FROM events e WHERE e.tenant_id = t.tenant_id),
           (SELECT count(*) FROM findings f WHERE f.tenant_id = t.tenant_id)
    FROM (SELECT DISTINCT tenant_id FROM received_payloads) t $$;
REVOKE ALL ON FUNCTION residency_counts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION residency_counts() TO tl_api;

-- Workers need the list of active tenants to run the sweeper per tenant.
CREATE FUNCTION active_tenants() RETURNS SETOF text
  LANGUAGE sql SECURITY DEFINER SET search_path = public
  AS $$ SELECT DISTINCT tenant_id FROM collectors $$;
REVOKE ALL ON FUNCTION active_tenants() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION active_tenants() TO tl_worker, tl_api;
