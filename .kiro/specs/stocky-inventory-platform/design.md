# Design Document

## Overview

STOCKY is a hackathon prototype (3–4 day build) that ingests inventory CSVs, runs deterministic Python rules to classify inventory conditions, then orchestrates four LLM agents in a fixed sequence to investigate flagged SKUs and produce a single recommendation. A human reviewer approves or rejects each recommendation, and **only deterministic backend code writes to the database — never the AI agents.**

The whole design is organized around one non-negotiable rule:

> **Safety boundary:** The four LLM agents produce text and structured JSON only. They receive no database handle and no write capability. The single component allowed to mutate inventory data is the deterministic **Apply-Action Service**, and it runs only after explicit reviewer approval.

This document is scoped to keep the MUST HAVE tier finishable in ~3 days with plain tools: React + Vite + Tailwind, FastAPI, SQLite, and one external LLM API called over plain HTTP. No agent frameworks (no LangChain, no CrewAI) — orchestration is a short Python function that calls each agent in order.

### Priority Tiers (from requirements)

- **MUST HAVE (Req 1–15):** CSV ingest, metric computation, condition detection, four-agent orchestration, safety boundary, approve/reject, deterministic apply-on-approval, lifecycle tracking, Dashboard, Inventory Table, Case page.
- **SHOULD HAVE (Req 16–17):** What-If Simulation, Impact/Metrics page.
- **NICE TO HAVE (Req 18–19):** Chaos Mode, Warehouse Visualisation.

## Architecture

### System Context

```
                         ┌──────────────────────────────────────────────┐
   Reviewer (browser)    │                 STOCKY Frontend               │
        │                │   React + Vite + Tailwind                     │
        │  HTTP/JSON     │   Dashboard · Inventory · Case · Impact ·      │
        └───────────────▶│   Simulation · (Chaos) · (Warehouse Viz)      │
                         └───────────────────────┬──────────────────────┘
                                                 │ REST (JSON, CSV upload)
                         ┌───────────────────────▼──────────────────────┐
                         │              STOCKY Backend (FastAPI)         │
                         │                                               │
                         │  ┌──────────────┐   ┌──────────────────────┐  │
                         │  │  Ingest /    │   │  Detection_Engine     │  │
                         │  │  CSV Parser  │──▶│  (pure Python, no LLM) │  │
                         │  └──────────────┘   └──────────┬───────────┘  │
                         │                                │ metrics +     │
                         │                                │ classifications
                         │  ┌─────────────────────────────▼───────────┐  │
                         │  │        Agent_Orchestrator (READ-ONLY)    │  │
                         │  │  Detective → Forecast → Strategy →       │  │
                         │  │  Manager   (1 LLM call each)             │  │───┐
                         │  └─────────────────────┬────────────────────┘  │  │ HTTPS
                         │       writes ONLY a Recommendation (pending)    │  │
                         │                        │                        │  ▼
                         │  ┌─────────────────────▼────────────────────┐  │ ┌───────────┐
                         │  │   Recommendation Lifecycle / Review API   │  │ │ External  │
                         │  │   pending → approved/rejected → applied   │  │ │ LLM API   │
                         │  └─────────────────────┬────────────────────┘  │ └───────────┘
                         │        on approval     │                        │
                         │  ┌═════════════════════▼════════════════════┐  │
                         │  ║  APPLY-ACTION SERVICE  (deterministic)    ║  │
                         │  ║  THE ONLY CODE THAT WRITES INVENTORY DATA ║  │
                         │  ┗═════════════════════┬════════════════════┛  │
                         │                        │                        │
                         │  ┌──────────────┐  ┌───▼─────────────────────┐  │
                         │  │ Simulation   │  │        SQLite           │  │
                         │  │ Engine       │  │   (Inventory_Store)     │  │
                         │  │ (read-only)  │  └─────────────────────────┘  │
                         │  └──────────────┘                               │
                         └──────────────────────────────────────────────┘
```

Two data paths lead into SQLite writes on the inventory tables:

