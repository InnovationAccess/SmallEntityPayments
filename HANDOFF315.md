# HANDOFF315 — USPTO Data Platform Complete System Snapshot
**Date:** 2026-03-17 | **Session:** 3 | **Branch:** claude/romantic-ramanujan

This document is a complete system snapshot written for a new session that has never seen this project. Read this file first, then start working.

---

## 1. System Overview

**SmallEntityPayments** (internally called "USPTO Data Platform") is a web-based patent analytics platform built by a patent attorney / IP strategist. It ingests official USPTO bulk data into Google BigQuery, then provides a browser UI for patent professionals to research, cross-reference, and analyze that data.

**Repository:** https://github.com/InnovationAccess/SmallEntityPayments
**Live URL:** https://uspto-api-1094570457455.us-central1.run.app
**GCP Project:** `uspto-data-app`
**BigQuery Dataset:** `uspto_data` (region: `us-west1`)

**Core business purpose:** Help patent attorneys and IP monetization specialists:
- Identify which companies are paying maintenance fees as small or micro entities (potentially underpaying)
- Track patent ownership chains from original inventor through all assignments
- Analyze entity status transitions (small→large) that signal growth
- Investigate prosecution payment patterns and fee declarations
- Generate leads for licensing or assertion campaigns

---

## 2. Domain Terminology

| Term | Definition |
|---|---|
| **Entity status** | USPTO fee classification: LARGE (full fees), SMALL (50% discount), MICRO (80% discount). Determined by the type of maintenance fee paid — NOT the entity_status column. |
| **Maintenance fee** | Periodic fees paid to keep a patent in force. Due at 3.5, 7.5, and 11.5 years after grant. |
| **Event code** | USPTO code on a maintenance fee event. `M1xxx/F17xx` = LARGE, `M2xxx/F27xx` = SMALL, `M3xxx` = MICRO. Payment codes: `M*551`=3.5yr, `M*552`=7.5yr, `M*553`=11.5yr. |
| **Declaration code** | `SMAL`, `BIG.`, `MICR` — entity status declarations filed during prosecution (pre-grant). Appear in `pfw_transactions`. |
| **Transition code** | `STOL` (small→large), `LTOS` (large→small), `STOM` (small→micro), `MTOS` (micro→small). Appear in `maintenance_fee_events_v2`. |
| **reel_frame** | Unique identifier for a USPTO assignment recordation (e.g., "047000/0001"). Links all 4 assignment tables together. |
| **application_number** | The universal cross-table key. Every patent asset has one. Patent numbers only exist for granted patents. NEVER search both simultaneously. |
| **normalized_type** | Fine-grained classification of an assignment's conveyance text into 14 categories (employee, divestiture, merger, security, etc.). New as of 2026-03-17. |
| **conveyance_text** | Free-text description of what an assignment conveys (e.g., "ASSIGNMENT OF ASSIGNORS INTEREST"). Source for normalized_type classification. |
| **employer_assignment** | Boolean: TRUE when inventors assign to their employer. The 86.9% majority case. |
| **MDM** | Master Data Management — the name normalization system. Maps variant company names (e.g., "SAMSUNG ELEC CO LTD" → "Samsung Electronics Co., Ltd.") via the `name_unification` table. |
| **reel/frame** | Same as reel_frame — the two-part assignment docket number. |
| **PASDL** | USPTO bulk data product: Patent Assignment Daily data (daily assignment XML files). |
| **PTFWPRE** | USPTO bulk product: Patent File Wrapper Prosecution (patent prosecution history XML). |
| **PTBLXML** | USPTO bulk product: Patent Grant Citations Bibliographic XML (weekly forward citation files). |
| **PTMNFEE2** | USPTO bulk product: Patent Maintenance Fee Events (full maintenance fee history). |
| **Prosecution phase** | Period from filing to patent grant. Entity status during this phase is tracked via `pfw_transactions` declarations. |
| **Post-grant phase** | Period after patent grant. Entity status tracked via maintenance fee payment codes. |

---

## 3. Architecture

