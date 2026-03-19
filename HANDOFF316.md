# HANDOFF316 — USPTO Data Platform Complete System Snapshot
**Date:** 2026-03-18 | **Session:** 5 | **Branch:** `claude/condescending-gagarin`

This document is a complete system snapshot written for a new session that has never seen this project. Read this file first, then start working.

---

## 1. System Overview

**SmallEntityPayments** (internally called "USPTO Data Platform") is a web-based patent analytics platform built by a patent attorney / IP strategist with 15+ years of patent monetization experience. The owner is NOT a coder — explain all technical decisions in plain language.

The platform ingests official USPTO bulk data into Google BigQuery (~1.9 billion rows across 27 tables), then provides a browser UI for patent professionals to research, cross-reference, and analyze that data.

**Repository:** https://github.com/InnovationAccess/SmallEntityPayments
**Live URL:** https://uspto-api-1094570457455.us-central1.run.app
**GCP Project:** `uspto-data-app`
**BigQuery Dataset:** `uspto_data` (region: `us-west1`)
**API Docs (Swagger):** https://uspto-api-1094570457455.us-central1.run.app/docs

### Core Business Purpose

Help patent attorneys and IP monetization specialists:
- **Identify underpaying entities** — companies paying maintenance fees or prosecution fees as Small/Micro entities that may not qualify (generates licensing leverage)
- **Quantify underpayment exposure** — calculate exact dollar amounts of reduced-rate payments vs. what should have been paid at Large entity rates, using the official USPTO fee schedule
- **Track patent ownership chains** — from original inventor through all assignments (employee, divestiture, merger, etc.)
- **Analyze entity status transitions** — small→large transitions that signal company growth
- **Investigate prosecution payment patterns** — find entities with Small entity declarations during prosecution
- **Generate leads** for licensing or assertion campaigns via SEC EDGAR enrichment

---

## 2. Domain Terminology

| Term | Definition |
|---|---|
| **Entity status** | USPTO fee classification: LARGE (full fees), SMALL (50% discount pre-UAIA / 60% discount post-UAIA), MICRO (75% discount pre-UAIA / 80% post-UAIA). **MUST be derived from event codes, NOT the entity_status column.** |
| **UAIA** | United States Advancing Inventing Act, effective Dec 29, 2022. Changed Small discount from 50% to 60% and Micro from 75% to 80%. |
| **Maintenance fee** | Periodic fees paid to keep a patent in force. Due at 3.5, 7.5, and 11.5 years after grant. |
| **Event code** | USPTO code on a maintenance fee event. `M1xxx/F17xx` = LARGE, `M2xxx/F27xx` = SMALL, `M3xxx` = MICRO. Payment codes: `M*551`=3.5yr, `M*552`=7.5yr, `M*553`=11.5yr. |
| **Prosecution payment** | Fee paid during the prosecution phase (pre-grant). Examples: filing fees, RCE fees, issue fees, extension fees. 102 event codes tracked, but only ~22 trigger actual payments. |
| **PAY / PROC / REV** | Classification of prosecution event codes. PAY = triggers actual payment. PROC = procedural (no fee). REV = reversal (subtracts a fee). |
| **Declaration code** | `SMAL`, `BIG.`, `MICR` — entity status declarations filed during prosecution. Appear in `pfw_transactions`. |
| **Transition code** | `STOL` (small→large), `LTOS` (large→small), `STOM` (small→micro), `MTOS` (micro→small). Appear in `maintenance_fee_events_v2`. |
| **reel_frame** | Unique identifier for a USPTO assignment recordation (e.g., "047000/0001"). Links all 4 assignment tables together. |
| **application_number** | The universal cross-table key. Every patent asset has one. Patent numbers only exist for granted patents. **NEVER search both simultaneously** — causes number-space collisions. |
| **normalized_type** | Fine-grained classification of an assignment's conveyance text into 14 categories (employee, divestiture, merger, security, etc.). |
| **conveyance_text** | Free-text description of what an assignment conveys (e.g., "ASSIGNMENT OF ASSIGNORS INTEREST"). Source for normalized_type classification. |
| **employer_assignment** | Boolean: TRUE when inventors assign to their employer. The 86.9% majority case. |
| **MDM** | Master Data Management — the name normalization system. Maps variant company names (e.g., "SAMSUNG ELEC CO LTD" → "Samsung Electronics Co., Ltd.") via the `name_unification` table. |
| **PASDL** | USPTO bulk data product: Patent Assignment Daily data (daily assignment XML files). |
| **PTFWPRE** | USPTO bulk product: Patent File Wrapper Prosecution (patent prosecution history XML). |
| **PTBLXML** | USPTO bulk product: Patent Grant Citations Bibliographic XML (weekly forward citation files). |
| **PTMNFEE2** | USPTO bulk product: Patent Maintenance Fee Events (full maintenance fee history). |
| **Prosecution phase** | Period from filing to patent grant. Entity status tracked via `pfw_transactions` declarations. |
| **Post-grant phase** | Period after patent grant. Entity status tracked via maintenance fee payment codes. |
| **Ownership window** | The period during which an entity owns a patent: `[acquired_date, divested_date)`. Used to filter KPIs to events during actual ownership. |
| **Fee schedule period** | One of 7 time periods where USPTO fee rates changed. Rates differ by period, entity size, and fee category. |
| **RCE ordinal** | 1st vs 2nd+ Request for Continued Examination. Different fee rates post-AIA (Mar 19, 2013). No distinct event codes — must be counted chronologically per application. |

---

## 3. Architecture

```
[USPTO Bulk Data] → [Cloud Run ETL Jobs] → [GCS Staging] → [BigQuery]
                                                               ↓
[Browser SPA] ← [Cloud Run API (FastAPI)] ← [BigQuery queries]
                                            ↑
                                [Vertex AI / Gemini] (NL→SQL + PDF vision)
                                [Unified Patents API] (litigation data)
```

