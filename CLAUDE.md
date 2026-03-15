# SmallEntityPayments — Project Instructions

## Quick Start
- Read HANDOFF314.md for full project context, architecture, and current state
- This project lives at: https://github.com/InnovationAccess/SmallEntityPayments
- GCP Project: uspto-data-app
- BigQuery Dataset: uspto_data (location: us-west1)
- Live URL: https://uspto-api-1094570457455.us-central1.run.app

## Critical Rules
- NEVER source data from anywhere the user hasn't explicitly specified
- Data integrity is the top priority — no shortcuts, no third-party datasets
- Only official USPTO bulk data products are authorized data sources
- The `name_unification` table (64 rows) is user-curated — NEVER drop, truncate, or bulk-modify it
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

## Current State (2026-03-12)
- All tables loaded: ~828M rows across 12 tables (~62.7 GB)
- Patent assignments normalized into 4 tables (v4): pat_assign_records, pat_assign_assignors, pat_assign_assignees, pat_assign_documents — linked by reel_frame, cross-table joins via application_number
- Old patent_assignments_v2 and patent_assignments_v3 tables dropped
- 4 automated update pipelines running on Cloud Scheduler (PASDL uses v4 parser)
- 7 frontend tabs: MDM, Query Builder, AI Assistant, Forward Citations, Entity Status, Prosecution Fees, Update Log
- All tables have sticky headers, sortable columns, column pickers, and assignment chain popup on patent numbers
- Assignment popup is movable (drag header) and resizable (drag corner)
- Citation tab includes examiner/applicant breakdown lists with name normalization
- Entity Status tab derives status from event codes, supports boolean name search in applicant fields
- Prosecution Fees tab has 3-phase workflow: entity discovery, application drill-down, invoice extraction via Gemini Vision
- ETL pipeline logging writes to `etl_log` BigQuery table
- cloudbuild-etl.yaml checked into repo root (was previously only at /tmp/)

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
- styles.css?v=10, app.js?v=13, mdm.js?v=8, query_builder.js?v=8, ai_assistant.js?v=8
- citations.js?v=5, entity_status.js?v=3, prosecution.js?v=3, etl_log.js?v=3

## ETL Pipeline
- Orchestrator: etl/update_pipeline.py (entrypoint for Cloud Run Jobs)
- Sources: ptblxml (citations), pasdl (assignments), ptmnfee2 (maint fees), ptfwpre (file wrapper)
- Each source has its own Cloud Run Job and Cloud Scheduler trigger
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
