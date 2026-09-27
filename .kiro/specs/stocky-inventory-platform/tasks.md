# Implementation Plan: STOCKY Inventory Platform

## Overview

This plan builds STOCKY incrementally, tier by tier. **MUST HAVE** delivers the full core loop — CSV ingest, deterministic detection, four-agent orchestration behind a hard safety boundary, human approve/reject, deterministic apply-on-approval, lifecycle tracking, and the three core pages (Dashboard, Inventory Table, Case) — scoped to finish in ~3 days. **SHOULD HAVE** adds what-if simulation and the Impact page. **NICE TO HAVE** adds Chaos Mode and the warehouse visualisation.

Sequencing within MUST HAVE flows: backend foundations → detection engine → agents + safety boundary → review/apply/lifecycle → frontend pages. The safety-boundary test (import-graph + no-mutation) is wired in early, as soon as the orchestrator exists, so the guarantee is enforced before agent work grows.

Tasks marked with `*` are optional (tests) and can be skipped for a faster demo path, though the property tests are the strongest evidence of the safety story and are recommended.

---

## MUST HAVE — Core Loop (target: 3 days)

- [x] 1. Project scaffolding and backend foundations
  - [x] 1.1 Scaffold backend package structure and app entry
    - Create `backend/app/` tree per design folder structure: `main.py` (FastAPI app + router registration + static mount), `config.py` (env config + thresholds: fast/slow/overstock/anomaly), `requirements.txt` (fastapi, uvicorn, sqlalchemy, httpx, pydantic, pytest, hypothesis)
    - Define threshold defaults in `config.py`: `FAST_MOVING_THRESHOLD=20.0`, `SLOW_MOVING_THRESHOLD=1.0`, `OVERSTOCK_DOC_THRESHOLD=90.0`, `ANOMALY_REL_THRESHOLD=0.5`; read `LLM_API_KEY`, `LLM_API_BASE`, `LLM_MODEL` from env
    - _Requirements: 2.4_

  - [x] 1.2 Define database schema, engine, and models
    - Implement `db.py` (SQLite engine/session, `init_db()`) and `models.py` with the four tables: `skus`, `sales_history`, `cases`, `recommendations` per the design SQL
    - Ensure `days_of_cover`/`stockout_eta` are nullable and `no_recent_sales`/`classifications` columns exist
    - _Requirements: 1.2, 12.1_

  - [x] 1.3 Define Pydantic schemas
    - Implement `schemas.py`: `SkuRow` (parsed CSV row), inventory list/detail responses, case response, recommendation response, upload result `{accepted, rejected:[{row,reason}], missing_column?}`, dashboard/impact/simulation responses
    - _Requirements: 1.5, 14.1_

  - [x] 1.4 Implement repositories with the single-writer split
    - Implement `repositories/sku_repo.py` with clearly separated **read** functions and the **only** inventory-write functions; `case_repo.py` (create/save_step/mark_failed/read); `recommendation_repo.py` (create pending, read, status update)
    - Keep inventory-write functions isolated so no agent/orchestrator module needs to import them
    - _Requirements: 1.2, 9.3, 11.1_

  - [x] 2. CSV upload and ingestion
  - [x] 2.1 Implement CSV parser with validation
    - Implement `ingest/csv_parser.py`: parse SKU, name, category, current_stock, reorder_point, lead_time_days, unit_cost, recent sales history; reject whole upload naming a missing required column; reject individual rows with non-numeric numeric fields returning the row number; count accepted/rejected
    - _Requirements: 1.1, 1.3, 1.4, 1.5_

  - [x] 2.2 Implement deterministic loader
    - Implement `ingest/loader.py`: persist parsed rows via `sku_repo` write path and populate `sales_history`; deterministic, runs once at upload
    - _Requirements: 1.2_

  - [x] 2.3 Wire ingest router
    - Implement `routers/ingest_router.py` `POST /api/inventory/upload` (multipart) returning accepted/rejected counts and reasons; register in `main.py`
    - _Requirements: 1.1, 1.5_

  - [x]* 2.4 Write property test for CSV ingest round trip and count integrity
    - **Property 1: CSV ingest round trip and count integrity**
    - Assert `accepted_count + rejected_count == total_rows`, accepted records read back equal to parsed input, rejected rows carry correct row number / missing column name
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

