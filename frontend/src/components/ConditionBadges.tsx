// Reusable, self-contained presentation of Detection_Engine classification
// labels as small coloured badges. Used by the Inventory Table (task 9.2) and
// the Dashboard (task 9.1). Kept generic: it renders whatever classification
// strings it is given, mapping known labels to friendly text + colours and
// falling back to a neutral style for anything unrecognised.

interface ConditionBadgesProps {
  classifications: string[];
}

// Known classification labels -> display text + Tailwind colour classes.
const BADGE_STYLES: Record<string, { label: string; className: string }> = {
  stockout_risk: {
    label: "Stockout risk",
    className: "bg-red-100 text-red-800 ring-red-600/20",
  },
  needs_reorder: {
    label: "Needs reorder",
    className: "bg-orange-100 text-orange-800 ring-orange-600/20",
  },
  fast_moving: {
    label: "Fast moving",
    className: "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
  },
  slow_moving: {
    label: "Slow moving",
    className: "bg-sky-100 text-sky-800 ring-sky-600/20",
  },
  overstock: {
    label: "Overstock",
    className: "bg-amber-100 text-amber-800 ring-amber-600/20",
  },
  trend_anomaly: {
    label: "Trend anomaly",
    className: "bg-purple-100 text-purple-800 ring-purple-600/20",
  },
};

const FALLBACK_CLASS = "bg-slate-100 text-slate-700 ring-slate-500/20";

/** Turn an unknown label like "trend_anomaly" into "Trend anomaly". */
function humanise(label: string): string {
  const spaced = label.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export default function ConditionBadges({ classifications }: ConditionBadgesProps) {
  if (!classifications || classifications.length === 0) {
    return <span className="text-slate-400">—</span>;
  }

  return (
    <div className="flex flex-wrap gap-1">
      {classifications.map((c) => {
        const style = BADGE_STYLES[c];
        return (
          <span
            key={c}
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
              style ? style.className : FALLBACK_CLASS
            }`}
          >
            {style ? style.label : humanise(c)}
          </span>
        );
      })}
    </div>
  );
}
