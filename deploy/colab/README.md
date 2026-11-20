# Running the SaaS tier on Google Colab

Use this path when your laptop can't run Docker or doesn't have the RAM. The split matches the real architecture:

| Where | What runs | Why |
|---|---|---|
| Laptop (Tier 1, "on-site") | simulator, collector (`scripts/native.py site`) | Raw logs and the HMAC key never leave the site |
| Colab (Tier 2, "regional SaaS") | Postgres + RLS, NATS JetStream, nginx mTLS, control, ingest-api, workers, api + dashboard | Most of the RAM and CPU (AIOps models) |

```
laptop collector ──mTLS 1.3──► bore.pub:PORT ──raw TCP──► Colab nginx :8443 ─► ingest ─► NATS ─► workers ─► Postgres
browser ──HTTPS──► https://<random>.trycloudflare.com ──► Colab api :8000 (dashboard, REST, WebSocket)
```

`bore` is a plain TCP relay, so the TLS session runs **between your collector and Colab's nginx**: the relay only sees ciphertext, and nginx still rejects any client without a certificate from our CA. The dashboard goes through a Cloudflare quick tunnel (HTTPS, WebSockets supported, no account needed).

## Steps

On the laptop (Git Bash or PowerShell from the repo root):

```bash
python scripts/tl.py init --local                         # once: .env secrets, users.yaml, certificates
EXTRA_SAN=DNS:bore.pub FORCE=1 sh deploy/certs/gen.sh     # server cert must also name bore.pub
cd dashboard && npm ci && npm run build && cd ..          # optional: bundle builds it if npm exists
python scripts/native.py bundle                           # -> dist/trustline-colab.zip (contains secrets!)
```

In Colab:

1. Open `deploy/colab/trustline_saas.ipynb` (File → Upload notebook).
2. Run cell 1 and upload `dist/trustline-colab.zip`.
3. Run cell 2 to install, then cell 3 to start. Cell 3 prints the dashboard URL and the ingest relay address, e.g. `bore.pub:41234`.

Back on the laptop:

```bash
python scripts/native.py site --ingest https://bore.pub:41234
```

Open the trycloudflare URL and log in as `admin@acme.test` with `DEMO_PASSWORD` from `.env`. The collector local UI is at http://localhost:9090.

## Caveats (and what to say if a judge asks)

- **Ephemeral:** Colab VMs reset after about 12 h, or about 90 min idle. Data in Postgres is lost, which is fine for a demo. Re-run cells 1–3, and the collector's outbox replays anything it buffered meanwhile (byte-identical, deduplicated).
- **No Docker in Colab.** Services run as processes, and the network isolation between tiers is shown by the laptop/Colab split, not by Docker networks.
- **TimescaleDB is optional.** The schema falls back to plain Postgres tables plus time indexes (`tl_hypertable()` in `deploy/db/init/20-schema.sql`).
- **One region only** (ap-south). The EU region and the residency demo need the Docker `--full` stack or a second VM.
- **Demo controls:** the dashboard's "inject fault" buttons call the simulator, which runs on the laptop. Use `python scripts/tl.py demo <scenario>` locally, or `curl localhost:8090/inject` instead.
- The tunnel URLs change on every start.

## Sturdier alternatives (same `native.py saas` command)

- **Oracle Cloud Always Free (ARM, up to 24 GB RAM):** a persistent VM. Run `native.py saas --setup` and open port 8443 instead of using bore.
- **GCP e2-standard-2 with credits** (`gcloud` is already installed on this laptop): same steps. Use a static IP and put its DNS name in `EXTRA_SAN`.
- **Docker Desktop + WSL2 on a 16 GB machine:** `python scripts/tl.py up` runs everything, including the EU region.
