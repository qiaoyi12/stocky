# Design Document

## Overview

This feature adds dataset-scoped storage to STOCKY. Today `POST /api/inventory/upload` implicitly replaces the single working dataset. After this change, every upload creates a new, independently-addressable `Dataset`, and `skus` / `cases` / `recommendations` rows all carry a `dataset_id` tying them to the dataset that produced them. Exactly one dataset is "active" at a time; every existing read path is scoped to the active dataset's rows only. A new `dataset_repo.py` is the sole sanctioned write surface for `Dataset` rows and for the cascading delete of a dataset's dependent rows, mirroring the existing `sku_repo.py` / `apply_action.py` split that keeps agents off the write path.

The database is SQLite with no migration framework (Alembic) in this project. Since this is a hackathon demo backed by a disposable `stocky.db`, schema changes are made directly in `app/models.py`; the existing dev workflow already recreates the DB from scratch (`init_db()` calls `Base.metadata.create_all()`), so there is no live data to migrate.

## Primary Key Decision: `skus`

**Current state:** `Sku.sku` is a plain `String` primary key (`app/models.py`). `sales_history.sku`, `cases.sku`, and `recommendations.sku` are `ForeignKey("skus.sku")`.

**Problem:** Requirement 1 requires that two datasets may legitimately contain the same SKU code (e.g. re-uploading a similar catalog), and that their rows stay fully isolated. A bare-string PK cannot hold two rows with the same `sku` value.

**Decision: composite primary key `(dataset_id, sku)` on `skus`.** Rejected alternative: a new surrogate autoincrement PK (e.g. `id`) on `skus`, with `sku` demoted to a plain indexed column.

**Justification for composite PK over a surrogate key:**

- `sku` remains the natural, human-meaningful identifier used throughout the API (`GET /api/inventory/{sku}`, `POST /api/investigations/{sku}/run`, `GET /api/cases/{sku}`, chaos/simulation endpoints). Every one of those routes is keyed by `sku` string in the URL today, scoped implicitly to "the" dataset. Introducing a surrogate integer PK would require either exposing that surrogate id through every one of those URLs (a much larger, cross-cutting API change) or doing an extra `(dataset_id, sku) -> id` lookup indirection inside every repo function — more moving parts for no benefit in a project with no other table that needs to reference a `Sku` row by surrogate id.
- The natural key for "one inventory item" was always `sku` scoped to its dataset — `(dataset_id, sku)` **is** that natural key, made explicit. A composite PK expresses the domain invariant directly: uniqueness of SKU code is per-dataset, never global.
- Downstream tables (`sales_history`, `cases`, `recommendations`) already reference `sku` by string, not by a surrogate id. Keeping `sku` as (part of) the PK means their foreign keys stay simple `(dataset_id, sku)` composite foreign keys instead of needing to be rewired to point at a new surrogate column.
- This is a demo codebase with a freshly-recreated SQLite file; there is no live-migration cost to weigh against the surrogate-key alternative, so the simpler, more explicit composite key wins.

**Consistently applied consequence:** every function in `sku_repo.py` that currently takes a bare `sku: str` now also takes `dataset_id: int`, and every lookup/write becomes a `(dataset_id, sku)` composite lookup. `sales_history`, `cases`, and `recommendations` all gain a `dataset_id` column and their FK/lookup logic is scoped the same way (see "Data Models" and "Repository Signature Changes" below).

`Dataset` itself gets a plain surrogate autoincrement integer PK (`id`) — it is a new top-level entity with no natural key conflict, so a surrogate id is the simplest choice and matches `Case`/`Recommendation`'s existing style.

## Architecture

