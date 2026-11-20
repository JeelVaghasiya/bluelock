import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { cx } from "@/lib/format";

export type Tone = "bad" | "warn" | "good" | "brand" | "calm";

const TONES: Record<Tone, { bg: string; ink: string }> = {
  bad: { bg: "linear-gradient(140deg, #e23a5e 0%, #c81e4a 100%)", ink: "#fff" },
  warn: { bg: "linear-gradient(140deg, #f7a531 0%, #ee8a12 100%)", ink: "#fff" },
  good: { bg: "linear-gradient(140deg, #2fc46f 0%, #17a35a 100%)", ink: "#fff" },
  brand: { bg: "linear-gradient(140deg, #14b8a6 0%, #0d8f86 100%)", ink: "#fff" },
  calm: { bg: "linear-gradient(140deg, #5b6cf0 0%, #4352d6 100%)", ink: "#fff" },
};

/** Count-up tween so changing numbers glide instead of jumping. */
function useTween(target: number, ms = 700) {
  const [v, setV] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const start = performance.now();
    const a = Number.isFinite(from.current) ? from.current : target;
    let raf = 0;
    const step = () => {
      const p = Math.max(0, Math.min(1, (performance.now() - start) / ms));
      const e = 1 - Math.pow(1 - p, 3);
      setV(a + (target - a) * e);
      if (p < 1) raf = requestAnimationFrame(step);
      else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(raf);
      from.current = target;
    };
  }, [target, ms]);
  return v;
}

function Spark({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const pts = data.map((d, i) => [(i / (data.length - 1)) * 100, 100 - ((d - min) / span) * 85 - 5]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="pointer-events-none absolute inset-x-0 bottom-0 h-[58%] w-full">
      {data.map((d, i) => (
        <rect key={i} x={(i / data.length) * 100} width={100 / data.length - 0.35} y={100 - ((d - min) / span) * 85 - 5} height={((d - min) / span) * 85 + 5} fill="rgba(255,255,255,0.10)" />
      ))}
      <path d={line} fill="none" stroke="rgba(255,255,255,0.55)" strokeWidth="0.9" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function KpiTile({
  label, value, unit, decimals = 0, tone, spark = [], sub, onClick, format, className,
}: {
  label: string; value: number; unit?: string; decimals?: number; tone: Tone; spark?: number[]; sub?: ReactNode;
  onClick?: () => void; format?: (v: number) => string; className?: string;
}) {
  const v = useTween(value);
  const t = TONES[tone];
  const text = format ? format(v) : v.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return (
    <button
      onClick={onClick}
      className={cx(
        "group relative flex min-h-[132px] w-full flex-col overflow-hidden rounded-lg p-3.5 text-left shadow-sm transition-[background,transform] duration-700 hover:-translate-y-px",
        className,
      )}
      style={{ background: t.bg, color: t.ink }}
    >
      <Spark data={spark} />
      <div className="relative z-10 text-[12px] font-semibold tracking-wide opacity-90">{label}</div>
      <div className="relative z-10 mt-auto flex items-baseline gap-1 pb-0.5">
        <span className="kpi-number text-[44px] xl:text-[52px]">{text}</span>
        {unit && <span className="text-[22px] font-semibold opacity-90">{unit}</span>}
      </div>
      {sub && <div className="relative z-10 text-[11.5px] font-medium opacity-90">{sub}</div>}
    </button>
  );
}
