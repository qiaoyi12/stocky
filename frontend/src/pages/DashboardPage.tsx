// Home dashboard page.
//
// On mount it fetches the dashboard aggregate (`getDashboard`) and the full
// inventory list (`listInventory`) together, then derives every number shown
// on the page from that REAL data client-side (KPI totals, stock health %,
// filtered table rows, key insights, etc). Nothing here is fabricated: any
// figure that can't be computed from the API response (a trend arrow, a
// dollar "savings" estimate, a specific recommended action) is intentionally
// omitted or clearly labelled as requiring further investigation.
//
// Sections (top to bottom): greeting header, CSV upload widget, 4 KPI stat
// cards, a warehouse "at a glance" banner with product chips, an inventory
// overview table with filter pills, a key-insights list, an AI recommendation
// preview, and a shop-health footer strip.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ApiError,
  getDashboard,
  listInventory,
  type DashboardResponse,
  type InventoryItem,
} from "../api/client";
import ConditionBadges from "../components/ConditionBadges";
import UploadCsv from "../components/UploadCsv";

// --- Classification helpers --------------------------------------------------

/** Classifications considered "risky" for the Stock Health / Healthy filter. */
const RISKY_CLASSIFICATIONS = new Set([
  "stockout_risk",
  "needs_reorder",
  "overstock",
  "slow_moving",
  "trend_anomaly",
]);

function isRisky(item: InventoryItem): boolean {
  return item.classifications.some((c) => RISKY_CLASSIFICATIONS.has(c));
}

function hasClassification(item: InventoryItem, label: string): boolean {
  return item.classifications.includes(label);
}

// --- Derived KPI helpers (all computed from real API data) ------------------

/** Sum of current_stock * unit_cost across every inventory item. */
function computeTotalStockValue(items: InventoryItem[]): number {
  return items.reduce((sum, i) => sum + i.current_stock * i.unit_cost, 0);
}

/** % of SKUs carrying no risky classification. Returns null when there are no
 * items yet (nothing to compute a percentage of). */
function computeStockHealthPct(items: InventoryItem[]): number | null {
  if (items.length === 0) {
    return null;
  }
  const healthy = items.filter((i) => !isRisky(i)).length;
  return Math.round((healthy / items.length) * 100);
}

/** Sum of excess_units across the overstock capital-tied-up breakdown. */
function computeExcessUnits(dashboard: DashboardResponse | null): number {
  return (
    dashboard?.overstock_capital_tied_up?.by_sku.reduce(
      (sum, s) => sum + s.excess_units,
      0,
    ) ?? 0
  );
}

function computeDeadStockCount(items: InventoryItem[]): number {
  return items.filter((i) => i.no_recent_sales).length;
}

// --- Formatting helpers -------------------------------------------------------

