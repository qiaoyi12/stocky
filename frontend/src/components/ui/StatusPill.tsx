// A single-status pill built on the `.pill` component class. Maps known status
// strings to colours drawn from the same palette as ConditionBadges, humanising
// the label for display. Unknown statuses fall back to a neutral slate style.
// (For rendering multiple classification badges at once, use ConditionBadges.)

interface StatusPillProps {
  status: string;
}

// Normalised status key -> pill colour classes.
const PILL_STYLES: Record<string, string> = {
  stockout_risk: "bg-rose-100 text-rose-800",
  "at-risk": "bg-rose-100 text-rose-800",
  at_risk: "bg-rose-100 text-rose-800",
  needs_reorder: "bg-orange-100 text-orange-800",
  low: "bg-orange-100 text-orange-800",
  overstock: "bg-amber-100 text-amber-800",
  slow_moving: "bg-sky-100 text-sky-800",
  fast_moving: "bg-emerald-100 text-emerald-800",
  healthy: "bg-emerald-100 text-emerald-800",
};

const FALLBACK_CLASS = "bg-slate-100 text-slate-700";

/** Turn a key like "stockout_risk" or "at-risk" into "Stockout risk". */
function humanise(label: string): string {
  const spaced = label.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export default function StatusPill({ status }: StatusPillProps) {
  const key = status.trim().toLowerCase();
  const className = PILL_STYLES[key] ?? FALLBACK_CLASS;
  return <span className={`pill ${className}`}>{humanise(status)}</span>;
}
