import type {
  RecommendationResponse,
  RecommendationStatus,
} from "../api/client";

// Presents a single agent-produced Recommendation and its lifecycle status.
//
// Approve / reject controls are rendered ONLY while the recommendation is
// `pending` (Req 15.3). Once the recommendation moves to any other status the
// controls disappear and the current status is shown instead (Req 15.2). The
// parent owns the approve/reject side effects and the busy flag so this
// component stays presentational.

interface RecommendationCardProps {
  recommendation: RecommendationResponse;
  onApprove: () => void;
  onReject: () => void;
  busy: boolean;
}

/** Tailwind classes for each lifecycle status badge. */
const STATUS_STYLES: Record<RecommendationStatus, string> = {
  pending: "bg-amber-100 text-amber-800",
  approved: "bg-blue-100 text-blue-800",
  rejected: "bg-rose-100 text-rose-800",
  applied: "bg-emerald-100 text-emerald-800",
};

/** Human-readable label for each action kind. */
const ACTION_LABELS: Record<string, string> = {
  reorder: "Reorder",
  adjust_reorder: "Adjust reorder point",
  markdown: "Markdown",
  no_action: "No action",
};

/**
 * Defensive guard for the rationale text. The backend now sends plain-English
 * prose, but if a future regression ever puts a raw JSON blob back into this
 * field we degrade gracefully rather than dumping JSON at the user.
 *
 * - Non-JSON-looking input is returned unchanged.
 * - A JSON-looking string is parsed; if it yields an object with a string
 *   `rationale` field, that value is used. Anything else (parse error, wrong
 *   shape) falls back to a friendly message instead of the raw blob.
 */
function displayRationale(raw: string): string {
  const trimmed = raw.trim();
  const looksLikeJson = trimmed.startsWith("{") || trimmed.startsWith("[");
  if (!looksLikeJson) {
    return raw;
  }

  const fallback = "See recommendation details.";
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "rationale" in parsed &&
      typeof (parsed as { rationale: unknown }).rationale === "string"
    ) {
      return (parsed as { rationale: string }).rationale;
    }
    return fallback;
  } catch {
    return fallback;
  }
}

export default function RecommendationCard({
  recommendation,
  onApprove,
  onReject,
  busy,
}: RecommendationCardProps) {
  const {
    action_kind,
    quantity,
    new_reorder_point,
    rationale,
    status,
  } = recommendation;

  const isPending = status === "pending";
  const actionLabel = ACTION_LABELS[action_kind] ?? action_kind;

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">
            Recommendation
          </h2>
          <p className="mt-1 text-sm text-slate-500">Proposed action</p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs font-medium capitalize ${STATUS_STYLES[status]}`}
        >
          {status}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Action
          </dt>
          <dd className="mt-1 text-sm font-semibold text-slate-900">
            {actionLabel}
          </dd>
        </div>
        {quantity != null && (
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Quantity
            </dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">
              {quantity}
            </dd>
          </div>
        )}
        {new_reorder_point != null && (
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
              New reorder point
            </dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">
              {new_reorder_point}
            </dd>
          </div>
        )}
      </dl>

      <div className="mt-4">
        <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Rationale
        </dt>
        <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
          {displayRationale(rationale)}
        </p>
      </div>

      {isPending ? (
        <div className="mt-5 flex gap-3">
          <button
            type="button"
            onClick={onApprove}
            disabled={busy}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Working…" : "Approve"}
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={busy}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? "Working…" : "Reject"}
          </button>
        </div>
      ) : (
        <p className="mt-5 text-sm text-slate-500">
          This recommendation is <span className="font-medium">{status}</span>.
          No further action is available.
        </p>
      )}
    </section>
  );
}
