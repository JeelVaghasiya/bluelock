import type { ReactNode } from "react";
import { Fingerprint, Info, ShieldCheck } from "lucide-react";
import { cx, PRIO_COLOR, SEV_COLOR, shortSrc, STATE_COLOR, STATE_GLYPH } from "@/lib/format";
import type { HealthState, Severity } from "@/lib/types";

export function Panel({
  title, subtitle, right, children, className, bodyClass, pad = true,
}: {
  title?: ReactNode; subtitle?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string; pad?: boolean;
}) {
  return (
    <section className={cx("panel flex min-w-0 flex-col", className)}>
      {(title || right) && (
        <header className="flex items-center gap-2 px-3.5 pt-3 pb-1.5">
          <div className="min-w-0 flex-1">
            <div className="panel-title truncate">{title}</div>
            {subtitle && <div className="truncate text-[11.5px] text-t3">{subtitle}</div>}
          </div>
          {right}
        </header>
      )}
      <div className={cx("min-h-0 flex-1", pad && "px-3.5 pb-3", bodyClass)}>{children}</div>
    </section>
  );
}

export function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <div className={cx("rise", className)}>
      <div className="section-bar">{title}</div>
      <div className="rounded-b-lg border border-t-0 border-line bg-canvas p-2.5 pt-3.5">{children}</div>
    </div>
  );
}

export function StateBadge({ state, stale, size = "md" }: { state: HealthState; stale?: boolean; size?: "sm" | "md" }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full font-medium capitalize",
        size === "sm" ? "px-1.5 py-px text-[11px]" : "px-2 py-0.5 text-[11.5px]",
        stale && "hatch",
      )}
      style={{ color: STATE_COLOR[state], background: `color-mix(in srgb, ${STATE_COLOR[state]} 13%, transparent)` }}
      title={stale ? "stale: no data for 30 s" : undefined}
    >
      <span aria-hidden>{STATE_GLYPH[state]}</span>
      {state}
      {stale && <span className="text-t3">· stale</span>}
    </span>
  );
}

export function SevBadge({ sev }: { sev: Severity }) {
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-px text-[11px] font-semibold capitalize"
      style={{ color: SEV_COLOR[sev], background: `color-mix(in srgb, ${SEV_COLOR[sev]} 14%, transparent)` }}
    >
      {sev}
    </span>
  );
}

export function PrioBadge({ p }: { p: string }) {
  return (
    <span className="inline-flex h-5 min-w-7 items-center justify-center rounded px-1.5 text-[11px] font-bold text-white" style={{ background: PRIO_COLOR[p] ?? "var(--info)" }}>
      {p}
    </span>
  );
}

/** Pseudonymous identifier. The real name exists only on-site; the SaaS never knows it. */
export function Pseudo({ src, role, className }: { src?: string | null; role?: string | null; className?: string }) {
  if (!src) return <span className="text-t3">—</span>;
  return (
    <span
      className={cx("inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-px align-middle", className)}
      style={{ borderColor: "color-mix(in srgb, var(--pseudo) 35%, transparent)", background: "color-mix(in srgb, var(--pseudo) 8%, transparent)" }}
      title={`${src}\nPseudonymous ID (HMAC on-site). The real name exists only on-site — re-identify from the collector's local UI.`}
    >
      <Fingerprint size={11} className="shrink-0 text-pseudo" />
      <span className="mono truncate text-[11.5px] text-t1">{shortSrc(src)}</span>
      {role && <span className="truncate text-[11px] text-t3">{role}</span>}
    </span>
  );
}

export function Redacted({ text }: { text: string }) {
  const parts = text.split(/(<[^>]+>)/g);
  return (
    <span className="mono text-[12px]">
      {parts.map((p, i) =>
        p.startsWith("<") && p.endsWith(">") ? (
          <span
            key={i}
            className="mx-px rounded px-1"
            style={{
              color: p.startsWith("<secret") ? "var(--redact)" : p.match(/^<(u|ip|h|svc|link|person)/) ? "var(--pseudo)" : "var(--text-3)",
              background: p.startsWith("<secret") ? "color-mix(in srgb, var(--redact) 12%, transparent)" : "color-mix(in srgb, var(--pseudo) 8%, transparent)",
            }}
          >
            {p.replace(/^</, "‹").replace(/>$/, "›")}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </span>
  );
}

export function Provenance({ payloadId, seq }: { payloadId?: string; seq?: number }) {
  if (!payloadId) return null;
  return (
    <a
      href={`/trust?payload=${payloadId}`}
      className="mono inline-flex items-center gap-1 rounded border border-line px-1.5 py-px text-[11px] text-t3 hover:text-accent"
      title="Provenance: open the ingest audit for this payload (its SHA-256 matches the on-site egress log)"
    >
      <ShieldCheck size={11} />
      {payloadId.slice(0, 8)}
      {seq !== undefined && <span>· seq {seq}</span>}
    </a>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-20 items-center justify-center gap-2 text-[12px] text-t3">
      <Info size={14} />
      {children}
    </div>
  );
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div className="animate-pulse rounded-md bg-panel-2" style={{ height: h }} />;
}

export function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel-3">
      <div className="h-full rounded-full transition-[width] duration-700" style={{ width: `${Math.min(100, (value / (max || 1)) * 100)}%`, background: color }} />
    </div>
  );
}

export function Kv({ k, v, mono }: { k: ReactNode; v: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-[12.5px]">
      <span className="text-t3">{k}</span>
      <span className={cx("text-right text-t1", mono && "mono")}>{v}</span>
    </div>
  );
}

export function Tabs<T extends string>({ value, options, onChange }: { value: T; options: { v: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-md border border-line bg-panel-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.v}
          onClick={() => onChange(o.v)}
          className={cx(
            "h-6 rounded px-2.5 text-[11.5px] font-medium transition-colors",
            value === o.v ? "bg-panel text-t1 shadow-sm" : "text-t3 hover:text-t2",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
