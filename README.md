# USPTO Data Platform — Patent Intelligence & MDM

A serverless, enterprise-grade **Patent Intelligence and Master Data Management (MDM)** platform built on Google Cloud. The platform ingests official USPTO bulk data products (Patent File Wrappers, Assignments, Maintenance Fees), enables human data-stewards to clean and normalize entity names, and supports both AI-driven natural-language querying and strict Boolean querying of the resulting dataset.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Stack](#2-technology-stack)
3. [Data Sources & ETL Pipeline](#3-data-sources--etl-pipeline)
4. [BigQuery Schema](#4-bigquery-schema)
5. [Core Features](#5-core-features)
   - [A. Master Data Management (MDM)](#a-master-data-management-mdm)
   - [B. Boolean Query Builder](#b-boolean-query-builder)
   - [C. AI Assistant (Conversational)](#c-ai-assistant-conversational)
6. [Repository Architecture](#6-repository-architecture)
7. [API Reference](#7-api-reference)
8. [Frontend Architecture](#8-frontend-architecture)
9. [ETL Scripts Reference](#9-etl-scripts-reference)
10. [Setup and Deployment](#10-setup-and-deployment)
11. [Infrastructure](#11-infrastructure)

---

## 1. Project Overview

The USPTO Data Platform solves a core data-quality problem that affects patent analytics: raw USPTO records contain hundreds of inconsistent spellings and abbreviations for the same legal entity. Left uncorrected, these inconsistencies make ownership queries unreliable and break downstream analytics.

The platform provides three integrated capabilities:

| Capability | Description |
|---|---|
| **Master Data Management** | Human-supervised, deterministic workflow to normalize raw entity names to canonical master records. No AI involvement in data writes. |
| **AI Assistant** | Conversational natural-language query interface powered by Google Gemini (Vertex AI) that translates plain-English questions into validated BigQuery SQL. |
| **Boolean Query Builder** | Parameterized, injection-safe SQL query builder for analysts who require strict, repeatable Boolean logic without LLM involvement. |

All three capabilities share a common BigQuery data warehouse and are served through a single FastAPI backend deployed as a containerized Cloud Run service.

**CRITICAL DATA INTEGRITY RULE**: This platform uses ONLY official USPTO bulk data products as data sources. No third-party datasets, no convenience substitutions. Data integrity is the top priority.

---

## 2. Technology Stack

| Layer | Technology | Details |
|---|---|---|
| **Backend** | Python 3.11, FastAPI 0.109.2, Uvicorn 0.27.1 | Async-capable ASGI server |
| **Database** | Google Cloud BigQuery | Serverless data warehouse, parameterized queries |
| **AI / LLM** | Google Vertex AI, Gemini 2.5 Flash | `google-cloud-aiplatform` SDK, multi-turn conversational chat |
| **Frontend** | Vanilla JavaScript, HTML5, CSS3 | No build toolchain (no webpack/npm), modular JS files |
| **Deployment** | Google Cloud Run, Docker | Containerized, auto-scaling, zero-downtime deployments |
| **CI/CD** | GitHub Actions | Workload Identity Federation (no service account keys) |
| **Data Staging** | Google Cloud Storage | `gs://uspto-bulk-staging/` for bulk data downloads |

---

## 3. Data Sources & ETL Pipeline

### Official USPTO Bulk Data Products (ONLY authorized sources)

| Product ID | Name | Format | Data Scope | ETL Script |
|---|---|---|---|---|
| **PTMNFEE2** | Maintenance Fee Events | Fixed-width ASCII (59 chars/record) | 2016+ (~12.2M rows) | `etl/parse_maintenance_fees.py` |
| **PASYR** | Patent Assignment System Year | PADX XML v2.0 | 2006+ (~17M rows) | `etl/parse_assignments_xml.py` + `etl/download_and_parse_pasyr.sh` |
| **PTFWPRE** | Patent File Wrapper (Bulk) | JSON (zip archives) | 2001-2026 | **IN PROGRESS** — parser needs to be built |

### USPTO API Access

- **Product Data API**: `GET https://api.uspto.gov/api/v1/datasets/products/{productIdentifier}`
- **File Download API**: `GET https://api.uspto.gov/api/v1/datasets/products/files/{productIdentifier}/{fileName}`
- **Authentication**: `x-api-key` header with USPTO API key (stored in `$USPTO_API_KEY` env var)

### Data Pipeline Flow

```
USPTO Bulk Data API
        |
        v
  Download to GCS (gs://uspto-bulk-staging/)
        |
        v
  ETL Parser (Python scripts in /etl/)
        |
        v
  JSONL/CSV output files
        |
        v
  bq load -> BigQuery (uspto-data-app.uspto_data.*)
        |
        v
  populate_entity_names.py -> entity_names table
```

---

## 4. BigQuery Schema

**Project**: `uspto-data-app`
**Dataset**: `uspto_data`

### patent_file_wrapper
| Column | Type | Description |
|---|---|---|
| `patent_number` | STRING (nullable) | Patent number. NULL for pending/ungranted applications |
| `application_number` | STRING | USPTO application number assigned at filing |
| `invention_title` | STRING | Title of the invention |
| `grant_date` | DATE | Date the patent was granted |
| `applicants` | ARRAY\<STRUCT\> | Nested array of applicant records |

**Applicants STRUCT fields**: `name`, `street_address`, `city`, `state`, `country`, `entity_type` (all STRING)

**Status**: Currently empty (0 rows). Previously contained contaminated data from a third-party source which was purged. Awaiting reload from official PTFWPRE bulk product.

### patent_assignments
| Column | Type | Description |
|---|---|---|
| `patent_number` | STRING (nullable) | Patent number. NULL for ungranted applications |
| `application_number` | STRING | USPTO application number |
| `recorded_date` | DATE | Date the assignment was recorded |
| `assignees` | ARRAY\<STRUCT\> | Nested array of assignee records |

**Assignees STRUCT fields**: `name`, `street_address`, `city`, `state`, `country` (all STRING)

**Current size**: ~17M rows, ~2 GB

### maintenance_fee_events
| Column | Type | Description |
|---|---|---|
| `patent_number` | STRING | Patent number |
| `application_number` | STRING | USPTO application number |
| `event_code` | STRING | 5-character event code |
| `event_date` | DATE | Date of the event |
| `fee_code` | STRING | Fee period (3.5_YEAR, 7.5_YEAR, 11.5_YEAR) |
| `entity_status` | STRING | SMALL, MICRO, or LARGE |

**Current size**: ~12.2M rows, ~528 MB

### name_unification
| Column | Type | Description |
|---|---|---|
| `representative_name` | STRING | The canonical/master entity name |
| `associated_name` | STRING | A variant/typo name linked to the representative |

**Purpose**: MDM normalization mappings. Each representative has a self-association plus one row per variant.

### entity_names
| Column | Type | Description |
|---|---|---|
| `entity_name` | STRING | Unique entity name from applicants/assignees |
| `frequency` | INT64 | Total occurrence count across all patent tables |

**Purpose**: Pre-computed lookup table for MDM search performance. Rebuilt by `etl/populate_entity_names.py` after any data load. Clustered by `entity_name`.

**Current size**: ~800K unique names

---

## 5. Core Features

### A. Master Data Management (MDM)

The MDM workflow is a **strictly manual, deterministic process** completely decoupled from AI. No LLM is involved in data writes.

#### Search Syntax (Boolean expressions)

- `term` or `+term` — result **must contain** this term
- `-term` — result **must NOT contain** this term
- `*` — wildcard (implicit, terms match as substrings)
- Terms are space-separated. Example: `+elect* +tele* -INC`
- Search queries against the pre-computed `entity_names` table

#### Copy/Paste Normalization Workflow

1. Data steward searches for entity names
2. **Copy** (clipboard icon) a name to set it as the active Representative Name
3. **Paste** (paste icon) on other rows to associate them under the representative
4. Associations stored in `name_unification` table

#### Color Coding

| Color | Meaning |
|---|---|
| **Orange** | Orphaned representative — master record with no aliases yet |
| **Red** | Active/copied representative — currently held in UI state for paste operations |
| **Green** | Normalized — successfully linked to a representative name |

#### Address Cross-Referencing

- Click the address icon on any entity to see all unique addresses
- Addresses aggregated from both `applicants` (patent_file_wrapper) and `assignees` (patent_assignments)
- When viewing a representative's addresses, all linked aliases' addresses are included
- Select addresses and search for other entities at those locations
- Helps discover that "Acme Corp." and "Acme Corporation" share the same address

### B. Boolean Query Builder

Strict, parameterized SQL query builder with no AI involvement.

#### Features

- **Multi-table queries**: Select one or more tables (patent_file_wrapper, patent_assignments, maintenance_fee_events)
- **Dynamic conditions**: Add/remove condition rows with field, operator, and value
- **Operators**: CONTAINS, EQUALS, STARTS_WITH, ENDS_WITH, AFTER, BEFORE
- **Logic**: AND/OR toggle for combining conditions
- **Name expansion**: EQUALS on name fields automatically expands via `name_unification` to include all associated names
- **Multi-value code selection**: event_code and fee_code fields use multi-select dropdowns populated from actual database values
- **Boolean expressions in name fields**: CONTAINS operator on name fields supports `+term -term` boolean syntax
- **Date fields**: Uses HTML date picker for event_date, grant_date, recorded_date
- **Sortable results**: Click column headers to sort (asc/desc/none cycle)
- **Column visibility**: Dropdown to show/hide columns in results

#### SQL Generation

- All values passed as `@parameterized` BigQuery query parameters (injection-safe)
- Automatic JOIN generation when conditions span multiple tables (joined on `patent_number`)
- Automatic UNNEST for array fields (applicants, assignees)
- Max results configurable (default 100)

### C. AI Assistant (Conversational)

Natural-language conversational interface powered by Google Gemini via Vertex AI.

#### Conversational Flow

1. User describes what data they want in plain English
2. Gemini **discusses and clarifies** the query (does NOT generate SQL immediately)
3. User refines the query through back-and-forth conversation
4. When user says "run it", "execute", "yes", etc., Gemini generates BigQuery SQL
5. SQL is executed with automatic retry (up to 3 attempts with error-driven fixes)
6. Results displayed in sortable table

#### SQL Security

- Generated SQL is extracted from ```sql code fences
- Trailing semicolons stripped
- Execution with automatic retry: on failure, Gemini receives the error and attempts to fix the SQL
- Maximum 3 attempts (original + 2 retries)

#### SQL Accordion

- Generated SQL is hidden behind a collapsible accordion
- Click "+" button to expand and view the SQL
- Intended for technical users who want to verify the query

#### Prompt Engineering

The system prompt includes:
- Complete BigQuery schema with data types and array structures
- Comprehensive SQL rules (FROM clause required, ARRAY_AGG syntax, UNNEST patterns)
- CTE patterns for flattening arrays before LEFT JOINs
- Name expansion pattern using name_unification table
- Conversation rules (discuss first, only generate SQL on explicit command)

---

## 6. Repository Architecture

```
SmallEntityPayments/
|-- api/
|   |-- __init__.py
|   |-- main.py                    # FastAPI entry point, router registration, static files
|   |-- config.py                  # Settings from env vars (GCP_PROJECT_ID, BIGQUERY_DATASET)
|   |-- models/
|   |   |-- __init__.py
|   |   |-- schemas.py             # Pydantic request/response models (25+ models)
|   |-- routers/
|   |   |-- __init__.py
|   |   |-- mdm.py                 # MDM endpoints: search, associate, delete, addresses
|   |   |-- query.py               # Boolean Query Builder: execute, fields, event/fee codes
|   |   |-- ai_assistant.py        # AI chat endpoint with retry logic
|   |-- services/
|       |-- __init__.py
|       |-- bigquery_service.py    # All BigQuery operations (queries, DML, name expansion)
|       |-- gemini_service.py      # Vertex AI Gemini: chat(), fix_sql(), prompt engineering
|-- frontend/
|   |-- index.html                 # SPA shell, 3 tab panels, 2 modals, accordion
|   |-- css/
|   |   |-- styles.css             # Complete stylesheet (~400 lines)
|   |-- js/
|       |-- app.js                 # Tab navigation, API helpers, buildInteractiveTable()
|       |-- mdm.js                 # MDM tab: search, copy/paste, address modals (~400 lines)
|       |-- query_builder.js       # Query Builder: dynamic conditions, code dropdowns (~300 lines)
|       |-- ai_assistant.js        # AI chat: history, message bubbles, SQL accordion (~100 lines)
|-- etl/
|   |-- parse_maintenance_fees.py  # PTMNFEE2 fixed-width parser -> CSV
|   |-- parse_assignments_xml.py   # PADX XML v2.0 parser -> JSONL (streaming, memory-safe)
|   |-- download_and_parse_pasyr.sh # PASYR orchestrator (download -> parse -> bq load, per-file)
|   |-- load_file_wrapper.py       # DEPRECATED -- loaded from third-party source, DO NOT USE
|   |-- populate_entity_names.py   # Rebuilds entity_names table from applicants + assignees
|-- .env.example                   # Environment variable template
|-- .gitignore                     # Excludes .env, __pycache__, *.json (SA keys), IDE files
|-- .gcloudignore                  # Cloud build exclusions
|-- Dockerfile                     # python:3.11-slim, pip install, uvicorn on port 8080
|-- requirements.txt               # fastapi, uvicorn, google-cloud-bigquery, google-cloud-aiplatform, pydantic, python-dotenv
|-- CLAUDE.md                      # AI assistant coding guidelines
|-- .github/
|   |-- workflows/
|       |-- deploy.yml             # CI/CD: checkout -> auth (WIF) -> docker build -> push -> Cloud Run deploy
|-- README.md                      # This file
```

---

## 7. API Reference

**Base URL**: `https://uspto-api-7wmnuaghmq-uc.a.run.app`

### Health Check
- `GET /health` -> `{"status": "ok"}`

### MDM Endpoints (prefix: `/mdm`)

| Method | Path | Request Body | Description |
|---|---|---|---|
| POST | `/mdm/search` | `{"query": "+elect* +tele*"}` | Boolean search of entity_names table |
| POST | `/mdm/associate` | `{"representative_name": "...", "associated_names": [...]}` | Link names to a representative |
| DELETE | `/mdm/associate` | `{"associated_name": "..."}` | Remove association (cascades if representative) |
| POST | `/mdm/addresses` | `{"name": "..."}` | Get unique addresses for entity |
| POST | `/mdm/search-by-address` | `{"addresses": [{"street_address": "...", "city": "..."}]}` | Find entities at given addresses |

### Query Builder Endpoints (prefix: `/query`)

| Method | Path | Request Body | Description |
|---|---|---|---|
| POST | `/query/execute` | `{"conditions": [...], "logic": "AND", "limit": 100, "tables": [...]}` | Execute parameterized query |
| GET | `/query/fields` | -- | Get available tables, fields, operators |
| GET | `/query/event-codes` | -- | Get distinct event_code values |
| GET | `/query/fee-codes` | -- | Get distinct fee_code values |

### AI Assistant Endpoints (prefix: `/ai`)

| Method | Path | Request Body | Description |
|---|---|---|---|
| POST | `/ai/ask` | `{"prompt": "...", "history": [{"role": "user", "content": "..."}]}` | Conversational AI query with history |

---

## 8. Frontend Architecture

### Design System

- **Primary color**: #1a56db (blue)
- **Font**: Segoe UI system font, 15px base
- **Layout**: Max-width 1280px, centered
- **Components**: Cards, modals (layered z-index), buttons, form controls, data tables

### Tab 1: MDM Name Normalization (`mdm.js`)

- State: `workspaceData`, `activeRepresentative`, `selectedRows`, sort state
- Table with action buttons: checkbox, address, copy, paste, copy-rep, delete
- Multi-select with Ctrl+click range selection
- Two-level modal system (address lookup -> address search results)
- Address results modal has same row actions as main table

### Tab 2: Boolean Query Builder (`query_builder.js`)

- Dynamic condition rows (add/remove)
- Field-dependent input types: text input, date picker, multi-select dropdown
- Fetches field metadata and code lists on initialization
- Results use `buildInteractiveTable()` with sortable columns and column picker

### Tab 3: AI Assistant (`ai_assistant.js`)

- Chat bubble interface with avatars
- `chatHistory` array sent with each request for conversational context
- SQL accordion (collapsed by default)
- Results use `buildInteractiveTable()`

### Shared Utilities (`app.js`)

- `apiPost()`, `apiGet()`, `apiDelete()` -- fetch wrappers with error handling
- `setLoading()` -- button loading state with spinner
- `showStatus()` -- timed status messages
- `buildInteractiveTable()` -- sortable columns, column picker dropdown, type-aware sorting
- `escHtml()` -- HTML escaping

### Cache Busting

All JS/CSS files loaded with `?v=7` query parameter. Increment on each deployment.

---

## 9. ETL Scripts Reference

### parse_maintenance_fees.py
```bash
python etl/parse_maintenance_fees.py <input_file.txt> <output_file.csv> [min_year]
```
- Parses PTMNFEE2 fixed-width format (59 chars/record)
- Derives `entity_status` (SMALL/MICRO/LARGE) from indicator + event_code
- Derives `fee_code` (3.5_YEAR/7.5_YEAR/11.5_YEAR) from event_code
- Default min_year: 2016

### parse_assignments_xml.py
```bash
python etl/parse_assignments_xml.py <input.xml> <output.jsonl> [min_year]
# or pipe to stdout:
python etl/parse_assignments_xml.py <input.xml> - [min_year]
```
- Streaming XML parser using `iterparse` (memory-safe for large files)
- Outputs JSONL with nested assignees array
- Default min_year: 2006
- Supports gzip output

### download_and_parse_pasyr.sh
```bash
export USPTO_API_KEY=your_key_here
bash etl/download_and_parse_pasyr.sh
```
- Downloads all PASYR files from USPTO API
- Processes one file at a time (disk-constrained environments)
- Unzip -> parse XML -> load JSONL to BigQuery -> cleanup
- Shows running totals

### populate_entity_names.py
```bash
python etl/populate_entity_names.py
```
- Rebuilds `entity_names` table via CREATE OR REPLACE TABLE
- Aggregates names from `patent_file_wrapper.applicants` + `patent_assignments.assignees`
- **Must run after any data load** into source tables

### load_file_wrapper.py -- DEPRECATED
**DO NOT USE.** This script loaded data from `patents-public-data.patents.publications` (a third-party Google BigQuery public dataset), violating the data integrity rule. The contaminated data has been purged. A new parser for the official PTFWPRE product needs to be built.

---

## 10. Setup and Deployment

### Prerequisites

- Google Cloud project with these APIs enabled:
  - BigQuery API
  - Cloud Run API
  - Artifact Registry API
  - Vertex AI API (`aiplatform.googleapis.com`)
- Service account with roles: `BigQuery Data Editor`, `BigQuery Job User`, `Vertex AI User`
- Docker installed for local development

### Environment Variables

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `GCP_PROJECT_ID` | Yes | Google Cloud project ID (`uspto-data-app`) |
| `BIGQUERY_DATASET` | Yes | BigQuery dataset name (`uspto_data`) |
| `GEMINI_API_KEY` | No | Legacy -- not used in production (Vertex AI uses service account) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Local only | Path to service account JSON key file |
| `USPTO_API_KEY` | ETL only | USPTO bulk data API key |

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn api.main:app --reload --port 8080

# Or build and run with Docker
docker build -t uspto-platform .
docker run --env-file .env -p 8080:8080 uspto-platform
```

The application serves at `http://localhost:8080`. The frontend is served as static files from the same container.

### Manual Deployment (Cloud Run source deploy)

```bash
gcloud run deploy uspto-api \
  --source . \
  --region us-central1 \
  --allow-unauthenticated
```

### Automated CI/CD Deployment

The GitHub Actions workflow (`.github/workflows/deploy.yml`) triggers on push to `main` or manual dispatch:

1. **Checkout** -- Clones repository
2. **Google Auth** -- Authenticates via Workload Identity Federation (no SA keys)
3. **Build & Push** -- Docker image -> Artifact Registry (`us-central1-docker.pkg.dev/{project}/uspto-repo/uspto-api:{sha}`)
4. **Deploy** -- Cloud Run service update with zero-downtime traffic migration

**Required GitHub Secrets:**
- `GCP_PROJECT_ID`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `GEMINI_API_KEY`

---

## 11. Infrastructure

### Google Cloud Resources

| Resource | Name/ID | Details |
|---|---|---|
| **Project** | `uspto-data-app` | All resources in this project |
| **Cloud Run Service** | `uspto-api` | Region: `us-central1`, unauthenticated access |
| **BigQuery Dataset** | `uspto_data` | 5 tables (see Schema section) |
| **GCS Bucket** | `gs://uspto-bulk-staging/` | Staging area for USPTO bulk data downloads |
| **Artifact Registry** | `uspto-repo` | Docker image repository |
| **Vertex AI** | `gemini-2.5-flash` | Region: `us-central1` |

### Live URL

`https://uspto-api-7wmnuaghmq-uc.a.run.app`