1. **Ingest** writes SKU rows once, at upload time (deterministic).
2. **Apply-Action Service** writes SKU mutations after approval (deterministic).

Nothing else mutates inventory. The Agent_Orchestrator may insert a `recommendations` row with status `pending` (that is a recommendation, not inventory state), but it holds no reference to SKU-write functions.

### The Safety Boundary (explicit architectural component)

This is the centerpiece of the design, so it is called out as its own module rather than a convention.

| Concern | How it is enforced |
| --- | --- |
| Agents cannot write | Agent functions have the signature `agent(context: AgentContext) -> AgentResult`. `AgentContext` is a frozen dataclass of plain data (SKU snapshot, classifications, prior outputs). No DB session, repository, or engine is ever passed in. |
| Agents cannot smuggle a mutation | `AgentResult` is text + parsed JSON. The Manager's JSON is validated into a `ProposedAction` (a typed, closed enum of action kinds). Any field that is not part of the schema is dropped. Output is stored as data; it is never `eval`'d, never turned into SQL, never dispatched as a command. |
| Only one writer | A single module `services/apply_action.py` imports the inventory write functions. The orchestrator package does not import it. This is checked by a unit test that asserts the orchestrator module graph never imports `apply_action` or the inventory-write repository. |
| Write happens only after approval | `apply_action.apply(recommendation_id)` refuses unless the recommendation status is `approved`, and refuses a second time once status is `applied` (idempotent). |

`ProposedAction` closed schema (the only actions the deterministic service knows how to execute):

```python
class ActionKind(str, Enum):
    REORDER        = "reorder"          # increase incoming/target stock by qty
    ADJUST_REORDER = "adjust_reorder"   # set reorder_point to new value
    MARKDOWN       = "markdown"         # flag for markdown (overstock/slow-mover)
    NO_ACTION      = "no_action"        # record decision, change nothing

@dataclass(frozen=True)
class ProposedAction:
    kind: ActionKind
    sku: str
    quantity: int | None = None         # for REORDER
    new_reorder_point: int | None = None  # for ADJUST_REORDER
```

If the Manager returns anything outside this schema, it is coerced to `NO_ACTION` and the raw text is preserved as rationale. The AI can *suggest*, but the executable surface is a small deterministic switch statement.

### Technology Stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Frontend | React + Vite + Tailwind | SPA, React Router for pages, `fetch` for API |
| Backend | Python 3.11 + FastAPI + Uvicorn | Pydantic models for request/response validation |
| ORM / DB | SQLite via SQLAlchemy (or `sqlite3` + thin repo) | single file `stocky.db`; SQLAlchemy keeps repos tidy |
| CSV | Python `csv` stdlib + Pydantic row validation | no pandas needed for MUST tier |
| LLM | External LLM API over `httpx` | one HTTP POST per agent; model + key from env |
| Deploy | Docker (multi-stage) on AWS Lightsail | one container serving API + built static frontend |

### Deployment (Docker on Lightsail)

A single multi-stage Dockerfile: stage 1 builds the Vite frontend to static assets; stage 2 runs FastAPI (Uvicorn) and serves those assets under `/`. SQLite lives on a mounted volume so the DB survives restarts. Environment: `LLM_API_KEY`, `LLM_API_BASE`, `LLM_MODEL`. One Lightsail container instance is enough for a demo.

## Folder Structure

