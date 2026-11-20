#!/usr/bin/env python3
"""Run TrustLine WITHOUT Docker.

  Laptop (Windows/macOS/Linux) = Tier 1 site:   simulator + collector
  Google Colab (or any Linux VM) = Tier 2 SaaS: Postgres, NATS, nginx(mTLS), control, ingest, workers, api

  python scripts/native.py site  [--ingest https://bore.pub:PORT] [--seconds 0]
  python scripts/native.py saas  [--setup] [--tunnel] [--timescale]      (Linux only)
  python scripts/native.py bundle                                         -> dist/trustline-colab.zip
  python scripts/native.py stop

The only thing that crosses from laptop to Colab is the mTLS ingest traffic (hop H2). The
`bore` tunnel relays raw TCP, so TLS terminates in Colab's nginx: the relay sees ciphertext only.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / ".run"
LOGS = RUN / "logs"
CERTS = ROOT / "deploy" / "certs" / "out"
PY = sys.executable
BORE_VER = "v0.6.0"
NATS_VER = "v2.15.3"
CLOUDFLARED_VER = "2026.11.1"


def env_file() -> dict[str, str]:
    p = ROOT / ".env"
    out: dict[str, str] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def sh(cmd: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, check=check, **kw)


def spawn(name: str, args: list[str], env: dict, cwd: Path) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{name}.log", "ab")
    full = {**os.environ, "PYTHONUNBUFFERED": "1", **env}
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen(args, cwd=cwd, env=full, stdout=log, stderr=subprocess.STDOUT, **kw)
    (RUN / "pids").mkdir(parents=True, exist_ok=True)
    (RUN / "pids" / f"{name}.pid").write_text(str(p.pid))
    print(f"  started {name:<12} pid {p.pid:<7} log .run/logs/{name}.log")
    return p


def wait_port(port: int, timeout: float = 60, host: str = "127.0.0.1") -> bool:
    import socket
    t = time.time()
    while time.time() - t < timeout:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.5)
    return False


def supervise(procs: dict[str, subprocess.Popen], seconds: int = 0) -> None:
    print("\nRunning. Ctrl+C stops everything.")
    t0 = time.time()
    try:
        while not seconds or time.time() - t0 < seconds:
            for name, p in procs.items():
                if p.poll() is not None:
                    print(f"!! {name} exited with {p.returncode} - see .run/logs/{name}.log")
                    tail = (LOGS / f"{name}.log").read_text(errors="replace").splitlines()[-15:]
                    print("\n".join("   " + x for x in tail))
                    raise KeyboardInterrupt
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for name, p in procs.items():
            if p.poll() is None:
                try:
                    p.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
                except Exception:
                    p.terminate()
        time.sleep(1.5)
        for p in procs.values():
            if p.poll() is None:
                p.kill()
        print("stopped")


# ====================================================================== Tier 1: site on the laptop
def cmd_site(a) -> None:
    env = env_file()
    if not env.get("HMAC_KEY_ACME") or "change-me" in env.get("HMAC_KEY_ACME", ""):
        sys.exit("run `python scripts/tl.py init --local` first (creates .env, users, certs)")
    if not (CERTS / "collectors" / "col-ac01" / "client.crt").exists():
        sys.exit("no collector certificate - run `python scripts/tl.py certs --local`")
    ui = ROOT / "dashboard" / "dist-local"
    if ui.exists():  # the collector serves its on-site UI from collector/static
        shutil.copytree(ui, ROOT / "collector" / "static", dirs_exist_ok=True)
    data = RUN / "site-acme"
    data.mkdir(parents=True, exist_ok=True)
    ingest = a.ingest or "https://127.0.0.1:8443"
    procs = {}
    procs["collector"] = spawn("collector", [PY, "-m", "collector.main"], {
        "SITE": "acme", "COLLECTOR_ID": "col-ac01", "REGION": "ap-south", "SRC_TZ": "+05:30",
        "INGEST_BASE": ingest, "SIMULATOR_URL": "http://127.0.0.1:8090", "CONTROL_URL": "https://127.0.0.1:8444",
        "HMAC_KEY": env["HMAC_KEY_ACME"], "CONFIG_DIR": str(ROOT / "config"),
        "SITE_DIR": str(ROOT / "config" / "sites" / "acme"), "DATA_DIR": str(data),
        "CERT_DIR": str(CERTS / "collectors" / "col-ac01"), "CA_FILE": str(CERTS / "ca.crt"),
        "WARMUP_SECONDS": env.get("WARMUP_SECONDS", "300"),
        "ORGANISER_MODE": env.get("ORGANISER_MODE", "off"), "ORGANISER_URL": env.get("ORGANISER_URL", ""),
        "ORGANISER_TOKEN": env.get("ORGANISER_TOKEN", ""), "ENROL_MODE": "preissued",
    }, ROOT / "collector")
    if not wait_port(8081, 30):
        print("collector did not open :8081 - check .run/logs/collector.log")
    if not a.no_sim:
        procs["simulator"] = spawn("simulator", [PY, "-m", "simulator.main"], {
            "SITES": "acme=http://127.0.0.1:8081", "SYSLOG_TARGETS": "acme=127.0.0.1:5514",
            "GROUND_TRUTH": str(RUN / "ground_truth.jsonl"), "SIM_SEED": "42",
            "PREWARM_MINUTES": env.get("PREWARM_MINUTES", "0")}, ROOT / "simulator")
    print(f"\nSite 'acme' -> ingest {ingest}")
    print("  Collector local UI  http://localhost:9090   (Egress proof, raw vault, buffer)")
    print("  Simulator           http://localhost:8090/scenarios")
    supervise(procs, a.seconds)


# ====================================================================== Tier 2: SaaS on Linux / Colab
def _pg_bin() -> Path:
    cands = sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)
    if not cands:
        sys.exit("postgres not installed - run with --setup")
    return cands[0]


def setup_linux(timescale: bool) -> None:
    if platform.system() != "Linux":
        sys.exit("saas mode needs Linux (Colab / a VM / WSL)")
    sh("apt-get -qq update && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y postgresql nginx curl unzip "
       "> /dev/null")
    if timescale:  # optional; schema works on plain Postgres too
        r = sh("curl -fsSL https://packagecloud.io/install/repositories/timescale/timescaledb/script.deb.sh | bash "
               "> /dev/null && apt-get -qq install -y timescaledb-2-postgresql-$(ls /usr/lib/postgresql | sort -n | "
               "tail -1) > /dev/null", check=False)
        if r.returncode == 0:
            sh("timescaledb-tune --quiet --yes > /dev/null", check=False)
    bin_ = RUN / "bin"
    bin_.mkdir(parents=True, exist_ok=True)
    arch = "arm64" if platform.machine() in ("aarch64", "arm64") else "amd64"
    if not (bin_ / "nats-server").exists():
        sh(f"curl -fsSL https://github.com/nats-io/nats-server/releases/download/{NATS_VER}/"
           f"nats-server-{NATS_VER}-linux-{arch}.tar.gz | tar xz --strip-components=1 -C {bin_} "
           f"nats-server-{NATS_VER}-linux-{arch}/nats-server")
    if not (bin_ / "bore").exists():
        barch = "aarch64" if arch == "arm64" else "x86_64"
        sh(f"curl -fsSL https://github.com/ekzhang/bore/releases/download/{BORE_VER}/bore-{BORE_VER}-{barch}-"
           f"unknown-linux-musl.tar.gz | tar xz -C {bin_}")
    if not (bin_ / "cloudflared").exists():
        sh(f"curl -fsSL -o {bin_}/cloudflared https://github.com/cloudflare/cloudflared/releases/download/"
           f"{CLOUDFLARED_VER}/cloudflared-linux-{arch} && chmod +x {bin_}/cloudflared")
    pip = f"{PY} -m pip install -q"
    sh(f"{pip} -e {ROOT}/schema -e {ROOT}/saas/common")
    for svc in ("ingest", "control", "workers", "api"):
        sh(f"{pip} -r {ROOT}/saas/{svc}/requirements.txt")


def psql(sql: str, db: str = "postgres", check: bool = True) -> str:
    r = subprocess.run(["su", "postgres", "-c", f"psql -v ON_ERROR_STOP=1 -qtA -d {db}"], input=sql, text=True,
                       capture_output=True)
    if check and r.returncode:
        raise SystemExit(f"psql failed: {r.stderr}")
    return r.stdout.strip()


def start_postgres(env: dict) -> None:
    sh("service postgresql start > /dev/null")
    for _ in range(30):
        if subprocess.run(["su", "postgres", "-c", "pg_isready -q"]).returncode == 0:
            break
        time.sleep(1)
    if psql("SELECT 1 FROM pg_database WHERE datname='trustline'") == "1":
        print("  postgres: database trustline exists")
        return
    print("  postgres: creating database, roles, schema, RLS")
    psql("CREATE DATABASE trustline")
    psql(f"""CREATE ROLE tl_ingest LOGIN PASSWORD '{env["PG_INGEST_PASSWORD"]}' NOBYPASSRLS;
