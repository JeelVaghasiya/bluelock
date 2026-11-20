/* One WebSocket per tab. Live envelopes invalidate the matching queries (throttled), raise toasts
 * for new P1/P2 incidents, and - after the refreshed data has been painted (double rAF) - send a
 * render_ack so the server can measure true event -> pixels latency. */
import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { getToken, isMock } from "./api";
import { useStore } from "./store";
import type { WsEnvelope } from "./types";
import { mock, tickMock } from "@/mock/world";

const KEYS: Record<string, string[][]> = {
  metric: [["overview"], ["series"], ["sources"], ["metric"], ["topology"]],
  health: [["overview"], ["sources"], ["topology"], ["source"]],
  incident: [["overview"], ["incidents"], ["incident"]],
  finding: [["findings"], ["series"]],
  event: [["events"], ["series"]],
  collector: [["fleet"], ["overview"]],
  delivery: [["delivery"]],
  remediation: [["incident"], ["audit"]],
};

const afterPaint = () => new Promise<number>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(Date.now()))));

export function startLive(qc: QueryClient, navigate: (to: string) => void): () => void {
  const st = useStore.getState();
  const pending = new Set<string>();
  const acks: { id: string; kind: string }[] = [];
  let flushTimer: number | undefined;
  let offsetMs = 0;
  const seenIncidents = new Set<string>();

  // Coalesce live envelopes: at most one refetch wave every MIN_GAP ms. Envelopes arrive ~1/s per
  // collector; refetching on each one made every panel reload (and re-animate) every second.
  const MIN_GAP = 2500;
  let lastFlush = 0;
  const flush = async () => {
    flushTimer = undefined;
    lastFlush = Date.now();
    const kinds = [...pending];
    pending.clear();
    const keys = new Set<string>();
    kinds.forEach((k) => (KEYS[k] ?? []).forEach((key) => keys.add(JSON.stringify(key))));
    await Promise.all([...keys].map((k) => qc.invalidateQueries({ queryKey: JSON.parse(k) })));
    const items = acks.splice(0);
    if (!items.length) return;
    const render = await afterPaint();
    send({ type: "render_ack", items: items.map((i) => ({ ...i, render_ts: new Date(render).toISOString() })) });
  };
  const schedule = () => {
    if (flushTimer === undefined) flushTimer = window.setTimeout(flush, Math.max(350, MIN_GAP - (Date.now() - lastFlush)));
  };

  const notify = (inc: { incident_id: string; priority: string; title: string; created?: boolean; status?: string }) => {
    if (seenIncidents.has(inc.incident_id) || inc.status === "resolved") return;
    seenIncidents.add(inc.incident_id);
    if (inc.priority === "P1" || inc.priority === "P2") {
      toast.error(`${inc.priority} · ${inc.title}`, {
        description: `${inc.incident_id} opened — click to see the evidence`,
        action: { label: "Open", onClick: () => navigate(`/incidents/${inc.incident_id}`) },
        duration: 9000,
      });
    }
  };

  const handle = (env: WsEnvelope) => {
    const s = useStore.getState();
    if (env.type === "hello") {
      (env.data?.incidents ?? []).forEach((i: { incident_id: string }) => seenIncidents.add(i.incident_id));
      return;
    }
    if (env.type === "pong") {
      const t0 = Date.parse(env.data.t0);
      const t1 = Date.now();
      offsetMs = Date.parse(env.data.server_ts) - (t0 + (t1 - t0) / 2);
      send({ type: "offset", offset_ms: offsetMs });
      return;
    }
    if (env.type === "latency") {
      s.pushLatency((env.data.samples ?? []).filter((x: { excluded: string | null }) => !x.excluded).map((x: { kind: string; ms: number }) => ({ ...x, at: Date.now() })));
      return;
    }
    if (env.type === "metric") s.touchPayload();
    if (env.type === "incident" && env.data.created) notify(env.data);
    if (env.id && (env.type === "metric" || env.type === "health")) acks.push({ id: env.id, kind: "dash" });
    if (env.id && (env.type === "incident" || env.type === "finding") && env.timing?.breach_ts) acks.push({ id: env.id, kind: "alert" });
    pending.add(env.type);
    schedule();
  };

  // ---------------------------------------------------------------- demo mode: simulated stream
  let ws: WebSocket | null = null;
  const send = (o: unknown) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(o));
  };
  if (isMock()) {
    st.setWs("demo");
    mock.overview().incidents.forEach((i) => seenIncidents.add(i.incident_id));
    const iv = window.setInterval(() => {
      tickMock();
      handle({ v: 1, type: "metric", id: null, data: {} });
      handle({ v: 1, type: "health", id: null, data: {} });
      for (const i of mock.overview().incidents) notify({ ...i, created: true });
      const lag = 700 + Math.random() * 700;
      useStore.getState().pushLatency([{ kind: "dash", ms: lag, at: Date.now() }]);
    }, 2000);
    const iv2 = window.setInterval(() => {
      ["incident", "finding", "event", "collector", "delivery", "remediation"].forEach((k) => pending.add(k));
      schedule();
    }, 4000);
    return () => {
      clearInterval(iv);
      clearInterval(iv2);
    };
  }

  // ---------------------------------------------------------------- real WebSocket
  let stopped = false;
  let backoff = 500;
  let pingTimer: number | undefined;
  const connect = () => {
    if (stopped) return;
    useStore.getState().setWs("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(getToken() ?? "")}`);
    ws.onopen = () => {
      backoff = 500;
      useStore.getState().setWs("live");
      const ping = () => send({ type: "ping", t0: new Date().toISOString() });
      ping();
      pingTimer = window.setInterval(ping, 10_000);
      ["overview", "sources", "incidents", "topology", "series"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    };
    ws.onmessage = (ev) => {
      try {
        handle(JSON.parse(ev.data));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = (ev) => {
      clearInterval(pingTimer);
      useStore.getState().setWs("down");
      if (ev.code === 4401) {
        window.dispatchEvent(new Event("tl-logout"));
        return;
      }
      if (!stopped) setTimeout(connect, backoff);
      backoff = Math.min(8000, backoff * 2);
    };
  };
  connect();
  return () => {
    stopped = true;
    clearInterval(pingTimer);
    ws?.close();
  };
}
