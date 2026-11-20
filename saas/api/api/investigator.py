"""TrustLine AI Investigator - an SRE agent over METADATA ONLY.

The agent reads an incident's evidence through three read-only tools (incident context, topology,
playbook rule), writes a plain-English diagnosis that cites the exact signals, z-scores and
confidence it relied on, and may recommend ONE playbook action. It never executes anything: a
recommendation is enqueued as a `proposed` remediation that a tenant-admin must approve, with the
rollback plan recorded next to it, and the whole investigation is written to the audit log.

Engines (INVESTIGATOR_ENGINE=auto picks the first one that has a credential)
  * gemini        - Google Gemini (generateContent REST API, function calling) when GEMINI_API_KEY
                    is configured on the regional API. Model: GEMINI_MODEL (default gemini-flash-latest).
  * claude        - Claude (Messages API, manual tool loop) when ANTHROPIC_API_KEY (or another
                    Anthropic credential) is configured on the regional API.
  * deterministic - the same three tools called in code and a templated diagnosis. Used when no
                    credential is configured or the API call fails, so the feature always works.

What leaves the region when a model engine runs: the incident's evidence object (pseudonymous
12-hex source codes, metric names, numbers, timestamps, rule ids) and topology edges between
pseudonyms. No raw log text, user names, IPs or secrets exist in the SaaS to send.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any, Awaitable, Callable

from tl_common.config import load_yaml

log = logging.getLogger("api.investigator")

MODEL = os.environ.get("INVESTIGATOR_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("INVESTIGATOR_EFFORT", "medium")
MAX_TURNS = int(os.environ.get("INVESTIGATOR_MAX_TURNS", "8"))
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
# tried in order when a model is overloaded (429/5xx), times out or is not available (404)
GEMINI_FALLBACKS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS", "gemini-3.8-flash,gemini-3.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
GEMINI_TIMEOUT_S = float(os.environ.get("GEMINI_TIMEOUT_S", "20"))
GEMINI_THINKING = os.environ.get("GEMINI_THINKING", "low")  # thinkingLevel; empty = model default
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are the TrustLine AI Investigator, an autonomous Site Reliability Engineering assistant inside a metadata-only AIOps SaaS.

Your job: investigate one correlated incident, explain in plain English what is happening and why, and recommend at most one policy-bounded remediation from the playbook.

Data you can see. You only ever receive allow-listed metadata: numeric metrics, baselines, z-scores, signal blame weights, detection-layer agreement, confidence scores, topology edges, and pseudonymous source identifiers such as `h:9c1e4a7b20f1` (host), `svc:...` (service) or `link:...` (uplink). Raw logs, user names, IP addresses and credentials never reach this platform, so do not ask for them and do not guess real names behind pseudonyms; refer to components by their pseudonym.

How to investigate:
1. Call get_incident_context for the incident first.
2. Call query_topology on the root-cause candidate to confirm which components depend on it, and on any other component whose role in the incident is unclear.
3. Call fetch_playbook_rule for the incident's rule to read the remediation, its rollback and its policy limits.
4. Finish by calling submit_diagnosis exactly once.

Grounding rules for the diagnosis:
- Every claim must cite the evidence it rests on: the signal's source pseudonym, metric, current value, baseline, z-score and weight, and the incident confidence. Use only numbers returned by the tools; never invent or round them into different values.
- Name which detection layers agree (L1 robust baseline, L2 streaming Half-Space Trees, L3 autoencoder, L4 topology correlation) and say plainly when evidence is thin, for example a single layer or low persistence.
- If the topology contradicts the engine's root-cause candidate, say so and explain which component you believe is the origin.
- Recommend only the action the playbook rule defines, or "investigate" when the rule has no executable action or the evidence does not support acting. State the rollback plan exactly as the playbook gives it; if the action is not reversible, say so.
- You never execute anything. A recommended action is queued for a human operator to approve.
- Keep the summary to three or four sentences an on-call engineer can act on."""

