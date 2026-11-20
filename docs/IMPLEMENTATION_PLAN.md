# TrustLine: end-to-end implementation plan (CICADA 2K26, 24-hour hackathon)

## 0. Facts that change the plan (read first)

1. **The clock is already running.** It is now Fri 20 Nov 2026, about 19:40 IST. The photo of the evaluation sheet is from 15:41 today and the sheet reads "CICADA 2K26 · 24-HOUR HACKATHON". The example payload in the Phase 2 doc is dated 2026-11-21 09:42 IST. My assumption is that the event started this afternoon and the final demo is Sat afternoon, which leaves **about 18 hours of build time**. The milestones in section B are hours after build start (T0 = 20:00 Fri), with the final demo assumed at T+18 (≈14:00 Sat). Confirm the real checkpoint times (question H1).
2. **The organiser telemetry stream is mandatory** (sheet "Mandatory" row, guide §3). The Phase 2 doc never mentions it. The collector needs a pluggable organiser adapter plus a mapping file, and our simulator becomes the backup source.
3. **This machine (Windows 11) is missing a lot:** no Docker Desktop, no WSL2, no `gh`, no `make`. It has 7.9 GB RAM and an i5-9300H (8 threads). System Python is 3.14 (3.13 also installed via uv). Node is 24.13.1, OpenSSL 3.5.5 comes with Git Bash, and uv is 0.10.2. Docker plus WSL2 is the first blocker. The full stack of 17 containers needs about 2–2.5 GB, which fits if WSL is capped at 5 GB. PyTorch and Ollama do not fit, so both are cut.
4. **The GitHub repo `JeelVaghasiya/bluelock` is PUBLIC and EMPTY.** Do not commit `others/`: it holds the organiser's judge guide and a 735 KB research doc.
5. **The Phase 2 numbers miss the real-time targets as written.** 10 s windows plus 2 s batches put dashboard p95 at about 11 s (target ≤10 s). A 3-of-5 persistence rule on 10 s windows means alerts take at least 30 s (target ≤15 s). Fixes are in A.3: 5 s windows, 1 s batches, and a fast-path threshold alert at the edge.

---

## A. What the guide requires, how TrustLine meets it, and the gaps

### A.1 Phases, marks and what judges check

| Phase (marks) | What judges check (guide plus physical sheet) | TrustLine answer | Evidence to show |
|---|---|---|---|
| **1. Problem understanding & ideation (10)** | Why raw data stays local and SaaS gets metadata only. Use cases: infra health, latency/loss, failed logins, fleet health. A prioritised feature list with a minimum viable demo (MVP). What stays local, what is redacted, what is sent. Tenant separation and who sees what. A feasible 24 h scope, roles and simulated-telemetry plan. **Sheet adds Q1 "situation before observability" and Q2 "what you now observe".** | Phase 2 doc covers most of it. Add Q1/Q2 answers, the MVP list and the allow-list field list (`docs/allow-list.md`). | One-page data-flow sketch with the trust boundary. Allow-list field table. Redaction examples. Owner table. |
| **2. Architecture & technical design (25)** | Two tiers with an authenticated link. Collect→filter/redact→aggregate→analyse→policy→transmit. Allow-listing, secret scrubbing, pseudonymous IDs. Collector auth, TLS, local retention, audit. Telemetry plan for CPU/mem/disk/bandwidth/latency/loss/connectivity. AIOps beyond thresholds, correlation, explanations. Scalability. Global-ready design: regional ingest, residency, UTC, out-of-order, idempotent delivery. **Evidence asked:** diagram with every hop labelled, payload schema and allow-list mechanism, behaviour when SaaS is unreachable plus duplicate avoidance, tenant isolation approach. | Phase 2 doc is strong here. Apply the corrections in A.3 as ADRs in `docs/adr/`. | Diagram H1–H9 (+H2b). Pydantic `extra="forbid"` code. Outbox and three duplicate layers. Row-level security (RLS). |
| **3. Implementation & working prototype (35, of which 10 are real-time and reliability)** | Working collector on the **organiser stream**. Raw logs and secrets visible on the collector side only. SaaS shows allow-listed fields per tenant. 4 health states with clear thresholds and stale-data handling. Contextual anomaly alerts (not only threshold breaches). Outage detection, buffering, retry and resume. **Measured** dashboard ≤10 s p95, alert ≤15 s, offline ≤60 s, zero loss and zero duplicates. Egress proof panel. **Evidence:** side-by-side raw secret vs redacted SaaS view, timestamps, forced outage with sent vs received IDs, egress log for one payload. | Collector local UI (raw viewer + egress proof). SaaS Events explorer showing redacted templates. Health engine. Fast-path alerts. Outbox. Delivery Ledger. Measured SLO panel. | Live only. "Score only what is demonstrated live." |
| **4. Final demo, validation & presentation (30)** | End-to-end from raw source to actionable recommendation. Correct reaction to scenarios injected on the spot. Few false alarms. **Explainable AIOps, weighted higher:** why flagged, which signals, why this action, confidence, reasoning path. Several alerts merged into one incident with affected components. Honest limitations. Innovation in privacy, correlation or noise. Confident answers to the §7 questions without slides. | L1/L2(/L3) detection, L4 graph correlation, L5 playbook evidence with weights, confidence breakdown and reasoning trace. Remediation with policy check, approval, audit and rollback. Ground-truth scoring to prove few false alarms. | Incident page with the Evidence panel. "7 alerts → 1 incident". Q&A cheat sheet. |
| **Mandatory (all teams)** | (1) real-time measured from timestamps, (2) shared organiser stream, (3) egress proof panel. | Measured latency pipeline with render acks. Organiser adapter. Local egress panel. | Never cut these. |
| **Bonus (tie-break)** | Privacy pipeline. Local buffering. Explainable AIOps. Alert-noise reduction. **Safe remediation simulation (approval + audit + rollback)**. Global-ready delivery (UTC, out-of-order, idempotent). | All six are covered once the remediation gap below is closed. | |

The live scenarios in guide §8 are: secret in a raw log, collector loses SaaS, repeated failed logins, latency rises, CPU/mem deviates from baseline, several alerts belong to one incident, duplicate/out-of-order delivery, and remediation proposed. All are scripted in section F.

### A.2 Coverage of the Phase 2 doc against the guide

The Phase 2 doc fully covers: two tiers, mTLS, the pipeline, allow-list, scrubbing, HMAC pseudonyms, retention, hash-chained audit, the telemetry plan, AIOps L0–L5, JetStream/Timescale scaling, residency, UTC, outbox, dedupe layers, RLS, health thresholds and the timing targets.

### A.3 Gaps and corrections

Each item is a change to the frozen contracts or an ADR.

| # | Gap or bug | Fix (decision) |
|---|---|---|
| G1 | **No organiser stream ingestion** (mandatory). | `collector/sources/organiser.py` with transports `http_push`, `http_poll`, `sse`, `ws`, `udp`, `file`. Field mapping lives in `config/organiser_mapping.yaml`, so Owner A edits YAML, not code, once the spec arrives. The simulator emits our best-guess format and replays `ground_truth.jsonl`. |
| G2 | **Dashboard p95 maths fails.** A sample waits up to one window: 10 s window + 2 s batch + ~1 s path gives p95 ≈ 11–12 s. | **5 s tumbling windows, 1 s batch.** p95 latency for the oldest sample in a window ≈ 4.75 + 0.5 grace + ≤1 batch + ~0.3 transport/store/push + 0.1 render ≈ **6.5 s**. Latency is measured conservatively from the window start, i.e. the oldest sample. ADR-0001. |
| G3 | **Alert ≤15 s fails.** 3-of-5 persistence on 10 s windows takes at least 30 s. | Two alert paths. **(a) Edge fast path:** the collector's stage 5 checks raw 1 s samples, and 3 consecutive samples beyond the Poor threshold produce a `FindingRecord(layer="T", severity=high, breach_ts=first sample)` that is flushed immediately. Expected about 3–5 s. **(b) Anomaly path:** \|z\|>3 in 2 of the last 3 five-second windows (≥10 s). Clearing still needs 3 normal windows (hysteresis). ADR-0002. |
| G4 | The side-by-side "redacted metadata on SaaS" needs something textual. The doc only sends metrics, findings and heartbeats. | New **`EventRecord`**: a redacted, masked log template plus count per 5 s window. It is length-capped and char-class-restricted, and passes through an **egress guard** that re-scans the final serialised bytes with the secret rules and blocks on any hit (defence in depth). This also powers a Log-Patterns explorer. |
| G5 | **Topology at SaaS is impossible as designed.** `config/topology.yaml` uses real names, but SaaS only sees HMAC codes and has no key. | The site topology (real names) stays in Tier 1, in `config/sites/<site>/topology.yaml`. The collector pseudonymises it and sends a **`TopologyRecord`** (nodes and edges as codes plus role/kind) at start and every 5 min. AIOps L4 builds its graph from that record. |
| G6 | **Safe remediation simulation** (bonus and scenario 8) has no design for policy check, approval, audit or rollback. | Remediation state machine in api: `proposed → policy_passed/failed → approved/rejected → dispatched → executed → verified → (rolled_back)`. The **collector pulls approved actions outbound** (`GET /v1/actions` through the same nginx mTLS door, **H2b**, the Datadog Private-Action-Runner pattern). It checks a **local allow-list**, executes against the site-local simulator (`/clear` or `/failover`), and reports an `ActionResultRecord`. A rollback button dispatches the inverse action. Everything is written to `audit_log`. |
| G7 | Source-level duplicates and out-of-order events from the organiser stream (scenario 7) are not handled. The doc dedupes payloads only. | Stage 2 adds a TTL dedupe of source events (sha1 of source, ts and body; 10 min LRU). Windows are by event time with a 0.5 s grace. Late samples within 2 min trigger a **re-emit of the window with `rev+1`**. SaaS upserts on `(tenant, src, metric, event_ts)` `WHERE rev < EXCLUDED.rev`. Samples later than 2 min are kept in raw.db and counted (`late_beyond_horizon`). |
| G8 | Clock skew uses `sent_ts`, but retries must be byte-identical, so backlog payloads would always show skew. | The body's `sent_ts` is the time the payload was sealed. Per-attempt data goes in headers (`X-TL-Attempt`, `X-TL-Attempt-Ts`, `X-TL-Lane`). Skew = `recv − X-TL-Attempt-Ts`, measured on the live lane only. Backlog payloads are excluded from latency p95, and the panel says so. |
| G9 | A gap check on "last seq" false-alarms because the two lanes (live first, backlog oldest-first) arrive out of order. | Set-based ledger: `expected = max(seq) − min(seq) + 1` vs `count(distinct seq)`. A hole is only a "missing data" event if it stays unfilled for 120 s **and** the collector reports `last_assigned_seq` beyond it. |
| G10 | Schema inconsistencies: the evidence example uses `error_rate` and `rtt_p95`, the `metric` Literal lacks `error_rate`/`jitter`/`net_util`/`disk_hours_to_full`, and `unit` lacks `h`. | Fixed in schema v1 (C.3). Evidence uses `metric` plus `stat` (`rtt`, `p95`). |
| G11 | "Service availability" and "prioritise incidents" are in the problem statement but not explicit in the doc. | `up` and `error_rate` per `svc:` come from HTTP checks. Incident **priority P1–P4** = f(severity, blast radius, security). |
| G12 | TimescaleDB continuous aggregates do not support RLS. | No continuous aggregates or compression in the hackathon build. Use `time_bucket()` on the hypertables (volumes are small), and state continuous aggregates as the production path. |
| G13 | RLS pitfall: the table owner bypasses RLS unless `FORCE ROW LEVEL SECURITY` is set, and superusers always bypass it. | Apps use the non-owner roles `tl_ingest`, `tl_worker`, `tl_api` with `NOBYPASSRLS`. `FORCE RLS` is on every table. `current_setting('app.tenant_id', true)` makes an unset tenant fail closed. Platform-ops sees the fleet through a `SECURITY DEFINER` function owned by the superuser. |
| G14 | "Revoked cert refused at handshake" needs a CRL, which a demo CA does not have. | A self-made or rogue-CA cert fails the TLS handshake (true). A revoked serial gets **403 at ingest** via the control-plane revocation list. State this honestly. |
| G15 | The 10 min warm-up conflicts with judges arriving early, and restarts reset baselines. | Persist baselines to the `baselines` table every 30 s. Warm-up 5 min (60 windows). Start the stack at least 15 min before each checkpoint. The simulator has `--prewarm 30m`, which backfills history with past `event_ts`. |
| G16 | "Few false alarms" is never measured. | `scripts/score.py` compares the simulator's `ground_truth.jsonl` with incidents: precision, recall, mean time to detect. Shown as a card in Trust Center. |
| G17 | Explainability needs a "reasoning path". | The evidence JSON gains `trace[]` (detect → corroborate → correlate → root-cause choice with rejected alternatives → playbook rule → policy), `suppressed[]` (alerts absorbed and why) and a confidence breakdown. |
| G18 | PyTorch and Ollama will not fit in 8 GB RAM. | L3 becomes a **scikit-learn `MLPRegressor` autoencoder (5→8→2→8→5)**, still a neural network and no new dependency. L6 is cut by default. |
| G19 | SaaS containers must never see the HMAC keys, but a global compose `env_file: .env` would leak them. | No `env_file` anywhere. Each service gets only an explicit `environment:` list. |
| G20 | The doc has H7 browser HTTPS on 443. | Local demo uses `http://localhost:3000`, stated as a limitation. Optional TLS through the dashboard nginx using our CA (cut early). |
| G21 | The sheet's Phase 1 Q1/Q2 questions are not prepared. | Answers in `docs/qa-cheatsheet.md` (Q1: blind spots, or raw logs shipped out; Q2: consolidated 4-state view, egress proof, explainable incidents). |
| G22 | Ownership is unbalanced: D had dashboard + api + simulator + real-time panel. | **Simulator moves to C** (pairs with fault ground truth). **Writer moves to B** (DB owner). The local UI pages move to A. |

---

## B. Build order, milestones and time boxes

Times are hours after build start. With T0 = 20:00 Fri: T+5 ≈ 01:00, T+9 ≈ 05:00, T+13 ≈ 09:00, T+18 ≈ 14:00 Sat. Re-anchor once the checkpoint times are confirmed. Plan two nap shifts of about 90 min: A and C at about T+9.5, B and D at about T+11.