```
[USPTO Bulk Data] → [Cloud Run ETL Jobs] → [GCS Staging] → [BigQuery]
                                                               ↓
[Browser] ← [Cloud Run API (FastAPI)] ← [BigQuery queries]
```

**Technologies:**
- **Backend:** Python 3.11, FastAPI, Uvicorn — served on Google Cloud Run (us-central1)
- **Frontend:** Vanilla JS (ES modules), no build step — served as static files from the same Cloud Run container
- **Database:** Google BigQuery (us-west1) — dataset `uspto_data`, ~1.0 billion rows across 24 tables
- **AI:** Vertex AI / Gemini (via google-cloud-aiplatform) for natural-language queries; Gemini Vision for PDF fee sheet extraction
- **ETL:** Cloud Run Jobs (separate container, `Dockerfile.etl`) triggered by Cloud Scheduler
- **Storage:** Google Cloud Storage `gs://uspto-bulk-staging/` — staging area for bulk downloads and prosecution invoices
- **CI/CD:** GitHub Actions (`.github/workflows/deploy.yml`) — auto-deploys to Cloud Run on push to `main`

**Key architectural rule:** Cross-table joins always use `application_number` as the key — never `patent_number`. A patent number only exists after grant; application_number exists from filing. Searching a string across both simultaneously causes collisions (e.g., patent 11172434 collides with application 11/172,434).

---

## 4. Backend — API Routes

**Entry point:** `api/main.py` — registers all 9 routers and mounts the frontend as `/static`

| Router file | Prefix | What it does |
|---|---|---|
| `api/routers/mdm.py` | `/mdm` | Entity name search, MDM CRUD (add/remove name associations) |
| `api/routers/query.py` | `/query` | Boolean query builder — multi-table parametrized BigQuery SQL |
| `api/routers/ai_assistant.py` | `/ai` | Natural language queries via Gemini; generates + executes BigQuery SQL |
| `api/routers/citations.py` | `/api/forward-citations` | Forward citation lookup with examiner/applicant breakdown |
| `api/routers/assignments.py` | `/api/assignments` | Assignment chain popup — resolves patent# → app# → reel_frames → chain |
| `api/routers/entity_status.py` | `/api/entity-status` | Entity status analytics, portfolio KPIs, micro chart timelines |
| `api/routers/prosecution.py` | `/api/prosecution` | Prosecution fee investigation (3-phase workflow) |
| `api/routers/etl_log.py` | `/api/etl-log` | ETL pipeline run history |
| `api/routers/sec_leads.py` | `/api/sec-leads` | SEC EDGAR lead enrichment (early stage) |

**Key endpoints:**