**Technologies:**
- **Backend:** Python 3.11, FastAPI, Uvicorn — served on Google Cloud Run (us-central1)
- **Frontend:** Vanilla JS (ES modules), no build step — served as static files from the same Cloud Run container
- **Database:** Google BigQuery (us-west1) — dataset `uspto_data`, ~1.9 billion rows across 27 tables (~155 GB)
- **AI:** Vertex AI / Gemini (via google-cloud-aiplatform) for natural-language queries; Gemini Vision for PDF fee sheet extraction
- **ETL:** Cloud Run Jobs (separate container, `Dockerfile.etl`) triggered by Cloud Scheduler
- **Storage:** Google Cloud Storage `gs://uspto-bulk-staging/` — staging area for bulk downloads and prosecution invoices
- **CI/CD:** GitHub Actions (`.github/workflows/deploy.yml`) — auto-deploys to Cloud Run on push to `main`
- **Litigation:** Unified Patents public Elasticsearch API — results cached in BigQuery for 30 days

### Key Architectural Rules

1. **Cross-table joins always use `application_number`** — never `patent_number`. A patent number only exists after grant; application_number exists from filing. Searching a string across both fields simultaneously causes collisions (e.g., patent 11172434 collides with application 11/172,434).

2. **Entity status MUST be derived from event codes** — M1xxx/F17xx=LARGE, M2xxx/F27xx=SMALL, M3xxx=MICRO. The `entity_status` column in USPTO data is populated inconsistently and MUST NOT be used.

3. **All BigQuery tables use flat/denormalized schemas** — no STRUCT/ARRAY except cpc_codes.

4. **NEVER search a string across multiple fields** — e.g., `WHERE app_num = @id OR patent_num = @id`. Always resolve to application_number first via `patent_file_wrapper_v2`.

---

## 4. Backend — API Routes

**Entry point:** `api/main.py` — registers all 10 routers and mounts the frontend as `/static`

| Router file | Prefix | What it does |
|---|---|---|
| `api/routers/mdm.py` | `/mdm` | Entity name search, MDM CRUD (add/remove name associations) |
| `api/routers/query.py` | `/query` | Boolean query builder — multi-table parametrized BigQuery SQL |
| `api/routers/ai_assistant.py` | `/ai` | Natural language queries via Gemini; generates + executes BigQuery SQL |
| `api/routers/citations.py` | `/api/forward-citations` | Forward citation lookup with examiner/applicant breakdown |
| `api/routers/assignments.py` | `/api/assignments` | Assignment chain popup — resolves patent# → app# → reel_frames → chain |
| `api/routers/entity_status.py` | `/api/entity-status` | **Main analytics router** — Entity status analytics, portfolio KPIs, micro chart timelines, prosecution payment analysis with fee calculation |
| `api/routers/prosecution.py` | `/api/prosecution` | Prosecution fee investigation (3-phase workflow) |
| `api/routers/litigation.py` | `/api/litigation` | Patent litigation lookup via Unified Patents API |
| `api/routers/etl_log.py` | `/api/etl-log` | ETL pipeline run history |
| `api/routers/sec_leads.py` | `/api/sec-leads` | SEC EDGAR lead enrichment (early stage) |

### Key Endpoints

```
GET  /api/assignments/{patent_number}/chain      Assignment chain for a patent
GET  /api/forward-citations/{patent_number}      Forward citation list
POST /api/entity-status/applicant-portfolio      Full portfolio analysis for an entity
POST /api/entity-status/bulk-timelines           Micro chart timeline data (batched, max 200/call)
POST /api/entity-status/prosecution-timelines    Prosecution payment analysis with fee calculation (max 200/call)
POST /api/entity-status/conversion-search        Find patents that changed entity status
GET  /api/entity-status/summary                  Aggregate entity status distribution
POST /api/litigation/bulk-lookup                 Patent litigation case lookup (batched, max 200/call)
POST /mdm/search                                 Boolean name search (entity_names table)
POST /mdm/resolve                                Resolve name to canonical via name_unification
POST /mdm/associate                              Add name association to name_unification
POST /query/execute                              Execute a structured boolean query
POST /ai/query                                   Natural language → SQL → results
POST /api/prosecution/discover-entities          Phase 1: find entities with SMAL declarations
POST /api/prosecution/drill-down                 Phase 2: applications for an entity
POST /api/prosecution/documents                  Phase 3: fetch USPTO API docs for an application
POST /api/prosecution/extract-fees              Extract fee codes from a PDF via Gemini Vision
GET  /api/etl-log/recent                         Recent pipeline runs
GET  /health                                     Health check
```

### Shared Services

- `api/services/bigquery_service.py` — `bq_service` singleton, `run_query()`, `search_entity_names()`, `expand_name_for_query()`
- `api/services/gemini_service.py` — Gemini AI wrapper for SQL generation and PDF vision
- `api/config.py` — `settings` object with all BigQuery table references as properties (single source of truth for table names)

### The Fee Calculation Engine (NEW — 2026-03-18)

`utils/fee_schedule.py` — Expert-verified forensic fee calculation module:
- **12 fee categories** × 7 fee schedule periods × 3 entity sizes (Large/Small/Micro)
- **7 fee periods** with exact effective dates: Oct 1 2006, Sep 26 2011, Mar 19 2013 (AIA/micro entity), Jan 16 2018, Oct 2 2020, Dec 29 2022 (UAIA), Jan 19 2025
- **22 PAY codes** that trigger actual payment (out of 102 tracked prosecution event codes)
- **4 REV codes** that reverse/subtract fees (MODPD28, ODPD28, RVIFEEHA, VFEE)
- **RCE ordinal counter** — tracks 1st vs 2nd+ RCE per application (different rates post-AIA)
- **P005 compound fee** — issue fee + petition for revival combined
- **IDS conditional** — IDS fee only paid if preceded by CTFR/MS95/NOA/MAILNOA/D.ISS
- **FEE. ±3-day dedup** — ignore FEE. if another PAY code exists within ±3 days
- **Same-category dedup** — same fee category + same app + same date = 1 payment

Called from `entity_status.py` → `_analyze_prosecution_apps()` → `calculate_payment_fees()`.

Result: each payment gets enriched with `cat` (fee category), `paid` (amount at entity rate), `large` (amount at Large rate), `delta` (underpayment = large - paid).

---

## 5. Frontend

**Entry:** `frontend/index.html` — single-page app with 9 tabs (no build step, served as static files)

