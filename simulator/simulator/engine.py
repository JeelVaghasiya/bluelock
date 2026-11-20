"""Per-site runtime: produces one second of telemetry per tick."""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np

from .faults import FaultBook
from .logs import app_log, secret_log, sshd_line
from .signals import HostState, LinkState, ServiceState
from .sites import Site


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def tz_hours(tz: str) -> float:
    sign = -1 if tz.startswith("-") else 1
    h, m = tz[1:].split(":")
    return sign * (int(h) + int(m) / 60)


class SiteRuntime:
    def __init__(self, site: Site, seed: int):
        self.site = site
        self.rng = np.random.default_rng(seed)
        self.tz_h = tz_hours(site.tz)
        self.hosts = {h: HostState(role=r, rng=self.rng, base_cpu=30 + 10 * self.rng.random())
                      for h, r in site.hosts.items()}
        self.links = {link: LinkState(rng=self.rng) for link in site.links}
        self.services = {s: ServiceState(rng=self.rng) for s in site.services}
        self.book = FaultBook()
        self.pending_secret: list[str | None] = []
        self.last_records: list[dict] = []
        self.held_back: list[dict] = []
        self.pid = 1000
        self.spray_idx = 0
        self.saf_count = 0

    # ---------------------------------------------------------------- helpers
    def _rec(self, ts: float, **kw) -> dict:
        return {"ts": iso(ts), "eid": str(uuid.uuid4()), "site": self.site.name, **kw}

    def _syslog_ts(self, ts: float) -> str:
        local = datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(hours=self.tz_h)
        return f"{local:%b} {local.day:2d} {local:%H:%M:%S}"  # RFC 3164: day space-padded

    # ---------------------------------------------------------------- tick
    def tick(self, now: float) -> tuple[list[dict], list[str]]:
        book = self.book
        book.prune(now)
        skew = sum(f.magnitude for f in book.of("clock_skew", now))
        ts = now + skew
        recs: list[dict] = []
        sys_lines: list[str] = []

        # ---- hosts
        for host, st in self.hosts.items():
            mods = {
                "cpu_spike": sum(f.magnitude for f in book.of("cpu_spike", now) if f.target == host),
                "mem_leak": sum(f.magnitude for f in book.of("mem_leak", now) if f.target == host),
                "disk_fill": sum(f.magnitude for f in book.of("disk_fill", now) if f.target == host),
            }
            vals = st.step(now, self.tz_h, mods)
            for name, unit in (("cpu", "%"), ("mem", "%"), ("disk", "%"), ("net_in", "B/s"),
                               ("net_out", "B/s"), ("net_util", "%")):
                recs.append(self._rec(ts, kind="metric", host=host, name=name,
                                      value=round(vals[name], 3), unit=unit))

        # ---- links
        link_rtt: dict[str, float] = {}
        svc_err_extra: dict[str, float] = {s: 0.0 for s in self.services}
        for link, st in self.links.items():
            mods: dict = {}
            for f in book.of("latency_ramp", now):
                if f.target == link:
                    e = f.elapsed(now)
                    mods["rtt_mult"] = 1 + (f.magnitude - 1) * min(1.0, e / 60)
                    if e > 75 and link not in book.failed_over:
                        for svc, via in self.site.service_links.items():
                            svc_err_extra[svc] += 0.09 * min(1.0, (e - 75) / 30)
            for f in book.of("packet_loss", now):
                if f.target == link:
                    mods["loss"] = f.magnitude
            for f in book.of("link_down", now):
                if f.target == link:
                    mods["down"] = True
                    if link not in book.failed_over:
                        for svc, via in self.site.service_links.items():
                            if via == link:
                                svc_err_extra[svc] += 0.3
            p = st.step(mods)
            link_rtt[link] = p["rtt_ms"]
            recs.append(self._rec(ts, kind="probe", link=link, target=f"probe.{link}", **p))

        # ---- services
        for svc, st in self.services.items():
            for f in book.of("service_errors", now):
                if f.target == svc:
                    svc_err_extra[svc] += f.magnitude
            via = self.site.service_links.get(svc)
            if via in book.failed_over:
                via = "uplink-2"
            c = st.step({"error_rate": svc_err_extra[svc]}, link_rtt.get(via) if via else None)
            recs.append(self._rec(ts, kind="check", service=svc, ok=c["ok"],
                                  latency_ms=c["latency_ms"], status=c["status"]))
            recs.append(self._rec(ts, kind="metric", service=svc, name="error_rate",
                                  value=c["error_rate"], unit="ratio"))

        # ---- application logs (2-4 per second)
        for _ in range(int(self.rng.integers(2, 5))):
            host = list(self.hosts)[int(self.rng.integers(0, len(self.hosts)))]
            app = self.site.hosts[host]
            line = app_log(self.rng, self.site, host, app)
            if line:
                recs.append(self._rec(ts, kind="log", host=host, app=app, **line))
        while self.pending_secret:
            which = self.pending_secret.pop()
            _, line = secret_log(self.rng, self.site, which)
            recs.append(self._rec(ts, kind="log", host="api-01" if "api-01" in self.hosts else list(self.hosts)[0],
                                  app="api", **line))

        # ---- sshd (syslog UDP)
        sys_lines.extend(self._sshd(ts, now))

        # ---- delivery pathologies
        out = recs
        if book.of("out_of_order", now):
            random.shuffle(out)
            keep, hold = [], []
            for r in out:
                (hold if self.rng.random() < 0.3 else keep).append(r)
            for r in hold:  # stamp them 2-8 s in the past, deliver next tick
                past = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) - timedelta(seconds=float(self.rng.uniform(2, 8)))
                r["ts"] = past.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            out = keep + self.held_back
            self.held_back = hold
        elif self.held_back:
            out = out + self.held_back
            self.held_back = []
        dups = [f for f in self.book.faults if f.kind == "dup_burst"]
        if dups:
            for f in dups:
                for _ in range(int(f.magnitude)):
                    out = out + [dict(r) for r in self.last_records]
            self.book.clear("dup_burst")
        self.last_records = recs
        return out, sys_lines

    def _sshd(self, ts: float, now: float) -> list[str]:
        out: list[str] = []
        stamp = self._syslog_ts(ts)
        site = self.site
        gw = "gw-01"

        def nxt() -> int:
            self.pid += 1
            return self.pid

        # background: a legit login now and then, rare fat-fingered failure
        if self.rng.random() < 0.03:
            out.append(sshd_line(stamp, gw, "ok", "deploy", site.internal_ips[0], nxt()))
        if self.rng.random() < 0.01:
            out.append(sshd_line(stamp, gw, "fail", site.users[1].split("@")[0], site.internal_ips[1], nxt()))

        attacker = site.attacker_ips[0]
        if attacker in self.book.blocked_ips:
            return out
        for f in self.book.of("login_spray", now):
            for _ in range(max(1, int(f.magnitude))):
                user = site.users[self.spray_idx % len(site.users)].split("@")[0]
                self.spray_idx += 1
                out.append(sshd_line(stamp, gw, "invalid" if self.spray_idx % 3 == 0 else "fail", user, attacker, nxt()))
        for f in self.book.of("brute_force", now):
            for _ in range(max(1, int(f.magnitude))):
                out.append(sshd_line(stamp, gw, "fail", "root", site.attacker_ips[1], nxt()))
        for f in self.book.of("success_after_failures", now):
            self.saf_count += 1
            user = site.users[2].split("@")[0]
            kind = "ok" if self.saf_count == 9 else ("fail" if self.saf_count < 9 else None)
            if kind:
                out.append(sshd_line(stamp, gw, kind, user, site.attacker_ips[2], nxt()))
        if not self.book.of("success_after_failures", now):
            self.saf_count = 0
        return out

    # ---------------------------------------------------------------- remediation hooks
    def action(self, key: str, target: str | None) -> dict:
        b = self.book
        if key == "failover_uplink":
            link = target or "uplink-1"
            b.failed_over.add(link)
            n = sum(b.clear(k, link) for k in ("latency_ramp", "packet_loss", "link_down"))
            return {"ok": True, "detail": f"traffic moved off {link}; {n} link fault(s) mitigated"}
        if key == "failback_uplink":
            b.failed_over.discard(target or "uplink-1")
            return {"ok": True, "detail": "traffic restored to primary uplink"}
        if key == "restart_service":
            hosts = self.site.services.get(target or "", [target]) if target else list(self.hosts)
            n = b.clear("service_errors", target)
            for h in hosts:
                n += b.clear("mem_leak", h) + b.clear("cpu_spike", h)
                if h in self.hosts:
                    self.hosts[h].mem = 48.0 if self.hosts[h].role != "db" else 60.0
            return {"ok": True, "detail": f"restarted {target}; {n} fault(s) cleared"}
        if key in ("scale_out",):
            n = b.clear("cpu_spike", target)
            return {"ok": True, "detail": f"load shifted from {target}; {n} fault(s) cleared"}
        if key == "scale_in":
            return {"ok": True, "detail": "capacity returned to baseline"}
        if key == "rotate_logs":
            n = b.clear("disk_fill", target)
            if target in self.hosts:
                self.hosts[target].disk = 52.0
            return {"ok": True, "detail": f"logs rotated on {target}; {n} fault(s) cleared"}
        if key == "block_source_ip":
            b.blocked_ips.update(self.site.attacker_ips if not target else [target])
            n = sum(b.clear(k) for k in ("login_spray", "brute_force", "success_after_failures"))
            return {"ok": True, "detail": f"blocked attacker source(s); {n} attack(s) stopped"}
        if key == "unblock_source_ip":
            b.blocked_ips.clear()
            return {"ok": True, "detail": "block removed"}
        return {"ok": False, "detail": f"unknown action {key}"}
