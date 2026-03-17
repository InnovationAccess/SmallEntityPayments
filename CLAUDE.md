# SmallEntityPayments — Project Instructions

## Quick Start
- Read HANDOFF315.md for full project context, architecture, and current state
- This project lives at: https://github.com/InnovationAccess/SmallEntityPayments
- GCP Project: uspto-data-app
- BigQuery Dataset: uspto_data (location: us-west1)
- Live URL: https://uspto-api-1094570457455.us-central1.run.app

## Critical Rules
- NEVER source data from anywhere the user hasn't explicitly specified
- Data integrity is the top priority — no shortcuts, no third-party datasets
- Only official USPTO bulk data products are authorized data sources
- The `name_unification` table is user-curated — NEVER drop, truncate, or bulk-modify it (row count grows over time as user adds MDM mappings; currently ~545 rows across 6 entities)
- The `etl/load_file_wrapper.py` script is DEPRECATED and deleted — do not recreate it
- NEVER search a string across multiple fields simultaneously (e.g., `WHERE app_num = @id OR patent_num = @id`) — this causes collisions between patent numbers and application serial numbers. Always resolve to application_number first via patent_file_wrapper_v2.
- Entity status must be DERIVED from maintenance fee event codes (M1xxx/F17xx=LARGE, M2xxx/F27xx=SMALL, M3xxx=MICRO), never from the entity_status column (which USPTO populates inconsistently)