| Milestone | Time | Owner A: Collector & privacy | Owner B: Transport, DB, security | Owner C: Simulator & AIOps | Owner D: API & dashboard | Exit gate |
|---|---|---|---|---|---|---|
| **M0 Contracts + skeleton** | T+0 → T+1.5 | `schema/` v1 models + fixtures + tests. `config/collector.yaml`. `organiser_mapping.yaml` draft. | Repo init, `.gitattributes`, `compose.yaml`, `gen.sh` certs, nginx template, `nats.conf`, DB init SQL plus an RLS-on-hypertable smoke test. `scripts/tl.py init/certs/up`. | Simulator skeleton (sites, 1 s metric push, syslog sshd). `/inject` stub. `thresholds.yaml`, `playbook.yaml`, site topology YAMLs. | Vite app scaffold, tokens, AppShell, `types.ts` from the WS/REST contract, MSW mocks from fixtures. api skeleton: login JWT, `/ws` hello. | **G0 contracts frozen:** schema v1, `POST /v1/ingest` response, WS envelope, `/inject` API, YAML formats. Infra containers healthy. |
| **M1 Walking skeleton (critical path)** | T+1.5 → T+5 | Stages 1 (HTTP push, syslog), 2 (parse + dedupe), 3-min (HMAC codes), 4 (5 s windows), 6 (policy), 7 (outbox + mTLS sender, pre-issued certs). Heartbeat. | ingest-api (DN→tenant, residency, schema, JetStream publish with `Nats-Msg-Id`, audit, ack). Writer: upserts, `received_payloads`, `live.*` publish. | All 9 signal families realistic (daily cycle, AR(1), log-normal RTT, two-state loss). Organiser replay mode. Health engine + stale + offline sweeper in workers. | api: `/overview`, `/sources`, `/metrics/query`, WS fan-out from both regional NATS. Dashboard: Overview with live CPU chart, Host map v1, render acks. | **G1:** a simulator metric is visible live for acme with a measured latency sample. |
| **M2 Privacy + reliability (Phase 3 core)** | T+5 → T+9 | Full redaction rules, entropy, egress guard, EventRecord templates, egress hash chain, purge jobs. **Local UI:** Raw viewer, Egress Proof, Buffer status, leak-field toggle. Organiser real mode once the spec is known. | Outage behaviour verified, two lanes, Delivery Ledger + gap check, duplicate counters per layer. Residency 403, rogue cert, tenant spoof scripts. Isolation-test endpoint + pytest. EU region complete. Revocation list. | Thresholds per tenant + transitions with reasons (UTC). L1 baseline + persistence/hysteresis/warm-up + baseline persistence. **L0 security detectors and the fast-path threshold** as pure functions in `collector/stages/s5_analyse.py` (A integrates). | Fleet page. Infrastructure hex map + list + source drawer (state reason/history). Trust Center (Ledger, audit lookup, Isolation button). **SLO panel**. Privacy ribbon. | **G2 Phase-3 ready:** every Phase 3 bullet demonstrable. Rehearse the Phase 3 walk-through once. |
| **M3 AIOps + explainability (Phase 4 core)** | T+9 → T+13 | Action pull `/v1/actions` + local allow-list + executor + ActionResultRecord. Topology pseudonymisation + TopologyRecord. "Re-identify on site" page. | Ingest serves `/v1/actions` from regional DB. api remediation state machine + policy check + audit. System tests (`tests/`). Control-plane token+CSR enrolment (else keep pre-issued). | L2 Half-Space Trees. **L4 graph correlation → incidents + priority. L5 playbook → evidence (weights, confidence, trace, suppressed).** Noise counters. L3 MLP autoencoder if on time. | Incidents list + **Incident detail** (story timeline, Evidence panel, confidence breakdown, blast-radius mini map, remediation stepper). Events (log patterns) explorer with facets. Security signals. | **G3:** all 8 live scenarios pass once end to end. |
| **M4 Hardening** | T+13 → T+15.5 | `docs/` diagram, allow-list, limitations. Redaction corpus tests. | Soak test of 30 min: p95 numbers, memory, restarts. Backup `docker save`. | `score.py`, false-alarm tuning, scenario timing. | Metrics explorer, Service map page, polish, empty and error states. | Rehearsal #1 at T+14 produces the fix list. |
| **M5 Freeze & rehearse** | T+15.5 → T+17 | Feature freeze at T+15.5. Rehearsals #2 and #3 with role-play judges asking the §7 questions. Record a backup video (Win+Alt+R). Five slides at most: problem, architecture, trust proof, AIOps, limitations. | | | | |
| **Buffer** | T+17 → T+18 | Restart the stack cleanly at least 15 min before judging (warm-up). Re-check ports and laptop power. | | | | |

**Working rules**

- **Dependency order:** schema → (collector ∥ ingest ∥ simulator ∥ dashboard with mocks) → writer → api/WS → health → AIOps → incidents UI → remediation.
- **Parallelism tricks:**
  - D builds against MSW mocks generated from `schema/fixtures/ws_samples.jsonl`.
  - C unit-tests AIOps on `schema/fixtures/payload_stream.jsonl` without the stack.
  - A uses `scripts/mock_ingest.py` (a plain HTTPS stub) before B's ingest is ready.
- **Git:** trunk-based on `main`. Each owner commits only in their own directories. Shared files (`schema/`, `compose.yaml`, `dashboard/src/api/types.ts`, `config/*.yaml`) change only through their owner, with a team announcement. Pull and rebase every hour.
- **If fewer than 16 h remain at start:** apply cut items 1–6 from F.3 immediately.

---

## C. Repository scaffold (execute directly)

Repo root is `G:\Jeel\Desktop\blue_lock`. Run `git init -b main`, then `git remote add origin https://github.com/JeelVaghasiya/bluelock.git`. Do not push until the user confirms. Keep `others/` but gitignore it.

### C.1 Tree

```
blue_lock/
├── .gitattributes  .gitignore  .editorconfig  .env.example  README.md  Makefile  compose.yaml
├── docs/  architecture.md  allow-list.md  demo-script.md  qa-cheatsheet.md  limitations.md
│   └── adr/ 0001-5s-windows.md 0002-fast-path-alerts.md 0003-event-records.md 0004-topology-record.md 0005-remediation-pull.md 0006-no-caggs.md
├── schema/
│   ├── pyproject.toml                         # name=trustline-schema, deps: pydantic==2.14.4, uuid6==2025.0.1
│   ├── trustline_schema/__init__.py           # re-exports v1 models, SCHEMA
│   ├── trustline_schema/v1.py                 # payload + record models (C.3)
│   ├── trustline_schema/ingest.py             # IngestAck, IngestError
│   ├── trustline_schema/ws.py                 # WsEnvelope, Timing, client messages
│   ├── trustline_schema/ids.py                # new_payload_id() (uuid6.uuid7), code(prefix,key,value)
│   ├── fixtures/ payload_valid.json payload_extra_field.json payload_raw_hostname.json payload_tenant_spoof.json
│   │             payload_non_utc.json payload_stream.jsonl ws_samples.jsonl incident_evidence.json
│   └── tests/test_v1.py
├── config/
│   ├── tenants.yaml        # acme→ap-south, globex→eu-west; collectors; expected config hash
│   ├── users.yaml          # admin@acme (tenant-admin), viewer@acme (tenant-viewer), admin@globex, ops@trustline (platform-ops); scrypt hashes
│   ├── thresholds.yaml     # 4-state thresholds + stale/offline timers + per-tenant overrides
│   ├── playbook.yaml       # rule → action → why → policy → rollback
│   ├── collector.yaml      # window_s, batch_ms, retention, redaction toggles, actions_allowlist
│   ├── organiser_mapping.yaml
│   └── sites/acme/topology.yaml  sites/globex/topology.yaml   # REAL names - Tier 1 only (mounted into collectors only)
├── simulator/  Dockerfile  requirements.txt
│   └── simulator/ __init__.py main.py sites.py signals.py logs.py faults.py emit.py ground_truth.py
├── collector/  Dockerfile  requirements.txt
│   ├── collector/ __init__.py main.py settings.py pipeline.py enrol.py actions.py localui.py heartbeat.py
│   ├── collector/sources/ http_push.py syslog_udp.py organiser.py probes.py file_tail.py
│   ├── collector/stages/ s1_collect.py s2_filter.py s3_redact.py s4_aggregate.py s5_analyse.py s6_policy.py s7_transmit.py
│   ├── collector/redact/ rules.py entropy.py pseudonym.py ner.py template.py egress_guard.py
│   ├── collector/stores/ sqlite.py raw.py egress.py outbox.py state.py purge.py
│   └── tests/ corpus/secrets.txt corpus/benign.txt test_redaction.py test_policy.py test_outbox.py test_aggregate.py test_egress_chain.py test_security_l0.py
├── saas/
│   ├── common/ pyproject.toml  tl_common/ __init__.py db.py natsio.py settings.py registry.py timing.py
│   ├── ingest/  Dockerfile requirements.txt  ingest/ main.py identity.py audit.py actions.py
│   ├── workers/ Dockerfile requirements.txt  workers/ main.py writer.py ledger.py health.py sweeper.py live.py
│   │            workers/aiops/ engine.py baseline.py hst.py autoencoder.py correlate.py explain.py priority.py noise.py
│   ├── control/ Dockerfile requirements.txt  control/ main.py ca.py enrol.py registry.py revocation.py
│   ├── api/     Dockerfile requirements.txt  api/ main.py auth.py deps.py ws.py live.py remediation.py
│   │            api/routes/ overview.py sources.py metrics.py events.py security.py incidents.py remediations.py fleet.py trust.py slo.py thresholds.py topology.py audit.py
│   └── agent/README.md   # stretch, not built
├── dashboard/  package.json  vite.config.ts  tsconfig.json  tsconfig.app.json  index.html  local.html  components.json  Dockerfile  nginx.conf
│   └── src/ main.tsx local-main.tsx styles/{tokens.css,index.css} lib/{api.ts,ws.ts,time.ts,format.ts,cn.ts}
│           store/{ui.ts,live.ts} api/types.ts mocks/{browser.ts,handlers.ts}
│           components/ui/* (shadcn)  components/{AppShell,SideNav,TopBar,TimePicker,TenantBadge,PrivacyRibbon,HealthBadge,
│             PseudonymChip,ProvenanceChip,FacetPanel,QueryBar,EChart,HexMap,ServiceMap,SidePanel,EvidencePanel,ConfidenceBreakdown,
│             StoryTimeline,ReasoningTrace,RemediationStepper,LatencyWaterfall,SloCard,JsonView,RedactionDiff,KpiTile}.tsx
│           pages/{Login,Overview,Infrastructure,Metrics,Network,ServiceMapPage,Events,Security,Monitors,Incidents,IncidentDetail,
│             Fleet,TrustCenter,Slo,Audit,Settings}.tsx
│           local/pages/{EgressProof,RawViewer,Buffer,Redactions,Reidentify}.tsx
├── deploy/
│   ├── nginx/templates/ingest.conf.template
│   ├── nats/nats.conf
│   ├── db/init/ 10-roles.sh 20-schema.sql 30-rls.sql
│   ├── certs/gen.sh          # out/ is gitignored
│   └── wslconfig.example
├── scripts/  tl.py  score.py  mock_ingest.py
│   └── demo/ wrong_region.py tenant_spoof.py rogue_cert.sh tls12_fail.sh isolation.py outage.py
└── tests/  Dockerfile requirements.txt conftest.py test_mtls.py test_residency.py test_isolation.py test_duplicates.py test_outage.py test_redaction_e2e.py test_latency.py
```

### C.2 Root files

**.gitattributes**. CRLF breaks `.sh` scripts in Linux containers, so line endings are forced:
```
* text=auto eol=lf
*.ps1 text eol=crlf
*.pdf binary
*.png binary
*.jpg binary
*.jpeg binary
*.whl binary
```

**.gitignore**
```
others/
.env
deploy/certs/out/
**/__pycache__/
.venv/
*.db
*.db-wal
*.db-shm
node_modules/
dashboard/dist/
dashboard/dist-local/
.pytest_cache/
*.log
```

**.env.example**. This file is used only for compose interpolation. Never use `env_file:` in compose (G19).
```
COMPOSE_PROJECT_NAME=trustline
COMPOSE_PROFILES=eu            # empty = lite (ap-south only)
PG_OWNER_PASSWORD=change-me
PG_INGEST_PASSWORD=change-me
PG_WORKER_PASSWORD=change-me
PG_API_PASSWORD=change-me
NATS_INGEST_PASSWORD=change-me
NATS_WORKER_PASSWORD=change-me
NATS_API_PASSWORD=change-me
JWT_SECRET=change-me-at-least-32-chars
DEMO_PASSWORD=trustline-demo
ENROL_MODE=preissued           # preissued | token
ENROL_TOKEN_ACME=change-me
ENROL_TOKEN_GLOBEX=change-me
HMAC_KEY_ACME=change-me-64-hex     # Tier 1 only: passed ONLY to collector-acme
HMAC_KEY_GLOBEX=change-me-64-hex
ORGANISER_MODE=off             # off|http_push|http_poll|sse|ws|udp|file
ORGANISER_URL=
ORGANISER_TOKEN=
WINDOW_SECONDS=5
BATCH_INTERVAL_MS=1000
HEARTBEAT_SECONDS=5
OFFLINE_TIMEOUT_SECONDS=45
STALE_AFTER_SECONDS=30
WARMUP_SECONDS=300
WITH_NER=0
```
`python scripts/tl.py init` copies this file to `.env` and replaces every `change-me` with `secrets.token_hex(24)`. The HMAC keys get `token_hex(32)`.

**Makefile.** A thin wrapper for Linux/mac users; on Windows use `python scripts/tl.py …`.
```
up: ; python scripts/tl.py up
down: ; python scripts/tl.py down
certs: ; python scripts/tl.py certs
test: ; python scripts/tl.py test
```

**scripts/tl.py.** Stdlib only (`argparse`, `subprocess`, `secrets`, `urllib`, `shutil`, `socket`). Subcommands:
- `doctor`: checks docker, compose v2, that ports 3000/8000/8090/8443/8543/9090/9091 are free, that RAM is ≥5 GB free for WSL, and that `.env` and certs exist.
- `init`: creates `.env` with secrets, then runs `certs`.
- `certs [--local] [--force]`: by default runs `docker compose --profile tools run --rm certgen`. `--local` runs `sh deploy/certs/gen.sh` via Git Bash with `MSYS_NO_PATHCONV=1`.
- `up [--lite] [--build]`, `down [-v]`, `ps`, `logs <svc>`.
- `test unit|system`: unit uses `uv run pytest schema collector saas`; system uses `docker compose --profile tools run --rm tester`.
- `demo <secret|spray|brute|latency|memleak|cpu|outage|dupes|ooo|leak-field|wrong-region|rogue|spoof|isolation|remediate|clear>`: POSTs to simulator `http://127.0.0.1:8090/inject` or runs `scripts/demo/*`.
- `outage --svc ingest-ap --seconds 60`.
- `score`.
- `prewarm --minutes 30`.

### C.3 `schema/trustline_schema/v1.py` (frozen at G0)

Field names must match exactly; implement as written.