TOOLS: list[dict] = [
    {
        "name": "get_incident_context",
        "description": "Fetch an incident's correlated evidence: root-cause candidate, per-signal values, baselines, z-scores and blame weights, the detection layers that agree, persistence, confidence and its parts, the matched playbook rule, the engine's reasoning trace, suppressed alerts, and the current health state of each affected component.",
        "input_schema": {"type": "object", "properties": {"incident_id": {"type": "string", "description": "Incident id, e.g. inc-0042"}},
                         "required": ["incident_id"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "query_topology",
        "description": "Query the dependency graph around one source pseudonym: what it depends on (upstream), what depends on it (downstream, up to two hops), and the current health state of each of those components.",
        "input_schema": {"type": "object", "properties": {"source_id": {"type": "string", "description": "Source pseudonym, e.g. link:3fa1c09e7d22"}},
                         "required": ["source_id"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "fetch_playbook_rule",
        "description": "Retrieve a playbook rule by id: its match conditions, the remediation action key and text, whether it is reversible and its rollback action, the approver role, and the maximum blast radius it may be applied to.",
        "input_schema": {"type": "object", "properties": {"rule_name": {"type": "string", "description": "Playbook rule id, e.g. upstream_link_degradation"}},
                         "required": ["rule_name"], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "submit_diagnosis",
        "description": "Submit the finished investigation. Call exactly once, after gathering evidence. The recommended action is queued for human approval; nothing is executed.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "root_cause", "root_cause_reasoning", "citations", "layers_agreeing", "confidence",
                         "recommended_action", "action_rationale", "rollback_plan", "risk", "caveats"],
            "properties": {
                "summary": {"type": "string", "description": "3-4 sentence plain-English diagnosis for the on-call engineer"},
                "root_cause": {"type": "string", "description": "Pseudonym of the component you conclude is the origin"},
                "root_cause_reasoning": {"type": "string", "description": "Why this component and not the others, citing timing, topology and z-scores"},
                "citations": {
                    "type": "array",
                    "description": "The evidence the diagnosis rests on, one entry per signal",
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["src", "metric", "value", "baseline", "z", "weight", "role"],
                        "properties": {
                            "src": {"type": "string"}, "metric": {"type": "string"},
                            "value": {"type": ["number", "null"]}, "baseline": {"type": ["number", "null"]},
                            "z": {"type": ["number", "null"]}, "weight": {"type": ["number", "null"]},
                            "role": {"type": "string", "enum": ["root_cause", "impacted", "context"]},
                        },
                    },
                },
                "layers_agreeing": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "description": "The incident confidence from the evidence (0-1), not your own estimate"},
                "recommended_action": {"type": "string", "description": "The playbook action key, or 'investigate'"},
                "action_rationale": {"type": "string"},
                "rollback_plan": {"type": "string", "description": "The rollback from the playbook, or why none exists"},
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "caveats": {"type": "array", "items": {"type": "string"}, "description": "Gaps or weak points in the evidence"},
            },
        },
        "strict": True,
    },
]

# last investigation per (tenant, incident) - the audit log keeps the permanent record
CACHE: "OrderedDict[tuple[str, str], dict]" = OrderedDict()


def _jsonable(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if hasattr(o, "hex") and not isinstance(o, (str, bytes, float)):
        return str(o)
    return o


def playbook() -> dict[str, dict]:
    return {r["id"]: r for r in (load_yaml("playbook.yaml") or {}).get("rules", [])}


class Tools:
    """Read-only tool implementations. Every query runs inside the caller's tenant transaction,
    so row-level security limits the agent to the tenant that asked."""

    def __init__(self, conn):
        self.c = conn

    async def get_incident_context(self, incident_id: str) -> dict:
        i = await self.c.fetchrow("SELECT incident_id, status, priority, severity, title, root_cause_src, affected,"
                                  " evidence, alert_count, breach_ts, opened_ts FROM incidents WHERE incident_id=$1",
                                  incident_id)
        if not i:
            return {"error": f"no incident {incident_id} for this tenant"}
        ev = dict(i["evidence"] or {})
        srcs = [i["root_cause_src"], *(i["affected"] or [])]
        hs = await self.c.fetch("SELECT src, kind, role, state, stale, reason FROM health_states WHERE src = ANY($1::text[])",
                                [s for s in srcs if s])
        alerts = await self.c.fetch("SELECT src, metric, layer, z, score, value, baseline, onset_ts FROM alerts"
                                    " WHERE incident_id=$1 ORDER BY onset_ts LIMIT 40", incident_id)
        return _jsonable({
            "incident_id": i["incident_id"], "status": i["status"], "priority": i["priority"], "title": i["title"],
            "root_cause_candidate": i["root_cause_src"], "affected": i["affected"], "alert_count": i["alert_count"],
            "breach_ts": i["breach_ts"], "opened_ts": i["opened_ts"],
            "signals": ev.get("signals", []), "layers_agreeing": ev.get("layers_agreeing", []),
            "persistence": ev.get("persistence"), "confidence": ev.get("confidence"),
            "confidence_parts": ev.get("confidence_parts"), "rule": ev.get("rule"), "why": ev.get("why"),
            "trace": ev.get("trace", []), "suppressed": ev.get("suppressed", [])[:10],
            "blast_radius": ev.get("blast_radius"), "noise": ev.get("noise"),
            "alerts": [dict(a) for a in alerts], "health": [dict(h) for h in hs],
        })

    async def query_topology(self, source_id: str) -> dict:
        t = await self.c.fetchrow("SELECT nodes, edges FROM topology")
        if not t:
            return {"error": "no topology reported yet"}
        edges = [(e.get("dependent"), e.get("dependency")) for e in (t["edges"] or [])]
        nodes = {n["src"]: n for n in (t["nodes"] or [])}
        if source_id not in nodes:
            return {"error": f"{source_id} is not in the topology", "known_sources": sorted(nodes)[:60]}
        # edge a -> b means "a depends on b"
        upstream = sorted({b for a, b in edges if a == source_id})
        down1 = sorted({a for a, b in edges if b == source_id})
        down2 = sorted({a for a, b in edges if b in down1 and a != source_id} - set(down1))
        hs = {r["src"]: dict(r) for r in await self.c.fetch(
            "SELECT src, state, stale, reason FROM health_states WHERE src = ANY($1::text[])",
            [source_id, *upstream, *down1, *down2])}

        def node(s: str) -> dict:
            n = nodes.get(s, {})
            h = hs.get(s, {})
            return {"src": s, "kind": n.get("kind"), "role": n.get("role"), "state": h.get("state", "excellent"),
                    "stale": h.get("stale", False), "reason": h.get("reason")}

        return {"source": node(source_id), "depends_on": [node(s) for s in upstream],
                "dependents": [node(s) for s in down1], "dependents_2_hops": [node(s) for s in down2],
                "edge_semantics": "A depends on B; a fault in B can degrade A"}

    async def fetch_playbook_rule(self, rule_name: str) -> dict:
        r = playbook().get(rule_name)
        if not r:
            return {"error": f"no playbook rule {rule_name}", "rules": sorted(playbook())}
        return r

    async def run(self, name: str, args: dict) -> dict:
        fn: Callable[..., Awaitable[dict]] | None = {
            "get_incident_context": self.get_incident_context, "query_topology": self.query_topology,
            "fetch_playbook_rule": self.fetch_playbook_rule}.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        return await fn(**args)


def _short(o: Any, n: int = 220) -> str:
    s = json.dumps(o, default=str)
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------------------------- engines
async def _claude(tools: Tools, incident_id: str) -> tuple[dict, list[dict], dict]:
    import anthropic  # optional dependency: only needed for the claude engine

    client = anthropic.AsyncAnthropic(max_retries=2, timeout=120)
    messages: list[dict] = [{"role": "user", "content":
                             f"Investigate incident {incident_id}. Use the tools, then call submit_diagnosis."}]
    trace: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "request_ids": []}
    for _ in range(MAX_TURNS):
        resp = await client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM_PROMPT, tools=TOOLS, messages=messages,
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        usage["requests"] += 1
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        usage["request_ids"].append(getattr(resp, "_request_id", None))
        usage["model"] = resp.model
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model declined: {getattr(resp.stop_details, 'category', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("model hit max_tokens before finishing")
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "pause_turn":
            continue
        calls = [b for b in resp.content if b.type == "tool_use"]
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                trace.append({"step": "note", "text": b.text.strip()[:600]})
        if not calls:  # answered in prose without submitting: ask once more for the structured result
            messages.append({"role": "user", "content": "Call submit_diagnosis now with your findings."})
            continue
        results = []
        final = None
        for b in calls:
            if b.name == "submit_diagnosis":
                final = b.input
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": "queued for human review"})
                continue
            t0 = time.perf_counter()
            try:
                out = await tools.run(b.name, b.input)
                err = "error" in out
            except Exception as e:  # noqa: BLE001 - report tool failures to the model, don't crash the loop
                out, err = {"error": f"{type(e).__name__}: {e}"}, True
            trace.append({"step": "tool", "tool": b.name, "input": b.input, "ok": not err,
                          "ms": round((time.perf_counter() - t0) * 1000, 1), "result_preview": _short(out)})
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, default=str),
                            **({"is_error": True} if err else {})})
        messages.append({"role": "user", "content": results})
        if final is not None:
            return final, trace, usage
    raise RuntimeError(f"no diagnosis after {MAX_TURNS} turns")


