# Implementation Plan: manage-uploaded-datasets

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement each step with incremental progress. Make sure that each prompt builds on the previous prompts, and ends with wiring things together. There should be no hanging or orphaned code that isn't integrated into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

Tasks are deliberately scoped to one file (or one tightly-coupled pair of files) each to keep individual units of work small: schema/model changes land first, then the new `dataset_repo`, then `sku_repo`/loader/ingest wiring, then each existing read-path router individually, then `case_repo`/`recommendation_repo`/orchestrator wiring, then frontend, then the safety-boundary test update, then a final DB-recreate + manual smoke-check task. `sample_inventory.csv` already has the 12-column schema from the earlier CSV migration and needs no changes here.

## Tasks

- [x] 1. Add `Dataset` model and dataset-scope every existing table in `app/models.py`
  - Add the new `Dataset` model (surrogate `id` PK, `filename`, `display_name`, `uploaded_at`, `is_active`)
  - Change `Sku` to a composite `(dataset_id, sku)` primary key with `dataset_id` as a `ForeignKey("datasets.id")`
  - Add a `dataset_id` column to `SalesHistory`, `Case`, and `Recommendation`; replace their plain `sku` FK with a composite `ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"])`
  - Replace `UniqueConstraint("sku", "day")` on `sales_history` with `UniqueConstraint("dataset_id", "sku", "day")`
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Add `DatasetSummary` and `DatasetRenameRequest` schemas in `app/schemas.py`
  - Add `DatasetSummary` (id, filename, display_name, uploaded_at, sku_count, is_active) and `DatasetRenameRequest` (display_name) Pydantic models
  - _Requirements: 5.1, 7.1_

- [x] 3. Create `app/repositories/dataset_repo.py` with read functions
  - Implement `get(session, dataset_id)`, `get_active(session)`, `get_active_id(session)`, `list_with_counts(session)` following the read/write banner convention used in `sku_repo.py`
  - `list_with_counts` computes `sku_count` per dataset via `SELECT COUNT(*) FROM skus WHERE dataset_id = :id`
  - _Requirements: 2.4, 5.1, 5.2_

  - [ ]* 3.1 Write property test for dataset listing accuracy
    - **Property 10: Dataset listing reports accurate SKU counts**
    - **Validates: Requirements 5.1, 5.2**

- [x] 4. Add write functions and exceptions to `app/repositories/dataset_repo.py`
  - Define `UnknownDataset` and `EmptyDisplayName` exceptions
  - Implement `create(session, *, filename)` — inserts a new `Dataset` row with `display_name=filename`, `uploaded_at=now_iso()`, `is_active=0`, flushes, returns the row
  - Implement `activate(session, dataset_id)` — raises `UnknownDataset` if unresolved; otherwise clears `is_active` on every `Dataset` row then sets it on the target, touching only `datasets.is_active`
  - Implement `rename(session, dataset_id, *, display_name)` — raises `UnknownDataset` if unresolved, `EmptyDisplayName` if `display_name.strip()` is empty, otherwise updates and flushes
  - Implement `delete(session, dataset_id)` — raises `UnknownDataset` if unresolved (checked before any delete statement); otherwise deletes `recommendations`, then `cases`, then `sales_history`, then `skus` rows matching `dataset_id`, then the `Dataset` row itself; does not touch `is_active` on any other row
  - _Requirements: 2.1, 2.2, 2.3, 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 6.1, 6.2, 7.1, 7.2, 7.3, 10.1, 10.2_

  - [ ]* 4.1 Write property test for exactly-one-active-dataset invariant
    - **Property 4: Exactly one active dataset after any activation sequence**
    - **Validates: Requirements 2.1, 2.2, 6.1**

  - [ ]* 4.2 Write property test for activation not touching dependent rows
    - **Property 5: Activation does not touch dependent rows**
    - **Validates: Requirements 2.3**

  - [ ]* 4.3 Write property test for cascading delete
    - **Property 7: Deleting a dataset removes it and all its dependent rows, and nothing else's**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

  - [ ]* 4.4 Write property test for not-found operations making no changes
    - **Property 8: Not-found dataset operations make no changes**
    - **Validates: Requirements 4.5, 6.2, 7.2**

  - [ ]* 4.5 Write property test for delete of active dataset
    - **Property 9: Deleting the active dataset leaves no dataset active**
    - **Validates: Requirements 4.6**

  - [ ]* 4.6 Write property test for rename semantics
    - **Property 11: Rename updates display name and nothing else**
    - **Validates: Requirements 7.1**

  - [ ]* 4.7 Write property test for empty display name rejection
    - **Property 12: Empty display name is rejected**
    - **Validates: Requirements 7.3**

  - [ ]* 4.8 Write property test for dataset metadata round-trip
    - **Property 3: Dataset metadata round-trip**
    - **Validates: Requirements 1.5, 3.4**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Add `dataset_id` parameter to every function in `app/repositories/sku_repo.py`
  - Update read functions (`get`, `list_all`, `exists`, `get_sales_history`, `get_sales_units`) to take `dataset_id: int` and perform composite `(dataset_id, sku)` lookups
  - Update write functions (`upsert_sku`, `replace_sales_history`, `set_metrics`, `apply_reorder`, `apply_adjust_reorder`) to take `dataset_id: int` (keyword) and scope every lookup/insert to it
  - `list_all` orders by `(Sku.dataset_id, Sku.sku)`
  - _Requirements: 1.1, 9.1_

  - [ ]* 6.1 Write property test for row-dataset association
    - **Property 1: Every row belongs to exactly one existing dataset**
    - **Validates: Requirements 1.1, 1.2, 1.3**

