# USPTO Data Platform — Frontend Specification

## 1. Project Overview

The USPTO Data Platform is a serverless web application for exploring USPTO patent data. It solves a critical data quality problem: entity names (patent applicants and assignees) in USPTO databases are riddled with typos and variations. The same company may appear as "GOOGLE LLC", "GOGGLE LLC", "GOGLE INC", etc.

The platform provides three core capabilities:

1. **Manual Name Normalization (MDM)** — A human-driven workflow to unify messy entity name variants under a single "Representative Name"
2. **Boolean Query Builder** — Structured multi-condition queries across patent data, assignments, and maintenance fee records
3. **AI Assistant** — Natural language queries translated to SQL via Google Gemini

When searching by entity name in Tabs 2 and 3, the system leverages normalization: if the searched name is a Representative Name (or is associated with one), the query automatically expands to cover all associated name variants.

## 2. Architecture

- **Frontend:** Vanilla HTML/CSS/JavaScript single-page application served as static files by FastAPI
- **Backend API:** Python FastAPI on Google Cloud Run
- **Database:** Google BigQuery (`uspto-data-app.uspto_data`)
- **AI:** Google Gemini 2.5 Flash for natural language to SQL translation
- **CI/CD:** GitHub Actions deploying to Cloud Run via Workload Identity Federation

The frontend is served by the FastAPI application at `/static/` and the root `/` serves `index.html`.

## 3. Database Schema

### 3.1 `patent_file_wrapper`
Patent bibliographic data from USPTO file wrappers.

| Column | Type | Description |
|--------|------|-------------|
| patent_number | STRING | Patent or application number |
| invention_title | STRING | Title of the invention |
| grant_date | DATE | Date the patent was granted |
| applicants | ARRAY\<STRUCT\> | List of applicants |

**Applicant STRUCT fields:** `name` (STRING), `street_address` (STRING, nullable), `city` (STRING), `state` (STRING), `country` (STRING), `entity_type` (STRING)

Partitioned by `grant_date`, clustered by `patent_number`.

### 3.2 `patent_assignments`
Patent assignment/transfer records.

| Column | Type | Description |
|--------|------|-------------|
| patent_number | STRING | Patent or application number |
| recorded_date | DATE | Date the assignment was recorded |
| assignees | ARRAY\<STRUCT\> | List of assignees |

**Assignee STRUCT fields:** `name` (STRING), `street_address` (STRING), `city` (STRING), `state` (STRING), `country` (STRING)

### 3.3 `maintenance_fee_events`
Maintenance fee payment history.

| Column | Type | Description |
|--------|------|-------------|
| patent_number | STRING | Patent number |
| event_code | STRING | Fee event code |
| event_date | DATE | Date of the fee event |
| fee_code | STRING | Fee type code |
| entity_status | STRING | Entity size status (SMALL, MICRO, LARGE) |

### 3.4 `name_unification`
Stores manual name normalization associations. Each row represents one association between a raw entity name and its chosen Representative Name.

| Column | Type | Description |
|--------|------|-------------|
| representative_name | STRING | The canonical/clean version of the entity name |
| associated_name | STRING | A variant/typo name linked to the representative |

A Representative Name may have many associated names (one-to-many). A name that is a Representative of itself will have one row where `representative_name` equals `associated_name`.

## 4. Tab 1 — MDM Name Normalization

### 4.1 Purpose

Provides a manual, human-supervised workspace for unifying entity name variants. The user searches for names, identifies the most likely correct spelling (typically the one with the highest frequency), designates it as the Representative Name, and associates typo variants with it.

### 4.2 Search Bar

At the top of Tab 1, a search bar accepts boolean search expressions against entity names from both the `patent_file_wrapper` (applicants) and `patent_assignments` (assignees) tables.

**Boolean operators:**
- `+` — AND (all terms must appear)
- `-` — NOT (term must not appear)
- `*` — Wildcard (any characters)

**Example searches:**
- `GOOG*` — finds "GOOGLE LLC", "GOOGLE INC", "GOOGL CORP", etc.
- `MICRO*+CORP` — finds names containing both a "MICRO..." prefix and "CORP"
- `APPLE+-INC` — finds names containing "APPLE" but not "INC"

### 4.3 Results Table (The Workspace)

Search results are displayed as a table of unique entity names. Each name appears once regardless of how many times it occurs in the databases. The table has the following columns:

