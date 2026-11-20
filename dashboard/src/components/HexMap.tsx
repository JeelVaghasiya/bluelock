/* Animated hexagon host map. Every hex keeps its identity across regroupings, so changing
 * "group by" (role / collector / kind / health) glides each tile to its new cluster. */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { cx, METRIC_LABEL, shortSrc, STATE_GLYPH } from "@/lib/format";
import type { HealthState, Source } from "@/lib/types";

const FILL: Record<HealthState, string> = {
  excellent: "#4cc27a",
  good: "#a3d977",
  fair: "#f5a640",
  poor: "#ea5455",
};
const FILL_DARK: Record<HealthState, string> = {
  excellent: "#2f9e5f",
  good: "#7fb556",
  fair: "#e08e22",
  poor: "#d63f45",
};
const GROUP_TINTS = ["#4f8ff7", "#3fb68b", "#f2994a", "#e86a92", "#9b8afb", "#56ccf2", "#f2c94c", "#8d9bb0"];

export type GroupBy = "role" | "collector_id" | "kind" | "state";

interface Placed {
  src: Source;
  x: number;
  y: number;
}
interface GroupBox {
  key: string;
  label: string;
  x: number;
  y: number;
  w: number;
  h: number;
  tint: string;
  n: number;
  worst: HealthState;
}

const RANK: Record<HealthState, number> = { excellent: 0, good: 1, fair: 2, poor: 3 };
const STATE_ORDER: HealthState[] = ["poor", "fair", "good", "excellent"];

function hexPath(r: number): string {
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const a = (Math.PI / 180) * (60 * i - 90);
    pts.push(`${(r * Math.cos(a)).toFixed(2)},${(r * Math.sin(a)).toFixed(2)}`);
  }
  return `M${pts.join("L")}Z`;
}

function layout(sources: Source[], groupBy: GroupBy, width: number, r: number) {
  const groups = new Map<string, Source[]>();
  for (const s of sources) {
    const k = String(s[groupBy] ?? "other");
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(s);
  }
  let keys = [...groups.keys()];
  if (groupBy === "state") keys = STATE_ORDER.filter((k) => groups.has(k));
  else keys.sort((a, b) => groups.get(b)!.length - groups.get(a)!.length || a.localeCompare(b));

  const hw = Math.sqrt(3) * r; // hex width
  const vs = 1.5 * r; // vertical step
  const pad = 14;
  const labelH = 22;
  const gap = 14;
  const placed: Placed[] = [];
  const boxes: GroupBox[] = [];
  let cx = 0;
  let cy = 0;
  let rowH = 0;
  keys.forEach((key, gi) => {
    const items = [...groups.get(key)!].sort((a, b) => RANK[b.state] - RANK[a.state] || a.src.localeCompare(b.src));
    const n = items.length;
    const cols = Math.max(2, Math.min(n, Math.ceil(Math.sqrt(n * 1.8))));
    const rows = Math.ceil(n / cols);
    const w = cols * hw + (rows > 1 ? hw / 2 : 0) + pad * 2;
    const h = (rows - 1) * vs + 2 * r + pad * 2 + labelH;
    if (cx > 0 && cx + w > width) {
      cx = 0;
      cy += rowH + gap;
      rowH = 0;
    }
    items.forEach((s, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      placed.push({ src: s, x: cx + pad + hw / 2 + col * hw + (row % 2 ? hw / 2 : 0), y: cy + labelH + pad + r + row * vs });
    });
    const worst = items.reduce<HealthState>((acc, s) => (RANK[s.state] > RANK[acc] ? s.state : acc), "excellent");
    const label = groupBy === "kind" ? { host: "Hosts", svc: "Services", link: "Network links" }[key] ?? key : key;
    boxes.push({ key, label, x: cx, y: cy, w, h, tint: groupBy === "state" ? FILL[key as HealthState] : GROUP_TINTS[gi % GROUP_TINTS.length], n, worst });
    cx += w + gap;
    rowH = Math.max(rowH, h);
  });
  return { placed, boxes, height: cy + rowH + 2 };
}