| Tab | JS file | What it does |
|---|---|---|
| MDM Name Normalization | `frontend/js/mdm.js` | Search entity names, view canonical mappings, add/remove associations |
| Boolean Query Builder | `frontend/js/query_builder.js` | Build multi-condition queries against 3 tables |
| AI Assistant | `frontend/js/ai_assistant.js` | Chat interface for natural language patent queries |
| Forward Citations | `frontend/js/citations.js` | Citation lookup with year chart, examiner/applicant breakdown |
| Entity Status | `frontend/js/entity_status.js` | **Main analytics tab** — conversion search, applicant portfolio with micro chart timelines, prosecution payment analysis with dollar amounts |
| Locate Payors | `frontend/js/prosecution.js` | 3-phase prosecution fee investigation |
| Update Log | `frontend/js/etl_log.js` | ETL pipeline monitoring |
| SEC Leads | `frontend/js/sec_leads.js` | SEC EDGAR lead generation (early stage) |
| (hidden) | `frontend/js/app.js` | Shared: tab switching, assignment chain popup, table utilities |

**Shared JS:** `frontend/js/app.js` — assignment chain popup, tab switching, shared utilities (`escHtml`, `buildInteractiveTable`, `enableAssignmentPopup`, `enableTableSorting`, `stampOriginalOrder`, `addColumnPicker`)

**CSS:** `frontend/css/styles.css` — all styles

### Current Cache-Busting Versions (MUST bump when files change)
```
styles.css?v=25       app.js?v=17           mdm.js?v=8
query_builder.js?v=9  ai_assistant.js?v=8   citations.js?v=5
entity_status.js?v=29 prosecution.js?v=9    etl_log.js?v=3
sec_leads.js?v=3
```

### Frontend Patterns

- All tables have sticky headers, sortable columns (click header), column picker dropdowns
- Any patent number in any table is clickable → opens assignment chain popup
- Assignment popup: movable (drag header), resizable (drag corner), fills right side of viewport, shows normalized_type column
- Name search uses MDM boolean syntax: `+elect +tele -inc` (CONTAINS auto-wraps with `%`)
- MDM main table has its own custom sorting — do NOT add `enableTableSorting()` to it
- `data-sort-key` attribute on `<th>` enables column sorting; `stampOriginalOrder()` + `enableTableSorting()` after populating tbody
- `enableAssignmentPopup(selector)` on any table with patent number cells

---

## 6. Key User Flows

### Flow A: Entity Status Portfolio Analysis (most important)
1. User goes to **Entity Status** tab
2. Types entity name in "Applicant Portfolio" input (e.g., "ETRI")
3. Clicks "Find" → dropdown shows matching canonical names with frequency counts
4. Selects a name → portfolio query runs (3 UNION DISTINCT sources: pfw_applicants, pfw_inventors, pat_assign_assignees)
5. **KPI cards show:**
   - Portfolio counts: Filed, Acquired, Divested, Expired, Currently Owned (for granted + pending)
   - Post-grant maintenance fee status breakdown (SMALL/LARGE/MICRO)
   - Prosecution status breakdown (SMALL/LARGE/MICRO declarations)
   - Transitions (STOL/LTOS), payment counts by milestone
   - **Ownership indicators:** Owned (green), Acquired (blue), Divested (amber with date)
6. Patent table shows individual patents with status, grant date, invention title, Events column
7. Events column shows micro chart timeline per patent (colored status line + icons for prosecution + maintenance events)
8. **Prosecution Payment Analysis** button → batch-analyzes all applications:
   - Count KPIs: Small/Micro/Large payment events (all-time + 10-year)
   - **Dollar Impact KPIs:** Amount Paid, Large Rate, Underpayment (all-time + 10-year)
   - Payment Summary pivot table (Year × Event Code)
   - Flagged Payments detail table (Small+Micro only) with Paid ($), Large ($), Underpay ($), Fee Category columns
   - All amounts calculated by the fee schedule engine using historical rates

### Flow B: Assignment Chain Lookup
1. Any patent number anywhere in the UI is clickable
2. Click → popup appears showing chronological assignment chain: Date | Type | Conveyance Text | Assignor(s) | Assignee(s)
3. "Type" column shows `normalized_type` (employee, divestiture, merger, etc.)
4. Multiple assignors/assignees show as separate lines (not semicolon-separated)
5. Popup fills from patent cell to right edge of viewport, full height

### Flow C: Prosecution Fee Investigation (3 phases)
1. **Locate Payors tab → Phase 1:** Enter min SMAL declarations → returns list of entities with counts
2. **Phase 2:** Click entity → shows all its applications with SMAL declarations
3. **Phase 3:** Click application → fetches fee documents from USPTO API → displays fee worksheets
4. **Fee extraction:** Button triggers Gemini Vision to extract fee codes from PDF invoice

### Flow D: Boolean Query Builder
1. Select tables (Patent File Wrapper, Patent Assignments, and/or Maintenance Fee Events)
2. Add conditions: field + operator + value
3. For name fields + CONTAINS: use boolean syntax (`+samsung +semiconductor -display`)
4. Execute → results table with sortable columns and column picker

### Flow E: MDM Name Management
1. Search for entity name using boolean syntax
2. Results show all matching names with frequency (how often they appear in USPTO data)
3. Names with a canonical mapping show their representative name
4. User can associate a variant name → canonical name to consolidate in all queries

### Flow F: Litigation Lookup
1. From the Entity Status portfolio view, litigation data auto-loads for visible patents
2. KPIs show: Total Cases, Unique Patents Litigated, Active Cases, Patent Assertion Entity cases
3. Litigation Details table shows case-level data with 14 columns
4. Data sourced from Unified Patents Elasticsearch API, cached in BigQuery for 30 days

---

## 7. Database — All Tables

Dataset: `uspto-data-app.uspto_data` | Location: `us-west1`

### Core Patent Data
| Table | Rows | Size | Purpose | Source |
|---|---|---|---|---|
| `patent_file_wrapper_v2` | 12.7M | 4.1 GB | One row per application: title, dates, applicant, inventor, examiner, status | PTFWPRE bulk XML |
| `maintenance_fee_events_v2` | 26.5M | 2.1 GB | Every maintenance fee event ever recorded — payment codes, declarations, transitions | PTMNFEE2 bulk file |
| `forward_citations` | 211.0M | 16.4 GB | All forward citation pairs (cited_patent → citing_patent) | PTBLXML weekly XML |