| Column | Content | Description |
|--------|---------|-------------|
| 0 | **Checkbox** | Allows multi-select of rows for batch normalization. Supports Ctrl+Click for range selection: clicking checkbox on row 5, then Ctrl+clicking checkbox on row 10 selects all rows 5–10. Individual checkboxes toggle independently without modifier keys. |
| 1 | **Address icon** | Opens the Address Modal (see §4.6) showing unique mailing addresses associated with this entity name. |
| 2 | **Raw Name** | The entity name as found in the database. |
| 3 | **Copy icon** | Marks this name as the active Representative Name. The name in column 2 turns **red** to indicate it is selected. Selection persists across table re-sorts until a different name is selected via any Copy icon. |
| 4 | **Paste icon** | Associates this row's name with the currently selected (red) Representative Name. If multiple rows are selected via checkboxes (column 0), clicking any Paste icon in the selection normalizes all selected rows at once. |
| 5 | **Frequency** | Total number of occurrences of this name across both `patent_file_wrapper` (applicants) and `patent_assignments` (assignees). |
| 6 | **Representative Name** | Displays the Representative Name this row's name is currently associated with. Empty if not yet normalized. |
| 7 | **Copy icon (secondary)** | Clicking this is equivalent to clicking the Copy icon (column 3) on the Representative Name shown in column 6. This allows selecting a Representative Name as active even when it is not visible in the current table/search results. |
| 8 | **Trash icon** | Deletes the association between this row's name and its Representative Name (shown in column 6), un-normalizing it. |

### 4.4 Color Coding

- **Red** — The currently active/selected Representative Name (set by clicking a Copy icon). Remains red across table re-sorts until a different name is selected.
- **Orange** — A name that IS a Representative Name (has associations), displayed when no copy selection is active or for non-selected representatives.
- **Green** — A normalized name (a name that has been associated with a Representative Name).
- Default/unstyled — A name that has no associations and is not a representative.

### 4.5 Sorting

The table is sortable by clicking column headers on:
- **Raw Name** (column 2) — alphabetical sort
- **Frequency** (column 5) — numerical sort
- **Representative Name** (column 6) — alphabetical sort

### 4.6 Normalization Workflow

**Typical workflow:**

1. User searches for a name pattern (e.g., `GOOG*`)
2. Results appear in the workspace table
3. User **sorts by Frequency** (column 5, descending) — the most common spelling rises to the top
4. User **clicks Copy** (column 3) on the most frequent name — it turns **red**, becoming the active Representative
5. User **sorts by Raw Name** (column 2, alphabetical) to see similar name variants grouped together
6. User **selects checkboxes** (column 0) on a range of obvious typo variants (using Ctrl+Click for ranges)
7. User **clicks Paste** (column 4) on any of the selected rows — all selected names are associated with the red Representative Name
8. The Representative Name appears in column 6 for each normalized row, and those names turn **green**

**Self-association:** Clicking Copy then Paste on the same row creates a Representative that is associated with itself. This explicitly marks a name as a Representative Name.

**Re-normalization cascading:** If a Representative Name (which already has names associated with it) is itself normalized under a different Representative Name via Paste, all names previously associated with the old Representative automatically cascade to the new Representative. The old Representative itself also becomes associated with the new one.

**Deleting a Representative's association (Trash icon on a Representative):** All names that were associated with that Representative become un-associated.

### 4.7 Address Modal

Clicking the **Address icon** (column 1) opens a modal/popup displaying all unique mailing addresses (street address and city) associated with the entity name in that row.

**If the name is a Representative Name:** The modal aggregates addresses from the Representative itself AND from all names normalized under it.

**Modal contents:**
- A scrollable list of unique addresses
- Each address has a **checkbox** to its left
- Checkboxes support **Ctrl+Click range selection** (same behavior as the main table checkboxes)
- **"Search Entities" button** — Runs a query using all selected addresses to find other entities at those locations, then displays results in a **second modal** with the same table structure as the main workspace (supporting the same normalization actions)
- **"Unselect All" button** — Clears all address checkboxes at once

**Purpose:** Address-based search is a fallback for finding severely misspelled entity names that boolean name search cannot catch. If two entities share the same mailing address, they are likely the same company despite different name spellings.

## 5. Tab 2 — Boolean Query Builder

### 5.1 Purpose

