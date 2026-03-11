# SmallEntityPayments — Project Instructions

## Quick Start
- Read HANDOFF312.md for full project context, architecture, and current state
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

## BigQuery Rules
- ALL `bq` CLI commands MUST include `--location=us-west1` — omitting this causes silent failures
- Upload files one at a time with `gsutil cp` (NOT `gsutil -m cp`) — parallel uploads cause failures
- Load BQ files individually (NOT wildcard patterns like `*.jsonl.gz`)
- Never concatenate `.gz` files — creates multi-stream gzip that BigQuery cannot read
- Date values like `0000-01-01` cause BQ load failures — parsers must validate year range 1700-2100

## Architecture
- Backend: Python 3.11 / FastAPI on Google Cloud Run (us-central1)
- Frontend: Vanilla JS SPA served from same Cloud Run container (no build step)
- Database: Google BigQuery (us-west1, dataset: uspto_data)
- AI: Vertex AI Gemini for natural language queries
- Storage: Google Cloud Storage (gs://uspto-bulk-staging/) for bulk data staging
- ETL: Cloud Run Jobs triggered by Cloud Scheduler, using gsutil/bq CLI tools
- All BigQuery tables use flat/denormalized schemas (no STRUCT/ARRAY except cpc_codes)

## Current State (2026-03-12)
- All tables loaded: ~892M rows across 9 tables
- 4 automated update pipelines running on Cloud Scheduler
- 5 frontend tabs: MDM, Query Builder, AI Assistant, Forward Citations, Update Log
- All tables have sticky headers, sortable columns, and assignment chain popup on patent numbers
- Citation tab includes examiner/applicant breakdown lists with name normalization
- ETL pipeline logging writes to `etl_log` BigQuery table

## Tech Stack
- Backend: Python/FastAPI on Google Cloud Run
- Frontend: Vanilla JS served from Cloud Run
- Database: Google BigQuery
- AI: Vertex AI (Gemini) for natural language queries
- Storage: Google Cloud Storage for bulk data staging
- ETL: Cloud Run Jobs + Cloud Scheduler for automated updates

## API Routers
- `/mdm/*` — MDM name normalization (api/routers/mdm.py)
- `/query/*` — Boolean query builder (api/routers/query.py)
- `/ai/*` — AI assistant (api/routers/ai_assistant.py)
- `/api/forward-citations/*` — Citation lookup with name resolution (api/routers/citations.py)
- `/api/assignments/*` — Assignment chain lookup (api/routers/assignments.py)
- `/api/etl-log/*` — Pipeline monitoring (api/routers/etl_log.py)

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
- ETL: `gcloud builds submit --config=/tmp/cloudbuild-etl.yaml --project=uspto-data-app`
- CI/CD: GitHub Actions deploys on push to main (.github/workflows/deploy.yml)
