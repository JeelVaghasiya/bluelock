"""L5 - evidence-backed explanation. Every sentence traces to a playbook rule and a signal.

weight      = |z_i| / sum|z|  (security findings use risk instead of z)
confidence  = 0.5*min(1, max|z|/8) + 0.3*(layers agreeing / 3) + 0.2*persistence
"""
from __future__ import annotations

from collections import defaultdict

from .correlate import Correlator, Incident, ts

KIND = {"h": "host", "svc": "svc", "link": "link", "ip": "ip", "u": "user"}
STAT = {"cpu": "p95", "rtt": "p95", "up": "min", "disk": "max"}


def short(src: str | None) -> str:
    if not src:
        return "?"
    p, _, h = src.partition(":")
    return f"{p}·{h[:4]}"


class _Fmt(dict):
    def __missing__(self, k):
        return "?"


def match_rule(playbook: dict, root_kind: str, root_metrics: set[str], impacted_metrics: set[str],
               categories: set[str], rising: bool, patterns: set[str] | None = None) -> dict:
    rules = playbook.get("rules") or []
    for r in rules:
        w = r.get("when") or {}
        if not w:
            continue
        if "categories_any" in w and not (categories & set(w["categories_any"])):
            continue
        if "patterns_any" in w and not ((patterns or set()) & set(w["patterns_any"])):
            continue
        if "root_kind" in w and w["root_kind"] != root_kind:
            continue
        if "root_metrics_any" in w and not (root_metrics & set(w["root_metrics_any"])):
            continue
        if "impacted_metrics_any" in w and not (impacted_metrics & set(w["impacted_metrics_any"])):
            continue
        if w.get("trend") == "rising" and not rising:
            continue
        return r
    return next((r for r in rules if not r.get("when")), {"id": "generic_anomaly", "action": {}, "why": ""})