```
stocky/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, router registration, static mount
│   │   ├── config.py               # env config, thresholds (fast/slow/overstock/anomaly)
│   │   ├── db.py                    # SQLite engine/session, init_db()
│   │   ├── models.py                # SQLAlchemy tables (skus, sales, cases, recommendations)
│   │   ├── schemas.py               # Pydantic request/response models
│   │   ├── repositories/
│   │   │   ├── sku_repo.py          # read + the ONLY inventory-write functions
│   │   │   ├── case_repo.py         # agent case/step outputs
│   │   │   └── recommendation_repo.py
│   │   ├── ingest/
│   │   │   ├── csv_parser.py        # parse + validate CSV → SkuRow, count accepted/rejected
│   │   │   └── loader.py            # persist parsed rows (deterministic write)
│   │   ├── detection/
│   │   │   ├── metrics.py           # sales_velocity, days_of_cover, stockout_eta (pure)
│   │   │   └── classify.py          # condition rules (pure, deterministic)
│   │   ├── agents/
│   │   │   ├── context.py           # AgentContext (frozen), AgentResult, ProposedAction
│   │   │   ├── llm_client.py        # httpx wrapper, one call() per agent
│   │   │   ├── detective.py         # role prompt + parse
│   │   │   ├── forecast.py
│   │   │   ├── strategy.py
│   │   │   ├── manager.py           # emits structured ProposedAction JSON
│   │   │   └── orchestrator.py      # sequential runner; READ-ONLY; writes recommendation only
│   │   ├── services/
│   │   │   ├── apply_action.py      # ★ SOLE inventory mutator, post-approval, idempotent
│   │   │   ├── review.py            # approve/reject + lifecycle transitions
│   │   │   ├── simulation.py        # what-if engine (read-only)
│   │   │   ├── dashboard.py         # aggregate counts
│   │   │   └── chaos.py             # NICE TO HAVE: working-copy event injection
│   │   └── routers/
│   │       ├── ingest_router.py
│   │       ├── inventory_router.py
│   │       ├── investigation_router.py
│   │       ├── review_router.py
│   │       ├── dashboard_router.py
│   │       └── simulation_router.py
│   ├── tests/
│   │   ├── test_metrics.py
│   │   ├── test_classify.py
│   │   ├── test_safety_boundary.py  # no-mutation + import-graph checks
│   │   ├── test_lifecycle.py
│   │   └── test_apply_action.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                  # router
│   │   ├── api/client.ts            # fetch wrappers
│   │   ├── pages/
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── InventoryPage.tsx
│   │   │   ├── CasePage.tsx         # Investigation_Page
│   │   │   ├── ImpactPage.tsx
│   │   │   ├── SimulationPage.tsx   # SHOULD HAVE
│   │   │   └── WarehousePage.tsx    # NICE TO HAVE
│   │   ├── components/
│   │   │   ├── UploadCsv.tsx
│   │   │   ├── ConditionBadges.tsx
│   │   │   ├── AgentCase.tsx        # four-step case display
│   │   │   ├── RecommendationCard.tsx  # approve/reject controls
│   │   │   └── MetricCompare.tsx    # current vs projected
│   │   └── index.css                # Tailwind
│   ├── index.html
│   ├── vite.config.ts
│   └── package.json
└── docker-compose.yml               # optional local dev
```

## Database Schema (SQLite)

Four core tables. Sales history is stored as its own table (one row per day per SKU) so metric computation and trend detection can query a window cleanly; a denormalized JSON column is an acceptable shortcut for the hackathon but the table is clearer for aggregation.