- [x] 3. Deterministic detection engine
  - [x] 3.1 Implement metrics computation
    - Implement `detection/metrics.py` (pure, no I/O, no LLM): `Sales_Velocity = sum(history)/W`; when velocity > 0 compute `Days_Of_Cover` and `Stockout_ETA`; when velocity == 0 set Days_Of_Cover/Stockout_ETA undefined and mark no recent sales
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x]* 3.2 Write property test for metric correctness including zero-velocity edge
    - **Property 2: Metric correctness including the zero-velocity edge**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.5**

  - [x] 3.3 Implement condition classification rules
    - Implement `detection/classify.py` (pure, deterministic): attach every applicable label — `stockout_risk`, `needs_reorder`, `fast_moving`, `slow_moving`, `overstock`, `trend_anomaly` (window-halves relative change) — using config thresholds
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x]* 3.4 Write property test for deterministic, complete classification
    - **Property 3: Deterministic, complete condition classification**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

  - [x] 3.5 Recompute and persist metrics + classifications on ingest
    - Wire loader (or a post-ingest step) to compute metrics + classifications and cache them on the `skus` row (`sales_velocity`, `days_of_cover`, `stockout_eta`, `no_recent_sales`, `classifications`)
    - _Requirements: 2.1, 3.7_

- [x] 4. Agent orchestration and the safety boundary
  - [x] 4.1 Define the safety-boundary data contracts
    - Implement `agents/context.py`: frozen `AgentContext` (SKU snapshot, classifications, prior outputs — plain data only, no DB handle), `AgentResult` (text + parsed JSON), `ActionKind` closed enum, frozen `ProposedAction`
    - _Requirements: 9.1, 9.2_

  - [x] 4.2 Implement the LLM client wrapper
    - Implement `agents/llm_client.py`: `httpx` wrapper with one `call()` per agent; raise a typed `LLMError` on API failure; model/key/base from config
    - _Requirements: 4.4, 5.4, 6.4, 7.5_

  - [x] 4.3 Implement the four agent roles
    - Implement `agents/detective.py`, `forecast.py`, `strategy.py`, `manager.py`: each has a role system prompt, consumes `AgentContext`, makes exactly one LLM call, returns `AgentResult`; Manager emits strict JSON parsed/validated into `ProposedAction` with `NO_ACTION` fallback for unparseable output (raw text preserved as rationale)
    - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 7.1, 7.2, 7.4, 9.4_

  - [x] 4.4 Implement the sequential orchestrator (read-only)
    - Implement `agents/orchestrator.py`: fixed order Detective→Forecast→Strategy→Manager, chain each output forward, fail-stop on `LLMError` recording the failed step and returning case status `error`; on success persist a `recommendations` row with status `pending` via `recommendation_repo`; orchestrator must NOT import `sku_repo` write functions or `apply_action`
    - _Requirements: 4.4, 5.4, 6.4, 7.3, 7.5, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2_

  - [x]* 4.5 Write safety-boundary test (import-graph + no-mutation)
    - Import-graph assertion: the `agents/orchestrator` module graph never imports `services/apply_action` or the `sku_repo` write functions
    - **Property 4: Agents never mutate inventory** — run each agent and the full orchestration against a store snapshot; assert `skus`/`sales_history` rows are byte-for-byte unchanged; the only new row is a `pending` recommendation
    - **Validates: Requirements 4.3, 5.3, 6.3, 7.4, 9.1, 9.2**

  - [x]* 4.6 Write property test for inert mutation-encoding output
    - **Property 5: Agent output that encodes a mutation is inert** — feed outputs crafted to look like SQL/shell/write instructions; assert stored as text only, inventory unchanged
    - **Validates: Requirements 9.4**

  - [x]* 4.7 Write property test for fixed sequential order with fail-stop
    - **Property 6: Fixed sequential order with fail-stop** — mocked LLM; assert success order equals `[Detective, Forecast, Strategy, Manager]`, and a failure at position i runs no later agent and reports failed step i
    - **Validates: Requirements 8.1, 8.2, 8.3, 4.4, 5.4, 6.4, 7.5**

  - [x]* 4.8 Write property test for Manager recommendation persisted as pending
    - **Property 7: Manager produces one well-formed recommendation persisted as pending**
    - **Validates: Requirements 7.2, 7.3**

  - [x] 4.9 Wire investigation router
    - Implement `routers/investigation_router.py` `POST /api/investigations/{sku}/run` (returns case id + status) and `GET /api/cases/{sku}` (four outputs + recommendation + status); register in `main.py`
    - _Requirements: 4.1, 8.1, 15.1, 15.2_

- [x] 5. Checkpoint — detection + agents + safety
  - Ensure all tests pass (metrics, classification, safety-boundary import-graph + no-mutation, order/fail-stop, Manager pending). Ask the user if questions arise.

