// A compact KPI tile built on the `.card` component class. Shows a label, a
// large display-font value, an emoji/icon bubble, and an optional trend line
// (▲/▼ + delta%) coloured by tone.
import type { ReactNode } from "react";

type Tone = "up" | "down" | "neutral";

interface StatCardProps {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  delta?: number;
  deltaSuffix?: string;
  tone?: Tone;
}

const TONE_STYLES: Record<Tone, string> = {
  up: "text-emerald-600",
  down: "text-rose-600",
  neutral: "text-slate-500",
};

export default function StatCard({
  icon,
  label,
  value,
  delta,
  deltaSuffix = "%",
  tone = "neutral",
}: StatCardProps) {
  const arrow = tone === "up" ? "▲" : tone === "down" ? "▼" : "•";
  return (
    <div className="card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm text-slate-500">{label}</div>
          <div className="mt-1 text-2xl font-display text-slate-800">{value}</div>
        </div>
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-brand-50 text-xl"
          aria-hidden="true"
        >
          {icon}
        </div>
      </div>
      {delta !== undefined && (
        <div className={`mt-3 flex items-center gap-1 text-sm font-semibold ${TONE_STYLES[tone]}`}>
          <span aria-hidden="true">{arrow}</span>
          <span>
            {Math.abs(delta)}
            {deltaSuffix}
          </span>
        </div>
      )}
    </div>
  );
}
