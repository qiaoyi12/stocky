// Inventory Table page (Req 14.1, 14.2, 14.3).
//
// On mount it fetches every SKU record via listInventory() and renders them in
// a table showing current_stock, reorder_point, Sales_Velocity, Days_Of_Cover
// and the Detection_Engine classifications as badges. Each row is a clickable,
// keyboard-focusable control that navigates to the Case page (/cases/:sku).
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  listInventory,
  type InventoryItem,
} from "../api/client";
import ConditionBadges from "../components/ConditionBadges";

/** Format an optional numeric metric, rendering "—" when it is unavailable
 * (null/undefined) or the SKU has no recent sales. */
function formatMetric(
  value: number | null | undefined,
  noRecentSales: boolean,
): string {
  if (noRecentSales || value == null) {
    return "—";
  }
  // Trim to at most two decimals without trailing zeroes.
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export default function InventoryPage() {
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
            err instanceof ApiError
              ? err.message
              : "Failed to load inventory.",
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

  function openCase(sku: string) {
    navigate(`/cases/${encodeURIComponent(sku)}`);
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold">Inventory</h1>

      {loading && (
        <p className="mt-4 text-slate-500" role="status">
          Loading inventory…
        </p>
      )}

      {error && !loading && (
        <div
          className="mt-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <p className="mt-4 text-slate-500">
          No SKUs yet. Upload a CSV from the Dashboard to get started.
        </p>
      )}

      {!loading && !error && items.length > 0 && (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr className="text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                <th scope="col" className="px-4 py-3">SKU</th>
                <th scope="col" className="px-4 py-3">Name</th>
                <th scope="col" className="px-4 py-3">Category</th>
                <th scope="col" className="px-4 py-3 text-right">Current stock</th>
                <th scope="col" className="px-4 py-3 text-right">Reorder point</th>
                <th scope="col" className="px-4 py-3 text-right">Sales velocity</th>
                <th scope="col" className="px-4 py-3 text-right">Days of cover</th>
                <th scope="col" className="px-4 py-3">Conditions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr
                  key={item.sku}
                  role="link"
                  tabIndex={0}
                  aria-label={`Open case for ${item.sku}`}
                  onClick={() => openCase(item.sku)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openCase(item.sku);
                    }
                  }}
                  className="cursor-pointer transition-colors hover:bg-slate-50 focus:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-slate-900"
                >
                  <td className="whitespace-nowrap px-4 py-3 font-medium text-slate-900">
                    {item.sku}
                  </td>
                  <td className="px-4 py-3 text-slate-700">{item.name}</td>
                  <td className="px-4 py-3 text-slate-500">{item.category}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-700">
                    {item.current_stock}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-slate-700">
                    {item.reorder_point}
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
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