def explain(inc: Incident, corr: Correlator, playbook: dict) -> dict:
    root = inc.root
    root_kind = KIND.get((root or "").split(":", 1)[0], "host")
    by_src: dict[str, list] = defaultdict(list)
    for m in inc.members:
        by_src[m.src].append(m)

    # one signal per (src, metric), strongest first
    signals = []
    for m in inc.members:
        mag = abs(m.z) if m.z is not None else (m.risk or 0.5) * 8
        signals.append({"src": m.src, "metric": m.metric or m.pattern, "stat": STAT.get(m.metric or "", "avg"),
                        "value": m.value, "baseline": m.baseline, "z": m.z, "layer": m.layer,
                        "onset_ts": m.onset_ts, "_mag": mag, "category": m.category, "pattern": m.pattern})
    dedup = {}
    for s in sorted(signals, key=lambda s: -s["_mag"]):
        dedup.setdefault((s["src"], s["metric"]), s)
    signals = list(dedup.values())
    total = sum(s["_mag"] for s in signals) or 1.0
    for s in signals:
        s["weight"] = round(s.pop("_mag") / total, 2)

    layers = set()
    for m in inc.members:
        layers |= m.layers | {m.layer}
    agreeing = sorted(x for x in layers if x in ("L1", "L2", "L3", "L0", "T"))
    model_layers = [x for x in agreeing if x in ("L1", "L2", "L3")] or agreeing
    maxz = max((abs(m.z) for m in inc.members if m.z is not None), default=None)
    magnitude = min(1.0, maxz / 8) if maxz is not None else max((m.risk or 0.6) for m in inc.members)
    agreement = min(1.0, len(model_layers) / 3)
    persistence = max((m.persistence for m in inc.members), default=1.0)
    parts = {"magnitude": round(0.5 * magnitude, 2), "agreement": round(0.3 * agreement, 2),
             "persistence": round(0.2 * persistence, 2)}
    confidence = round(sum(parts.values()), 2)

    root_members = by_src.get(root, [])
    root_metrics = {m.metric for m in root_members if m.metric}
    impacted = corr.impacted(inc)
    impacted_metrics = {m.metric for m in inc.members if m.src != root and m.metric}
    categories = {m.category for m in inc.members}
    rising = any((m.z or 0) > 0 for m in root_members)
    patterns = {m.pattern for m in inc.members if m.pattern}
    rule = match_rule(playbook, root_kind, root_metrics, impacted_metrics, categories, rising, patterns)
    rm = max(root_members, key=lambda m: abs(m.z or 0), default=inc.members[0])
    others = [m for m in inc.members if m.src != root]
    lead = (min((ts(m.onset_ts) for m in others), default=ts(rm.onset_ts)) - ts(rm.onset_ts)).total_seconds()
    dur = (ts(max(m.last_ts for m in inc.members)) - ts(rm.onset_ts)).total_seconds()
    f = _Fmt(root=short(root), metric=f"{rm.metric} {STAT.get(rm.metric or '', 'avg')}" if rm.metric else rm.pattern,
             lead_s=f"{max(0, lead):.0f}", impacted_n=len(impacted), value=_num(rm.value),
             baseline=_num(rm.baseline), z=_num(rm.z), duration_s=f"{max(0, dur):.0f}",
             fail_count=rm.fail_count, distinct_users=rm.distinct_users, window_s=rm.window_s,
             pattern=(rm.pattern or "").replace("_", " "), layers="+".join(model_layers), hours_to_full="?")
    action = dict(rule.get("action") or {})
    action_text = (action.get("text") or "Investigate {root}").format_map(f)
    why = (rule.get("why") or "{layers} flagged {root}").format_map(f)
    title = _title(rule.get("id", "generic_anomaly"), root, rm)

    trace = []
    for m in sorted(inc.members, key=lambda m: ts(m.onset_ts))[:3]:
        if m.layer == "L1":
            trace.append({"step": "detect", "layer": "L1",
                          "detail": f"{short(m.src)} {m.metric} z={_num(m.z)} vs baseline {_num(m.baseline)}"
                                    f" (persistence {m.persistence:.0%})"})
        elif m.layer == "T":
            trace.append({"step": "detect", "layer": "T",
                          "detail": f"edge fast path: {short(m.src)} {m.metric}={_num(m.value)} beyond Poor "
                                    "threshold for 3 consecutive samples"})
        else:
            trace.append({"step": "detect", "layer": m.layer,
                          "detail": f"{(m.pattern or '').replace('_', ' ')} from {short(m.src)}: "
                                    f"{m.fail_count} fails, {m.distinct_users} users, risk {m.risk}"})
    for m in inc.members:
        if m.hst and "L2" in m.layers:
            trace.append({"step": "corroborate", "layer": "L2",
                          "detail": f"Half-Space Trees score {m.hst['score']} > p99 {m.hst['threshold']}"})
            break
    for m in inc.members:
        if m.contrib and "L3" in m.layers:
            top = max(m.contrib.items(), key=lambda kv: kv[1])
            trace.append({"step": "corroborate", "layer": "L3",
                          "detail": f"autoencoder reconstruction error above p99; {top[0]} explains {top[1]:.0%}"})
            break
    trace.append({"step": "correlate", "detail": f"{len(inc.members)} alert(s) on {len(inc.srcs)} component(s) "
                                                 "within +/-120 s and graph distance <= 2", "members": len(inc.members)})
    trace.append({"step": "root_cause", "chosen": root, "rejected": inc.rejected})
    trace.append({"step": "playbook", "rule": rule.get("id"),
                  "matched": [f"root_kind={root_kind}"] + [f"root_metric={x}" for x in sorted(root_metrics)][:2]
                             + [f"impacted={x}" for x in sorted(impacted_metrics)][:2]})
    pol = [f"reversible={'yes' if action.get('reversible') else 'no'}",
           f"blast radius {inc.blast} <= {action.get('max_blast_radius', '?')}",
           f"requires {action.get('requires_role', 'tenant-admin')}"]
    trace.append({"step": "policy", "detail": ", ".join(pol)})

    return {
        "incident": inc.incident_id, "priority": inc.priority, "title": title, "root_cause": root,
        "root_role": corr.roles.get(root or ""), "affected": impacted,
        "signals": signals, "layers_agreeing": model_layers, "persistence": persistence,
        "rule": rule.get("id"), "confidence": confidence, "confidence_parts": parts,
        "action": {"key": action.get("key", "investigate"), "text": action_text,
                   "reversible": bool(action.get("reversible")), "rollback_key": action.get("rollback_key"),
                   "requires_role": action.get("requires_role", "tenant-admin"),
                   "max_blast_radius": action.get("max_blast_radius"),
                   "executable": action.get("execute", True) and action.get("key") not in (None, "investigate")},
        "why": why, "trace": trace, "suppressed": inc.suppressed[-20:],
        "noise": {"raw_alerts": len(inc.members), "incidents": 1}, "blast_radius": inc.blast,
    }


def _num(v) -> str:
    if v is None:
        return "?"
    v = float(v)
    return f"{v:.3f}" if abs(v) < 1 else f"{v:.1f}"


def _title(rule: str, root: str | None, m) -> str:
    names = {
        "upstream_link_degradation": "Upstream link degradation",
        "link_down": "Link down",
        "memory_leak": "Memory leak",
        "cpu_saturation": "CPU saturation",
        "disk_fill": "Disk filling up",
        "auth_attack": f"Login attack ({(m.pattern or 'auth').replace('_', ' ')})",
        "login_flood": "Login flood (distributed, >100 failed logins/s)",
        "service_errors": "Service error spike",
        "generic_anomaly": "Anomalous behaviour",
    }
    return f"{names.get(rule, rule)} on {short(root)}"
