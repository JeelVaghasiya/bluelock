"""Site inventories. Real names live only here and in Tier 1 config."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Site:
    name: str
    tz: str
    hosts: dict[str, str]  # host -> role
    services: dict[str, list[str]]  # service -> hosts it runs on
    links: list[str]
    service_links: dict[str, str]  # service -> uplink it routes via
    users: list[str]
    internal_ips: list[str]
    attacker_ips: list[str] = field(default_factory=lambda: ["203.0.113.7", "198.51.100.23", "192.0.2.44"])
    dynamic: bool = False  # load drives link RTT, service latency and error rate; per-host phase and swing


SITES: dict[str, Site] = {
    "acme": Site(
        name="acme",
        tz="+05:30",
        hosts={"web-01": "web", "web-02": "web", "api-01": "api", "db-01": "db", "gw-01": "gateway"},
        services={"web": ["web-01", "web-02"], "api": ["api-01"], "db": ["db-01"]},
        links=["uplink-1", "uplink-2"],
        service_links={"web": "uplink-1", "api": "uplink-1"},
        users=["alice@acme.test", "bob@acme.test", "carol@acme.test", "deploy", "root", "admin",
               "dave@acme.test", "erin@acme.test", "frank@acme.test", "grace@acme.test",
               "heidi@acme.test", "ivan@acme.test", "judy@acme.test", "mallory@acme.test",
               "oscar@acme.test", "peggy@acme.test", "trent@acme.test", "victor@acme.test"],
        internal_ips=["10.0.1.11", "10.0.1.12", "10.0.1.23", "10.0.2.5", "10.0.2.9"],
    ),
    "globex": Site(
        name="globex",
        tz="+01:00",
        hosts={"web-01": "web", "api-01": "api", "gw-01": "gateway"},
        services={"web": ["web-01"], "api": ["api-01"]},
        links=["uplink-1", "uplink-2"],
        service_links={"web": "uplink-1", "api": "uplink-1"},
        users=["hank@globex.test", "lena@globex.test", "omar@globex.test", "root", "admin",
               "ops@globex.test", "sara@globex.test", "tom@globex.test", "uma@globex.test"],
        internal_ips=["10.8.0.11", "10.8.0.12", "10.8.1.4"],
    ),
}


def _large_acme() -> Site:
    """`SIM_PROFILE=large`: a 24-host, 7-service, 4-link site. Every name of the standard site is kept
    (web-01, api-01, db-01, gw-01, uplink-1, ...) so all scenarios and stories still target the same things.
    Must stay in step with config/sites/acme-large/topology.yaml (test_lab checks it)."""
    hosts: dict[str, str] = {}
    services: dict[str, list[str]] = {}
    for svc, role, n in (("web", "web", 6), ("api", "api", 4), ("db", "db", 2), ("cache", "cache", 2),
                         ("auth", "auth", 2), ("search", "search", 2), ("queue", "queue", 1)):
        names = [f"{role}-{i:02d}" for i in range(1, n + 1)]
        hosts.update({h: role for h in names})
        services[svc] = names
    workers = [f"worker-{i:02d}" for i in range(1, 4)]
    hosts.update({h: "worker" for h in workers})
    services["queue"] += workers
    hosts.update({"gw-01": "gateway", "gw-02": "gateway"})
    base = SITES["acme"]
    return Site(name="acme", tz=base.tz, hosts=hosts, services=services,
                links=["uplink-1", "uplink-2", "wan-dc2", "wan-cdn"],
                # web + api behind uplink-1 exactly as in the standard site (same blast radius for failover)
                service_links={"web": "uplink-1", "api": "uplink-1", "auth": "wan-dc2", "queue": "wan-dc2",
                               "search": "wan-cdn"},
                users=base.users, internal_ips=base.internal_ips + ["10.0.3.7", "10.0.3.8", "10.0.4.21"],
                dynamic=True)


PROFILES = {"standard": lambda name: SITES[name], "large": lambda name: _large_acme() if name == "acme" else SITES[name]}