### Patent File Wrapper — Detail Tables (pfw_*)
All linked to `patent_file_wrapper_v2` via `application_number`. All **fully loaded** (mid-1990s to 2026).

| Table | Rows | Size | Purpose |
|---|---|---|---|
| `pfw_attorneys` | 586.5M | 40.5 GB | Attorney/agent assignments |
| `pfw_transactions` | 498.1M | 32.9 GB | Prosecution history events: SMAL, BIG., MICR declarations + all transaction codes |
| `pfw_pta_history` | 378.9M | 34.8 GB | PTA calculation history |
| `pfw_inventors` | 35.7M | 3.7 GB | All inventors per application (min app: 05603052) |
| `pfw_publications` | 18.2M | 0.9 GB | Publication details |
| `pfw_embedded_assignments` | 17.3M | 5.1 GB | Assignments embedded in prosecution record |
| `pfw_document_metadata` | 13.4M | 2.5 GB | Document filing metadata |
| `pfw_correspondence_address` | 12.7M | 1.6 GB | Correspondence addresses |
| `pfw_continuity` | 12.3M | 1.6 GB | Parent continuity relationships |
| `pfw_child_continuity` | 9.5M | 1.3 GB | Child continuation relationships |
| `pfw_applicants` | 7.5M | 0.8 GB | All applicants per application (min app: 08930379) |
| `pfw_patent_term_adjustment` | 6.0M | 0.4 GB | PTA calculations |
| `pfw_foreign_priority` | 4.7M | 0.2 GB | Foreign priority claims |

### Assignment Tables (v4 — normalized, current)
All 4 tables linked by `reel_frame` (STRING). **This is the active schema. v1/v2/v3 are gone.**

| Table | Rows | Size | Purpose |
|---|---|---|---|
| `pat_assign_records` | 9.07M | 1.9 GB | One row per assignment transaction. Has `normalized_type`, `review_flag`, `employer_assignment`. Partitioned by recorded_date month, clustered by reel_frame. |
| `pat_assign_assignors` | 23.6M | 0.8 GB | One row per assignor per assignment. Has `assignor_name`, `assignor_execution_date`. |
| `pat_assign_documents` | 17.0M | 1.8 GB | One row per patent property per assignment. Has `application_number`, `patent_number`, dates. Clustered by reel_frame + application_number. |
| `pat_assign_assignees` | 9.4M | 0.9 GB | One row per assignee per assignment. Has `assignee_name`, address fields. |

**`pat_assign_records` key columns:**
- `reel_frame` — primary link key
- `recorded_date` — date USPTO recorded the assignment
- `conveyance_text` — free text (e.g., "ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)")
- `conveyance_type` — coarse 8-bucket classification (legacy)
- `normalized_type` — fine-grained 14-category classification (see below)
- `review_flag` — TRUE for 15,806 records needing human review
- `employer_assignment` — TRUE for employee assignments, FALSE for all others

### MDM / Name Tables
| Table | Rows | Purpose |
|---|---|---|
| `entity_names` | 13.2M | Pre-computed unique entity names with frequency counts. Used for fast name search. |
| `name_unification` | 571 | **User-curated. NEVER modify programmatically.** Maps variant names → canonical representative names. |

### Support / Cache Tables
| Table | Rows | Purpose |
|---|---|---|
| `etl_log` | 16 | Pipeline run history (source, start, end, status, rows loaded) |
| `patent_litigation_cache` | 5,724 | Cached Unified Patents litigation results (30-day TTL) |
| `patent_litigation` | 123 | Distinct litigation cases |
| `prosecution_payment_cache` | 2 | Cached prosecution payment analysis results (has `cache_version` column; version 2 = fee-enriched) |
| `sec_leads_results` | 165 | SEC EDGAR enrichment output (early-stage feature) |

### normalized_type Values (pat_assign_records)
| Value | Count | Meaning |
|---|---|---|
| employee | 7,881,089 (86.9%) | Inventor → employer. Verified by matching assignors against pfw_inventors. |
| divestiture | 704,504 (7.8%) | Corporate entity selling/transferring patents to another corporate entity. |
| name_change | 173,915 (1.9%) | Entity renamed — no ownership change. |
| government | 105,941 (1.2%) | Government interest / Bayh-Dole confirmatory assignment. |
| security | 74,665 (0.8%) | Security interest — patent used as loan collateral. |
| merger | 51,389 (0.6%) | Merger/acquisition — target's assets transferred to acquirer. |
| release | 34,200 (0.4%) | Security interest fully terminated. |
| review | 15,806 (0.2%) | Uncertain — flagged for human review. |
| address_change | 13,821 (0.2%) | Address update only. |
| license | 6,490 (0.1%) | License granted. |
| correction | 3,420 (<0.1%) | Corrective recordation — no new rights. |
| court_order | 855 (<0.1%) | Court-ordered transfer (often bankruptcy). |
| partial_release | 596 (<0.1%) | Subset of collateralized assets released. |
| license_termination | 60 (<0.1%) | License terminated. |

**Classification pipeline (utils/conveyance_classifier.py):**
1. Regex on `conveyance_text` → non-assignment types (~461K)
2. Corporate assignor filter (all assignors are Inc/Corp/LLC/etc) → divestiture (~564K)
3. Inventor name matching via `pfw_inventors` → employee (~8M)
4. Majority-match rule: ≥50% of person-assignors match inventors → employee

---

## 8. External Services and Integrations