def _gemini_schema(s: Any) -> Any:
    """JSON Schema -> the OpenAPI subset Gemini function declarations accept
    (no additionalProperties/strict; ["number","null"] becomes type + nullable)."""
    if isinstance(s, list):
        return [_gemini_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    out: dict[str, Any] = {}
    for k, v in s.items():
        if k in ("additionalProperties", "strict"):
            continue
        if k == "type" and isinstance(v, list):
            types = [t for t in v if t != "null"]
            out["type"] = types[0] if types else "string"
            if "null" in v:
                out["nullable"] = True
        elif k == "properties":
            out[k] = {pk: _gemini_schema(pv) for pk, pv in v.items()}
        else:
            out[k] = _gemini_schema(v)
    return out


GEMINI_TOOLS = [{"functionDeclarations": [
    {"name": t["name"], "description": t["description"], "parameters": _gemini_schema(t["input_schema"])}
    for t in TOOLS]}]


class _Overloaded(RuntimeError):
    pass


async def _gemini(tools: Tools, incident_id: str) -> tuple[dict, list[dict], dict]:
    """Run the loop on GEMINI_MODEL; if that model is overloaded, restart on the next fallback
    (a conversation is never switched between models mid-way, so thought signatures stay valid)."""
    errors = []
    for model in dict.fromkeys([GEMINI_MODEL, *GEMINI_FALLBACKS]):
        try:
            diag, trace, usage = await _gemini_run(tools, incident_id, model)
            if errors:
                usage["fallbacks"] = errors
            return diag, trace, usage
        except _Overloaded as e:
            log.warning("gemini %s overloaded, trying next model: %s", model, e)
            errors.append(f"{model}: {e}")
    raise RuntimeError("all Gemini models overloaded - " + "; ".join(errors))[:300]


async def _gemini_run(tools: Tools, incident_id: str, model: str) -> tuple[dict, list[dict], dict]:
    import httpx

    key = os.environ["GEMINI_API_KEY"]
    contents: list[dict] = [{"role": "user", "parts": [{"text":
                             f"Investigate incident {incident_id}. Use the tools, then call submit_diagnosis."}]}]
    trace: list[dict] = []
    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "model": model}
    thinking = GEMINI_THINKING
    async with httpx.AsyncClient(timeout=httpx.Timeout(GEMINI_TIMEOUT_S, connect=10)) as http:
        for _ in range(MAX_TURNS):
            body = {
                "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": contents, "tools": GEMINI_TOOLS,
                # ANY: every turn must be a function call, so the loop always ends in submit_diagnosis
                "toolConfig": {"functionCallingConfig": {"mode": "ANY"}},
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192,
                                     **({"thinkingConfig": {"thinkingLevel": thinking}} if thinking else {})},
            }
            r = None
            for attempt in range(2):  # one quick retry, then hand over to the next model
                try:
                    r = await http.post(GEMINI_URL.format(model=model), json=body,
                                        headers={"x-goog-api-key": key, "Content-Type": "application/json"})
                except httpx.TimeoutException as e:  # a hung model: move on rather than wait again
                    raise _Overloaded(f"no answer in {GEMINI_TIMEOUT_S:g}s") from e
                if r.status_code == 400 and thinking and "thinking" in r.text.lower():
                    thinking = ""  # this model has no thinkingLevel: resend without it
                    body["generationConfig"].pop("thinkingConfig", None)
                    continue
                if r.status_code not in (429, 500, 502, 503, 504):
                    break
                if attempt:
                    raise _Overloaded(f"HTTP {r.status_code}")
                await asyncio.sleep(1)
            if r.status_code == 404:  # model retired / not offered to this key
                raise _Overloaded("HTTP 404 model not available")
            if r.status_code != 200:
                try:
                    msg = r.json().get("error", {}).get("message", r.text)
                except ValueError:
                    msg = r.text
                raise RuntimeError(f"Gemini HTTP {r.status_code}: {str(msg)[:200]}")
            data = r.json()
            usage["requests"] += 1
            um = data.get("usageMetadata") or {}
            usage["input_tokens"] += int(um.get("promptTokenCount") or 0)
            usage["output_tokens"] += int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)
            usage["model"] = data.get("modelVersion") or model
            if (data.get("promptFeedback") or {}).get("blockReason"):
                raise RuntimeError(f"Gemini blocked the prompt: {data['promptFeedback']['blockReason']}")
            cands = data.get("candidates") or []
            if not cands:
                raise RuntimeError("Gemini returned no candidates")
            cand = cands[0]
            parts = (cand.get("content") or {}).get("parts") or []
            if not parts:
                raise RuntimeError(f"Gemini stopped without output: {cand.get('finishReason')}")
            # echo the model turn back verbatim (keeps thought signatures valid)
            contents.append({"role": "model", "parts": parts})
            for p in parts:
                if (p.get("text") or "").strip() and not p.get("thought"):
                    trace.append({"step": "note", "text": p["text"].strip()[:600]})
            calls = [p["functionCall"] for p in parts if p.get("functionCall")]
            if not calls:
                contents.append({"role": "user", "parts": [{"text": "Call submit_diagnosis now with your findings."}]})
                continue
            results, final = [], None
            for fc in calls:
                name, args = fc.get("name"), fc.get("args") or {}
                if name == "submit_diagnosis":
                    final = args
                    out: dict = {"result": "queued for human review"}
                else:
                    t0 = time.perf_counter()
                    try:
                        out = await tools.run(name, args)
                        err = "error" in out
                    except Exception as e:  # noqa: BLE001 - report tool failures to the model, don't crash the loop
                        out, err = {"error": f"{type(e).__name__}: {e}"}, True
                    trace.append({"step": "tool", "tool": name, "input": args, "ok": not err,
                                  "ms": round((time.perf_counter() - t0) * 1000, 1), "result_preview": _short(out)})
                fr: dict[str, Any] = {"name": name, "response": json.loads(json.dumps(out, default=str))}
                if fc.get("id"):
                    fr["id"] = fc["id"]
                results.append({"functionResponse": fr})
            contents.append({"role": "user", "parts": results})
            if final is not None:
                return _normalise(final), trace, usage
    raise RuntimeError(f"no diagnosis after {MAX_TURNS} turns")


