import { formatDistanceToNowStrict } from "date-fns";
import type { HealthState, Severity } from "./types";

export const STATE_COLOR: Record<HealthState, string> = {
  excellent: "var(--excellent)",
  good: "var(--good)",
  fair: "var(--fair)",
  poor: "var(--poor)",
};
export const STATE_GLYPH: Record<HealthState, string> = { excellent: "●", good: "◐", fair: "▲", poor: "■" };
export const SEV_COLOR: Record<Severity, string> = {
  critical: "var(--critical)",
  high: "var(--high)",
  medium: "var(--medium)",
  low: "var(--low)",
  info: "var(--info)",
};
export const PRIO_COLOR: Record<string, string> = { P1: "var(--critical)", P2: "var(--high)", P3: "var(--medium)", P4: "var(--low)" };

/** Reads a CSS variable so ECharts gets concrete colours that follow the theme. */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

const KIND_LABEL: Record<string, string> = { h: "host", svc: "svc", link: "link", ip: "ip", u: "user" };
export function shortSrc(src?: string | null): string {
  if (!src) return "—";
  const [p, h] = src.split(":");
  return `${KIND_LABEL[p] ?? p}·${(h ?? "").slice(0, 4)}`;
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(digits) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(digits) + "M";
  if (a >= 1e4) return (v / 1e3).toFixed(digits) + "k";
  if (a >= 100) return Math.round(v).toLocaleString();
  if (a >= 1) return v.toFixed(digits);
  if (a === 0) return "0";
  return v.toFixed(3);
}

export function fmtMetric(metric: string, v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  switch (metric) {
    case "cpu":
    case "mem":
    case "disk":
      return `${v.toFixed(1)}%`;
    case "loss":
      return `${v.toFixed(2)}%`;
    case "rtt":
    case "jitter":
      return v >= 100 ? `${Math.round(v)} ms` : `${v.toFixed(1)} ms`;
    case "error_rate":
      return `${(v * 100).toFixed(2)}%`;
    case "up":
      return `${(v * 100).toFixed(1)}%`;
    case "disk_hours_to_full":
      return `${v.toFixed(1)} h`;
    default:
      return fmtNum(v);
  }
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms >= 10_000) return `${(ms / 1000).toFixed(0)} s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

export function ago(ts?: string | number | null): string {
  if (!ts) return "—";
  try {
    return formatDistanceToNowStrict(typeof ts === "number" ? ts : new Date(ts), { addSuffix: true });
  } catch {
    return "—";
  }
}

export function hhmmss(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export const METRIC_LABEL: Record<string, string> = {
  cpu: "CPU",
  mem: "Memory",
  disk: "Disk used",
  rtt: "Latency",
  loss: "Packet loss",
  jitter: "Jitter",
  error_rate: "Error rate",
  up: "Availability",
  disk_hours_to_full: "Disk full in",
};

export const cx = (...xs: (string | false | null | undefined)[]) => xs.filter(Boolean).join(" ");
