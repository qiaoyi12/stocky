// What-If Simulation page (Req 16.1, 16.3).
//
// Reads :sku from the route and (best-effort) prefills the form from the SKU's
// current record via getInventoryItem. The form collects optional hypothetical
// inputs — current_stock, reorder_point, lead_time_days, unit_cost, and a
// free-text sales_history (comma/space separated -> number[]). Only fields the
// Reviewer actually filled in are sent as overrides.
//
// On submit it calls runSimulation(sku, overrides) — a read-only backend
// projection — and renders MetricCompare with the returned current vs projected
// metrics side by side. Loading and error states are handled explicitly.
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  getInventoryItem,
  runSimulation,
  type InventoryItem,
  type SimulationRequest,
  type SimulationResponse,
} from "../api/client";
import MetricCompare from "../components/MetricCompare";

// The four scalar override fields are held as raw strings so the inputs can be
// left blank (meaning "don't override"). sales_history is likewise raw text.
interface FormState {
  current_stock: string;
  reorder_point: string;
  lead_time_days: string;
  unit_cost: string;
  sales_history: string;
}

const EMPTY_FORM: FormState = {
  current_stock: "",
  reorder_point: "",
  lead_time_days: "",
  unit_cost: "",
  sales_history: "",
};

/** Parse a raw scalar field into a number, or null when blank. Returns the
 * sentinel `NaN` for a non-numeric entry so the caller can flag it. */
function parseScalar(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return null;
  }
  return Number(trimmed);
}

/** Parse the sales-history text (comma and/or whitespace separated) into a list
 * of numbers, or null when blank. Returns undefined when any token is
 * non-numeric so the caller can surface a validation error. */
function parseSalesHistory(raw: string): number[] | null | undefined {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return null;
  }
  const tokens = trimmed.split(/[\s,]+/).filter((t) => t.length > 0);
  const numbers = tokens.map(Number);
  if (numbers.some((n) => Number.isNaN(n))) {
    return undefined;
  }
  return numbers;
}

/** Build the SimulationRequest from the form, sending only provided fields.
 * Throws a descriptive Error when a field is present but not a valid number. */
function buildOverrides(form: FormState): SimulationRequest {
  const overrides: SimulationRequest = {};

  const scalarFields: Array<[keyof SimulationRequest, string, string]> = [
    ["current_stock", form.current_stock, "Current stock"],
    ["reorder_point", form.reorder_point, "Reorder point"],
    ["lead_time_days", form.lead_time_days, "Lead time (days)"],
    ["unit_cost", form.unit_cost, "Unit cost"],
  ];

  for (const [key, raw, label] of scalarFields) {
    const value = parseScalar(raw);
    if (value === null) {
      continue;
    }
    if (Number.isNaN(value)) {
      throw new Error(`${label} must be a number.`);
    }
    (overrides[key] as number) = value;
  }

  const history = parseSalesHistory(form.sales_history);
  if (history === undefined) {
    throw new Error("Sales history must be a list of numbers.");
  }
  if (history !== null) {
    overrides.sales_history = history;
  }

  return overrides;
}

export default function SimulationPage() {
  const { sku } = useParams<{ sku: string }>();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [item, setItem] = useState<InventoryItem | null>(null);
  const [result, setResult] = useState<SimulationResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Best-effort prefill of the form from the SKU's current record. A failure
  // here is non-fatal: the Reviewer can still enter values manually.
  useEffect(() => {
    if (!sku) {
      return;
    }
    let cancelled = false;
    getInventoryItem(sku)
      .then((data) => {
        if (cancelled) {
          return;
        }
        setItem(data);
        setForm({
          current_stock: String(data.current_stock),
          reorder_point: String(data.reorder_point),
          lead_time_days: String(data.lead_time_days),
          unit_cost: String(data.unit_cost),
          sales_history: "",
        });
      })
      .catch(() => {
        // Ignore: prefill is optional.
      });
    return () => {
      cancelled = true;
    };
  }, [sku]);

  function updateField(field: keyof FormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!sku) {
        setError("No SKU specified.");
        return;
      }

      let overrides: SimulationRequest;
      try {
        overrides = buildOverrides(form);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Invalid input.");
        return;
      }

      setRunning(true);
      setError(null);
      try {
        const res = await runSimulation(sku, overrides);
        setResult(res);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.message
            : "Failed to run simulation.",
        );
      } finally {
        setRunning(false);
      }
    },
    [sku, form],
  );

  const numberFields: Array<{
    key: keyof FormState;
    label: string;
    step?: string;
  }> = [
    { key: "current_stock", label: "Current stock" },
    { key: "reorder_point", label: "Reorder point" },
    { key: "lead_time_days", label: "Lead time (days)" },
    { key: "unit_cost", label: "Unit cost", step: "0.01" },
  ];

  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-display text-2xl text-slate-900">
          Simulation
          {sku ? <span className="text-slate-400"> · {sku}</span> : null}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Enter hypothetical inputs to project metrics. Only fields you change
          are applied; leave a field as-is to keep the current value. This is
          read-only and never modifies inventory.
        </p>
      </div>

      <form onSubmit={onSubmit} className="card space-y-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {numberFields.map(({ key, label, step }) => (
            <div key={key}>
              <label
                htmlFor={`sim-${key}`}
                className="block text-sm font-medium text-slate-600"
              >
                {label}
              </label>
              <input
                id={`sim-${key}`}
                type="number"
                inputMode="decimal"
                step={step ?? "1"}
                value={form[key]}
                onChange={(e) => updateField(key, e.target.value)}
                className="mt-1 block w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
                placeholder="unchanged"
              />
            </div>
          ))}
        </div>

        <div>
          <label
            htmlFor="sim-sales_history"
            className="block text-sm font-medium text-slate-600"
          >
            Sales history
          </label>
          <input
            id="sim-sales_history"
            type="text"
            value={form.sales_history}
            onChange={(e) => updateField("sales_history", e.target.value)}
            className="mt-1 block w-full rounded-xl border border-slate-200 px-3 py-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
            placeholder="e.g. 3, 5, 2, 0, 4  (comma or space separated)"
          />
          <p className="mt-1 text-xs text-slate-400">
            Optional. A list of recent daily sales, comma or space separated.
            Leave blank to keep the current history.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={running || !sku}
            className="btn-primary"
          >
            {running ? "Simulating…" : "Run simulation"}
          </button>
          {item && (
            <span className="text-sm text-slate-500">
              {item.name} · {item.category}
            </span>
          )}
        </div>
      </form>

      {error && (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}
        </div>
      )}

      {running && (
        <p className="text-sm text-slate-500" role="status">
          Running simulation…
        </p>
      )}

      {result && !running && (
        <div className="card space-y-3">
          <h2 className="font-display text-lg text-slate-900">
            Current vs projected
          </h2>
          <MetricCompare current={result.current} projected={result.projected} />
        </div>
      )}
    </section>
  );
}
