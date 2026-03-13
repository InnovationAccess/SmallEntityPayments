# HANDOFF — SmallEntityPayments Project Snapshot (2026-03-12, Session 2)

## System Overview

**SmallEntityPayments** is a web-based patent data analytics platform. It ingests official USPTO bulk data products (patent file wrappers, assignments, maintenance fees, forward citations) into Google BigQuery, then provides a web UI for patent attorneys and business strategists to search, cross-reference, and analyze that data.

**Who it's for:** Patent attorneys, IP strategists, and innovation analysts who need to track patent ownership changes, entity status (small vs. large), maintenance fee history, citation patterns, and prosecution payment behavior across millions of US patents.

**What it does:**
- Normalizes messy USPTO entity names (MDM — Master Data Management)
- Runs boolean queries across patent, assignment, and maintenance fee data
- Provides a conversational AI assistant (Gemini) for natural-language patent data queries
- Looks up forward citations for any patent with summary statistics, breakdown by examiner/applicant, and year chart
- Shows assignment chain popup on hover over any patent number in results
- Tracks entity status conversions (small/micro/large) with conversion search and applicant portfolio analysis
- Investigates prosecution payment patterns with 3-phase workflow (entity discovery, application drill-down, invoice extraction)
- Monitors automated data pipeline updates from USPTO

**Repository:** https://github.com/InnovationAccess/SmallEntityPayments
**Live URL:** https://uspto-api-1094570457455.us-central1.run.app

---

## What Changed Since Last Handoff (HANDOFF313.md -> HANDOFF314.md)

This section covers 5 commits from `217877b` through `efd4c6c`.

### 1. Help Button Fix, Draggable Assignment Popup, Column Pickers (`217877b`)

- **Help toggle buttons**: Changed from event delegation to inline `onclick` handlers on all 7 tabs for reliable click handling
- **Assignment popup**: Made movable (drag by header) and resizable (drag corner handle), with proper z-index management
- **Column selector dropdowns**: Added to all data tables across all tabs for column visibility control

### 2. Assignment Chain Bug Fix (`91b37dd`)

**Root cause:** The assignment chain SQL used `WHERE application_number = @id OR patent_number = @id`, which caused collisions when a patent number (e.g., 11172434 for ETRI) matched a different patent's application serial number (e.g., application 11/172,434 for Stryker patent 7398571).

**Fix in `api/routers/assignments.py`:** Changed to a two-step resolution approach:
1. First resolves `patent_number` -> `application_number` via `patent_file_wrapper_v2`
2. Then finds assignment `reel_frame`s for that application_number via `pat_assign_documents`
3. Falls back to direct `patent_number` match if no file wrapper record exists

**Architecture rule established:** A string should NEVER be searched across multiple fields simultaneously. All cross-table lookups must resolve to `application_number` first (the universal key).

### 3. Boolean Name Search in Entity Status Tab (`efd4c6c`)

Added ability to search for entity names using boolean expressions (e.g., `+elect* +telecom* +rese* +inst*`) in both applicant name fields on the Entity Status tab.

**Frontend changes:**
- `frontend/index.html`: Added "Find" buttons and suggestion dropdown containers for both the Conversion Search applicant field and the Applicant Portfolio input. Updated placeholders and help text.
- `frontend/css/styles.css` (v=10): New styles for `.es-name-search-row`, `.es-suggestion-list`, `.es-suggestion-item`, `.es-suggestion-header`
- `frontend/js/entity_status.js` (v=3): New `findNames()` function (calls existing `POST /mdm/search` endpoint), `isBooleanQuery()` helper, event wiring for Find buttons and Enter key handling

**No backend changes needed** — reuses the existing `POST /mdm/search` endpoint that already parses boolean expressions.

**UX flow:**
1. User types `+elect* +telecom*` in applicant field
2. Clicks "Find" (or presses Enter if boolean detected)
3. Dropdown shows matching names with frequency counts
4. User clicks a name -> it populates the input, dropdown hides
5. User clicks "Search Conversions" or "Analyze Portfolio" as normal

---

## Domain Terminology

| Term | Definition |
|------|-----------|
| **PTFWPRE** | Patent File Wrapper Pre-grant/grant — USPTO bulk product containing application metadata, transaction history, and continuity data for all US patent applications |
| **PTBLXML** | Patent Grant Bibliographic XML — weekly USPTO bulk product containing citation data from newly granted patents |
| **PASDL** | Patent Assignment Daily — daily USPTO bulk product with ownership transfer records |
| **PASYR** | Patent Assignment Yearly — annual USPTO bulk product with the same data as PASDL but as yearly archives |
| **PTMNFEE2** | Patent Maintenance Fee Events — USPTO bulk product with maintenance fee payment/lapse history |
| **ODP API** | USPTO Open Data Portal API — the REST API at api.uspto.gov used to list and download bulk data products |
| **MDM** | Master Data Management — the process of normalizing variant entity names to a single canonical "representative" name |
| **Name Unification** | The system's name for MDM associations — mapping raw names to a representative name |
| **Entity Status** | Whether a patent applicant qualifies as "small entity" (reduced fees), "micro entity", or "large entity" |
| **Forward Citations** | Patents that cite a given patent — indicates the patent's influence on later innovations |
| **Conveyance** | The type of ownership transfer in a patent assignment (e.g., "ASSIGNMENT OF ASSIGNORS INTEREST") |
| **Conveyance Type** | Classified category of conveyance (ASSIGNMENT, SECURITY_INTEREST, MERGER, RELEASE, LICENSE, GOVERNMENT_INTEREST, CORRECTION, OTHER) |
| **Reel/Frame** | The physical recording location of a patent assignment at the USPTO — used as the primary key linking the 4 assignment tables |
| **CPC Codes** | Cooperative Patent Classification — hierarchical codes classifying a patent's technology area |
| **Kind Code** | A letter suffix on a patent number indicating the document type (B1=granted patent without prior pub, B2=with prior pub, A1=application pub) |
| **SMAL** | PFW transaction event code for Small Entity declaration |
| **BIG.** | PFW transaction event code for Large Entity declaration |
| **MICR** | PFW transaction event code for Micro Entity declaration |
| **Prosecution Fees** | Fees paid during patent prosecution (examination, issue, maintenance) — entity status determines fee schedule |