- [x] 6. Review, apply, and lifecycle
  - [x] 6.1 Implement lifecycle transition validation
    - Implement `services/review.py`: centralized legal-transition table (pending→approved, pending→rejected, approved→applied); approve/reject a non-pending recommendation is refused with the current status retained; illegal transitions raise and retain status
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 12.1, 12.2, 12.3_

  - [x] 6.2 Implement the deterministic apply-action service (sole inventory mutator)
    - Implement `services/apply_action.py`: the ONLY module importing inventory-write functions; `apply(recommendation_id)` refuses unless status is `approved`, applies the exact `ProposedAction` delta (reorder / adjust_reorder / markdown / no_action) affecting only the target SKU, sets status `applied` on success, leaves `approved` + returns error on failure, and is idempotent (re-apply of `applied` is a no-op)
    - _Requirements: 11.1, 11.2, 11.3, 11.4_

  - [x] 6.3 Wire review router
    - Implement `routers/review_router.py`: `POST /api/recommendations/{id}/approve` (pending→approved then invoke apply, return `{status, applied, error?}`), `/reject` (pending→rejected), `/apply` (deterministic apply of an approved recommendation); register in `main.py`
    - _Requirements: 10.2, 10.3, 11.1, 11.2, 11.3_

  - [x]* 6.4 Write property test for lifecycle validity
    - **Property 8: Lifecycle validity**
    - **Validates: Requirements 10.2, 10.3, 10.4, 12.1, 12.2, 12.3, 11.2**

  - [x]* 6.5 Write property test for the exact apply delta
    - **Property 9: Deterministic apply produces the exact action delta**
    - **Validates: Requirements 11.1**

  - [x]* 6.6 Write property test for apply idempotence
    - **Property 10: Apply is idempotent (applied at most once)**
    - **Validates: Requirements 11.4**

  - [x]* 6.7 Write property test for post-approval-only mutation
    - **Property 11: Only the deterministic apply path (post-approval) changes inventory** — orchestration/simulation without approve+apply leaves inventory unchanged
    - **Validates: Requirements 9.3, 16.2**

- [x] 7. Dashboard aggregation service and router
  - [x] 7.1 Implement dashboard aggregation
    - Implement `services/dashboard.py`: per-condition counts, per-lifecycle-status counts, prioritised stockout-risk group
    - _Requirements: 13.1, 13.2, 13.3_

  - [x] 7.2 Wire dashboard and inventory routers
    - Implement `routers/dashboard_router.py` `GET /api/dashboard`; `routers/inventory_router.py` `GET /api/inventory` and `GET /api/inventory/{sku}`; register in `main.py`
    - _Requirements: 13.1, 13.2, 13.3, 14.1, 14.2, 14.3_

  - [x]* 7.3 Write property test for aggregate condition counts
    - **Property 12: Aggregate condition counts are consistent**
    - **Validates: Requirements 13.1, 17.2**

  - [x]* 7.4 Write property test for aggregate lifecycle counts
    - **Property 13: Aggregate lifecycle counts are consistent**
    - **Validates: Requirements 13.2, 17.1**

- [x] 8. Frontend scaffolding and API client
  - [x] 8.1 Scaffold frontend app
    - Create `frontend/` Vite + React + Tailwind project: `main.tsx`, `App.tsx` (React Router with routes for Dashboard `/`, Inventory `/inventory`, Case `/cases/:sku`), `index.css` (Tailwind)
    - _Requirements: 13.1, 14.1, 15.1_

  - [x] 8.2 Implement API client wrappers
    - Implement `api/client.ts`: `fetch` wrappers for upload, inventory list/detail, run investigation, get case, approve/reject, dashboard
    - _Requirements: 1.1, 14.1, 15.1_

- [x] 9. Frontend MUST HAVE pages
  - [x] 9.1 Implement Dashboard page and upload widget
    - Implement `pages/DashboardPage.tsx` + `components/UploadCsv.tsx` (multipart POST, shows accepted/rejected counts) + `components/ConditionBadges.tsx`; render per-condition counts, per-lifecycle-status counts, prioritised stockout group
    - _Requirements: 1.5, 13.1, 13.2, 13.3_

  - [x] 9.2 Implement Inventory Table page
    - Implement `pages/InventoryPage.tsx`: table of every SKU with current_stock, reorder_point, Sales_Velocity, Days_Of_Cover and condition badges; row click navigates to the Case page
    - _Requirements: 14.1, 14.2, 14.3_

  - [x] 9.3 Implement Investigation / Case page
    - Implement `pages/CasePage.tsx` + `components/AgentCase.tsx` (four steps or error state) + `components/RecommendationCard.tsx` (approve/reject controls only while pending); "Start investigation" control when no case exists; show recommendation + current status
    - _Requirements: 15.1, 15.2, 15.3, 15.4_

  - [x]* 9.4 Write frontend component tests
    - Test `RecommendationCard` (controls only when pending), `UploadCsv` (accepted/rejected display), and routing from Inventory rows to the Case page
    - _Requirements: 14.3, 15.3_

