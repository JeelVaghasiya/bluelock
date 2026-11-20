"""TrustLine collector - one asyncio process, seven stages joined by bounded queues.

  sources -> [1 collect] -> q -> [2 filter] -> q -> [3 redact] -> q -> [4 aggregate] -> q
          -> [5 analyse] -> q -> [6 policy] -> [7 transmit] -> outbox -> mTLS -> SaaS

Raw data enters at stage 1 and never reaches stage 7: stage 7 only accepts a validated
`Payload`, and a `Payload` cannot hold a field outside the trustline.v1 allow-list.
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from . import enrol, localui, settings, tasks
from .models import Counters
from .redact.pseudonym import Pseudonymizer
from .sources import http_push, syslog_udp
from .sources.organiser import OrganiserSource
from .stages.s1_collect import Collect
from .stages.s2_filter import Filter
from .stages.s3_redact import Redact
from .stages.s4_aggregate import Aggregate, WindowAggregator
from .stages.s5_analyse import Analyse, FastPath, SecurityL0
from .stages.s6_policy import Policy
from .stages.s7_transmit import Transmit
from .stores import open_stores

QMAX = 10_000


async def serve(app, port: int, host: str | None = None) -> None:
    host = host or os.environ.get("BIND_HOST", "0.0.0.0")  # native.py: 127.0.0.1 (raw UI never on the LAN)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False))
    await server.serve()


async def amain() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("collector")
    s = settings.load()
    await enrol.ensure_certificate(s)
    stores = open_stores(s.data_dir)
    c = Counters()

    topo_kinds = {"host": "h", "link": "link"}
    known = {n["name"]: topo_kinds[n.get("kind")] for n in (s.topology.get("nodes") or [])
             if n.get("kind") in topo_kinds}
    pseudo = Pseudonymizer(s.hmac_key, known, on_new=stores.state.remember_code)
    pseudo.reverse.update(stores.state.load_codes())

    q = {name: asyncio.Queue(maxsize=QMAX) for name in ("collect", "parsed", "clean", "agg", "records")}
    sec = s.cfg.get("security") or {}
    red = s.cfg.get("redaction") or {}
    snd = s.cfg.get("sender") or {}

    collect = Collect(stores.raw, q["collect"], c)
    filt = Filter(q["collect"], q["parsed"], c, s.src_tz)
    redact = Redact(q["parsed"], q["clean"], c, pseudo, stores.raw,
                    entropy_threshold=float(red.get("entropy_threshold", 4.0)),
                    use_ner=True)  # heuristic name-spotter always; Presidio when WITH_NER=1
    agg = Aggregate(q["clean"], q["agg"], WindowAggregator(s.src_tz, s.window_s, float(s.cfg.get("grace_s", 0.5)),
                                                           float(s.cfg.get("late_horizon_s", 120)), c))
    analyse = Analyse(q["agg"], q["records"], c,
                      FastPath(s.threshold_for, int((s.cfg.get("fast_path") or {}).get("consecutive_samples", 3))),
                      SecurityL0(int(sec.get("window_s", 60)), int(sec.get("brute_force_fails", 10)),
                                 int(sec.get("spray_distinct_users", 5)), int(sec.get("success_after_fails", 5)),
                                 float(sec.get("login_flood_rate", 100))),
                      s.src_tz)
    tx = Transmit(stores, c, ingest_base=s.ingest_base, ca_file=str(s.ca_file), cert=s.client_cert,
                  timeout_s=float(snd.get("timeout_s", 5)), backoff_max_s=float(snd.get("backoff_max_s", 10)),
                  backlog_rate=float(snd.get("backlog_rate_per_s", 50)),
                  backlog_inflight=int(snd.get("backlog_inflight", 4)))
    policy = Policy(q["records"], tx, stores, c, collector_id=s.collector_id, region=s.region, batch_ms=s.batch_ms)

    organiser = OrganiserSource(s.organiser, s.site, collect.accept)
    action_log: list = []
    ingress = http_push.build_app(collect.accept, organiser)
    ui = localui.build_app(s, stores, c, tx, policy, collect, pseudo, organiser.stats, action_log, q)
    await syslog_udp.start(s.syslog_port, collect.accept)

    log.info("collector %s site=%s region=%s -> %s (window %ss, batch %sms)", s.collector_id, s.site, s.region,
             s.ingest_base, s.window_s, s.batch_ms)
    await asyncio.gather(
        collect.flusher(), filt.run(), redact.run(), redact.span_flusher(), agg.run(), analyse.run(), policy.run(),
        tx.run(), organiser.run(),
        tasks.heartbeat(q["records"], s, stores, c, tx), tasks.topology(q["records"], s, pseudo),
        tasks.actions(q["records"], s, pseudo, stores, action_log), tasks.purge(s, stores),
        serve(ingress, s.push_port), serve(ui, s.ui_port),
    )


def main() -> None:
    try:
        import uvloop  # type: ignore

        uvloop.install()
    except ImportError:
        pass
    asyncio.run(amain())


if __name__ == "__main__":
    main()
