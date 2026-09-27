# Requirements Document

## Introduction

STOCKY currently supports uploading a single CSV of inventory data via `POST /api/inventory/upload`; each new upload implicitly replaces the working dataset with no way to keep, name, switch between, or remove previously uploaded datasets. This feature introduces dataset-scoped storage for `skus`, `cases`, and `recommendations`, a new "Datasets" page for viewing/switching/deleting uploaded datasets, and a set of dataset management endpoints. Exactly one dataset is active at a time; every existing read path (inventory, dashboard, warehouse, impact, agents/cases) must be scoped to the active dataset only, and the modified upload endpoint must create, load, and activate a dataset in place of its current single-dataset behavior. All new write logic must remain on the sanctioned write surface (ingest loader, `apply_action.py`, and the new dataset write functions) — agents never write to the database.

## Glossary

- **Dataset**: A named, timestamped collection of SKU, case, and recommendation rows created by one CSV upload, uniquely identified by a `dataset_id`.
- **Active_Dataset**: The single Dataset currently flagged as active; all read endpoints and new case/recommendation writes are scoped to it.
- **Dataset_Repo**: The sanctioned backend write module (e.g. `dataset_repo.py`) that creates, activates, renames, and deletes Dataset rows and performs cascading deletes of dependent rows.
- **Datasets_Page**: The new frontend page listing all Datasets, allowing the user to switch the Active_Dataset, rename a Dataset's display name, and delete a Dataset.
- **Display_Name**: A user-editable label shown for a Dataset in the Datasets_Page, defaulting to the uploaded filename.
- **Ingest_Loader**: The existing backend module invoked by `POST /api/inventory/upload` that parses an uploaded CSV and writes SKU rows.
- **Scoped_Read_Path**: Any backend router or service that queries `skus`, `cases`, or `recommendations` for display purposes (inventory, dashboard, warehouse, impact, agents/cases).

## Requirements

### Requirement 1: Dataset data isolation

**User Story:** As a STOCKY user, I want each uploaded dataset's SKUs, cases, and recommendations kept separate from other datasets, so that switching or deleting one dataset never affects another dataset's data.

#### Acceptance Criteria

1. THE Dataset_Repo SHALL associate every `skus` row with exactly one `dataset_id`.
2. THE Dataset_Repo SHALL associate every `cases` row with exactly one `dataset_id`.
3. THE Dataset_Repo SHALL associate every `recommendations` row with exactly one `dataset_id`.
4. WHEN a Scoped_Read_Path queries `skus`, `cases`, or `recommendations`, THE Scoped_Read_Path SHALL filter results to the `dataset_id` of the Active_Dataset.
5. THE Dataset_Repo SHALL store, for each Dataset, an uploaded filename, an upload timestamp, and a Display_Name.

### Requirement 2: Single active dataset

**User Story:** As a STOCKY user, I want exactly one dataset marked active at any time, so that the rest of the application always has an unambiguous data source.

#### Acceptance Criteria

1. THE Dataset_Repo SHALL maintain an active flag such that exactly one Dataset has the flag set at any time a Dataset exists.
2. WHEN a Dataset is activated, THE Dataset_Repo SHALL clear the active flag on every other Dataset before or within the same transaction that sets the new Active_Dataset.
3. WHEN the Active_Dataset changes, THE Dataset_Repo SHALL update only the active flag on Dataset rows and SHALL NOT copy, move, or reload `skus`, `cases`, or `recommendations` rows.
4. IF no Dataset exists, THEN THE Scoped_Read_Path SHALL return an empty result set for inventory, dashboard, warehouse, impact, and agents/cases queries.

### Requirement 3: Dataset upload creates and activates a dataset

**User Story:** As a STOCKY user, I want each CSV upload to become its own dataset and immediately become active, so that I can review its data right away without losing prior uploads.

#### Acceptance Criteria

1. WHEN a CSV is submitted to `POST /api/inventory/upload`, THE Ingest_Loader SHALL create a new Dataset row with the uploaded filename and current timestamp before writing any SKU rows.
2. WHEN the Ingest_Loader parses the uploaded CSV, THE Ingest_Loader SHALL write each resulting `skus` row with the `dataset_id` of the newly created Dataset.
3. WHEN the new Dataset's SKU rows finish loading, THE Dataset_Repo SHALL activate the newly created Dataset.
4. THE Ingest_Loader SHALL initialize the new Dataset's Display_Name to the uploaded filename.

### Requirement 4: Dataset deletion cascades

**User Story:** As a STOCKY user, I want to delete a dataset I no longer need, so that I can keep my dataset list free of clutter without leaving orphaned data behind.

#### Acceptance Criteria

1. WHEN a Dataset is deleted through `DELETE /api/datasets/{id}`, THE Dataset_Repo SHALL delete every `skus` row with a matching `dataset_id`.
2. WHEN a Dataset is deleted through `DELETE /api/datasets/{id}`, THE Dataset_Repo SHALL delete every `cases` row with a matching `dataset_id`.
3. WHEN a Dataset is deleted through `DELETE /api/datasets/{id}`, THE Dataset_Repo SHALL delete every `recommendations` row with a matching `dataset_id`.
4. WHEN a Dataset is deleted through `DELETE /api/datasets/{id}`, THE Dataset_Repo SHALL delete the Dataset row itself after its dependent rows are removed.
5. IF a delete request targets a `dataset_id` that does not exist, THEN THE Dataset_Repo SHALL return a not-found error and SHALL make no changes to any table.
6. IF the deleted Dataset was the Active_Dataset, THEN THE Dataset_Repo SHALL leave no Dataset active until the user activates a remaining Dataset.