| Service | Purpose | Configuration |
|---|---|---|
| Google Cloud Run (us-central1) | Hosts the API + frontend (service: `uspto-api`) | Auto-deploy via GitHub Actions on push to main |
| Google BigQuery (us-west1) | Primary data store — 27 tables, ~155 GB | `GCP_PROJECT_ID`, `BIGQUERY_DATASET` env vars |
| Google Cloud Storage | Bulk data staging + prosecution invoice storage | `gs://uspto-bulk-staging/` |
| Google Cloud Run Jobs | ETL pipeline execution — 4 jobs | `uspto-update-ptblxml/pasdl/ptmnfee2/ptfwpre` |
| Google Cloud Scheduler | Triggers ETL jobs on schedule | See Section 11 |
| Vertex AI / Gemini | Natural language queries + PDF vision extraction | `GEMINI_API_KEY` env var (set in Cloud Run service) |
| USPTO Bulk Data API | Source of all patent data (PASDL, PTFWPRE, PTMNFEE2, PTBLXML) | `USPTO_API_KEY` env var (set in Cloud Run Jobs) |
| USPTO Document API | Fetches individual prosecution documents (fee sheets) | `https://api.uspto.gov/api/v1/patent/applications/{}/documents` |
| Unified Patents API | Patent litigation case data | Public Elasticsearch endpoint, rate-limited (200/batch, 2s delay) |
| GitHub Actions | CI/CD — deploys on push to main | Secrets: `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `GEMINI_API_KEY`, `API_SECRET` |

No paid third-party APIs beyond GCP. USPTO APIs are free with API key registration.

---

## 9. Authentication and Access

- **No user authentication** — the app is public (`--allow-unauthenticated` on Cloud Run)
- **GCP auth:** Cloud Run uses a service account with BigQuery access. GitHub Actions uses Workload Identity Federation (keyless auth) — no long-lived service account keys.
- **USPTO API:** Key `inbsszqfwwnkjfebpibunnbllbygqz` used in ETL jobs and `api/routers/prosecution.py`

---

## 10. Testing

Test files in `tests/`:
- `test_api_endpoints.py` — API endpoint smoke tests
- `test_conveyance_classifier.py` — Tests for `classify_conveyance_normalized()`
- `test_parse_helpers.py` — Tests for XML parsing helpers
- `test_parse_v4.py` — Tests for v4 assignment parser
- `test_patent_number.py` — Tests for `normalize_patent_number()`

**Run tests:** `python -m pytest tests/` from the repo root.

**Coverage gaps:**
- No tests for BigQuery queries (would require mocking)
- No E2E browser tests
- No tests for entity_status, prosecution, litigation, or fee_schedule modules
- No tests for the fee calculation engine (utils/fee_schedule.py)

---

## 11. Deployment

### API Service (Cloud Run)
```bash
# Manual deploy (most common operation):
gcloud run deploy uspto-api --source=. --project=uspto-data-app --region=us-central1 --allow-unauthenticated

# Auto-deploy: push to main branch triggers GitHub Actions
```

### ETL Container (Cloud Run Jobs)
```bash
# Rebuild ETL image (after changing any ETL code):
gcloud builds submit --project=uspto-data-app --config=cloudbuild-etl.yaml .

