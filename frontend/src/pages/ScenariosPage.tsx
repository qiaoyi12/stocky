// Simulations hub page (route: /simulations).
//
// A launcher distinct from the per-SKU What-If Lab (/chaos) and the per-SKU
// Simulation page (/simulation/:sku). It fetches the inventory list so the
// user can pick a SKU and navigate to its Simulation page, and links out to
// Chaos Mode for warehouse-wide disruption scenarios. This page never calls
// runSimulation or runChaos itself — it only navigates.
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, listInventory, type InventoryItem } from "../api/client";

export default function ScenariosPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selectedSku, setSelectedSku] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    listInventory()
      .then((res) => {
        if (cancelled) return;
        setItems(res.items);
        if (res.items.length > 0) {
          setSelectedSku(res.items[0].sku);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? err.message : "Failed to load inventory.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const filteredItems = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (item) =>
        item.name.toLowerCase().includes(q) ||
        item.sku.toLowerCase().includes(q),
    );
  }, [items, query]);

  function handleRunSimulation() {
    if (!selectedSku) return;
    navigate(`/simulation/${encodeURIComponent(selectedSku)}`);
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-display text-2xl text-slate-900">
          Simulations <span aria-hidden="true">🧬</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Run a deterministic what-if projection for a specific SKU, or inject
          a synthetic disruption scenario across your whole warehouse.
        </p>
      </div>

      <div className="card space-y-4">
        <h2 className="font-display text-lg text-slate-900">
          Per-SKU What-If Simulation
        </h2>

        {loading && (
          <p role="status" className="text-sm text-slate-500">
            Loading inventory…
          </p>
        )}
        {error && (
          <div
            role="alert"
            className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          >
            {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {items.length === 0 ? (
              <p className="text-sm text-slate-500">
                No SKUs yet. Upload a CSV from the Dashboard to get started.
              </p>
            ) : (
              <>
                <div>
                  <label
                    htmlFor="sku-search"
                    className="mb-1 block text-sm font-medium text-slate-600"
                  >
                    Search SKUs
                  </label>
                  <input
                    id="sku-search"
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search by name or SKU..."
                    className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
                  />
                </div>

                <div>
                  <label
                    htmlFor="sku-select"
                    className="mb-1 block text-sm font-medium text-slate-600"
                  >
                    Select a SKU
                  </label>
                  <select
                    id="sku-select"
                    value={selectedSku}
                    onChange={(e) => setSelectedSku(e.target.value)}
                    className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
                  >
                    {filteredItems.length === 0 && (
                      <option value="">No matching SKUs</option>
                    )}
                    {filteredItems.map((item) => (
                      <option key={item.sku} value={item.sku}>
                        {item.name} ({item.sku})
                      </option>
                    ))}
                  </select>
                </div>

                <button
                  type="button"
                  onClick={handleRunSimulation}
                  disabled={!selectedSku}
                  className="btn-primary"
                >
                  Run simulation
                </button>
              </>
            )}
          </>
        )}
      </div>

      <div className="card space-y-3">
        <h2 className="font-display text-lg text-slate-900">
          Warehouse-Wide Disruption
        </h2>
        <p className="text-sm text-slate-500">
          Inject a synthetic scenario, like a demand spike or a supplier
          delay, and see how conditions shift across your inventory. Your
          source data is never altered.
        </p>
        <button
          type="button"
          onClick={() => navigate("/chaos")}
          className="btn-primary"
        >
          Try Chaos Mode →
        </button>
      </div>
    </section>
  );
}