```
GET  /api/assignments/{patent_number}/chain      Assignment chain for a patent
GET  /api/forward-citations/{patent_number}      Forward citation list
POST /api/entity-status/applicant-portfolio      Full portfolio analysis for an entity
POST /api/entity-status/bulk-timelines           Micro chart timeline data (batched, max 200/call)
POST /api/entity-status/conversion-search        Find patents that changed entity status
GET  /api/entity-status/summary                  Aggregate entity status distribution
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

**Shared services:**
- `api/services/bigquery_service.py` — `bq_service` singleton, `run_query()`, `search_entity_names()`, `expand_name_for_query()`
- `api/services/gemini_service.py` — Gemini AI wrapper for SQL generation and PDF vision
- `api/config.py` — `settings` object with all BigQuery table references as properties

---

## 5. Frontend

**Entry:** `frontend/index.html` — single-page app with 8 tabs

| Tab | JS file | What it does |
|---|---|---|
| MDM Name Normalization | `frontend/js/mdm.js` | Search entity names, view canonical mappings, add/remove associations |
| Boolean Query Builder | `frontend/js/query_builder.js` | Build multi-condition queries against 3 tables |
| AI Assistant | `frontend/js/ai_assistant.js` | Chat interface for natural language patent queries |
| Forward Citations | `frontend/js/citations.js` | Citation lookup with year chart, examiner/applicant breakdown |
| Entity Status | `frontend/js/entity_status.js` | **Main analytics tab** — conversion search, applicant portfolio with micro chart timelines |
| Locate Payors | `frontend/js/prosecution.js` | 3-phase prosecution fee investigation |
| Update Log | `frontend/js/etl_log.js` | ETL pipeline monitoring |
| SEC Leads | `frontend/js/sec_leads.js` | SEC EDGAR lead generation (early stage) |

**Shared JS:** `frontend/js/app.js` — assignment chain popup, tab switching, shared utilities (`escHtml`, `buildInteractiveTable`, `enableAssignmentPopup`, `enableTableSorting`)

**Current cache-busting versions (MUST be bumped when files change):**
```
styles.css?v=23     app.js?v=16         mdm.js?v=8
query_builder.js?v=9  ai_assistant.js?v=8  citations.js?v=5
entity_status.js?v=19  prosecution.js?v=9   etl_log.js?v=3
```

**Frontend patterns:**
- All tables have sticky headers, sortable columns (click header), column picker dropdowns
- Any patent number in any table is clickable → opens assignment chain popup
- Assignment popup: movable (drag header), resizable (drag corner), fills right side of viewport
- Name search uses MDM boolean syntax: `+elect +tele -inc` (CONTAINS auto-wraps with `%`)
- Entity Status portfolio does NOT require a PR — it's a single page, no routing

---

## 6. Key User Flows

### Flow A: Entity Status Analysis (most important)
1. User goes to **Entity Status** tab
2. Types entity name in "Applicant Portfolio" input (e.g., "ETRI")
3. Clicks "Find" → dropdown shows matching canonical names with frequency counts
4. Selects a name → portfolio query runs
5. KPI cards show: total patents, total applications, prosecution status breakdown (SMALL/LARGE/MICRO), post-grant status breakdown, transitions (STOL/LTOS), payment counts by milestone
6. Patent table shows individual patents with status, grant date, invention title, Events column
7. Events column shows micro chart timeline per patent (prosecution + maintenance events)
8. Clicking any patent number opens the assignment chain popup

### Flow B: Assignment Chain Lookup
1. Any patent number anywhere in the UI is clickable
2. Click → popup appears showing chronological assignment chain: Date | Assignor(s) | Type | Assignee(s)
3. Multiple assignors/assignees show as separate lines (not semicolon-separated)
4. Popup fills from patent cell to right edge of viewport, full height

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

---

## 7. Database — All Tables

Dataset: `uspto-data-app.uspto_data` | Location: `us-west1`

### Core Patent Data
| Table | Rows | Purpose | Source |
|---|---|---|---|
| `patent_file_wrapper_v2` | ~10M | One row per application: title, dates, applicant, inventor, examiner, status | PTFWPRE bulk XML |
| `maintenance_fee_events_v2` | ~350M | Every maintenance fee event ever recorded — payment codes, declarations, transitions | PTMNFEE2 bulk file |
| `forward_citations` | ~600M | All forward citation pairs (cited_patent → citing_patent) | PTBLXML weekly XML |

### Patent File Wrapper — Detail Tables (pfw_*)
All linked to `patent_file_wrapper_v2` via `application_number`. All **fully loaded** (mid-1990s to 2026).

| Table | Rows | Purpose |
|---|---|---|
| `pfw_applicants` | 7.47M | All applicants per application (min app: 08930379) |
| `pfw_inventors` | 35.7M | All inventors per application (min app: 05603052) |
| `pfw_transactions` | ~100M+ | Prosecution history events: SMAL, BIG., MICR declarations + all other transaction codes |
| `pfw_attorneys` | — | Attorney/agent assignments |
| `pfw_child_continuity` | — | Child continuation relationships |
| `pfw_correspondence_address` | — | Correspondence addresses |
| `pfw_document_metadata` | — | Document filing metadata |
| `pfw_embedded_assignments` | — | Assignments embedded in prosecution record |
| `pfw_foreign_priority` | — | Foreign priority claims |
| `pfw_patent_term_adjustment` | — | PTA calculations |
| `pfw_pta_history` | — | PTA calculation history |
| `pfw_publications` | — | Publication details |

### Assignment Tables (v4 — normalized, current)
All 4 tables linked by `reel_frame` (STRING). **This is the active schema. v1/v2/v3 are gone.**

| Table | Rows | Purpose |
|---|---|---|
| `pat_assign_records` | 9.07M | One row per assignment transaction. Has `normalized_type`, `review_flag`, `employer_assignment`. Partitioned by recorded_date month, clustered by reel_frame. |
| `pat_assign_assignors` | ~15M | One row per assignor per assignment. Has `assignor_name`, `assignor_execution_date`. |
| `pat_assign_assignees` | ~10M | One row per assignee per assignment. Has `assignee_name`, address fields. |
| `pat_assign_documents` | ~13M | One row per patent property per assignment. Has `application_number`, `patent_number`, dates. Clustered by reel_frame + application_number. |

**`pat_assign_records` key columns:**
- `reel_frame` — primary link key
- `recorded_date` — date USPTO recorded the assignment
- `conveyance_text` — free text (e.g., "ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)")
- `conveyance_type` — coarse 8-bucket classification (legacy)
- `normalized_type` — fine-grained 14-category classification (NEW, see below)
- `review_flag` — TRUE for 15,806 records needing human review
- `employer_assignment` — TRUE for employee assignments, FALSE for all others

### MDM / Name Tables
| Table | Rows | Purpose |
|---|---|---|
| `entity_names` | 7.68M | Pre-computed unique entity names with frequency counts. Used for fast name search. |
| `name_unification` | ~545 | **User-curated. NEVER modify programmatically.** Maps variant names → canonical representative names. |

### Support Tables
| Table | Rows | Purpose |
|---|---|---|
| `etl_log` | Growing | Pipeline run history (source, start, end, status, rows loaded) |
| `sec_leads_results` | Small | SEC EDGAR enrichment output (early-stage feature) |

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
| partial_release | 596 (<0.1%) | Partial release of collateralized assets. |
| license_termination | 60 (<0.1%) | License terminated. |

**Classification pipeline:**
1. Regex on `conveyance_text` → non-assignment types (~461K)
2. Corporate assignor filter (all assignors are Inc/Corp/LLC/etc) → divestiture (~564K)
3. Inventor name matching via `pfw_inventors` → employee (~8M)
4. Majority-match rule: ≥50% of person-assignors match inventors → employee

---

## 8. External Services and Integrations

| Service | Purpose | Configuration |
|---|---|---|
| Google Cloud Run (us-central1) | Hosts the API + frontend | Auto-deploy via GitHub Actions |
| Google BigQuery (us-west1) | Primary data store | `GCP_PROJECT_ID`, `BIGQUERY_DATASET` env vars |
| Google Cloud Storage | Bulk data staging + prosecution invoice storage | `gs://uspto-bulk-staging/` |
| Google Cloud Run Jobs | ETL pipeline execution | 4 jobs: `uspto-update-ptblxml/pasdl/ptmnfee2/ptfwpre` |
| Google Cloud Scheduler | Triggers ETL jobs on schedule | See schedule below |
| Vertex AI / Gemini | Natural language queries + PDF vision | `GEMINI_API_KEY` env var |
| USPTO Bulk Data API | Source of all patent data | `USPTO_API_KEY=inbsszqfwwnkjfebpibunnbllbygqz` |
| USPTO Document API | Fetches prosecution documents (fee sheets) | `https://api.uspto.gov/api/v1/patent/applications/{}/documents` |
| GitHub Actions | CI/CD — deploys on push to main | Secrets: `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `GEMINI_API_KEY`, `API_SECRET` |

---

## 9. Authentication and Access

- **No user authentication** — the app is public (`--allow-unauthenticated` on Cloud Run)
- **GCP auth:** Cloud Run uses a service account with BigQuery access. GitHub Actions uses Workload Identity Federation (keyless auth) — no long-lived service account keys.
- **USPTO API:** Key `inbsszqfwwnkjfebpibunnbllbygqz` used in `api/routers/prosecution.py` (hardcoded — low risk, public bulk data API)

---

## 10. Testing

Test files in `tests/`:
- `test_api_endpoints.py` — API endpoint smoke tests
- `test_conveyance_classifier.py` — Tests for `classify_conveyance_normalized()`
- `test_parse_helpers.py` — Tests for XML parsing helpers
- `test_parse_v4.py` — Tests for v4 assignment parser
- `test_patent_number.py` — Tests for `normalize_patent_number()`

Run tests: `python -m pytest tests/` from the repo root.

**Coverage gaps:** No tests for the BigQuery queries themselves (would require mocking), no E2E browser tests, no tests for entity_status or prosecution routers.

---

## 11. Deployment

### API Service (Cloud Run)
```bash
# Manual deploy:
gcloud run deploy uspto-api --source=. --project=uspto-data-app --region=us-central1 --allow-unauthenticated