```python
from datetime import timedelta
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

SCHEMA = "trustline.v1"

def _utc(v):
    if v.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return v

def _v7(u: UUID):
    if u.version != 7:
        raise ValueError("payload_id must be UUIDv7")
    return u

UtcTs = Annotated[AwareDatetime, AfterValidator(_utc)]
UUID7 = Annotated[UUID, AfterValidator(_v7)]
Hex12 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]
SrcId = Annotated[str, StringConstraints(pattern=r"^(h|ip|u|svc|link):[0-9a-f]{12}$")]  # HMAC code only
TzOffset = Annotated[str, StringConstraints(pattern=r"^[+-]\d{2}:\d{2}$")]
CollectorId = Annotated[str, StringConstraints(pattern=r"^col-[0-9a-f]{4,12}$")]
RuleName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{2,32}$")]
SafeText = Annotated[str, StringConstraints(max_length=160,
            pattern=r"^[A-Za-z0-9 _.,:;=/()\[\]<>'\"#@+*%!?|-]*$")]
NonNeg = Annotated[int, Field(ge=0)]
Region = Literal["ap-south", "eu-west"]
MetricName = Literal["cpu","mem","disk","disk_hours_to_full","net_in","net_out","net_util",
                     "rtt","jitter","loss","up","error_rate"]
Unit = Literal["%","ms","B/s","ratio","h"]
Severity = Literal["info","low","medium","high","critical"]

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

class MetricRecord(_Strict):
    type: Literal["metric"]
    event_ts: UtcTs            # window start (= oldest possible sample)
    collect_ts: UtcTs          # window emitted at collector
    win_s: Literal[1, 5, 60]
    rev: Annotated[int, Field(ge=0, le=50)] = 0
    n: Annotated[int, Field(ge=1)]
    src_tz: TzOffset
    src: SrcId
    metric: MetricName
    min: float
    avg: float
    p95: float
    max: float
    p50: float | None = None
    unit: Unit

class FindingRecord(_Strict):
    type: Literal["finding"]
    finding_id: UUID
    event_ts: UtcTs
    breach_ts: UtcTs           # first offending raw sample -> alert-latency origin
    collect_ts: UtcTs
    src_tz: TzOffset
    src: SrcId
    category: Literal["auth","threshold","anomaly","connectivity"]
    pattern: Literal["brute_force","spray","success_after_failures","threshold_breach","edge_anomaly","link_down"]
    severity: Severity
    risk: Annotated[float, Field(ge=0, le=1)]
    layer: Literal["L0","T"]
    metric: MetricName | None = None
    value: float | None = None
    threshold: float | None = None
    baseline: float | None = None
    z: float | None = None
    fail_count: NonNeg | None = None
    distinct_users: NonNeg | None = None
    window_s: Annotated[int, Field(ge=1, le=3600)] | None = None

class EventRecord(_Strict):
    type: Literal["event"]
    event_ts: UtcTs
    collect_ts: UtcTs
    win_s: Literal[5, 60]
    src_tz: TzOffset
    src: SrcId
    category: Literal["auth","system","network","app","security"]
    level: Literal["debug","info","warn","error","critical"]
    template_id: Hex12
    template: SafeText         # e.g. "Failed password for <u:4be1c02a9f10> from <ip:3fa1c09e7d22> port <num>"
    count: Annotated[int, Field(ge=1)]
    redacted: NonNeg

class HeartbeatRecord(_Strict):
    type: Literal["heartbeat"]
    event_ts: UtcTs
    src_tz: TzOffset
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    config_hash: Hex12
    cpu: Annotated[float, Field(ge=0)]
    mem_mb: Annotated[float, Field(ge=0)]
    buffer_depth: NonNeg
    buffer_bytes: NonNeg
    last_acked_seq: NonNeg
    last_assigned_seq: NonNeg
    redactions_total: NonNeg
    blocked_total: NonNeg
    dup_source_events: NonNeg
    late_beyond_horizon: NonNeg
    uptime_s: NonNeg
    saas_reachable: bool

class TopoNode(_Strict):
    src: SrcId
    kind: Literal["host","svc","link"]
    role: Literal["web","api","db","gateway","uplink","worker","other"]

class TopoEdge(_Strict):
    dependent: SrcId
    dependency: SrcId
    kind: Literal["depends_on","runs_on","routes_via"]

class TopologyRecord(_Strict):
    type: Literal["topology"]
    event_ts: UtcTs
    nodes: Annotated[list[TopoNode], Field(max_length=500)]
    edges: Annotated[list[TopoEdge], Field(max_length=2000)]

class ActionResultRecord(_Strict):
    type: Literal["action_result"]
    event_ts: UtcTs
    action_id: UUID
    status: Literal["executed","failed","rolled_back","refused_local_policy"]
    code: RuleName

Record = Annotated[Union[MetricRecord, FindingRecord, EventRecord, HeartbeatRecord,
                         TopologyRecord, ActionResultRecord], Field(discriminator="type")]

class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, populate_by_name=True)
    schema_: Literal["trustline.v1"] = Field(alias="schema")   # 'schema' shadows BaseModel attr -> alias
    payload_id: UUID7
    collector_id: CollectorId
    seq: Annotated[int, Field(ge=1)]
    region: Region
    sent_ts: UtcTs             # sealed time; immutable across retries
    redactions: NonNeg
    redactions_by_rule: dict[RuleName, NonNeg] = {}
    records: Annotated[list[Record], Field(min_length=1, max_length=500)]
# Serialize ALWAYS with model_dump_json(by_alias=True). tenant_id is NOT a field -> a body claiming a tenant is rejected (422).
```

**`ingest.py`**
- `IngestAck {payload_id: UUID, status: Literal["stored","duplicate"], seq: int, recv_ts: UtcTs}`.
- Errors return `{error: Literal["bad_encoding","schema_rejected","residency_denied","identity_mismatch","revoked","too_large","queue_unavailable"], detail: str}`.
- Status codes: 400, 422, 403, 413, 503.
- The collector retries on 5xx, 408, 429 and timeouts. Any other 4xx marks the payload `rejected`.

**Wire format.** `POST /v1/ingest` with `Content-Type: application/vnd.trustline.v1+json` and `Content-Encoding: gzip`. The body is gzip with `mtime=0`, stored once in the outbox and resent byte-identical. `sha256` is computed over the gzip bytes. Headers: `X-TL-Attempt`, `X-TL-Attempt-Ts`, `X-TL-Lane: live|backlog`.

**`ws.py`**. Server→client envelope:
```json
{"v":1,"type":"metric|event|finding|incident|health|collector|delivery|remediation|latency|hello|pong",
 "tenant":"acme","region":"ap-south","id":"<record or entity id>",
 "timing":{"event_ts":"…","collect_ts":"…","sent_ts":"…","recv_ts":"…","stored_ts":"…","push_ts":"…","breach_ts":"…"},
 "data":{…}}
```
Client→server messages:
- `{"type":"subscribe","topics":["metric","incident",…]}`
- `{"type":"render_ack","items":[{"id":"…","kind":"dash|alert|offline","render_ts":"…"}]}`
- `{"type":"ping","t0":"…"}`; the server answers `pong` with `{t0, server_ts}` for browser clock-offset estimation.

**`ids.py`**
- `new_payload_id()` returns `uuid6.uuid7()`.
- `code(prefix, key: bytes, value: str)` returns `f"{prefix}:{hmac.new(key, value.lower().encode(), sha256).hexdigest()[:12]}"`.

**Fixtures** (generated by `schema/tests/make_fixtures.py`, committed at G0): the valid payload with metric, finding, event and heartbeat records; an extra field (`raw_msg`); a raw hostname in `src`; a tenant spoof (`tenant_id` in the body); a non-UTC timestamp; a 600-record stream for C; WS samples for D; incident evidence for D.

**`schema/tests/test_v1.py` must assert:**
- the valid fixture passes;
- extra field, raw `src`, `tenant_id`, non-UTC, NaN, UUIDv4 and over 500 records are each rejected;
- a round trip `model_dump_json(by_alias=True)` → parse gives identical results.

### C.4 `compose.yaml` (root)

```yaml
name: trustline
x-py: &py
  restart: unless-stopped
  init: true
x-tsdb: &tsdb
  image: timescale/timescaledb:2.32.1-pg16
  restart: unless-stopped
  mem_limit: 384m
  command: ["postgres","-c","shared_buffers=64MB","-c","max_connections=50","-c","work_mem=4MB","-c","timescaledb.telemetry_level=off"]
  healthcheck: {test: ["CMD-SHELL","pg_isready -U tl_owner -d trustline"], interval: 5s, timeout: 3s, retries: 30}

networks:
  site-acme:   {internal: true}
  site-globex: {internal: true}
  sim-ctl: {}
  wan: {}                       # "the internet": collectors, nginx, control
  ap-core: {internal: true}
  eu-core: {internal: true}
  global: {}

volumes: {tsdb-ap: {}, tsdb-eu: {}, nats-ap: {}, nats-eu: {}, col-acme: {}, col-globex: {}, control-data: {}}

services:
  simulator:
    <<: *py
    build: {context: ., dockerfile: simulator/Dockerfile}
    environment:
      SITES: "acme=http://collector-acme:8081,globex=http://collector-globex:8081"
      SYSLOG_TARGETS: "acme=collector-acme:5514,globex=collector-globex:5514"
      SIM_SEED: "42"
    networks: [site-acme, site-globex, sim-ctl]
    ports: ["127.0.0.1:8090:8090"]
    mem_limit: 192m

  collector-acme:
    <<: *py
    build: {context: ., dockerfile: collector/Dockerfile, args: {WITH_NER: "${WITH_NER:-0}"}}
    environment:
      SITE: acme
      COLLECTOR_ID: col-ac01
      REGION: ap-south
      SRC_TZ: "+05:30"
      INGEST_BASE: https://nginx-ap:8443
      CONTROL_URL: https://control:8444
      ENROL_MODE: ${ENROL_MODE}
      ENROL_TOKEN: ${ENROL_TOKEN_ACME}
      HMAC_KEY: ${HMAC_KEY_ACME}
      ORGANISER_MODE: ${ORGANISER_MODE}
      ORGANISER_URL: ${ORGANISER_URL}
      ORGANISER_TOKEN: ${ORGANISER_TOKEN}
      WINDOW_SECONDS: ${WINDOW_SECONDS}
      BATCH_INTERVAL_MS: ${BATCH_INTERVAL_MS}
      HEARTBEAT_SECONDS: ${HEARTBEAT_SECONDS}
    volumes:
      - col-acme:/data                                   # named volume: SQLite WAL is unsafe on Windows bind mounts
      - ./config:/config:ro
      - ./deploy/certs/out/ca.crt:/certs/ca.crt:ro
      - ./deploy/certs/out/collectors/col-ac01:/certs/client:ro
    networks: [site-acme, wan]
    ports: ["127.0.0.1:9090:9090"]                       # local UI: localhost only (H9)
    mem_limit: 384m

  collector-globex:
    # same as acme with: SITE=globex, COLLECTOR_ID=col-9b01, REGION=eu-west, SRC_TZ="+01:00",
    # INGEST_BASE=https://nginx-eu:8443, ENROL_TOKEN_GLOBEX, HMAC_KEY_GLOBEX,
    # volume col-globex, certs col-9b01, networks [site-globex, wan], port 127.0.0.1:9091:9090, profiles: [eu]
    profiles: [eu]

  nginx-ap:
    image: nginx:1.30.7-alpine
    restart: unless-stopped
    environment: {TL_REGION: ap-south, TL_SERVER_NAME: nginx-ap, TL_UPSTREAM: "ingest-ap:8000"}
    volumes:
      - ./deploy/nginx/templates:/etc/nginx/templates:ro
      - ./deploy/certs/out/servers/nginx-ap:/etc/nginx/certs/server:ro
      - ./deploy/certs/out/ca.crt:/etc/nginx/certs/ca.crt:ro
    networks: [wan, ap-core]
    ports: ["8443:8443"]
    mem_limit: 32m
  nginx-eu:
    # same with eu values; host port "8543:8443"; networks [wan, eu-core]
    profiles: [eu]

  ingest-ap:
    <<: *py
    build: {context: ., dockerfile: saas/ingest/Dockerfile}
    environment:
      REGION: ap-south
      NATS_URL: nats://ingest:${NATS_INGEST_PASSWORD}@nats-ap:4222
      DATABASE_URL: postgresql://tl_ingest:${PG_INGEST_PASSWORD}@tsdb-ap:5432/trustline
      CONTROL_INTERNAL: http://control:8000
    networks: [ap-core]
    depends_on: {tsdb-ap: {condition: service_healthy}, nats-ap: {condition: service_started}}
    mem_limit: 160m

  nats-ap:
    image: nats:2.15.3-alpine
    restart: unless-stopped
    command: ["-c", "/etc/nats/nats.conf"]
    environment:
      NATS_INGEST_PASSWORD: ${NATS_INGEST_PASSWORD}
      NATS_WORKER_PASSWORD: ${NATS_WORKER_PASSWORD}
      NATS_API_PASSWORD: ${NATS_API_PASSWORD}
    volumes: ["./deploy/nats/nats.conf:/etc/nats/nats.conf:ro", "nats-ap:/data"]
    networks: [ap-core]
    mem_limit: 128m

  tsdb-ap:
    <<: *tsdb
    environment:
      POSTGRES_DB: trustline
      POSTGRES_USER: tl_owner
      POSTGRES_PASSWORD: ${PG_OWNER_PASSWORD}
      PG_INGEST_PASSWORD: ${PG_INGEST_PASSWORD}
      PG_WORKER_PASSWORD: ${PG_WORKER_PASSWORD}
      PG_API_PASSWORD: ${PG_API_PASSWORD}
      TS_TUNE_MEMORY: 384MB
      TS_TUNE_NUM_CPUS: "2"
      TIMESCALEDB_TELEMETRY: "off"
    volumes:   # mount FILES, not the directory: the image ships its own 000_/001_ init scripts there
      - tsdb-ap:/var/lib/postgresql/data
      - ./deploy/db/init/10-roles.sh:/docker-entrypoint-initdb.d/10-roles.sh:ro
      - ./deploy/db/init/20-schema.sql:/docker-entrypoint-initdb.d/20-schema.sql:ro
      - ./deploy/db/init/30-rls.sql:/docker-entrypoint-initdb.d/30-rls.sql:ro
    networks: [ap-core]

  workers-ap:
    <<: *py
    build: {context: ., dockerfile: saas/workers/Dockerfile}
    environment:
      REGION: ap-south
      NATS_URL: nats://worker:${NATS_WORKER_PASSWORD}@nats-ap:4222
      DATABASE_URL: postgresql://tl_worker:${PG_WORKER_PASSWORD}@tsdb-ap:5432/trustline
      CONFIG_DIR: /config
      CONTROL_INTERNAL: http://control:8000
      STALE_AFTER_SECONDS: ${STALE_AFTER_SECONDS}
      OFFLINE_TIMEOUT_SECONDS: ${OFFLINE_TIMEOUT_SECONDS}
      WARMUP_SECONDS: ${WARMUP_SECONDS}
    volumes: ["./config/thresholds.yaml:/config/thresholds.yaml:ro", "./config/playbook.yaml:/config/playbook.yaml:ro"]
    networks: [ap-core]
    depends_on: {tsdb-ap: {condition: service_healthy}, ingest-ap: {condition: service_started}}
    mem_limit: 512m

  # ingest-eu, nats-eu, tsdb-eu, workers-eu: identical with eu-west / eu-core / *-eu names, all with profiles: [eu]

  control:
    <<: *py
    build: {context: ., dockerfile: saas/control/Dockerfile}
    environment:
      ENROL_TOKENS: "acme=${ENROL_TOKEN_ACME},globex=${ENROL_TOKEN_GLOBEX}"
      TENANTS_FILE: /config/tenants.yaml
    volumes:
      - ./deploy/certs/out/ca:/ca:ro                     # CA key lives ONLY here
      - ./deploy/certs/out/servers/control:/tls:ro
      - "./config/tenants.yaml:/config/tenants.yaml:ro"
      - control-data:/data
    networks: [wan, global, ap-core, eu-core]            # 8444 TLS (enrol, wan); 8000 http internal registry
    mem_limit: 160m

  api:
    <<: *py
    build: {context: ., dockerfile: saas/api/Dockerfile}
    environment:
      DB_AP_URL: postgresql://tl_api:${PG_API_PASSWORD}@tsdb-ap:5432/trustline
      DB_EU_URL: postgresql://tl_api:${PG_API_PASSWORD}@tsdb-eu:5432/trustline
      NATS_AP_URL: nats://api:${NATS_API_PASSWORD}@nats-ap:4222
      NATS_EU_URL: nats://api:${NATS_API_PASSWORD}@nats-eu:4222
      JWT_SECRET: ${JWT_SECRET}
      USERS_FILE: /config/users.yaml
      TENANTS_FILE: /config/tenants.yaml
    volumes: ["./config/users.yaml:/config/users.yaml:ro", "./config/tenants.yaml:/config/tenants.yaml:ro",
              "./config/thresholds.yaml:/config/thresholds.yaml:ro", "./config/playbook.yaml:/config/playbook.yaml:ro"]
    networks: [global, ap-core, eu-core]
    ports: ["127.0.0.1:8000:8000"]
    mem_limit: 256m

  dashboard:
    build: {context: ./dashboard}
    restart: unless-stopped
    networks: [global]
    ports: ["127.0.0.1:3000:80"]
    mem_limit: 32m

  certgen:
    profiles: [tools]
    image: alpine:3.22
    working_dir: /work
    entrypoint: ["sh", "/work/deploy/certs/gen.sh"]
    environment: {FORCE: "${FORCE:-0}"}
    volumes: ["./deploy/certs:/work/deploy/certs"]

  tester:
    profiles: [tools]
    build: {context: ., dockerfile: tests/Dockerfile}
    environment:
      DB_AP_ADMIN: postgresql://tl_owner:${PG_OWNER_PASSWORD}@tsdb-ap:5432/trustline
      DB_EU_ADMIN: postgresql://tl_owner:${PG_OWNER_PASSWORD}@tsdb-eu:5432/trustline
      DEMO_PASSWORD: ${DEMO_PASSWORD}
    volumes: ["./:/work:ro", "./deploy/certs/out:/certs:ro", "/var/run/docker.sock:/var/run/docker.sock"]
    networks: [wan, global, sim-ctl, ap-core, eu-core]
```