function formatCurrency(amount: number): string {
  return amount.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

/** Mirrors InventoryPage's formatMetric: render "—" when a metric is
 * unavailable (null/undefined) or the SKU has no recent sales. */
function formatMetric(
  value: number | null | undefined,
  noRecentSales: boolean,
): string {
  if (noRecentSales || value == null) {
    return "—";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** Deterministic keyword-based emoji for a product name, purely decorative. */
function emojiForName(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("bunny") || n.includes("rabbit")) return "🐰";
  if (n.includes("bear") || n.includes("teddy")) return "🐻";
  if (n.includes("cat")) return "🐱";
  if (n.includes("key")) return "🔑";
  if (n.includes("mug") || n.includes("cup")) return "☕";
  if (n.includes("lamp")) return "💡";
  if (n.includes("pillow")) return "🛏️";
  if (n.includes("bag")) return "👜";
  if (n.includes("dog")) return "🐶";
  if (n.includes("frog")) return "🐸";
  if (n.includes("panda")) return "🐼";
  if (n.includes("hamster")) return "🐹";
  return "📦";
}

/** Friendly label for a classification key, e.g. "stockout_risk" -> "Stockout risk". */
function humanise(label: string): string {
  const spaced = label.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const PRIMARY_CLASSIFICATION_STYLE: Record<string, string> = {
  stockout_risk: "bg-red-100 text-red-800",
  needs_reorder: "bg-orange-100 text-orange-800",
  fast_moving: "bg-emerald-100 text-emerald-800",
  slow_moving: "bg-sky-100 text-sky-800",
  overstock: "bg-amber-100 text-amber-800",
  trend_anomaly: "bg-purple-100 text-purple-800",
};

// --- Table filters -----------------------------------------------------------

type TableFilter =
  | "all"
  | "healthy"
  | "at_risk"
  | "overstock"
  | "slow_moving"
  | "stockout";

const TABLE_FILTERS: { key: TableFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "healthy", label: "Healthy" },
  { key: "at_risk", label: "At Risk" },
  { key: "overstock", label: "Overstock" },
  { key: "slow_moving", label: "Slow Moving" },
  { key: "stockout", label: "Stockout" },
];

function matchesFilter(item: InventoryItem, filter: TableFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "healthy":
      return !isRisky(item);
    case "at_risk":
      return (
        hasClassification(item, "stockout_risk") ||
        hasClassification(item, "needs_reorder")
      );
    case "overstock":
      return hasClassification(item, "overstock");
    case "slow_moving":
      return hasClassification(item, "slow_moving");
    case "stockout":
      return hasClassification(item, "stockout_risk");
    default:
      return true;
  }
}

// --- Warehouse banner chips ---------------------------------------------------

/** Pick up to `count` chip candidates: prioritise stockout_risk items (already
 * urgency-sorted by the backend), then pad with other notable items (overstock
 * or fast_moving) from the full inventory list, skipping duplicates. */
function pickChipItems(
  stockoutRisk: InventoryItem[],
  allItems: InventoryItem[],
  count: number,
): InventoryItem[] {
  const chosen: InventoryItem[] = [...stockoutRisk.slice(0, count)];
  const chosenSkus = new Set(chosen.map((i) => i.sku));

  if (chosen.length < count) {
    const notable = allItems.filter(
      (i) =>
        !chosenSkus.has(i.sku) &&
        (hasClassification(i, "overstock") ||
          hasClassification(i, "fast_moving")),
    );
    for (const item of notable) {
      if (chosen.length >= count) break;
      chosen.push(item);
      chosenSkus.add(item.sku);
    }
  }

  return chosen.slice(0, count);
}

/** A single warehouse-banner tile linking to a SKU's case page. */
function ProductChip({ item }: { item: InventoryItem }) {
  const primary = item.classifications[0];
  return (
    <Link
      to={`/cases/${encodeURIComponent(item.sku)}`}
      className="flex flex-col gap-1 rounded-xl bg-white/80 p-3 shadow-sm ring-1 ring-white/60 backdrop-blur transition hover:bg-white"
    >
      <div className="flex items-center gap-2">
        <span className="text-xl" aria-hidden="true">
          {emojiForName(item.name)}
        </span>
        <span className="truncate text-sm font-semibold text-slate-800">
          {item.name}
        </span>
      </div>
      {primary && (
        <span
          className={`pill w-fit ${
            PRIMARY_CLASSIFICATION_STYLE[primary] ??
            "bg-slate-100 text-slate-700"
          }`}
        >
          {humanise(primary)}
        </span>
      )}
      <span className="text-xs text-slate-500">
        Stock: <span className="font-medium text-slate-700">{item.current_stock}</span>
      </span>
    </Link>
  );
}

// --- KPI stat card ------------------------------------------------------------

function KpiCard({
  emoji,
  value,
  label,
}: {
  emoji: string;
  value: string;
  label: string;
}) {
  return (
    <div className="stat-card">
      <div>
        <div className="font-display text-2xl text-slate-900">{value}</div>
        <div className="mt-1 text-sm text-slate-500">{label}</div>
      </div>
      <span
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xl"
        aria-hidden="true"
      >
        {emoji}
      </span>
    </div>
  );
}

// --- Key insights --------------------------------------------------------------

interface Insight {
  key: string;
  sku: string;
  text: string;
}

/** Build up to a handful of short, real-data-backed insight lines. Empty
 * categories are omitted rather than padded with filler. */
function buildInsights(
  dashboard: DashboardResponse,
  items: InventoryItem[],
): Insight[] {
  const insights: Insight[] = [];
  const bySku = new Map(items.map((i) => [i.sku, i]));

  const topStockout = dashboard.stockout_risk[0];
  if (topStockout) {
    insights.push({
      key: "stockout",
      sku: topStockout.sku,
      text: topStockout.stockout_eta
        ? `${topStockout.name}: stockout predicted around ${topStockout.stockout_eta}`
        : `${topStockout.name}: at risk of stockout`,
    });
  }

  const topOverstock = dashboard.overstock_capital_tied_up?.by_sku[0];
  if (topOverstock) {
    const match = bySku.get(topOverstock.sku);
    const label = match ? match.name : topOverstock.sku;
    insights.push({
      key: "overstock",
      sku: topOverstock.sku,
      text: `${label}: ${formatCurrency(topOverstock.capital_tied_up)} tied up in excess stock`,
    });
  }

  const fastMoving = items.find((i) => hasClassification(i, "fast_moving"));
  if (fastMoving) {
    insights.push({
      key: "fast_moving",
      sku: fastMoving.sku,
      text:
        fastMoving.sales_velocity != null
          ? `${fastMoving.name}: high velocity (${formatMetric(fastMoving.sales_velocity, fastMoving.no_recent_sales)}/day) — consider increasing stock`
          : `${fastMoving.name}: fast moving — consider increasing stock`,
    });
  }

  const anomaly = items.find((i) => hasClassification(i, "trend_anomaly"));
  if (anomaly) {
    insights.push({
      key: "trend_anomaly",
      sku: anomaly.sku,
      text: `${anomaly.name}: notable demand shift detected`,
    });
  }

  return insights;
}

// --- Page ----------------------------------------------------------------------

export default function DashboardPage() {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<TableFilter>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dashboardRes, inventoryRes] = await Promise.all([
        getDashboard(),
        listInventory(),
      ]);
      setDashboard(dashboardRes);
      setItems(inventoryRes.items);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load the dashboard.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const totalStockValue = useMemo(() => computeTotalStockValue(items), [items]);
  const stockHealthPct = useMemo(() => computeStockHealthPct(items), [items]);
  const stockoutCount = dashboard?.condition_counts.stockout_risk ?? 0;
  const excessUnits = useMemo(() => computeExcessUnits(dashboard), [dashboard]);
  const deadStockCount = useMemo(() => computeDeadStockCount(items), [items]);

  const chipItems = useMemo(
    () => (dashboard ? pickChipItems(dashboard.stockout_risk, items, 4) : []),
    [dashboard, items],
  );

  const filteredItems = useMemo(
    () => items.filter((i) => matchesFilter(i, filter)).slice(0, 8),
    [items, filter],
  );

  const insights = useMemo(
    () => (dashboard ? buildInsights(dashboard, items) : []),
    [dashboard, items],
  );

  const topRecommendationCandidate = dashboard?.stockout_risk[0] ?? null;

  function openCase(sku: string) {
    navigate(`/cases/${encodeURIComponent(sku)}`);
  }

  return (
    <section className="space-y-6">
      {/* Greeting header */}
      <div>
        <h1 className="font-display text-3xl text-slate-900">
          Good morning, Agent! <span aria-hidden="true">☀️</span>
        </h1>
        <p className="mt-1 text-slate-500">
          Here's what's happening in your warehouse today.
        </p>
      </div>

      <UploadCsv
        onUploaded={() => {
          void load();
        }}
      />

      {loading && (
        <p className="text-sm text-slate-500" role="status">
          Loading dashboard…
        </p>
      )}

      {error && !loading && (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}{" "}
          <button
            type="button"
            onClick={() => void load()}
            className="font-medium underline"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && dashboard && (
        <>
          {/* KPI stat cards */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <KpiCard
              emoji="💰"
              value={formatCurrency(totalStockValue)}
              label="Total Stock Value"
            />
            <KpiCard
              emoji="💚"
              value={stockHealthPct != null ? `${stockHealthPct}%` : "—"}
              label="Stock Health"
            />
            <KpiCard
              emoji="🚨"
              value={String(stockoutCount)}
              label="Stockouts"
            />
            <KpiCard
              emoji="📦"
              value={String(excessUnits)}
              label="Excess units"
            />
          </div>

          {/* Warehouse banner */}
          <div className="card bg-gradient-to-br from-brand-50 via-white to-brand-100">
            <h2 className="font-display text-xl text-slate-900">
              Your warehouse at a glance
            </h2>
            {chipItems.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">
                No SKUs yet. Upload a CSV to populate your warehouse.
              </p>
            ) : (
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {chipItems.map((item) => (
                  <ProductChip key={item.sku} item={item} />
                ))}
              </div>
            )}
            <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-xl bg-white/70 px-4 py-2.5 text-sm">
              <span className="text-slate-600">
                4 AI agents are analysing your inventory…
              </span>
              <Link
                to="/agents"
                className="font-semibold text-brand-700 hover:text-brand-800"
              >
                View agents →
              </Link>
            </div>
          </div>

          {/* Inventory overview */}
          <div className="card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-display text-xl text-slate-900">
                Inventory Overview
              </h2>
              <div className="flex flex-wrap gap-2">
                {TABLE_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    onClick={() => setFilter(f.key)}
                    className={`pill transition ${
                      filter === f.key
                        ? "bg-brand-500 text-white"
                        : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                    }`}
                    aria-pressed={filter === f.key}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            {items.length === 0 ? (
              <p className="mt-4 text-sm text-slate-500">
                No SKUs yet. Upload a CSV to get started.
              </p>
            ) : filteredItems.length === 0 ? (
              <p className="mt-4 text-sm text-slate-500">
                No SKUs match this filter.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto rounded-xl border border-slate-100">
                <table className="min-w-full divide-y divide-slate-100 text-sm">
                  <thead className="bg-slate-50">
                    <tr className="text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                      <th scope="col" className="px-4 py-3">Product</th>
                      <th scope="col" className="px-4 py-3">Category</th>
                      <th scope="col" className="px-4 py-3 text-right">Stock</th>
                      <th scope="col" className="px-4 py-3 text-right">Velocity</th>
                      <th scope="col" className="px-4 py-3 text-right">Days Left</th>
                      <th scope="col" className="px-4 py-3">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {filteredItems.map((item) => (
                      <DashboardInventoryRow
                        key={item.sku}
                        item={item}
                        onOpen={openCase}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Key insights + AI recommendation preview */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="card">
              <h2 className="font-display text-xl text-slate-900">
                Key Insights
              </h2>
              {insights.length === 0 ? (
                <p className="mt-3 text-sm text-slate-500">
                  Nothing notable to report right now.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {insights.map((insight) => (
                    <li key={insight.key}>
                      <Link
                        to={`/cases/${encodeURIComponent(insight.sku)}`}
                        className="block rounded-lg px-2 py-1.5 text-sm text-slate-700 transition hover:bg-slate-50"
                      >
                        {insight.text}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="card">
              <h2 className="font-display text-xl text-slate-900">
                AI Recommendation
              </h2>
              {topRecommendationCandidate ? (
                <div className="mt-3">
                  <div className="flex items-center gap-2">
                    <span className="text-lg" aria-hidden="true">
                      {emojiForName(topRecommendationCandidate.name)}
                    </span>
                    <span className="font-semibold text-slate-800">
                      {topRecommendationCandidate.name}
                    </span>
                    <span className="pill bg-red-100 text-red-800">
                      High priority
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-slate-500">
                    Run an investigation to get the AI's recommendation.
                  </p>
                  <Link
                    to={`/cases/${encodeURIComponent(topRecommendationCandidate.sku)}`}
                    className="btn-primary mt-3 inline-block"
                  >
                    Investigate →
                  </Link>
                </div>
              ) : (
                <p className="mt-3 text-sm text-slate-500">
                  No urgent cases right now.
                </p>
              )}
            </div>
          </div>

          {/* Shop health footer strip */}
          <div className="card grid grid-cols-2 gap-4 sm:grid-cols-4">
            <ShopHealthMetric
              label="Stock Health"
              value={stockHealthPct != null ? `${stockHealthPct}%` : "—"}
            />
            <ShopHealthMetric label="Stockouts" value={String(stockoutCount)} />
            <ShopHealthMetric label="Excess units" value={String(excessUnits)} />
            <ShopHealthMetric label="Dead Stock" value={String(deadStockCount)} />
          </div>
        </>
      )}
    </section>
  );
}

// --- Small presentational subcomponents --------------------------------------

/** One row of the Inventory Overview table, mirroring InventoryPage's
 * accessible row-as-link pattern (role="link", tabIndex, Enter/Space handling). */
function DashboardInventoryRow({
  item,
  onOpen,
}: {
  item: InventoryItem;
  onOpen: (sku: string) => void;
}) {
  return (
    <tr
      role="link"
      tabIndex={0}
      aria-label={`Open case for ${item.sku}`}
      onClick={() => onOpen(item.sku)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(item.sku);
        }
      }}
      className="cursor-pointer transition-colors hover:bg-slate-50 focus:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500"
    >
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-base" aria-hidden="true">
            {emojiForName(item.name)}
          </span>
          <span className="font-medium text-slate-800">{item.name}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-slate-500">{item.category}</td>
      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-700">
        {item.current_stock}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-700">
        {formatMetric(item.sales_velocity, item.no_recent_sales)}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-700">
        {formatMetric(item.days_of_cover, item.no_recent_sales)}
      </td>
      <td className="px-4 py-3">
        <ConditionBadges classifications={item.classifications} />
      </td>
    </tr>
  );
}

function ShopHealthMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-display text-xl text-slate-900">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </div>
  );
}