# Auto-deploy: push to main branch triggers GitHub Actions
```

### ETL Container (Cloud Run Jobs)
```bash
# Rebuild ETL image (after changing any ETL code):
gcloud builds submit --project=uspto-data-app --config=cloudbuild-etl.yaml .

# After rebuilding, update each job to pick up new image:
gcloud run jobs update uspto-update-pasdl --image=us-central1-docker.pkg.dev/uspto-data-app/cloud-run-source-deploy/uspto-etl:latest --project=uspto-data-app --region=us-central1
# (repeat for each job)
```

### Environment Variables (Cloud Run Service)
```
GCP_PROJECT_ID=uspto-data-app
BIGQUERY_DATASET=uspto_data
GEMINI_API_KEY=<secret>
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
| `uspto-update-pasdl` | Mondays 3am | Patent assignments (daily files) — includes `resolve_assignment_pending()` post-load |
| `uspto-update-ptmnfee2` | 1st of month 4am | Maintenance fees |
| `uspto-update-ptfwpre` | 15th of month 1am | Patent file wrapper |

---

## 12. File Structure

```
/
├── api/
│   ├── main.py                    # FastAPI app + router registration
│   ├── config.py                  # Settings class — all BQ table references
│   ├── models/schemas.py          # Pydantic request/response models
│   ├── routers/
│   │   ├── mdm.py                 # MDM name normalization + _parse_boolean_query()
│   │   ├── query.py               # Boolean query builder
│   │   ├── ai_assistant.py        # Gemini AI assistant
│   │   ├── citations.py           # Forward citations
│   │   ├── assignments.py         # Assignment chain popup
│   │   ├── entity_status.py       # Entity status analytics (MAIN analytics router)
│   │   ├── prosecution.py         # Prosecution fee investigation
│   │   ├── etl_log.py             # ETL pipeline log
│   │   └── sec_leads.py           # SEC EDGAR leads (early stage)
│   └── services/
│       ├── bigquery_service.py    # bq_service singleton, run_query(), expand_name_for_query()
│       └── gemini_service.py      # Gemini AI wrapper
├── etl/
│   ├── update_pipeline.py         # ETL orchestrator — entrypoint for Cloud Run Jobs
│   ├── parse_assignments_xml_v4.py # PASDL XML parser (current) — outputs normalized_type
│   ├── normalize_conveyance.py    # One-time migration (already run — do not re-run)
│   ├── reload_assignments_v4.py   # Backfile reload with .done markers (already run)
│   ├── parse_file_wrapper.py      # PTFWPRE XML parser
│   ├── parse_maintenance_fees_v2.py # PTMNFEE2 parser
│   ├── parse_ptblxml.py           # PTBLXML citation parser
│   ├── backfill_pfw.py            # PFW backfill (already run — complete)
│   └── populate_entity_names_v2.py # Rebuilds entity_names table
├── utils/
│   ├── conveyance_classifier.py   # classify_conveyance() + classify_conveyance_normalized()
│   └── patent_number.py           # normalize_patent_number() — strips punctuation/prefixes
├── frontend/
│   ├── index.html                 # SPA shell — 8 tabs, script/CSS references with ?v= versions
│   ├── css/styles.css             # All styles (v=23)
│   └── js/
│       ├── app.js                 # Shared: tab switching, assignment popup, utilities (v=16)
│       ├── mdm.js                 # MDM tab (v=8)
│       ├── query_builder.js       # Query builder tab (v=9)
│       ├── ai_assistant.js        # AI assistant tab (v=8)
│       ├── citations.js           # Forward citations tab (v=5)
│       ├── entity_status.js       # Entity status tab — largest file (v=19)
│       ├── prosecution.js         # Prosecution fees tab (v=9)
│       ├── etl_log.js             # Update log tab (v=3)
│       └── sec_leads.js           # SEC leads tab
├── database/
│   ├── setup_v4.sql               # Current schema — 4 assignment tables
│   ├── setup_pfw_tables.sql       # pfw_* table schemas
│   └── setup_sec_leads.sql        # SEC leads table schema
├── tests/                         # Pytest tests
├── patent_analyzer/               # SEC EDGAR enrichment pipeline (separate feature)
├── Dockerfile                     # API service image (python:3.11-slim, port 8080)
├── Dockerfile.etl                 # ETL job image
├── cloudbuild-etl.yaml            # Cloud Build config for ETL image
├── requirements.txt               # Python dependencies
├── .env.example                   # Template for local dev env vars
├── CLAUDE.md                      # Project-level AI assistant instructions (read first)
└── HANDOFF315.md                  # This file
```