Notes on the compose file:
- The api uses `api.depends_on` with conditions on both tsdb services when the eu profile is active. In lite mode, the api must tolerate an unreachable `DB_EU_URL` (lazy pool).
- nginx uses Docker's DNS resolver (`resolver 127.0.0.11`) with variable `proxy_pass`. nginx then keeps running while `ingest-ap` is stopped (it returns 502, the collector retries), and nginx can start before ingest exists.
- The site networks are `internal: true`, so the raw data path physically cannot leave. Collectors reach SaaS only through `wan` → nginx.
- Memory limits total about 3.8 GB; real usage is about 2–2.5 GB.

### C.5 `deploy/nginx/templates/ingest.conf.template`

The nginx image runs envsubst only for env vars that exist, so `$ssl_*` nginx variables survive.

```nginx
log_format tl '$time_iso8601 "$ssl_client_s_dn" $ssl_client_verify $ssl_protocol $status $request_time $request_length';
server {
  listen 8443 ssl;
  http2 on;
  server_name ${TL_SERVER_NAME} localhost;
  access_log /dev/stdout tl;
  server_tokens off;
  ssl_certificate     /etc/nginx/certs/server/server.crt;
  ssl_certificate_key /etc/nginx/certs/server/server.key;
  ssl_protocols TLSv1.3;
  ssl_client_certificate /etc/nginx/certs/ca.crt;
  ssl_verify_client on;
  ssl_verify_depth 2;
  client_max_body_size 5m;
  resolver 127.0.0.11 valid=10s ipv6=off;
  set $ingest http://${TL_UPSTREAM};

  location ~ ^/v1/(ingest|actions|config)$ {
    proxy_pass $ingest;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header X-Client-DN $ssl_client_s_dn;          # overwrites any client-sent header
    proxy_set_header X-Client-Verify $ssl_client_verify;
    proxy_set_header X-Client-Serial $ssl_client_serial;
    proxy_set_header X-Client-Fingerprint $ssl_client_fingerprint;
    proxy_set_header X-TL-Region ${TL_REGION};
    proxy_connect_timeout 2s;
    proxy_read_timeout 10s;
  }
  location / { return 404; }
}
```
nginx prints the DN in RFC 2253 order (`O=tenant-acme,CN=col-ac01`). Parse it order-independently: `CN=(col-[0-9a-f]+)` and `O=tenant-([a-z0-9-]+)`.

### C.6 `deploy/certs/gen.sh` (POSIX sh, runs in the alpine `certgen` container or Git Bash)

```sh
#!/bin/sh
set -eu
export MSYS_NO_PATHCONV=1            # Git Bash: stop "/CN=..." being rewritten to a Windows path
cd "$(dirname "$0")"
OUT=out
DAYS=30
if [ -f "$OUT/ca/ca.crt" ] && [ "${FORCE:-0}" != "1" ]; then echo "certs exist (FORCE=1 to regenerate)"; exit 0; fi
command -v openssl >/dev/null 2>&1 || apk add --no-cache openssl >/dev/null
rm -rf "$OUT"
mkdir -p "$OUT/ca" "$OUT/servers" "$OUT/collectors" "$OUT/rogue"
EC="-newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes"

openssl req -x509 $EC -keyout "$OUT/ca/ca.key" -out "$OUT/ca/ca.crt" -days $DAYS \
  -subj "/CN=TrustLine Demo Root CA/O=trustline" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign"
cp "$OUT/ca/ca.crt" "$OUT/ca.crt"

issue() {  # $1=dir $2=subject $3=eku $4=san(optional) $5=ca-dir
  d=$1; mkdir -p "$d"; cadir=${5:-$OUT/ca}
  openssl req $EC -keyout "$d/$(basename "$d" | sed 's/.*/key/')".tmp -out "$d/req.csr" -subj "$2" 2>/dev/null
  mv "$d/key.tmp" "$d/$3.key"
  { echo "basicConstraints=CA:FALSE"; echo "keyUsage=critical,digitalSignature"; echo "extendedKeyUsage=$3"; [ -n "${4:-}" ] && echo "subjectAltName=$4"; } > "$d/ext.cnf"
  openssl x509 -req -in "$d/req.csr" -CA "$cadir/ca.crt" -CAkey "$cadir/ca.key" -CAcreateserial \
    -days $DAYS -sha256 -extfile "$d/ext.cnf" -out "$d/$3.crt"
  rm -f "$d/req.csr" "$d/ext.cnf"
}
# Servers -> server.crt/server.key ; clients -> client.crt/client.key
for s in nginx-ap nginx-eu control; do
  issue "$OUT/servers/$s" "/CN=$s/O=trustline" serverAuth "DNS:$s,DNS:localhost,IP:127.0.0.1"
  mv "$OUT/servers/$s/serverAuth.crt" "$OUT/servers/$s/server.crt"
  mv "$OUT/servers/$s/serverAuth.key" "$OUT/servers/$s/server.key"
done
for c in "col-ac01:acme" "col-9b01:globex"; do
  id=${c%%:*}; t=${c##*:}
  issue "$OUT/collectors/$id" "/CN=$id/O=tenant-$t" clientAuth ""
  mv "$OUT/collectors/$id/clientAuth.crt" "$OUT/collectors/$id/client.crt"
  mv "$OUT/collectors/$id/clientAuth.key" "$OUT/collectors/$id/client.key"
done
# Rogue CA + rogue collector claiming tenant-acme (must FAIL the handshake)
openssl req -x509 $EC -keyout "$OUT/rogue/ca.key" -out "$OUT/rogue/ca.crt" -days $DAYS -subj "/CN=Rogue CA/O=evil" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign"
issue "$OUT/rogue/client" "/CN=col-ac01/O=tenant-acme" clientAuth "" "$OUT/rogue"
chmod -R a+r "$OUT"
echo "PKI written to deploy/certs/out"
```
The `issue()` key-naming line is convoluted. When implementing, simplify it to `-keyout "$d/$3.key"`. Intent: server dirs end with `server.crt`/`server.key`, client dirs with `client.crt`/`client.key`, plus `out/ca.crt` and `out/ca/{ca.crt,ca.key}`.

### C.7 `deploy/nats/nats.conf`

```
port: 4222
http_port: 8222
jetstream { store_dir: "/data", max_memory_store: 64MB, max_file_store: 1GB }
authorization {
  users = [
    { user: ingest, password: $NATS_INGEST_PASSWORD,
      permissions: { publish: ["ingest.>", "$JS.API.>"], subscribe: ["_INBOX.>"] } }
    { user: worker, password: $NATS_WORKER_PASSWORD,
      permissions: { publish: ["$JS.API.>", "$JS.ACK.>", "live.>"], subscribe: ["_INBOX.>"] } }
    { user: api,    password: $NATS_API_PASSWORD,
      permissions: { publish: ["_INBOX.>"], subscribe: ["live.>", "_INBOX.>"] } }
  ]
}
```

On startup, ingest runs an idempotent stream setup: `js.add_stream(name="INGEST", subjects=["ingest.>"], storage=file, duplicate_window=120, max_age=86400)` (update it if it exists).

Workers create two durable pull consumers:
- `writer`, filter `ingest.<region>.>`, `ack_wait=30s`, `max_ack_pending=1000`;
- `aiops`, same filter.

Live push goes over core NATS on `live.<tenant>.<kind>`.

### C.8 DB init

**`10-roles.sh`**
```sh
#!/bin/sh
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<EOSQL
CREATE ROLE tl_ingest LOGIN PASSWORD '${PG_INGEST_PASSWORD}' NOBYPASSRLS;
CREATE ROLE tl_worker LOGIN PASSWORD '${PG_WORKER_PASSWORD}' NOBYPASSRLS;
CREATE ROLE tl_api    LOGIN PASSWORD '${PG_API_PASSWORD}'    NOBYPASSRLS;
EOSQL
```

**`20-schema.sql`.** Run `CREATE EXTENSION IF NOT EXISTS timescaledb;` first. Every table has `tenant_id text NOT NULL`. Columns are abbreviated below.

- `metrics(tenant_id, collector_id, src, metric, event_ts timestamptz, win_s smallint, rev smallint, n int, min, avg, p50, p95, max double precision, unit, src_tz, collect_ts, recv_ts, stored_ts default now(), payload_id uuid, PRIMARY KEY (tenant_id, src, metric, event_ts))`, then `SELECT create_hypertable('metrics', by_range('event_ts', INTERVAL '1 hour'));`
- `findings(…, finding_id uuid, …, breach_ts, PRIMARY KEY (tenant_id, finding_id, event_ts))` (hypertable)
- `events(…, template_id, template, count, redacted, level, category, PRIMARY KEY (tenant_id, src, template_id, event_ts))` (hypertable)
- `heartbeats(…, PRIMARY KEY (tenant_id, collector_id, event_ts))` (hypertable)
- `collectors(tenant_id, collector_id, region, first_seen, last_seen, last_hb_event_ts, version, config_hash, expected_config_hash, cert_serial, state, state_reason, skew_ms, buffer_depth, last_acked_seq, last_assigned_seq, PRIMARY KEY (tenant_id, collector_id))`
- `topology(tenant_id, nodes jsonb, edges jsonb, updated_ts, PRIMARY KEY (tenant_id))`
- `health_states(tenant_id, src, kind, role, state CHECK (state IN ('excellent','good','fair','poor')), stale bool, reason, signals jsonb, since, updated_ts, PRIMARY KEY (tenant_id, src))`
- `health_transitions(id bigserial PK, tenant_id, src, old_state, new_state, reason, at)`
- `alerts(tenant_id, alert_id uuid PK, src, metric, layer, z, score, value, baseline, onset_ts, last_ts, incident_id, suppressed_reason)`: the raw alerts, for noise accounting
- `incidents(tenant_id, incident_id text, status, priority, severity, title, root_cause_src, affected jsonb, evidence jsonb, alert_count, breach_ts, opened_ts, alert_ts, updated_ts, resolved_ts, PRIMARY KEY (tenant_id, incident_id))`
- `remediations(tenant_id, action_id uuid PK, incident_id, action_key, params jsonb, policy jsonb, state, collector_id, proposed_ts, approved_by, approved_ts, dispatched_ts, executed_ts, verified_ts, rollback_of uuid, result_code)`
- `received_payloads(payload_id uuid PRIMARY KEY, tenant_id, collector_id, seq bigint, sha256, records int, received_at, stored_at, UNIQUE (tenant_id, collector_id, seq))`
- `ingest_audit(id bigserial PK, tenant_id, collector_id, payload_id, seq, sha256, region, result, detail, attempt int, lane, attempt_ts, received_at, skew_ms)`
- `delivery_counters(tenant_id, collector_id, js_duplicates bigint, db_conflicts bigint, PRIMARY KEY (tenant_id, collector_id))`
- `latency_samples(tenant_id, kind, ref_id, lane, event_ts, collect_ts, sent_ts, recv_ts, stored_ts, push_ts, render_ts, total_ms double precision, at timestamptz default now())` (hypertable on `at`)
- `thresholds_override(tenant_id PK, doc jsonb, updated_by, updated_ts)`
- `baselines(tenant_id, key, state jsonb, updated_ts, PRIMARY KEY (tenant_id, key))`
- `audit_log(id bigserial PK, tenant_id, actor, role, action, target, detail jsonb, at)`

**`30-rls.sql`**
```sql
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
GRANT SELECT, INSERT, UPDATE ON ingest_audit, collectors, received_payloads TO tl_ingest;
GRANT SELECT ON remediations, thresholds_override TO tl_ingest;          -- /v1/actions, /v1/config
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO tl_worker;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tl_api;
GRANT INSERT, UPDATE ON incidents, remediations, thresholds_override, audit_log, latency_samples TO tl_api;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO tl_ingest, tl_worker, tl_api;
CREATE FUNCTION fleet_overview() RETURNS SETOF collectors LANGUAGE sql SECURITY DEFINER SET search_path = public
  AS $$ SELECT * FROM collectors $$;           -- owned by superuser tl_owner -> bypasses RLS (platform-ops only)
REVOKE ALL ON FUNCTION fleet_overview() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fleet_overview() TO tl_api;
```

**DB access rule** (in `tl_common/db.py`): `async with tenant_tx(pool, tenant) as conn:` opens a transaction and runs `SELECT set_config('app.tenant_id', $1, true)` (transaction-local). There is no other way to query.

**M0 smoke test for B:** RLS on a hypertable returns 0 rows for another tenant. If it does not, drop `create_hypertable` and use plain tables; the SQL is otherwise unchanged and `time_bucket` becomes `date_bin`.

### C.9 Python requirements (pinned; containers are `python:3.12-slim`)

| Service | requirements.txt |
|---|---|
| schema | `pydantic==2.14.4` `uuid6==2025.0.1` |
| collector | `fastapi==0.165.0` `uvicorn[standard]==0.54.0` `httpx==0.28.1` `orjson==3.13.0` `aiosqlite==0.22.1` `numpy==2.5.5` `scikit-learn==1.9.1` `psutil==7.2.2` `icmplib==3.0.4` `watchfiles==1.3.0` `cryptography==50.0.2` `PyYAML==6.0.3` `structlog==26.1.0`. If `WITH_NER=1`: `presidio-analyzer==2.2.366` `spacy==3.8.18` + `https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl` (URL verified). |
| ingest | `fastapi==0.165.0` `uvicorn[standard]==0.54.0` `nats-py==2.17.0` `asyncpg==0.32.0` `orjson==3.13.0` `PyYAML==6.0.3` `httpx==0.28.1` |
| workers | `nats-py==2.17.0` `asyncpg==0.32.0` `numpy==2.5.5` `scikit-learn==1.9.1` `river==0.27.0` `networkx==3.7` `PyYAML==6.0.3` `orjson==3.13.0` |
| control | `fastapi==0.165.0` `uvicorn[standard]==0.54.0` `cryptography==50.0.2` `PyYAML==6.0.3` |
| api | `fastapi==0.165.0` `uvicorn[standard]==0.54.0` `asyncpg==0.32.0` `nats-py==2.17.0` `PyJWT==2.15.1` `PyYAML==6.0.3` `orjson==3.13.0` |
| simulator | `fastapi==0.165.0` `uvicorn[standard]==0.54.0` `numpy==2.5.5` `httpx==0.28.1` `orjson==3.13.0` |
| tests | `pytest==9.2.0` `pytest-asyncio==1.5.0` `httpx==0.28.1` `asyncpg==0.32.0` `nats-py==2.17.0` `docker==7.*` |