export function HexMap({
  sources, groupBy, onSelect, selected, size = "md", dark,
}: {
  sources: Source[]; groupBy: GroupBy; onSelect?: (s: Source) => void; selected?: string | null; size?: "sm" | "md" | "lg"; dark?: boolean;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<{ s: Source; x: number; y: number } | null>(null);
  const r = size === "sm" ? 13 : size === "lg" ? 22 : 17;

  useLayoutEffect(() => {
    if (!wrap.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(240, e[0].contentRect.width)));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  const { placed, boxes, height } = useMemo(() => layout(sources, groupBy, width, r), [sources, groupBy, width, r]);
  const path = useMemo(() => hexPath(r - 1.6), [r]);
  const fills = dark ? FILL_DARK : FILL;

  // first paint: start every hex at the centre so the map "assembles" on load
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const t = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(t);
  }, []);

  return (
    <div ref={wrap} className="relative w-full">
      <svg width={width} height={height} className="block overflow-visible">
        {boxes.map((b) => (
          <g key={`bg-${groupBy}-${b.key}`} className="hex-group" style={{ transform: `translate(${b.x}px, ${b.y}px)` }}>
            <rect width={b.w} height={b.h} rx={8} fill={b.tint} fillOpacity={dark ? 0.1 : 0.075} stroke={b.tint} strokeOpacity={0.18} />
            <text x={10} y={16} fontSize={11.5} fontWeight={600} fill="var(--text-2)">
              {b.label}
              <tspan fill="var(--text-3)" fontWeight={400}>
                {"  "}
                {b.n}
              </tspan>
            </text>
          </g>
        ))}
        {placed.map(({ src: s, x, y }) => (
          <g
            key={s.src}
            className="hex"
            style={{ transform: mounted ? `translate(${x}px, ${y}px)` : `translate(${width / 2}px, ${height / 2}px) scale(0.2)`, opacity: mounted ? 1 : 0 }}
            onMouseEnter={() => setHover({ s, x, y })}
            onMouseLeave={() => setHover(null)}
            onClick={() => onSelect?.(s)}
          >
            <path
              d={path}
              fill={s.stale ? "var(--stale)" : fills[s.state]}
              stroke={selected === s.src ? "var(--text-1)" : "var(--panel)"}
              strokeWidth={selected === s.src ? 2.4 : 1.6}
              style={{ transition: "fill 600ms ease" }}
            />
            {s.kind !== "host" && <circle r={2.4} fill="rgba(255,255,255,0.85)" />}
          </g>
        ))}
      </svg>
      {hover && (
        <div
          className="pointer-events-none absolute z-20 w-64 rounded-md border border-line-strong bg-panel p-2.5 text-[12px] shadow-lg"
          style={{ left: Math.min(width - 260, hover.x + 18), top: hover.y + 14 }}
        >
          <div className="mb-1 flex items-center justify-between">
            <span className="mono font-semibold">{shortSrc(hover.s.src)}</span>
            <span className="text-t3">{hover.s.role}</span>
          </div>
          <div className={cx("mb-1.5 font-medium capitalize")} style={{ color: FILL[hover.s.state] }}>
            {STATE_GLYPH[hover.s.state]} {hover.s.state}
          </div>
          <div className="mb-1.5 text-t2">{hover.s.reason}</div>
          {Object.entries(hover.s.signals)
            .slice(0, 4)
            .map(([m, sig]) => (
              <div key={m} className="flex justify-between text-[11.5px]">
                <span className="text-t3">{METRIC_LABEL[m] ?? m}</span>
                <span className="mono" style={{ color: FILL[sig.state] }}>
                  {m === "error_rate" ? (sig.value * 100).toFixed(2) + "%" : sig.value.toFixed(1)}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
