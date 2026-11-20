"""AI Investigator: tools are read-only and grounded, the deterministic engine cites real numbers,
the Gemini and Claude loops handle tool calls -> submit_diagnosis, and only the playbook's action is queued."""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "saas" / "api"), str(ROOT / "saas" / "common")]
os.environ.setdefault("CONFIG_DIR", str(ROOT / "config"))

from api import investigator as inv  # noqa: E402

EV = json.loads((ROOT / "schema" / "fixtures" / "incident_evidence.json").read_text())
EV["action"] = {"key": "failover_uplink", "executable": True, "reversible": True, "rollback_key": "failback_uplink"}
ROOTSRC, SVC, HOST = "link:4714195e0a1a", "svc:1d3a59c03421", "h:9e187529af08"


class FakeConn:
    """Answers the handful of SELECTs the tools issue; anything else is a test failure."""

    async def fetchrow(self, sql, *a):
        if "FROM incidents" in sql:
            return None if a[0] != "inc-0412" else {
                "incident_id": "inc-0412", "status": "open", "priority": "P2", "severity": "poor",
                "title": "Upstream link degradation", "root_cause_src": ROOTSRC, "affected": [SVC, HOST],
                "evidence": EV, "alert_count": 5, "breach_ts": None, "opened_ts": None}
        if "FROM topology" in sql:
            return {"nodes": [{"src": s, "kind": k, "role": k} for s, k in ((ROOTSRC, "link"), (SVC, "service"), (HOST, "host"))],
                    "edges": [{"dependent": SVC, "dependency": ROOTSRC}, {"dependent": HOST, "dependency": SVC}]}
        raise AssertionError(sql)

    async def fetch(self, sql, *a):
        assert sql.lstrip().upper().startswith("SELECT"), sql
        return []

    async def fetchval(self, sql, *a):
        assert "FROM incidents" in sql
        return EV


def run(coro):
    return asyncio.run(coro)


def test_tools_are_grounded():
    t = inv.Tools(FakeConn())
    ctx = run(t.get_incident_context("inc-0412"))
    assert ctx["confidence"] == 0.87 and ctx["signals"][0]["z"] == 7.4
    assert "error" in run(t.get_incident_context("inc-nope"))
    topo = run(t.query_topology(ROOTSRC))
    assert [d["src"] for d in topo["dependents"]] == [SVC]
    assert [d["src"] for d in topo["dependents_2_hops"]] == [HOST]
    assert run(t.fetch_playbook_rule("upstream_link_degradation"))["action"]["rollback_key"] == "failback_uplink"


def test_deterministic_engine(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    queued = []

    async def enqueue(iid, by):
        queued.append((iid, by))
        return {"action_id": "a1", "state": "proposed"}

    out = run(inv.investigate(FakeConn(), "acme", "inc-0412", enqueue))
    d = out["diagnosis"]
    assert out["engine"] == "deterministic" and out["requires_approval"]
    assert d["root_cause"] == ROOTSRC and d["citations"][0]["z"] == 7.4 and "7.4" in d["summary"]
    assert d["recommended_action"] == "failover_uplink" and "failback_uplink" in d["rollback_plan"]
    assert queued == [("inc-0412", "ai-investigator")]


def _block(**kw):
    return NS(**kw)


def test_claude_loop_and_policy_bound(monkeypatch):
    """Model calls a tool, then submits a diagnosis recommending an action that is NOT the playbook's:
    the loop must feed the tool result back and the policy bound must refuse to queue it."""
    calls = []
    script = [
        [_block(type="tool_use", id="t1", name="get_incident_context", input={"incident_id": "inc-0412"})],
        [_block(type="text", text="Link is the origin."),
         _block(type="tool_use", id="t2", name="submit_diagnosis", input={
             "summary": "s", "root_cause": ROOTSRC, "root_cause_reasoning": "r", "citations": [],
             "layers_agreeing": ["L1"], "confidence": 0.87, "recommended_action": "restart_service",
             "action_rationale": "x", "rollback_plan": "none", "risk": "low", "caveats": []})],
    ]

    class Msgs:
        async def create(self, **kw):
            calls.append(kw)
            assert kw["model"] == inv.MODEL and kw["betas"] == ["server-side-fallback-2026-07-01"]
            assert all(t.get("strict") for t in kw["tools"])
            content = script[len(calls) - 1]
            return NS(content=content, stop_reason="tool_use", stop_details=None, model=kw["model"],
                      usage=NS(input_tokens=10, output_tokens=5), _request_id="req_x")

    anthropic = pytest.importorskip("anthropic")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: NS(beta=NS(messages=Msgs())))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    async def enqueue(iid, by):
        raise AssertionError("must not queue a non-playbook action")

    out = run(inv.investigate(FakeConn(), "acme", "inc-0412", enqueue))
    assert out["engine"] == "claude" and out["queued"] is None
    assert "not the playbook action" in out["policy_note"] and out["diagnosis"]["recommended_action"] == "investigate"
    tr = calls[1]["messages"][2]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "t1" and "7.4" in tr["content"]
    assert [t["tool"] for t in out["trace"] if t["step"] == "tool"] == ["get_incident_context"]


