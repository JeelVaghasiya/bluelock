import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago, fmtMetric, STATE_COLOR } from "@/lib/format";
import type { HealthState, Source } from "@/lib/types";
import { HexMap } from "@/components/HexMap";
import type { GroupBy } from "@/components/HexMap";
import { Panel, Pseudo, Skeleton, StateBadge, Tabs } from "@/components/ui";

const STATES: HealthState[] = ["poor", "fair", "good", "excellent"];

export default function Infrastructure() {
  const theme = useStore((s) => s.theme);
  const openPanel = useStore((s) => s.openPanel);
  const panel = useStore((s) => s.panel);
  const [groupBy, setGroupBy] = useState<GroupBy>("role");
  const [kind, setKind] = useState<"all" | "host" | "svc" | "link" | "device">("all");
  const [only, setOnly] = useState<HealthState | null>(null);
  const [q, setQ] = useState("");
  const { data } = useQuery({ queryKey: ["sources"], queryFn: () => api<{ sources: Source[] }>("/api/sources"), refetchInterval: 5000 });

  const all = data?.sources ?? [];
  const list = useMemo(
    () =>
      all.filter(
        (s) => (kind === "all" || s.kind === kind) && (!only || s.state === only) && (!q || s.src.includes(q) || s.role.includes(q) || (s.collector_id ?? "").includes(q)),
      ),
    [all, kind, only, q],
  );
  const counts = useMemo(() => Object.fromEntries(STATES.map((st) => [st, all.filter((s) => s.state === st).length])), [all]);

  if (!data) return <div className="p-4"><Skeleton h={500} /></div>;
  return (
    <div className="mx-auto max-w-[1680px] space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <input className="input w-64" placeholder="filter: role, site, code…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Tabs value={kind} onChange={setKind} options={[{ v: "all", label: "all" }, { v: "host", label: "hosts" }, { v: "svc", label: "services" }, { v: "link", label: "links" }, { v: "device", label: "devices" }]} />
        <div className="flex gap-1">
          {STATES.map((st) => (
            <button
              key={st}
              onClick={() => setOnly(only === st ? null : st)}
              className="inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[11.5px] font-medium capitalize transition-opacity"
              style={{ borderColor: STATE_COLOR[st], color: STATE_COLOR[st], opacity: only && only !== st ? 0.4 : 1, background: only === st ? `color-mix(in srgb, ${STATE_COLOR[st]} 12%, transparent)` : undefined }}
            >
              {st} <b className="tnum">{counts[st]}</b>
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 text-[12px] text-t3">
          group by
          <Tabs value={groupBy} onChange={setGroupBy} options={[{ v: "role", label: "role" }, { v: "collector_id", label: "site" }, { v: "kind", label: "kind" }, { v: "state", label: "health" }]} />
        </div>
      </div>
      <Panel title="Host map" subtitle={`${list.length} components · colour = health · click a tile for details`}>
        <HexMap sources={list} groupBy={groupBy} size="lg" selected={panel?.id} onSelect={(s) => openPanel({ kind: "source", id: s.src })} dark={theme === "dark"} />
      </Panel>
      <Panel title="Component list" pad={false}>
        <div className="max-h-[480px] overflow-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Component</th>
                <th>Kind</th>
                <th>Site</th>
                <th>State</th>
                <th>Reason</th>
                <th>Key signals</th>
                <th>Since</th>
              </tr>
            </thead>
            <tbody>
              {list
                .slice()
                .sort((a, b) => STATES.indexOf(a.state) - STATES.indexOf(b.state))
                .map((s) => (
                  <tr key={s.src} className="cursor-pointer" onClick={() => openPanel({ kind: "source", id: s.src })}>
                    <td><Pseudo src={s.src} role={s.role} /></td>
                    <td className="text-t2">{s.kind}</td>
                    <td className="mono text-t2">{s.collector_id}</td>
                    <td><StateBadge state={s.state} stale={s.stale} size="sm" /></td>
                    <td className="max-w-[320px] truncate text-t2">{s.reason}</td>
                    <td className="text-[11.5px]">
                      {Object.entries(s.signals)
                        .slice(0, 3)
                        .map(([m, sig]) => (
                          <span key={m} className="mr-2" style={{ color: STATE_COLOR[sig.state] }}>
                            {m} {fmtMetric(m, sig.value)}
                          </span>
                        ))}
                    </td>
                    <td className="text-t3">{ago(s.since)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