---

## Architecture

```
User Browser
    |
    v
Cloud Run Service (uspto-api, us-central1)
    |-- FastAPI (Python 3.11)
    |   |-- /static/* -> frontend/ (Vanilla JS SPA)
    |   |-- /mdm/* -> MDM name normalization API
    |   |-- /query/* -> Boolean query builder API
    |   |-- /ai/* -> Gemini AI assistant API
    |   |-- /api/forward-citations/* -> Citation lookup API
    |   |-- /api/assignments/* -> Assignment chain API (4-table JOIN)
    |   |-- /api/entity-status/* -> Entity status analytics API
    |   |-- /api/prosecution/* -> Prosecution fee investigation API
    |   |-- /api/etl-log/* -> Pipeline monitoring API
    |   |-- /health -> Health check
    |
    v
Google BigQuery (us-west1, dataset: uspto_data)
    |-- 12 tables, ~828M rows, ~62 GB
    |
Google Vertex AI (us-central1)
    |-- Gemini model for natural language -> SQL (AI assistant)
    |-- Gemini Vision for PDF extraction (prosecution invoices)
    |
Google Cloud Storage (gs://uspto-bulk-staging/)
    |-- Staging area for JSONL files before BQ load
    |-- prosecution-invoices/ — downloaded USPTO payment PDFs

Cloud Run Jobs (4 jobs, us-central1)
    |-- Triggered by Cloud Scheduler
    |-- Run update_pipeline.py for each data source
    |-- Download from USPTO ODP API -> parse -> GCS -> BigQuery
```

**Data flow:** USPTO ODP API -> Cloud Run Job downloads ZIP -> ETL parser creates JSONL.gz (4 files for assignments) -> gsutil uploads to GCS -> bq load into BigQuery -> API serves to frontend.

**Key architectural decisions:**
- All BigQuery tables use flat/denormalized schemas (no STRUCT or ARRAY columns, except `cpc_codes ARRAY<STRING>`)
- Patent assignments normalized into 4 tables linked by `reel_frame`, with `application_number` as the universal cross-table join key
- Frontend is vanilla JS with no build toolchain — served as static files from the same Cloud Run container
- ETL jobs use the `bq` and `gsutil` CLI tools (not Python SDK) for BigQuery loads and GCS uploads
- Each ETL source runs in its own Cloud Run Job with its own scheduler
- Entity status is DERIVED from maintenance fee event codes (M1=LARGE, M2=SMALL, M3=MICRO), never from the entity_status column (which USPTO populates inconsistently)
- Patent number resolution always goes through application_number — NEVER search a string across multiple fields simultaneously

---

## Backend

### API Entry Point
**File:** `api/main.py` (40 lines)

Registers 8 routers, serves frontend static files, and provides a health check.

### API Routers

#### 1. MDM Router (`api/routers/mdm.py`)
**Prefix:** `/mdm` | **Tag:** MDM

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/mdm/search` | Boolean search on entity names (`+` AND, `-` NOT, `*` wildcard) |
| POST | `/mdm/associate` | Link variant names to a representative (canonical) name |
| DELETE | `/mdm/associate` | Remove a name association (cascades if representative) |
| POST | `/mdm/addresses` | Get addresses for an entity name from assignment records |
| POST | `/mdm/search-by-address` | Find entity names at given addresses |

#### 2. Query Router (`api/routers/query.py`)
**Prefix:** `/query` | **Tag:** Query

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/query/execute` | Execute a multi-condition boolean query across selected tables |
| GET | `/query/fields` | Get available tables, fields, and operators for the query builder |
| GET | `/query/event-codes` | Get distinct maintenance fee event codes |
| GET | `/query/entity-statuses` | Get distinct entity status values |

Name fields (applicant_name, inventor_name, etc.) get special treatment: EQUALS triggers name expansion via name_unification, CONTAINS supports boolean expressions.

**v4 assignment table handling:** Uses a 4-alias system (`ad`=documents, `ar`=records, `ae`=assignees, `ao`=assignors). All assignment sub-tables join internally via `reel_frame`. Cross-table joins (e.g., to `patent_file_wrapper_v2`) use `application_number` through the `ad` (documents) table. Results are enriched with CTE-based "recent assignee" and "applicant name" columns.

#### 3. AI Assistant Router (`api/routers/ai_assistant.py`)
**Prefix:** `/ai` | **Tag:** AI Assistant

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/ai/ask` | Send natural-language question to Gemini, optionally execute generated SQL |

Includes auto-retry: if generated SQL fails, Gemini is asked to fix it (up to 2 retries).

#### 4. Forward Citations Router (`api/routers/citations.py`)
**Prefix:** `/api/forward-citations` | **Tag:** Forward Citations

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/forward-citations/{patent_number}` | Get all patents citing a given patent (with applicant names resolved via name_unification, examiner names) |
| GET | `/api/forward-citations/{patent_number}/summary` | Get citation statistics (by category, by year, date range, examiner breakdown, applicant breakdown) |

