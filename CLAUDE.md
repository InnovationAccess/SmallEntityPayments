# SmallEntityPayments — Project Instructions

## Quick Start
- Read HANDOFF.md for full project context, architecture, and current state
- This project lives at: https://github.com/InnovationAccess/SmallEntityPayments
- GCP Project: uspto-data-app
- BigQuery Dataset: uspto_data

## Critical Rules
- NEVER source data from anywhere the user hasn't explicitly specified
- Data integrity is the top priority — no shortcuts, no third-party datasets
- Only official USPTO bulk data products are authorized data sources
- The patent_file_wrapper table is currently EMPTY — do not load from Google's patents-public-data
- The etl/load_file_wrapper.py script is DEPRECATED — do not use it

## Current Priority
- Complete PTFWPRE bulk data download (2001-2020 still needed)
- Build new PTFWPRE JSON parser
- Load patent_file_wrapper table from official USPTO data

## Tech Stack
- Backend: Python/FastAPI on Google Cloud Run
- Frontend: Vanilla JS served from Cloud Run
- Database: Google BigQuery
- AI: Vertex AI (Gemini) for natural language queries
- Storage: Google Cloud Storage for bulk data staging

## Environment Variables
- GCP_PROJECT_ID=uspto-data-app
- BIGQUERY_DATASET=uspto_data