**Dockerfile pattern** (build context is the repo root, so `schema/` and `saas/common/` are reachable):
```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY schema /src/schema
RUN pip install /src/schema
COPY saas/common /src/common
RUN pip install /src/common      # SaaS services only
COPY saas/ingest/requirements.txt .
RUN pip install -r requirements.txt
COPY saas/ingest/ingest ./ingest
CMD ["uvicorn","ingest.main:app","--host","0.0.0.0","--port","8000","--no-access-log"]
```

The collector Dockerfile is multi-stage:
1. A first stage `FROM node:24-alpine AS localui` runs `npm ci && npm run build:local` in `dashboard/`.
2. The final stage copies `dist-local` to `/app/static`.
3. `CMD ["python","-m","collector.main"]`.

**Local dev:** `uv python install 3.12`, then `uv venv --python 3.12`, then `uv pip install -e schema -r collector/requirements.txt …`. System Python 3.14 may lack wheels for river and spaCy.

### C.10 Dashboard `package.json` (pinned)

These are stable majors chosen deliberately over the newest majors (TS 7, Router 8, Table 9, xyflow 13, Vite 8), which have fewer examples. In a 24 h build, documented APIs beat novelty.

- **Dependencies:** `react 19.3.1`, `react-dom 19.3.1`, `react-router 7.18.6`, `@tanstack/react-query 5.108.2`, `@tanstack/react-table 8.21.3`, `echarts 5.6.0`, `@xyflow/react 12.12.1`, `@dagrejs/dagre 1.1.8`, `zustand 5.1.0`, `lucide-react 0.577.0`, `cmdk 1.1.1`, `sonner 2.0.8`, `date-fns 4.4.0`, `@date-fns/tz 1.5.0`, `radix-ui 1.8.1`, `class-variance-authority 0.7.1`, `tailwind-merge 3.7.1`, `clsx 2.1.1`, `@fontsource-variable/inter 5.3.0`, `@fontsource/jetbrains-mono 5.3.0`.
- **devDependencies:** `vite 7.3.8`, `@vitejs/plugin-react 5.2.0`, `typescript 5.9.3`, `tailwindcss 4.3.5`, `@tailwindcss/vite 4.3.5`, `@types/react 19.3.0`, `@types/react-dom 19.3.0`, `@types/node 24.19.2`, `vitest 3.2.7`, `msw 2.15.0`.
- **Scripts:** `dev`, `build` (= `tsc -b && vite build`), `build:local` (= `vite build --mode local`), `typecheck`, `test`, `mock` (dev with `VITE_MOCK=1`).
- **`vite.config.ts`:** plugins `react()` and `tailwindcss()`; alias `@` → `src`.
  - Dev proxy: `/api` → `http://localhost:8000`, `/ws` → `ws://localhost:8000` (`ws: true`), `/local` → `http://localhost:9090`.
  - Build: `mode === 'local'` gives input `local.html` and outDir `dist-local`; otherwise `index.html` and `dist`.
- **`dashboard/Dockerfile`:** `node:24-alpine` runs `npm ci && npm run build`, then `nginx:1.30.7-alpine` serves `dist`. `nginx.conf` proxies `/api/` and `/ws` (with upgrade headers) to `api:8000` and falls back to `index.html` for SPA routes.
- **shadcn:** run `npx shadcn@latest init` (Tailwind v4, "new-york", CSS variables). Then `npx shadcn@latest add button badge card dialog drawer dropdown-menu input select tabs tooltip table popover command separator scroll-area switch sheet skeleton toggle-group`.

### C.11 Config file shapes

**`thresholds.yaml`**
```yaml
version: 1
stale_after_s: 30
offline_after_s: 45
signals:
  cpu:  {stat: p95, excellent: 60, good: 80, fair: 90, unit: "%"}
  mem:  {stat: avg, excellent: 70, good: 80, fair: 90, unit: "%"}
  disk: {stat: max, excellent: 70, good: 80, fair: 90, poor_if_hours_to_full_lt: 24}
  rtt:  {stat: p95, excellent: 50, good: 100, fair: 200, unit: ms}
  loss: {stat: avg, excellent: 0.5, good: 1, fair: 3, unit: "%"}
  up:   {stat: min, lower_is_worse: true, excellent: 1.0, good: 0.99, fair: 0.95}
  error_rate: {stat: avg, excellent: 0.01, good: 0.02, fair: 0.05}
overrides: {globex: {cpu: {excellent: 50, good: 70, fair: 85}}}
```

**`playbook.yaml`**
```yaml
rules:
  - id: upstream_link_degradation
    when: {root_kind: link, root_metrics_any: [rtt, loss], impacted_metrics_any: [error_rate, up, rtt]}
    action: {key: failover_uplink, text: "Check link {root} or fail over to the secondary uplink",
             reversible: true, rollback_key: failback_uplink, requires_role: tenant-admin, max_blast_radius: 6}
    why: "Link {root} {metric} rose {lead_s}s before {impacted_n} dependent component(s) degraded"
  - id: memory_leak
    when: {root_kind: host, root_metrics_any: [mem], trend: rising}
    action: {key: restart_service, text: "Restart the leaking service on {root} during low traffic",
             reversible: true, rollback_key: none, requires_role: tenant-admin}
    why: "..."
  - id: cpu_saturation
    # …
  - id: auth_attack
    # …
  - id: disk_fill
    # …
  - id: generic_anomaly
    # fallback, action "Investigate", no execution
```

**`config/sites/acme/topology.yaml`** (Tier 1, real names):
```yaml
nodes: [{name: web-01, kind: host, role: web}, {name: api, kind: svc, role: api}, {name: db, kind: svc, role: db}, {name: uplink-1, kind: link, role: uplink}, …]
edges: [{dependent: web, dependency: api, kind: depends_on}, {dependent: api, dependency: db, kind: depends_on}, {dependent: api, dependency: uplink-1, kind: routes_via}, …]
```

**`organiser_mapping.yaml`**
```yaml
mode: ${ORGANISER_MODE}
url: ${ORGANISER_URL}
format: jsonl          # jsonl|json_array|syslog|csv
fields:                # dotted paths in organiser records
  ts: timestamp
  ts_format: auto      # rfc3339|epoch_s|epoch_ms
  site: tenant
  host: host
  kind: type
  metric: metric
  value: value
  message: message
  link: link
  rtt_ms: latency_ms
  loss: packet_loss
  user: user
  src_ip: src_ip
site_map: {"tenant-1": acme, "tenant-2": globex}
```

**`users.yaml`**
```yaml
users:
  - {email: admin@acme.test, tenant: acme, role: tenant-admin, scrypt: "<hash of DEMO_PASSWORD>"}
  # viewer@acme.test (tenant-viewer), admin@globex.test (tenant-admin), ops@trustline.test (platform-ops)
```
`tl.py init` writes the hashes.

---

## D. Component implementation details

### D.1 Simulator (Owner C) — `simulator/`

- **Sites** (`sites.py`):
  - acme (`+05:30`): hosts web-01, web-02, api-01, db-01, gw-01; services web, api, db; links uplink-1, uplink-2.
  - globex (`+01:00`): a smaller copy.
- **Signals** (`signals.py`, numpy, seeded):
  - CPU = daily sine + AR(1) (φ=0.8, σ=3);
  - mem = slow drift; disk = linear fill;
  - net = diurnal + backup spikes;
  - RTT ~ log-normal(μ=ln 20, σ=0.25);
  - loss = two-state Gilbert-Elliott;
  - `up` = HTTP check; `error_rate` per service.
- **Emission** (`emit.py`): 1 s JSON-lines `POST http://collector-<site>:8081/v1/push`. Record kinds are `metric`, `probe`, `check` and `log`, as in the example lines below. sshd lines go out as syslog UDP to `:5514`. Every record has `ts` (RFC 3339 UTC, ms) and `eid` (uuid4).
- **Logs** (`logs.py`): app logs with an occasional `user=alice@acme.test`. On injection, add `AKIAIOSFODNN7EXAMPLE`, `ghp_…`, `Authorization: Bearer eyJ…`, `password=…`, a PEM block, or a 4111-1111-1111-1111 card number.
- **Faults** (`faults.py`, FastAPI `:8090`):
  - `POST /inject {site, kind, target?, magnitude?, duration_s?}` where kind is one of `cpu_spike|mem_leak|disk_fill|latency_ramp|packet_loss|link_down|service_errors|login_spray|brute_force|success_after_failures|secret_in_log|dup_burst|out_of_order|clock_skew`;
  - `POST /clear {site, kind?}`; `POST /failover {site, link}` (used by remediation); `GET /scenarios`; `GET /ground_truth`.
  - `latency_ramp` on uplink-1 also raises `error_rate` on the dependent `api` and `web` after 60–90 s. This produces the correlated story.
- **Ground truth** (`ground_truth.py`): appends `{inject_ts, site, kind, target, expected_src_kind, expected_detection}` to `/data/ground_truth.jsonl`.
- **Pre-warm:** `--prewarm 30m` emits 30 min of history at 50× speed with past timestamps, then switches to real time.
- **Replay:** `--replay file.jsonl` replays an organiser capture for a dress rehearsal.

Example emitted lines:
```
{"ts":"…Z","eid":"…","site":"acme","kind":"metric","host":"web-01","name":"cpu","value":47.2,"unit":"%"}
{"ts":"…Z","eid":"…","site":"acme","kind":"probe","link":"uplink-1","target":"10.0.0.1","rtt_ms":21.4,"loss":0.0,"ok":true}
{"ts":"…Z","eid":"…","site":"acme","kind":"check","service":"api","ok":true,"latency_ms":12,"status":200}
{"ts":"…Z","eid":"…","site":"acme","kind":"log","host":"api-01","app":"api","level":"error","msg":"…"}
<38>Nov 21 09:42:01 gw-01 sshd[1234]: Failed password for invalid user admin from 203.0.113.7 port 52344 ssh2
```

### D.2 Collector (Owner A; C writes the s5 detectors)

**Process.** `collector.main` runs uvloop under asyncio. It runs these tasks:
- the sources;
- the 7 stages joined by `asyncio.Queue(maxsize=10000)`;
- the sender loop and heartbeat (5 s);
- the action poller (2 s);
- topology push (start + 5 min);
- purge (hourly);
- the local UI (FastAPI on `:9090`).

Ingress (`:8081`, UDP `:5514`) is bound on the site network only.

**Stages**

| Stage | Module | Key functions and behaviour |
|---|---|---|
| s1 collect | `stages/s1_collect.py`, `sources/*` | `RawEvent(source, ts, recv_ts, body, eid)`. Writes to `raw.db` (`raw(id, eid, source, event_ts, collect_ts, body TEXT)`) in batched inserts every 200 ms. `sources/organiser.py`: `OrganiserSource(mapping).run()` dispatches by mode; httpx stream for SSE/poll, `websockets` for ws, a protocol class for UDP. `probes.py` (icmplib, real mode only) and `psutil` (collector self-metrics). |
| s2 filter | `s2_filter.py` | Parsers: `parse_sshd()` (regex for Failed/Accepted/invalid user), `parse_json_log()`, `parse_metric()`, `parse_probe()`, `parse_check()`. Drops health-check noise and debug lines. Source dedupe `TTLSet(600s)` keyed by `eid` or `sha1(source\|ts\|body)` → `dup_source_events++`. Outputs `ParsedEvent(kind, event_ts, host, fields, msg)`. |
| s3 redact | `redact/*`, `s3_redact.py` | Order matters: (1) secret rules then entropy, (2) identities to HMAC codes, (3) NER (optional), (4) template masking. `rules.py` is a list of `Rule(name, regex, validator)`: `aws_access_key \b(AKIA\|ASIA)[0-9A-Z]{16}\b`, `aws_secret`, `github_token \bgh[pousr]_[A-Za-z0-9]{36,}\b`, `github_pat`, `slack_token \bxox[abprs]-[A-Za-z0-9-]{10,}`, `jwt \beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}`, `bearer (?i)authorization:\s*bearer\s+\S+`, `password_kv (?i)\b(pass(word)?\|pwd\|secret\|token\|api[_-]?key)\s*[=:]\s*\S+`, `pem_private_key`, `card` (+Luhn), `url_credentials [a-z]+://[^/\s:@]+:[^/\s@]+@`. `entropy.py`: tokens of 20+ chars `[A-Za-z0-9+/=_-]` with Shannon entropy > 4.0 (hex digests max out at 4.0 bits/char, so they never trigger). Replacement is `<secret:rule>`. `pseudonym.py`: `Pseudonymizer(key).code(prefix, value)` with a reverse map kept in `state.db` (code→name, Tier 1 only). Applied to IPv4/IPv6, emails, usernames (`u:`), known hostnames and FQDNs (`h:`), services (`svc:`), links (`link:`). `ner.py`: Presidio if `WITH_NER=1`, else a heuristic (`Mr\|Ms\|Dr` + capitalised word, ~200-name first-name list) → `<person>`. `template.py`: masks numbers→`<num>`, hex→`<hex>`, uuid→`<uuid>`, paths→`<path>`, truncates to 160 chars, strips chars outside `SafeText`; `template_id = sha256(template)[:12]`. Counts per rule are kept for `redactions_by_rule`. Output is `CleanEvent`. |
| s4 aggregate | `s4_aggregate.py` | `WindowAggregator(win_s=5, grace=0.5, late_horizon=120)` keyed by (src, metric) on `event_ts`. Closes on watermark **or** wall-clock (`now ≥ end + grace`), so a stalled stream still flushes. Stats via numpy: min/avg/p50/p95/max. Closed windows keep samples for 120 s; a late sample re-emits `rev+1`; older samples increment `late_beyond_horizon`. Event templates are counted per (src, template_id, 5 s). |
| s5 analyse | `s5_analyse.py` (C) | **Fast path T:** per (src, metric), raw 1 s samples, 3 consecutive beyond the Poor threshold (thresholds from `/v1/config` or the mounted YAML) → FindingRecord `threshold_breach`, `breach_ts` = first sample, severity high. Re-arms after 3 normal windows. **L0 security**, sliding 60 s per coded IP: fails ≥10 → `brute_force`; distinct users ≥5 → `spray`; a success after ≥5 fails → `success_after_failures` (critical). IsolationForest (n_estimators=100, contamination=0.01) on [fail_rate, distinct_users, hour_of_day, success_ratio], trained on the first 5 min, gives `risk`. **Edge pre-score:** robust z per metric (attached to the finding). |
| s6 policy | `s6_policy.py` | `build_payload(records)` → `Payload.model_validate(dict)`. On `ValidationError`: append to egress as `blocked`, reason = first error (`extra_forbidden: raw_msg`), `blocked_total++`, **no seq consumed**. On success: assign `seq` (monotonic in state.db), `payload_id=uuid7()`, `sent_ts=now`; serialise `orjson`/`by_alias`; **egress guard** (`egress_guard.scan(json_bytes)` with all secret rules + raw IPv4/email regexes over the final bytes; any hit means `blocked` with reason `egress_guard:<rule>`); gzip with `mtime=0`; sha256. Priority: a finding with severity ≥ high flushes now, otherwise batch every 1 s. Max 500 records per payload. **Demo toggle** `POST /local/debug/leak-field` makes the next record include `raw_msg`, which is expected to be blocked. |
| s7 transmit | `s7_transmit.py`, `stores/outbox.py`, `stores/egress.py` | In one SQLite transaction: insert `outbox(payload_id PK, seq UNIQUE, created_ts, body BLOB, sha256, bytes, status pending, attempts 0, next_attempt_ts, priority)` and append egress `sealed`. **Hash chain:** `entry_hash = sha256(prev_hash ‖ payload_id ‖ seq ‖ sha256 ‖ status ‖ ts)`; the egress row stores `json_text` (exact pretty JSON of the body) for the proof panel. **Sender:** httpx.AsyncClient with an `ssl.SSLContext` (`cafile=ca.crt`, `load_cert_chain(client.crt, client.key)`, `minimum_version=TLSv1_3`) and timeout 5 s. **Lanes:** live = created < 10 s ago and not yet attempted, sent first (1 in flight); backlog = oldest seq first, up to 4 in flight, token bucket 50/s. Backoff `min(30, 2**attempts) * uniform(0.5, 1.5)`. 2xx stored/duplicate → `acked` + egress `acked`; non-retryable 4xx → `rejected`. **Cap:** 500 MB, compacts old metric windows to 60 s (new payloads; old ones marked `superseded`), never drops findings or heartbeats (implemented with a unit test; not exercised in the demo). |

