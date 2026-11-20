import { useEffect } from "react";
import type { ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, AlarmClock, BellRing, Boxes, ChevronsLeft, ChevronsRight, FileClock, Gauge, Hexagon, LayoutDashboard, LogOut,
  Moon, Network, ScrollText, Server, Settings, ShieldAlert, ShieldCheck, Sun,
} from "lucide-react";
import { api, isMock, setDemo, setToken } from "@/lib/api";
import { startLive } from "@/lib/live";
import { RANGE_MIN, useStore } from "@/lib/store";
import type { Range } from "@/lib/store";
import { ago, cx, fmtMs, fmtNum } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { SourcePanel } from "./SourcePanel";

const NAV: { to: string; label: string; icon: ReactNode; group?: string }[] = [
  { to: "/", label: "Overview", icon: <LayoutDashboard size={18} /> },
  { to: "/infrastructure", label: "Infrastructure", icon: <Hexagon size={18} /> },
  { to: "/metrics", label: "Metrics", icon: <Activity size={18} /> },
  { to: "/network", label: "Service map", icon: <Network size={18} /> },
  { to: "/events", label: "Events", icon: <ScrollText size={18} /> },
  { to: "/security", label: "Security", icon: <ShieldAlert size={18} /> },
  { to: "/incidents", label: "Incidents", icon: <BellRing size={18} /> },
  { to: "/fleet", label: "Fleet", icon: <Server size={18} />, group: "trust" },
  { to: "/trust", label: "Trust Center", icon: <ShieldCheck size={18} />, group: "trust" },
  { to: "/slo", label: "Real-time SLOs", icon: <Gauge size={18} />, group: "trust" },
  { to: "/audit", label: "Audit log", icon: <FileClock size={18} />, group: "trust" },
  { to: "/settings", label: "Settings", icon: <Settings size={18} />, group: "trust" },
];

function Logo({ collapsed }: { collapsed: boolean }) {
  return (
    <div className="flex h-12 items-center gap-2.5 px-[15px]">
      <svg width="26" height="26" viewBox="0 0 32 32" className="shrink-0">
        <rect width="32" height="32" rx="8" fill="#0d9488" />
        <path d="M6 17h20" stroke="white" strokeWidth="3" strokeLinecap="round" />
        <path d="M16 7v18" stroke="#fbbf24" strokeWidth="3" strokeLinecap="round" />
      </svg>
      {!collapsed && <span className="text-[15px] font-bold tracking-tight text-white">TrustLine</span>}
    </div>
  );
}

function Nav() {
  const collapsed = useStore((s) => s.navCollapsed);
  const toggle = useStore((s) => s.toggleNav);
  const me = useStore((s) => s.me);
  const { data } = useQuery({ queryKey: ["incidents", "open-count"], queryFn: () => api<{ incidents: { status: string }[] }>("/api/incidents"), refetchInterval: 10_000 });
  const open = data?.incidents.filter((i) => i.status !== "resolved").length ?? 0;
  const logout = () => {
    setToken(null);
    setDemo(false);
    window.dispatchEvent(new Event("tl-logout"));
  };
  return (
    <nav className={cx("flex shrink-0 flex-col bg-nav transition-[width] duration-200", collapsed ? "w-[56px]" : "w-[212px]")}>
      <Logo collapsed={collapsed} />
      <div className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-2">
        {NAV.map((n, i) => (
          <div key={n.to}>
            {n.group && NAV[i - 1]?.group !== n.group && <div className="mx-2 my-2 h-px bg-white/10" />}
            <NavLink
              to={n.to}
              end={n.to === "/"}
              title={collapsed ? n.label : undefined}
              className={({ isActive }) =>
                cx(
                  "group relative flex h-9 items-center gap-3 rounded-md px-[9px] text-[13px] font-medium transition-colors",
                  isActive ? "bg-nav-2 text-white" : "text-nav-text hover:bg-white/5 hover:text-white",
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && <span className="absolute top-1.5 bottom-1.5 left-0 w-[3px] rounded-r bg-accent-2" />}
                  <span className="shrink-0">{n.icon}</span>
                  {!collapsed && <span className="truncate">{n.label}</span>}
                  {n.to === "/incidents" && open > 0 && (
                    <span className={cx("rounded-full bg-poor px-1.5 text-[10px] font-bold leading-4 text-white", collapsed ? "absolute top-1 right-1" : "ml-auto")}>{open}</span>
                  )}
                </>
              )}
            </NavLink>
          </div>
        ))}
      </div>
      <div className="border-t border-white/10 p-2">
        {!collapsed && me && (
          <div className="mb-2 rounded-md bg-white/5 px-2.5 py-2 text-[11.5px] text-nav-text">
            <div className="truncate font-medium text-white">{me.email}</div>
            <div className="truncate">
              {me.role} · {me.tenant_name ?? me.tenant}
            </div>
          </div>
        )}
        <div className={cx("flex", collapsed ? "flex-col items-center gap-1" : "items-center justify-between")}>
          <button onClick={logout} title="Sign out" className="rounded p-2 text-nav-text hover:bg-white/5 hover:text-white">
            <LogOut size={16} />
          </button>
          <button onClick={toggle} title={collapsed ? "Expand" : "Collapse"} className="rounded p-2 text-nav-text hover:bg-white/5 hover:text-white">
            {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          </button>
        </div>
      </div>
    </nav>
  );
}

