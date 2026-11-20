import { mock } from "@/mock/world";

const TOKEN_KEY = "tl_token";
const DEMO_KEY = "tl_demo";

export const isMock = (): boolean => {
  if (import.meta.env.VITE_MOCK === "1") return true;
  try {
    return localStorage.getItem(DEMO_KEY) === "1";
  } catch {
    return false;
  }
};
export function setDemo(on: boolean) {
  try {
    if (on) localStorage.setItem(DEMO_KEY, "1");
    else localStorage.removeItem(DEMO_KEY);
  } catch {
    /* storage unavailable */
  }
}
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Q = Record<string, string | number | undefined | null>;

/** Demo-mode router: same paths as the real API, answered by the simulated tenant. */
function mockRoute(method: string, path: string, q: URLSearchParams, body: unknown): unknown {
  const p = path.replace(/^\/api/, "");
  const m = (re: RegExp) => p.match(re);
  const num = (k: string, d: number) => Number(q.get(k) ?? d);
  let r: RegExpMatchArray | null;
  if (method === "GET") {
    if (p === "/me") return mock.me();
    if (p === "/overview") return mock.overview();
    if (p === "/series/overview") return mock.overviewSeries(num("minutes", 60));
    if (p === "/sources") {
      const s = mock.sources().sources.filter((x) => (!q.get("state") || x.state === q.get("state")) && (!q.get("kind") || x.kind === q.get("kind")));
      return { sources: s };
    }
    if ((r = m(/^\/sources\/(.+)$/))) return mock.source(decodeURIComponent(r[1]));
    if (p === "/metrics/query") {
      const from = q.get("from");
      const minutes = from ? Math.max(5, Math.round((Date.now() - Date.parse(from)) / 60000)) : 15;
      return mock.metricQuery(q.get("src")!, q.get("metric")!, minutes, q.get("stat") ?? undefined);
    }
    if (p === "/topology") return mock.topology();
    if (p === "/events") {
      let ev = mock.events(60).events;
      const qq = q.get("q");
      if (qq) ev = ev.filter((e) => e.template.toLowerCase().includes(qq.toLowerCase()));
      for (const k of ["level", "category", "src"] as const) if (q.get(k)) ev = ev.filter((e) => e[k] === q.get(k));
      return { events: ev.slice(0, 300) };
    }
    if (p === "/events/facets") return mock.facets();
    if (p === "/security/findings") return mock.findings();
    if (p === "/incidents") return mock.incidents();
    if ((r = m(/^\/incidents\/([^/]+)$/))) return mock.incident(r[1]);
    if (p === "/fleet") return mock.fleet();
    if (p === "/delivery") return mock.delivery();
    if (p === "/trust/residency") return mock.residency();
    if (p === "/trust/audit") return mock.audit(q.get("payload_id") ?? "");
    if (p === "/slo") return mock.slo();
    if (p === "/thresholds") return mock.thresholds();
    if (p === "/audit") return mock.auditLog();
    if (p === "/sim/scenarios") return mock.scenarios();
  } else {
    if (p === "/auth/login") return { token: "demo", user: mock.me() };
    if ((r = m(/^\/incidents\/([^/]+)\/remediations$/))) return mock.propose(r[1]);
    if ((r = m(/^\/incidents\/([^/]+)\/(ack|resolve)$/))) return mock.incidentOp(r[1], r[2]);
    if ((r = m(/^\/remediations\/([^/]+)\/(approve|reject|rollback)$/))) return mock.remediationOp(r[1], r[2]);
    if (p === "/trust/isolation-test") return mock.isolation();
    if (p === "/sim/inject") return mock.inject((body as { kind: string }).kind);
    if (p === "/sim/clear") return mock.clear();
    if (p === "/thresholds") return { ok: true };
  }
  throw new ApiError(404, `demo mode: no handler for ${method} ${path}`);
}

export async function api<T = unknown>(path: string, opts: { method?: string; query?: Q; body?: unknown } = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  if (isMock()) {
    await new Promise((r) => setTimeout(r, 60 + Math.random() * 90));
    try {
      return structuredClone(mockRoute(method, path, qs, opts.body)) as T;
    } catch (e) {
      if (e instanceof ApiError) throw e;
      throw new ApiError(404, (e as Error).message);
    }
  }
  const url = path + (qs.toString() ? `?${qs}` : "");
  const headers: Record<string, string> = { Accept: "application/json" };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401 && !path.startsWith("/api/auth")) {
    setToken(null);
    window.dispatchEvent(new Event("tl-logout"));
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail ?? j.error ?? msg;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return (await res.json()) as T;
}