```sql
-- SKUs / inventory items
CREATE TABLE skus (
    sku            TEXT PRIMARY KEY,      -- stock-keeping unit id
    name           TEXT NOT NULL,
    category       TEXT NOT NULL,
    current_stock  INTEGER NOT NULL,
    reorder_point  INTEGER NOT NULL,
    lead_time_days INTEGER NOT NULL,
    unit_cost      REAL NOT NULL,
    -- cached deterministic metrics (recomputed on ingest / chaos re-run)
    sales_velocity REAL,                  -- units/day
    days_of_cover  REAL,                  -- NULL when velocity == 0 (undefined)
    stockout_eta   TEXT,                  -- ISO date, NULL when velocity == 0
    no_recent_sales INTEGER DEFAULT 0,    -- 1 when velocity == 0
    classifications TEXT DEFAULT '[]',    -- JSON array of condition labels
    updated_at     TEXT NOT NULL
);

-- Sales history (recent daily sales per SKU)
CREATE TABLE sales_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sku      TEXT NOT NULL REFERENCES skus(sku),
    day      TEXT NOT NULL,               -- ISO date
    units    INTEGER NOT NULL,
    UNIQUE(sku, day)
);

-- Agent case / investigation outputs (one case per investigation run)
CREATE TABLE cases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT NOT NULL REFERENCES skus(sku),
    status        TEXT NOT NULL,          -- running | complete | error
    failed_step   TEXT,                   -- NULL, or detective|forecast|strategy|manager
    detective_out TEXT,                   -- agent output text
    forecast_out  TEXT,
    strategy_out  TEXT,
    manager_out   TEXT,                   -- raw manager text/rationale
    created_at    TEXT NOT NULL,
    completed_at  TEXT
);

-- Recommendations with lifecycle status
CREATE TABLE recommendations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       INTEGER NOT NULL REFERENCES cases(id),
    sku           TEXT NOT NULL REFERENCES skus(sku),
    action_kind   TEXT NOT NULL,          -- reorder|adjust_reorder|markdown|no_action
    quantity      INTEGER,                -- for reorder
    new_reorder_point INTEGER,            -- for adjust_reorder
    rationale     TEXT NOT NULL,          -- Manager's supporting text (stored as text only)
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|applied
    created_at    TEXT NOT NULL,
    decided_at    TEXT,                   -- approve/reject time
    applied_at    TEXT                    -- set by apply-action service
);
```

Notes:
- `days_of_cover` and `stockout_eta` are `NULL` when `sales_velocity == 0` (Req 2.5).
- `rationale` and `manager_out` are plain text sinks. Even if the LLM writes something that looks like a command, it lands here as inert text (Req 9.4).
- `status` is the single lifecycle field driving Req 10–12.

## API Endpoints

All under `/api`. Request/response bodies are JSON except CSV upload (multipart).

| Method | Path | Purpose | Requirements |
| --- | --- | --- | --- |
| POST | `/api/inventory/upload` | Upload CSV; returns `{accepted, rejected: [{row, reason}], missing_column?}` | 1.1–1.5 |
| GET | `/api/inventory` | List all SKUs with metrics + classifications | 14.1, 14.2 |
| GET | `/api/inventory/{sku}` | Single SKU detail | 14.3 |
| POST | `/api/investigations/{sku}/run` | Run the four-agent sequence; returns case id + status | 4–8 |
| GET | `/api/cases/{sku}` | Get the case (four agent outputs + recommendation + status) | 15.1, 15.2 |
| POST | `/api/recommendations/{id}/approve` | Approve pending → approved, then trigger apply | 10.2, 11.1 |
| POST | `/api/recommendations/{id}/reject` | Reject pending → rejected | 10.3 |
| POST | `/api/recommendations/{id}/apply` | Deterministic apply of an approved recommendation (also called internally by approve) | 11.1–11.4 |
| GET | `/api/dashboard` | Condition counts + lifecycle-status counts + prioritised stockout group | 13.1–13.3 |
| POST | `/api/simulation/{sku}` | What-if projection from hypothetical inputs; read-only | 16.1–16.3 |
| GET | `/api/impact` | Recommendation outcome counts + aggregate condition metrics | 17.1, 17.2 |
| POST | `/api/chaos` | (NICE) Inject synthetic events into the working copy, reclassify | 18.1–18.3 |

`approve` returns `{status, applied: bool, error?}`. Approval sets status to `approved`, then invokes `apply_action.apply()`; on success status becomes `applied`, on failure it stays `approved` with an error (Req 11.2, 11.3).

## Deterministic Inventory Analysis Logic

All formulas live in `detection/metrics.py` and `detection/classify.py`. Pure functions, no LLM, no I/O. Thresholds are configurable in `config.py`.

### Metrics (`metrics.py`)

Let `history` be the recent daily sales list and `W = len(history)` the window length (e.g. last 14 days).

```
Sales_Velocity = sum(history) / W                       # units/day  (Req 2.1)

if Sales_Velocity > 0:
    Days_Of_Cover = current_stock / Sales_Velocity      # days       (Req 2.2)
    Stockout_ETA  = today + round(Days_Of_Cover) days   # ISO date   (Req 2.3)
else:
    Days_Of_Cover = None      # undefined                            (Req 2.5)
    Stockout_ETA  = None
    no_recent_sales = True
```