const TITLES: Record<string, string> = Object.fromEntries(NAV.map((n) => [n.to, n.label]));

function LiveChip() {
  const ws = useStore((s) => s.ws);
  const lag = useStore((s) => s.lagMs);
  const label = ws === "live" ? "live" : ws === "demo" ? "demo stream" : ws === "connecting" ? "connecting…" : "reconnecting…";
  const ok = ws === "live" || ws === "demo";
  return (
    <span
      className={cx("inline-flex h-7 items-center gap-2 rounded-full border px-2.5 text-[11.5px] font-medium", ok ? "border-accent/30 bg-accent-soft text-accent" : "border-fair/40 text-fair")}
      title="Measured event → pixels latency of the last update (window start to render)"
    >
      {ok ? <span className="live-dot" /> : <AlarmClock size={12} />}
      {label}
      {ok && lag !== null && <span className="mono text-[11px] text-t2">{fmtMs(lag)}</span>}
    </span>
  );
}

function TopBar() {
  const loc = useLocation();
  const me = useStore((s) => s.me);
  const theme = useStore((s) => s.theme);
  const toggleTheme = useStore((s) => s.toggleTheme);
  const range = useStore((s) => s.range);
  const setRange = useStore((s) => s.setRange);
  const base = "/" + (loc.pathname.split("/")[1] ?? "");
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
      <div className="flex min-w-0 items-center gap-2">
        <Boxes size={15} className="text-t3" />
        <span className="text-t3">{me?.tenant_name ?? me?.tenant ?? "…"}</span>
        <span className="text-t3">/</span>
        <span className="truncate font-semibold">{TITLES[base] ?? (base === "/incidents" ? "Incidents" : "TrustLine")}</span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        {isMock() && <span className="rounded-full bg-boundary/15 px-2.5 py-1 text-[11px] font-semibold text-boundary">DEMO DATA</span>}
        {me?.region && (
          <span className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 text-[11.5px] text-t2" title="This tenant's data is stored and processed only in its home region">
            <span className="h-2 w-2 rounded-full bg-accent" />
            {me.region}
          </span>
        )}
        <div className="inline-flex rounded-md border border-line bg-panel-2 p-0.5">
          {(Object.keys(RANGE_MIN) as Range[]).map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              className={cx("h-6 rounded px-2.5 text-[11.5px] font-medium", range === r ? "bg-panel text-t1 shadow-sm" : "text-t3 hover:text-t2")}
            >
              {r === "live" ? "Live" : r}
            </button>
          ))}
        </div>
        <LiveChip />
        <button onClick={toggleTheme} className="rounded-md p-1.5 text-t3 hover:bg-panel-2 hover:text-t1" title="Toggle theme">
          {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
        </button>
      </div>
    </header>
  );
}

/** Every page: what crossed the trust boundary, in one line. */
function PrivacyRibbon() {
  const { data } = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/overview"), refetchInterval: 5000 });
  const last = useStore((s) => s.lastPayloadAt);
  const me = useStore((s) => s.me);
  const p = data?.privacy;
  return (
    <div className="flex h-7 shrink-0 items-center gap-4 overflow-hidden border-b border-line bg-panel-2 px-4 text-[11.5px] whitespace-nowrap text-t2">
      <span className="inline-flex items-center gap-1.5 font-semibold text-accent">
        <ShieldCheck size={13} /> Metadata-only
      </span>
      <span>
        raw fields received: <b className="text-excellent">0</b>
      </span>
      <span>
        redactions 24h: <b className="tnum text-redact">{fmtNum(p?.redactions_24h ?? 0, 1)}</b>
      </span>
      <span>
        blocked at egress: <b className="tnum text-t1">{p?.blocked_total ?? 0}</b>
      </span>
      <span>
        last payload: <b className="text-t1">{last ? ago(last) : "—"}</b>
      </span>
      <span>
        residency: <b className="text-t1">{me?.region ?? "—"}</b> <span className="text-excellent">✓</span>
      </span>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const theme = useStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => startLive(qc, nav), [qc, nav]);
  return (
    <div className="flex h-full">
      <Nav />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <PrivacyRibbon />
        <main className="min-h-0 flex-1 overflow-y-auto bg-bg">{children}</main>
      </div>
      <SourcePanel />
    </div>
  );
}
