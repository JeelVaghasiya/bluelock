#!/usr/bin/env python3
"""TrustLine operator CLI - stdlib only, works on Windows, macOS and Linux.

  python scripts/tl.py doctor            check docker, ports, RAM, .env, certs
  python scripts/tl.py init              create .env (random secrets), users.yaml, certs
  python scripts/tl.py certs [--local]   (re)generate the demo PKI
  python scripts/tl.py up [--full] [--build]
  python scripts/tl.py down [-v]
  python scripts/tl.py ps | logs <svc>
  python scripts/tl.py test unit|system
  python scripts/tl.py demo <scenario> [--site acme]
  python scripts/tl.py outage [--svc ingest-ap] [--seconds 60]
  python scripts/tl.py score
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
CERTS = ROOT / "deploy" / "certs" / "out"
USERS_EXAMPLE = ROOT / "config" / "users.example.yaml"
USERS = ROOT / "config" / "users.yaml"
SIM = "http://127.0.0.1:8090"
PORTS = [3000, 8000, 8090, 8443, 8543, 9090, 9091]
FULL_SIM = {
    "SIM_SITES": "acme=http://collector-acme:8081,globex=http://collector-globex:8081",
    "SIM_SYSLOG": "acme=collector-acme:5514,globex=collector-globex:5514",
}
SCENARIOS = {
    # name: (simulator kind, default target)
    "secret": ("secret_in_log", None),
    "spray": ("login_spray", None),
    "brute": ("brute_force", None),
    "success-after-fail": ("success_after_failures", None),
    "latency": ("latency_ramp", "uplink-1"),
    "loss": ("packet_loss", "uplink-1"),
    "link-down": ("link_down", "uplink-1"),
    "memleak": ("mem_leak", "db-01"),
    "cpu": ("cpu_spike", "api-01"),
    "disk": ("disk_fill", "db-01"),
    "errors": ("service_errors", "api"),
    "dupes": ("dup_burst", None),
    "ooo": ("out_of_order", None),
    "skew": ("clock_skew", None),
}


# ------------------------------------------------------------------ helpers
def run(cmd: list[str], check: bool = True, env: dict | None = None) -> int:
    print("+", " ".join(cmd))
    e = os.environ.copy()
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=ROOT, env=e)
    if check and r.returncode != 0:
        sys.exit(r.returncode)
    return r.returncode


def compose(*args: str, env: dict | None = None, check: bool = True) -> int:
    return run(["docker", "compose", *args], check=check, env=env)


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def scrypt_hash(password: str) -> str:
    """Format shared with saas/api/api/auth.py: scrypt$n$r$p$salt$hash (urlsafe b64)."""
    n, r, p = 2**14, 8, 1
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    b = lambda x: base64.urlsafe_b64encode(x).decode().rstrip("=")  # noqa: E731
    return f"scrypt${n}${r}${p}${b(salt)}${b(h)}"


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode(errors="replace")}
    except urllib.error.URLError as e:
        return {"error": f"cannot reach {url}: {e.reason} (is the stack up?)"}


# ------------------------------------------------------------------ commands
def cmd_doctor(_a) -> None:
    ok = True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'OK' if good else '!!'}] {label}" + ("" if good else f"  -> {hint}"))

    print("TrustLine doctor")
    docker = shutil.which("docker")
    check("docker on PATH", bool(docker), "install Docker Desktop (see README 'Setup')")
    if docker:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        check("docker compose v2", r.returncode == 0, "update Docker Desktop")
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        check("docker daemon running", r.returncode == 0, "start Docker Desktop")
    for port in PORTS:
        with socket.socket() as s:
            s.settimeout(0.3)
            busy = s.connect_ex(("127.0.0.1", port)) == 0
        print(f"  [{'..' if busy else 'OK'}] port {port} " +
              ("in use (fine if TrustLine is already running)" if busy else "free"))
    check(".env exists", ENV.exists(), "python scripts/tl.py init")
    check("users.yaml exists", USERS.exists(), "python scripts/tl.py init")
    check("certs exist", (CERTS / "ca.crt").exists(), "python scripts/tl.py certs")
    if ENV.exists():
        check("no change-me left in .env", "change-me" not in ENV.read_text(), "python scripts/tl.py init --force")
    print("all good" if ok else "fix the items marked !!")


def cmd_init(a) -> None:
    if ENV.exists() and not a.force:
        print(".env exists (use --force to regenerate secrets)")
    else:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        text = re.sub(r"change-me-hex", lambda _m: secrets.token_hex(32), text)
        text = re.sub(r"change-me", lambda _m: secrets.token_hex(24), text)
        ENV.write_text(text, encoding="utf-8")
        print("wrote .env with fresh secrets")
    env = read_env()
    pw = env.get("DEMO_PASSWORD", "trustline-demo")
    lines = []
    for line in USERS_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if 'scrypt: ""' in line:
            line = line.replace('scrypt: ""', f'scrypt: "{scrypt_hash(pw)}"')
        lines.append(line)
    USERS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote config/users.yaml (all demo users share DEMO_PASSWORD={pw!r})")
    cmd_certs(argparse.Namespace(local=a.local, force=a.force))


def cmd_certs(a) -> None:
    if a.local or not shutil.which("docker"):
        sh = shutil.which("sh") or r"C:\Program Files\Git\bin\sh.exe"
        run([sh, "deploy/certs/gen.sh"], env={"FORCE": "1" if a.force else "0", "MSYS_NO_PATHCONV": "1"})
    else:
        compose("--profile", "tools", "run", "--rm", "certgen", env={"FORCE": "1" if a.force else "0"})


def _profile_env(full: bool) -> dict:
    env = {"COMPOSE_PROFILES": "eu" if full else read_env().get("COMPOSE_PROFILES", "")}
    if env["COMPOSE_PROFILES"] == "eu":
        env.update(FULL_SIM)
    return env


def cmd_up(a) -> None:
    if not ENV.exists():
        cmd_init(argparse.Namespace(force=False, local=False))
    args = ["up", "-d"] + (["--build"] if a.build else [])
    compose(*args, env=_profile_env(a.full))
    print("\nTrustLine is starting:")
    print("  SaaS dashboard      http://localhost:3000   (admin@acme.test / DEMO_PASSWORD)")
    print("  Collector local UI  http://localhost:9090   (on-site only)")
    print("  Simulator API       http://localhost:8090/scenarios")
    print("Baselines warm up for WARMUP_SECONDS (default 5 min) before anomaly alerts fire.")


def cmd_down(a) -> None:
    compose("--profile", "eu", "down", *(["-v"] if a.volumes else []))


def cmd_ps(_a) -> None:
    compose("--profile", "eu", "ps")


def cmd_logs(a) -> None:
    compose("logs", "-f", "--tail", "200", a.service)


def cmd_test(a) -> None:
    if a.kind == "unit":
        py = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
        run([str(py) if py.exists() else sys.executable, "-m", "pytest", "-q",
             "schema", "collector/tests", "saas/workers/tests", "simulator/tests"])
    else:
        compose("--profile", "tools", "run", "--rm", "tester")


def cmd_demo(a) -> None:
    name = a.scenario
    if name == "clear":
        print(post(f"{SIM}/clear", {"site": a.site}))
        return
    if name in ("leak-field",):
        port = 9090 if a.site == "acme" else 9091
        print(post(f"http://127.0.0.1:{port}/local/debug/leak-field", {}))
        return
    if name in ("wrong-region", "rogue", "spoof", "isolation"):
        run([sys.executable, str(ROOT / "scripts" / "demo" / f"{name.replace('-', '_')}.py")], check=False)
        return
    if name not in SCENARIOS:
        sys.exit(f"unknown scenario {name!r}; choose from: {', '.join(sorted(SCENARIOS))}, "
                 "leak-field, wrong-region, rogue, spoof, isolation, clear")
    kind, target = SCENARIOS[name]
    body = {"site": a.site, "kind": kind}
    if a.target or target:
        body["target"] = a.target or target
    if a.magnitude:
        body["magnitude"] = a.magnitude
    if a.duration:
        body["duration_s"] = a.duration
    print(post(f"{SIM}/inject", body))


def cmd_outage(a) -> None:
    print(f"stopping {a.svc} for {a.seconds}s - watch the collector local UI buffer grow")
    compose("stop", a.svc)
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        left = int(a.seconds - (time.time() - t0))
        print(f"\r  outage... {left:3d}s left", end="", flush=True)
        time.sleep(1)
    print()
    compose("start", a.svc)
    print("restarted - backlog drains oldest-first; check Trust Center > Delivery Ledger")


def cmd_score(_a) -> None:
    run([sys.executable, str(ROOT / "scripts" / "score.py")], check=False)


def main() -> None:
    p = argparse.ArgumentParser(prog="tl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("init")
    s.add_argument("--force", action="store_true")
    s.add_argument("--local", action="store_true", help="generate certs with local openssl (Git Bash)")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("certs")
    s.add_argument("--local", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_certs)
    s = sub.add_parser("up")
    s.add_argument("--full", action="store_true", help="both regions (needs ~3 GB for Docker)")
    s.add_argument("--build", action="store_true")
    s.set_defaults(fn=cmd_up)
    s = sub.add_parser("down")
    s.add_argument("-v", "--volumes", action="store_true", help="also delete data volumes")
    s.set_defaults(fn=cmd_down)
    sub.add_parser("ps").set_defaults(fn=cmd_ps)
    s = sub.add_parser("logs")
    s.add_argument("service")
    s.set_defaults(fn=cmd_logs)
    s = sub.add_parser("test")
    s.add_argument("kind", choices=["unit", "system"])
    s.set_defaults(fn=cmd_test)
    s = sub.add_parser("demo")
    s.add_argument("scenario")
    s.add_argument("--site", default="acme")
    s.add_argument("--target")
    s.add_argument("--magnitude", type=float)
    s.add_argument("--duration", type=int)
    s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("outage")
    s.add_argument("--svc", default="ingest-ap")
    s.add_argument("--seconds", type=int, default=60)
    s.set_defaults(fn=cmd_outage)
    sub.add_parser("score").set_defaults(fn=cmd_score)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
