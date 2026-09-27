// AI Agents roster page (/agents).
//
// Introduces the four agent personas that power an investigation (Pip the
// Detective, Mimi the Forecaster, Bibi the Strategist and Stocky the
// Manager) and, below that, a REAL summary of SKUs that currently need
// investigation — derived from listInventory() + getDashboard() the same way
// DashboardPage derives its own numbers. This page never triggers an LLM
// call itself; it only links into the existing Case page (/cases/:sku) so the
// user can start an investigation from there.

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  getDashboard,
  listInventory,
  type DashboardResponse,
  type InventoryItem,
} from "../api/client";
import { AGENT_PERSONAS, PERSONA_ORDER, colorClasses } from "../lib/agentPersonas";

/** Classifications considered "risky" — mirrors DashboardPage's definition. */
const RISKY_CLASSIFICATIONS = new Set([
  "stockout_risk",
  "needs_reorder",
  "overstock",
  "slow_moving",
  "trend_anomaly",
]);

function needsInvestigation(item: InventoryItem): boolean {
  return item.classifications.some((c) => RISKY_CLASSIFICATIONS.has(c));
}

/** Honest one-line description of what each agent actually does. */
const PERSONA_DESCRIPTIONS: Record<string, string> = {
  detective:
    "Investigates why a flagged SKU is in its current condition, digging into the numbers behind the alert.",
  forecast:
    "Projects near-term demand and stock trajectory so we know what's coming next.",
  strategy:
    "Proposes 2-3 candidate courses of action with their trade-offs.",
  manager:
    "Consolidates everything into one recommendation for a human to approve.",
};

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; items: InventoryItem[]; dashboard: DashboardResponse }
  | { kind: "error"; message: string };

export default function AgentsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const [dashboard, inventory] = await Promise.all([
        getDashboard(),
        listInventory(),
      ]);
      setState({ kind: "loaded", items: inventory.items, dashboard });
    } catch (err) {
      setState({
        kind: "error",
        message:
          err instanceof ApiError ? err.message : "Failed to load agents data.",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const needsAttention =
    state.kind === "loaded" ? state.items.filter(needsInvestigation) : [];

  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-display text-3xl text-slate-900">
          AI Agents <span aria-hidden="true">🤖</span>
        </h1>
        <p className="mt-1 text-slate-500">
          Meet the four agents that work together on every investigation.
        </p>
      </div>

      {/* Persona roster */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {PERSONA_ORDER.map((key) => {
          const persona = AGENT_PERSONAS[key];
          return (
            <div key={key} className="card">
              <span
                className={`flex h-12 w-12 items-center justify-center rounded-full text-2xl ${colorClasses(
                  persona.color,
                )}`}
                aria-hidden="true"
              >
                {persona.emoji}
              </span>
              <h2 className="mt-3 font-display text-lg text-slate-900">
                {persona.name}
              </h2>
              <p className="text-sm font-semibold text-slate-500">
                {persona.role}
              </p>
              <p className="mt-2 text-sm text-slate-600">
                {PERSONA_DESCRIPTIONS[key]}
              </p>
            </div>
          );
        })}
      </div>

      {/* Real summary of SKUs needing investigation */}
      <div className="card">
        <h2 className="font-display text-xl text-slate-900">
          Where the agents are needed right now
        </h2>

        {state.kind === "loading" && (
          <p className="mt-3 text-sm text-slate-500" role="status">
            Loading inventory…
          </p>
        )}

        {state.kind === "error" && (
          <div
            className="mt-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
            role="alert"
          >
            {state.message}{" "}
            <button
              type="button"
              onClick={() => void load()}
              className="font-medium underline"
            >
              Retry
            </button>
          </div>
        )}

        {state.kind === "loaded" && (
          <>
            <p className="mt-2 text-sm text-slate-600">
              <span className="font-semibold text-slate-900">
                {needsAttention.length}
              </span>{" "}
              SKU{needsAttention.length === 1 ? "" : "s"} currently need
              investigation
              {state.dashboard.condition_counts.stockout_risk ? (
                <>
                  {" "}
                  (
                  <span className="font-semibold text-slate-900">
                    {state.dashboard.condition_counts.stockout_risk}
                  </span>{" "}
                  at risk of stockout)
                </>
              ) : null}
              .
            </p>

            {needsAttention.length === 0 ? (
              <p className="mt-3 text-sm text-slate-500">
                Nothing flagged right now — the warehouse looks healthy.
              </p>
            ) : (
              <ul className="mt-4 space-y-2">
                {needsAttention.slice(0, 5).map((item) => (
                  <li key={item.sku}>
                    <Link
                      to={`/cases/${encodeURIComponent(item.sku)}`}
                      className="flex items-center justify-between rounded-xl px-3 py-2 text-sm transition hover:bg-brand-50"
                    >
                      <span className="font-medium text-slate-800">
                        {item.name}{" "}
                        <span className="text-slate-400">({item.sku})</span>
                      </span>
                      <span className="font-semibold text-brand-700">
                        Investigate →
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}
