# Requirements Document

## Introduction

STOCKY is an agentic AI inventory monitoring and simulation prototype built for a 3-4 day hackathon. The platform ingests inventory data via CSV, runs deterministic Python rules to detect inventory conditions (fast-moving, slow-moving, overstock, stockout risk, trend/anomaly), then orchestrates a sequence of four LLM agents (Detective → Forecast → Strategy → Manager) to investigate findings and produce recommendations. A human reviewer approves or rejects each recommendation, and only deterministic backend code applies approved changes to the database.

The defining architectural constraint is a strict safety boundary: AI agents analyse and recommend but never mutate inventory data or the database. All state changes are performed by deterministic Python code after explicit human approval. Recommendations move through a lifecycle of pending → approved/rejected → applied.

The stack is React + Vite + Tailwind (frontend), Python + FastAPI (backend), SQLite (database), an external LLM API (AI reasoning), and Docker on AWS Lightsail (deployment). This document defines requirements across three priority tiers — MUST HAVE, SHOULD HAVE, and NICE TO HAVE — with MUST HAVE scoped to be achievable within 3 days.

## Glossary

- **STOCKY**: The overall inventory monitoring and simulation platform, comprising frontend, backend, detection engine, and agent orchestrator.
- **Detection_Engine**: The deterministic Python component that classifies inventory conditions using fixed rules and no LLM calls.
- **Agent_Orchestrator**: The deterministic Python component that runs the four LLM agents sequentially and collects their outputs.
- **Detective_Agent**: The first LLM agent; investigates a flagged SKU and explains the likely cause of the detected condition.
- **Forecast_Agent**: The second LLM agent; projects future demand and stock trajectory for a flagged SKU.
- **Strategy_Agent**: The third LLM agent; proposes candidate courses of action for a flagged SKU.
- **Manager_Agent**: The fourth LLM agent; consolidates prior agent outputs into a single recommendation with a proposed inventory action.
- **Recommendation**: A structured, agent-produced proposal for an inventory action, carrying a status of pending, approved, rejected, or applied.
- **Recommendation_Lifecycle**: The ordered set of statuses a Recommendation moves through: pending → approved or rejected → applied.
- **Inventory_Store**: The SQLite database and the deterministic backend code that reads from and writes to it.
- **Reviewer**: The human user who approves or rejects recommendations.
- **SKU**: A stock-keeping unit record, including name, category, current_stock, reorder_point, lead_time_days, unit_cost, and recent sales history.
- **Sales_Velocity**: Units sold per day, computed by the Detection_Engine from recent sales history.
- **Days_Of_Cover**: The number of days current_stock will last at current Sales_Velocity.
- **Stockout_ETA**: The estimated date on which current_stock reaches zero at current Sales_Velocity.
- **Dashboard**: The frontend page summarising inventory health and detected conditions.
- **Inventory_Table**: The frontend page listing all SKU records and their computed metrics.
- **Investigation_Page**: The frontend page presenting the four-agent case for a flagged SKU (also called the Case page).
- **Simulation_Engine**: The deterministic Python component that projects outcomes for hypothetical (what-if) inputs without writing to the Inventory_Store.
- **Impact_Page**: The frontend page presenting metrics on detected conditions and recommendation outcomes.
- **Chaos_Mode**: An optional feature that injects synthetic disruptive events into the dataset for demonstration.
- **Warehouse_Visualisation**: An optional interactive graphical depiction of inventory state.

## Requirements

### Requirement 1: CSV Upload and Ingestion (MUST HAVE)

**User Story:** As a Reviewer, I want to upload a CSV of inventory data, so that STOCKY can analyse my current stock.

#### Acceptance Criteria

1. WHEN a Reviewer submits a CSV file, THE STOCKY SHALL parse rows containing SKU, name, category, current_stock, reorder_point, lead_time_days, unit_cost, and recent sales history.
2. WHEN a CSV file is parsed successfully, THE Inventory_Store SHALL persist each parsed SKU record.
3. IF a CSV file is missing a required column, THEN THE STOCKY SHALL reject the upload and return a message naming the missing column.
4. IF a CSV row contains a non-numeric value in current_stock, reorder_point, lead_time_days, or unit_cost, THEN THE STOCKY SHALL reject that row and return the affected row number.
5. WHEN a CSV upload completes, THE STOCKY SHALL return the count of accepted SKU records and the count of rejected rows.

