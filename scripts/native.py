#!/usr/bin/env python3
"""Run TrustLine WITHOUT Docker.

  Laptop (Windows/macOS/Linux) = Tier 1 site:   simulator + collector
  Google Colab / Linux VM / a Windows laptop = Tier 2 SaaS: Postgres, NATS, nginx(mTLS), control,
                                                ingest, workers, api

  Colab:  python scripts/native.py saas --setup --tunnel        (prints bore.pub + trycloudflare URLs)
          python scripts/native.py site --ingest https://bore.pub:PORT --saas https://xxxx.trycloudflare.com
  LAN:    python scripts/native.py saas --setup --lan           (server laptop, Windows or Linux)
          python scripts/native.py site-pack                    -> dist/trustline-site-pack.zip (give to site laptop)
          python scripts/native.py site --lan 192.168.x.y       (site laptop)
  Other:  python scripts/native.py install --site|--saas        (create .venv + install Python deps)
          python scripts/native.py bundle                       -> dist/trustline-colab.zip
          python scripts/native.py stop

The only thing that crosses from site to SaaS is the mTLS ingest traffic (hop H2). The
`bore` tunnel relays raw TCP, so TLS terminates in Colab's nginx: the relay sees ciphertext only.
On a LAN the site dials https://nginx-ap:8443 with nginx-ap pinned to the server's IP, so the
server certificate is still verified by name. The server exposes only :8443 (mTLS) and :8000
(dashboard); Postgres, NATS, ingest and control listen on 127.0.0.1.
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
if platform.system() == "Windows" and (ROOT / ".venv" / "Scripts" / "python.exe").exists():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
elif platform.system() != "Windows" and (ROOT / ".venv" / "bin" / "python").exists():
    PY = str(ROOT / ".venv" / "bin" / "python")
WIN = platform.system() == "Windows"
BIN = RUN / "bin"
BORE_VER = "v0.6.0"
NATS_VER = "v2.15.3"
CLOUDFLARED_VER = "2026.11.1"
PG_WIN_VER = "17.11-1"
NGINX_WIN_VER = "1.30.1"
SAAS_PROCS = ("nats", "control", "ingest", "workers", "api", "nginx")


def lan_ip() -> str:
    """The address other machines on this network reach us at (no packet is sent)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
        try:
            u.connect(("10.255.255.255", 1))
            return u.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def exe(name: str) -> str:
    return str(BIN / (f"{name}.exe" if WIN else name))


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
    sim_ui = ROOT / "dashboard" / "dist-sim"
    if sim_ui.exists():  # the simulator serves the Simulation Lab from simulator/static
        shutil.copytree(sim_ui, ROOT / "simulator" / "static", dirs_exist_ok=True)
    data = RUN / "site-acme"
    data.mkdir(parents=True, exist_ok=True)
    pins = ""
    if a.lan:  # dial the cert's real name; pin it to the server's LAN address
        ingest, pins = "https://nginx-ap:8443", f"nginx-ap={a.lan}"
        a.saas = a.saas or f"http://{a.lan}:8000"
        import socket
        with socket.socket() as t:
            t.settimeout(3)
            if t.connect_ex((a.lan, 8443)) != 0:
                print(f"WARNING: {a.lan}:8443 is not reachable yet. On the server run `native.py saas --lan`, allow it "
                      "through Windows Firewall (Private network), and check both laptops are on the same Wi-Fi/hotspot.")
    else:
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
        "ORGANISER_TOKEN": env.get("ORGANISER_TOKEN", ""), "ENROL_MODE": "preissued", "INGEST_HOSTS": pins,
        "BIND_HOST": "127.0.0.1",  # the raw vault / egress UI and push port stay on this machine
    }, ROOT / "collector")
    if not wait_port(8081, 30):
        print("collector did not open :8081 - check .run/logs/collector.log")
    if not a.no_sim:
        procs["simulator"] = spawn("simulator", [PY, "-m", "simulator.main"], {
            "SITES": "acme=http://127.0.0.1:8081", "SYSLOG_TARGETS": "acme=127.0.0.1:5514",
            "GROUND_TRUTH": str(RUN / "ground_truth.jsonl"), "SIM_SEED": "42",
            "COLLECTOR_LOCAL": "http://127.0.0.1:9090", "INGEST_BASE": ingest, "CERT_ROOT": str(CERTS),
            "INGEST_HOSTS": pins, "BIND_HOST": a.lab_host,
            # SLA benchmarks: the Lab logs in to the SaaS dashboard API and reads its own measurements
            "SAAS_URL": a.saas or env.get("SAAS_URL", ""), "SAAS_EMAIL": env.get("SAAS_EMAIL", "admin@acme.test"),
            "SAAS_PASSWORD": env.get("SAAS_PASSWORD") or env.get("DEMO_PASSWORD", "trustline-demo"),
            "PREWARM_MINUTES": env.get("PREWARM_MINUTES", "0")}, ROOT / "simulator")
    print(f"\nSite 'acme' -> ingest {ingest}" + (f" (nginx-ap = {a.lan})" if a.lan else ""))
    if a.saas:
        print(f"  SaaS dashboard      {a.saas}")
    print("  Collector local UI  http://localhost:9090   (Egress proof, raw vault, buffer)")
    print("  Simulation Lab      http://localhost:8090   (fire scenarios, stories, trust-boundary attacks)")
    supervise(procs, a.seconds)