```
                         ┌─────────────────────────┐
                         │   ingest_router.py       │
                         │  POST /api/inventory/    │
                         │  upload                  │
                         └───────────┬──────────────┘
                                     │ 1. create dataset (dataset_repo.create)
                                     │ 2. parse_csv (unchanged, pure)
                                     │ 3. load_rows(session, rows, dataset_id=...)
                                     │ 4. activate dataset (dataset_repo.activate)
                                     ▼
        ┌───────────────────────────────────────────────────┐
        │                  loader.py                         │
        │   load_rows(..., dataset_id)                        │
        │   recompute_metrics_for(..., dataset_id)             │
        │   recompute_all_metrics(..., dataset_id)              │
        └───────────────────┬────────────────────────────────┘
                             │ sku_repo.upsert_sku(dataset_id=...)
                             │ sku_repo.replace_sales_history(dataset_id=...)
                             │ sku_repo.set_metrics(dataset_id=...)
                             ▼
                     ┌───────────────┐
                     │   sku_repo.py  │   (write surface, unchanged shape,
                     │  (+dataset_id) │    every fn now dataset-scoped)
                     └───────────────┘

  ┌─────────────────────────────────────────────────────────────┐
  │                       dataset_repo.py  (NEW)                 │
  │  create(filename) -> Dataset                                  │
  │  activate(dataset_id)  [deactivates all others, same txn]     │
  │  rename(dataset_id, display_name)                              │
  │  delete(dataset_id)  [cascades skus/sales_history/cases/recs]   │
  │  list_with_counts() -> [DatasetSummary]                         │
  │  get_active(session) -> Optional[Dataset]                       │
  │  get_active_id(session) -> Optional[int]                         │
  └─────────────────────────────────────────────────────────────┘
                             ▲
                             │ imported by datasets_router.py ONLY
                             │ (never by agents/*, orchestrator.py)
                             │
  ┌─────────────────────────┴─────────────────────────────────────┐
  │  Scoped read paths — every one resolves the active dataset_id    │
  │  via dataset_repo.get_active_id() then filters:                   │
  │                                                                    │
  │  inventory_router.py   -> sku_repo.list_all(session, dataset_id)   │
  │  dashboard.py (service) -> sku_repo.list_all / recommendation_repo  │
  │  impact_router (via dashboard.py) -> same aggregation, dataset_id   │
  │  investigation_router.py -> sku_repo.get / case_repo (dataset_id)   │
  └────────────────────────────────────────────────────────────────┘
```

The **safety boundary is preserved and extended, not weakened**: `dataset_repo.py` joins `sku_repo.py`'s write section and `apply_action.py` as sanctioned write surfaces. Agents (`app/agents/detective.py`, `forecast.py`, `strategy.py`, `manager.py`) and `orchestrator.py` must not import `dataset_repo`. The orchestrator already only imports `case_repo` / `recommendation_repo` (which now also carry `dataset_id`, populated from the SKU snapshot's dataset, not chosen by the agent).

## Components and Interfaces

### 1. `app/models.py` (modified)

- New `Dataset` model.
- `Sku.sku` PK changed from plain PK to composite `(dataset_id, sku)`; `dataset_id` is a `ForeignKey("datasets.id")`.
- `SalesHistory`, `Case`, `Recommendation` each gain a `dataset_id` column (`ForeignKey("datasets.id")`, `nullable=False`). `SalesHistory`/`Case`/`Recommendation`'s existing `sku` FK to `skus.sku` becomes a composite FK to `skus.(dataset_id, sku)`.
- `UniqueConstraint("dataset_id", "sku", "day")` replaces the old `UniqueConstraint("sku", "day")` on `sales_history`.

### 2. `app/repositories/dataset_repo.py` (new)

The sanctioned write surface for `Dataset` rows, following the same read/write banner convention as `sku_repo.py`.

```python
def create(session: Session, *, filename: str) -> Dataset: ...
def get(session: Session, dataset_id: int) -> Optional[Dataset]: ...
def get_active(session: Session) -> Optional[Dataset]: ...
def get_active_id(session: Session) -> Optional[int]: ...
def list_with_counts(session: Session) -> List[DatasetSummary]: ...

# WRITE FUNCTIONS
def activate(session: Session, dataset_id: int) -> Dataset: ...
def rename(session: Session, dataset_id: int, *, display_name: str) -> Dataset: ...
def delete(session: Session, dataset_id: int) -> None: ...
```