---

## 13. Configuration Files

| File | Purpose |
|---|---|
| `api/config.py` | All BigQuery table references as Python properties. Single source of truth for table names. |
| `Dockerfile` | API service — `python:3.11-slim`, exposes 8080, runs uvicorn |
| `Dockerfile.etl` | ETL jobs — installs gsutil/bq CLI tools in addition to Python deps |
| `cloudbuild-etl.yaml` | Builds and pushes ETL Docker image to Artifact Registry |
| `.github/workflows/deploy.yml` | GitHub Actions: on push to main → build → push → deploy to Cloud Run |
| `requirements.txt` | fastapi, uvicorn, google-cloud-bigquery, google-cloud-aiplatform, pydantic, ijson, requests, beautifulsoup4, lxml |
| `.env.example` | Template showing required env vars for local development |

---

## 14. Environment and Dependencies

**Python:** 3.11 (pinned in Dockerfile)

**Key packages:**
- `fastapi==0.109.2` + `uvicorn==0.27.1` — web framework
- `google-cloud-bigquery>=3.17.0` — BigQuery client
- `google-cloud-aiplatform>=1.38.0` — Vertex AI / Gemini
- `pydantic>=2.0.0` — request/response validation
- `ijson>=3.2.0` — streaming JSON parser for large USPTO files
- `lxml`, `beautifulsoup4` — XML parsing

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
| Google Cloud (project: `uspto-data-app`) | Everything — BigQuery, Cloud Run, GCS, Scheduler, Artifact Registry |
| GitHub (org: InnovationAccess) | Source code, CI/CD via GitHub Actions |
| USPTO Bulk Data API | Downloading PASDL, PTFWPRE, PTMNFEE2, PTBLXML bulk files |
| USPTO Document API | Fetching individual prosecution documents (fee sheets) |