### Condition Rules (`classify.py`)

Configurable thresholds (defaults chosen for demo data):

```python
FAST_MOVING_THRESHOLD   = 20.0   # units/day
SLOW_MOVING_THRESHOLD   = 1.0    # units/day
OVERSTOCK_DOC_THRESHOLD = 90.0   # days of cover
ANOMALY_REL_THRESHOLD   = 0.5    # 50% directional change between window halves
```

Each SKU gets zero or more labels; all applicable labels attach (Req 3.7):

| Label | Rule | Requirement |
| --- | --- | --- |
| `stockout_risk` | `Days_Of_Cover` is defined and `Days_Of_Cover <= lead_time_days` | 3.1 |
| `needs_reorder` | `current_stock <= reorder_point` | 3.2 |
| `fast_moving` | `Sales_Velocity > FAST_MOVING_THRESHOLD` | 3.3 |
| `slow_moving` | `Sales_Velocity <= SLOW_MOVING_THRESHOLD` | 3.4 |
| `overstock` | `Days_Of_Cover` is defined and `Days_Of_Cover > OVERSTOCK_DOC_THRESHOLD` | 3.5 |
| `trend_anomaly` | see below | 3.6 |

**Trend / anomaly rule (3.6):** split the window into an earlier half and a recent half, compare mean sales.

```
earlier_mean = mean(history[: W//2])
recent_mean  = mean(history[W//2 :])
base = max(earlier_mean, epsilon)          # avoid divide-by-zero
rel_change = (recent_mean - earlier_mean) / base
if abs(rel_change) > ANOMALY_REL_THRESHOLD:
    label "trend_anomaly"   (direction = up if rel_change > 0 else down)
```

Classification is fully deterministic: same SKU input always produces the same label set (Req 3.7).

## Agent Workflow

Plain Python orchestration — a loop over four agent functions, one LLM HTTP call each, outputs chained forward. No agent framework.

### Orchestrator (`orchestrator.py`)

```python
STEPS = [detective, forecast, strategy, manager]  # fixed order (Req 8.1)

def run_investigation(sku_record, classifications) -> CaseResult:
    case = case_repo.create(sku=sku_record.sku, status="running")
    ctx = AgentContext(sku=sku_record, classifications=classifications, prior={})
    for step in STEPS:                              # sequential (Req 8.1, 8.2)
        try:
            result = step.run(ctx)                  # exactly one LLM call inside
        except LLMError as e:
            case_repo.mark_failed(case.id, step.name, str(e))  # Req 4.4/5.4/6.4/7.5, 8.3
            return CaseResult(status="error", failed_step=step.name)
        case_repo.save_step(case.id, step.name, result.text)
        ctx = ctx.with_output(step.name, result)    # chain output forward (Req 8.2)
    # Manager produced structured ProposedAction
    action = ctx.prior["manager"].action
    recommendation_repo.create(                     # persist pending (Req 7.3)
        case_id=case.id, sku=sku_record.sku, action=action,
        rationale=ctx.prior["manager"].text, status="pending")
    return CaseResult(status="complete", case_id=case.id)
```

The orchestrator imports `case_repo` and `recommendation_repo` (which can insert case/recommendation rows) but **does not import `sku_repo` write functions or `apply_action`**. Agents receive only `AgentContext` — plain data.

### Agent Roles (one LLM call each)

| Agent | Role in system prompt | Input | Output |
| --- | --- | --- | --- |
| **Detective** | "You investigate why a flagged SKU is in its detected condition. Explain the likely cause. Do not propose actions." | SKU record + classifications (Req 4.2) | Investigation text (Req 4.1) |
| **Forecast** | "You project near-term demand and stock trajectory given the investigation." | SKU + classifications + Detective output (Req 5.1) | Demand/trajectory projection (Req 5.2) |
| **Strategy** | "You propose candidate courses of action. List options with trade-offs. Do not pick one." | SKU + classifications + Detective + Forecast (Req 6.1) | ≥1 candidate options (Req 6.2) |
| **Manager** | "You consolidate everything into ONE recommendation. Respond with strict JSON: `{action_kind, sku, quantity?, new_reorder_point?, rationale}`." | SKU + classifications + all prior outputs (Req 7.1) | Single recommendation, structured (Req 7.2) |