def _normalise(d: dict) -> dict:
    """Fill any field a model left out so the dashboard always gets the full diagnosis shape."""
    d = dict(d)
    for k, v in (("summary", ""), ("root_cause", "unknown"), ("root_cause_reasoning", ""), ("citations", []),
                 ("layers_agreeing", []), ("confidence", 0), ("recommended_action", "investigate"),
                 ("action_rationale", ""), ("rollback_plan", ""), ("risk", "medium"), ("caveats", [])):
        if d.get(k) is None:
            d[k] = v
    if d["risk"] not in ("low", "medium", "high"):
        d["risk"] = "medium"
    return d


async def _deterministic(tools: Tools, incident_id: str) -> tuple[dict, list[dict], dict]:
    """Same tools, no model: a templated, fully grounded diagnosis."""
    trace: list[dict] = []

    async def call(name: str, **kw) -> dict:
        t0 = time.perf_counter()
        out = await tools.run(name, kw)
        trace.append({"step": "tool", "tool": name, "input": kw, "ok": "error" not in out,
                      "ms": round((time.perf_counter() - t0) * 1000, 1), "result_preview": _short(out)})
        return out

    ctx = await call("get_incident_context", incident_id=incident_id)
    if "error" in ctx:
        raise RuntimeError(ctx["error"])
    root = ctx.get("root_cause_candidate") or "unknown"
    topo = await call("query_topology", source_id=root) if root != "unknown" else {}
    rule = await call("fetch_playbook_rule", rule_name=ctx.get("rule") or "") if ctx.get("rule") else {}
    sigs = sorted(ctx.get("signals") or [], key=lambda s: -(s.get("weight") or 0))
    cites = [{"src": s.get("src"), "metric": s.get("metric"), "value": s.get("value"), "baseline": s.get("baseline"),
              "z": s.get("z"), "weight": s.get("weight"), "role": "root_cause" if s.get("src") == root else "impacted"}
             for s in sigs[:6]]
    top = cites[0] if cites else None
    layers = ctx.get("layers_agreeing") or []
    conf = ctx.get("confidence") or 0
    deps = [d["src"] for d in (topo.get("dependents") or [])]
    action = (rule.get("action") or {}) if rule else {}
    akey = action.get("key") or "investigate"
    reversible = bool(action.get("reversible"))
    rb = action.get("rollback_key")
    caveats = []
    if len(layers) < 2:
        caveats.append("only one detection layer agrees - treat as a lead, not a conclusion")
    if (ctx.get("persistence") or 0) < 0.67:
        caveats.append(f"persistence is {ctx.get('persistence')} - the anomaly may be intermittent")
    if not deps and len(ctx.get("affected") or []) > 0:
        caveats.append("topology shows no dependents for the root candidate; correlation is by time only")
    summ = (f"{ctx.get('title')}. The origin is most likely {root}"
            + (f": {top['metric']} is {top['value']} against a baseline of {top['baseline']} (z={top['z']}, weight {top['weight']})" if top else "")
            + f". {len(ctx.get('affected') or [])} dependent component(s) degraded after it"
            + (f" ({', '.join(deps[:4])})" if deps else "")
            + f"; {len(layers)} detection layer(s) agree ({', '.join(layers) or 'none'}), confidence {conf}."
            + (f" Recommended: {akey} - queued for approval." if akey != "investigate" else " No safe automatic action; investigate manually."))
    return {
        "summary": summ, "root_cause": root,
        "root_cause_reasoning": ctx.get("why") or "root is the anomalous member with no anomalous dependency",
        "citations": cites, "layers_agreeing": layers, "confidence": conf,
        "recommended_action": akey, "action_rationale": (action.get("text") or "").replace("{root}", root),
        "rollback_plan": (f"{rb} restores the previous state" if reversible and rb and rb != "none"
                          else "not reversible - approve only with a manual recovery plan"),
        "risk": "low" if reversible and (ctx.get("blast_radius") or 1) <= 3 else "medium", "caveats": caveats,
    }, trace, {"requests": 0}


