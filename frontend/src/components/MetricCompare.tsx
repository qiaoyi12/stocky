// MetricCompare — side-by-side presentation of a SKU's current vs projected
// Detection_Engine metrics (Req 16.3). Used by the Simulation page (task 11.4)
// to render the read-only what-if comparison returned by POST /api/simulation.
//
// It renders one row per metric (Sales_Velocity, Days_Of_Cover, Stockout_ETA,
// "no recent sales", and the classification badges) with a "Current" and a
// "Projected" column. Rows whose projected value differs from the current one
// are highlighted so the change stands out at a glance.
import type { MetricsView } from "../api/client";
import ConditionBadges from "./ConditionBadges";

interface MetricCompareProps {
  current: MetricsView;
  projected: MetricsView;
}

/** Format an optional numeric metric, rendering "—" when it is unavailable
 * (null/undefined) or the SKU has no recent sales at that velocity. */
function formatNumber(
  value: number | null | undefined,
  noRecentSales: boolean,
): string {
  if (noRecentSales || value == null) {
    return "—";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

/** Format the Stockout_ETA, which the backend returns as an ISO date string (or
 * null when velocity is zero / no recent sales). */
function formatEta(
  value: string | null | undefined,
  noRecentSales: boolean,
): string {
  if (noRecentSales || value == null || value === "") {
    return "—";
  }
  return value;
}

/** Compare two classification lists order-insensitively for the highlight. */
function sameClassifications(a: string[], b: string[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  const setB = new Set(b);
  return a.every((label) => setB.has(label));
}

/** A single comparison row. `changed` drives the highlight styling. */
function CompareRow({
  label,
  changed,
  currentCell,
  projectedCell,
}: {
  label: string;
  changed: boolean;
  currentCell: React.ReactNode;
  projectedCell: React.ReactNode;
}) {
  return (
    <tr className={changed ? "bg-amber-50" : undefined}>
      <th
        scope="row"
        className="px-4 py-3 text-left font-medium text-slate-600"
      >
        <span className="flex items-center gap-2">
          {label}
          {changed && (
            <span
              className="inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-600/20"
              aria-label="changed"
            >
              changed
            </span>
          )}
        </span>
      </th>
      <td className="px-4 py-3 text-slate-700">{currentCell}</td>
      <td className="px-4 py-3 text-slate-900">{projectedCell}</td>
    </tr>
  );
}

export default function MetricCompare({ current, projected }: MetricCompareProps) {
  const velocityChanged =
    formatNumber(current.sales_velocity, current.no_recent_sales) !==
    formatNumber(projected.sales_velocity, projected.no_recent_sales);

  const docChanged =
    formatNumber(current.days_of_cover, current.no_recent_sales) !==
    formatNumber(projected.days_of_cover, projected.no_recent_sales);

  const etaChanged =
    formatEta(current.stockout_eta, current.no_recent_sales) !==
    formatEta(projected.stockout_eta, projected.no_recent_sales);

  const noSalesChanged = current.no_recent_sales !== projected.no_recent_sales;

  const classificationsChanged = !sameClassifications(
    current.classifications ?? [],
    projected.classifications ?? [],
  );

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50">
          <tr className="text-left text-xs font-medium uppercase tracking-wide text-slate-500">
            <th scope="col" className="px-4 py-3">Metric</th>
            <th scope="col" className="px-4 py-3">Current</th>
            <th scope="col" className="px-4 py-3">Projected</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          <CompareRow
            label="Sales velocity"
            changed={velocityChanged}
            currentCell={
              <span className="tabular-nums">
                {formatNumber(current.sales_velocity, current.no_recent_sales)}
              </span>
            }
            projectedCell={
              <span className="tabular-nums">
                {formatNumber(
                  projected.sales_velocity,
                  projected.no_recent_sales,
                )}
              </span>
            }
          />
          <CompareRow
            label="Days of cover"
            changed={docChanged}
            currentCell={
              <span className="tabular-nums">
                {formatNumber(current.days_of_cover, current.no_recent_sales)}
              </span>
            }
            projectedCell={
              <span className="tabular-nums">
                {formatNumber(
                  projected.days_of_cover,
                  projected.no_recent_sales,
                )}
              </span>
            }
          />
          <CompareRow
            label="Stockout ETA"
            changed={etaChanged}
            currentCell={formatEta(current.stockout_eta, current.no_recent_sales)}
            projectedCell={formatEta(
              projected.stockout_eta,
              projected.no_recent_sales,
            )}
          />
          <CompareRow
            label="No recent sales"
            changed={noSalesChanged}
            currentCell={current.no_recent_sales ? "Yes" : "No"}
            projectedCell={projected.no_recent_sales ? "Yes" : "No"}
          />
          <CompareRow
            label="Conditions"
            changed={classificationsChanged}
            currentCell={
              <ConditionBadges classifications={current.classifications ?? []} />
            }
            projectedCell={
              <ConditionBadges
                classifications={projected.classifications ?? []}
              />
            }
          />
        </tbody>
      </table>
    </div>
  );
}
