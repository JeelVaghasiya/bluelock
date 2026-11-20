/* Dependency graph from the collector's pseudonymised topology. Layered left->right by
 * dependency depth; edges glow when both ends are degraded (the path an incident took). */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { shortSrc, STATE_COLOR } from "@/lib/format";
import type { HealthState, Topology } from "@/lib/types";
import { Panel, Skeleton } from "@/components/ui";

const RANK: Record<HealthState, number> = { excellent: 0, good: 1, fair: 2, poor: 3 };

export default function ServiceMap() {
  const openPanel = useStore((s) => s.openPanel);
  const [showHosts, setShowHosts] = useState(false);
  const { data } = useQuery({ queryKey: ["topology"], queryFn: () => api<Topology>("/api/topology"), refetchInterval: 5000 });

  const g = useMemo(() => {
    if (!data) return null;
    const nodes = data.nodes.filter((n) => showHosts || n.kind !== "host");
    const ids = new Set(nodes.map((n) => n.src));
    const edges = data.edges.filter((e) => ids.has(e.dependent) && ids.has(e.dependency));
    // depth = longest path from a node with no dependents (users' side) -> right
    const deps = new Map<string, string[]>();
    edges.forEach((e) => deps.set(e.dependent, [...(deps.get(e.dependent) ?? []), e.dependency]));
    const depth = new Map<string, number>();
    const visit = (id: string, d: number, seen: Set<string>) => {
      if (seen.has(id)) return;
      depth.set(id, Math.max(depth.get(id) ?? 0, d));
      seen.add(id);
      (deps.get(id) ?? []).forEach((x) => visit(x, d + 1, new Set(seen)));
    };
    const roots = nodes.filter((n) => !edges.some((e) => e.dependency === n.src));
    roots.forEach((r) => visit(r.src, 0, new Set()));
    nodes.forEach((n) => depth.has(n.src) || depth.set(n.src, 0));
    const cols = new Map<number, string[]>();
    nodes.forEach((n) => cols.set(depth.get(n.src)!, [...(cols.get(depth.get(n.src)!) ?? []), n.src]));
    const W = 220;
    const H = 74;
    const pos = new Map<string, { x: number; y: number }>();
    let maxRows = 0;
    [...cols.keys()].sort().forEach((c) => {
      const list = cols.get(c)!.sort();
      maxRows = Math.max(maxRows, list.length);
      list.forEach((id, i) => pos.set(id, { x: 30 + c * W, y: 30 + i * H }));
    });
    return { nodes, edges, pos, width: 60 + cols.size * W, height: 40 + maxRows * H };
  }, [data, showHosts]);

  if (!data || !g) return <div className="p-4"><Skeleton h={500} /></div>;
  const byId = new Map(g.nodes.map((n) => [n.src, n]));
  return (
    <div className="mx-auto max-w-[1680px] space-y-3 p-4">
      <Panel
        title="Service map"
        subtitle="arrows point from a component to what it depends on · topology arrives pseudonymised from the collector"
        right={
          <label className="flex items-center gap-2 text-[12px] text-t2">
            <input type="checkbox" checked={showHosts} onChange={(e) => setShowHosts(e.target.checked)} /> show hosts
          </label>
        }
      >
        <div className="overflow-auto">
          <svg width={g.width} height={g.height} className="block">
            <defs>
              <marker id="arr" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="var(--border-strong)" />
              </marker>
              <marker id="arr-hot" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="var(--poor)" />
              </marker>
            </defs>
            {g.edges.map((e, i) => {
              const a = g.pos.get(e.dependent)!;
              const b = g.pos.get(e.dependency)!;
              const sa = byId.get(e.dependent)?.state ?? "excellent";
              const sb = byId.get(e.dependency)?.state ?? "excellent";
              const hot = RANK[sa] >= 2 && RANK[sb] >= 2;
              const x1 = a.x + 170;
              const y1 = a.y + 22;
              const x2 = b.x;
              const y2 = b.y + 22;
              const mx = (x1 + x2) / 2;
              return (
                <g key={i}>
                  <path
                    d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 2},${y2}`}
                    fill="none"
                    stroke={hot ? "var(--poor)" : "var(--border-strong)"}
                    strokeWidth={hot ? 2.2 : 1.3}
                    strokeDasharray={e.kind === "routes_via" ? "5 4" : undefined}
                    markerEnd={`url(#${hot ? "arr-hot" : "arr"})`}
                    opacity={hot ? 1 : 0.8}
                  />
                </g>
              );
            })}
            {g.nodes.map((n) => {
              const p = g.pos.get(n.src)!;
              const st = n.state ?? "excellent";
              return (
                <g key={n.src} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" onClick={() => openPanel({ kind: "source", id: n.src })}>
                  <rect width={170} height={44} rx={8} fill="var(--panel)" stroke={STATE_COLOR[st]} strokeWidth={RANK[st] >= 2 ? 2 : 1.2} />
                  <rect width={5} height={44} rx={2} fill={STATE_COLOR[st]} />
                  <text x={14} y={19} fontSize={12} fontWeight={600} fill="var(--text-1)" fontFamily="JetBrains Mono, monospace">
                    {shortSrc(n.src)}
                  </text>
                  <text x={14} y={34} fontSize={11} fill="var(--text-3)">
                    {n.role} · {n.kind}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
        <div className="mt-2 flex gap-4 text-[11.5px] text-t3">
          <span>── depends_on</span>
          <span>- - routes_via</span>
          <span style={{ color: "var(--poor)" }}>━ degraded path (both ends fair/poor)</span>
        </div>
      </Panel>
    </div>
  );
}
