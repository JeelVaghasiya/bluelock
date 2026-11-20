import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { hhmmss } from "@/lib/format";
import { Panel, Skeleton } from "@/components/ui";

interface Row {
  id: number;
  actor: string;
  role: string;
  action: string;
  target: string | null;
  detail: Record<string, unknown>;
  at: string;
}

export default function Audit() {
  const { data } = useQuery({ queryKey: ["audit"], queryFn: () => api<{ audit: Row[] }>("/api/audit"), refetchInterval: 5000 });
  if (!data) return <div className="p-4"><Skeleton h={400} /></div>;
  return (
    <div className="mx-auto max-w-[1680px] p-4">
      <Panel title="Audit log" subtitle="who did what — approvals, rollbacks, threshold changes, isolation tests" pad={false}>
        <table className="table">
          <thead>
            <tr>
              <th className="w-24">Time</th>
              <th>Actor</th>
              <th>Role</th>
              <th>Action</th>
              <th>Target</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {data.audit.map((a) => (
              <tr key={a.id}>
                <td className="mono text-t3">{hhmmss(a.at)}</td>
                <td>{a.actor}</td>
                <td className="text-t2">{a.role}</td>
                <td className="font-medium">{a.action}</td>
                <td className="mono">{a.target ?? "—"}</td>
                <td className="mono max-w-[420px] truncate text-[11px] text-t3">{JSON.stringify(a.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}