### Requirement 5: Dataset listing endpoint

**User Story:** As a STOCKY user, I want to see metadata about every uploaded dataset, so that I can identify and choose between them.

#### Acceptance Criteria

1. WHEN a client sends `GET /api/datasets`, THE Dataset_Repo SHALL return every Dataset's id, filename, Display_Name, upload timestamp, SKU count, and active status.
2. THE Dataset_Repo SHALL compute each returned Dataset's SKU count from the count of `skus` rows matching that Dataset's `dataset_id`.

### Requirement 6: Dataset activation endpoint

**User Story:** As a STOCKY user, I want to switch which dataset is active, so that I can review a different dataset's inventory without re-uploading it.

#### Acceptance Criteria

1. WHEN a client sends `POST /api/datasets/{id}/activate` for an existing Dataset, THE Dataset_Repo SHALL set that Dataset as the Active_Dataset.
2. IF `POST /api/datasets/{id}/activate` targets a `dataset_id` that does not exist, THEN THE Dataset_Repo SHALL return a not-found error and SHALL NOT change the Active_Dataset.

### Requirement 7: Dataset rename endpoint

**User Story:** As a STOCKY user, I want to rename a dataset's display name, so that I can identify it more easily than by its raw filename.

#### Acceptance Criteria

1. WHEN a client sends `PATCH /api/datasets/{id}` with a new Display_Name, THE Dataset_Repo SHALL update that Dataset's Display_Name to the submitted value.
2. IF `PATCH /api/datasets/{id}` targets a `dataset_id` that does not exist, THEN THE Dataset_Repo SHALL return a not-found error and SHALL make no changes.
3. IF `PATCH /api/datasets/{id}` submits an empty Display_Name, THEN THE Dataset_Repo SHALL reject the request and SHALL make no changes.

### Requirement 8: Datasets page

**User Story:** As a STOCKY user, I want a dedicated Datasets page reachable from the main navigation, so that I can manage uploaded datasets in one place.

#### Acceptance Criteria

1. THE Datasets_Page SHALL appear as a navigation item positioned below the "Reports" navigation item, labeled with a 🗂️ emoji.
2. WHEN the Datasets_Page loads, THE Datasets_Page SHALL display, for every Dataset, its Display_Name, upload date/time, SKU count, and an active/inactive badge.
3. WHEN a user selects an inactive Dataset's switch control, THE Datasets_Page SHALL call `POST /api/datasets/{id}/activate` and SHALL update the displayed active/inactive badges to reflect the new Active_Dataset.
4. WHEN a user edits a Dataset's Display_Name and submits the change, THE Datasets_Page SHALL call `PATCH /api/datasets/{id}` and SHALL display the updated Display_Name.
5. WHEN a user selects a Dataset's delete control, THE Datasets_Page SHALL call `DELETE /api/datasets/{id}` and SHALL remove that Dataset from the displayed list.
6. IF a delete or activate request made from the Datasets_Page fails, THEN THE Datasets_Page SHALL display an error message and SHALL leave the dataset list unchanged.

### Requirement 9: Existing read paths remain dataset-scoped

**User Story:** As a STOCKY user, I want the inventory, dashboard, warehouse, impact, and agents/cases pages to reflect only the active dataset, so that data from other datasets never leaks into my current view.

#### Acceptance Criteria

1. WHEN the inventory router queries `skus`, THE inventory router SHALL restrict results to the Active_Dataset's `dataset_id`.
2. WHEN the dashboard router or service queries `skus`, `cases`, or `recommendations`, THE dashboard router SHALL restrict results to the Active_Dataset's `dataset_id`.
3. WHEN the warehouse view's backend router queries `skus`, THE warehouse view's backend router SHALL restrict results to the Active_Dataset's `dataset_id`.
4. WHEN the impact router queries `skus`, `cases`, or `recommendations`, THE impact router SHALL restrict results to the Active_Dataset's `dataset_id`.
5. WHEN the agents/cases router queries `cases` or `recommendations`, THE agents/cases router SHALL restrict results to the Active_Dataset's `dataset_id`.

### Requirement 10: Safety boundary preservation

**User Story:** As a STOCKY maintainer, I want dataset management writes confined to the same sanctioned write surface as existing writes, so that the system's agent/database safety boundary is not weakened.

#### Acceptance Criteria

1. THE Dataset_Repo SHALL be the only module that inserts, updates, or deletes Dataset rows.
2. THE Dataset_Repo SHALL be the only module that performs the cascading delete of `skus`, `cases`, and `recommendations` rows described in Requirement 4.
3. WHERE an agent module (Detective, Forecast, Strategy, or Manager) executes, THE agent module SHALL NOT call any Dataset_Repo write function.
4. THE dataset activation, rename, and delete endpoints SHALL call Dataset_Repo write functions rather than issuing direct write statements from router code.