CREATE ROLE tl_worker LOGIN PASSWORD '{env["PG_WORKER_PASSWORD"]}' NOBYPASSRLS;
CREATE ROLE tl_api LOGIN PASSWORD '{env["PG_API_PASSWORD"]}' NOBYPASSRLS;""")
    for f in ("20-schema.sql", "30-rls.sql"):
        psql((ROOT / "deploy" / "db" / "init" / f).read_text(encoding="utf-8"), db="trustline")
    ts = psql("SELECT count(*) FROM pg_extension WHERE extname='timescaledb'", db="trustline")
    print(f"  postgres: ready ({'TimescaleDB hypertables' if ts == '1' else 'plain Postgres tables'})")


def nginx_conf() -> Path:
    tpl = (ROOT / "deploy" / "nginx" / "templates" / "ingest.conf.template").read_text(encoding="utf-8")
    srv = CERTS / "servers" / "nginx-ap"
    tpl = (tpl.replace("${TL_SERVER_NAME}", "nginx-ap bore.pub").replace("${TL_UPSTREAM}", "127.0.0.1:8001")
           .replace("${TL_REGION}", "ap-south").replace("/etc/nginx/certs/server/", f"{srv}/")
           .replace("/etc/nginx/certs/ca.crt", str(CERTS / "ca.crt")))
    tpl = re.sub(r"\n\s*resolver [^\n]*", "", tpl).replace("access_log /dev/stdout tl;", f"access_log {LOGS}/nginx-access.log tl;")
    # Compatibility with nginx < 1.25.1 (e.g. Ubuntu 22.04 / Colab default nginx 1.18):
    # 'http2 on;' directive was added in 1.25.1; older versions require 'listen ... http2;'
    tpl = re.sub(r"\n\s*http2\s+on\s*;", "", tpl)
    tpl = re.sub(r"listen\s+(\d+)\s+ssl(?!\s+http2)\s*;", r"listen \1 ssl http2;", tpl)
    conf = RUN / "nginx.conf"
    conf.write_text(f"""worker_processes 1;