### Requirement 2: Deterministic Metric Computation (MUST HAVE)

**User Story:** As a Reviewer, I want STOCKY to compute inventory metrics from my data, so that detection is fast, consistent, and does not depend on an LLM.

#### Acceptance Criteria

1. WHEN a SKU record is ingested, THE Detection_Engine SHALL compute Sales_Velocity from the recent sales history using deterministic Python code.
2. WHEN Sales_Velocity is greater than zero, THE Detection_Engine SHALL compute Days_Of_Cover as current_stock divided by Sales_Velocity.
3. WHEN Sales_Velocity is greater than zero, THE Detection_Engine SHALL compute Stockout_ETA from current_stock and Sales_Velocity.
4. THE Detection_Engine SHALL compute all metrics without invoking any LLM API.
5. WHERE Sales_Velocity equals zero, THE Detection_Engine SHALL record Days_Of_Cover as undefined and mark the SKU as having no recent sales.

### Requirement 3: Deterministic Condition Detection (MUST HAVE)

**User Story:** As a Reviewer, I want STOCKY to flag inventory conditions automatically, so that I can focus attention on SKUs that need action.

#### Acceptance Criteria

1. WHEN Days_Of_Cover is at or below the lead_time_days threshold, THE Detection_Engine SHALL classify the SKU as stockout risk.
2. WHEN current_stock is at or below reorder_point, THE Detection_Engine SHALL classify the SKU as needing reorder.
3. WHEN Sales_Velocity exceeds the configured fast-moving threshold, THE Detection_Engine SHALL classify the SKU as fast-moving.
4. WHEN Sales_Velocity is at or below the configured slow-moving threshold, THE Detection_Engine SHALL classify the SKU as slow-moving.
5. WHEN Days_Of_Cover exceeds the configured overstock threshold, THE Detection_Engine SHALL classify the SKU as overstock.
6. WHEN the recent sales history shows a directional change beyond the configured anomaly threshold, THE Detection_Engine SHALL classify the SKU as having a trend or anomaly.
7. THE Detection_Engine SHALL attach every applicable classification to each SKU using deterministic Python rules.

### Requirement 4: Detective Agent Investigation (MUST HAVE)

**User Story:** As a Reviewer, I want an AI agent to explain why a SKU was flagged, so that I understand the underlying cause.

#### Acceptance Criteria

1. WHEN the Agent_Orchestrator processes a flagged SKU, THE Detective_Agent SHALL produce a written investigation of the detected condition.
2. THE Detective_Agent SHALL base the investigation on the SKU record and the Detection_Engine classifications provided as input.
3. THE Detective_Agent SHALL NOT modify any SKU record or the Inventory_Store.
4. IF the LLM API returns an error, THEN THE Agent_Orchestrator SHALL record the Detective_Agent step as failed and return an error status for the case.

### Requirement 5: Forecast Agent Projection (MUST HAVE)

**User Story:** As a Reviewer, I want an AI agent to project future demand for a flagged SKU, so that I can anticipate stock trajectory.

#### Acceptance Criteria

1. WHEN the Detective_Agent step completes, THE Agent_Orchestrator SHALL invoke the Forecast_Agent with the SKU record, classifications, and Detective_Agent output.
2. THE Forecast_Agent SHALL produce a projection of future demand and stock trajectory for the SKU.
3. THE Forecast_Agent SHALL NOT modify any SKU record or the Inventory_Store.
4. IF the LLM API returns an error, THEN THE Agent_Orchestrator SHALL record the Forecast_Agent step as failed and return an error status for the case.

### Requirement 6: Strategy Agent Options (MUST HAVE)

**User Story:** As a Reviewer, I want an AI agent to propose courses of action, so that I have candidate strategies to consider.

#### Acceptance Criteria

1. WHEN the Forecast_Agent step completes, THE Agent_Orchestrator SHALL invoke the Strategy_Agent with the SKU record, classifications, and prior agent outputs.
2. THE Strategy_Agent SHALL produce one or more candidate courses of action for the SKU.
3. THE Strategy_Agent SHALL NOT modify any SKU record or the Inventory_Store.
4. IF the LLM API returns an error, THEN THE Agent_Orchestrator SHALL record the Strategy_Agent step as failed and return an error status for the case.