# ====================================================================== Tier 2: SaaS on Linux / Colab
def _pg_bin() -> Path:
    cands = sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)
    if not cands:
        sys.exit("postgres not installed - run with --setup")
    return cands[0]


def _download(url: str, dest: Path) -> None:
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        total, n, last = int(r.headers.get("Content-Length") or 0), 0, 0.0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            n += len(chunk)
            if total and time.time() - last > 3:
                print(f"    {n * 100 // total}% of {total // (1 << 20)} MB", flush=True)
                last = time.time()
    part.replace(dest)


def setup_windows() -> None:
    """Portable server binaries into .run/bin (no installers, no services, nothing outside the repo)."""
    dl = RUN / "dl"
    items = [
        (BIN / "pgsql" / "bin" / "pg_ctl.exe",
         f"https://get.enterprisedb.com/postgresql/postgresql-{PG_WIN_VER}-windows-x64-binaries.zip",
         lambda n: n.startswith("pgsql/") and not n.startswith(("pgsql/pgAdmin", "pgsql/symbols", "pgsql/StackBuilder",
                                                                "pgsql/doc")), None),
        (BIN / "nginx" / "nginx.exe", f"https://nginx.org/download/nginx-{NGINX_WIN_VER}.zip",
         lambda n: True, (f"nginx-{NGINX_WIN_VER}", "nginx")),
        (BIN / "nats-server.exe", f"https://github.com/nats-io/nats-server/releases/download/{NATS_VER}/"
                                  f"nats-server-{NATS_VER}-windows-amd64.zip",
         lambda n: n.endswith("/nats-server.exe"), "flat"),
    ]
    for target, url, keep, how in items:
        if target.exists():
            continue
        z = dl / url.rsplit("/", 1)[1]
        if not z.exists():
            _download(url, z)
        print(f"  extracting {z.name}")
        with zipfile.ZipFile(z) as zf:
            for m in zf.infolist():
                if m.is_dir() or not keep(m.filename):
                    continue
                if how == "flat":
                    out = BIN / Path(m.filename).name
                elif how:
                    out = BIN / m.filename.replace(how[0], how[1], 1)
                else:
                    out = BIN / m.filename
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    print("  server binaries ready in .run/bin (Postgres, nginx, NATS)")
    _pip_install(saas=True)


def _pip_install(saas: bool = False, site: bool = False) -> None:
    reqs = (["-e", str(ROOT / "schema")]
            + (["-e", str(ROOT / "saas" / "common")] + sum((["-r", str(ROOT / "saas" / x / "requirements.txt")]
                                                            for x in ("ingest", "control", "workers", "api")), [])
               if saas else [])
            + (["-r", str(ROOT / "collector" / "requirements.txt"), "-r", str(ROOT / "simulator" / "requirements.txt")]
               if site else []))
    r = subprocess.run([PY, "-m", "pip", "--version"], capture_output=True)
    if r.returncode:
        print("  (no pip in this Python - skipping dependency install; run `native.py install` if imports fail)")
        return
    sh(" ".join(f'"{x}"' for x in [PY, "-m", "pip", "install", "-q", *reqs]))


