import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  ApiError,
  approveRecommendation,
  getCase,
  rejectRecommendation,
  runInvestigation,
  type CaseResponse,
} from "../api/client";
import AgentCase from "../components/AgentCase";
import RecommendationCard from "../components/RecommendationCard";

// Investigation / Case page (Req 15.1-15.4).
//
// Reads :sku from the route. On mount it fetches the case:
//   - 404 (no case yet) -> render a "Start investigation" control (Req 15.4)
//     that runs the investigation and reloads.
//   - case present -> render the four agent outputs / error state via AgentCase
//     (Req 15.1) and, when a recommendation exists, the RecommendationCard with
//     its status (Req 15.2) plus approve/reject controls while pending (Req 15.3).
// Loading and error states are handled explicitly.

type LoadState =
  | { kind: "loading" }
  | { kind: "no-case" }
  | { kind: "loaded"; data: CaseResponse }
  | { kind: "error"; message: string };

export default function CasePage() {
  const { sku } = useParams<{ sku: string }>();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  // busy is true while a mutating action (start / approve / reject) is running.
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!sku) {
      setState({ kind: "error", message: "No SKU specified." });
      return;
    }
    setState({ kind: "loading" });
    try {
      const data = await getCase(sku);
      setState({ kind: "loaded", data });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setState({ kind: "no-case" });
        return;
      }
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to load case.",
      });
    }
  }, [sku]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleStart = useCallback(async () => {
    if (!sku) return;
    setBusy(true);
    try {
      await runInvestigation(sku);
      await load();
    } catch (err) {
      setState({
        kind: "error",
        message:
          err instanceof Error ? err.message : "Failed to start investigation.",
      });
    } finally {
      setBusy(false);
    }
  }, [sku, load]);

  const handleDecision = useCallback(
    async (decide: (id: number) => Promise<unknown>, id: number) => {
      setBusy(true);
      try {
        await decide(id);
        await load();
      } catch (err) {
        setState({
          kind: "error",
          message:
            err instanceof Error ? err.message : "Failed to update recommendation.",
        });
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <section className="space-y-6">
      <div>
        <h1 className="font-display text-3xl text-slate-900">Case: {sku}</h1>
        <p className="mt-1 text-slate-500">
          Four-agent investigation and recommendation review.
        </p>
      </div>

      {state.kind === "loading" && (
        <p className="text-sm text-slate-500" role="status">
          Loading case…
        </p>
      )}

      {state.kind === "error" && (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          <h2 className="font-display text-lg text-rose-800">
            Something went wrong
          </h2>
          <p className="mt-1 text-sm text-rose-700">{state.message}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="btn-ghost mt-4"
          >
            Retry
          </button>
        </div>
      )}

      {state.kind === "no-case" && (
        <div className="card bg-gradient-to-br from-brand-50 via-white to-brand-100 text-center">
          <span className="text-3xl" aria-hidden="true">
            🕵️
          </span>
          <h2 className="mt-2 font-display text-lg text-slate-900">
            No investigation yet
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Run the four-agent investigation to produce a recommendation for
            this SKU.
          </p>
          <button
            type="button"
            onClick={() => void handleStart()}
            disabled={busy}
            className="btn-primary mt-4"
          >
            {busy ? "Starting…" : "Start investigation"}
          </button>
        </div>
      )}

      {state.kind === "loaded" && (
        <>
          <AgentCase agentCase={state.data} />
          {state.data.recommendation && (
            <RecommendationCard
              recommendation={state.data.recommendation}
              busy={busy}
              onApprove={() =>
                void handleDecision(
                  approveRecommendation,
                  state.data.recommendation!.id,
                )
              }
              onReject={() =>
                void handleDecision(
                  rejectRecommendation,
                  state.data.recommendation!.id,
                )
              }
            />
          )}
        </>
      )}
    </section>
  );
}