### Manager Structured Output

The Manager prompt demands strict JSON. The orchestrator parses it and validates into `ProposedAction`:

```python
def parse_manager(raw: str) -> ManagerResult:
    try:
        data = json.loads(extract_json(raw))
        kind = ActionKind(data["action_kind"])       # closed enum; unknown → ValueError
        action = ProposedAction(
            kind=kind, sku=data["sku"],
            quantity=data.get("quantity"),
            new_reorder_point=data.get("new_reorder_point"),
        )
    except (ValueError, KeyError, json.JSONDecodeError):
        action = ProposedAction(kind=ActionKind.NO_ACTION, sku=ctx.sku.sku)  # safe fallback
    return ManagerResult(text=raw, action=action)     # raw kept as rationale text
```

This is where Req 9.4 is enforced: the executable part is a validated closed enum; everything else is inert text.

### Chaining Semantics

Each agent's text is appended to `AgentContext.prior` and rendered into the next agent's user prompt (Req 8.2). If any step raises, the sequence stops immediately, the failed step name is recorded, and the case returns `error` (Req 8.3). No later agent runs.

## Simulation Design (What-If, read-only)

`services/simulation.py` reuses the exact metric/classify functions from `detection/`. It takes hypothetical inputs (e.g. changed `current_stock`, `reorder_point`, or a hypothetical daily sales rate), builds an **in-memory** SKU snapshot, and computes projected metrics + classifications. It never opens a write session and never calls `sku_repo` writers or `apply_action`.

```python
def simulate(sku: str, overrides: dict) -> SimulationResult:
    current = sku_repo.get(sku)                        # read only
    hypo = replace(current.as_data(), **overrides)     # in-memory copy
    projected_metrics = metrics.compute(hypo)          # same pure functions
    projected_labels  = classify.classify(hypo, projected_metrics)
    return SimulationResult(
        current=current.metrics_view(),
        projected=projected_metrics,
        projected_classifications=projected_labels,
    )                                                  # nothing persisted (Req 16.2)
```

The response returns current and projected side by side for comparison (Req 16.3). Because it shares the deterministic engine, simulating the same inputs twice yields identical results (Req 16.1).

## Frontend Page Structure

React SPA, React Router, Tailwind for styling. `api/client.ts` wraps the endpoints above.

| Page | Route | Contents | Requirements |
| --- | --- | --- | --- |
| **Dashboard** | `/` | CSV upload widget; condition-category counts; lifecycle-status counts; prioritised stockout-risk group | 13.1–13.3, 1.x |
| **Inventory Table** | `/inventory` | Table of every SKU: current_stock, reorder_point, Sales_Velocity, Days_Of_Cover, condition badges; row click → Case page | 14.1–14.3 |
| **Investigation / Case** | `/cases/:sku` | Four agent outputs (Detective→Forecast→Strategy→Manager); recommendation + status; approve/reject controls when pending; "Start investigation" when none exists | 15.1–15.4 |
| **Impact** | `/impact` | Recommendation outcome counts; aggregate condition metrics across dataset | 17.1, 17.2 |
| **Simulation** | `/simulation/:sku` | Form for hypothetical inputs; current vs projected metric comparison (SHOULD) | 16.1–16.3 |
| **Chaos Mode** | `/chaos` | (NICE) Toggle + inject synthetic events into working copy, then reclassify | 18.1–18.3 |
| **Warehouse Visualisation** | `/warehouse` | (NICE) SKUs grouped visually by condition; node click → Case page | 19.1, 19.2 |

Key components: `UploadCsv` (multipart POST, shows accepted/rejected counts), `ConditionBadges`, `AgentCase` (renders the four steps or an error state), `RecommendationCard` (approve/reject buttons, disabled once non-pending), `MetricCompare`.