def cmd_install(a) -> None:
    """Create .venv (if missing) and install the Python packages a tier needs."""
    global PY
    venv = ROOT / ".venv"
    py = venv / ("Scripts/python.exe" if WIN else "bin/python")
    if not py.exists():
        sh(f'"{sys.executable}" -m venv "{venv}"')
    PY = str(py)
    sh(f'"{PY}" -m pip install -q --upgrade pip', check=False)
    _pip_install(saas=a.saas or not a.site, site=a.site or not a.saas)
    print(f"\nInstalled into {venv}. native.py uses it automatically.")


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


PGDATA = RUN / "pgdata"


def _pg(name: str) -> str:
    return str(BIN / "pgsql" / "bin" / f"{name}.exe")


def psql(sql: str, db: str = "postgres", check: bool = True) -> str:
    if WIN:  # portable cluster in .run/pgdata, superuser password = PG_OWNER_PASSWORD
        r = subprocess.run([_pg("psql"), "-h", "127.0.0.1", "-p", "5432", "-U", "postgres", "-v", "ON_ERROR_STOP=1",
                            "-qtA", "-d", db], input=sql.encode("utf-8"), capture_output=True,
                           env={**os.environ, "PGPASSWORD": env_file()["PG_OWNER_PASSWORD"], "PGCLIENTENCODING": "UTF8"})
        out, err = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    else:
        r = subprocess.run(["su", "postgres", "-c", f"psql -v ON_ERROR_STOP=1 -qtA -d {db}"], input=sql, text=True,
                           capture_output=True)
        out, err = r.stdout, r.stderr
    if check and r.returncode:
        raise SystemExit(f"psql failed: {err}")
    return out.strip()


def _start_postgres_windows(env: dict) -> None:
    if not Path(_pg("pg_ctl")).exists():
        sys.exit("Postgres binaries missing - run `python scripts/native.py saas --setup --lan` once")
    if not (PGDATA / "PG_VERSION").exists():
        print("  postgres: initialising a portable cluster in .run/pgdata")
        pw = RUN / "pg-superuser.tmp"
        pw.write_text(env["PG_OWNER_PASSWORD"], encoding="utf-8")
        try:
            subprocess.run([_pg("initdb"), "-D", str(PGDATA), "-U", "postgres", "-E", "UTF8", "--locale=C",
                            "-A", "scram-sha-256", f"--pwfile={pw}"], check=True, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL)
        finally:
            pw.unlink(missing_ok=True)
    if subprocess.run([_pg("pg_ctl"), "-D", str(PGDATA), "status"], capture_output=True).returncode != 0:
        # no pipes: the postmaster inherits pg_ctl's handles and would keep a captured pipe open forever
        with open(LOGS / "pg_ctl.log", "ab") as lf:
            subprocess.run([_pg("pg_ctl"), "-D", str(PGDATA), "-l", str(LOGS / "postgres.log"), "-w", "-t", "60",
                            "-o", "-c listen_addresses=127.0.0.1 -p 5432", "start"], check=True,
                           stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT)
    print("  postgres: running on 127.0.0.1:5432 (not reachable from the LAN)")


def start_postgres(env: dict) -> None:
    if WIN:
        _start_postgres_windows(env)
    else:
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


def nginx_conf(listen: str = "8443") -> Path:
    tpl = (ROOT / "deploy" / "nginx" / "templates" / "ingest.conf.template").read_text(encoding="utf-8")
    srv = (CERTS / "servers" / "nginx-ap").as_posix()
    run, logs = RUN.as_posix(), LOGS.as_posix()
    tpl = (tpl.replace("${TL_SERVER_NAME}", "nginx-ap bore.pub").replace("${TL_UPSTREAM}", "127.0.0.1:8001")
           .replace("${TL_REGION}", "ap-south").replace("/etc/nginx/certs/server/", f"{srv}/")
           .replace("/etc/nginx/certs/ca.crt", (CERTS / "ca.crt").as_posix()))
    tpl = re.sub(r"\n\s*resolver [^\n]*", "", tpl).replace("access_log /dev/stdout tl;", f"access_log {logs}/nginx-access.log tl;")
    tpl = tpl.replace("listen 8443 ssl", f"listen {listen} ssl")
    # Compatibility with nginx < 1.25.1 (e.g. Ubuntu 22.04 / Colab default nginx 1.18):
    # 'http2 on;' directive was added in 1.25.1; older versions require 'listen ... http2;'
    tpl = re.sub(r"\n\s*http2\s+on\s*;", "", tpl)
    tpl = re.sub(r"listen\s+(\d+)\s+ssl(?!\s+http2)\s*;", r"listen \1 ssl http2;", tpl)
    conf = RUN / "nginx.conf"
    tmp = f"{run}/nginx-tmp"
    conf.write_text(f"""worker_processes 1;
pid {run}/nginx.pid;
error_log {logs}/nginx-error.log warn;
events {{ worker_connections 512; }}
http {{
  client_body_temp_path {tmp}/body;
  proxy_temp_path {tmp}/proxy;
  fastcgi_temp_path {tmp}/fastcgi;
  uwsgi_temp_path {tmp}/uwsgi;
  scgi_temp_path {tmp}/scgi;
{tpl}
}}
""", encoding="utf-8")
    (RUN / "nginx-tmp").mkdir(parents=True, exist_ok=True)
    return conf


