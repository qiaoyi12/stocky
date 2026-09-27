// Impact / metrics page.
//
// On mount it fetches the impact aggregate (`getImpact`) and renders:
//   - recommendation outcome counts by lifecycle status across the dataset (Req 17.1)
//   - aggregate detected-condition counts across all SKUs               (Req 17.2)
// Each group is shown as labelled stat cards plus a small horizontal bar
// visualisation (each bar scaled against the group's largest value) so the
// relative distribution is readable at a glance. Loading and error states are
// handled explicitly, mirroring the Dashboard page's conventions.
//
// snake_case keys from the backend are humanized into readable labels.

import { useCallback, useEffect, useState } from "react";
import { ApiError, getImpact, type ImpactResponse } from "../api/client";

/** Turn a snake_case status/condition key into a readable label. */
function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** A single labelled count card. */
function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-card">
      <div>
        <div className="font-display text-2xl text-slate-900">{value}</div>
        <div className="mt-1 text-sm text-slate-500">{humanize(label)}</div>
      </div>
    </div>
  );
}

/** Format a number as USD currency (e.g. 1234.5 -> "$1,234.50"). */
function formatCurrency(amount: number): string {
  return amount.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
  });
}

/** A single horizontal bar, width scaled against the group's largest value. */
function StatBar({
  label,
  value,
  max,
  color,
}: {
  label: string;
  value: number;
  max: number;
  color: string;
}) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="w-40 shrink-0 truncate text-sm text-slate-600">
        {humanize(label)}
      </div>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-brand-100">
        <div
          className={`h-full rounded-full ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="w-10 shrink-0 text-right text-sm font-medium text-slate-900">
        {value}
      </div>
    </div>
  );
}

/** A titled section rendering a set of counts as stat cards + bar chart. */
function MetricGroup({
  title,
  description,
  entries,
  emptyText,
  barColor,
}: {
  title: string;
  description: string;
  entries: [string, number][];
  emptyText: string;
  barColor: string;
}) {
  const max = entries.reduce((m, [, v]) => Math.max(m, v), 0);

  return (
    <div>
      <h2 className="font-display text-lg text-slate-900">{title}</h2>
      <p className="mb-3 text-sm text-slate-500">{description}</p>

      {entries.length === 0 ? (
        <p className="text-sm text-slate-500">{emptyText}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {entries.map(([label, count]) => (
              <StatCard key={label} label={label} value={count} />
            ))}
          </div>

          <div className="card mt-4 space-y-3">
            {entries.map(([label, count]) => (
              <StatBar
                key={label}
                label={label}
                value={count}
                max={max}
                color={barColor}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function ImpactPage() {
  const [data, setData] = useState<ImpactResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadImpact = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getImpact());
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load impact metrics.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadImpact();
  }, [loadImpact]);

  const outcomeEntries = data ? Object.entries(data.outcome_counts) : [];
  const conditionEntries = data ? Object.entries(data.condition_counts) : [];
  const overstockCapital = data?.overstock_capital_tied_up;
  const overstockSkuCount = overstockCapital?.by_sku.length ?? 0;

  return (
    <section className="space-y-6">
      <h1 className="font-display text-2xl text-slate-900">
        Impact <span aria-hidden="true">📊</span>
      </h1>

      {loading && (
        <p role="status" className="text-sm text-slate-500">
          Loading impact metrics…
        </p>
      )}

      {error && (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}{" "}
          <button
            type="button"
            onClick={() => void loadImpact()}
            className="font-medium underline"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && data && (
        <>
          {/* Cost of overstock — honest current figure (no fabricated before/after). */}
          {overstockCapital && (
            <div>
              <h2 className="font-display text-lg text-slate-900">
                Cost of overstock
              </h2>
              <p className="mb-3 text-sm text-slate-500">
                Current capital tied up in excess inventory across the dataset.
              </p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                <div className="stat-card">
                  <div>
                    <div className="font-display text-2xl text-slate-900">
                      {formatCurrency(overstockCapital.total)}
                    </div>
                    <div className="mt-1 text-sm text-slate-500">
                      Capital tied up in overstock (current)
                    </div>
                  </div>
                </div>
                {overstockSkuCount > 0 && (
                  <StatCard
                    label="overstock_skus"
                    value={overstockSkuCount}
                  />
                )}
              </div>
            </div>
          )}

          {/* Recommendation outcome counts by lifecycle status (Req 17.1) */}
          <MetricGroup
            title="Recommendation outcomes"
            description="Counts of recommendations by lifecycle status across the dataset."
            entries={outcomeEntries}
            emptyText="No recommendations yet."
            barColor="bg-indigo-500"
          />

          {/* Aggregate detected-condition counts across all SKUs (Req 17.2) */}
          <MetricGroup
            title="Detected conditions"
            description="Aggregate detected conditions across all SKUs."
            entries={conditionEntries}
            emptyText="No SKUs yet. Upload a CSV to get started."
            barColor="bg-amber-500"
          />
        </>
      )}
    </section>
  );
}
