import { useState } from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ago, cx } from "@/lib/format";
import type { IncidentRow } from "@/lib/types";
import { Bar, Empty, Panel, PrioBadge, Pseudo, Skeleton, Tabs } from "@/components/ui";

export default function Incidents() {
  const nav = useNavigate();
  const [status, setStatus] = useState<"active" | "resolved" | "all">("active");
  const { data } = useQuery({ queryKey: ["incidents"], queryFn: () => api<{ incidents: IncidentRow[] }>("/api/incidents"), refetchInterval: 5000 });
  if (!data) return <div className="p-4"><Skeleton h={400} /></div>;
  const rows = data.incidents.filter((i) => status === "all" || (status === "active" ? i.status !== "resolved" : i.status === "resolved"));
  return (
    <div className="mx-auto max-w-[1680px] space-y-3 p-4">
      <Panel
        title="Incidents"
        subtitle="each incident is many correlated alerts with one root cause"
        right={<Tabs value={status} onChange={setStatus} options={[{ v: "active", label: "active" }, { v: "resolved", label: "resolved" }, { v: "all", label: "all" }]} />}
        pad={false}
      >
        {rows.length ? (
          <table className="table">
            <thead>
              <tr>
                <th className="w-12">Prio</th>
                <th>Title</th>
                <th>Root cause</th>
                <th className="w-24">Status</th>
                <th className="w-32">Confidence</th>
                <th className="w-16">Alerts</th>
                <th className="w-32">Opened</th>
                <th className="w-32">Resolved</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.incident_id} className="cursor-pointer" onClick={() => nav(`/incidents/${i.incident_id}`)}>
                  <td><PrioBadge p={i.priority} /></td>
                  <td>
                    <div className="font-medium">{i.title}</div>
                    <div className="mono text-[11px] text-t3">{i.incident_id} · {i.rule?.replace(/_/g, " ")}</div>
                  </td>
                  <td><Pseudo src={i.root_cause_src} /></td>
                  <td>
                    <span className={cx("rounded-full px-2 py-0.5 text-[11px] font-semibold", i.status === "resolved" ? "bg-excellent/12 text-excellent" : i.status === "acknowledged" ? "bg-low/15 text-low" : "bg-poor/12 text-poor")}>
                      {i.status}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <Bar value={i.confidence ?? 0} max={1} color="var(--accent)" />
                      <span className="tnum text-[12px]">{Math.round((i.confidence ?? 0) * 100)}%</span>
                    </div>
                  </td>
                  <td className="tnum">{i.alert_count}</td>
                  <td className="text-t2">{ago(i.opened_ts)}</td>
                  <td className="text-t3">{i.resolved_ts ? ago(i.resolved_ts) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>Nothing here</Empty>
        )}
      </Panel>
    </div>
  );
}
