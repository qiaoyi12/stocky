// Typed fetch wrappers for the STOCKY backend REST API.
//
// All requests are made against relative URLs ("" base), so Vite's dev-server
// proxy (`/api` -> backend) and same-origin static serving in production both
// work without changes. A single `request` helper sets JSON headers, checks
// `res.ok` (throwing an ApiError carrying the backend `detail` on non-2xx), and
// parses the JSON body. File uploads use `FormData` and deliberately omit the
// JSON content-type so the browser sets the multipart boundary.
//
// The interfaces mirror the backend Pydantic schemas (see backend/app/schemas.py)
// so responses map cleanly onto typed values on the frontend.

// --- Shared types -----------------------------------------------------------

export type ActionKind = "reorder" | "adjust_reorder" | "markdown" | "no_action";

export type RecommendationStatus = "pending" | "approved" | "rejected" | "applied";

export type CaseStatus = "running" | "complete" | "error";

// --- CSV ingest -------------------------------------------------------------

export interface RejectedRow {
  row: number;
  reason: string;
}

export interface UploadResult {
  accepted: number;
  rejected_count: number;
  rejected: RejectedRow[];
  missing_column?: string | null;
}

// --- Inventory --------------------------------------------------------------

export interface InventoryItem {
  sku: string;
  name: string;
  category: string;
  current_stock: number;
  reorder_point: number;
  lead_time_days: number;
  unit_cost: number;
  sales_velocity?: number | null;
  days_of_cover?: number | null;
  stockout_eta?: string | null;
  no_recent_sales: boolean;
  classifications: string[];
  updated_at?: string | null;
}

export interface InventoryListResponse {
  items: InventoryItem[];
}

// --- Agent case -------------------------------------------------------------

export interface RecommendationResponse {
  id: number;
  case_id: number;
  sku: string;
  action_kind: ActionKind;
  quantity?: number | null;
  new_reorder_point?: number | null;
  rationale: string;
  status: RecommendationStatus;
  created_at?: string | null;
  decided_at?: string | null;
  applied_at?: string | null;
}

export interface CaseResponse {
  id: number;
  sku: string;
  status: CaseStatus;
  failed_step?: string | null;
  detective_out?: string | null;
  forecast_out?: string | null;
  strategy_out?: string | null;
  manager_out?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  recommendation?: RecommendationResponse | null;
}

export interface RunInvestigationResponse {
  case_id: number;
  status: CaseStatus;
  failed_step?: string | null;
}

// --- Review / apply ---------------------------------------------------------

export interface ReviewResult {
  status: RecommendationStatus;
  applied: boolean;
  error?: string | null;
}

// --- Overstock capital ------------------------------------------------------

/** Per-SKU contribution to the capital tied up in overstock. */
export interface CapitalTiedUpItem {
  sku: string;
  excess_units: number;
  capital_tied_up: number;
}

/** Aggregate "cost of overstock" figure plus its per-SKU breakdown. */
export interface CapitalTiedUp {
  total: number;
  by_sku: CapitalTiedUpItem[];
}

// --- Dashboard --------------------------------------------------------------

export interface DashboardResponse {
  condition_counts: Record<string, number>;
  lifecycle_counts: Record<string, number>;
  stockout_risk: InventoryItem[];
  overstock_capital_tied_up?: CapitalTiedUp;
}

// --- Impact -----------------------------------------------------------------

export interface ImpactResponse {
  outcome_counts: Record<string, number>;
  condition_counts: Record<string, number>;
  overstock_capital_tied_up?: CapitalTiedUp;
}

// --- Simulation -------------------------------------------------------------

export interface MetricsView {
  sales_velocity?: number | null;
  days_of_cover?: number | null;
  stockout_eta?: string | null;
  no_recent_sales: boolean;
  classifications: string[];
}

export interface SimulationRequest {
  current_stock?: number | null;
  reorder_point?: number | null;
  lead_time_days?: number | null;
  unit_cost?: number | null;
  sales_history?: number[] | null;
}

export interface SimulationResponse {
  sku: string;
  current: MetricsView;
  projected: MetricsView;
}

// --- Chaos ------------------------------------------------------------------

/** Synthetic disruption scenarios the chaos service can inject (Req 18.1). */
export type ChaosScenario = "demand_spike" | "demand_drop" | "supply_delay";

/** Per-SKU before/after view produced by injecting chaos into a working copy
 * and re-running the Detection_Engine classification (Req 18.2). */
export interface SkuChaosResult {
  sku: string;
  current_stock_before: number;
  current_stock_after: number;
  lead_time_days_before: number;
  lead_time_days_after: number;
  sales_history_before: number[];
  sales_history_after: number[];
  sales_velocity_before?: number | null;
  sales_velocity_after?: number | null;
  days_of_cover_before?: number | null;
  days_of_cover_after?: number | null;
  classifications_before: string[];
  classifications_after: string[];
}

export interface ChaosRequest {
  scenario: ChaosScenario;
  /** Disruption magnitude; must be > 0 (backend default 2.0). */
  intensity?: number;
  /** Optional subset of SKUs to affect; omit to let the backend choose. */
  skus?: string[];
}