### Requirement 7: Manager Agent Recommendation (MUST HAVE)

**User Story:** As a Reviewer, I want an AI agent to consolidate the analysis into a single recommendation, so that I can make one clear decision.

#### Acceptance Criteria

1. WHEN the Strategy_Agent step completes, THE Agent_Orchestrator SHALL invoke the Manager_Agent with the SKU record, classifications, and all prior agent outputs.
2. THE Manager_Agent SHALL produce a single Recommendation containing a proposed inventory action and a supporting rationale.
3. WHEN the Manager_Agent produces a Recommendation, THE STOCKY SHALL persist the Recommendation with status pending.
4. THE Manager_Agent SHALL NOT modify any SKU record or the Inventory_Store.
5. IF the LLM API returns an error, THEN THE Agent_Orchestrator SHALL record the Manager_Agent step as failed and return an error status for the case.

### Requirement 8: Sequential Agent Orchestration (MUST HAVE)

**User Story:** As a Reviewer, I want the four agents to run in a fixed order, so that each agent builds on the previous analysis.

#### Acceptance Criteria

1. WHEN an investigation is started for a flagged SKU, THE Agent_Orchestrator SHALL invoke the agents in the order Detective_Agent, Forecast_Agent, Strategy_Agent, Manager_Agent.
2. WHEN each agent completes, THE Agent_Orchestrator SHALL pass that agent's output as input to the next agent.
3. IF any agent step fails, THEN THE Agent_Orchestrator SHALL stop the sequence and report which step failed.
4. THE Agent_Orchestrator SHALL run as deterministic Python code that coordinates agent invocations.

### Requirement 9: AI Safety Boundary (MUST HAVE)

**User Story:** As a Reviewer, I want a guarantee that AI agents cannot change my data, so that I retain full control over inventory state.

#### Acceptance Criteria

1. THE Agent_Orchestrator SHALL restrict all four agents to producing analysis and recommendation text only.
2. WHEN any agent runs, THE STOCKY SHALL prevent that agent from issuing a write operation to the Inventory_Store.
3. THE STOCKY SHALL apply changes to the Inventory_Store only through deterministic backend code invoked after Reviewer approval.
4. IF an agent output attempts to encode a direct data mutation, THEN THE STOCKY SHALL treat that output as recommendation text and SHALL NOT execute it as a data operation.

### Requirement 10: Human Approve and Reject (MUST HAVE)

**User Story:** As a Reviewer, I want to approve or reject each recommendation, so that no change happens without my consent.

#### Acceptance Criteria

1. WHILE a Recommendation has status pending, THE STOCKY SHALL allow the Reviewer to approve or reject the Recommendation.
2. WHEN a Reviewer approves a pending Recommendation, THE STOCKY SHALL set the Recommendation status to approved.
3. WHEN a Reviewer rejects a pending Recommendation, THE STOCKY SHALL set the Recommendation status to rejected.
4. IF a Reviewer attempts to approve or reject a Recommendation that is not pending, THEN THE STOCKY SHALL reject the action and return the current status.

### Requirement 11: Deterministic Database Update on Approval (MUST HAVE)

**User Story:** As a Reviewer, I want approved recommendations to update the database automatically and correctly, so that acting on a decision is reliable.

#### Acceptance Criteria

1. WHEN a Recommendation status becomes approved, THE Inventory_Store SHALL apply the proposed inventory action using deterministic backend code.
2. WHEN the deterministic update completes successfully, THE STOCKY SHALL set the Recommendation status to applied.
3. IF the deterministic update fails, THEN THE STOCKY SHALL leave the Recommendation status at approved and return an error describing the failure.
4. THE STOCKY SHALL apply the inventory action only once per Recommendation.

### Requirement 12: Recommendation Lifecycle Tracking (MUST HAVE)

**User Story:** As a Reviewer, I want to see the status of every recommendation, so that I can track what is pending, decided, and applied.

#### Acceptance Criteria

1. THE STOCKY SHALL record each Recommendation with one status from the set pending, approved, rejected, applied.
2. WHEN a Recommendation status changes, THE STOCKY SHALL follow the Recommendation_Lifecycle order of pending to approved or rejected, and approved to applied.
3. IF a status transition is requested that is not part of the Recommendation_Lifecycle order, THEN THE STOCKY SHALL reject the transition and retain the current status.