No paid third-party APIs beyond GCP. USPTO APIs are free with API key registration.

---

## 16. Related Projects and Systems

- **`patent_analyzer/`** — A separate pipeline (within this repo) for SEC EDGAR company enrichment. Uses Apollo.io API for contact data. Feeds into the SEC Leads tab. Early-stage / partially built.
- **InnovationAccess GitHub org** — Parent org for this project.
- **Synpathub GitHub** — Owner's personal GitHub; some related tooling may live there.

---

## 17. Git State

**Main branch:** `main` — auto-deploys to Cloud Run via GitHub Actions
**Current working branch:** `claude/romantic-ramanujan` — all session work is here, pushed to GitHub
**Open PRs:** None formally opened. Session branch has commits ahead of main that need to be merged.

**Recent commits on this branch:**
```
6ecc542  Session 3 wrap-up: docs update + cache version fix
62bfe2f  Fix Boolean Query Builder CONTAINS returning 0 results
f448bad  Format semicolon-separated names as separate lines in assignment popup
```

**Recent commits on main:**
```
25abe3e  Update docs: add normalized_type system, fix stale pfw_inventors status
fe769c0  Add review as a standalone normalized type for human-review assignments
62a1dd2  Add court_order as a standalone normalized assignment type
f663764  Add normalized_type classification for 9M assignment records
```