pid {RUN}/nginx.pid;
error_log {LOGS}/nginx-error.log warn;
events {{ worker_connections 512; }}
http {{
  client_body_temp_path {RUN}/nginx-tmp;
  proxy_temp_path {RUN}/nginx-tmp;
{tpl}
}}
""")
    (RUN / "nginx-tmp").mkdir(parents=True, exist_ok=True)
    return conf


def nats_conf() -> Path:
    c = (ROOT / "deploy" / "nats" / "nats.conf").read_text(encoding="utf-8")
    c = c.replace('store_dir: "/data"', f'store_dir: "{RUN}/nats"')
    p = RUN / "nats.conf"
    p.write_text(c)
    return p


def tunnel(name: str, args: list[str], pattern: str, timeout: float = 40) -> str | None:
    p = spawn(name, args, {}, RUN)
    rx = re.compile(pattern)
    t = time.time()
    while time.time() - t < timeout:
        txt = (LOGS / f"{name}.log").read_text(errors="replace")
        m = rx.search(txt)
        if m:
            return m.group(1)
        if p.poll() is not None:
            break
        time.sleep(0.5)
    return None


def cmd_saas(a) -> None:
    if a.setup:
        setup_linux(a.timescale)
    env = env_file()
    for k in ("PG_INGEST_PASSWORD", "PG_WORKER_PASSWORD", "PG_API_PASSWORD", "NATS_INGEST_PASSWORD",
              "NATS_WORKER_PASSWORD", "NATS_API_PASSWORD", "JWT_SECRET"):
        if not env.get(k) or "change-me" in env[k]:
            sys.exit(f".env is missing {k} - upload the bundle from `python scripts/native.py bundle`")
    if not (CERTS / "servers" / "nginx-ap" / "server.crt").exists():
        sys.exit("no server certificates in deploy/certs/out (bundle them, or run deploy/certs/gen.sh)")
    RUN.mkdir(exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Linux":
        sh("killall -9 nats-server bore cloudflared 2>/dev/null || true", check=False)
        sh("fuser -k 4222/tcp 8222/tcp 8443/tcp 8000/tcp 8001/tcp 8010/tcp 2>/dev/null || true", check=False)
    start_postgres(env)
    procs: dict[str, subprocess.Popen] = {}
    procs["nats"] = spawn("nats", [str(RUN / "bin" / "nats-server"), "-c", str(nats_conf()), "-js"], {
        k: env[k] for k in ("NATS_INGEST_PASSWORD", "NATS_WORKER_PASSWORD", "NATS_API_PASSWORD")}, RUN)
    wait_port(4222, 20)
    cfg = {"CONFIG_DIR": str(ROOT / "config"), "TENANTS_FILE": str(ROOT / "config" / "tenants.yaml"),
           "WARMUP_SECONDS": env.get("WARMUP_SECONDS", "300"), "STALE_AFTER_SECONDS": env.get("STALE_AFTER_SECONDS", "30"),
           "OFFLINE_TIMEOUT_SECONDS": env.get("OFFLINE_TIMEOUT_SECONDS", "45")}
    pg = "postgresql://{u}:{p}@127.0.0.1:5432/trustline"
    nats = "nats://{u}:{p}@127.0.0.1:4222"
    procs["control"] = spawn("control", [PY, "-m", "control.main"], {
        **cfg, "CA_DIR": str(CERTS / "ca"), "TLS_DIR": str(CERTS / "servers" / "control"), "DATA_DIR": str(RUN / "control"),
        "INTERNAL_PORT": "8010", "ENROL_PORT": "8444",
        "ENROL_TOKENS": f"acme={env.get('ENROL_TOKEN_ACME', '')},globex={env.get('ENROL_TOKEN_GLOBEX', '')}"},
        ROOT / "saas" / "control")
    procs["ingest"] = spawn("ingest", [PY, "-m", "uvicorn", "ingest.main:app", "--host", "127.0.0.1", "--port", "8001",
                                       "--no-access-log"], {
        **cfg, "REGION": "ap-south", "CONTROL_INTERNAL": "http://127.0.0.1:8010",
        "DATABASE_URL": pg.format(u="tl_ingest", p=env["PG_INGEST_PASSWORD"]),
        "NATS_URL": nats.format(u="ingest", p=env["NATS_INGEST_PASSWORD"])}, ROOT / "saas" / "ingest")
    wait_port(8001, 60)
    procs["workers"] = spawn("workers", [PY, "-m", "workers.main"], {
        **cfg, "REGION": "ap-south", "DATABASE_URL": pg.format(u="tl_worker", p=env["PG_WORKER_PASSWORD"]),
        "NATS_URL": nats.format(u="worker", p=env["NATS_WORKER_PASSWORD"])}, ROOT / "saas" / "workers")
    dash = ROOT / "dashboard" / "dist"
    procs["api"] = spawn("api", [PY, "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000",
                                 "--no-access-log"], {
        **cfg, "DB_AP_URL": pg.format(u="tl_api", p=env["PG_API_PASSWORD"]),
        "NATS_AP_URL": nats.format(u="api", p=env["NATS_API_PASSWORD"]), "JWT_SECRET": env["JWT_SECRET"],
        "USERS_FILE": str(ROOT / "config" / "users.yaml"), "SIMULATOR_URL": a.simulator_url,
        **({"DASHBOARD_DIR": str(dash)} if dash.exists() else {})}, ROOT / "saas" / "api")
    sh(f"nginx -c {nginx_conf()} -p {RUN}", check=True)
    wait_port(8443, 10)
    print("  nginx mTLS ingest on :8443")
    if a.tunnel:
        hostport = tunnel("bore", [str(RUN / "bin" / "bore"), "local", "8443", "--to", "bore.pub"],
                          r"listening at (bore\.pub:\d+)")
        url = tunnel("cloudflared", [str(RUN / "bin" / "cloudflared"), "tunnel", "--no-autoupdate", "--url",
                                     "http://127.0.0.1:8000"], r"(https://[a-z0-9-]+\.trycloudflare\.com)")
        print("\n" + "=" * 72)
        print(f" Dashboard (share this)   {url or 'cloudflared failed - see .run/logs/cloudflared.log'}")
        print(f" mTLS ingest relay        {hostport or 'bore failed - see .run/logs/bore.log'}")
        if hostport:
            print("\n On your laptop run:")
            print(f"   python scripts/native.py site --ingest https://{hostport}")
        print("=" * 72)
    try:
        supervise(procs)
    finally:
        sh(f"nginx -c {RUN}/nginx.conf -p {RUN} -s stop", check=False)


# ====================================================================== bundle for Colab upload
EXCLUDE_DIRS = {".git", ".venv", "node_modules", "others", ".run", "dist-local", "__pycache__", ".pytest_cache",
                "dist", ".idea", ".vscode"}


def cmd_bundle(a) -> None:
    """Zip the repo (code + .env + users.yaml + certs + built dashboard) for upload to Colab.

    Contains secrets (.env, private keys): upload it only to your own Colab session. Never commit it.
    """
    if not (ROOT / ".env").exists():
        sys.exit("run `python scripts/tl.py init --local` first")
    crt = CERTS / "servers" / "nginx-ap" / "server.crt"
    try:
        import ssl
        sans = [v for _, v in ssl._ssl._test_decode_cert(str(crt)).get("subjectAltName", ())]
    except Exception:
        sans = []
    if "bore.pub" not in sans:
        print("WARNING: nginx-ap server cert does not name bore.pub - the laptop collector will refuse the tunnel.")
        print("  Fix (Git Bash):  EXTRA_SAN=DNS:bore.pub FORCE=1 sh deploy/certs/gen.sh")
    if a.build_dashboard and (ROOT / "dashboard" / "package.json").exists():
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if npm:
            sh(f'"{npm}" --prefix dashboard run build')
    out = ROOT / "dist" / "trustline-colab.zip"
    out.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in ROOT.rglob("*"):
            rel = p.relative_to(ROOT)
            parts = set(rel.parts)
            keep_dist = rel.parts[:2] == ("dashboard", "dist")
            if p.is_dir() or (parts & EXCLUDE_DIRS and not keep_dist) or p.suffix in (".db", ".pyc") or \
                    "-wal" in p.name or "-shm" in p.name:
                continue
            z.write(p, rel.as_posix())
            n += 1
    print(f"wrote {out.relative_to(ROOT)} ({n} files, {out.stat().st_size // 1024} KiB)")
    print("Upload it in the Colab notebook (deploy/colab/trustline_saas.ipynb). It contains secrets - don't share it.")


def cmd_stop(_a) -> None:
    for pid_file in (RUN / "pids").glob("*.pid"):
        try:
            pid = int(pid_file.read_text())
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                os.killpg(pid, signal.SIGTERM)
            print(f"stopped {pid_file.stem}")
        except Exception:
            pass
        pid_file.unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(prog="native", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("site", help="laptop: simulator + collector")
    s.add_argument("--ingest", help="https://bore.pub:PORT printed by the Colab notebook")
    s.add_argument("--no-sim", action="store_true", help="organiser stream only, no simulator")
    s.add_argument("--seconds", type=int, default=0, help="stop after N seconds (0 = run until Ctrl+C)")
    s.set_defaults(fn=cmd_site)
    s = sub.add_parser("saas", help="Linux/Colab: the regional SaaS stack")
    s.add_argument("--setup", action="store_true", help="apt/pip install everything first")
    s.add_argument("--tunnel", action="store_true", help="open bore (mTLS ingest) + cloudflared (dashboard) tunnels")
    s.add_argument("--timescale", action="store_true", help="try installing TimescaleDB (optional)")
    s.add_argument("--simulator-url", default="http://127.0.0.1:8090",
                   help="demo controls in the dashboard call this (not reachable from Colab by default)")
    s.set_defaults(fn=cmd_saas)
    s = sub.add_parser("bundle", help="zip the repo + secrets + certs for Colab upload")
    s.add_argument("--no-build-dashboard", dest="build_dashboard", action="store_false")
    s.set_defaults(fn=cmd_bundle)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