**Stores.** SQLite WAL in the named volume `/data`: `raw.db`, `egress.db`, `outbox.db`, `state.db`. Purge: raw 7 d, egress 30 d, hourly.

**Enrolment** (`enrol.py`):
- `ENROL_MODE=preissued` uses `/certs/client`.
- `ENROL_MODE=token` generates an EC key and a CSR (`cryptography`) with `CN=<id>, O=tenant-<site>`, then `POST https://control:8444/v1/enrol {token, csr}`. Control returns `{cert, ca, ingest_base, region, expires}`. The collector stores the result in `/data/pki`.

**Actions** (`actions.py`), every 2 s:
- `GET {INGEST_BASE}/v1/actions?collector=…` over the same mTLS door returns approved actions.
- Each action is checked against `config/collector.yaml: actions_allowlist: [failover_uplink, failback_uplink, restart_service]`. Anything else gives `refused_local_policy`.
- Codes are resolved to real names with the reverse map, then the site simulator is called (`/failover`, `/clear`).
- The result is enqueued as an `ActionResultRecord`.

**Local UI API** (`localui.py`, `:9090`, never published beyond 127.0.0.1):

| Endpoint | Returns |
|---|---|
| `GET /local/status` | SaaS reachability, outbox depth and bytes, pending by lane, last ack, redactions by rule, blocked count, warm-up |
| `GET /local/raw?limit=&q=` | Raw events; secrets highlighted using the rule spans |
| `GET /local/egress?limit=&status=` | Egress entries |
| `GET /local/egress/{payload_id}` | Exact JSON, sha256, prev/entry hash, contributing raw event IDs, redaction spans for the diff |
| `POST /local/egress/verify` | Recomputes the chain; returns `{ok, broken_at}` |
| `GET /local/reidentify?code=` | Real name (on-site only) |
| `WS /local/ws` | Live egress feed |

**Collector tests:**
- `test_redaction`: every line in `corpus/secrets.txt` leaves no match after s3 (assert by re-running the rules). Benign corpus lines are unchanged except masking. HMAC is deterministic. The same user in different tenant keys gives different codes.
- `test_policy`: an extra field is blocked; a raw hostname in src is blocked; the egress guard catches an AKIA planted in a template; a blocked payload consumes no seq.
- `test_outbox`: the retry body is byte-identical (sha equal); the backoff schedule stays within bounds; lane ordering is right; a crash between send and ack leads to a resend that the server marks duplicate.
- `test_aggregate`: shuffled samples inside a window give the same stats; a late sample (<120 s) produces rev+1; a sample >120 s late increments the counter; a stalled stream still closes on wall-clock.
- `test_egress_chain`: tampering with a row makes verify report `broken_at=id`.
- `test_security_l0`: synthetic spray, brute force and success-after-failures each produce the right pattern and severity.

### D.3 Transport and ingest (Owner B)

**ingest-api `POST /v1/ingest`** (`ingest/main.py`):
1. Require `X-Client-Verify == SUCCESS`, else 403.
2. `identity.parse_dn()` → (tenant, collector).
3. Revoked serial (registry from control `GET /internal/registry` every 10 s, cached) → 403 `revoked`.
4. `registry.home(tenant) != REGION` → audit `residency_denied`, 403.
5. Body larger than 5 MB → 413.
6. `sha256(raw)`; gunzip (bounded at 20 MB) → `Payload.model_validate_json()`; a `ValidationError` → 422 `schema_rejected` (audited).
7. `payload.collector_id != cert CN` or `payload.region != REGION` → 403 `identity_mismatch`.
8. `ack = await js.publish(f"ingest.{REGION}.{tenant}", raw, headers={"Nats-Msg-Id": str(payload_id), "TL-Tenant": tenant, "TL-Collector": cn, "TL-Sha256": sha, "TL-Recv-Ts": now, "TL-Attempt-Ts": hdr, "TL-Lane": lane}, timeout=2)`. A NATS error → 503.
9. `status = "duplicate" if ack.duplicate else "stored"`. If duplicate, `delivery_counters.js_duplicates++`.
10. Audit row (`tenant_tx`). Upsert `collectors.last_seen` and `skew_ms` (live lane only).
11. Return `IngestAck`.

Latency budget for this path: under 30 ms.

**`GET /v1/actions`** returns `remediations WHERE collector_id=cn AND state='approved'` (then marks them `dispatched`).
**`GET /v1/config`** returns the tenant's thresholds and `config_hash`.

**Writer** (`workers/writer.py`, durable pull consumer `writer`, fetch batch 50, timeout 0.2 s), per message, inside one `tenant_tx(tenant)`:
1. `INSERT INTO received_payloads … ON CONFLICT (payload_id) DO NOTHING RETURNING 1`. A conflict means `db_conflicts++`, ack, skip.
2. Bulk insert records by type:
   - metrics: `ON CONFLICT (tenant_id, src, metric, event_ts) DO UPDATE SET … WHERE metrics.rev < EXCLUDED.rev`;
   - findings and events: `ON CONFLICT DO NOTHING`;
   - heartbeats: insert plus upsert `collectors`;
   - topology: upsert;
   - action_result: update `remediations`.
3. Commit, then `msg.ack()`, then publish `live.<tenant>.<type>` with timing (`stored_ts`).

**Ledger** (`ledger.py`, every 5 s): per collector, `min`, `max` and `count(distinct seq)` over 30 min. Holes unfilled for 120 s with `last_assigned_seq` beyond them produce a `delivery` live event and an audit entry.

**Reliability system tests** (`tests/`):
- `test_mtls`: no cert → handshake error; rogue cert → handshake error; `ssl.TLSVersion.TLSv1_2` max → handshake error; a valid cert → 200.
- `test_residency`: the globex cert against nginx-ap → 403.
- `test_duplicates`: post the same body 5× → 1 stored and 4 duplicate, and the DB row count increases once. Republish after the JetStream window (direct writer test with a forged message) → DB conflict counter goes up.
- `test_outage`: `docker stop ingest-ap` for 60 s → the collector buffer grows and SaaS marks the collector Poor within 60 s. Start it again → within 60 s `count(distinct seq) == max-min+1`, zero duplicate rows, and the sent IDs from `/local/egress` equal the `received_payloads` IDs.
- `test_isolation`: the acme JWT asking for globex incidents returns empty; raw SQL as `tl_api` with `app.tenant_id=acme` selecting `WHERE tenant_id='globex'` returns 0; unset tenant returns 0.
- `test_redaction_e2e`: inject `secret_in_log`, then grep every SaaS table (`SELECT … ::text ILIKE '%AKIA%'`) → 0 hits; the `events.template` contains `<secret:aws_access_key>`.

### D.4 Control plane (Owner B) — `saas/control`

- **`:8444` TLS (server-only, wan):** `POST /v1/enrol`. It validates the one-time token (marked used in `/data/tokens.json`), checks the CSR subject matches the token's tenant and allowed CN, signs with `cryptography` (7-day lifetime, EKU clientAuth), and returns cert, CA, `ingest_base`, region.
- **`:8000` internal:** `GET /internal/registry` returns tenants→home region, collectors, revoked serials and expected config hash. `POST /internal/revoke {serial}`.
- The CA key is mounted only here.

### D.5 Workers: health and AIOps (Owner C)

**Health** (`health.py`). On every metric window it evaluates the per-signal state from thresholds (with the tenant override from the DB). The source state is the worst signal state.
- An open incident on the source makes it at least Fair (the state cannot be better than Fair).
- A change writes a `health_transitions` row (UTC, old, new, reason such as `"cpu p95 91.2% ≥ 90 (poor)"`) and publishes `live.<t>.health`.

**Sweeper** (`sweeper.py`, every 2 s, per tenant):
- A source with no data for 30 s drops one level, reason "stale data", with `stale=true`.
- A collector with no heartbeat for 45 s goes Poor ("collector offline") along with all its sources. This writes a `latency_samples(kind='offline', event_ts=last_hb_event_ts, stored_ts=change)` row.
- An expected config hash that differs from the reported one marks the collector "config drift" (Fair).

**AIOps engine** (`aiops/engine.py`, consumer `aiops`). Per window:

- **L1** (`baseline.py`): per (tenant, src, metric) a ring of the last 120 values (10 min) gives median and MAD, plus EWMA(α=0.1). Hour-of-day buckets are used once they hold at least 30 samples; otherwise the global baseline applies. `z = (x − median) / (1.4826·MAD + ε)`, where ε = 1 % of the median or a unit floor (1 % CPU, 1 ms RTT). Rules:
  - warm-up: 60 windows, learning only, no alerts;
  - raise: \|z\|>3 in 2 of the last 3 windows;
  - clear: 3 normal windows;
  - state persisted to `baselines` every 30 s.
- **L2** (`hst.py`): `river.anomaly.HalfSpaceTrees(n_trees=10, height=6, window_size=60, seed=42)` on a per-host vector [cpu, mem, disk, rtt, loss] min-max scaled. Anomaly when the score is above the rolling p99 of the source's own scores (calibrated after warm-up).
- **L3** (`autoencoder.py`, cut-able): per tenant, `MLPRegressor(hidden_layer_sizes=(8,2,8), activation="tanh", max_iter=400, random_state=0)` fit X→X on StandardScaled warm-up vectors. The threshold is the p99 of training reconstruction error. Per-feature contribution is the squared-error share. Refit every 15 min in `asyncio.to_thread`.
- **Edge findings** (L0/T) arrive as findings and become alerts directly.
- **L4** (`correlate.py`): a `networkx.DiGraph` from the tenant's TopologyRecord, edge dependency→dependent ("impacts"). A new alert attaches to an open incident if any member is within undirected graph distance ≤2 **and** the onset is within ±120 s. Otherwise it creates `inc-NNNN` (per-tenant sequence). Root cause is the member with no anomalous ancestor; ties go to the earliest onset. Rejected candidates are recorded with the reason ("depends on X").
- **Priority** (`priority.py`):
  - P1: critical, or blast radius ≥4, or auth `success_after_failures`;
  - P2: high, or blast radius ≥2;
  - P3: medium;
  - P4: everything else.
- **L5** (`explain.py`): the first matching playbook rule produces this evidence object:
```json
{"incident":"inc-0412","priority":"P2","root_cause":"link:3fa1c09e7d22","affected":["svc:8b20…","svc:51aa…"],
 "signals":[{"src":"link:3fa1…","metric":"rtt","stat":"p95","value":184,"baseline":22,"z":7.4,"weight":0.61,"layer":"L1","onset_ts":"…"},…],
 "layers_agreeing":["L1","L2","L3"],"persistence":1.0,"rule":"upstream_link_degradation",
 "confidence":0.87,"confidence_parts":{"magnitude":0.46,"agreement":0.30,"persistence":0.20},
 "action":{"key":"failover_uplink","text":"…","reversible":true,"rollback_key":"failback_uplink","requires_role":"tenant-admin"},
 "why":"Link 3fa1 rtt p95 rose 94 s before 2 dependent services degraded",
 "trace":[{"step":"detect","layer":"L1","detail":"z=7.4 in 3/3 windows"},{"step":"corroborate","layer":"L2","detail":"HST score 0.93 > p99 0.81"},
          {"step":"correlate","detail":"graph distance ≤2 within ±120 s","members":3},
          {"step":"root_cause","chosen":"link:3fa1…","rejected":[{"src":"svc:8b20…","why":"depends on link:3fa1…; onset +94 s"}]},
          {"step":"playbook","rule":"upstream_link_degradation","matched":["root_kind=link","root_metric=rtt","impacted=error_rate"]},
          {"step":"policy","detail":"reversible, blast radius 3 ≤ 6, requires tenant-admin"}],
 "suppressed":[{"alert_id":"…","src":"svc:51aa…","metric":"rtt","reason":"absorbed into inc-0412"},{"…":"…","reason":"persistence 1/3 not met"}],
 "noise":{"raw_alerts":7,"incidents":1}}
```
  - `weight = |z_i| / Σ|z|` (for L3, the reconstruction-error share).
  - `confidence = 0.5·min(1, max|z|/8) + 0.3·(layers/3) + 0.2·persistence`.
  - Each incident write publishes `live.<t>.incident` with `breach_ts` (earliest member) and `alert_ts`.
- **Remediation policy** (`api/remediation.py`, state machine owned by B, rules by C). Policy check: the action key is in the playbook, `reversible` or explicit approval, blast radius ≤ max, the approver role is allowed, and the collector is online. Every transition writes `audit_log`. Verification waits until the root-cause state is ≤ Good for 3 windows, then sets `verified`; otherwise `failed` and rollback is offered.
- **Scoring** (`scripts/score.py`): matches ground-truth injections to incidents (same site, expected src kind, within 120 s) and reports precision, recall, MTTD and false alarms per hour.

**AIOps unit tests:**
- a step change flags in ≤3 windows; a daily-cycle series gives no flag; a single-window blip gives no flag (persistence); oscillation does not flap (hysteresis);
- link + 2 services within 90 s give 1 incident with root = link; unrelated hosts give 2 incidents;
- the confidence formula reproduces values;
- playbook rule selection, including the fallback;
- the priority table.

### D.6 API (Owner D; remediation endpoints with B)

- **Auth:** `POST /api/auth/login` checks scrypt and issues a JWT HS256 with `{sub, tenant, role, region, exp: 8h}`. `deps.current_user()` runs on every route. `tenant_db(user)` picks the pool by the tenant's home region (H8) and runs `tenant_tx`.
- **Routes:**

| Route | Purpose |
|---|---|
| `GET /api/overview` | KPIs, state counts, open incidents, noise ratio, redactions 24 h, SLO p95s |
| `GET /api/sources?state=&kind=&role=&collector=` | Sources list |
| `GET /api/sources/{src}` | Source detail with state history |
| `GET /api/metrics/query?src=&metric=&stat=&from=&to=&step=` | `time_bucket` series plus baseline band (median ± 3·1.4826·MAD from `baselines`) plus threshold lines |
| `GET /api/events?q=&level=&category=&src=&from=&to=` | Events |
| `GET /api/events/facets` | Facet counts |
| `GET /api/security/findings` | Security findings |
| `GET /api/incidents` | Incidents list |
| `GET /api/incidents/{id}` | Incident with evidence |
| `POST /api/incidents/{id}/ack\|resolve` | Incident actions |
| `POST /api/incidents/{id}/remediations` | Propose (runs the policy check) |
| `POST /api/remediations/{id}/approve\|reject\|rollback` | Remediation actions (tenant-admin only) |
| `GET /api/fleet` | Own tenant; platform-ops uses `fleet_overview()` |
| `GET /api/delivery` | Ledger and counters per collector |
| `GET /api/trust/audit?payload_id=` | Ingest audit lookup |
| `POST /api/trust/isolation-test` | Runs the cross-tenant query as the caller; returns `{rows:0, sql, policy}` |
| `GET /api/trust/residency` | Per-region row counts by tenant; the api holds an admin-free counting function per region |
| `GET /api/slo?window=15m` | p50/p95/max per kind plus histogram plus per-hop breakdown |
| `GET/PUT /api/thresholds` | PUT by tenant-admin, audited |
| `GET /api/topology` | Topology |
| `GET /api/audit` | Audit log |

