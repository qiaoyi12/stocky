// Interactive Warehouse Visualisation page (Req 19.1, 19.2).
//
// On mount it fetches every SKU via listInventory() and lays them out as a
// friendly warehouse of condition "zones" (shelves). Each zone corresponds to
// one Detection_Engine classification label; a zone contains one coloured
// "box" tile per SKU carrying that label, plus a live count in the zone header
// (Req 19.1).
//
// Grouping decision: a SKU is placed in EVERY zone whose label it carries, so a
// multi-label SKU (e.g. both `stockout_risk` and `fast_moving`) appears as a
// tile in each of those zones. SKUs with no classifications are collected in a
// dedicated "Healthy / OK" zone so nothing is dropped from the visualisation.
//
// Every tile is a real <button>, so it is clickable and keyboard-accessible by
// default (Tab to focus, Enter/Space to activate). Activating a tile navigates
// to that SKU's Case page at /cases/:sku (Req 19.2).
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  listInventory,
  type InventoryItem,
} from "../api/client";

// Sentinel key for the catch-all zone holding SKUs with no classifications.
const HEALTHY_ZONE = "__healthy__";

// Per-zone friendly title + Tailwind colour classes. Known Detection_Engine
// labels get bespoke colours; the special healthy zone gets a calm green. Any
// unrecognised label falls back to a neutral slate style (see zoneStyle()).
const ZONE_STYLES: Record<
  string,
  { title: string; header: string; tile: string; ring: string }
> = {
  stockout_risk: {
    title: "Stockout risk",
    header: "bg-rose-50 text-rose-700 border-rose-100",
    tile: "bg-rose-50/60 hover:bg-rose-100 text-rose-800 border-rose-100",
    ring: "focus-visible:ring-rose-400",
  },
  needs_reorder: {
    title: "Needs reorder",
    header: "bg-orange-50 text-orange-700 border-orange-100",
    tile: "bg-orange-50/60 hover:bg-orange-100 text-orange-800 border-orange-100",
    ring: "focus-visible:ring-orange-400",
  },
  fast_moving: {
    title: "Fast moving",
    header: "bg-emerald-50 text-emerald-700 border-emerald-100",
    tile: "bg-emerald-50/60 hover:bg-emerald-100 text-emerald-800 border-emerald-100",
    ring: "focus-visible:ring-emerald-400",
  },
  slow_moving: {
    title: "Slow moving",
    header: "bg-sky-50 text-sky-700 border-sky-100",
    tile: "bg-sky-50/60 hover:bg-sky-100 text-sky-800 border-sky-100",
    ring: "focus-visible:ring-sky-400",
  },
  overstock: {
    title: "Overstock",
    header: "bg-amber-50 text-amber-700 border-amber-100",
    tile: "bg-amber-50/60 hover:bg-amber-100 text-amber-800 border-amber-100",
    ring: "focus-visible:ring-amber-400",
  },
  trend_anomaly: {
    title: "Trend anomaly",
    header: "bg-brand-50 text-brand-700 border-brand-100",
    tile: "bg-brand-50/60 hover:bg-brand-100 text-brand-800 border-brand-100",
    ring: "focus-visible:ring-brand-400",
  },
  [HEALTHY_ZONE]: {
    title: "Healthy / OK",
    header: "bg-green-50 text-green-700 border-green-100",
    tile: "bg-green-50/60 hover:bg-green-100 text-green-800 border-green-100",
    ring: "focus-visible:ring-green-400",
  },
};

const FALLBACK_ZONE = {
  header: "bg-slate-50 text-slate-700 border-slate-100",
  tile: "bg-slate-50/60 hover:bg-slate-100 text-slate-800 border-slate-100",
  ring: "focus-visible:ring-slate-400",
};

// Preferred display order of zones; unknown labels sort after these, healthy last.
const ZONE_ORDER = [
  "stockout_risk",
  "needs_reorder",
  "overstock",
  "trend_anomaly",
  "fast_moving",
  "slow_moving",
];