## BigQuery Rules
- ALL `bq` CLI commands MUST include `--location=us-west1` — omitting this causes silent failures
- bq CLI flag syntax: use `--use_legacy_sql=false` (NOT `--nouse_legacy_sql` — that's invalid)
- Upload files one at a time with `gsutil cp` (NOT `gsutil -m cp`) — parallel uploads cause failures
- Load BQ files individually (NOT wildcard patterns like `*.jsonl.gz`)
- Never concatenate `.gz` files — creates multi-stream gzip that BigQuery cannot read
- Date values like `0000-01-01` cause BQ load failures — parsers must validate year range 1700-2100
- When adding new columns to parser output, ALWAYS `ALTER TABLE ADD COLUMN` before loading — BQ rejects JSON fields not in the table schema ("No such field" error)
- Use `--schema_update_option=ALLOW_FIELD_ADDITION` in `bq load` to auto-add new fields
- NEVER truncate a table and reload from only the latest data file — if the data source provides partial updates (e.g., PTFWPRE only covers recent years), use targeted `DELETE WHERE source_file IN (...)` to preserve historical data
- `bq load --nosync` returns immediately without waiting for the load to complete — load failures are silent. Always verify row counts after loading

## Cloud Run Rules (learned from failures)
- Cloud Run `/tmp` is backed by RAM (tmpfs) — downloading large files to `/tmp` consumes memory
- For large files (>1 GB), set Cloud Run Job memory to at least 2x the file size + 2 GB for process overhead
- Cloud Run resolves container image tags to digests at job UPDATE time, not execution time — after rebuilding a container, you MUST run `gcloud run jobs update --image=...` to pick up the new image
- Delete large temp files immediately after use to free tmpfs memory (e.g., delete ZIP after parsing)
- Delete output files after upload to GCS to free memory progressively

## USPTO Data Parsing Rules (learned from failures)
- USPTO bulk JSON has inconsistent types: fields named `*Bag` are usually arrays but sometimes dicts or scalars — always use `_as_list()` helper to normalize before iterating
- When iterating bag items, always check `isinstance(item, dict)` before calling `.get()` — some bags contain strings or integers
- Isolate each extraction function in its own try/except — never let one bad field in `parse_attorneys()` cause the record to be lost from `parse_biblio()`, `parse_transactions()`, etc.
- `KeyError(0)` prints as just "0" — extremely hard to debug. This happens when `dict[0]` is called (treating dict as list). Always use `_as_list()` to prevent this
- The USPTO API returns HTTP 429 (rate limited) — retry with backoff, don't assume the download URL is broken

## Architecture
- Backend: Python 3.11 / FastAPI on Google Cloud Run (us-central1)
- Frontend: Vanilla JS SPA served from same Cloud Run container (no build step)
- Database: Google BigQuery (us-west1, dataset: uspto_data)
- AI: Vertex AI Gemini for natural language queries + PDF vision extraction
- Storage: Google Cloud Storage (gs://uspto-bulk-staging/) for bulk data staging + prosecution invoices
- ETL: Cloud Run Jobs triggered by Cloud Scheduler, using gsutil/bq CLI tools
- All BigQuery tables use flat/denormalized schemas (no STRUCT/ARRAY except cpc_codes)
- Cross-table joins use application_number as the universal key (not patent_number)

## Current State (2026-03-17)
- All tables loaded: ~1.0 billion rows across 24 tables
- Patent assignments normalized into 4 tables (v4): pat_assign_records, pat_assign_assignors, pat_assign_assignees, pat_assign_documents — linked by reel_frame, cross-table joins via application_number
- Old patent_assignments_v2 and patent_assignments_v3 tables dropped
- **Conveyance normalization complete**: All 9.07M assignment records classified into 14 fine-grained `normalized_type` categories (see Normalized Assignment Types below). The `employer_assignment` boolean is fully populated. 15,806 uncertain records flagged as `review` for human review.
- **normalized_type is NOT yet exposed in the frontend** — no UI shows it yet
- PASDL daily pipeline automatically normalizes new records: parser outputs `assignment_pending`, then `resolve_assignment_pending()` runs corporate filter + inventor matching post-load
- 4 automated update pipelines running on Cloud Scheduler (PASDL uses v4 parser with normalized_type output)
- 8 frontend tabs: MDM, Query Builder, AI Assistant, Forward Citations, Entity Status (+ Applicant Portfolio), Prosecution Fees, Update Log, SEC Leads
- 11 pfw_* tables: pfw_applicants (7.47M rows), pfw_inventors (35.7M rows), pfw_transactions, pfw_child_continuity, pfw_foreign_priority, pfw_publications, pfw_patent_term_adjustment, pfw_pta_history, pfw_correspondence_address, pfw_attorneys, pfw_document_metadata, pfw_embedded_assignments
- **pfw_applicants and pfw_inventors are FULLY LOADED back to mid-1990s** (not just 2021+)
- entity_names: 7.68M rows
- All tables have sticky headers, sortable columns, column pickers, and assignment chain popup on patent numbers
- Assignment popup: movable (drag header), resizable (drag corner), fills right side of viewport, names show one per line
- Citation tab includes examiner/applicant breakdown lists with name normalization
- Entity Status tab: micro chart timelines per patent, batched API calls (200/batch), single patent timeline view
- Prosecution Fees tab has 3-phase workflow: entity discovery, application drill-down, invoice extraction via Gemini Vision
- ETL pipeline logging writes to `etl_log` BigQuery table

## Known Architecture Issue (open — next to fix)
The `get_applicant_portfolio()` in `api/routers/entity_status.py` finds patents via 4 UNION DISTINCT sources:
1. `pfw_applicants` — all applicants
2. `pfw_inventors` — all inventors
3. `pat_assign_assignees` — received as assignee
4. `patent_file_wrapper_v2.first_applicant_name` — **REDUNDANT** (pfw_applicants now covers same data back to mid-1990s)

**Critical**: The query finds all patents ever TOUCHED by the entity, not just currently OWNED. Patents sold/divested are still counted in KPIs. Fix: use `normalized_type` (divestiture/merger) to exclude patents where entity later appears as assignOR in an ownership-transfer record.

## Normalized Assignment Types
The `pat_assign_records.normalized_type` column classifies each assignment into one of 14 categories:

| normalized_type | Count | Meaning |
|---|---|---|
| employee | 7,881,089 (86.9%) | Inventors assigning to their employer (verified by matching assignor names against pfw_inventors) |
| divestiture | 704,504 (7.8%) | Patent assets sold/transferred between corporate entities |
| name_change | 173,915 (1.9%) | Entity name or legal form change (no ownership change) |
| government | 105,941 (1.2%) | Government interest / confirmatory license (Bayh-Dole) |
| security | 74,665 (0.8%) | Security interest granted (loan collateral) |
| merger | 51,389 (0.6%) | Acquirer takes target's assets (actual ownership change) |
| release | 34,200 (0.4%) | Security interest fully terminated |
| review | 15,806 (0.2%) | Uncertain classification, flagged for human review |
| address_change | 13,821 (0.2%) | Address update only |
| license | 6,490 (0.1%) | License granted under patent assets |
| correction | 3,420 (<0.1%) | Typo/error fix in a prior recordation (no new rights) |
| court_order | 855 (<0.1%) | Court-ordered transfer (typically bankruptcy) |
| partial_release | 596 (<0.1%) | Subset of collateralized assets released |
| license_termination | 60 (<0.1%) | License terminated |

**Classification approach:**
- Rule-based regex matching on `conveyance_text` for non-assignment types (~461K records)
- Corporate assignor filter: if ALL assignors are corporate entities (Inc., Corp., LLC, etc.) → divestiture (~564K records)
- Inventor name matching: join assignor names against `pfw_inventors` via `pat_assign_documents.application_number`. Majority-match rule (≥50% of person-assignors match inventors → employee). Typo-resilient: one non-matching name among several matches doesn't prevent classification.
- `employer_assignment` boolean: TRUE for employee, FALSE for all others
- `review_flag` boolean: TRUE for uncertain records needing human review

**Related files:**
- `utils/conveyance_classifier.py` — `classify_conveyance_normalized()` for parser-time classification
- `etl/normalize_conveyance.py` — One-time migration script (already run)
- `etl/update_pipeline.py` — `resolve_assignment_pending()` for daily pipeline post-load normalization

## Tech Stack
- Backend: Python/FastAPI on Google Cloud Run
- Frontend: Vanilla JS served from Cloud Run
- Database: Google BigQuery
- AI: Vertex AI (Gemini) for natural language queries and PDF vision extraction
- Storage: Google Cloud Storage for bulk data staging
- ETL: Cloud Run Jobs + Cloud Scheduler for automated updates

## API Routers (8 total)
- `/mdm/*` — MDM name normalization (api/routers/mdm.py)
- `/query/*` — Boolean query builder (api/routers/query.py)
- `/ai/*` — AI assistant (api/routers/ai_assistant.py)
- `/api/forward-citations/*` — Citation lookup with name resolution (api/routers/citations.py)
- `/api/assignments/*` — Assignment chain lookup (api/routers/assignments.py)
- `/api/entity-status/*` — Entity status analytics (api/routers/entity_status.py)
- `/api/prosecution/*` — Prosecution fee investigation (api/routers/prosecution.py)
- `/api/etl-log/*` — Pipeline monitoring (api/routers/etl_log.py)

## Frontend Cache-Busting Versions
- styles.css?v=23, app.js?v=16, mdm.js?v=8, query_builder.js?v=9, ai_assistant.js?v=8
- citations.js?v=5, entity_status.js?v=19, prosecution.js?v=9, etl_log.js?v=3
- ALWAYS bump the ?v= number when changing any JS or CSS file — browsers aggressively cache these

## ETL Pipeline
- Orchestrator: etl/update_pipeline.py (entrypoint for Cloud Run Jobs)
- Sources: ptblxml (citations), pasdl (assignments), ptmnfee2 (maint fees), ptfwpre (file wrapper)
- Each source has its own Cloud Run Job and Cloud Scheduler trigger
- PASDL post-load step: `resolve_assignment_pending()` classifies new assignment records (corporate filter → inventor matching → employee/divestiture/review)
- Logs each run to the `etl_log` BigQuery table

## Environment Variables
- GCP_PROJECT_ID=uspto-data-app
- BIGQUERY_DATASET=uspto_data
- GCS_BUCKET=uspto-bulk-staging
- GEMINI_API_KEY (set in Cloud Run service env)
- USPTO_API_KEY (set in Cloud Run Job env)

## Deployment
- API: `gcloud run deploy uspto-api --source=. --project=uspto-data-app --region=us-central1 --allow-unauthenticated`
- ETL: `gcloud builds submit --config=cloudbuild-etl.yaml --project=uspto-data-app`
- CI/CD: GitHub Actions deploys on push to main (.github/workflows/deploy.yml)