def engine_available() -> str:
    """Which engine an investigation will try first."""
    pick = os.environ.get("INVESTIGATOR_ENGINE", "auto").lower()
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                      or os.environ.get("ANTHROPIC_PROFILE"))
    if pick == "deterministic":
        return "deterministic"
    if pick == "gemini":
        return "gemini" if has_gemini else "deterministic"
    if pick == "claude":
        return "claude" if has_claude else "deterministic"
    return "gemini" if has_gemini else "claude" if has_claude else "deterministic"


async def investigate(conn, tenant: str, incident_id: str, enqueue: Callable[[str, str], Awaitable[dict | None]]) -> dict:
    tools = Tools(conn)
    t0 = time.perf_counter()
    engine, fallback_reason = "deterministic", None
    want = engine_available()
    if want != "deterministic":
        try:
            diag, trace, usage = await (_gemini if want == "gemini" else _claude)(tools, incident_id)
            engine = want
        except Exception as e:  # noqa: BLE001 - any API failure degrades to the deterministic engine
            log.warning("%s investigator failed, using deterministic engine: %s", want, e)
            fallback_reason = f"{want}: {type(e).__name__}: {e}"[:300]
    else:
        fallback_reason = "no model credential configured on the regional API"
    if engine == "deterministic":
        diag, trace, usage = await _deterministic(tools, incident_id)

    # Policy bound: only the playbook's own action for this incident may be queued.
    ev = await conn.fetchval("SELECT evidence FROM incidents WHERE incident_id=$1", incident_id) or {}
    allowed = (ev.get("action") or {}).get("key")
    proposed = diag.get("recommended_action")
    queued, policy_note = None, None
    if proposed and proposed != "investigate":
        if proposed != allowed:
            policy_note = f"'{proposed}' is not the playbook action for this incident ('{allowed}') - not queued"
            diag["recommended_action"] = "investigate"
        else:
            queued = await enqueue(incident_id, "ai-investigator")
    out = {
        "incident_id": incident_id, "engine": engine, "model": usage.get("model") if engine != "deterministic" else None,
        "fallback_reason": fallback_reason, "diagnosis": diag, "trace": trace, "queued": queued,
        "policy_note": policy_note, "requires_approval": True, "usage": usage,
        "ms": round((time.perf_counter() - t0) * 1000), "at": datetime.now().astimezone().isoformat(),
        "data_sent": (f"incident evidence + topology between pseudonyms (metadata only) -> {engine}"
                      if engine != "deterministic" else "none (ran in-region)"),
    }
    CACHE[(tenant, incident_id)] = out
    CACHE.move_to_end((tenant, incident_id))
    while len(CACHE) > 300:
        CACHE.popitem(last=False)
    return out