export interface ChaosResponse {
  scenario: ChaosScenario;
  intensity: number;
  affected_count: number;
  results: SkuChaosResult[];
  /** True when the originally uploaded source data was left untouched (Req 18.3). */
  source_unchanged: boolean;
}

// --- Uploads -----------------------------------------------------------------

export interface UploadSummary {
  id: number;
  filename: string;
  uploaded_at: string;
  sku_count: number;
}

// --- Base fetch helper ------------------------------------------------------

/** Base URL for API calls. Empty string keeps requests relative so the Vite
 * dev-server proxy (`/api` -> backend) and same-origin production serving both
 * work unchanged. */
const BASE_URL = "";

/** Error thrown for any non-2xx response, carrying the HTTP status and the
 * backend-supplied detail message when available. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Extract a human-readable error message from a failed response body. FastAPI
 * returns `{ detail: ... }`; fall back to the raw text or the status line. */
async function extractError(res: Response): Promise<string> {
  const text = await res.text();
  if (text) {
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string") {
        return body.detail;
      }
      if (body.detail != null) {
        return JSON.stringify(body.detail);
      }
    } catch {
      // Non-JSON body; fall through to the raw text.
    }
    return text;
  }
  return `${res.status} ${res.statusText}`;
}

/** Core request helper: throws ApiError on non-2xx, otherwise parses JSON. When
 * `body` is a FormData no JSON content-type is set (the browser adds the
 * multipart boundary); any other body is JSON-serialised with a JSON header. */
async function request<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const { method = "GET", body } = options;
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;

  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: payload,
  });

  if (!res.ok) {
    throw new ApiError(res.status, await extractError(res));
  }

  // 204 / empty bodies parse to undefined; callers for such endpoints type as void.
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// --- Endpoint wrappers ------------------------------------------------------

/** POST /api/inventory/upload — multipart CSV upload (Req 1.1, 1.5). */
export function uploadInventory(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  return request<UploadResult>("/api/inventory/upload", {
    method: "POST",
    body: form,
  });
}

/** GET /api/inventory — list every SKU with cached metrics (Req 14.1, 14.2). */
export function listInventory(): Promise<InventoryListResponse> {
  return request<InventoryListResponse>("/api/inventory");
}

/** GET /api/inventory/{sku} — single-SKU detail (Req 14.3). */
export function getInventoryItem(sku: string): Promise<InventoryItem> {
  return request<InventoryItem>(`/api/inventory/${encodeURIComponent(sku)}`);
}

/** POST /api/investigations/{sku}/run — run the four-agent investigation (Req 15.1). */
export function runInvestigation(sku: string): Promise<RunInvestigationResponse> {
  return request<RunInvestigationResponse>(
    `/api/investigations/${encodeURIComponent(sku)}/run`,
    { method: "POST" },
  );
}

/** GET /api/cases/{sku} — fetch a SKU's case with agent outputs + recommendation (Req 15.1, 15.2). */
export function getCase(sku: string): Promise<CaseResponse> {
  return request<CaseResponse>(`/api/cases/${encodeURIComponent(sku)}`);
}

/** POST /api/recommendations/{id}/approve — approve then deterministically apply (Req 10.2, 11.1). */
export function approveRecommendation(id: number): Promise<ReviewResult> {
  return request<ReviewResult>(`/api/recommendations/${id}/approve`, {
    method: "POST",
  });
}

/** POST /api/recommendations/{id}/reject — reject a pending recommendation (Req 10.3). */
export function rejectRecommendation(id: number): Promise<ReviewResult> {
  return request<ReviewResult>(`/api/recommendations/${id}/reject`, {
    method: "POST",
  });
}

/** POST /api/recommendations/{id}/apply — deterministic apply of an approved recommendation (Req 11.1). */
export function applyRecommendation(id: number): Promise<ReviewResult> {
  return request<ReviewResult>(`/api/recommendations/${id}/apply`, {
    method: "POST",
  });
}

/** GET /api/dashboard — condition + lifecycle counts and stockout-risk group (Req 13.1-13.3). */
export function getDashboard(): Promise<DashboardResponse> {
  return request<DashboardResponse>("/api/dashboard");
}

/** POST /api/simulation/{sku} — read-only what-if metric comparison (Req 16.1, 16.3). */
export function runSimulation(
  sku: string,
  overrides: SimulationRequest,
): Promise<SimulationResponse> {
  return request<SimulationResponse>(
    `/api/simulation/${encodeURIComponent(sku)}`,
    { method: "POST", body: overrides },
  );
}

/** GET /api/impact — recommendation outcome counts + aggregate condition metrics (Req 17.1, 17.2). */
export function getImpact(): Promise<ImpactResponse> {
  return request<ImpactResponse>("/api/impact");
}

/** POST /api/chaos — inject synthetic events into a working copy and reclassify (Req 18.1-18.3). */
export function runChaos(payload: ChaosRequest): Promise<ChaosResponse> {
  return request<ChaosResponse>("/api/chaos", { method: "POST", body: payload });
}

/** GET /api/uploads — the upload history, most recent first. */
export function listUploads(): Promise<UploadSummary[]> {
  return request<UploadSummary[]>("/api/uploads");
}

/** DELETE /api/uploads — delete all uploaded inventory data (full reset). */
export function deleteAllUploads(): Promise<void> {
  return request<void>("/api/uploads", { method: "DELETE" });
}