#### 5. Assignment Chain Router (`api/routers/assignments.py`)
**Prefix:** `/api/assignments` | **Tag:** Assignments

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/assignments/{patent_number}/chain` | Get the chain of assignments for a patent, sorted by execution date |

Uses a CTE-based approach: resolves patent_number -> application_number via patent_file_wrapper_v2 first, then finds matching reel_frames via pat_assign_documents, then joins records/assignors/assignees via reel_frame. Falls back to direct patent_number match for patents not in file wrapper.

#### 6. Entity Status Router (`api/routers/entity_status.py`)
**Prefix:** `/api/entity-status` | **Tag:** Entity Status

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/entity-status/summary` | Aggregate entity status distribution and conversion rates by year |
| GET | `/api/entity-status/{patent_number}` | Get entity status timeline for a single patent |
| POST | `/api/entity-status/conversions` | Find patents that changed entity status (e.g. small to large) |
| POST | `/api/entity-status/by-applicant` | Entity status breakdown for all patents of a given applicant |

Entity status is DERIVED from event codes (M1xxx=LARGE, M2xxx=SMALL, M3xxx=MICRO), not from the entity_status column.

#### 7. Prosecution Router (`api/routers/prosecution.py`)
**Prefix:** `/api/prosecution` | **Tag:** Prosecution Payments

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/prosecution/entities` | Phase 1: Find entities with N+ SMAL declarations (2016+) |
| POST | `/api/prosecution/applications` | Phase 2: List applications for a selected entity |
| POST | `/api/prosecution/documents` | Phase 3a: Query USPTO ODP API for payment-related documents |
| POST | `/api/prosecution/download` | Phase 3b: Download PDFs from USPTO to GCS |
| POST | `/api/prosecution/extract` | Phase 3c: Run Gemini Vision extraction on a PDF to get fee codes |

Uses Gemini 2.5 Flash for PDF vision extraction. PDFs are stored in `gs://uspto-bulk-staging/prosecution-invoices/`.