Allows users to construct structured, multi-condition queries across patent data. A dropdown lets the user select which data tables to include in the query: `patent_file_wrapper`, `patent_assignments`, `maintenance_fee_events`, or combinations thereof.

### 5.2 Query Capabilities

The builder supports conditions on fields from any of the selected tables, joined via AND/OR logic. Queries leverage name normalization: when filtering by entity name, if the name is a Representative or is associated with one, the query automatically expands to include all associated name variants.

**Supported fields include:**
- Patent number, invention title, grant date
- Applicant/assignee name, city, state, country, entity type
- Maintenance fee event code, event date, fee code, entity status

**Example queries the UI must support:**
- "List all patents and patent applications filed by applicant or assigned to 'Google LLC', for which 3rd maintenance fee was paid at small entity rate, in the past 5 years."
- "List the 20 entity names associated with the highest number of 3rd maintenance fee payments at small entity rates in the past 10 years."

### 5.3 UI Elements

- **Table selector** — Dropdown/multi-select to choose which BigQuery tables to query against
- **Condition rows** — Each row has a field selector, operator selector (CONTAINS, EQUALS, STARTS_WITH, ENDS_WITH), and value input
- **Logic toggle** — AND / OR to join conditions
- **Max results** — Numeric input to limit returned rows
- **Execute button** — Runs the query and displays results in a data table

## 6. Tab 3 — AI Assistant

### 6.1 Purpose

A natural language interface where users type questions in plain English. Google Gemini 2.5 Flash translates the question into BigQuery SQL, executes it, and returns both the answer and the raw data.

### 6.2 Query Capabilities

Same scope as Tab 2 — can query across all tables and leverages name normalization for entity name expansion.

**Example queries:**
- "Show me all patents filed by Google in the last 5 years where small entity maintenance fees were paid."
- "Which 20 entities have the most 3rd maintenance fee payments at small entity rates in the past decade?"

### 6.3 UI Elements

- **Chat interface** — Scrollable message area with user and AI message bubbles
- **Text input** — Textarea for natural language questions (supports Ctrl/Cmd+Enter to submit)
- **Generated SQL block** — Displays the SQL that Gemini produced (collapsible card)
- **Results table** — Displays returned data rows

## 7. API Endpoints

### 7.1 MDM Endpoints (Tab 1)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/mdm/search` | Boolean search for entity names across both tables. Returns unique names with frequencies and current representative associations. |
| POST | `/mdm/associate` | Creates an association between one or more entity names and a representative name. Handles cascading if the target is already a representative. |
| DELETE | `/mdm/associate` | Removes the association for a given entity name. If the name is a representative, un-associates all names under it. |
| POST | `/mdm/addresses` | Returns unique addresses for a given entity name (and all associated names if it is a representative). |
| POST | `/mdm/search-by-address` | Finds entity names matching the given addresses. |

### 7.2 Query Endpoints (Tab 2)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/query/fields` | Returns available fields, operators, and tables for the query builder. |
| POST | `/query/execute` | Executes a boolean query with the given conditions, logic operator, selected tables, and limit. Expands entity names via normalization. |

### 7.3 AI Endpoints (Tab 3)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/ai/ask` | Accepts a natural language prompt, generates SQL via Gemini, executes it, and returns the answer, generated SQL, and data rows. Expands entity names via normalization. |

### 7.4 Utility Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check. Returns `{"status": "ok"}`. |
| GET | `/` | Serves the frontend `index.html`. |

## 8. Name Normalization Integration in Queries (Tabs 2 & 3)

When a query in Tab 2 or Tab 3 filters by entity name:

1. The system checks `name_unification` to see if the searched name has a `representative_name`.
2. If it does, the system finds the representative and ALL names associated with that representative.
3. The query is expanded to search for all those name variants using an `IN (...)` clause or equivalent.

This ensures that a search for "Google LLC" (if it is a Representative) automatically covers "GOGGLE LLC", "GOGLE INC", and every other variant the user has manually associated.

## 9. Technology & Dependencies

- **Python 3.11** with FastAPI 0.109.2 and Uvicorn
- **Google Cloud BigQuery** Python client >=3.17.0
- **Google Generative AI SDK** >=0.4.0 (Gemini 2.5 Flash)
- **Pydantic** >=2.0.0 for request/response validation
- **Docker** container deployed to Google Cloud Run
- **GitHub Actions** CI/CD with Workload Identity Federation
