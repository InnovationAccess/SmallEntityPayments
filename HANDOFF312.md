# HANDOFF — SmallEntityPayments Project Snapshot (2026-03-12)

## System Overview

**SmallEntityPayments** is a web-based patent data analytics platform. It ingests official USPTO bulk data products (patent file wrappers, assignments, maintenance fees, forward citations) into Google BigQuery, then provides a web UI for patent attorneys and business strategists to search, cross-reference, and analyze that data.

**Who it's for:** Patent attorneys, IP strategists, and innovation analysts who need to track patent ownership changes, entity status (small vs. large), maintenance fee history, and citation patterns across millions of US patents.

**What it does:**
- Normalizes messy USPTO entity names (MDM — Master Data Management)
- Runs boolean queries across patent, assignment, and maintenance fee data
- Provides a conversational AI assistant (Gemini) for natural-language patent data queries
- Looks up forward citations for any patent with summary statistics, breakdown by examiner/applicant, and year chart
- Shows assignment chain popup on hover over any patent number in results
- Monitors automated data pipeline updates from USPTO

**Repository:** https://github.com/InnovationAccess/SmallEntityPayments
**Live URL:** https://uspto-api-1094570457455.us-central1.run.app

---

## What Changed Since Last Handoff (HANDOFF311.md → HANDOFF312.md)

This section covers 7 commits from `3c4b608` through `f34bc52`, all deployed to Cloud Run.

### 1. Sticky Table Headers (`3c4b608`)
- All data tables now have sticky headers — column headings remain visible while scrolling
- CSS: `.data-table th { position: sticky; top: 0; z-index: 10; }`
- `.table-scroll-wrap` class provides `max-height: 70vh; overflow-y: auto`
- Applied to all static tables in HTML and dynamic tables built by `buildGenericTable` and `buildInteractiveTable`

### 2. Citation Breakdown Lists (`f2c4560`, `a0a6686`)
- Replaced unique examiner/applicant count KPI cards with detailed name-and-count lists
- **Citing Examiners** list shows each examiner name and how many times they cited the patent (filtered to `citation_category = 'examiner'`)
- **Citing Applicants** list shows each applicant name (resolved via `name_unification`) and count (filtered to `citation_category = 'applicant'`)
- Lists use `COALESCE(..., 'Unknown')` so totals always match the KPI cards exactly
- Displayed in two side-by-side scrollable columns below the year chart

### 3. Sortable Table Headers (`f3563ee`)
- All tables across all tabs now support click-to-sort on column headers
- New utility functions in `app.js`: `enableTableSorting(tableEl)` and `stampOriginalOrder(tableEl)`
- Works via `data-sort-key="<colIndex>"` attributes on `<th>` elements
- Supports numeric-aware sorting (numbers sort by value, strings by locale)
- Three-state toggle: ascending → descending → original order
- Applied to: citations table, ETL log table, address results modal table
- MDM main table retains its own custom sorting mechanism
- Dynamic tables from Query Builder and AI Assistant use `buildInteractiveTable` which already had sorting

### 4. Assignment Chain Popup (`ab22a0e`, `ea9715b`, `f34bc52`)
- Hovering over any patent number in any results table shows a popup with the patent's full assignment chain
- **New backend endpoint**: `GET /api/assignments/{patent_number}/chain` (`api/routers/assignments.py`)
  - Queries `patent_assignments_v2` table
  - Deduplicates by `(reel_frame, assignor_name, assignee_name)` using `ROW_NUMBER()`
  - Returns assignments sorted by execution date ascending
- **New router registered** in `api/main.py`: `assignments.router`
- **Frontend popup** in `app.js`:
  - `enableAssignmentPopup(selector)` — call after populating any table with patent number cells
  - Shows: Date, Assignor, Type (conveyance), Assignee in a table inside the popup
  - 300ms show delay, 200ms hide delay to prevent flicker
  - Client-side cache (`_chainCache`) avoids redundant API calls
  - Positioned to the right of the hovered patent number, aligned to its top
  - Viewport-aware: flips left if would overflow right edge, shifts up if would overflow bottom