def nats_conf() -> Path:
    c = (ROOT / "deploy" / "nats" / "nats.conf").read_text(encoding="utf-8")
    c = c.replace('store_dir: "/data"', f'store_dir: "{(RUN / "nats").as_posix()}"')
    # natively NATS is only ever used on this machine: never expose it (or its monitor) to the network
    c = c.replace("port: 4222\nhttp_port: 8222", "host: 127.0.0.1\nport: 4222\nhttp: 127.0.0.1:8222")
    p = RUN / "nats.conf"
    p.write_text(c)
    return p


def tunnel(name: str, args: list[str], pattern: str, timeout: float = 40) -> tuple[str | None, subprocess.Popen]:
    log_file = LOGS / f"{name}.log"
    log_file.unlink(missing_ok=True)
    p = spawn(name, args, {}, RUN)
    rx = re.compile(pattern)
    t = time.time()
    while time.time() - t < timeout:
        if log_file.exists():
            txt = log_file.read_text(errors="replace")
            matches = rx.findall(txt)
            if matches:
                return matches[-1], p
        if p.poll() is not None:
            break
        time.sleep(0.5)
    return None, p


def _stop_named(names) -> None:
    for name in names:
        f = RUN / "pids" / f"{name}.pid"
        if f.exists():
            try:
                pid = int(f.read_text())
                if WIN:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                else:
                    os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass
            f.unlink(missing_ok=True)