- **WS `/ws?token=`:** on connect, `hello` with a snapshot (health states, open incidents, fleet). Then the api forwards `live.<tenant>.*` from **both** regional NATS connections, filtered to the user's tenant (platform-ops gets `collector` events only). On `render_ack`, it inserts `latency_samples` (kind dash or alert), correcting `render_ts` with the client offset from ping/pong.

### D.7 Real-time measurement

| Kind | Start | End |
|---|---|---|
| dash | `event_ts` (window start = oldest sample) | `render_ts` (double `requestAnimationFrame` after the store update) |
| alert | `breach_ts` (first offending sample) | incident or finding `render_ts` |
| offline | `last_hb_event_ts` | Poor state change |

The SLO page shows p50, p95 and max against the targets, with a hop waterfall: window wait, seal, wire+ingest, store, push, render. Backlog-lane and `rev>0` samples are excluded and counted separately, and the panel says so.

---

## E. Dashboard UI/UX spec (Datadog-inspired, original TrustLine product)

### E.1 Stack and why

| Library | Why |
|---|---|
| React 19 + Vite 7 + TypeScript 5.9 | Per the Phase 2 doc; fast HMR; stable. |
| Tailwind v4 + shadcn/ui (Radix) | Copy-in accessible primitives (dialogs, drawers, popovers, command menu) that can be themed with our own tokens, so it looks nothing like Datadog. |
| ECharts 5.6 behind a 40-line `<EChart>` wrapper (`echarts.init`, ResizeObserver, `setOption` notMerge=false) | One library for timeseries with `markArea` (anomaly band), `markLine` (thresholds), `markPoint` (incident overlays), `connect` for synced crosshairs, `dataZoom` brush-to-zoom feeding the global time picker, plus gauge, heatmap and bar. Canvas handles 1 Hz live updates. No wrapper dependency, which avoids React 19 compatibility risk. |
| `@xyflow/react` 12 + dagre | Service map / trust-boundary diagram with auto layout, custom nodes coloured by health, and animated edges for live flow. |
| TanStack Query 5 | Server cache. WS deltas go into it via `setQueryData`; reconnect triggers `invalidateQueries` to fill gaps. |
| TanStack Table 8 | Datadog-dense sortable tables: fleet, host list, events, ledger. |
| Zustand 5 | Global UI state: time range, live mode, UTC/local, tenant, facet selections, side-panel stack. |
| react-router 7 | Simple routes; the side panel is deep-linked via `?panel=`. |
| cmdk | ⌘K command palette (jump to source, incident, page). |
| date-fns + `@date-fns/tz` | UTC/local/source-tz rendering. |
| Fontsource packages | Fonts work offline on venue Wi-Fi. |
| SVG hex map (custom, about 150 lines) | No hexbin dependency. |
| MSW | D builds before the API exists. |

### E.2 Design tokens (`src/styles/tokens.css`, Tailwind `@theme`)

The palette is deliberately not Datadog purple: a teal "trust line" accent on a blue-black base.

| Token | Value | Use |
|---|---|---|
| `--tl-bg-0` | `#0A0E13` | App background |
| `--tl-bg-1` | `#0F141B` | Sidebar, top bar |
| `--tl-bg-2` | `#141A22` | Cards, panels |
| `--tl-bg-3` | `#1B232E` | Hover, raised, table header |
| `--tl-border` / `--tl-border-strong` | `#263040` / `#344155` | Dividers |
| `--tl-text-1/2/3` | `#E6EDF3` / `#A3B1C2` / `#6E7C8E` | Primary, secondary, muted |
| `--tl-accent` / hover / subtle | `#14B8A6` / `#2DD4BF` / `rgba(20,184,166,.12)` | Brand, primary buttons, focus ring, live pulse |
| `--tl-boundary` | `#F5B544` | Anything crossing the trust boundary; on-site banner |
| `--tl-redact` | `#A78BFA` | Redaction chips `‹secret:aws_access_key›` |
| `--tl-pseudo` | `#60A5FA` | Pseudonymous ID chips |
| Health | excellent `#22A06B` ●, good `#8BD17C` ◐, fair `#F5A524` ▲, poor `#E5484D` ■, stale `#6E7C8E` with diagonal hatch ⌀ | Always colour **plus** glyph **plus** label (colour-blind safe) |
| Severity | critical `#E5484D`, high `#F97316`, medium `#F5A524`, low `#60A5FA`, info `#8A97A8` | |
| Chart categorical | `#4FB3BF #F2A65A #9B8AFB #E86A92 #6CC07A #F2D15C #5DA0E8 #C77DDB` | Series |
| Anomaly band | `rgba(163,177,194,.14)`; threshold lines dashed in state colours | |

- **Type:** Inter Variable 13 px base (Datadog-like density), sizes 11/12/13/14/16/20/28; JetBrains Mono 12 px for IDs, hashes and JSON. Line height 1.4.
- **Spacing and shape:** 4 px grid, radius 4 (chips), 6 (cards, inputs), 10 (dialogs), table rows 32 px.
- **Motion:** 120 ms ease-out; live dot pulses at 2 s. Elevation comes from background tone, not shadows.
- **Logo:** a wordmark "TrustLine" with a glyph of a horizontal line crossing a vertical bar (the boundary), in teal.

### E.3 Information architecture (left nav, collapsible 56↔220 px)

1. Overview
2. Infrastructure (Host Map · Host List)
3. Metrics (Lens explorer)
4. Network (Links · Service Map)
5. Events (redacted log patterns)
6. Security (signals)
7. Monitors (health rules and state history)
8. Incidents (badge)
9. Fleet
10. **Trust Center** (Boundary · Delivery Ledger · Egress Audit · Isolation · Residency · Detection Quality)
11. **Real-time SLOs**
12. Audit Log
13. Settings (thresholds, users)

The bottom of the nav shows user, role and tenant, plus a region badge.

**Global top bar:** breadcrumb · tenant badge (platform-ops gets a switcher limited to fleet) · home-region chip `ap-south` · search/⌘K · **time picker** (Live · 5m · 15m · 1h · 4h · 1d · custom; `[`/`]` shift) · UTC/Local/Source-TZ toggle · WS status chip showing measured lag (`● live 0.6 s`).

**Privacy ribbon** (unique, every page, 24 px): `Metadata-only · raw fields received: 0 · redactions 24h: 1,284 · blocked payloads: 2 · last payload 0.8 s ago · residency: ap-south ✓`.

### E.4 Interaction patterns borrowed from Datadog (re-implemented)

- **Facet panel** on the left of explorers, with counts and checkboxes.
- **Query bar** with `key:value` tags and autocomplete (`site:acme role:db state:poor metric:rtt`).
- **Click any row → right side panel** (560 px, stackable, deep-linkable) instead of a page change.
- **Pivot links** in every panel ("View metrics · Related events · Open incident · Show on map").
- **Brush-to-zoom** on any chart sets the global time range and turns Live off.
- **Synced crosshair** across charts on a page.
- **Event overlays** (incident markers) on timeseries.
- **Saved views** in localStorage.
- **Keyboard:** `/` search, `⌘K` palette, `Esc` closes the panel.

### E.5 What is unique to TrustLine (talking points against Datadog)

1. **Egress Proof panel** at the edge (local, zero-egress), with hash-chain verification and a raw↔clean diff. Datadog's Live Capture uploads pre-redaction data to the SaaS; TrustLine's proof never leaves the site.
2. **Provenance chip** on every SaaS datum (`payload 0193a1c2 · seq 18234 · sha 9f3c…`). Clicking it shows the ingest audit, and that hash matches the local panel.
3. **Pseudonym chips** (`host·9c1e`, role "db") with the tooltip "real name exists only on-site", plus an on-site **Re-identify** page.
4. **Evidence panel** with signal weights, the confidence formula broken into parts, layers agreeing, the reasoning trace, rejected root-cause hypotheses, and suppressed alerts ("why we did NOT alert": warm-up, persistence, hysteresis).
5. **Measured SLO panel** showing the hop waterfall, not claims.
6. **Trust Center:** live boundary diagram with per-hop counters, Delivery Ledger (sent vs received, duplicates dropped per layer), one-click isolation test, residency map.
7. **Noise meter:** raw alerts → incidents (e.g. `47 → 6, −87 %`).
8. **Remediation stepper:** policy check → approval → outbound pull → verify → rollback, with an audit trail.

### E.6 Page wireframes

**App shell + Overview**
```
+------+------------------------------------------------------------------------------------------+
| [TL] | Overview                 [acme] [ap-south]  [/ Search ⌘K]  [● Live | Past 15m v] [UTC v] [● 0.6s] |
|  ◉   |------------------------------------------------------------------------------------------|
|  ▦   | Metadata-only · raw fields received 0 · redactions 24h 1,284 · blocked 2 · last payload 0.8s |
|  ∿   |------------------------------------------------------------------------------------------|
|  ⇄   | [Sources 23] [● Excellent 15] [◐ Good 4] [▲ Fair 3] [■ Poor 1] [Collectors 2/2 online]      |
|  ≡   | [Open incidents 2 (P1:1)] [Noise 47→6 (-87%)] [Dash p95 6.4s ✓≤10] [Alert p95 4.1s ✓≤15]   |
|  ⚑   |------------------------------------------------------------------------------------------|
|  ⚠   | Health over time (stacked state bands)          | Storyline (incident cards)                 |
|  ▤   | ███████████▓▓▓▓░░░████████████                 | P1 inc-0412 Uplink degradation  conf 0.87  |
|  ⛨   |                                                 |   link·3fa1 → svc·8b20, svc·51aa  2m ago   |
|  ⏱   | Top movers (|z|)          | Security signals      |   [Open] [Propose fix]                     |
|  ⚙   | link·3fa1 rtt z=7.4 ▲     | spray ip·3fa1 risk .92 | P3 inc-0409 Memory drift host·9c1e  0.71  |
+------+------------------------------------------------------------------------------------------+
```

**Infrastructure: Host Map (hex)**
```
| Fill by: [Health v]  Size by: [none v]  Group by: [site v][role v]  Filter: role:db state:fair   |
|------------------------------------------------------------------------------------------|
| acme · ap-south                              | globex · eu-west                          |
|   web        api        db      gateway      |   web       api                         |
|  ⬢ ⬢ ⬢     ⬢ ⬢       ⬢▲      ⬢             |  ⬢ ⬢      ⬢                           |
|  links: ⬢■ uplink-1  ⬢ uplink-2              |                                           |
|------------------------------------------------------------------------------------------|
| Legend: ● Excellent ◐ Good ▲ Fair ■ Poor ⌀ Stale        Hover: host·9c1e (db) cpu p95 72%  |
```
Clicking a hex opens a side panel: state + **reason** + since (UTC/local) · transitions timeline · sparklines per signal with threshold lines · open incidents · provenance chip.

**Metrics: Lens explorer**
```
| Query: [metric rtt v] [stat p95 v] from [src: link·3fa1 x] group by [src v]  [+ add]  [Compare: 1h ago] |
| ---------------------------------------------------------------------------------------------------- |
|  ms                                         ▲ inc-0412                                                 |
| 200 - - - - - - - - - - - - - - - - - - - - - - - - - - - -  Poor threshold (dashed red)            |
| 100 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░╱‾‾‾‾  (grey band = learned baseline ±3σ_robust)            |
|  20 ───────────────────────────────────╱                                                             |
| [table: src | last | baseline | z | state ]                                                         |
```

**Events: redacted log patterns**
```
| Facets            | [search: level:error category:auth]                         [▁▂▅▇▃▁ histogram] |
| Level             | Time       Src        Count Pattern                                         |
|  ☑ error 412      | 09:42:05   h·9c1e     37    Failed password for ‹u:4be1…› from ‹ip:3fa1…›    |
|  ☐ warn 88        |                               port ‹num›           [‹redacted 0›]          |
| Category          | 09:41:50   svc·8b20   1     POST /login token=‹secret:github_token›          |
|  ☑ auth           |                               [‹redacted 1›] [provenance ▸]                 |
| Redacted only ☐   |                                                                             |
```

**Incident detail** (the Phase 4 money page)
```
| P1 inc-0412  Upstream link degradation     ● open  since 09:40:12 UTC (15:10 IST)  [Ack] [Resolve] |
| Storyline:  ROOT CAUSE ─────▶ CRITICAL ─────▶ IMPACT                                                |
|             link·3fa1 rtt↑     svc·8b20 errors↑   svc·51aa slow, web up 0.97                          |
|             09:38:30           09:40:04 (+94s)     09:40:20                                            |
|----------------------------------------------+-----------------------------------------------------|
| EVIDENCE                     confidence 0.87  | Blast radius (mini service map, root ringed)        |
|  signal              value  base   z   weight|   [uplink-1]■──▶[api]▲──▶[web]◐                       |
|  link·3fa1 rtt p95   184ms  22    7.4 ██████ 61%|                └─▶[db]●                           |
|  svc·8b20 error_rate 9%     0.4%  4.8 ████   39%|-----------------------------------------------------|
|  Layers agreeing: [L1 ✓][L2 ✓][L3 ✓]           | RECOMMENDED ACTION (rule upstream_link_degradation)  |
|  Confidence = 0.5×0.93 + 0.3×1.0 + 0.2×1.0     |  "Fail over to secondary uplink"                     |
|  WHY: link rtt rose 94 s before errors on      |  Policy ✓ reversible ✓ blast 3≤6 ✓ needs admin       |
|  2 services that depend on it                  |  [Propose] → [Approve] → dispatched → executed →     |
| REASONING PATH (expand)                        |  verifying… ✓ Good ×3        [Rollback]              |
|  1 detect L1 z=7.4 3/3  2 corroborate L2 .93   |  Audit: proposed by admin@acme 09:41:02 …            |
|  3 correlate 7 alerts → 1  4 root: link (svc   |                                                       |
|    rejected: depends on link) 5 playbook 6 policy| Merged alerts (7) · Suppressed (3, reasons)          |
```

**Fleet**
```
| Collector | Tenant | Region   | Status    | Last seen | Version | Config     | CPU | Mem   | Buffer | Acked seq | Skew  |
| col-ac01  | acme   | ap-south | ● Online  | 0.9 s     | 0.3.1   | a41c ✓     | 3%  | 182MB | 0      | 18233     | 12 ms |
| col-9b01  | globex | eu-west  | ■ Offline | 47 s      | 0.3.1   | 77e2 ⚠drift| –   | –     | 312*   | 9120      | –     |
```
Side panel: heartbeat sparkline, delivery ledger, cert serial/expiry.

**Trust Center**
```
| [Boundary] [Delivery Ledger] [Egress Audit] [Isolation] [Residency] [Detection quality]              |
| Boundary:  SITE acme (raw stays)  ║ H2 mTLS TLS1.3 ║  REGION ap-south                                  |
|   sim ─H1─▶ collector ──▶ egress ═╬═══ 1.0 pay/s ═╬═▶ nginx ─H3─▶ ingest ─H4─▶ JetStream ─H5─▶ TSDB    |
|   raw.db 7d  redactions 1,284    ║ blocked 2       ║  dup dropped: JS 12 · DB 3   RLS ✓               |
| Ledger: col-ac01 sent 18,234 · received 18,234 · gaps 0 · retries 37 · JS dup 12 · DB conflicts 3 ✓  |
| Isolation: [Run cross-tenant test]  → as acme: SELECT … WHERE tenant_id='globex' → 0 rows ✓ (policy shown) |
| Residency: map pins ap-south(acme: 412k rows, globex: 0) · eu-west(globex: 120k, acme: 0) · 403s today: 1 |
```

