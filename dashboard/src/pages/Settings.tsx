import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FlaskConical, Save, Square, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Panel, Skeleton } from "@/components/ui";

interface Th {
  defaults: Record<string, { stat: string; excellent: number; good: number; fair: number; lower_is_worse?: boolean; unit?: string }>;
  tenant_overrides: Record<string, Partial<Record<"excellent" | "good" | "fair", number>>>;
  updated_by: string | null;
}
interface Scen {
  scenarios: { kind: string; label?: string; target?: string; description?: string }[] | Record<string, unknown>;
  status: { active?: { kind: string; target: string }[] };
}

export default function Settings() {
  const qc = useQueryClient();
  const me = useStore((s) => s.me);
  const admin = me?.role === "tenant-admin";
  const { data } = useQuery({ queryKey: ["thresholds"], queryFn: () => api<Th>("/api/thresholds") });
  const sc = useQuery({ queryKey: ["scenarios"], queryFn: () => api<Scen>("/api/sim/scenarios"), refetchInterval: 5000, retry: 0 });
  const [edit, setEdit] = useState<Record<string, Record<string, number>>>({});
  const save = useMutation({
    mutationFn: () => api("/api/thresholds", { method: "PUT", body: edit }),
    onSuccess: () => {
      toast.success("Thresholds saved (audited)");
      qc.invalidateQueries({ queryKey: ["thresholds"] });
    },
    onError: (e: Error) => toast.error("Save failed", { description: e.message }),
  });
  const inject = useMutation({
    mutationFn: (kind: string) => api("/api/sim/inject", { method: "POST", body: { kind } }),
    onSuccess: (_r, k) => toast.success(`Injected ${k}`, { description: "watch the Overview — first alerts after the persistence window" }),
    onError: (e: Error) => toast.error("Inject failed", { description: e.message }),
  });
  const clear = useMutation({ mutationFn: () => api("/api/sim/clear", { method: "POST" }), onSuccess: () => toast.success("Faults cleared") });

  if (!data) return <div className="p-4"><Skeleton h={400} /></div>;
  const list = Array.isArray(sc.data?.scenarios)
    ? (sc.data!.scenarios as { kind: string; label?: string; target?: string }[])
    : Object.entries((sc.data?.scenarios as Record<string, { description?: string }>) ?? {}).map(([kind, v]) => ({ kind, label: v?.description ?? kind, target: undefined }));
  return (
    <div className="mx-auto grid max-w-[1680px] gap-3 p-4 xl:grid-cols-[1fr_420px]">
      <Panel
        title="Health thresholds"
        subtitle="tenant overrides on top of config/thresholds.yaml · changes are audited"
        right={
          admin && (
            <button className="btn btn-primary" disabled={!Object.keys(edit).length || save.isPending} onClick={() => save.mutate()}>
              <Save size={14} /> Save
            </button>
          )
        }
        pad={false}
      >
        <table className="table">
          <thead>
            <tr>
              <th>Signal</th>
              <th>Stat</th>
              <th>Excellent &lt;</th>
              <th>Good &lt;</th>
              <th>Fair &lt;</th>
              <th>Poor</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.defaults).map(([m, t]) => (
              <tr key={m}>
                <td className="font-medium">{m}</td>
                <td className="text-t3">{t.stat}{t.lower_is_worse ? " (lower is worse)" : ""}</td>
                {(["excellent", "good", "fair"] as const).map((k) => (
                  <td key={k}>
                    <input
                      className="input h-7 w-24"
                      type="number"
                      step="any"
                      disabled={!admin}
                      defaultValue={data.tenant_overrides[m]?.[k] ?? t[k]}
                      onChange={(e) => setEdit((x) => ({ ...x, [m]: { ...(x[m] ?? {}), [k]: Number(e.target.value) } }))}
                    />
                  </td>
                ))}
                <td className="text-poor">{t.lower_is_worse ? `< ${t.fair}` : `≥ ${t.fair}`} {t.unit ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      <Panel title="Demo controls" subtitle="inject faults into the simulator (the stream an organiser would send)">
        {sc.isError ? (
          <div className="text-[12px] text-t3">Simulator not reachable from the API. Run <span className="mono">python scripts/tl.py demo &lt;scenario&gt;</span> on the site machine instead.</div>
        ) : (
          <div className="space-y-2">
            {list.map((s) => (
              <div key={s.kind} className="flex items-center gap-2 rounded-md border border-line p-2">
                <FlaskConical size={15} className="text-accent" />
                <div className="min-w-0 flex-1">
                  <div className="text-[12.5px] font-medium">{s.label ?? s.kind}</div>
                  <div className="mono text-[11px] text-t3">{s.kind}</div>
                </div>
                <button className="btn" disabled={!admin || inject.isPending} onClick={() => inject.mutate(s.kind)}>
                  <Zap size={13} /> Inject
                </button>
              </div>
            ))}
            <button className="btn w-full justify-center" disabled={!admin} onClick={() => clear.mutate()}>
              <Square size={13} /> Clear all faults
            </button>
            {!!sc.data?.status?.active?.length && (
              <div className="text-[12px] text-t2">Active: {sc.data.status.active.map((a) => a.kind).join(", ")}</div>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}