- [x] 10. Checkpoint — MUST HAVE end-to-end
  - Ensure all backend and frontend tests pass; the full core loop (upload → detect → investigate → approve → deterministic apply) is wired with no orphaned code. Ask the user if questions arise.

---

## SHOULD HAVE — Simulation and Impact

- [x] 11. What-If simulation
  - [x] 11.1 Implement the read-only simulation engine
    - Implement `services/simulation.py`: reuse `detection/metrics.py` and `detection/classify.py`; build an in-memory SKU snapshot from overrides, compute projected metrics + classifications; never open a write session, never call `sku_repo` writers or `apply_action`; return current vs projected side by side
    - _Requirements: 16.1, 16.2, 16.3_

  - [x] 11.2 Wire simulation router
    - Implement `routers/simulation_router.py` `POST /api/simulation/{sku}`; register in `main.py`
    - _Requirements: 16.1, 16.3_

  - [x]* 11.3 Write property test for deterministic, read-only simulation
    - **Property 14: Simulation is deterministic and read-only**
    - **Validates: Requirements 16.1, 16.2, 16.3**

  - [x] 11.4 Implement Simulation page
    - Implement `pages/SimulationPage.tsx` + `components/MetricCompare.tsx`: form for hypothetical inputs, current vs projected comparison; add `/simulation/:sku` route and client wrapper
    - _Requirements: 16.1, 16.3_

- [x] 12. Impact / metrics page
  - [x] 12.1 Implement impact aggregation and router
    - Add impact aggregation (recommendation outcome counts by status + aggregate condition metrics across all SKUs) reusing dashboard aggregation helpers; expose `GET /api/impact`; register in `main.py`
    - _Requirements: 17.1, 17.2_

  - [x] 12.2 Implement Impact page
    - Implement `pages/ImpactPage.tsx`: recommendation outcome counts and aggregate condition metrics; add `/impact` route and client wrapper
    - _Requirements: 17.1, 17.2_

---

## NICE TO HAVE — Chaos Mode and Warehouse Visualisation

- [x] 13. Chaos Mode
  - [x] 13.1 Implement chaos service and router
    - Implement `services/chaos.py`: inject synthetic disruptive events into a working copy only (never alter originally uploaded source data), then re-run the Detection_Engine classification on affected SKUs; expose `POST /api/chaos`; register in `main.py`
    - _Requirements: 18.1, 18.2, 18.3_

  - [x]* 13.2 Write property test for chaos working-copy isolation
    - **Property 15: Chaos affects only the working copy**
    - **Validates: Requirements 18.1, 18.2, 18.3**

  - [x] 13.3 Implement Chaos Mode page
    - Implement `pages/WarehousePage.tsx` companion route `/chaos`: toggle + inject events, then show reclassified SKUs; add client wrapper
    - _Requirements: 18.1, 18.2_

- [x] 14. Interactive warehouse visualisation
  - [x] 14.1 Implement Warehouse Visualisation page
    - Implement `pages/WarehousePage.tsx`: render SKUs grouped visually by detected condition; node click navigates to the Case page; add `/warehouse` route
    - _Requirements: 19.1, 19.2_

- [x] 15. Final checkpoint
  - Ensure all tests pass across every tier. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP; the safety property tests (4.5, 4.6) are the core evidence of the "agents never write" guarantee and are strongly recommended.
- The safety boundary is enforced early: task 4.1 defines the data-only contracts, task 4.4 keeps the orchestrator free of write imports, and task 4.5 turns that into an automated import-graph + no-mutation check before agent surface grows.
- Each task references specific requirements (granular sub-requirement clauses) for traceability.
- Property tests use Hypothesis (≥100 iterations); LLM calls are always mocked so deterministic properties run fast and offline.
- MUST HAVE (tasks 1–10) is the 3-day core loop. SHOULD HAVE (11–12) and NICE TO HAVE (13–14) build on it without changing the core.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "3.1", "3.3", "4.1", "4.2"] },
    { "id": 3, "tasks": ["2.1", "3.2", "3.4", "4.3"] },
    { "id": 4, "tasks": ["2.2", "3.5", "4.4"] },
    { "id": 5, "tasks": ["2.3", "4.5", "4.6", "4.7", "4.8", "4.9", "6.1"] },
    { "id": 6, "tasks": ["2.4", "6.2", "7.1"] },
    { "id": 7, "tasks": ["6.3", "7.2", "8.1", "11.1", "13.1"] },
    { "id": 8, "tasks": ["6.4", "6.5", "6.6", "6.7", "7.3", "7.4", "8.2", "11.2", "11.3", "12.1", "13.2"] },
    { "id": 9, "tasks": ["9.1", "9.2", "9.3", "11.4", "12.2", "13.3", "14.1"] },
    { "id": 10, "tasks": ["9.4"] }
  ]
}
```
