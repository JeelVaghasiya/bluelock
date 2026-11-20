import { create } from "zustand";
import type { Me } from "./types";

export type Theme = "light" | "dark";
export type Range = "live" | "15m" | "1h" | "4h";
export const RANGE_MIN: Record<Range, number> = { live: 15, "15m": 15, "1h": 60, "4h": 240 };

function initialTheme(): Theme {
  try {
    const t = localStorage.getItem("tl_theme");
    if (t === "light" || t === "dark") return t;
  } catch {
    /* ignore */
  }
  return "light";
}

export interface LiveSample {
  kind: string;
  ms: number;
  at: number;
}

interface State {
  me: Me | null;
  setMe: (m: Me | null) => void;
  theme: Theme;
  toggleTheme: () => void;
  range: Range;
  setRange: (r: Range) => void;
  ws: "connecting" | "live" | "down" | "demo";
  setWs: (s: State["ws"]) => void;
  lagMs: number | null;
  latency: LiveSample[];
  pushLatency: (s: LiveSample[]) => void;
  lastPayloadAt: number | null;
  touchPayload: () => void;
  navCollapsed: boolean;
  toggleNav: () => void;
  panel: { kind: "source"; id: string } | null;
  openPanel: (p: State["panel"]) => void;
}

export const useStore = create<State>((set) => ({
  me: null,
  setMe: (me) => set({ me }),
  theme: initialTheme(),
  toggleTheme: () =>
    set((s) => {
      const theme = s.theme === "light" ? "dark" : "light";
      try {
        localStorage.setItem("tl_theme", theme);
      } catch {
        /* ignore */
      }
      return { theme };
    }),
  range: "live",
  setRange: (range) => set({ range }),
  ws: "connecting",
  setWs: (ws) => set({ ws }),
  lagMs: null,
  latency: [],
  pushLatency: (xs) =>
    set((s) => {
      const latency = [...s.latency, ...xs].slice(-300);
      const dash = latency.filter((x) => x.kind === "dash");
      return { latency, lagMs: dash.length ? dash[dash.length - 1].ms : s.lagMs };
    }),
  lastPayloadAt: null,
  touchPayload: () => set({ lastPayloadAt: Date.now() }),
  navCollapsed: true,
  toggleNav: () => set((s) => ({ navCollapsed: !s.navCollapsed })),
  panel: null,
  openPanel: (panel) => set({ panel }),
}));
