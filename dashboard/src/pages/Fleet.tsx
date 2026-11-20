import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { ago, cx, fmtMs, fmtNum } from "@/lib/format";
import type { Collector } from "@/lib/types";
import { KpiTile } from "@/components/KpiTile";
import { Panel, Skeleton } from "@/components/ui";

interface Delivery {
  ledger: { collector_id: string; min_seq: number; max_seq: number; received: number; gaps: number; js_duplicates?: number; db_conflicts?: number; last_assigned_seq?: number; buffer_depth?: number; last_received?: string }[];
  recent: { payload_id: string; seq: number; result: string; lane: string; attempt: number; received_at: string; skew_ms: number | null }[];
}

export default function Fleet() {
  const me = useStore((s) => s.me);
  const ops = me?.role === "platform-ops";
  const { data } = useQuery({ queryKey: ["fleet"], queryFn: () => api<{ collectors: Collector[]; scope: string }>("/api/fleet"), refetchInterval: 3000 });
  const del = useQuery({ queryKey: ["delivery"], queryFn: () => api<Delivery>("/api/delivery"), refetchInterval: 3000, enabled: !ops });
  if (!data) return <div className="p-4"><Skeleton h={400} /></div>;
  const cs = data.collectors;
  const online = cs.filter((c) => c.state === "online").length;
  const buffered = cs.reduce((a, c) => a + (c.buffer_depth ?? 0), 0);
  const drift = cs.filter((c) => c.expected_config_hash && c.config_hash !== c.expected_config_hash).length;
  const dups = (del.data?.ledger ?? []).reduce((a, l) => a + (l.js_duplicates ?? 0) + (l.db_conflicts ?? 0), 0);
  const gaps = (del.data?.ledger ?? []).reduce((a, l) => a + l.gaps, 0);
  return (
    <div className="mx-auto max-w-[1680px] space-y-3 p-4">
      {ops && (
        <div className="rounded-md border border-boundary/40 bg-boundary/10 px-3 py-2 text-[12.5px]">
          Platform-ops view: collector health across all tenants. Tenant telemetry, incidents and events are not visible to this role.
        </div>
      )}
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <KpiTile label="Collectors online" value={online} tone={online === cs.length ? "good" : "bad"} sub={`${cs.length} registered · scope ${data.scope}`} />
        <KpiTile label="Buffered payloads" value={buffered} tone={buffered ? "warn" : "brand"} sub="sealed on-site, waiting for the SaaS" />
        <KpiTile label="Sequence gaps" value={gaps} tone={gaps ? "bad" : "good"} sub="per-collector seq ledger" />
        <KpiTile label="Duplicates dropped" value={dups} tone="calm" sub="JetStream window + DB unique key" />
      </div>
      <Panel title="Collectors" subtitle={drift ? `${drift} collector(s) with config drift` : "versions and config hashes in sync"} pad={false}>
        <table className="table">
          <thead>
            <tr>
              {ops && <th>Tenant</th>}
              <th>Collector</th>
              <th>Region</th>
              <th>State</th>
              <th>Last heartbeat</th>
              <th>Version</th>
              <th>Config</th>
              <th className="text-right">CPU</th>
              <th className="text-right">Mem</th>
              <th className="text-right">Buffer</th>
              <th className="text-right">Seq acked / assigned</th>
              <th className="text-right">Clock skew</th>
            </tr>
          </thead>
          <tbody>
            {cs.map((c) => (
              <tr key={`${c.tenant_id}-${c.collector_id}`}>
                {ops && <td>{c.tenant_id}</td>}
                <td className="mono font-semibold">{c.collector_id}</td>
                <td>{c.region}</td>
                <td>
                  <span className={cx("rounded-full px-2 py-0.5 text-[11px] font-semibold", c.state === "online" ? "bg-excellent/12 text-excellent" : "bg-poor/12 text-poor")}>
                    {c.state === "online" ? "●" : "■"} {c.state}
                  </span>
                  {c.state !== "online" && <div className="text-[11px] text-t3">{c.state_reason}</div>}
                </td>
                <td className="text-t2">{ago(c.last_hb_event_ts)}</td>
                <td className="mono">{c.version ?? "—"}</td>
                <td className="mono">
                  {c.config_hash ?? "—"}
                  {c.expected_config_hash && c.expected_config_hash !== c.config_hash && <span className="ml-1 text-fair">drift</span>}
                </td>
                <td className="text-right tnum">{c.cpu?.toFixed(1) ?? "—"}%</td>
                <td className="text-right tnum">{c.mem_mb?.toFixed(0) ?? "—"} MB</td>
                <td className="text-right tnum">{c.buffer_depth ?? 0}</td>
                <td className="text-right mono">{fmtNum(c.last_acked_seq ?? 0, 0)} / {fmtNum(c.last_assigned_seq ?? 0, 0)}</td>
                <td className="text-right tnum">{c.skew_ms != null ? fmtMs(c.skew_ms) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      {!ops && del.data && (
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel title="Delivery ledger" subtitle="exactly-once: what the collector assigned vs what landed" pad={false}>
            <table className="table">
              <thead>
                <tr>
                  <th>Collector</th>
                  <th className="text-right">Seq range</th>
                  <th className="text-right">Received</th>
                  <th className="text-right">Gaps</th>
                  <th className="text-right">JS dups</th>
                  <th className="text-right">DB conflicts</th>
                </tr>
              </thead>
              <tbody>
                {del.data.ledger.map((l) => (
                  <tr key={l.collector_id}>
                    <td className="mono">{l.collector_id}</td>
                    <td className="mono text-right">{l.min_seq}–{l.max_seq}</td>
                    <td className="text-right tnum">{l.received.toLocaleString()}</td>
                    <td className={cx("text-right tnum font-semibold", l.gaps ? "text-poor" : "text-excellent")}>{l.gaps}</td>
                    <td className="text-right tnum">{l.js_duplicates ?? 0}</td>
                    <td className="text-right tnum">{l.db_conflicts ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title="Recent ingest attempts" subtitle="every attempt is audited — retries are byte-identical" pad={false}>
            <div className="max-h-72 overflow-auto">
              <table className="table">
                <thead>
                  <tr>
                    <th>Payload</th>
                    <th className="text-right">Seq</th>
                    <th>Result</th>
                    <th>Lane</th>
                    <th className="text-right">Attempt</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {del.data.recent.map((r, i) => (
                    <tr key={i}>
                      <td><a className="mono text-accent hover:underline" href={`/trust?payload=${r.payload_id}`}>{r.payload_id.slice(0, 13)}</a></td>
                      <td className="text-right mono">{r.seq}</td>
                      <td className={cx("font-medium", r.result === "stored" ? "text-excellent" : r.result === "duplicate" ? "text-low" : "text-poor")}>{r.result}</td>
                      <td>{r.lane}</td>
                      <td className="text-right tnum">{r.attempt}</td>
                      <td className="text-t3">{ago(r.received_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}