## Error Handling

| Failure | Handling | Requirement |
| --- | --- | --- |
| Missing CSV column | Reject whole upload, name the column | 1.3 |
| Non-numeric numeric field | Reject that row, return row number; keep valid rows | 1.4 |
| LLM API error mid-sequence | Stop, mark step failed, case status `error`, return failed step | 4.4, 5.4, 6.4, 7.5, 8.3 |
| Manager returns unparseable JSON | Coerce to `NO_ACTION`, preserve raw as rationale | 7.2, 9.4 |
| Approve/reject a non-pending recommendation | Refuse, return current status | 10.4 |
| Apply fails (e.g. DB error) | Leave status `approved`, return error | 11.3 |
| Re-apply an applied recommendation | No-op (idempotent), status stays `applied` | 11.4 |
| Illegal lifecycle transition | Reject, retain current status | 12.3 |

## Recommendation Lifecycle

```
                approve            apply (deterministic, on approval)
   pending ──────────────▶ approved ──────────────────────────────▶ applied
      │                       ▲  │
      │ reject                │  │ apply fails → stays approved (retry)
      ▼                       └──┘
   rejected
```

Transition validation is centralized in `services/review.py`:

```python
LEGAL = {
    ("pending",  "approved"): True,
    ("pending",  "rejected"): True,
    ("approved", "applied"):  True,
}
def transition(rec, target):
    if (rec.status, target) not in LEGAL:
        raise IllegalTransition(current=rec.status)   # Req 12.3, 10.4
    ...
```

Any request outside this table is refused and the current status is retained (Req 12.2, 12.3).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: CSV ingest round trip and count integrity

For any CSV composed of a set of valid rows and a set of invalid rows (missing required column omitted per-file; non-numeric numeric fields per-row), the upload result satisfies `accepted_count + rejected_count == total_rows`, every accepted record reads back from the store equal to its parsed input, and every rejected row is reported with its correct row number (or, for a missing column, the missing column name).

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

### Property 2: Metric correctness including the zero-velocity edge

For any SKU with recent sales history over window W, `Sales_Velocity == sum(history)/W`; when `Sales_Velocity > 0` then `Days_Of_Cover == current_stock / Sales_Velocity` and `Stockout_ETA == base_date + round(Days_Of_Cover)` days; and when `Sales_Velocity == 0` then `Days_Of_Cover` is undefined, `Stockout_ETA` is undefined, and the SKU is marked as having no recent sales.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

### Property 3: Deterministic, complete condition classification

For any SKU, classifying it twice yields the identical label set, and that set contains exactly the labels whose rules hold: `stockout_risk` iff Days_Of_Cover is defined and ≤ lead_time_days; `needs_reorder` iff current_stock ≤ reorder_point; `fast_moving` iff velocity > fast threshold; `slow_moving` iff velocity ≤ slow threshold; `overstock` iff Days_Of_Cover is defined and > overstock threshold; `trend_anomaly` iff the relative change between window halves exceeds the anomaly threshold.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

### Property 4: Agents never mutate inventory

For any store state and any flagged SKU, running any single agent (Detective, Forecast, Strategy, Manager) or the full orchestration leaves every `skus` and `sales_history` row byte-for-byte unchanged; the only new row an investigation may create is a `recommendations` row with status `pending`.

**Validates: Requirements 4.3, 5.3, 6.3, 7.4, 9.1, 9.2**

### Property 5: Agent output that encodes a mutation is inert

For any agent output string — including strings crafted to look like SQL, shell commands, or write instructions — the system stores that output as text (in an agent-output or rationale column) and the inventory tables remain unchanged; no part of the output is executed as a data operation.

**Validates: Requirements 9.4**

### Property 6: Fixed sequential order with fail-stop

For any successful investigation, the recorded execution order equals exactly `[Detective, Forecast, Strategy, Manager]`; and for any run where the agent at position i fails, no agent after position i executes and the reported failed step equals position i.