#### 8. ETL Log Router (`api/routers/etl_log.py`)
**Prefix:** `/api/etl-log` | **Tag:** ETL Log

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/etl-log` | Get recent pipeline run history (default limit 100) |
| GET | `/api/etl-log/summary` | Get latest successful run per source |

### Services

- **`api/services/bigquery_service.py`** — Singleton `BigQueryService` with lazy client init. Methods: `run_query`, `search_entity_names`, `associate_names`, `delete_association`, `get_addresses`, `search_by_address`, `expand_name_for_query`. All queries are parameterized.
- **`api/services/gemini_service.py`** — Uses Vertex AI SDK (`vertexai.generative_models`). Sends full BigQuery schema context to Gemini so it can generate correct SQL. Schema context includes all 4 normalized assignment tables with column listings, the `application_number` cross-table join strategy, and recommended CTE patterns for recent-assignee lookups. Maintains conversation history for multi-turn chat.

### Configuration
- **`api/config.py`** — `Settings` class reads env vars and provides computed properties for fully-qualified table names. Includes properties for all 12 tables.
- **`api/models/schemas.py`** — Pydantic models for all request/response types.

---

## Frontend

**Location:** `frontend/` (served at `/static/*`)
**Technology:** Vanilla JS ES modules, no build step, no framework

### Pages / Tabs (7 total)

| # | Tab | HTML Section | JS File | Purpose |
|---|-----|-------------|---------|---------|
| 1 | MDM Name Normalization | `#tab-mdm` | `mdm.js` | Search entity names, copy/paste representative associations, address lookup |
| 2 | Boolean Query Builder | `#tab-query-builder` | `query_builder.js` | Multi-condition queries across patent tables with name expansion |
| 3 | AI Assistant | `#tab-ai-assistant` | `ai_assistant.js` | Conversational AI for natural-language patent data questions |
| 4 | Forward Citations | `#tab-citations` | `citations.js` | Look up which patents cite a given patent, with stats, year chart, examiner/applicant breakdown lists |
| 5 | Entity Status | `#tab-entity-status` | `entity_status.js` | Entity status analytics, conversion patterns, applicant portfolios, boolean name search |
| 6 | Prosecution Fees | `#tab-prosecution` | `prosecution.js` | 3-phase prosecution fee investigation, document retrieval, fee code extraction |
| 7 | Update Log | `#tab-etl-log` | `etl_log.js` | Monitor automated pipeline run history and source status |

### Shared Code (`app.js`)
- Tab switching logic
- `apiGet`, `apiPost`, `apiDelete` — fetch wrappers with error handling
- `setLoading`, `showStatus` — UI state helpers
- `escHtml` — HTML escaping
- `buildGenericTable` — renders array of objects as static HTML table
- `buildInteractiveTable` — renders sortable table with column-visibility picker
- `enableTableSorting(tableEl)` — adds click-to-sort on `<th data-sort-key="N">` headers
- `stampOriginalOrder(tableEl)` — saves original row order for sort reset
- `enableAssignmentPopup(selector)` — attaches hover popup to patent number cells
- Assignment chain popup: movable (drag by header), resizable (drag corner), cached per patent number

### Cache-Busting Versions (current, in index.html)
- `styles.css?v=10`
- `app.js?v=13`
- `mdm.js?v=8`
- `query_builder.js?v=8`
- `ai_assistant.js?v=8`
- `citations.js?v=5`
- `entity_status.js?v=3`
- `prosecution.js?v=3`
- `etl_log.js?v=3`

### Styling
- **`frontend/css/styles.css`** (~1,200 lines) — Single CSS file
- CSS custom properties in `:root` for theming
- Responsive breakpoint at 640px
- Entity status badges: `.badge-small`, `.badge-micro`, `.badge-large`
- ETL status badges: `.etl-badge-ok`, `.etl-badge-fail`, `.etl-badge-skip`
- Suggestion list for boolean name search: `.es-suggestion-list`, `.es-suggestion-item`

---

## Key User Flows

### Flow 1: Entity Name Normalization (Tab 1: MDM)
1. User types a boolean search expression (e.g., `GOOG*`)
2. Frontend calls `POST /mdm/search` with the query
3. Backend parses the expression, runs a BigQuery query against `entity_names` LEFT JOIN `name_unification`
4. Results appear in a table showing raw names, frequencies, and current representative names
5. User clicks the copy icon on a name to set it as the "Representative"
6. User clicks the paste icon on other names to associate them with that representative
7. Frontend calls `POST /mdm/associate` to save the associations to `name_unification`
8. These associations automatically expand name searches in the Query Builder and AI tabs

### Flow 2: Boolean Query Builder (Tab 2: Query Builder)
1. User selects which tables to query (patent file wrapper, assignments, maintenance fees)
2. User adds conditions (field, operator, value) and selects AND/OR logic
3. Frontend calls `POST /query/execute` with the conditions
4. Backend builds parameterized SQL using 4-alias system for assignments (`ad`, `ar`, `ae`, `ao`)
5. Results display in a dynamic table with column picker and sortable headers
6. Hovering over patent numbers shows assignment chain popup

### Flow 3: AI Natural Language Query (Tab 3: AI Assistant)
1. User types a natural-language question
2. Frontend calls `POST /ai/ask` with the prompt and conversation history
3. Backend sends the prompt + full schema context to Gemini
4. Gemini generates SQL; backend executes it against BigQuery
5. If the SQL fails, backend asks Gemini to fix it (up to 2 retries)
6. Results appear in a table; generated SQL is in a collapsible accordion

### Flow 4: Forward Citation Lookup (Tab 4: Citations)
1. User enters a patent number
2. Frontend calls both the citation list and summary endpoints in parallel
3. Summary shows total citations, by-category KPIs, date range, year chart
4. Below the chart: two side-by-side scrollable lists — Citing Examiners and Citing Applicants
5. Table shows each citing patent with filing date, category badge, applicant/assignee, and examiner

### Flow 5: Entity Status Analytics (Tab 5: Entity Status)
1. **Single Patent Lookup**: Enter a patent number to see its full maintenance fee event timeline with derived entity status at each event
2. **Conversion Search**: Search for patents that changed entity status (e.g., SMALL -> LARGE) within a grant year range, optionally filtered by applicant name (supports boolean name search via Find button)
3. **Applicant Portfolio**: Enter an applicant name (or boolean search expression) to see their complete entity status breakdown across all patents

### Flow 6: Prosecution Fee Investigation (Tab 6: Prosecution Fees)
1. **Phase 1 — Entity Discovery**: Set minimum SMAL declaration threshold, click "Discover Entities" to find entities with significant prosecution activity
2. **Phase 2 — Application Drill-down**: Click an entity to select it, adjust date range, click "Load Applications" to see all applications with SMAL declarations. Ctrl+Click for multi-select, Shift+Click for range select.
3. **Phase 3 — Invoice Retrieval & Extraction**: Click "Retrieve Invoices" to query USPTO ODP API for payment documents, then "Download & Extract" to download PDFs and run Gemini Vision extraction for entity status, fee codes, and payment amounts.

### Flow 7: Pipeline Monitoring (Tab 7: Update Log)
1. Tab loads lazily when activated
2. Summary cards show each source's schedule and last successful run
3. Table shows run history with status badges, duration, file/row counts

---

## Database

**Project:** `uspto-data-app` | **Dataset:** `uspto_data` | **Location:** `us-west1`

### Tables

| Table | Rows | Size | Clustering | Purpose |
|-------|------|------|-----------|---------|
| `pfw_transactions` | 497,486,866 | 32.9 GB | application_number | Every transaction event in a patent application's prosecution history |
| `forward_citations` | 210,059,818 | 16.3 GB | cited_patent_number, citing_patent_number | Which granted patents cite which earlier patents |
| `maintenance_fee_events_v2` | 26,527,580 | 2.1 GB | patent_number | Maintenance fee payments and lapses |
| `pat_assign_assignors` | 23,637,751 | 0.71 GB | reel_frame | One row per assignor per assignment transaction |
| `pat_assign_documents` | 16,978,703 | 1.80 GB | reel_frame, application_number | One row per patent property per assignment |
| `patent_file_wrapper_v2` | 12,733,017 | 3.9 GB | application_number, patent_number | One row per patent application — filing date, grant date, entity status, applicant, inventor |
| `pfw_continuity` | 12,253,958 | 1.6 GB | application_number | Parent/child relationships between patent applications |
| `pat_assign_assignees` | 9,430,399 | 1.36 GB | reel_frame, assignee_name | One row per assignee per assignment (with address fields) |
| `pat_assign_records` | 9,071,498 | 1.80 GB | reel_frame | One row per assignment transaction (conveyance, recorded date). Partitioned by `recorded_date` (MONTH) |
| `entity_names` | 7,684,636 | 0.24 GB | entity_name | Aggregated unique entity names with frequency counts |
| `name_unification` | 64 | <0.01 GB | — | User-curated MDM associations. **DO NOT MODIFY PROGRAMMATICALLY** |
| `etl_log` | ~10 | <0.01 GB | source, started_at | Pipeline run tracking |

**Total:** ~828 million rows, ~62.7 GB

### Key Relationships
- `pat_assign_records`, `pat_assign_assignors`, `pat_assign_assignees`, `pat_assign_documents` all linked by `reel_frame`
- `pat_assign_documents.application_number` links to `patent_file_wrapper_v2.application_number` (universal cross-table join key)
- `patent_file_wrapper_v2.application_number` links to `pfw_transactions.application_number` and `pfw_continuity.application_number`
- `patent_file_wrapper_v2.patent_number` links to `forward_citations.cited_patent_number` and `maintenance_fee_events_v2.patent_number`
- `entity_names.entity_name` links to `name_unification.associated_name` for MDM lookups
- `name_unification.representative_name` is the canonical name that associated names map to

### Data Sources and Loading
- **patent_file_wrapper_v2, pfw_transactions, pfw_continuity** — Parsed from PTFWPRE JSON ZIPs by `etl/parse_pfw.py`
- **forward_citations** — Parsed from PTBLXML XML ZIPs by `etl/parse_ptblxml.py`
- **pat_assign_records, pat_assign_assignors, pat_assign_assignees, pat_assign_documents** — Parsed from PASYR/PASDL XML ZIPs by `etl/parse_assignments_xml_v4.py` (produces 4 JSONL.gz files per shard)
- **maintenance_fee_events_v2** — Parsed from PTMNFEE2 fixed-width text by `etl/parse_maintenance_fees_v2.py`
- **entity_names** — Rebuilt by SQL aggregation from `patent_file_wrapper_v2`, `pat_assign_assignees`, and `pat_assign_assignors`
- **name_unification** — User-curated (64 rows); modified only through the MDM UI
- **etl_log** — Written by `update_pipeline.py` after each pipeline run

---

## External Services and Integrations

| Service | Purpose | Configuration |
|---------|---------|--------------|
| **Google Cloud Run** | Hosts the API service and 4 ETL jobs | Project: `uspto-data-app`, Region: `us-central1` |
| **Google BigQuery** | Primary database | Project: `uspto-data-app`, Dataset: `uspto_data`, Location: `us-west1` |
| **Google Vertex AI** | Gemini model for AI assistant + PDF vision extraction | Project: `uspto-data-app`, Region: `us-central1`, Model: `gemini-2.5-flash` |
| **Google Cloud Storage** | Staging area for JSONL files + prosecution invoice PDFs | Bucket: `gs://uspto-bulk-staging/` |
| **Google Cloud Scheduler** | Triggers ETL Cloud Run Jobs on schedule | Project: `uspto-data-app`, Region: `us-central1` |
| **Google Cloud Build** | Builds the ETL Docker container | Project: `uspto-data-app` |
| **USPTO ODP API** | Source of all patent bulk data + document retrieval | Base URL: `https://api.uspto.gov`, requires `X-API-KEY` header |
| **GitHub Actions** | CI/CD pipeline for API service deployment | Repo: InnovationAccess/SmallEntityPayments |

---

## Authentication and Access

- **No user authentication.** The platform is publicly accessible (Cloud Run `--allow-unauthenticated`).
- **No roles or permissions.** All users can read and write MDM associations.
- **USPTO ODP API** requires an API key passed as `X-API-KEY` header.
- **GCP services** authenticate via Cloud Run's service account (default compute service account).
- **GitHub Actions** authenticates to GCP via Workload Identity Federation (no service account keys stored in GitHub).

---

## Testing

**Test files exist but are not run in CI:**
- `tests/test_api_endpoints.py` — FastAPI endpoint unit tests
- `tests/test_conveyance_classifier.py` — Conveyance classification tests
- `tests/test_parse_helpers.py` — Parser utility tests
- `tests/test_parse_v4.py` — v4 assignment parser tests
- `tests/test_patent_number.py` — Patent number normalization tests

**No automated test pipeline.** The GitHub Actions workflow deploys on push to main but does not run any tests. Testing has been done manually via the live UI and `curl` commands.

---

## Deployment

### API Service (Cloud Run)
```bash
gcloud run deploy uspto-api \
  --source=. \
  --project=uspto-data-app \
  --region=us-central1 \
  --allow-unauthenticated
```
This builds using `Dockerfile` (python:3.11-slim, uvicorn on port 8080) and deploys in one step.

**Also deployable via GitHub Actions:** Push to `main` triggers `.github/workflows/deploy.yml` which builds, pushes to Artifact Registry, and deploys to Cloud Run.

### ETL Container (Cloud Build)
```bash
gcloud builds submit \
  --config=cloudbuild-etl.yaml \
  --project=uspto-data-app
```
Builds `Dockerfile.etl` and pushes to:
`us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest`

### Cloud Run Jobs
Each job runs the ETL container with a different source argument:
- `uspto-update-ptblxml` — `python etl/update_pipeline.py ptblxml`
- `uspto-update-pasdl` — `python etl/update_pipeline.py pasdl` (uses v4 parser, loads 4 tables)
- `uspto-update-ptmnfee2` — `python etl/update_pipeline.py ptmnfee2`
- `uspto-update-ptfwpre` — `python etl/update_pipeline.py ptfwpre`

### Cloud Scheduler
| Job | Schedule | Timezone |
|-----|----------|----------|
| `uspto-schedule-ptblxml` | `0 2 * * 0` (Sun 2am) | America/Los_Angeles |
| `uspto-schedule-pasdl` | `0 3 * * 1` (Mon 3am) | America/Los_Angeles |
| `uspto-schedule-ptmnfee2` | `0 4 1 * *` (1st 4am) | America/Los_Angeles |
| `uspto-schedule-ptfwpre` | `0 1 15 * *` (15th 1am) | America/Los_Angeles |

### Environment Variables

**API service (Cloud Run):**
- `GCP_PROJECT_ID=uspto-data-app`
- `BIGQUERY_DATASET=uspto_data`
- `GEMINI_API_KEY` — Vertex AI API key (set in Cloud Run env)

**ETL jobs (Cloud Run Jobs):**
- `USPTO_API_KEY` — USPTO ODP API key (set in Cloud Run Job env)
- `GCP_PROJECT_ID=uspto-data-app`
- `BIGQUERY_DATASET=uspto_data`
- `GCS_BUCKET=uspto-bulk-staging`

**GitHub Actions secrets:**
- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `API_SECRET`
- `GEMINI_API_KEY`

---

## File Structure

```
SmallEntityPayments/
├── api/                          # FastAPI backend
│   ├── __init__.py
│   ├── config.py                 # Settings class (env vars, 12 table properties)
│   ├── main.py                   # FastAPI app entry point (8 routers)
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py            # Pydantic request/response models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── ai_assistant.py       # POST /ai/ask
│   │   ├── assignments.py        # GET /api/assignments/{patent}/chain (CTE-based resolution)
│   │   ├── citations.py          # GET /api/forward-citations/* (with name resolution)
│   │   ├── entity_status.py      # Entity status analytics (derives from event codes)
│   │   ├── etl_log.py            # GET /api/etl-log, /api/etl-log/summary
│   │   ├── mdm.py                # POST/DELETE /mdm/*
│   │   ├── prosecution.py        # 3-phase prosecution fee investigation + Gemini PDF extraction
│   │   └── query.py              # POST /query/execute (4-alias assignment system)
│   └── services/
│       ├── __init__.py
│       ├── bigquery_service.py   # BigQuery client singleton
│       └── gemini_service.py     # Vertex AI Gemini integration (v4 schema context)
├── database/
│   ├── setup.sql                 # Original v1 DDL (obsolete)
│   ├── setup_v2.sql              # v2 DDL for non-assignment tables
│   ├── setup_v3.sql              # v3 DDL (superseded by v4)
│   └── setup_v4.sql              # v4 DDL — 4 normalized assignment tables
├── etl/                          # ETL scripts
│   ├── update_pipeline.py        # Master orchestrator (Cloud Run Job entrypoint, uses v4 parser)
│   ├── download_ptblxml.py       # PTBLXML downloader (citations)
│   ├── download_pasdl.py         # PASDL downloader (daily assignments)
│   ├── download_ptmnfee2.py      # PTMNFEE2 downloader (maintenance fees)
│   ├── parse_ptblxml.py          # Citation XML parser
│   ├── parse_assignments_xml_v4.py  # v4 assignment XML parser (4-file normalized output) — CURRENT
│   ├── parse_assignments_xml_v3.py  # v3 assignment parser (flat output, superseded)
│   ├── parse_assignments_xml_v2.py  # v2 assignment parser (superseded)
│   ├── parse_assignments_xml.py  # v1 assignment parser (obsolete)
│   ├── parse_maintenance_fees_v2.py # Maintenance fee fixed-width parser — CURRENT
│   ├── parse_maintenance_fees.py # v1 maintenance fee parser (obsolete)
│   ├── parse_pfw.py              # Patent file wrapper JSON parser (3 output tables)
│   ├── parse_file_wrapper.py     # Earlier PFW parser (superseded)
│   ├── reload_assignments_v4.py  # Standalone v4 full reload from PASYR archives
│   ├── reload_assignments_v3.py  # Standalone v3 full reload (superseded)
│   ├── populate_entity_names_v2.py  # Standalone entity_names rebuilder
│   ├── fix_bad_dates.py          # Fixes 0000-01-01 dates in PASYR files
│   └── download_and_parse_pasyr.sh  # Shell script for PASYR processing
├── frontend/                     # Vanilla JS SPA
│   ├── css/
│   │   └── styles.css            # All styles (~1,200 lines)
│   ├── index.html                # Single-page app (7 tabs, 676 lines)
│   └── js/
│       ├── app.js                # Shared utilities, tab switching, API helpers, table sorting, assignment popup
│       ├── mdm.js                # Tab 1: MDM Name Normalization
│       ├── query_builder.js      # Tab 2: Boolean Query Builder
│       ├── ai_assistant.js       # Tab 3: AI Assistant
│       ├── citations.js          # Tab 4: Forward Citations
│       ├── entity_status.js      # Tab 5: Entity Status (with boolean name search)
│       ├── prosecution.js        # Tab 6: Prosecution Fee Investigation
│       └── etl_log.js            # Tab 7: Update Log
├── utils/
│   ├── __init__.py
│   ├── patent_number.py          # Patent number normalization utility
│   └── conveyance_classifier.py  # Classifies raw conveyance text into categories
├── tools/
│   └── extract_fee_codes.py      # Fee code extraction utility
├── tests/
│   ├── test_api_endpoints.py     # FastAPI endpoint tests
│   ├── test_conveyance_classifier.py  # Conveyance classification tests
│   ├── test_parse_helpers.py     # Parser utility tests
│   ├── test_parse_v4.py          # v4 assignment parser tests
│   └── test_patent_number.py     # Patent number normalization tests
├── .github/workflows/
│   └── deploy.yml                # GitHub Actions CI/CD (Cloud Run deployment)
├── Dockerfile                    # API service container (python:3.11-slim)
├── Dockerfile.etl                # ETL job container (python:3.11-slim + gcloud CLI)
├── cloudbuild-etl.yaml           # Cloud Build config for ETL container
├── requirements.txt              # Python dependencies (8 packages)
├── .env.example                  # Example environment variables
├── CLAUDE.md                     # Project instructions for AI assistants
├── HANDOFF314.md                 # This file — current handoff snapshot
├── HANDOFF313.md                 # Prior handoff (2026-03-12, session 1)
├── HANDOFF312.md                 # Prior handoff (2026-03-12, pre-v4)
├── HANDOFF311.md                 # Prior handoff (2026-03-09)
├── README.md                     # Project README
└── FRONTEND_SPECIFICATION.md     # Frontend design spec
```

---

## Configuration Files

| File | Purpose | Critical Settings |
|------|---------|-------------------|
| `Dockerfile` | API service container | python:3.11-slim, uvicorn on port 8080 |
| `Dockerfile.etl` | ETL job container | python:3.11-slim + google-cloud-cli (for gsutil/bq) |
| `cloudbuild-etl.yaml` | Cloud Build config for ETL container | Builds `Dockerfile.etl`, pushes to Artifact Registry |
| `requirements.txt` | Python dependencies | fastapi, uvicorn, google-cloud-bigquery, google-cloud-aiplatform, ijson, requests, pydantic, python-dotenv |
| `.env.example` | Environment variable template | GCP_PROJECT_ID, BIGQUERY_DATASET, GEMINI_API_KEY |
| `.github/workflows/deploy.yml` | CI/CD | Deploys to Cloud Run on push to main (Workload Identity Federation) |
| `api/config.py` | Runtime config | Reads env vars; provides 12 table property methods |

---

## Environment and Dependencies

### Python Dependencies (`requirements.txt`)
```
fastapi==0.109.2
uvicorn==0.27.1
google-cloud-bigquery>=3.17.0
google-cloud-aiplatform>=1.38.0
pydantic>=2.0.0
python-dotenv>=1.0.0
ijson>=3.2.0
requests>=2.31.0
```

Note: `prosecution.py` also uses `httpx` and `google-cloud-storage` which are installed as transitive dependencies of the above packages.

### Development Environment
- Chromebooks (Pixelbook i7 + secondary) with Linux terminal
- Dell Linux server for large-scale data processing
- Python 3.11+
- Google Cloud SDK (`gcloud`, `gsutil`, `bq` CLI tools)

---

## Accounts and Services

| Service | Account/ID | Notes |
|---------|-----------|-------|
| GCP Project | `uspto-data-app` | Hosts all cloud resources |
| BigQuery Dataset | `uspto_data` in `us-west1` | ALL bq commands need `--location=us-west1` |
| Cloud Run Service | `uspto-api` in `us-central1` | API + frontend |
| Cloud Run Jobs | 4 jobs in `us-central1` | ETL pipelines |
| GCS Bucket | `gs://uspto-bulk-staging/` | Data staging + prosecution invoices |
| GitHub Repo | `InnovationAccess/SmallEntityPayments` | Source code |
| GitHub User | `Synpathub` | Owner |
| GitHub Org | `InnovationAccess` | Organization |
| Artifact Registry | `us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/` | ETL container images |
| USPTO API | Key: see prosecution.py | Patent data access |

---

## Workarounds and Gotchas

1. **BigQuery location is `us-west1`** — All `bq` CLI commands MUST include `--location=us-west1` or they silently fail. The Cloud Run service is in `us-central1` but BigQuery is in `us-west1`.

2. **No parallel uploads** — `gsutil -m cp` (parallel upload) has repeatedly caused failures. Always use `gsutil cp` (sequential, one file at a time).

3. **No wildcard BQ loads** — Loading `gs://bucket/*.jsonl.gz` has caused problems. Load files individually.

4. **Concatenating .gz files creates multi-stream gzip** — BigQuery cannot read multi-stream gzip. Never concatenate `.gz` files.

5. **Date value `0000-01-01`** — Some PASYR records have this date, which causes BigQuery load failures. The parser validates year range 1700-2100 and nullifies out-of-range dates.

6. **PTBLXML annual files (2002-2005) are too large** — They exceed memory for in-memory XML parsing. Use weekly files only.

7. **`name_unification` table (64 rows) is user-curated** — Never drop, truncate, or bulk-modify this table programmatically. Changes should only come through the MDM UI.

8. **Cloud Build `--tag` and `--config` are mutually exclusive** — Must use a config YAML file for the ETL build.

9. **`cloudbuild-etl.yaml` is now in the repo root** — Use `--config=cloudbuild-etl.yaml` (not `/tmp/cloudbuild-etl.yaml`).

10. **Router prefix inconsistency** — MDM/Query/AI use short prefixes (`/mdm`, `/query`, `/ai`) while newer routers use `/api/...`. Cosmetic only.

11. **Assignment chain popup uses client-side caching** — The `_chainCache` object in `app.js` caches API responses per patent number. Assignment data won't refresh until page reload.

12. **Cache-busting via query params** — Frontend scripts use `?v=N` suffixes. Bump the version number when modifying any JS or CSS file.

13. **NEVER search a string across multiple fields** — Patent number queries must resolve to `application_number` first. The pattern `WHERE app_num = @id OR patent_num = @id` causes collisions (e.g., patent 11172434 vs. application 11/172,434). This was a bug that was fixed in this session.

14. **Entity status derived from event codes, not the column** — The `entity_status` column in `maintenance_fee_events_v2` is unreliable. Always use the event code mapping: M1xxx/F17xx=LARGE, M2xxx/F27xx=SMALL, M3xxx=MICRO.

15. **PASDL v4 produces 4 files per shard** — The ETL pipeline uploads and loads each of the 4 output files individually into their respective BigQuery tables.

---

## Decisions That Were Rejected

1. **Google's `patents-public-data` BigQuery dataset** — Rejected as a data source. Only official USPTO bulk data allowed.
2. **Nested STRUCT/ARRAY schemas in BigQuery** — Abandoned because it complicated queries and the AI assistant couldn't generate correct SQL.
3. **Cloud Functions for ETL** — Rejected in favor of Cloud Run Jobs (longer timeouts, existing scripts).
4. **Parallel ETL execution** — Rejected for safety; sequential execution preferred.
5. **`etl/load_file_wrapper.py`** — Deprecated and deleted. Use `parse_pfw.py` with PTFWPRE data.
6. **In-memory XML parsing for large files** — All parsers use streaming approaches.
7. **Flat denormalized assignment table (v2/v3)** — Replaced with 4 normalized tables for 67% storage reduction.
8. **Cross-field search in assignment chain** — `WHERE app_num = @id OR patent_num = @id` caused collisions. Now resolves via patent_file_wrapper_v2 first.

---

## Known Limitations and Constraints

1. **No authentication** — Anyone with the URL can access the platform and modify MDM associations.
2. **No automated tests in CI** — Test files exist but the GitHub Actions workflow doesn't run them.
3. **Gemini SQL generation is imperfect** — Sometimes generates incorrect SQL for complex joins.
4. **PTFWPRE and PTMNFEE2 updates are full table replacements** — Brief data unavailability during update windows.
5. **Cloud Run Job disk space** — PTFWPRE ZIPs are 2-6 GB and may exceed default ephemeral storage.
6. **`database/setup_v2.sql` references `ingestion_log`** — But the actual table is named `etl_log`.

---

## Open Issues and Bugs

- None known at this time. All previously identified issues have been resolved.

---

## Work In Progress

- None. All tasks from this session are complete and deployed.

---

## Git State

- **Current branch:** `main`
- **Latest commit:** `efd4c6c` — "Add boolean name search to Entity Status applicant fields"
- **Open PRs:** None
- **Uncommitted work:** None (clean tree)
- **Branching convention:** Work is done in worktree branches (`claude/*`), merged to `main` via fast-forward

### Recent Commits (newest first)
```
efd4c6c Add boolean name search to Entity Status applicant fields
91b37dd Fix assignment chain returning irrelevant patents due to number collision
217877b Add help button fix, draggable assignment popup, and column pickers to all tables
9bf4268 Fix help toggle: use event delegation for reliable click handling
2eb4260 Add help (?) button with usage instructions to all 7 tabs
2e43119 Fix PDF download: follow redirects from USPTO API
8800331 Add Phase 3 prosecution invoice retrieval and AI extraction
e241c2e Add Prosecution Fee Investigation tab (Phase 1 + 2) and PDF extraction prototype
4d18b7b Add Entity Status Analytics tab (derives status from event codes)
7ccb1f4 Add tests, HANDOFF313, and check in cloudbuild-etl.yaml
e757c3c Normalize patent assignments into 4 tables (v4)
```

### Currently Deployed
- **API service:** revision `uspto-api-00081-tgx` (boolean name search build)
- **ETL container:** `us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest`

---

## Next Steps

No outstanding engineering tasks. Potential future work (not prioritized by the user):

1. **Add authentication** — Protect the platform with user login if it will be shared publicly
2. **Add automated tests to CI** — Run existing test files in GitHub Actions before deployment
3. **Citation network analysis** — Build citation graph features on top of the forward_citations data
4. **MDM workflow improvements** — Better UI for bulk name association workflows
5. **Prosecution fee trend analysis** — Aggregate extracted fee data to identify patterns across entities

---

## Commands and Context

### Quick Start Commands
```bash
# Pull latest code
cd /home/uzi/projects/SmallEntityPayments
git pull origin main

# Deploy API to Cloud Run
gcloud run deploy uspto-api --source=. --project=uspto-data-app --region=us-central1 --allow-unauthenticated

# Rebuild and deploy ETL container
gcloud builds submit --config=cloudbuild-etl.yaml --project=uspto-data-app

# Run an ETL job manually
gcloud run jobs execute uspto-update-ptblxml --project=uspto-data-app --region=us-central1 --wait

# Check BigQuery table counts
bq query --location=us-west1 --project_id=uspto-data-app --nouse_legacy_sql \
  "SELECT table_id, row_count FROM \`uspto_data.__TABLES__\` ORDER BY row_count DESC"

# Test API endpoints
curl -s https://uspto-api-1094570457455.us-central1.run.app/health
curl -s https://uspto-api-1094570457455.us-central1.run.app/api/etl-log?limit=5 | python3 -m json.tool
curl -s https://uspto-api-1094570457455.us-central1.run.app/api/assignments/7654321/chain | python3 -m json.tool
```

### Key URLs
- **Live app:** https://uspto-api-1094570457455.us-central1.run.app
- **GitHub repo:** https://github.com/InnovationAccess/SmallEntityPayments
- **GCP Console:** https://console.cloud.google.com/run?project=uspto-data-app
- **BigQuery Console:** https://console.cloud.google.com/bigquery?project=uspto-data-app

### Key Identifiers
- GCP Project: `uspto-data-app`
- BigQuery Dataset: `uspto_data` (location: `us-west1`)
- Cloud Run Service: `uspto-api` (region: `us-central1`)
- GCS Bucket: `gs://uspto-bulk-staging/`
- ETL Image: `us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest`
- GitHub Org: `InnovationAccess`