- `create(filename)`: inserts a new `Dataset` row with `filename=filename`, `display_name=filename` (Req 3.4), `uploaded_at=now_iso()`, `is_active=0`. Returns the row (with its assigned `id`) after `flush()`. Does **not** activate it — activation is a separate, explicit step (Req 3.1 vs 3.3 are sequenced separately by the caller).
- `activate(dataset_id)`: raises `UnknownDataset` if the id does not resolve. Otherwise, within the same function (single transaction, not yet committed — commit remains the router's job): sets `is_active = 0` on every `Dataset` row, then sets `is_active = 1` on the target row (Req 2.2). Touches only `datasets.is_active`; never touches `skus`/`cases`/`recommendations` (Req 2.3).
- `rename(dataset_id, display_name)`: raises `UnknownDataset` if the id does not resolve; raises `EmptyDisplayName` if `display_name.strip()` is empty (Req 7.3). Otherwise sets `display_name = display_name` and flushes.
- `delete(dataset_id)`: raises `UnknownDataset` if the id does not resolve (Req 4.5) — checked *before* any delete statement runs, so a not-found request makes zero changes. Otherwise deletes, in this order, every `recommendations` row with matching `dataset_id`, every `cases` row with matching `dataset_id`, every `sales_history` row with matching `dataset_id`, every `skus` row with matching `dataset_id`, and finally the `Dataset` row itself (Req 4.1-4.4; dependent-first order avoids relying on `ON DELETE CASCADE` behavior, keeping the cascade explicit and testable). Does not touch `is_active` on any *other* dataset — if the deleted dataset was active, no dataset is active afterward (Req 4.6); the caller must explicitly activate a remaining one.
- `list_with_counts()`: returns one `DatasetSummary` per `Dataset` row (id, filename, display_name, uploaded_at, sku_count, is_active), with `sku_count` computed as `SELECT COUNT(*) FROM skus WHERE dataset_id = :id` per dataset (Req 5.1, 5.2).
- `get_active_id(session)`: convenience used by every scoped read path; returns `None` when no dataset is active (Req 2.4), which callers treat as "no active dataset -> empty result".

`DatasetSummary` is a small dataclass/Pydantic model (lives in `schemas.py`) shaped as the `GET /api/datasets` response item.

### 3. `app/repositories/sku_repo.py` (modified signatures)

Every function gains a `dataset_id: int` parameter and every SKU lookup becomes a composite `(dataset_id, sku)` lookup (via `session.get(Sku, (dataset_id, sku))` for composite-PK gets, or a `where(Sku.dataset_id == dataset_id, Sku.sku == sku)` clause elsewhere):

```python
def get(session: Session, dataset_id: int, sku: str) -> Optional[Sku]: ...
def list_all(session: Session, dataset_id: int) -> List[Sku]: ...
def exists(session: Session, dataset_id: int, sku: str) -> bool: ...
def get_sales_history(session: Session, dataset_id: int, sku: str) -> List[SalesHistory]: ...
def get_sales_units(session: Session, dataset_id: int, sku: str) -> List[int]: ...

# WRITE FUNCTIONS
def upsert_sku(session: Session, *, dataset_id: int, sku: str, ...) -> Sku: ...
def replace_sales_history(session: Session, *, dataset_id: int, sku: str, units, base_date=None) -> List[SalesHistory]: ...
def set_metrics(session: Session, *, dataset_id: int, sku: str, ...) -> Sku: ...
def apply_reorder(session: Session, *, dataset_id: int, sku: str, quantity: int) -> Sku: ...
def apply_adjust_reorder(session: Session, *, dataset_id: int, sku: str, new_reorder_point: int) -> Sku: ...
```

`list_all` orders by `(Sku.dataset_id, Sku.sku)` — but since it now always takes a `dataset_id`, in practice it orders by `Sku.sku` within that one dataset.

### 4. `app/ingest/loader.py` (modified)

- `load_rows(session, rows, *, dataset_id: int, base_date=None) -> int` — every `sku_repo` call inside now passes `dataset_id=dataset_id`.
- `recompute_metrics_for(session, dataset_id: int, sku: str, *, today=None) -> None` — same threading.
- `recompute_all_metrics(session, dataset_id: int, *, today=None) -> int` — iterates `sku_repo.list_all(session, dataset_id)`.

### 5. `app/routers/ingest_router.py` (modified)

`upload_inventory_csv` becomes, in order (Req 3.1-3.4):

1. Read file bytes, run `parse_csv` (unchanged — parsing has no dataset concept).
2. If `rows` is non-empty: `dataset = dataset_repo.create(session, filename=file.filename)` — creates the dataset row *before* any SKU row is written (Req 3.1), with `display_name` initialized to `file.filename` (Req 3.4).
3. `load_rows(session, rows, dataset_id=dataset.id)` — every accepted row is written tagged with `dataset.id` (Req 3.2).
4. `dataset_repo.activate(session, dataset.id)` — once loading succeeds, the new dataset becomes active (Req 3.3).
5. `session.commit()`. On any exception between steps 2-4, `session.rollback()` (unchanged transaction-boundary pattern) — a failed load leaves neither a half-populated active dataset nor an orphaned inactive one with partial data, since nothing commits until every step succeeds.

If `rows` is empty (whole upload rejected for a missing column, or every row individually rejected), no `Dataset` is created at all — this matches Req 3's framing ("when a CSV is submitted... SHALL create... before writing any SKU rows"; an upload that writes zero rows creates no dataset, avoiding empty-dataset clutter from a rejected file).

### 6. `app/routers/datasets_router.py` (new)

```
GET    /api/datasets                 -> list[DatasetSummary]                (Req 5)
POST   /api/datasets/{id}/activate   -> DatasetSummary (the now-active one)  (Req 6)
PATCH   /api/datasets/{id}           -> DatasetSummary (updated)              (Req 7)
DELETE /api/datasets/{id}            -> 204 No Content                       (Req 4)
```

- All four handlers call `dataset_repo` functions exclusively (never issuing raw SQLAlchemy writes themselves — Req 10.4), catch `UnknownDataset` -> HTTP 404, catch `EmptyDisplayName` -> HTTP 400, and commit once on success / rollback on any exception, matching the existing router transaction-boundary convention (`review_router.py`, `ingest_router.py`).
- `PATCH /api/datasets/{id}` request body: `{"display_name": str}` (new `DatasetRenameRequest` schema).
- This is a small new security-relevant surface: like every other STOCKY endpoint, it has **no authentication or authorization** — any client that can reach the API can delete or rename any dataset. This matches the rest of the app's current (unauthenticated) posture, so it is not a new gap introduced by this feature, but it is worth flagging since delete is destructive and irreversible once committed.

### 7. Existing read paths — scoping changes

Each of the following resolves the active dataset id once per request via `dataset_repo.get_active_id(session)` and threads it through:

- **`inventory_router.py`**: `list_inventory` and `get_inventory_item` call `dataset_repo.get_active_id(session)`. If `None`, `list_inventory` returns `InventoryListResponse(items=[])` and `get_inventory_item` raises 404 (Req 2.4, 9.1). Otherwise both call `sku_repo.list_all(session, dataset_id)` / `sku_repo.get(session, dataset_id, sku)`. This same endpoint backs the frontend's `WarehousePage.tsx` (which calls `listInventory()`), so scoping `GET /api/inventory` also satisfies Req 9.3 ("warehouse view's backend router") — there is no separate warehouse backend router in this codebase; the warehouse page is a client-side grouping of the inventory listing.
- **`services/dashboard.py`**: `build_dashboard(session)` and `build_impact(session)` both change signature to accept the resolved `dataset_id` (resolved by their routers) and pass it into `sku_repo.list_all(session, dataset_id)` and a new `recommendation_repo.list_all(session, dataset_id)`. When `dataset_id is None`, both return their respective response shapes with empty/zeroed collections (Req 2.4, 9.2, 9.4).
- **`dashboard_router.py`** / **`impact_router.py`**: resolve `dataset_id = dataset_repo.get_active_id(session)` and pass it to `build_dashboard` / `build_impact`.
- **`investigation_router.py`**: `run_investigation_endpoint` and `get_case_endpoint` resolve the active `dataset_id` first; 404 if `None` (nothing to investigate) or if the SKU is not found within that dataset. `sku_repo.get(session, dataset_id, sku)` replaces the bare-sku lookup; `case_repo.create`/`get_latest_for_sku` gain `dataset_id` (Req 9.5). The `Case` created for a run is tagged with the *active* dataset's id (the same one the SKU snapshot was read from), not chosen independently.
- **`chaos_router.py` / `services/chaos.py`** and **`simulation_router.py` / `services/simulation.py`**: not explicitly named in Requirement 9, but both read through `sku_repo.get` / `sku_repo.list_all`, so they are updated identically (resolve active `dataset_id`, thread it through) to keep the whole app consistent and avoid leaving a stale unscoped read path that would break once `sku_repo.get`'s signature changes. This is a mechanical signature-compatibility change, not new scope: without it the app would not compile/import.

### 8. `app/repositories/case_repo.py` / `recommendation_repo.py` (modified)

- `case_repo.create(session, *, dataset_id: int, sku: str, status="running") -> Case`
- `case_repo.get_latest_for_sku(session, dataset_id: int, sku: str) -> Optional[Case]`
- `recommendation_repo.create_pending(session, *, dataset_id: int, case_id: int, sku: str, ...) -> Recommendation`
- `recommendation_repo.list_all(session, dataset_id: int) -> List[Recommendation]`

`orchestrator.py` passes `dataset_id=sku_snapshot.dataset_id` (the `SkuSnapshot` dataclass in `agents/context.py` gains a `dataset_id: int` field, populated by `investigation_router.py` from the resolved active dataset — never chosen by an agent). This keeps the orchestrator's existing safety-boundary story intact: it still only imports `case_repo` / `recommendation_repo`, never `dataset_repo` or `sku_repo`'s write functions.

### 9. Frontend: `DatasetsPage.tsx` (new)

Mirrors the structure of `WarehousePage.tsx` / `ImpactPage.tsx` (fetch-on-mount, loading/error/empty states):

```tsx
export default function DatasetsPage() {
  // GET /api/datasets on mount -> DatasetSummary[]
  // per row: display_name (editable inline), uploaded_at, sku_count, active badge, activate/delete controls
  // activate: POST /api/datasets/{id}/activate -> refresh badges (Req 8.3)
  // rename: PATCH /api/datasets/{id} -> update displayed name (Req 8.4)
  // delete: DELETE /api/datasets/{id} -> remove row from local list (Req 8.5)
  // any failed activate/delete/rename call: show inline error, leave list state untouched (Req 8.6)
}
```

New `api/client.ts` additions:

```typescript
export interface DatasetSummary {
  id: number;
  filename: string;
  display_name: string;
  uploaded_at: string;
  sku_count: number;
  is_active: boolean;
}

export function listDatasets(): Promise<DatasetSummary[]>;
export function activateDataset(id: number): Promise<DatasetSummary>;
export function renameDataset(id: number, displayName: string): Promise<DatasetSummary>;
export function deleteDataset(id: number): Promise<void>;
```

`Layout.tsx`: new `NavItem` inserted immediately after `{ label: "Reports", emoji: "📊", to: "/impact" }` and before `{ label: "About", ... }`:

```typescript
{ label: "Datasets", emoji: "🗂️", to: "/datasets" },
```

`App.tsx`: new route `<Route path="/datasets" element={<DatasetsPage />} />`.

## Data Models

```python
class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    uploaded_at = Column(String, nullable=False)  # ISO-8601, set at create()
    is_active = Column(Integer, nullable=False, default=0)  # 0/1 flag; SQLite has no native bool


class Sku(Base):
    __tablename__ = "skus"

    dataset_id = Column(Integer, ForeignKey("datasets.id"), primary_key=True)
    sku = Column(String, primary_key=True)
    # ... all existing columns unchanged ...


class SalesHistory(Base):
    __tablename__ = "sales_history"
    __table_args__ = (
        UniqueConstraint("dataset_id", "sku", "day", name="uq_sales_history_dataset_sku_day"),
        ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"]),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    sku = Column(String, nullable=False)
    day = Column(String, nullable=False)
    units = Column(Integer, nullable=False)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"]),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    sku = Column(String, nullable=False)
    # ... all existing columns unchanged ...


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"]),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    sku = Column(String, nullable=False)
    # ... all existing columns unchanged ...
```

`DatasetSummary` (in `schemas.py`, backs `GET /api/datasets`):

```python
class DatasetSummary(BaseModel):
    id: int
    filename: str
    display_name: str
    uploaded_at: str
    sku_count: int
    is_active: bool


class DatasetRenameRequest(BaseModel):
    display_name: str
```

## Error Handling

| Condition | Response |
|---|---|
| `POST /api/datasets/{id}/activate`, unknown id | 404, no state change (Req 6.2) |
| `PATCH /api/datasets/{id}`, unknown id | 404, no state change (Req 7.2) |
| `PATCH /api/datasets/{id}`, empty/whitespace `display_name` | 400, no state change (Req 7.3) |
| `DELETE /api/datasets/{id}`, unknown id | 404, no state change (Req 4.5) |
| No active dataset, `GET /api/inventory` | 200, `{"items": []}` (Req 2.4) |
| No active dataset, `GET /api/inventory/{sku}` | 404 |
| No active dataset, `GET /api/dashboard` / `GET /api/impact` | 200, zeroed/empty summaries (Req 2.4) |
| No active dataset, `POST /api/investigations/{sku}/run` / `GET /api/cases/{sku}` | 404 |
| Upload where every row is rejected (`rows` empty) | `UploadResult` as today; no `Dataset` row created |

All new error paths reuse the existing convention: repository functions raise a typed exception (`UnknownDataset`, `EmptyDisplayName`), and the router layer is the only place that translates those into HTTP status codes — repositories themselves never raise `HTTPException` (consistent with `services/review.py`'s `UnknownRecommendation`/`IllegalTransition` pattern).

## Safety Boundary

`dataset_repo.py`'s write functions (`activate`, `rename`, `delete`) join `sku_repo.py`'s write section and `apply_action.py` as the only sanctioned mutators. The rule extends cleanly:

- `datasets_router.py` is the only router that imports `dataset_repo`'s write functions.
- `ingest_router.py` imports `dataset_repo.create` and `dataset_repo.activate` (steps 2 and 4 of upload) — this is expected, mirroring how it already imports the sanctioned `loader.py` write path.
- `app/agents/*.py` and `app/agents/orchestrator.py` must **not** import `dataset_repo` at all. The existing import-graph safety-boundary test is extended with one more assertion: no module under `app/agents/` imports anything from `app.repositories.dataset_repo`.
- Agents receive a `dataset_id` only as an inert integer field on the plain-data `SkuSnapshot`/`AgentContext` they already receive — never a session, never a repo handle.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Every row belongs to exactly one existing dataset

For any sequence of dataset creations and CSV uploads, every persisted `skus`, `cases`, and `recommendations` row has a non-null `dataset_id` that matches an existing `Dataset` row's id.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 2: Dataset isolation under scoped reads

For any two or more datasets — including ones whose `skus` rows share the same `sku` code — activating one of them and then querying any scoped read path (`GET /api/inventory`, `GET /api/inventory/{sku}`, `GET /api/dashboard`, `GET /api/impact`, `GET /api/cases/{sku}`) returns only rows whose `dataset_id` equals the active dataset's id; rows belonging to every other dataset are absent from the result.

**Validates: Requirements 1.4, 9.1, 9.2, 9.3, 9.4, 9.5**

### Property 3: Dataset metadata round-trip

For any filename and upload timestamp used to create a `Dataset`, reading that dataset back (directly or via `GET /api/datasets`) returns the same filename, the same upload timestamp, and a `display_name` initialized to the filename.

**Validates: Requirements 1.5, 3.4**

### Property 4: Exactly one active dataset after any activation sequence

For any non-empty set of datasets and any sequence of `activate` calls against existing dataset ids, after each call exactly one dataset has its active flag set, and it is the most recently activated one; every other dataset's active flag is clear.

**Validates: Requirements 2.1, 2.2, 6.1**

### Property 5: Activation does not touch dependent rows

For any dataset with existing `skus`, `cases`, and `recommendations` rows, activating a different dataset and then re-activating the original leaves every one of the original dataset's dependent rows (ids and field values) byte-for-byte unchanged, and only the `is_active` column of `Dataset` rows differs before and after.

**Validates: Requirements 2.3**

### Property 6: Upload creates, tags, and activates a new dataset

For any CSV whose parse produces at least one accepted row, after `POST /api/inventory/upload` completes: exactly one new `Dataset` row exists whose filename matches the uploaded file, every newly persisted `skus` row's `dataset_id` equals that new dataset's id, and that new dataset is the active dataset.

**Validates: Requirements 3.1, 3.2, 3.3**

### Property 7: Deleting a dataset removes it and all its dependent rows, and nothing else's

For any dataset with an arbitrary number of `skus`, `sales_history`, `cases`, and `recommendations` rows, and any other dataset with its own rows, deleting the first dataset results in: zero remaining `skus`/`sales_history`/`cases`/`recommendations` rows with the deleted `dataset_id`, no remaining `Dataset` row with that id, and every row belonging to the other dataset unchanged.

**Validates: Requirements 4.1, 4.2, 4.3, 4.4**

### Property 8: Not-found dataset operations make no changes

For any `dataset_id` that does not correspond to an existing `Dataset` row, calling activate, rename, or delete on it raises a not-found error and leaves every table's row count and every existing row's field values, and the currently active dataset (if any), unchanged.

**Validates: Requirements 4.5, 6.2, 7.2**

### Property 9: Deleting the active dataset leaves no dataset active

For any dataset that is currently the active dataset, deleting it results in no dataset being active, regardless of how many other (inactive) datasets remain.

**Validates: Requirements 4.6**

### Property 10: Dataset listing reports accurate SKU counts

For any set of datasets each populated with an arbitrary (possibly zero) number of `skus` rows, `GET /api/datasets` returns one entry per dataset whose `sku_count` equals the actual number of `skus` rows carrying that dataset's id, and whose id/filename/display_name/uploaded_at/is_active fields match the underlying `Dataset` row.

**Validates: Requirements 5.1, 5.2**

### Property 11: Rename updates display name and nothing else

For any existing dataset and any non-empty (post-trim) display name string, renaming it results in the stored `display_name` equaling the submitted value while the dataset's `id`, `filename`, `uploaded_at`, `is_active` flag, and all of its dependent rows remain unchanged.

**Validates: Requirements 7.1**

### Property 12: Empty display name is rejected

For any existing dataset and any display name string that is empty or consists only of whitespace, submitting it as a rename is rejected and the dataset's stored `display_name` is left unchanged.

**Validates: Requirements 7.3**

### Property 13: No active dataset yields empty scoped results

When no `Dataset` row exists (or none is active), every scoped read path returns an empty result: `GET /api/inventory` returns no items, `GET /api/dashboard` and `GET /api/impact` return zeroed/empty summaries, and `GET /api/inventory/{sku}` / `GET /api/cases/{sku}` / `POST /api/investigations/{sku}/run` return a not-found response.

**Validates: Requirements 2.4**

## Testing Strategy

**Unit / example tests** (concrete scenarios, not driven by generators):
- `datasets_router.py` HTTP wiring: each endpoint calls the matching `dataset_repo` function and maps its exceptions to the right status code (404 / 400).
- `ingest_router.py`: uploading a CSV with zero accepted rows creates no `Dataset` row.
- `DatasetsPage.tsx`: nav item position/label/emoji (Req 8.1); activate/rename/delete button click -> mocked API call -> UI update (Req 8.2-8.5); a mocked failed activate/delete call leaves the rendered list unchanged and shows an error (Req 8.6).
- Safety-boundary import-graph test extended: no module under `app/agents/` imports `app.repositories.dataset_repo`.

**Property-based tests** (Hypothesis, matching the project's existing `.hypothesis` setup, minimum 100 iterations each): implement Properties 1-13 above, generating random combinations of dataset counts, SKU codes (including intentionally colliding codes across datasets), row counts, filenames, and display-name strings (including whitespace-only strings for Property 12).

Each property test must be tagged:
**Feature: manage-uploaded-datasets, Property {number}: {property title}**