**Validates: Requirements 8.1, 8.2, 8.3, 4.4, 5.4, 6.4, 7.5**

### Property 7: Manager produces one well-formed recommendation persisted as pending

For any Manager output, parsing yields exactly one recommendation whose `action_kind` is a member of the closed action enum (unknown or malformed output coerces to `no_action`) with a non-empty rationale; and when the case completes successfully, the persisted recommendation reads back with status `pending`.

**Validates: Requirements 7.2, 7.3**

### Property 8: Lifecycle validity

For any recommendation, its status is always one of {pending, approved, rejected, applied}; a transition takes effect only if it is one of pending→approved, pending→rejected, or approved→applied; and any other requested transition (including approve/reject on a non-pending recommendation) is refused with the current status retained.

**Validates: Requirements 10.2, 10.3, 10.4, 12.1, 12.2, 12.3, 11.2**

### Property 9: Deterministic apply produces the exact action delta

For any approved recommendation carrying a `ProposedAction`, applying it transforms the inventory exactly by that action's deterministic delta (reorder increases target/incoming stock by quantity; adjust_reorder sets reorder_point to the new value; markdown flags the SKU; no_action changes nothing) and no other SKU is affected.

**Validates: Requirements 11.1**

### Property 10: Apply is idempotent (applied at most once)

For any recommendation, applying it two or more times yields the same inventory state as applying it exactly once, and the status remains `applied`.

**Validates: Requirements 11.4**

### Property 11: Only the deterministic apply path (post-approval) changes inventory

For any sequence of orchestration and simulation calls performed without an approval-plus-apply, the inventory tables are unchanged; inventory changes appear only after `apply_action.apply` runs on an approved recommendation.

**Validates: Requirements 9.3, 16.2**

### Property 12: Aggregate condition counts are consistent

For any classified dataset, the per-condition counts reported by the Dashboard and Impact page equal the number of SKUs carrying that classification, recomputed independently from the stored classifications.

**Validates: Requirements 13.1, 17.2**

### Property 13: Aggregate lifecycle counts are consistent

For any set of recommendations, the per-status counts reported by the Dashboard and Impact page equal the actual number of recommendations in each status, and those counts sum to the total number of recommendations.

**Validates: Requirements 13.2, 17.1**

### Property 14: Simulation is deterministic and read-only

For any hypothetical inputs, the Simulation_Engine's projected metrics and classifications equal the deterministic Detection_Engine functions applied to those inputs, running the same simulation twice yields identical results, and the inventory tables are unchanged after simulation.

**Validates: Requirements 16.1, 16.2, 16.3**

### Property 15: Chaos affects only the working copy

For any Chaos_Mode injection, the originally uploaded source dataset is unchanged while the working dataset reflects the injected events, and the affected SKUs are reclassified.

**Validates: Requirements 18.1, 18.2, 18.3**

## Testing Strategy

**Dual approach.** Property-based tests cover the deterministic engine and lifecycle (Properties 1–15 above). Example/integration tests cover LLM-dependent shapes and UI wiring.

- **Property tests (≥100 iterations each, Hypothesis):** metric correctness, classification determinism/completeness, no-mutation safety invariant, lifecycle validity, apply delta + idempotence, aggregation counts, simulation determinism. Each test is tagged `Feature: stocky-inventory-platform, Property {n}: {property text}`.
- **Example tests:** Detective/Forecast/Strategy/Manager output shape with a mocked LLM; orchestrator prompt-assembly (spy on the LLM client to confirm each agent receives the prior outputs); Dashboard prioritised stockout group.
- **Edge-case tests:** LLM error at each of the four steps → case error + failed step; apply failure leaves status approved.
- **Safety import-graph test:** assert the `agents/orchestrator` module graph never imports `services/apply_action` or the `sku_repo` write functions — the safety boundary as an automated check.
- **Frontend:** component tests for `RecommendationCard` (controls only when pending), `UploadCsv` (accepted/rejected display), and routing from Inventory/Warehouse rows to the Case page.

LLM calls are always mocked in tests so the deterministic properties run fast and offline.