- [ ] 7. Thread `dataset_id` through `app/ingest/loader.py`
  - Update `load_rows(session, rows, *, dataset_id, base_date=None)` so every `sku_repo` call passes `dataset_id=dataset_id`
  - Update `recompute_metrics_for(session, dataset_id, sku, *, today=None)` and `recompute_all_metrics(session, dataset_id, *, today=None)` to thread `dataset_id` the same way
  - _Requirements: 1.1, 3.2_

- [ ] 8. Wire dataset creation/activation into `app/routers/ingest_router.py`
  - In `upload_inventory_csv`, after `parse_csv` succeeds and `rows` is non-empty: call `dataset_repo.create(session, filename=file.filename)` before `load_rows`, call `load_rows(session, rows, dataset_id=dataset.id)`, then call `dataset_repo.activate(session, dataset.id)`, then commit; roll back on any exception between these steps
  - If `rows` is empty, create no `Dataset` row (unchanged `UploadResult` behavior)
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 8.1 Write unit test for zero-accepted-rows upload creating no dataset
    - Cover the case where every row is rejected or the file is missing a required column
    - _Requirements: 3.1_

  - [ ]* 8.2 Write property test for upload creating, tagging, and activating a dataset
    - **Property 6: Upload creates, tags, and activates a new dataset**
    - **Validates: Requirements 3.1, 3.2, 3.3**

- [ ] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Create `app/routers/datasets_router.py`
  - Implement `GET /api/datasets` (calls `dataset_repo.list_with_counts`), `POST /api/datasets/{id}/activate`, `PATCH /api/datasets/{id}` (body: `DatasetRenameRequest`), `DELETE /api/datasets/{id}` (204 No Content)
  - Each handler calls `dataset_repo` functions exclusively, catches `UnknownDataset` -> HTTP 404, catches `EmptyDisplayName` -> HTTP 400, commits once on success / rolls back on exception
  - Register the new router in `app/main.py`
  - _Requirements: 4.5, 5.1, 6.1, 6.2, 7.1, 7.2, 7.3, 10.4_

  - [ ]* 10.1 Write unit tests for `datasets_router.py` HTTP wiring
    - Cover each endpoint's success path and its 404/400 error mapping
    - _Requirements: 4.5, 6.2, 7.2, 7.3_

- [ ] 11. Scope `app/routers/inventory_router.py` to the active dataset
  - `list_inventory` and `get_inventory_item` resolve `dataset_id = dataset_repo.get_active_id(session)` first
  - If `dataset_id is None`, `list_inventory` returns `InventoryListResponse(items=[])` and `get_inventory_item` raises 404
  - Otherwise call `sku_repo.list_all(session, dataset_id)` / `sku_repo.get(session, dataset_id, sku)`
  - _Requirements: 2.4, 9.1_

- [ ] 12. Scope `app/services/dashboard.py` and its routers to the active dataset
  - Change `build_dashboard(session, dataset_id)` and `build_impact(session, dataset_id)` to accept a resolved `dataset_id` and pass it to `sku_repo.list_all` and `recommendation_repo.list_all`
  - When `dataset_id is None`, return empty/zeroed response shapes
  - Update `app/routers/dashboard_router.py` and `app/routers/impact_router.py` to resolve `dataset_id = dataset_repo.get_active_id(session)` and pass it through
  - _Requirements: 2.4, 9.2, 9.4_

- [ ] 13. Add `dataset_id` to `app/repositories/case_repo.py` and `app/repositories/recommendation_repo.py`
  - `case_repo.create(session, *, dataset_id, sku, status="running")` and `case_repo.get_latest_for_sku(session, dataset_id, sku)`
  - `recommendation_repo.create_pending(session, *, dataset_id, case_id, sku, ...)` and `recommendation_repo.list_all(session, dataset_id)`
  - _Requirements: 1.3, 9.5_

- [ ] 14. Add `dataset_id` to `SkuSnapshot` and thread it through `app/agents/context.py` and `app/agents/orchestrator.py`
  - `SkuSnapshot` (in `app/agents/context.py`) gains a plain `dataset_id: int` field
  - `orchestrator.py` passes `dataset_id=sku_snapshot.dataset_id` into its `case_repo`/`recommendation_repo` calls; it must not import `dataset_repo` or `sku_repo`'s write functions
  - _Requirements: 9.5, 10.3_

