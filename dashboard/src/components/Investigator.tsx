/* AI Investigator panel: runs the SRE agent over this incident's metadata and shows a grounded
 * diagnosis (cited signals), the tool calls it made, and the action it queued for human approval. */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Bot, ChevronDown, ChevronRight, Lock, RotateCcw, ShieldCheck, Sparkles, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { cx, fmtMetric, METRIC_LABEL } from "@/lib/format";
import type { Investigation } from "@/lib/types";
import { Panel, Pseudo } from "@/components/ui";

const ENGINE_NAME: Record<string, string> = { gemini: "Gemini", claude: "Claude" };
const RISK: Record<string, string> ={ low: "text-excellent", medium: "text-fair", high: "text-poor" };

export function InvestigatorPanel({ id, resolved }: { id: string; resolved: boolean }) {
  const qc = useQueryClient();
  const [showTrace, setShowTrace] = useState(false);
  const q = useQuery({
    queryKey: ["investigation", id],
    queryFn: () => api<{ investigation: Investigation | null; engine_available: string }>(`/api/incidents/${id}/investigation`),
    staleTime: 60_000,
  });
  const run = useMutation({
    mutationFn: () => api<Investigation>(`/api/incidents/${id}/investigate`, { method: "POST" }),
    onSuccess: (r) => {
      qc.setQueryData(["investigation", id], { investigation: r, engine_available: q.data?.engine_available ?? r.engine });
      qc.invalidateQueries({ queryKey: ["incident", id] });
      toast.success("Investigation complete", { description: r.queued?.action_id ? "Action queued for approval" : "No action queued" });
    },
    onError: (e: Error) => toast.error("Investigation failed", { description: e.message }),
  });
  const inv = q.data?.investigation;
  const engine = q.data?.engine_available ?? "deterministic";
  const d = inv?.diagnosis;

  return (
    <Panel
      title={<span className="inline-flex items-center gap-1.5"><Bot size={15} className="text-accent" /> AI Investigator</span>}
      subtitle={inv ? `${inv.engine !== "deterministic" ? inv.model ?? ENGINE_NAME[inv.engine] : "in-region deterministic engine"} · ${(inv.ms / 1000).toFixed(1)}s` : engine !== "deterministic" ? `${ENGINE_NAME[engine] ?? engine} · metadata only · human-approved actions` : "deterministic engine · metadata only"}
      right={
        <button className="btn" disabled={run.isPending || resolved} onClick={() => run.mutate()}>
          {run.isPending ? <Sparkles size={14} className="animate-pulse" /> : inv ? <RotateCcw size={14} /> : <Sparkles size={14} />}
          {run.isPending ? "Investigating…" : inv ? "Re-run" : "Investigate"}
        </button>
      }
    >
      {!inv && !run.isPending && (
        <div className="space-y-2 text-[12px] text-t2">
          <p>Reads this incident through three read-only tools (incident context, topology, playbook rule), explains the cause citing each signal's z-score and weight, and queues at most one playbook action for a tenant-admin to approve.</p>
          <p className="inline-flex items-center gap-1.5 text-t3"><Lock size={12} /> Sees only pseudonymous metadata: no raw logs, user names or IPs exist in the SaaS.</p>
        </div>
      )}
      {run.isPending && !inv && <div className="h-24 animate-pulse rounded-md bg-panel-3" />}
      {inv && d && (
        <div className={cx("space-y-3", run.isPending && "opacity-60")}>
          <p className="text-[13px] leading-relaxed">{d.summary}</p>

          <div className="rounded-md border border-line p-2.5 text-[12px]">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-t3">Root cause</span> <Pseudo src={d.root_cause} />
              <span className="ml-auto text-t3">confidence <b className="text-t1">{d.confidence}</b></span>
            </div>
            <div className="text-t2">{d.root_cause_reasoning}</div>
          </div>

          {d.citations.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-t3">Evidence cited</div>
              <div className="space-y-1">
                {d.citations.map((c, i) => (
                  <div key={i} className="flex items-center gap-2 text-[11.5px]">
                    <span className={cx("h-1.5 w-1.5 shrink-0 rounded-full", c.role === "root_cause" ? "bg-poor" : c.role === "impacted" ? "bg-fair" : "bg-t3")} />
                    <Pseudo src={c.src} />
                    <span className="truncate">{METRIC_LABEL[c.metric] ?? c.metric}</span>
                    <span className="mono ml-auto shrink-0 text-t2">
                      {fmtMetric(c.metric, c.value)} vs {fmtMetric(c.metric, c.baseline)} · z {c.z ?? "—"} · w {c.weight ?? "—"}
                    </span>
                  </div>
                ))}
              </div>
              <div className="mt-1.5 text-[11px] text-t3">layers agreeing: {d.layers_agreeing.join(", ") || "none"}</div>
            </div>
          )}

          <div className="rounded-md border border-accent/30 bg-accent-soft p-2.5 text-[12px]">
            <div className="flex items-center gap-2">
              <Wrench size={13} />
              <span className="mono font-semibold">{d.recommended_action}</span>
              <span className={cx("ml-auto text-[11px] font-semibold uppercase", RISK[d.risk])}>{d.risk} risk</span>
            </div>
            {d.action_rationale && <div className="mt-1 text-t2">{d.action_rationale}</div>}
            <div className="mt-1 text-t2"><b>Rollback:</b> {d.rollback_plan}</div>
            <div className="mt-1.5 inline-flex items-center gap-1.5 text-[11.5px]">
              <ShieldCheck size={13} className="text-excellent" />
              {inv.queued?.action_id ? `Queued for human approval (${inv.queued.state}${inv.queued.note ? ` · ${inv.queued.note}` : ""}) - nothing runs until a tenant-admin approves.`
                : inv.queued ? `Not queued: ${inv.queued.note ?? inv.queued.state}` : "Nothing queued - recommendation is to investigate."}
            </div>
            {inv.policy_note && <div className="mt-1 text-[11.5px] text-fair">Policy: {inv.policy_note}</div>}
          </div>

          {d.caveats.length > 0 && (
            <div className="space-y-1">
              {d.caveats.map((c, i) => (
                <div key={i} className="flex gap-1.5 text-[11.5px] text-fair"><AlertTriangle size={12} className="mt-0.5 shrink-0" />{c}</div>
              ))}
            </div>
          )}

          <button className="inline-flex items-center gap-1 text-[11.5px] text-t3 hover:text-t1" onClick={() => setShowTrace((v) => !v)}>
            {showTrace ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Agent trace · {inv.trace.filter((t) => t.step === "tool").length} tool calls
            {inv.usage.requests ? ` · ${inv.usage.requests} model calls · ${((inv.usage.input_tokens ?? 0) + (inv.usage.output_tokens ?? 0)).toLocaleString()} tokens` : ""}
          </button>
          {showTrace && (
            <div className="space-y-1.5 rounded-md border border-line p-2">
              {inv.trace.map((t, i) =>
                t.step === "tool" ? (
                  <div key={i} className="text-[11px]">
                    <span className={cx("mono font-semibold", t.ok ? "text-excellent" : "text-poor")}>{t.tool}</span>
                    <span className="mono text-t3">({Object.values(t.input ?? {}).join(", ")}) · {t.ms}ms</span>
                    <div className="mono truncate text-t3">{t.result_preview}</div>
                  </div>
                ) : (
                  <div key={i} className="text-[11px] italic text-t2">{t.text}</div>
                ),
              )}
              <div className="border-t border-line pt-1.5 text-[11px] text-t3">
                Data sent: {inv.data_sent}
                {inv.fallback_reason && inv.engine === "deterministic" ? ` · ${inv.fallback_reason}` : ""}
              </div>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}