**Convention:** Session work happens on `claude/<worktree-name>` branches. Merge to main when stable. GitHub Actions deploys on push to main.

---

## 18. Workarounds and Gotchas

**BigQuery:**
- ALL `bq` CLI commands MUST include `--location=us-west1` — omitting causes silent failures (jobs appear to succeed but do nothing)
- Use `--use_legacy_sql=false` NOT `--nouse_legacy_sql` (the latter is invalid syntax)
- Upload files one at a time with `gsutil cp` (NOT `gsutil -m cp`) — parallel uploads fail silently
- Load BQ files individually (NOT wildcard `*.jsonl.gz`) — BQ rejects wildcard patterns
- NEVER concatenate `.gz` files — creates multi-stream gzip that BigQuery cannot read
- Date `0000-01-01` causes BQ load failures — all parsers validate year range 1700-2100
- `bq load --nosync` returns immediately — failures are silent. Always verify row counts after loading
- INFORMATION_SCHEMA is region-specific; use `--location=us-west1` for all schema queries

**Cloud Run:**
- `/tmp` is backed by RAM (tmpfs) — large file downloads consume memory, not disk
- For files >1GB: set memory to at least 2x file size + 2GB overhead
- Cloud Run resolves container image tags to digests at job UPDATE time — after rebuilding ETL image, you MUST run `gcloud run jobs update --image=...` for each job
- Delete large temp files immediately after use to free memory

**USPTO Data Parsing:**
- Fields named `*Bag` in USPTO JSON are usually arrays but sometimes dicts or scalars — always use `_as_list()` helper
- Always check `isinstance(item, dict)` before calling `.get()` on bag items
- `KeyError(0)` prints as just "0" — happens when a dict is treated as a list
- Isolate each parsing function in its own try/except — one bad field must not kill the whole record
- USPTO API returns HTTP 429 (rate limit) — retry with backoff

**Assignment queries:**
- NEVER search `WHERE app_num = @id OR patent_num = @id` — causes collisions
- Assignment chain lookup: first resolve patent_number → application_number via `patent_file_wrapper_v2`, then find reel_frames via `pat_assign_documents`
- `pat_assign_assignors` and `pat_assign_assignees` are separate tables — JOIN both via reel_frame, aggregate with STRING_AGG to avoid cross-product duplicates

**Frontend:**
- Bump `?v=N` cache-busting version in `index.html` EVERY TIME a JS or CSS file changes — browsers aggressively cache these
- MDM table has its own custom sort — do NOT add `enableTableSorting()` to it (breaks sorting)

---

## 19. Decisions That Were Rejected

**Fabricated transition colors in micro charts:** When a patent has a STOL (small→large) event, the timeline line is all red (large) from grant, making it unclear why it's in the small→large group. Suggestion was to infer a "from" color (green) before the STOL event. **Rejected** — "destroys the integrity of the data. You cannot insert events that do not exist." Instead, transitions show as gray dots on the timeline.

**Single OR search across patent_number + application_number:** `WHERE field = @x OR other_field = @x` causes number space collisions. Rejected in favor of always resolving to application_number first.

**Wildcard-only PTBLXML parsing (annual files):** Annual PTBLXML citation files (2002–2005) are too large for in-memory XML parsing. Rejected in favor of weekly files only.

**parallel gsutil uploads (`gsutil -m cp`):** Causes silent upload failures. Rejected in favor of sequential one-at-a-time uploads.

**Using `entity_status` column for status:** USPTO populates this column inconsistently. Rejected — entity status must always be DERIVED from event codes (M1xxx=LARGE, M2xxx=SMALL, M3xxx=MICRO).

---

## 20. Known Limitations and Constraints

**Portfolio ownership accuracy:** The Entity Status applicant portfolio query finds patents the entity ever TOUCHED (filed, invented, received as assignee) — not just currently OWNS. Patents that were subsequently divested are still counted in KPIs and payment/declaration reports. `normalized_type` exists to fix this but the fix is not yet implemented.