**Real-time SLOs**
```
| Collection→dashboard p95 6.4 s  [====|----] target 10 ✓ | Breach→alert p95 4.1 s ✓15 | Offline detect 47 s ✓60 |
| Waterfall (p95): window 4.7 | seal 0.4 | wire+ingest 0.05 | store 0.08 | push 0.02 | render 0.11              |
| Histogram per kind · samples n=1,920 (excluded: backlog 412, revisions 9 - shown, not hidden)                  |
```

**Collector Local UI** (`:9090`; amber "ON-SITE · NEVER EXPOSED" banner, same design system)
```
| ON-SITE  col-ac01 · acme · SaaS: ● reachable (or ■ OFFLINE · 312 payloads buffered, oldest 61 s)       |
| [Egress Proof] [Raw (on-site)] [Redactions] [Buffer] [Re-identify] [Debug]                              |
| seq    payload_id   status   sha256    chain | Payload 0193a1c2 · seq 18234 · 3 records · 1 redaction  |
| 18234  0193a1c2…    ACKED ✓  9f3c…     ✓     |  RAW (on-site)              │ SENT (exact bytes)      |
| 18235  0193a1c3…    PENDING  1b0e…     ✓     |  "token=ghp_x8Kq…Zt" (red)  │ "‹secret:github_token›" |
| —      —            BLOCKED  —         ✓     |  user alice@acme.test       │ ‹u:4be1c02a9f10›        |
|        extra_forbidden: raw_msg              |  [Verify chain ✓ 18,236 entries]  [Copy sha]            |
```

### E.7 Real-time behaviour

- **One WS per tab:** reconnect backoff 0.5→8 s; a banner shows "Reconnecting…"; on reconnect, invalidate queries for the current range.
- **Live store:** ring buffers per (src, metric) for 15 min. Chart updates are batched with `requestAnimationFrame` and throttled to 1 Hz.
- **Render ack:** after each batch, a double rAF, then `render_ack` with corrected `render_ts`. Clock offset from ping/pong every 10 s.
- **Live off:** charts freeze and a "Resume live" button appears.
- **Animations:** off in live charts.
- **Toast (sonner)** for new P1/P2 incidents, with a click-through.

### E.8 Page priority for Owner D

- **P0 (by G2/G3):** Login, AppShell + ribbon, Overview, Host Map + side panel, Fleet, Trust Center (Ledger, Isolation, Audit lookup), SLO, Incidents + Incident detail. The local UI (Egress Proof, Raw, Buffer) is built by A with D's components.
- **P1:** Events explorer, Security, Service map, Metrics Lens.
- **P2:** Monitors/threshold editor, Audit log, Residency map, Settings.

---

## F. Demo script, risks and cut list

### F.1 Final demo (about 10 min; every step live; maps to the rubric)

**Pre-flight** (T−20 min): `python scripts/tl.py up`, warm-up of at least 15 min, organiser stream flowing (or simulator), `tl.py doctor` green. Open tabs:
1. SaaS as admin@acme
2. SaaS as admin@globex (incognito)
3. Collector local UI `:9090`
4. Trust Center
5. A terminal

| # | Time | Action | Shows | Criteria hit |
|---|---|---|---|---|
| 1 | 0:00 | Q1/Q2 story, then the boundary diagram (Trust Center › Boundary, live counters) | Two tiers, single door H2 | P1, P2, P4 presentation |
| 2 | 0:45 | Secret in raw log (organiser or `tl.py demo secret`) | Local Raw viewer shows the red AKIA/ghp. Egress panel shows the same payload with `‹secret:…›`, redactions 1. SaaS Events shows the redacted template; the provenance sha matches the local sha. | P3 raw stays local, metadata only; scenario 1; egress proof (mandatory) |
| 3 | 2:00 | Leak-field toggle | Egress row BLOCKED `extra_forbidden: raw_msg`, no seq consumed | Allow-list fails closed (P2/P3, bonus privacy) |
| 4 | 2:40 | Password spray | Security signal: `ip·3fa1`, 37 fails, 14 distinct users, risk .92, P1; no auth lines in SaaS | Scenario 3 |
| 5 | 3:30 | Latency ramp on uplink-1 | Health turns Fair→Poor with reasons; 7 alerts → **1 incident**; storyline root→impact; **Evidence panel** (weights, confidence parts, layers, reasoning path, suppressed) | Scenarios 4 and 6; P4 explainable (weighted higher); bonus noise reduction |
| 6 | 5:00 | Remediation | Propose → policy ✓ → viewer cannot approve → admin approves → collector pulls over mTLS → simulator fails over → verified Good ×3 → audit trail → Rollback available | Scenario 8; bonus safe remediation |
| 7 | 6:00 | Memory leak | Lens chart: value leaves the grey baseline band before the static threshold; L1/L3 contributions | Scenario 5; beyond thresholds |
| 8 | 6:40 | Outage: `tl.py outage --seconds 60` | Local UI "SaaS OFFLINE · N buffered"; Fleet shows col-ac01 Poor "collector offline" at about 47 s (measured); restart; backlog drains; **Ledger sent = received, gaps 0, duplicates dropped per layer** | Scenario 2; P3 failure handling; 10-mark reliability; bonus buffering |
| 9 | 8:00 | Duplicates and out-of-order | Charts stay correct; window `rev 1` shown; JS/DB dedupe counters | Scenario 7; bonus global-ready |
| 10 | 8:40 | Isolation + residency + mTLS | Isolation button → 0 rows + SQL; `tl.py demo wrong-region` → 403; `demo rogue` → handshake fail; residency map row counts | P2/P3 tenant separation, global-ready |
| 11 | 9:20 | SLO page + Detection quality | Measured p95s against targets; precision/recall vs ground truth | Mandatory real-time; P4 correctness |
| 12 | 9:40 | Limitations + roadmap | Demo CA without CRL; one host; simulated remediation; short warm-up; no load test at 1,000 collectors; HTTP on localhost; future human-approved autonomous ops | P4 honesty |

**Phase 3 checkpoint walk-through** (about 5 min): steps 2, 3, 8, 11, plus a Host Map tour of the 4 states, stale handling (`docker pause` simulator → stale ⌀ in 30 s) and a threshold edit with its reason.

Keep `docs/qa-cheatsheet.md` with one-line answers to the 12 questions in guide §7.

### F.2 Risks

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Docker/WSL not installed; only 8 GB RAM | High / blocks everything | User step G1–G3 now. `.wslconfig` 5 GB. Lite profile. Run the full demo on a 16 GB teammate laptop. |
| Organiser stream format or transport unknown, or it arrives late | High / mandatory | Adapter + mapping YAML. Ask organisers now. Simulator mimics the likely JSON. `--replay` to rehearse with a capture. |
| Venue Wi-Fi blocks pulls or installs | Med / high | Pre-pull images and pre-build tonight. `docker save -o trustline-images.tar`. Fonts bundled. Hotspot backup. |
| Windows gotchas: CRLF, MSYS path conversion, SQLite on bind mounts, Hyper-V excluded ports | High / med | `.gitattributes`, `MSYS_NO_PATHCONV`, named volumes, `tl.py doctor` port check. |
| RLS behaviour on hypertables | Low / high | M0 smoke test; plain-table fallback. |
| Warm-up makes the first minutes alert-blind or false-alarm prone | Med / med | Persisted baselines, prewarm, start the stack early, `score.py` tuning. |
| False alarms during the live demo | Med / high (P4) | Persistence/hysteresis, 5 min warm-up, threshold tuning at M4, noise meter. |
| Latency p95 drifts over 10 s under load | Low / high | 5 s windows, 1 s batch, flush on window close, perf soak at M4. |
| One frontend owner overloaded | High / med | Page priorities, A builds local UI pages, MSW mocks from hour 1. |
| Laptop sleep or Windows Update reboot | Med / high | Power settings, active hours, plugged in. |
| Merge conflicts on shared contracts | Med / med | Ownership by directory; contracts frozen at G0; versioned changes only. |

### F.3 Cut list (first cut first)

1. L6 agent and Ollama (default off)
2. Presidio NER (regex + heuristic spotter stays)
3. Browser TLS (H7)
4. L3 autoencoder
5. Control-plane token+CSR enrolment (pre-issued certs stay; enrolment is explained on the diagram)
6. `/v1/config` pull (mounted YAML instead)
7. Outbox 500 MB compaction (unit test only)
8. Metrics Lens query builder (fixed chart presets)
9. Service map auto-layout (static positions)
10. Residency map graphic (a table instead)
11. Audit log page, threshold editor UI (YAML + restart)
12. Second region's AIOps (keep eu nginx, ingest, tsdb, writer so residency stays real; aiops only in ap)
13. Remediation execution via collector pull (fallback: api calls the simulator directly, labelled "simulated executor"; approval and audit still shown)

**Never cut:** organiser adapter, egress proof panel, measured SLOs, redaction + allow-list + egress guard, outbox + ledger + dedupe, 4 states + stale/offline, Evidence panel + correlation, isolation test.

---

## G. Steps the user must do, in order

1. **Now (10 min): confirm with organisers**
   - final demo time and the Phase 3/4 checkpoint times;
   - **the organiser telemetry stream**: endpoint/transport, a sample record, auth, timestamp field and format, whether it is per team or per tenant, the injection schedule, and whether teams get a capture file to rehearse with.
2. **Install WSL2** (admin PowerShell): `wsl --install`, then reboot.
3. **Install Docker Desktop:** `winget install -e --id Docker.DockerDesktop`.
   - Start it and accept the WSL2 backend.
   - Create `C:\Users\Jeel\.wslconfig` with `[wsl2]` `memory=5GB` `processors=6` `swap=4GB`, then run `wsl --shutdown` and restart Docker.
   - Verify with `docker version`, `docker compose version` and `docker run --rm hello-world`.
4. **Pre-pull images** while the network is good: `docker pull nginx:1.30.7-alpine nats:2.15.3-alpine timescale/timescaledb:2.32.1-pg16 python:3.12-slim node:24-alpine alpine:3.22`.
5. **GitHub:**
   - `winget install -e --id GitHub.cli`, then `gh auth login`;
   - `git config --global user.name/user.email`;
   - add teammates as collaborators (repo Settings → Collaborators, or `gh api -X PUT repos/JeelVaghasiya/bluelock/collaborators/<login>`);
   - decide public vs private (question H15).
6. **Python:** `uv self update` (0.10.2 → current), then `uv python install 3.12`. Node 24 is already fine.
7. **Ports:** run `netsh interface ipv4 show excludedportrange protocol=tcp` and make sure 3000, 8000, 8090, 8443, 8543, 9090 and 9091 are not inside an excluded range.
8. **Power:** set "never sleep when plugged in", set Windows Update active hours to cover the event, and close heavy apps during the demo.
9. **Team:**
   - map A, B, C and D to real people;
   - every teammate installs Docker, Node ≥20, uv/Python 3.12 and git, and clones the repo;
   - pick the **demo machine** (16 GB or more RAM preferred).
10. After the parent creates the scaffold, run `python scripts/tl.py init`, then `python scripts/tl.py up`, then open `http://localhost:3000`.
11. **Optional:** a GCP VM (gcloud SDK is already installed) to host Tier 2 as a "real cloud region" while collectors run on the laptop. Only do this if venue Wi-Fi is reliable.
12. **Prepare:** the backup demo recording (Win+Alt+R), Phase 1 artefacts (one-page data flow, allow-list, MVP list) if not yet shown, and a phone hotspot.

---

## H. Open decisions (each with a recommended default)

| # | Question | Default if no answer |
|---|---|---|
| H1 | When did the hackathon start, and when are the Phase 3 and final checkpoints? | T0 = 20:00 Fri, Phase 3 ≈ T+10, final ≈ T+18 (14:00 Sat) |
| H2 | Team size, and do roles A–D map to 4 real people? | 4 people with the rebalanced ownership (simulator to C, writer to B). With 3 people: merge B into A for transport and keep D on UI. |
| H3 | Is the organiser stream spec available? | Build the HTTP-push/poll JSONL adapter plus mapping YAML; simulator as backup |
| H4 | Which machine runs the demo? | A teammate's laptop with 16 GB+; this 8 GB machine for dev/lite |
| H5 | Keep both regions? | Yes (residency proof); lite profile for dev |
| H6 | TimescaleDB vs plain Postgres? | TimescaleDB image, hypertables + `time_bucket`, no continuous aggregates or compression; plain-PG fallback |
| H7 | L6 agent / Ollama in scope? | No; deterministic narrative from evidence. Revisit only after G3 on a 16 GB+ machine. |
| H8 | L3 autoencoder? | scikit-learn MLP (no PyTorch); cut 4th |
| H9 | Presidio NER? | Off (`WITH_NER=0`); regex + heuristic name spotter |
| H10 | Enrolment? | Pre-issued certs first; token+CSR enrolment by G3 if time |
| H11 | Deployment: local Docker only, or split laptop / cloud? | Local Docker only |
| H12 | Browser HTTPS? | HTTP on localhost, stated as a limitation |
| H13 | Branding? | "TrustLine", dark, teal `#14B8A6`, amber boundary, violet redaction; no Datadog purple, logo or feature names |
| H14 | Cross-platform scripting? | `scripts/tl.py` (stdlib Python) plus POSIX sh executed inside containers; Makefile optional |
| H15 | Repo visibility and `others/`? | Keep `others/` out of git. Make the repo private during the event if the rules allow, public at submission. |
| H16 | Approve the ADRs (5 s windows, 1 s batch, fast-path alerts, EventRecord, TopologyRecord, remediation pull)? | Yes; required for the targets and the scenarios |
| H17 | Remediation execution path? | Collector outbound pull (H2b); fallback is the api calling the simulator directly |
| H18 | Demo logins? | Four seeded users sharing `DEMO_PASSWORD` |
| H19 | Commit or push cadence? | Trunk-based on `main`; the parent commits the scaffold only after you approve |
| H20 | Python in containers? | 3.12-slim; local dev via uv 3.12, not system 3.14 |

### Critical files for implementation
- `G:\Jeel\Desktop\blue_lock\schema\trustline_schema\v1.py`
- `G:\Jeel\Desktop\blue_lock\compose.yaml`
- `G:\Jeel\Desktop\blue_lock\deploy\certs\gen.sh`, `deploy\nginx\templates\ingest.conf.template`, `deploy\db\init\30-rls.sql`
- `G:\Jeel\Desktop\blue_lock\collector\collector\stages\s6_policy.py` and `s7_transmit.py`
- `G:\Jeel\Desktop\blue_lock\saas\workers\workers\aiops\explain.py` and `dashboard\src\pages\IncidentDetail.tsx`

Source documents: `G:\Jeel\Desktop\blue_lock\others\TrustLine — Phase 2 Architecture Plan.md`, `G:\Jeel\Desktop\blue_lock\others\Smart_Observability_Hackathon_Evaluation_Guide_v8.pdf` (10 pages, extracted via pdftotext), `G:\Jeel\Desktop\blue_lock\others\Datadog Secure AIOps Solution Analysis.md`, `G:\Jeel\Desktop\blue_lock\others\WhatsApp Image 2026-11-20 at 3.41.04 PM.jpeg` (Atria CICADA 2K26 24-hour evaluation sheet).