# After rebuilding, update each job to pick up new image:
gcloud run jobs update uspto-update-pasdl --image=us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest --project=uspto-data-app --region=us-central1
# (repeat for each of the 4 jobs)
```

### Environment Variables (Cloud Run Service)
```
GCP_PROJECT_ID=uspto-data-app
BIGQUERY_DATASET=uspto_data
GEMINI_API_KEY=<secret>
API_SECRET=<secret>
ALLOWED_ORIGINS=*
```

### Environment Variables (Cloud Run Jobs)
```
GCP_PROJECT_ID=uspto-data-app
BIGQUERY_DATASET=uspto_data
GCS_BUCKET=uspto-bulk-staging
USPTO_API_KEY=inbsszqfwwnkjfebpibunnbllbygqz
```

### ETL Schedule (Cloud Scheduler, America/Los_Angeles)
| Job | Schedule | What it updates |
|---|---|---|
| `uspto-update-ptblxml` | Sundays 2am | Forward citations (weekly files) |
| `uspto-update-pasdl` | Mondays 3am | Patent assignments (daily files) — includes `resolve_assignment_pending()` post-load for normalized_type |
| `uspto-update-ptmnfee2` | 1st of month 4am | Maintenance fees |
| `uspto-update-ptfwpre` | 15th of month 1am | Patent file wrapper |

---

## 12. File Structure

```
/
├── api/
│   ├── main.py                    # FastAPI app + router registration
│   ├── config.py                  # Settings class — all BQ table references
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py             # Pydantic request/response models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── mdm.py                 # MDM name normalization + _parse_boolean_query()
│   │   ├── query.py               # Boolean query builder
│   │   ├── ai_assistant.py        # Gemini AI assistant
│   │   ├── citations.py           # Forward citations
│   │   ├── assignments.py         # Assignment chain popup
│   │   ├── entity_status.py       # Entity status analytics + fee calculation (LARGEST backend file)
│   │   ├── prosecution.py         # Prosecution fee investigation (3-phase)
│   │   ├── litigation.py          # Patent litigation via Unified Patents
│   │   ├── etl_log.py             # ETL pipeline log
│   │   └── sec_leads.py           # SEC EDGAR leads (early stage)
│   └── services/
│       ├── __init__.py
│       ├── bigquery_service.py    # bq_service singleton, run_query(), expand_name_for_query()
│       └── gemini_service.py      # Gemini AI wrapper
├── etl/
│   ├── update_pipeline.py         # ETL orchestrator — entrypoint for Cloud Run Jobs
│   ├── parse_assignments_xml_v4.py # PASDL XML parser (current) — outputs normalized_type
│   ├── normalize_conveyance.py    # One-time migration (already run — do not re-run)
│   ├── reload_assignments_v4.py   # Backfile reload with .done markers (already run)
│   ├── parse_pfw.py               # PTFWPRE master XML parser (calls sub-parsers)
│   ├── parse_file_wrapper.py      # PTFWPRE biblio/transactions parser
│   ├── parse_maintenance_fees_v2.py # PTMNFEE2 parser
│   ├── parse_ptblxml.py           # PTBLXML citation parser
│   ├── backfill_pfw.py            # PFW backfill (already run — complete)
│   ├── populate_entity_names_v2.py # Rebuilds entity_names table
│   ├── download_pasdl.py          # Download PASDL files from USPTO
│   ├── download_pasyr.py          # Download PASYR (annual assignment) files
│   ├── download_ptblxml.py        # Download PTBLXML citation files
│   └── (legacy parsers: parse_assignments_xml.py, v2, v3, parse_maintenance_fees.py)
├── utils/
│   ├── __init__.py
│   ├── fee_schedule.py            # Fee calculation engine — rates, PAY/PROC/REV codes, dollar lookup
│   ├── conveyance_classifier.py   # classify_conveyance() + classify_conveyance_normalized()
│   └── patent_number.py           # normalize_patent_number() — strips punctuation/prefixes
├── frontend/
│   ├── index.html                 # SPA shell — 9 tabs, script/CSS references with ?v= versions
│   ├── css/styles.css             # All styles
│   └── js/
│       ├── app.js                 # Shared: tab switching, assignment popup, utilities
│       ├── mdm.js                 # MDM tab
│       ├── query_builder.js       # Query builder tab
│       ├── ai_assistant.js        # AI assistant tab
│       ├── citations.js           # Forward citations tab
│       ├── entity_status.js       # Entity status tab — LARGEST frontend file (~1600 lines)
│       ├── prosecution.js         # Prosecution fees tab
│       ├── etl_log.js             # Update log tab
│       └── sec_leads.js           # SEC leads tab
├── patent_analyzer/               # SEC EDGAR enrichment pipeline (separate feature)
│   ├── __init__.py
│   ├── apollo_enrichment.py       # Apollo.io contact enrichment
│   ├── board_extraction.py        # Board member extraction
│   ├── documents.py               # Document processing
│   ├── report_generator.py        # Report output
│   ├── run_pipeline.py            # Pipeline entrypoint
│   ├── scoring.py                 # Lead scoring
│   └── sec_edgar.py               # SEC EDGAR API client
├── tools/
│   └── extract_fee_codes.py       # Utility: extract fee codes from prosecution data
├── tests/
│   ├── __init__.py
│   ├── test_api_endpoints.py
│   ├── test_conveyance_classifier.py
│   ├── test_parse_helpers.py
│   ├── test_parse_v4.py
│   └── test_patent_number.py
├── Dockerfile                     # API service image (python:3.11-slim, port 8080)
├── Dockerfile.etl                 # ETL job image
├── cloudbuild-etl.yaml            # Cloud Build config for ETL image
├── requirements.txt               # Python dependencies
├── .github/workflows/deploy.yml   # GitHub Actions CI/CD
├── .gitignore
├── CLAUDE.md                      # Project-level AI assistant instructions (read first)
├── HANDOFF316.md                  # This file
└── historical_fees_table_since_2006.csv  # Expert-provided fee schedule data (reference)
```

---

## 13. Configuration Files

| File | Purpose |
|---|---|
| `api/config.py` | All BigQuery table references as Python properties. Single source of truth for table names. Has a `prosecution_payment_cache_table` property for the fee cache. |
| `Dockerfile` | API service — `python:3.11-slim`, exposes 8080, runs uvicorn |
| `Dockerfile.etl` | ETL jobs — installs gsutil/bq CLI tools in addition to Python deps |
| `cloudbuild-etl.yaml` | Builds and pushes ETL Docker image to Artifact Registry |
| `.github/workflows/deploy.yml` | GitHub Actions: on push to main → build → push → deploy to Cloud Run. Uses Workload Identity Federation. |
| `requirements.txt` | fastapi, uvicorn, google-cloud-bigquery, google-cloud-aiplatform, pydantic, ijson, requests, beautifulsoup4, lxml |
| `.env.example` | Template showing required env vars for local development |
| `.claude/settings.local.json` | Claude Code local settings |

---

## 14. Environment and Dependencies

**Python:** 3.11 (pinned in Dockerfile)

**Key packages (requirements.txt):**
- `fastapi==0.109.2` + `uvicorn==0.27.1` — web framework
- `google-cloud-bigquery>=3.17.0` — BigQuery client
- `google-cloud-aiplatform>=1.38.0` — Vertex AI / Gemini
- `pydantic>=2.0.0` — request/response validation
- `ijson>=3.2.0` — streaming JSON parser for large USPTO files
- `lxml>=5.0.0`, `beautifulsoup4>=4.12.0` — XML parsing
- `requests>=2.31.0` — HTTP client (USPTO API, Unified Patents)
- `python-dotenv>=1.0.0` — env var loading
- `google-cloud-secret-manager>=2.16.0` — GCP secrets

**Local dev env vars required:**
```
GCP_PROJECT_ID=uspto-data-app
BIGQUERY_DATASET=uspto_data
GEMINI_API_KEY=<from GCP Secret Manager>
```

---

## 15. Accounts and Services Required

| Account / Service | Used for |
|---|---|
| Google Cloud (project: `uspto-data-app`) | Everything — BigQuery, Cloud Run, GCS, Scheduler, Artifact Registry, Vertex AI |
| GitHub (org: InnovationAccess) | Source code, CI/CD via GitHub Actions |
| USPTO Bulk Data API | Downloading PASDL, PTFWPRE, PTMNFEE2, PTBLXML bulk files |
| USPTO Document API | Fetching individual prosecution documents (fee sheets) |
| Unified Patents | Public Elasticsearch API for patent litigation data (no account needed) |

No paid third-party APIs beyond GCP. USPTO APIs are free with API key registration.

---

## 16. Related Projects and Systems

- **`patent_analyzer/`** — A separate pipeline (within this repo) for SEC EDGAR company enrichment. Uses Apollo.io API for contact data. Feeds into the SEC Leads tab. Early-stage / partially built.
- **InnovationAccess GitHub org** — Parent org for this project.
- **Synpathub GitHub** — Owner's personal GitHub; some related tooling may live there.
- **historical_fees_table_since_2006.csv** — Expert-provided fee schedule reference file. Contains 102 event codes × 7 fee periods × 3 entity sizes. Used to build the `FEE_RATES` table in `utils/fee_schedule.py`. Keep for reference; do not delete.

---

## 17. Git State

**Main branch:** `main` — auto-deploys to Cloud Run via GitHub Actions
**Current working branch:** `claude/condescending-gagarin` — all session work is here, pushed to GitHub
**Open PRs:** None formally opened. Session branch has ~25 commits ahead of main that need to be merged.

**Recent commits on this branch (newest first):**
```
e57351a  Add prosecution fee calculation engine with dollar amounts and underpayment analysis
a4a7ac3  Add 10-year KPI row and tooltips for prosecution payment analysis
c7d8862  Add BigQuery cache for prosecution payment analysis
dc6b252  Add prosecution payment analysis — unified timeline sparklines
c384c65  Fix Events micro charts not loading for litigated patents filter
ca21eca  Add litigation details table with 14-column case view and 5 KPIs
9f0a36e  Add patent litigation integration from Unified Patents API + fix table layout
3fc393d  Add 37 CFR 1.28(c) column to payment table (M1559 events)
bc4c8b7  Filter portfolio inbound assignments to ownership-transfer types only
66408f4  Redesign portfolio KPIs: Filed/Acquired/Divested/Expired/Owned for granted + pending
65cad5d  Add normalized_type column to assignment popup, rename Type to Conveyance Text
f45405d  Add ownership window filtering to Entity Status portfolio
```

**Convention:** Session work happens on `claude/<worktree-name>` branches. Merge to main when stable. GitHub Actions deploys on push to main.

**IMPORTANT SESSION PROTOCOL:** At session end, merge the session branch to `main` before closing — handoff documents left on feature branches are invisible to the next session.

---

## 18. Workarounds and Gotchas

### BigQuery
- **ALL `bq` CLI commands MUST include `--location=us-west1`** — omitting causes silent failures (jobs appear to succeed but do nothing)
- Use `--use_legacy_sql=false` NOT `--nouse_legacy_sql` (the latter is invalid syntax)
- Upload files one at a time with `gsutil cp` (NOT `gsutil -m cp`) — parallel uploads fail silently
- Load BQ files individually (NOT wildcard `*.jsonl.gz`) — BQ rejects wildcard patterns
- NEVER concatenate `.gz` files — creates multi-stream gzip that BigQuery cannot read
- Date `0000-01-01` causes BQ load failures — all parsers validate year range 1700-2100
- `bq load --nosync` returns immediately — failures are silent. Always verify row counts after loading
- INFORMATION_SCHEMA is region-specific; use `--location=us-west1` for all schema queries
- When adding new columns to parser output, ALWAYS `ALTER TABLE ADD COLUMN` before loading — BQ rejects JSON fields not in schema
- Use `--schema_update_option=ALLOW_FIELD_ADDITION` in `bq load` to auto-add new fields
- NEVER truncate a table and reload from only the latest data file — use targeted `DELETE WHERE source_file IN (...)` to preserve historical data

### Cloud Run
- `/tmp` is backed by RAM (tmpfs) — large file downloads consume memory, not disk
- For files >1GB: set memory to at least 2x file size + 2GB overhead
- Cloud Run resolves container image tags to digests at job UPDATE time — after rebuilding ETL image, you MUST run `gcloud run jobs update --image=...` for each job
- Delete large temp files immediately after use to free memory

### USPTO Data Parsing
- Fields named `*Bag` in USPTO JSON are usually arrays but sometimes dicts or scalars — always use `_as_list()` helper
- Always check `isinstance(item, dict)` before calling `.get()` on bag items
- `KeyError(0)` prints as just "0" — happens when a dict is treated as a list
- Isolate each parsing function in its own try/except — one bad field must not kill the whole record
- USPTO API returns HTTP 429 (rate limit) — retry with backoff

### Assignment Queries
- NEVER search `WHERE app_num = @id OR patent_num = @id` — causes collisions
- Assignment chain lookup: first resolve patent_number → application_number via `patent_file_wrapper_v2`, then find reel_frames via `pat_assign_documents`
- `pat_assign_assignors` and `pat_assign_assignees` are separate tables — JOIN both via reel_frame, aggregate with STRING_AGG to avoid cross-product duplicates

### Frontend
- **Bump `?v=N` cache-busting version in `index.html` EVERY TIME a JS or CSS file changes** — browsers aggressively cache these
- MDM table has its own custom sort — do NOT add `enableTableSorting()` to it (breaks sorting)
- Always call `stampOriginalOrder(tbl)` before `enableTableSorting(tbl)` when populating dynamic tables

### Fee Calculation
- **UAIA effective date is Dec 29, 2022** (NOT Jan 1, 2023) — critical for rate lookup
- **ABNF is PROC, not PAY** — expert corrected their own initial classification
- **Pre-2013: no RCE tier distinction** — use flat rate for all RCEs regardless of ordinal
- **Pre-2013: micro entity did not exist** — micro rates = small rates
- **Appeal brief fee ($0 post-2012)** — AP.B was a paid code before 2013 ($500), eliminated after
- **P005 is a compound fee** — issue fee + petition for revival, NOT just the issue fee
- **P007 is a $420 processing fee proxy** — NOT the $2,200 petition for revival rate
- **FEE. must be deduped** — only count if no other PAY code within ±3 days for same application

---

## 19. Decisions That Were Rejected

1. **Fabricated transition colors in micro charts:** When a patent has a STOL (small→large) event, the timeline line is all red (large) from grant. Suggestion was to infer a "from" color (green) before the STOL event. **Rejected** — "destroys the integrity of the data. You cannot insert events that do not exist." Transitions show as gray dots instead.

2. **Single OR search across patent_number + application_number:** `WHERE field = @x OR other_field = @x` causes number space collisions. Rejected in favor of always resolving to application_number first.

3. **Wildcard-only PTBLXML parsing (annual files):** Annual PTBLXML citation files (2002–2005) are too large for in-memory XML parsing. Rejected in favor of weekly files only.

4. **Parallel gsutil uploads (`gsutil -m cp`):** Causes silent upload failures. Rejected in favor of sequential one-at-a-time uploads.

5. **Using `entity_status` column for status:** USPTO populates this column inconsistently. Rejected — entity status must always be DERIVED from event codes.

6. **Counting all 102 prosecution codes as payments:** Expert confirmed this would inflate figures 3-5x. Only ~22 PAY codes trigger actual fee payments. The rest are procedural or reversals.

7. **Using entity_status column for fee calculation:** Expert confirmed the only reliable source is the event code → entity status mapping (M1/F17=LARGE, M2/F27=SMALL, M3=MICRO).

8. **Source 4 (`first_applicant_name`) in portfolio query:** Was originally needed when pfw_applicants was incomplete. Now redundant since pfw_applicants covers back to mid-1990s. Kept temporarily for safety but can be removed.

---

## 20. Known Limitations and Constraints

1. **Prosecution payment cache is nearly empty** — Only 2 rows in `prosecution_payment_cache`. The cache works (tested), but most users haven't re-analyzed since cache_version 2 was deployed. Old cache entries (version NULL or 1) are treated as stale and re-analyzed.

2. **SEC Leads tab is early-stage** — The `patent_analyzer/` pipeline is partially built and not fully integrated with the main platform.

3. **No user authentication** — The app is publicly accessible. Appropriate for internal tool use; would need auth layer before public/commercial deployment.

4. **PTFWPRE coverage** — Only covers recent years for incremental updates. Historical file wrapper data comes from the completed backfill.

5. **`assignments_table` property in config.py** — Still points to `patent_assignments_v3` (the old flat table, now dropped). Labeled "Kept for rollback" but the table no longer exists. If any code references `settings.assignments_table`, it would fail. All active code uses `assign_records_table` etc.

6. **Query Builder missing some fields** — `normalized_type` not yet available as a filterable field in the Boolean Query Builder.

7. **Review queue** — 15,806 assignments flagged `review_flag=TRUE` have no UI for reviewing/classifying them.

8. **Fee calculation precision** — The fee schedule uses exact USPTO rates for 7 periods, but doesn't account for very early filings (pre-2006 rates not included; earliest period is used as fallback).

---

## 21. Open Issues and Bugs

**None currently blocking.** Most recent bugs fixed in prior sessions:
- ~~Boolean Query Builder CONTAINS always returning 0 results~~ — Fixed
- ~~Assignment popup showing duplicate rows~~ — Fixed
- ~~Micro charts not loading for >200 patents~~ — Fixed
- ~~Events micro charts not loading for litigated patents filter~~ — Fixed

**Potential issue to investigate:**
- The `assignments_table` property in `api/config.py` still points to `patent_assignments_v3` (dropped table). Should be removed or updated.

---

## 22. Work In Progress

**The fee calculation engine was completed and deployed in this session (2026-03-18).** No work is left in an unfinished state.

**What was delivered:**
- `utils/fee_schedule.py` — complete fee calculation engine
- Backend integration in `entity_status.py` — prosecution timeline API enriches payments with dollar amounts
- Frontend integration in `entity_status.js` — Dollar Impact KPI row, detail table with dollar columns
- BigQuery `prosecution_payment_cache` table has `cache_version` column for cache invalidation
- PROS_PAY_DESCRIPTIONS updated with official USPTO Fee Schedule names

---

## 23. Next Steps (Prioritized)

1. **Merge session branch to main** — The `claude/condescending-gagarin` branch has ~25 commits ahead of main covering: ownership window filtering, assignment popup improvements, litigation integration, prosecution payment analysis, and the fee calculation engine. Create a PR and merge.

2. **Add `normalized_type` to Query Builder** — Add it to `_TABLE_FIELDS["patent_assignments"]` in `query.py` so users can filter assignments by type (e.g., only divestitures, only mergers).

3. **Build the "review" queue UI** — 15,806 assignments are flagged `review_flag=TRUE`. A simple UI to view and manually classify these would complete the normalization pipeline.

4. **Remove redundant source 4 from portfolio query** — Remove `first_applicant_name` UNION arm from `get_applicant_portfolio()`.

5. **Add fee calculation tests** — `utils/fee_schedule.py` has no test coverage. Write tests for `get_fee()`, `get_period_index()`, and `calculate_payment_fees()` with known-good inputs/outputs.

6. **Remove stale `assignments_table` property** — Clean up `api/config.py` to remove the reference to the dropped `patent_assignments_v3` table.

7. **Extension tier support** — Currently only 1-month extension (1.17(a)(1)) is implemented. Expert provided 5-tier extension rates but the implementation uses only the 1st tier since event codes don't differentiate tiers. Future enhancement: use date-diff calculation to determine extension tier.

8. **Maintenance fee dollar amounts** — Apply the same fee schedule approach to maintenance fee payments (3.5yr, 7.5yr, 11.5yr) to calculate post-grant underpayment amounts. This would complete the full-lifecycle underpayment analysis.

---

## 24. Commands and Context

**Deploy API (most common operation):**
```bash
gcloud run deploy uspto-api --source=. --project=uspto-data-app --region=us-central1 --allow-unauthenticated
```

**Rebuild ETL image (after changing ETL code):**
```bash
gcloud builds submit --project=uspto-data-app --config=cloudbuild-etl.yaml .
# Then update each Cloud Run Job to pick up new image
```

**Run a BigQuery query:**
```bash
bq query --location=us-west1 --project_id=uspto-data-app --use_legacy_sql=false "SELECT ..."
```

**Check a table's row count:**
```bash
bq query --location=us-west1 --project_id=uspto-data-app --use_legacy_sql=false "SELECT COUNT(*) FROM \`uspto-data-app.uspto_data.TABLE_NAME\`"
```

**Check normalized_type distribution:**
```bash
bq query --location=us-west1 --project_id=uspto-data-app --use_legacy_sql=false "SELECT normalized_type, COUNT(*) cnt FROM \`uspto-data-app.uspto_data.pat_assign_records\` GROUP BY 1 ORDER BY 2 DESC"
```

**Test prosecution fee calculation API:**
```bash
curl -s -X POST 'https://uspto-api-1094570457455.us-central1.run.app/api/entity-status/prosecution-timelines' \
  -H 'Content-Type: application/json' \
  -d '{"application_numbers": ["17123079","17126081"]}'
```

**Git workflow:**
```bash
git pull origin main          # Always pull first
git push                      # Push to current branch
# To merge session work to main, create a PR on GitHub
```

**Live URL:** https://uspto-api-1094570457455.us-central1.run.app
**API docs (Swagger):** https://uspto-api-1094570457455.us-central1.run.app/docs
**GitHub repo:** https://github.com/InnovationAccess/SmallEntityPayments