- Wired to: `citations.js`, `query_builder.js`, `ai_assistant.js`

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
| **Entity Status** | Whether a patent applicant qualifies as "small entity" (reduced fees) or "large entity" |
| **Forward Citations** | Patents that cite a given patent — indicates the patent's influence on later innovations |
| **Conveyance** | The type of ownership transfer in a patent assignment (e.g., "ASSIGNMENT OF ASSIGNORS INTEREST") |
| **Reel/Frame** | The physical recording location of a patent assignment at the USPTO |
| **CPC Codes** | Cooperative Patent Classification — hierarchical codes classifying a patent's technology area |
| **Kind Code** | A letter suffix on a patent number indicating the document type (B1=granted patent without prior pub, B2=with prior pub, A1=application pub) |

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
    |   |-- /api/assignments/* -> Assignment chain API
    |   |-- /api/etl-log/* -> Pipeline monitoring API
    |   |-- /health -> Health check
    |
    v
Google BigQuery (us-west1, dataset: uspto_data)
    |-- 9 tables, ~892M rows, ~93 GB
    |
Google Vertex AI (us-central1)
    |-- Gemini model for natural language -> SQL
    |
Google Cloud Storage (gs://uspto-bulk-staging/)
    |-- Staging area for JSONL files before BQ load

Cloud Run Jobs (4 jobs, us-central1)
    |-- Triggered by Cloud Scheduler
    |-- Run update_pipeline.py for each data source
    |-- Download from USPTO ODP API -> parse -> GCS -> BigQuery
```

**Data flow:** USPTO ODP API -> Cloud Run Job downloads ZIP -> ETL parser creates JSONL.gz -> gsutil uploads to GCS -> bq load into BigQuery -> API serves to frontend.

**Key architectural decisions:**
- All BigQuery tables use flat/denormalized schemas (no STRUCT or ARRAY columns, except `cpc_codes ARRAY<STRING>`)
- Frontend is vanilla JS with no build toolchain — served as static files from the same Cloud Run container
- ETL jobs use the `bq` and `gsutil` CLI tools (not Python SDK) for BigQuery loads and GCS uploads
- Each ETL source runs in its own Cloud Run Job with its own scheduler

---

## Backend

### API Entry Point
**File:** `api/main.py`

Registers 6 routers, serves frontend static files, and provides a health check.

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

**Detail endpoint** joins `forward_citations` → `patent_file_wrapper_v2` → `name_unification` to resolve applicant names to representative names.

**Summary endpoint** returns:
- `total_citations`, `by_category` (examiner/applicant counts), `by_year` (year→count map)
- `by_examiner`: array of `{name, count}` filtered to `citation_category='examiner'`
- `by_applicant`: array of `{name, count}` filtered to `citation_category='applicant'`, resolved via name_unification

#### 5. Assignment Chain Router (`api/routers/assignments.py`) — **NEW**
**Prefix:** `/api/assignments` | **Tag:** Assignments

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/assignments/{patent_number}/chain` | Get the chain of assignments for a patent, sorted by execution date |

Deduplicates by `(reel_frame, assignor_name, assignee_name)` using `ROW_NUMBER()`. Returns `{patent_number, assignments: [{execution_date, assignor, conveyance, assignee, reel_frame}]}`.

#### 6. ETL Log Router (`api/routers/etl_log.py`)
**Prefix:** `/api/etl-log` | **Tag:** ETL Log

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/etl-log` | Get recent pipeline run history (default limit 100) |
| GET | `/api/etl-log/summary` | Get latest successful run per source |

### Services

- **`api/services/bigquery_service.py`** — Singleton `BigQueryService` with lazy client init. Methods: `run_query`, `search_entity_names`, `associate_names`, `delete_association`, `get_addresses`, `search_by_address`, `expand_name_for_query`. All queries are parameterized.
- **`api/services/gemini_service.py`** — Uses Vertex AI SDK (`vertexai.generative_models`). Sends full BigQuery schema context to Gemini so it can generate correct SQL. Maintains conversation history for multi-turn chat.

### Configuration
- **`api/config.py`** — `Settings` class reads env vars and provides computed properties for fully-qualified table names.
- **`api/models/schemas.py`** — Pydantic models for all request/response types.

---

## Frontend

**Location:** `frontend/` (served at `/static/*`)
**Technology:** Vanilla JS ES modules, no build step, no framework

### Pages / Tabs

| Tab | HTML Section | JS File | Purpose |
|-----|-------------|---------|---------|
| MDM Name Normalization | `#tab-mdm` | `mdm.js` | Search entity names, copy/paste representative associations, address lookup |
| Boolean Query Builder | `#tab-query-builder` | `query_builder.js` | Multi-condition queries across patent tables with name expansion |
| AI Assistant | `#tab-ai-assistant` | `ai_assistant.js` | Conversational AI for natural-language patent data questions |
| Forward Citations | `#tab-citations` | `citations.js` | Look up which patents cite a given patent, with stats, year chart, examiner/applicant breakdown lists |
| Update Log | `#tab-etl-log` | `etl_log.js` | Monitor automated pipeline run history and source status |

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
- Assignment chain popup system: `showChainPopup`, `hideChainPopup`, `getOrCreatePopup` (internal)

### Key UI Components
- Tab navigation in the header (`.tab-nav` with `.tab-btn` buttons)
- Data tables (`.data-table`) with sticky headers and sortable columns
- Sticky headers: `.data-table th { position: sticky; top: 0; z-index: 10; }`
- Table containers: `.table-scroll-wrap { max-height: 70vh; overflow-y: auto; }`
- Status badges for entity types (`.badge-small`, `.badge-micro`, `.badge-large`)
- ETL status badges (`.etl-badge-ok`, `.etl-badge-fail`, `.etl-badge-skip`)
- Citation year chart (`.cite-year-chart` with bar visualization)
- Citation breakdown lists (`.cite-breakdown-row` with `.cite-breakdown-col` side-by-side)
- Assignment chain popup (`.chain-popup` with `.chain-table`)
- Patent number hover links (`.patent-link` — blue dotted underline)
- Modal dialogs for address lookup and search results
- Code block accordion for showing generated SQL

### Styling
- **`frontend/css/styles.css`** — Single CSS file, ~831 lines
- CSS custom properties in `:root` for theming
- Responsive breakpoint at 640px

### Cache-Busting Versions (in index.html)
- `styles.css?v=4`
- `app.js?v=11`
- `mdm.js?v=8`
- `query_builder.js?v=8`
- `ai_assistant.js?v=8`
- `citations.js?v=4`
- `etl_log.js?v=2`

---

## Key User Flows

### Flow 1: Entity Name Normalization (MDM Tab)
1. User types a boolean search expression (e.g., `GOOGLE*+INC`)
2. Frontend calls `POST /mdm/search` with the query
3. Backend parses the expression, runs a BigQuery query against `entity_names` LEFT JOIN `name_unification`
4. Results appear in a table showing raw names, frequencies, and current representative names
5. User clicks the copy icon on a name to set it as the "Representative"
6. User clicks the paste icon on other names to associate them with that representative
7. Frontend calls `POST /mdm/associate` to save the associations to `name_unification`
8. These associations automatically expand name searches in the Query Builder and AI tabs

### Flow 2: Boolean Query Builder (Query Builder Tab)
1. User selects which tables to query (patent file wrapper, assignments, maintenance fees)
2. User adds conditions (field, operator, value) and selects AND/OR logic
3. Frontend calls `POST /query/execute` with the conditions
4. Backend builds parameterized SQL, expanding entity names via `name_unification`
5. Results display in a dynamic table with column picker and sortable headers
6. Hovering over patent numbers shows assignment chain popup

### Flow 3: AI Natural Language Query (AI Tab)
1. User types a natural-language question (e.g., "Show me all patents assigned from Apple to Google in 2023")
2. Frontend calls `POST /ai/ask` with the prompt and conversation history
3. Backend sends the prompt + full schema context to Gemini
4. Gemini generates SQL; backend executes it against BigQuery
5. If the SQL fails, backend asks Gemini to fix it (up to 2 retries)
6. Results appear in a table below the chat; generated SQL is in a collapsible accordion
7. Hovering over patent numbers shows assignment chain popup

### Flow 4: Forward Citation Lookup (Citations Tab)
1. User enters a patent number (e.g., `7654321` or `US7,654,321`)
2. Frontend calls both the citation list and summary endpoints in parallel
3. Summary shows: total citations, by-category KPIs (examiner/applicant), date range, year chart
4. Below the chart: two side-by-side scrollable lists — Citing Examiners (name + count) and Citing Applicants (name + count, resolved via name_unification)
5. Table shows each citing patent with filing date, category badge, applicant/assignee, and examiner
6. Applicant names in the table are resolved to representative names via name_unification
7. Hovering over citing patent numbers shows assignment chain popup
8. All columns are sortable by clicking the header

### Flow 5: Pipeline Monitoring (Update Log Tab)
1. Tab loads lazily (MutationObserver detects when tab becomes active)
2. Frontend calls `/api/etl-log` and `/api/etl-log/summary` in parallel
3. Summary cards show each source's schedule and last successful run
4. Table shows run history with status badges, duration, file/row counts, and error tooltips
5. Table headers are sortable

---

## Database

**Project:** `uspto-data-app` | **Dataset:** `uspto_data` | **Location:** `us-west1`

### Tables

| Table | Rows | Size | Clustering | Purpose |
|-------|------|------|-----------|---------|
| `pfw_transactions` | 497,486,866 | 32.9 GB | application_number | Every transaction event in a patent application's prosecution history |
| `forward_citations` | 210,059,818 | 16.3 GB | cited_patent_number, citing_patent_number | Which granted patents cite which earlier patents |
| `patent_assignments_v2` | 122,912,474 | 36.3 GB | doc_number | Denormalized ownership transfer records (one row per assignor/patent combo) |
| `maintenance_fee_events_v2` | 26,527,580 | 2.1 GB | patent_number | Maintenance fee payments and lapses |
| `patent_file_wrapper_v2` | 12,733,017 | 3.9 GB | application_number, patent_number | One row per patent application — filing date, grant date, entity status, applicant, inventor |
| `pfw_continuity` | 12,253,958 | 1.6 GB | application_number | Parent/child relationships between patent applications |
| `entity_names` | 9,857,896 | 0.3 GB | entity_name | Aggregated unique entity names with frequency counts |
| `name_unification` | 64 | <0.01 GB | — | User-curated MDM associations (representative -> associated names). **DO NOT MODIFY PROGRAMMATICALLY** |
| `etl_log` | 1 | <0.01 GB | source, started_at | Pipeline run tracking (status, timing, file/row counts) |

**Total:** ~892 million rows, ~93.4 GB

### Key Relationships
- `patent_file_wrapper_v2.application_number` links to `pfw_transactions.application_number` and `pfw_continuity.application_number`
- `patent_file_wrapper_v2.patent_number` links to `forward_citations.cited_patent_number` and `maintenance_fee_events_v2.patent_number`
- `patent_assignments_v2.doc_number` is the patent/application number for assignment records
- `entity_names.entity_name` links to `name_unification.associated_name` for MDM lookups
- `name_unification.representative_name` is the canonical name that associated names map to
- `forward_citations` → `patent_file_wrapper_v2` (via citing_patent_number) for applicant/examiner names
- `patent_file_wrapper_v2.first_applicant_name` → `name_unification.associated_name` for name resolution in citations

### Data Sources and Loading
- **patent_file_wrapper_v2, pfw_transactions, pfw_continuity** — Parsed from PTFWPRE JSON ZIPs by `etl/parse_pfw.py`
- **forward_citations** — Parsed from PTBLXML XML ZIPs by `etl/parse_ptblxml.py`
- **patent_assignments_v2** — Parsed from PASYR/PASDL XML ZIPs by `etl/parse_assignments_xml_v2.py`
- **maintenance_fee_events_v2** — Parsed from PTMNFEE2 fixed-width text by `etl/parse_maintenance_fees_v2.py`
- **entity_names** — Rebuilt by SQL aggregation from patent_file_wrapper_v2 and patent_assignments_v2
- **name_unification** — User-curated (64 rows); modified only through the MDM UI
- **etl_log** — Written by `update_pipeline.py` after each pipeline run

### DDL
- `database/setup_v2.sql` — CREATE TABLE statements for all tables (note: references `ingestion_log` instead of `etl_log`)

---

## External Services and Integrations

| Service | Purpose | Configuration |
|---------|---------|--------------|
| **Google Cloud Run** | Hosts the API service and 4 ETL jobs | Project: `uspto-data-app`, Region: `us-central1` |
| **Google BigQuery** | Primary database | Project: `uspto-data-app`, Dataset: `uspto_data`, Location: `us-west1` |
| **Google Vertex AI** | Gemini model for AI assistant | Project: `uspto-data-app`, Region: `us-central1` |
| **Google Cloud Storage** | Staging area for JSONL files before BQ load | Bucket: `gs://uspto-bulk-staging/` |
| **Google Cloud Scheduler** | Triggers ETL Cloud Run Jobs on schedule | Project: `uspto-data-app`, Region: `us-central1` |
| **Google Cloud Build** | Builds the ETL Docker container | Project: `uspto-data-app` |
| **USPTO ODP API** | Source of all patent bulk data | Base URL: `https://api.uspto.gov`, requires `USPTO_API_KEY` header |
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

**No automated tests exist.** Testing has been done manually:
- API endpoints tested via `curl` commands
- ETL pipelines smoke-tested by running Cloud Run Jobs and verifying BQ row counts
- Frontend tested by loading the live URL and exercising each tab

**What's not covered:** Unit tests, integration tests, end-to-end tests. The GitHub Actions workflow deploys on push to main but does not run any tests.

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
  --config=/tmp/cloudbuild-etl.yaml \
  --project=uspto-data-app
```
The cloudbuild config builds `Dockerfile.etl` and pushes to:
`us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest`

The cloudbuild YAML is not checked into the repo. It lives at `/tmp/cloudbuild-etl.yaml`:
```yaml
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', 'Dockerfile.etl', '-t', 'us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest', '.']
images:
  - 'us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest'
timeout: '600s'
```

### Cloud Run Jobs
Each job runs the ETL container with a different source argument:
- `uspto-update-ptblxml` — `python etl/update_pipeline.py ptblxml`
- `uspto-update-pasdl` — `python etl/update_pipeline.py pasdl`
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
│   ├── config.py                 # Settings class (env vars, table names)
│   ├── main.py                   # FastAPI app entry point (6 routers)
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py            # Pydantic request/response models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── ai_assistant.py       # POST /ai/ask
│   │   ├── assignments.py        # GET /api/assignments/{patent}/chain  ← NEW
│   │   ├── citations.py          # GET /api/forward-citations/* (with name resolution)
│   │   ├── etl_log.py            # GET /api/etl-log, /api/etl-log/summary
│   │   ├── mdm.py                # POST/DELETE /mdm/*
│   │   └── query.py              # POST /query/execute, GET /query/fields
│   └── services/
│       ├── __init__.py
│       ├── bigquery_service.py   # BigQuery client singleton
│       └── gemini_service.py     # Vertex AI Gemini integration
├── database/
│   ├── setup.sql                 # Original v1 DDL (obsolete)
│   └── setup_v2.sql              # Current v2 DDL for all tables
├── etl/                          # ETL scripts
│   ├── update_pipeline.py        # Master orchestrator (Cloud Run Job entrypoint)
│   ├── download_ptblxml.py       # PTBLXML downloader (citations)
│   ├── download_pasdl.py         # PASDL downloader (daily assignments)
│   ├── download_pasyr.py         # PASYR downloader (annual assignments)
│   ├── download_and_parse_pasyr.sh  # Shell script for sequential PASYR processing
│   ├── parse_ptblxml.py          # Citation XML parser
│   ├── parse_assignments_xml_v2.py  # Assignment XML parser (denormalized output)
│   ├── parse_maintenance_fees_v2.py # Maintenance fee fixed-width parser
│   ├── parse_pfw.py              # Patent file wrapper JSON parser (3 output tables)
│   ├── parse_file_wrapper.py     # Earlier PFW parser (single output, superseded)
│   ├── populate_entity_names_v2.py  # Standalone entity_names rebuilder
│   ├── fix_bad_dates.py          # Fixes 0000-01-01 dates in PASYR files
│   ├── parse_assignments_xml.py  # v1 assignment parser (obsolete)
│   └── parse_maintenance_fees.py # v1 maintenance fee parser (obsolete)
├── frontend/                     # Vanilla JS SPA
│   ├── css/
│   │   └── styles.css            # All styles (~831 lines)
│   ├── index.html                # Single-page app (5 tabs)
│   └── js/
│       ├── app.js                # Shared utilities, tab switching, API helpers, table sorting, assignment popup
│       ├── mdm.js                # MDM Name Normalization tab
│       ├── query_builder.js      # Boolean Query Builder tab
│       ├── ai_assistant.js       # AI Assistant tab
│       ├── citations.js          # Forward Citations tab (with breakdown lists)
│       └── etl_log.js            # Update Log tab
├── utils/
│   ├── __init__.py
│   └── patent_number.py          # Patent number normalization utility
├── .github/workflows/
│   └── deploy.yml                # GitHub Actions CI/CD
├── Dockerfile                    # API service container
├── Dockerfile.etl                # ETL job container (includes gsutil/bq CLI)
├── requirements.txt              # Python dependencies
├── .env.example                  # Example environment variables
├── .gitignore
├── CLAUDE.md                     # Project instructions for AI assistants
├── HANDOFF.md                    # Handoff document (2026-03-08)
├── HANDOFF311.md                 # Handoff document (2026-03-11)
├── HANDOFF312.md                 # This file (2026-03-12)
├── README.md                     # Project README
├── FRONTEND_SPECIFICATION.md     # Frontend design spec
├── MaintFeeEventsFileDocumentation.doc  # USPTO PTMNFEE2 format docs
└── PADX-File-Description-v2_Hague.doc   # USPTO assignment file format docs
```

---

## Configuration Files

| File | Purpose | Critical Settings |
|------|---------|-------------------|
| `Dockerfile` | API service container | python:3.11-slim, uvicorn on port 8080 |
| `Dockerfile.etl` | ETL job container | python:3.11-slim + google-cloud-cli (for gsutil/bq) |
| `requirements.txt` | Python dependencies | fastapi, uvicorn, google-cloud-bigquery, google-cloud-aiplatform, ijson, requests |
| `.env.example` | Environment variable template | GCP_PROJECT_ID, BIGQUERY_DATASET, GEMINI_API_KEY |
| `.github/workflows/deploy.yml` | CI/CD | Deploys to Cloud Run on push to main |
| `api/config.py` | Runtime config | Reads GCP_PROJECT_ID, BIGQUERY_DATASET, GEMINI_API_KEY from env |

---

## Workarounds and Gotchas

1. **BigQuery location is `us-west1`** — All `bq` CLI commands MUST include `--location=us-west1` or they silently fail. The Cloud Run service is in `us-central1` but BigQuery is in `us-west1`.

2. **No parallel uploads** — `gsutil -m cp` (parallel upload) has repeatedly caused failures. Always use `gsutil cp` (sequential, one file at a time).

3. **No wildcard BQ loads** — Loading `gs://bucket/*.jsonl.gz` has caused problems. Load files individually.

4. **Concatenating .gz files creates multi-stream gzip** — BigQuery cannot read multi-stream gzip. Never concatenate `.gz` files.

5. **Date value `0000-01-01`** — Some PASYR records have this date, which causes BigQuery load failures. The parser validates year range 1700-2100 and nullifies out-of-range dates.

6. **PTBLXML annual files (2002-2005) are too large** — They exceed memory for in-memory XML parsing. Use weekly files only.

7. **`name_unification` table (64 rows) is user-curated** — Never drop, truncate, or bulk-modify this table programmatically. Changes should only come through the MDM UI.

8. **Cloud Build `--tag` and `--config` are mutually exclusive** — You must use a config YAML file for the ETL build, not `--tag` and `--dockerfile` flags together.

9. **The cloudbuild-etl.yaml is NOT in the repo** — It lives at `/tmp/cloudbuild-etl.yaml`. If the server reboots, you'll need to recreate it (see Deployment section above for contents).

10. **Router prefix inconsistency** — MDM/Query/AI use short prefixes (`/mdm`, `/query`, `/ai`) while Citations, Assignments, and ETL Log use `/api/...`. This is cosmetic and doesn't cause issues.

11. **Assignment chain popup uses client-side caching** — The `_chainCache` object in `app.js` caches API responses per patent number. This means assignment data won't refresh until page reload.

12. **Cache-busting via query params** — Frontend scripts use `?v=N` suffixes (e.g., `app.js?v=11`). Bump the version number when modifying any JS or CSS file.

---

## Decisions That Were Rejected

1. **Google's `patents-public-data` BigQuery dataset** — Rejected as a data source. The project requires only official USPTO bulk data to ensure data integrity and provenance.

2. **Nested STRUCT/ARRAY schemas in BigQuery** — The v1 schema used nested types for addresses. Abandoned in v2 because it complicated queries and the AI assistant couldn't generate correct SQL for nested fields. All v2 tables are flat/denormalized.

3. **Cloud Functions for ETL** — Considered for automation but rejected in favor of Cloud Run Jobs because ETL scripts already exist as long-running Python scripts, and Jobs support longer timeouts.

4. **Parallel ETL execution** — Running multiple sources simultaneously was rejected in favor of sequential execution per the user's preference for safety over efficiency.

5. **`etl/load_file_wrapper.py`** — This script attempted to load patent file wrapper data from a different source. Explicitly deprecated and deleted. Use `parse_pfw.py` with PTFWPRE data instead.

6. **In-memory XML parsing for large files** — `xml.etree.ElementTree.parse()` fails on multi-GB files. All parsers use streaming approaches (iterparse for XML, ijson for JSON).

7. **Unique examiner/applicant count KPI cards** — Initially added as simple number cards, replaced by detailed name-and-count breakdown lists for more useful analysis.

---

## Known Limitations and Constraints

1. **No authentication** — Anyone with the URL can access the platform and modify MDM associations.
2. **No automated tests** — All testing is manual.
3. **GitHub Actions CI/CD not fully tested** — The workflow exists but has not been triggered in a while; may need secrets verification.
4. **Gemini SQL generation is imperfect** — Sometimes generates incorrect SQL, especially for complex joins. The auto-retry helps but doesn't catch everything.
5. **PTFWPRE updates are full table replacements** — The entire patent_file_wrapper_v2, pfw_transactions, and pfw_continuity tables are truncated and reloaded. This means brief data unavailability during the update window.
6. **PTMNFEE2 updates are full table replacements** — Same concern as PTFWPRE.
7. **Cloud Run Job disk space** — PTFWPRE ZIPs are 2-6 GB. The default Cloud Run Job may not have enough ephemeral storage for very large files.
8. **`database/setup_v2.sql` references `ingestion_log`** — But the actual deployed table is named `etl_log`. The DDL file is slightly out of sync.

---

## Open Issues and Bugs

- None known at this time. All previously identified issues have been resolved.

---

## Work In Progress

- None. All tasks from this session are complete.

---

## Git State

- **Current branch:** `claude/beautiful-leakey` (worktree branch) at `f34bc52`
- **Main branch (remote):** `origin/main` at `f34bc52` — up to date with all work
- **Main branch (local):** `ebd9286` — 7 commits behind remote (needs `git pull` to sync)
- **Latest commit:** `f34bc52` — "Reposition assignment popup to right of patent number"
- **Open PRs:** None
- **Uncommitted work:** None (clean tree)
- **Branching convention:** Work is done in worktree branches (`claude/*`), merged to `main` via fast-forward

### Recent Commits (newest first)
```
f34bc52 Reposition assignment popup to right of patent number
ea9715b Fix assignment chain query: include recorded_date in outer SELECT
ab22a0e Add patent assignment chain popup on hover over patent numbers
f3563ee Add click-to-sort on all table headers across all tabs
a0a6686 Fix citation breakdown lists to match KPI category counts
f2c4560 Replace unique examiner/applicant KPIs with detailed breakdown lists
3c4b608 Add sticky table headers across all tabs
ebd9286 Add unique examiner/applicant counts and name normalization to citations
8873c55 Update forward citations table: add applicant/examiner, remove grant date/kind
dc04e81 Add HANDOFF311.md comprehensive project snapshot and update CLAUDE.md
51e02c8 Add ETL Update Log tab with pipeline run tracking
```

---

## Next Steps

No outstanding engineering tasks. Potential future work (not prioritized by the user):

1. **Add authentication** — Protect the platform with user login if it will be shared publicly
2. **Add automated tests** — Unit tests for parsers, integration tests for API endpoints
3. **Verify GitHub Actions CI/CD** — Trigger a deployment via push and confirm it works end-to-end
4. **Check in cloudbuild-etl.yaml** — Move it from `/tmp/` to the repo so it survives server reboots
5. **Entity type analytics** — Analyze small-entity-to-large-entity conversion patterns (original project goal)
6. **Citation network analysis** — Build citation graph features on top of the forward_citations data
7. **MDM workflow improvements** — Better UI for bulk name association workflows

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
gcloud builds submit --config=/tmp/cloudbuild-etl.yaml --project=uspto-data-app

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