/** Turn an unknown label like "trend_anomaly" into "Trend anomaly". */
function humanise(label: string): string {
  const spaced = label.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Resolve the visual style for a zone key, falling back for unknown labels. */
function zoneStyle(key: string) {
  const known = ZONE_STYLES[key];
  if (known) {
    return known;
  }
  return { title: humanise(key), ...FALLBACK_ZONE };
}

interface Zone {
  key: string;
  items: InventoryItem[];
}

/** Group SKUs into zones by classification label. A SKU appears once per label
 * it carries; SKUs with no labels go to the healthy zone. Zones are returned in
 * a stable, human-friendly order. */
function buildZones(items: InventoryItem[]): Zone[] {
  const map = new Map<string, InventoryItem[]>();

  for (const item of items) {
    const labels =
      item.classifications && item.classifications.length > 0
        ? item.classifications
        : [HEALTHY_ZONE];
    for (const label of labels) {
      const bucket = map.get(label);
      if (bucket) {
        bucket.push(item);
      } else {
        map.set(label, [item]);
      }
    }
  }

  const rank = (key: string): number => {
    if (key === HEALTHY_ZONE) {
      return ZONE_ORDER.length + 1; // healthy always last
    }
    const idx = ZONE_ORDER.indexOf(key);
    return idx === -1 ? ZONE_ORDER.length : idx; // unknown labels before healthy
  };

  return Array.from(map.entries())
    .map(([key, zoneItems]) => ({
      key,
      // Stable SKU order within a zone for a predictable layout.
      items: [...zoneItems].sort((a, b) => a.sku.localeCompare(b.sku)),
    }))
    .sort((a, b) => {
      const byRank = rank(a.key) - rank(b.key);
      return byRank !== 0 ? byRank : a.key.localeCompare(b.key);
    });
}

export default function WarehousePage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    listInventory()
      .then((res) => {
        if (!cancelled) {
          setItems(res.items);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load inventory.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const zones = useMemo(() => buildZones(items), [items]);

  function openCase(sku: string) {
    navigate(`/cases/${encodeURIComponent(sku)}`);
  }

  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="font-display text-2xl text-slate-900">
          Warehouse <span aria-hidden="true">🏭</span>
        </h1>
        {!loading && !error && items.length > 0 && (
          <p className="text-sm text-slate-500">
            {items.length} SKU{items.length === 1 ? "" : "s"} across{" "}
            {zones.length} zone{zones.length === 1 ? "" : "s"}
          </p>
        )}
      </div>
      <p className="text-sm text-slate-500">
        SKUs are shelved by detected condition. A SKU appears in every zone whose
        condition it matches. Pick a box to open its case.
      </p>

      {loading && (
        <p className="mt-4 text-sm text-slate-500" role="status">
          Loading warehouse…
        </p>
      )}

      {error && !loading && (
        <div
          className="mt-4 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="mt-4 text-sm text-slate-500">
          No SKUs yet. Upload a CSV from the Dashboard to fill the warehouse.
        </p>
      )}

      {!loading && !error && items.length > 0 && (
        <div className="mt-6 grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {zones.map((zone) => {
            const style = zoneStyle(zone.key);
            return (
              <div
                key={zone.key}
                className="flex flex-col overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card"
              >
                <div
                  className={`flex items-center justify-between border-b px-4 py-3 ${style.header}`}
                >
                  <h2 className="font-display text-sm text-slate-800">
                    {style.title}
                  </h2>
                  <span className="inline-flex min-w-6 items-center justify-center rounded-full bg-white/70 px-2 py-0.5 text-xs font-medium tabular-nums">
                    {zone.items.length}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3">
                  {zone.items.map((item) => (
                    <button
                      key={`${zone.key}:${item.sku}`}
                      type="button"
                      onClick={() => openCase(item.sku)}
                      title={`${item.name} · stock ${item.current_stock}`}
                      aria-label={`Open case for ${item.sku} (${item.name})`}
                      className={`flex flex-col rounded-xl border px-2.5 py-2 text-left shadow-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 ${style.tile} ${style.ring}`}
                    >
                      <span className="truncate text-xs font-semibold">
                        {item.sku}
                      </span>
                      <span className="truncate text-[11px] opacity-70">
                        {item.name}
                      </span>
                      <span className="mt-1 text-[11px] tabular-nums opacity-60">
                        stock {item.current_stock}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