def test_gemini_loop_queues_playbook_action(monkeypatch):
    """Gemini calls a tool (with a thought signature), then submits the playbook's own action:
    the function response must go back with the signature intact and the action must be queued."""
    import httpx

    bodies = []
    script = [
        [{"functionCall": {"name": "get_incident_context", "args": {"incident_id": "inc-0412"}}, "thoughtSignature": "sig1"}],
        [{"functionCall": {"name": "submit_diagnosis", "args": {
            "summary": "s", "root_cause": ROOTSRC, "root_cause_reasoning": "r",
            "citations": [{"src": ROOTSRC, "metric": "loss_pct", "value": 6.2, "baseline": 0.1, "z": 7.4, "weight": 0.61, "role": "root_cause"}],
            "layers_agreeing": ["L1", "L2"], "confidence": 0.87, "recommended_action": "failover_uplink",
            "action_rationale": "x", "rollback_plan": "failback_uplink", "risk": "low"}}}],  # caveats omitted
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-goog-api-key"] == "test-key" and "gemini-flash-latest:generateContent" in str(req.url)
        body = json.loads(req.content)
        bodies.append(body)
        decls = body["tools"][0]["functionDeclarations"]
        assert "additionalProperties" not in json.dumps(decls) and body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": script[len(bodies) - 1]},
                                                         "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
                                         "modelVersion": "gemini-flash-test"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(inv, "GEMINI_MODEL", "gemini-flash-latest")
    queued = []

    async def enqueue(iid, by):
        queued.append(by)
        return {"action_id": "a1", "state": "proposed"}

    out = run(inv.investigate(FakeConn(), "acme", "inc-0412", enqueue))
    assert out["engine"] == "gemini" and out["model"] == "gemini-flash-test" and queued == ["ai-investigator"]
    assert out["diagnosis"]["caveats"] == [] and out["usage"]["input_tokens"] == 200
    turn = bodies[1]["contents"]
    assert turn[1]["parts"][0]["thoughtSignature"] == "sig1"
    fr = turn[2]["parts"][0]["functionResponse"]
    assert fr["name"] == "get_incident_context" and fr["response"]["confidence"] == 0.87


def test_incident_routes_are_not_shadowed():
    """A generic /api/incidents/{iid}/{op} route once swallowed /investigate and /remediations (404)."""
    pytest.importorskip("fastapi")
    from starlette.routing import Match

    from api import main

    def endpoint(method, path):
        scope = {"type": "http", "method": method, "path": path, "root_path": ""}
        for r in main.app.router.routes:
            m, _ = r.matches(scope)
            if m == Match.FULL:
                return r.endpoint.__name__
        return None

    assert endpoint("POST", "/api/incidents/inc-0002/investigate") == "investigate"
    assert endpoint("POST", "/api/incidents/inc-0002/remediations") == "propose"
    assert endpoint("POST", "/api/incidents/inc-0002/ack") == "incident_ack"
    assert endpoint("POST", "/api/incidents/inc-0002/resolve") == "incident_resolve"