- [ ] 15. Scope `app/routers/investigation_router.py` to the active dataset
  - `run_investigation_endpoint` and `get_case_endpoint` resolve `dataset_id = dataset_repo.get_active_id(session)` first, returning 404 if `None`
  - `_snapshot_from_row` populates `SkuSnapshot.dataset_id` from the resolved active dataset
  - Replace bare-sku lookups with `sku_repo.get(session, dataset_id, sku)`; thread `dataset_id` into `case_repo.create` / `case_repo.get_latest_for_sku`
  - _Requirements: 2.4, 9.5_

- [ ] 16. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 17. Scope `app/routers/chaos_router.py` / `app/services/chaos.py` to the active dataset
  - Resolve `dataset_id = dataset_repo.get_active_id(session)` in the router and thread it into `apply_chaos`, replacing its `sku_repo.get` / `sku_repo.list_all` calls with the dataset-scoped signatures
  - _Requirements: 9.1_

- [ ] 18. Scope `app/routers/simulation_router.py` / `app/services/simulation.py` to the active dataset
  - Resolve `dataset_id = dataset_repo.get_active_id(session)` in the router and thread it into `simulate`, replacing its `sku_repo.get` / `sku_repo.get_sales_units` calls with the dataset-scoped signatures
  - _Requirements: 9.1_

  - [ ]* 18.1 Write property test for scoped-read dataset isolation
    - **Property 2: Dataset isolation under scoped reads**
    - **Validates: Requirements 1.4, 9.1, 9.2, 9.3, 9.4, 9.5**

  - [ ]* 18.2 Write property test for no-active-dataset empty results
    - **Property 13: No active dataset yields empty scoped results**
    - **Validates: Requirements 2.4**

- [ ] 19. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 20. Add dataset API types and functions to `frontend/src/api/client.ts`
  - Add `DatasetSummary` interface
  - Add `listDatasets()`, `activateDataset(id)`, `renameDataset(id, displayName)`, `deleteDataset(id)` functions following the existing `request<T>` helper pattern
  - _Requirements: 5.1, 6.1, 7.1, 4.1_

- [ ] 21. Create `frontend/src/pages/DatasetsPage.tsx`
  - Fetch `listDatasets()` on mount with loading/error/empty states, mirroring `WarehousePage.tsx` / `ImpactPage.tsx`
  - Render, per dataset row: display_name (inline-editable), uploaded_at, sku_count, active/inactive badge, activate control, delete control
  - Activate calls `activateDataset(id)` and updates the displayed active/inactive badges
  - Inline rename calls `renameDataset(id, displayName)` and updates the displayed name
  - Delete calls `deleteDataset(id)` and removes the row from the displayed list
  - Any failed activate/rename/delete call shows an inline error and leaves the list state unchanged
  - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 21.1 Write unit tests for `DatasetsPage.tsx`
    - Cover activate/rename/delete success paths updating displayed state, and a mocked failed call leaving the list unchanged with an error shown
    - _Requirements: 8.3, 8.4, 8.5, 8.6_

- [ ] 22. Wire the Datasets nav item and route
  - In `frontend/src/components/Layout.tsx`, insert `{ label: "Datasets", emoji: "🗂️", to: "/datasets" }` into `NAV_ITEMS` immediately after the "Reports" entry
  - In `frontend/src/App.tsx`, add `<Route path="/datasets" element={<DatasetsPage />} />`
  - _Requirements: 8.1_

- [ ] 23. Extend the safety-boundary test in `backend/tests/test_safety_boundary.py`
  - Add a static AST assertion that no source file under `app/agents/` imports `app.repositories.dataset_repo` (mirroring the existing `sku_repo` write-function check)
  - _Requirements: 10.3_

- [ ] 24. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 25. Recreate `stocky.db` against the new dataset-scoped schema and smoke-check the upload flow
  - Delete the existing `backend/stocky.db` file and call `init_db()` to recreate all tables from the updated `app/models.py`
  - Write and run a small script or test that uploads `sample_inventory.csv` through the ingest path end-to-end (dataset creation, load, activation) and confirms `GET /api/datasets` and `GET /api/inventory` return the expected dataset-scoped rows
  - _Requirements: 3.1, 3.2, 3.3, 5.1_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties defined in design.md; each is tagged `Feature: manage-uploaded-datasets, Property {number}: {property title}`
- Unit tests validate specific examples and edge cases
- `sample_inventory.csv` already has the 12-column schema from the earlier CSV migration and needs no regeneration
- Task 25's DB recreation is a known outstanding item for this schema change since there is no migration framework in this project

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2"] },
    { "id": 1, "tasks": ["3", "4"] },
    { "id": 2, "tasks": ["3.1", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "6"] },
    { "id": 3, "tasks": ["6.1", "7", "13"] },
    { "id": 4, "tasks": ["8", "14", "20"] },
    { "id": 5, "tasks": ["8.1", "8.2", "10", "11", "12", "15", "17", "18", "21"] },
    { "id": 6, "tasks": ["10.1", "18.1", "18.2", "21.1", "22", "23"] },
    { "id": 7, "tasks": ["25"] }
  ]
}
```