def cmd_saas(a) -> None:
    if a.setup:
        setup_windows() if WIN else setup_linux(a.timescale)
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
        sh("killall -9 nginx nats-server bore cloudflared 2>/dev/null || true", check=False)
        sh("fuser -k 8443/tcp 8000/tcp 8001/tcp 8010/tcp 4222/tcp 2>/dev/null || true", check=False)
        time.sleep(0.5)
    elif WIN:  # leftovers from a previous run of this script only
        _stop_named(SAAS_PROCS)
        for im in ("nginx.exe", "nats-server.exe"):
            subprocess.run(["taskkill", "/IM", im, "/F"], capture_output=True)
        time.sleep(0.5)
        if not Path(exe("nats-server")).exists():
            sys.exit("server binaries missing - run `python scripts/native.py saas --setup --lan` once")
    start_postgres(env)
    procs: dict[str, subprocess.Popen] = {}
    procs["nats"] = spawn("nats", [exe("nats-server"), "-c", str(nats_conf()), "-js"], {
        k: env[k] for k in ("NATS_INGEST_PASSWORD", "NATS_WORKER_PASSWORD", "NATS_API_PASSWORD")}, RUN)
    wait_port(4222, 20)
    cfg = {"CONFIG_DIR": str(ROOT / "config"), "TENANTS_FILE": str(ROOT / "config" / "tenants.yaml"),
           "WARMUP_SECONDS": env.get("WARMUP_SECONDS", "300"), "STALE_AFTER_SECONDS": env.get("STALE_AFTER_SECONDS", "30"),
           "OFFLINE_TIMEOUT_SECONDS": env.get("OFFLINE_TIMEOUT_SECONDS", "45"), "BIND_HOST": "127.0.0.1"}
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
    public = "0.0.0.0" if (a.lan or not WIN) else "127.0.0.1"  # Windows: LAN-visible only with --lan
    procs["api"] = spawn("api", [PY, "-m", "uvicorn", "api.main:app", "--host", public, "--port", "8000",
                                 "--no-access-log"], {
        **cfg, "DB_AP_URL": pg.format(u="tl_api", p=env["PG_API_PASSWORD"]),
        "NATS_AP_URL": nats.format(u="api", p=env["NATS_API_PASSWORD"]), "JWT_SECRET": env["JWT_SECRET"],
        "USERS_FILE": str(ROOT / "config" / "users.yaml"), "SIMULATOR_URL": a.simulator_url,
        # AI Investigator: a key from the process env (Colab secret) or .env enables the Gemini / Claude engine
        # (a Colab Secret exported into the process env wins over the bundle's .env)
        **{k: os.environ.get(k) or env[k] for k in ("GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FALLBACK_MODELS",
                                                    "GEMINI_THINKING", "GEMINI_TIMEOUT_S", "ANTHROPIC_API_KEY",
                                                    "INVESTIGATOR_MODEL", "INVESTIGATOR_EFFORT", "INVESTIGATOR_ENGINE")
           if os.environ.get(k) or env.get(k)},
        **({"DASHBOARD_DIR": str(dash)} if dash.exists() else {})}, ROOT / "saas" / "api")
    wait_port(8000, 30)
    if WIN:  # nginx/Windows runs in the foreground: supervise it like the other processes
        conf = nginx_conf("8443" if a.lan else "127.0.0.1:8443")
        ngx = BIN / "nginx"
        procs["nginx"] = spawn("nginx", [str(ngx / "nginx.exe"), "-p", ngx.as_posix() + "/", "-c", conf.as_posix()], {}, ngx)
    else:
        conf = nginx_conf()
        sh(f"nginx -c {conf} -p {RUN} -s stop 2>/dev/null || true", check=False)
        sh(f"nginx -c {conf} -p {RUN}", check=True)
    if not wait_port(8443, 15):
        print("!! nginx did not open :8443 - see .run/logs/nginx-error.log")
    print("  nginx mTLS ingest on :8443")
    if a.lan:
        ip = lan_ip()
        print("\n" + "=" * 76, flush=True)
        print(f" LAN SaaS is up on {ip}   (only :8443 mTLS ingest and :8000 dashboard are exposed)", flush=True)
        print(f" Dashboard           http://{ip}:8000     (admin@acme.test / DEMO_PASSWORD from .env)", flush=True)
        print(f" mTLS ingest         {ip}:8443  (certificate name nginx-ap)", flush=True)
        print("\n On the SITE laptop (after unzipping dist/trustline-site-pack.zip into the repo):", flush=True)
        print(f"   python scripts/native.py site --lan {ip}", flush=True)
        print("\n Windows Firewall: click 'Allow' (Private networks) for python.exe and nginx.exe if asked.", flush=True)
        print("=" * 76, flush=True)
    if a.tunnel:
        hostport, p_bore = tunnel("bore", [str(RUN / "bin" / "bore"), "local", "8443", "--to", "bore.pub"],
                                  r"listening at (bore\.pub:\d+)")
        url, p_cf = tunnel("cloudflared", [str(RUN / "bin" / "cloudflared"), "tunnel", "--no-autoupdate", "--url",
                                           "http://127.0.0.1:8000"], r"(https://[a-z0-9-]+\.trycloudflare\.com)")
        if p_bore:
            procs["bore"] = p_bore
        if p_cf:
            procs["cloudflared"] = p_cf
        print("\n" + "=" * 72, flush=True)
        print(f" Dashboard (share this)   {url or 'cloudflared failed - see .run/logs/cloudflared.log'}", flush=True)
        print(f" mTLS ingest relay        {hostport or 'bore failed - see .run/logs/bore.log'}", flush=True)
        if hostport:
            print("\n On your laptop run:", flush=True)
            print(f"   python scripts/native.py site --ingest https://{hostport}", flush=True)
        print("=" * 72, flush=True)
    try:
        supervise(procs)
    finally:
        if WIN:
            subprocess.run(["taskkill", "/IM", "nginx.exe", "/F"], capture_output=True)
            if not a.keep_db:
                subprocess.run([_pg("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "stop"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
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


SITE_ENV_KEYS = ("HMAC_KEY_ACME", "WARMUP_SECONDS", "DEMO_PASSWORD", "ORGANISER_MODE", "ORGANISER_URL",
                 "ORGANISER_TOKEN", "WINDOW_SECONDS", "BATCH_INTERVAL_MS", "HEARTBEAT_SECONDS")


def cmd_site_pack(a) -> None:
    """Everything the SITE laptop needs that is not in git - and nothing it should not have.

    In:  the site's HMAC key, the collector client certs + the CA *certificate*, the built on-site UI
         and Simulation Lab.  Out: CA private key, server keys, JWT/DB/NATS secrets, users, API keys.
    """
    env = env_file()
    if not env.get("HMAC_KEY_ACME"):
        sys.exit("run `python scripts/tl.py init --local` first")
    for d in ("dist-local", "dist-sim"):
        if not any((ROOT / "dashboard" / d).glob("*.html")):
            print(f"WARNING: dashboard/{d} is not built - run `npm run build:local` / `npm run build:sim` in dashboard/")
    out = ROOT / "dist" / "trustline-site-pack.zip"
    out.parent.mkdir(exist_ok=True)
    files = [CERTS / "ca.crt", *(CERTS / "collectors").rglob("*"), *(CERTS / "rogue").rglob("*"),
             *(ROOT / "dashboard" / "dist-local").rglob("*"), *(ROOT / "dashboard" / "dist-sim").rglob("*")]
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(".env", "# TrustLine SITE secrets (from `native.py site-pack`). Never commit.\n"
                   + "".join(f"{k}={env[k]}\n" for k in SITE_ENV_KEYS if env.get(k)))
        for f in files:
            if f.is_file() and not f.name.endswith(".srl"):
                z.write(f, f.relative_to(ROOT).as_posix())
                n += 1
    print(f"wrote {out.relative_to(ROOT)} ({n + 1} files, {out.stat().st_size // 1024} KiB)")
    print("Give it to the site laptop (USB / chat). It holds the collector's private key - treat it like a password.")
    print("There, inside the cloned repo:  python -m zipfile -e trustline-site-pack.zip .")
    print(f"Then:                          python scripts/native.py site --lan {lan_ip()}")


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
    s.add_argument("--lan", metavar="SERVER_IP", help="LAN mode: the SaaS laptop's IP (ingest + dashboard on that host)")
    s.add_argument("--lab-host", default="127.0.0.1",
                   help="bind address of the Simulation Lab (0.0.0.0 lets the SaaS dashboard's demo buttons reach it)")
    s.add_argument("--saas", help="SaaS dashboard URL (https://xxxx.trycloudflare.com) for the Lab's SLA benchmarks")
    s.add_argument("--seconds", type=int, default=0, help="stop after N seconds (0 = run until Ctrl+C)")
    s.set_defaults(fn=cmd_site)
    s = sub.add_parser("saas", help="Colab/Linux/Windows: the regional SaaS stack")
    s.add_argument("--setup", action="store_true",
                   help="install first (Linux: apt+pip; Windows: portable Postgres/nginx/NATS into .run/bin)")
    s.add_argument("--lan", action="store_true", help="serve the dashboard (:8000) and mTLS ingest (:8443) to the LAN")
    s.add_argument("--keep-db", action="store_true", help="Windows: leave Postgres running on exit")
    s.add_argument("--tunnel", action="store_true", help="open bore (mTLS ingest) + cloudflared (dashboard) tunnels")
    s.add_argument("--timescale", action="store_true", help="try installing TimescaleDB (optional)")
    s.add_argument("--simulator-url", default="http://127.0.0.1:8090",
                   help="demo controls in the dashboard call this (not reachable from Colab by default)")
    s.set_defaults(fn=cmd_saas)
    s = sub.add_parser("site-pack", help="zip the site laptop's certs + HMAC key + built UIs (LAN mode)")
    s.set_defaults(fn=cmd_site_pack)
    s = sub.add_parser("install", help="create .venv and install Python deps (--site, --saas, default both)")
    s.add_argument("--site", action="store_true")
    s.add_argument("--saas", action="store_true")
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("bundle", help="zip the repo + secrets + certs for Colab upload")
    s.add_argument("--no-build-dashboard", dest="build_dashboard", action="store_false")
    s.set_defaults(fn=cmd_bundle)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
