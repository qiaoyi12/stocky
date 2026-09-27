// Chaos Mode page (route: /chaos).
//
// Lets a Reviewer pick a synthetic disruption scenario and intensity, then
// inject it (POST /api/chaos). The backend applies the events to a working
// copy only — never the originally uploaded source data — and re-runs the
// Detection_Engine classification on the affected SKUs (Req 18.1, 18.2).
//
// The results render per SKU as a before -> after comparison: reclassified
// condition badges plus the key metric changes (sales velocity, days of cover,
// lead time). A prominent notice reflects `source_unchanged` to reassure the
// Reviewer that the source data is untouched (Req 18.3).

import { useState } from "react";
import {
  ApiError,
  runChaos,
  type ChaosResponse,
  type ChaosScenario,
  type SkuChaosResult,
} from "../api/client";
import ConditionBadges from "../components/ConditionBadges";

const SCENARIOS: { value: ChaosScenario; label: string; description: string }[] = [
  {
    value: "demand_spike",
    label: "Demand spike",
    description: "A sudden surge in recent sales.",
  },
  {
    value: "demand_drop",
    label: "Demand drop",
    description: "A sharp fall in recent sales.",
  },
  {
    value: "supply_delay",
    label: "Supply delay",
    description: "Longer supplier lead times.",
  },
];

const DEFAULT_INTENSITY = 2;

/** Render a number metric, coping with null/undefined and choosing a sensible
 * number of decimals. */
function formatMetric(value: number | null | undefined, decimals = 1): string {
  if (value == null) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(decimals);
}

/** A single before -> after metric row. */
function MetricDelta({
  label,
  before,
  after,
  decimals = 1,
}: {
  label: string;
  before: number | null | undefined;
  after: number | null | undefined;
  decimals?: number;
}) {
  const changed = before !== after;
  return (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="tabular-nums">
        <span className="text-slate-500">{formatMetric(before, decimals)}</span>
        <span className="mx-1 text-slate-400">→</span>
        <span
          className={
            changed ? "font-semibold text-slate-900" : "text-slate-500"
          }
        >
          {formatMetric(after, decimals)}
        </span>
      </span>
    </div>
  );
}

/** One affected SKU: reclassified badges + metric deltas. */
function SkuResultCard({ result }: { result: SkuChaosResult }) {
  return (
    <li className="card">
      <div className="mb-3 font-mono text-sm font-semibold text-slate-900">
        {result.sku}
      </div>

      {/* Reclassification: before -> after (Req 18.2) */}
      <div className="mb-3 space-y-2">
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-400">
            Before
          </div>
          <ConditionBadges classifications={result.classifications_before} />
        </div>
        <div>
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-400">
            After
          </div>
          <ConditionBadges classifications={result.classifications_after} />
        </div>
      </div>

      {/* Key metric changes */}
      <div className="space-y-1 border-t border-slate-100 pt-3">
        <MetricDelta
          label="Sales velocity"
          before={result.sales_velocity_before}
          after={result.sales_velocity_after}
          decimals={2}
        />
        <MetricDelta
          label="Days of cover"
          before={result.days_of_cover_before}
          after={result.days_of_cover_after}
        />
        <MetricDelta
          label="Lead time (days)"
          before={result.lead_time_days_before}
          after={result.lead_time_days_after}
          decimals={0}
        />
        <MetricDelta
          label="Current stock"
          before={result.current_stock_before}
          after={result.current_stock_after}
          decimals={0}
        />
      </div>
    </li>
  );
}

export default function ChaosPage() {
  const [scenario, setScenario] = useState<ChaosScenario>("demand_spike");
  const [intensity, setIntensity] = useState<number>(DEFAULT_INTENSITY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ChaosResponse | null>(null);

  const injectDisabled = loading || !(intensity > 0);

  async function handleInject() {
    setLoading(true);
    setError(null);
    try {
      setResult(await runChaos({ scenario, intensity }));
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to inject chaos events.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-display text-2xl text-slate-900">
          Chaos Mode <span aria-hidden="true">🧪</span>
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Inject synthetic disruptive events to demonstrate how STOCKY reacts to
          sudden change. Events apply to a working copy only — your uploaded
          source data is never altered.
        </p>
      </div>

      {/* Controls (Req 18.1) */}
      <div className="card">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-600">Scenario</span>
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value as ChaosScenario)}
              disabled={loading}
              className="rounded-xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200 disabled:opacity-50"
            >
              {SCENARIOS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-600">Intensity</span>
            <input
              type="number"
              min={0}
              step={0.5}
              value={intensity}
              onChange={(e) => setIntensity(Number(e.target.value))}
              disabled={loading}
              className="w-28 rounded-xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200 disabled:opacity-50"
            />
          </label>

          <button
            type="button"
            onClick={() => void handleInject()}
            disabled={injectDisabled}
            className="btn-primary"
          >
            {loading ? "Injecting…" : "Inject events"}
          </button>
        </div>

        <p className="mt-2 text-xs text-slate-500">
          {SCENARIOS.find((s) => s.value === scenario)?.description}
        </p>
        {!(intensity > 0) && (
          <p className="mt-1 text-xs text-red-600">
            Intensity must be greater than 0.
          </p>
        )}
      </div>

      {error && (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}{" "}
          <button
            type="button"
            onClick={() => void handleInject()}
            className="font-medium underline"
          >
            Retry
          </button>
        </div>
      )}

      {loading && (
        <p role="status" className="text-sm text-slate-500">
          Injecting…
        </p>
      )}

      {result && !loading && (
        <div className="space-y-4">
          {/* Source-data reassurance (Req 18.3) */}
          <div
            className={`pill ${
              result.source_unchanged
                ? "bg-emerald-100 text-emerald-800"
                : "bg-amber-100 text-amber-800"
            }`}
          >
            {result.source_unchanged
              ? "✓ Source data unchanged — events were applied to a working copy only."
              : "Warning: the backend reported that source data may have changed."}
          </div>

          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm text-slate-600">
            <span>
              Scenario:{" "}
              <span className="font-medium text-slate-900">
                {SCENARIOS.find((s) => s.value === result.scenario)?.label ??
                  result.scenario}
              </span>
            </span>
            <span>
              Intensity:{" "}
              <span className="font-medium text-slate-900">
                {result.intensity}
              </span>
            </span>
            <span>
              Affected SKUs:{" "}
              <span className="font-medium text-slate-900">
                {result.affected_count}
              </span>
            </span>
          </div>

          {result.results.length === 0 ? (
            <p className="text-sm text-slate-500">
              No SKUs were affected by this scenario.
            </p>
          ) : (
            <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {result.results.map((r) => (
                <SkuResultCard key={r.sku} result={r} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