**Source 4 redundancy in portfolio query:** `get_applicant_portfolio()` in `entity_status.py` includes `patent_file_wrapper_v2.first_applicant_name` as source 4 of the UNION. This was a backfill safety net when `pfw_applicants` was incomplete. `pfw_applicants` is now fully loaded (back to mid-1990s), making source 4 redundant. Should be removed.

**normalized_type not in frontend:** The 14-category assignment classification exists in BigQuery but is not exposed anywhere in the UI. Not filterable in Query Builder, not shown in assignment popup, not used in Entity Status portfolio filtering.

**Query Builder missing normalized_type field:** The `patent_assignments` fields in `query.py` include `conveyance_type` (coarse) but not `normalized_type` (fine-grained). Should be added.

**SEC Leads tab:** Early-stage feature. The `patent_analyzer/` pipeline is partially built and not fully integrated.

**No user authentication:** The app is publicly accessible. Appropriate for internal tool use; would need auth layer before public/commercial deployment.

**PTFWPRE coverage:** Only covers recent years (exact cutoff unknown). Historical file wrapper data comes from the backfill which is complete.

---

## 21. Open Issues and Bugs

**None currently blocking.** Most recent bugs fixed in this session:
- ~~Boolean Query Builder CONTAINS always returning 0 results~~ — Fixed (commit 62bfe2f)
- ~~Assignment popup showing duplicate rows~~ — Fixed (commit 3889dc1)
- ~~Micro charts not loading for >200 patents~~ — Fixed (commit 5086bc2)

**Potential issue to investigate:**
- The `assignments_table` property in `api/config.py` still points to `patent_assignments_v3` (the old flat table). This property is labeled "Kept for rollback" but the old v3 table has been dropped. If any code path still references `settings.assignments_table`, it would fail at query time.

---

## 22. Work In Progress

**Not started — highest priority:**

1. **Use `normalized_type` to filter portfolio for current ownership** — The `get_applicant_portfolio()` endpoint should exclude patents where the entity appears as assignOR in a subsequent `divestiture` or `merger` normalized_type record. This would make the KPIs reflect what the entity actually owns today, not its full historical touch count. This is a backend change in `api/routers/entity_status.py`.

2. **Expose `normalized_type` in the UI** — Add it as:
   - A filterable field in the Query Builder (`patent_assignments` table)
   - A column in the assignment chain popup
   - A filter in Entity Status portfolio (show only divestitures, mergers, etc.)

3. **Remove redundant source 4 from portfolio query** — Remove `patent_file_wrapper_v2.first_applicant_name` from the UNION in `get_applicant_portfolio()`. `pfw_applicants` is now complete.

---

## 23. Next Steps (Prioritized)

1. **Fix portfolio ownership accuracy** — Most impactful for business value. Implement `normalized_type`-based filtering to exclude divested patents from Entity Status KPIs and payment reports. Backend change in `entity_status.py`.

2. **Add `normalized_type` to Query Builder** — Add it to `_TABLE_FIELDS["patent_assignments"]` in `query.py` so users can filter assignments by type (e.g., only divestitures, only mergers).

3. **Show `normalized_type` in assignment popup** — Add a "Type" column showing the normalized_type alongside the existing conveyance type.

4. **Remove redundant source 4 from portfolio query** — Cleanup: remove `first_applicant_name` UNION arm from `get_applicant_portfolio()`.

5. **Build the "review" queue UI** — 15,806 assignments are flagged `review_flag=TRUE`. A simple UI to view and manually classify these would complete the normalization pipeline.

---

## 24. Commands and Context

**Deploy API (most common operation):**
```bash
gcloud run deploy uspto-api --source=. --project=uspto-data-app --region=us-central1 --allow-unauthenticated --quiet
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

**Git workflow:**
```bash
git pull origin main          # Always pull first
git push                      # Push to current branch
# To merge session work to main, create a PR on GitHub
```

**Live URL:** https://uspto-api-1094570457455.us-central1.run.app
**API docs (Swagger):** https://uspto-api-1094570457455.us-central1.run.app/docs