### Requirement 13: Dashboard (MUST HAVE)

**User Story:** As a Reviewer, I want a dashboard summarising inventory health, so that I can see the overall state at a glance.

#### Acceptance Criteria

1. WHEN the Reviewer opens the Dashboard, THE STOCKY SHALL display the count of SKUs in each detected condition category.
2. WHEN the Reviewer opens the Dashboard, THE STOCKY SHALL display the count of Recommendations in each Recommendation_Lifecycle status.
3. WHERE at least one SKU is classified as stockout risk, THE Dashboard SHALL present those SKUs as a prioritised group.

### Requirement 14: Inventory Table (MUST HAVE)

**User Story:** As a Reviewer, I want a table of all SKUs and their metrics, so that I can inspect individual items.

#### Acceptance Criteria

1. WHEN the Reviewer opens the Inventory_Table, THE STOCKY SHALL list every SKU record with current_stock, reorder_point, Sales_Velocity, and Days_Of_Cover.
2. THE Inventory_Table SHALL display the Detection_Engine classifications attached to each SKU.
3. WHEN the Reviewer selects a SKU in the Inventory_Table, THE STOCKY SHALL provide navigation to the Investigation_Page for that SKU.

### Requirement 15: AI Investigation / Case Page (MUST HAVE)

**User Story:** As a Reviewer, I want a page that presents the full four-agent case for a SKU, so that I can review the reasoning behind a recommendation.

#### Acceptance Criteria

1. WHEN the Reviewer opens the Investigation_Page for a SKU, THE STOCKY SHALL display the Detective_Agent, Forecast_Agent, Strategy_Agent, and Manager_Agent outputs for that SKU.
2. WHEN a Recommendation exists for the SKU, THE Investigation_Page SHALL display the Recommendation and its current status.
3. WHILE a Recommendation on the Investigation_Page has status pending, THE STOCKY SHALL present approve and reject controls.
4. IF no investigation has been run for the SKU, THEN THE Investigation_Page SHALL present a control to start the investigation.

### Requirement 16: What-If Simulation (SHOULD HAVE)

**User Story:** As a Reviewer, I want to simulate hypothetical changes, so that I can compare outcomes before deciding.

#### Acceptance Criteria

1. WHEN the Reviewer submits hypothetical inputs for a SKU, THE Simulation_Engine SHALL compute projected metrics using deterministic Python code.
2. THE Simulation_Engine SHALL NOT modify any SKU record or the Inventory_Store.
3. WHEN a simulation completes, THE STOCKY SHALL display the projected metrics alongside the current metrics for comparison.

### Requirement 17: Impact / Metrics Page (SHOULD HAVE)

**User Story:** As a Reviewer, I want a metrics page, so that I can measure the effect of detections and decisions.

#### Acceptance Criteria

1. WHEN the Reviewer opens the Impact_Page, THE STOCKY SHALL display counts of Recommendations by outcome status across the dataset.
2. WHEN the Reviewer opens the Impact_Page, THE STOCKY SHALL display aggregate metrics for detected conditions across all SKUs.

### Requirement 18: Chaos Mode (NICE TO HAVE)

**User Story:** As a Reviewer, I want to inject disruptive events for demonstration, so that I can showcase how STOCKY responds to sudden change.

#### Acceptance Criteria

1. WHERE Chaos_Mode is enabled, THE STOCKY SHALL inject synthetic disruptive events into the working dataset.
2. WHEN Chaos_Mode injects events, THE Detection_Engine SHALL re-run classification on the affected SKUs.
3. THE STOCKY SHALL apply Chaos_Mode events only to the working dataset and SHALL NOT alter the originally uploaded source data.

### Requirement 19: Interactive Warehouse Visualisation (NICE TO HAVE)

**User Story:** As a Reviewer, I want an interactive warehouse visualisation, so that inventory state is engaging and easy to grasp.

#### Acceptance Criteria

1. WHEN the Reviewer opens the Warehouse_Visualisation, THE STOCKY SHALL render a graphical depiction of SKUs grouped by detected condition.
2. WHEN the Reviewer selects a SKU in the Warehouse_Visualisation, THE STOCKY SHALL provide navigation to the Investigation_Page for that SKU.
