# TrustLine

**Privacy-first observability and AIOps.** Raw telemetry stays on site, and only allow-listed, redacted metadata reaches the cloud. The SaaS then turns that metadata into explainable incidents.

```
 SITE (Tier 1, raw data never leaves)                       ║ H2 mTLS ║   REGION (Tier 2, metadata only)
 simulator / organiser stream ─H1─▶ collector                ║  TLS1.3 ║
   1 collect → 2 filter → 3 redact → 4 aggregate              ║  cert   ║ nginx ─▶ ingest-api ─▶ JetStream ─▶ writer / aiops ─▶ TimescaleDB (RLS)
   → 5 analyse → 6 policy (allow-list) → 7 transmit ═════════╬═════════╬═▶                                              │
   raw.db · egress.db (hash chain) · outbox.db · local UI     ║         ║                       api + dashboard ◀────────┘
```

See [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) for the full plan: rubric mapping, milestones, UI spec and demo script.

## Status

| Component | State |
|---|---|
| `schema/`: trustline.v1 allow-list (Pydantic, `extra="forbid"`) | ✅ built, 12 tests |
| `simulator/`: 9 signal families, 14 fault scenarios, remediation hooks | ✅ built, 4 tests |
| `collector/`: 7-stage pipeline, redaction, egress guard, hash-chained egress log, outbox, mTLS sender, L0 security, fast-path alerts, organiser adapter, local UI API | ✅ built, 39 tests, verified end-to-end against a mutual-TLS mock ingest (25 s outage → 0 loss, 0 duplicates) |
| `deploy/`: PKI script, nginx mTLS, NATS, DB schema + row-level security, compose | ✅ written. PKI and mTLS verified (rogue CA, missing cert and TLS 1.2 are all refused). Compose not yet run: Docker is needed |
| `saas/`: ingest, workers (writer, health, AIOps L1–L5), control, api | ✅ built. AIOps has 9 tests; all services import. A full run needs Postgres (Docker or Colab) |
| `dashboard/`: SaaS UI (Overview, Infrastructure, Metrics, Service map, Events, Security, Incidents with explainable evidence and remediation, Fleet, Trust Center, SLO, Audit, Settings) plus the collector's on-site UI (Egress proof, Raw vault, Buffer, Re-identify) | ✅ built, type-checked, with a demo-data mode |

## Setup (Windows)

1. **WSL2** (admin PowerShell): `wsl --install`, then reboot.
2. **Docker Desktop**: `winget install -e --id Docker.DockerDesktop`, then start it.
   - Create `%UserProfile%\.wslconfig` with `[wsl2]`, `memory=5GB` and `processors=6`.
   - Run `wsl --shutdown` and restart Docker.
   - Check it works: `docker run --rm hello-world`.
3. **Python 3.12 for local dev** (optional): `uv python install 3.12`.
4. In this folder:
   ```
   python scripts/tl.py init      # .env with random secrets, demo users, certificates
   python scripts/tl.py doctor    # checks docker, ports, files
   python scripts/tl.py up        # lite: site acme -> region ap-south   (--full adds globex -> eu-west)
   ```
5. Open the apps:
   - SaaS dashboard: <http://localhost:3000> (`admin@acme.test` / `trustline-demo`)
   - Collector local UI (on-site only): <http://localhost:9090>
   - Simulator: <http://localhost:8090/scenarios>

## Demo scenarios

```
python scripts/tl.py demo secret        # secret in a raw log -> redacted before egress
python scripts/tl.py demo leak-field    # a developer bug adds a raw field -> BLOCKED by the allow-list
python scripts/tl.py demo spray         # password spray -> L0 security finding
python scripts/tl.py demo latency       # uplink latency ramp -> dependent services degrade -> 1 incident
python scripts/tl.py demo memleak | cpu | disk | loss | link-down | errors | dupes | ooo | skew
python scripts/tl.py outage --seconds 60  # SaaS unreachable -> buffer -> drain, 0 loss / 0 duplicates
python scripts/tl.py demo clear
```

## No Docker? Run the SaaS on Google Colab

The laptop runs the site (collector + simulator) and a free Colab VM runs the regional SaaS. Full guide: [`deploy/colab/README.md`](deploy/colab/README.md).

```
python scripts/tl.py init --local                          # once: .env, users, certs
EXTRA_SAN=DNS:bore.pub FORCE=1 sh deploy/certs/gen.sh      # Git Bash: server cert must name the tunnel host
cd dashboard && npm ci && npm run build && npm run build:local && cd ..
python scripts/native.py bundle                            # -> dist/trustline-colab.zip (contains secrets: never commit/share)
# upload the zip in deploy/colab/trustline_saas.ipynb and run the cells; it prints
#   ingest: bore.pub:NNNNN   and   dashboard: https://<random>.trycloudflare.com
python scripts/native.py site --ingest https://bore.pub:NNNNN
```

## Dashboard only (no backend)

```
cd dashboard && npm ci
npm run mock          # SaaS UI with a live synthetic tenant at http://localhost:5173
npm run dev           # against a real api on :8000 (proxied)
```
On the login page, **Explore with demo data** switches any build to demo mode. The collector UI also accepts `?demo`.

## Developing without Docker

```
uv venv --python 3.12 .venv && uv pip install --python .venv -e schema -r collector/requirements.txt -r simulator/requirements.txt pytest
python scripts/tl.py test unit
python scripts/mock_ingest.py          # mTLS mock SaaS on :18443 for the collector
```

## Repository rules

- `others/` (organiser material) and `.env` are gitignored. Never commit them.
- `schema/` is the frozen contract. Change it only with a version bump.
- Tier-1 files (`config/sites/*`, HMAC keys) are mounted only into collectors.
