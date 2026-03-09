# Handoff Document — For Successor Claude Desktop Instance

**Date**: 2026-03-08
**From**: Claude Opus (Chromebook session via claude.ai)
**To**: Claude Desktop instance on a more powerful machine
**Project**: USPTO Data Platform — Patent Intelligence & MDM

---

## CRITICAL RULES — READ FIRST

1. **NEVER source data from anywhere the user hasn't explicitly specified.** Data integrity is the top priority. No shortcuts, no third-party datasets, no "convenience" substitutions. Only use official USPTO bulk data products.

2. **The `patent_file_wrapper` table is currently EMPTY (0 rows).** It was previously contaminated with data from `patents-public-data.patents.publications` — a free Google BigQuery public dataset maintained by Google, NOT by USPTO. The user strongly objected to this. All contaminated data has been purged. The table needs to be reloaded from the official USPTO PTFWPRE bulk product.

3. **The `etl/load_file_wrapper.py` script is DEPRECATED and must NOT be used.** It sources data from the third-party dataset. A new parser must be written for the PTFWPRE JSON format.

4. **Only official USPTO bulk data products are authorized data sources:**
   - PTMNFEE2 (Maintenance Fee Events) — already loaded
   - PASYR (Patent Assignments) — already loaded
   - PTFWPRE (Patent File Wrapper) — download partially complete, parser not yet built

---

## CURRENT STATE OF THE PROJECT

### What is working (deployed and live)

The application is deployed at `https://uspto-api-7wmnuaghmq-uc.a.run.app` and all three tabs are functional:

1. **Tab 1 — MDM Name Normalization**: Fully functional. Boolean search, copy/paste workflow, address cross-referencing, address search. 64 name_unification records exist.

2. **Tab 2 — Boolean Query Builder**: Fully functional. Multi-table queries, dynamic conditions, multi-select for event/fee codes, boolean expressions in name fields, sortable results with column visibility.

3. **Tab 3 — AI Assistant**: Conversational chat implemented and deployed. Gemini discusses queries first, only generates SQL when user says "run it" or equivalent. SQL accordion (collapsed by default). Auto-retry on SQL errors (3 attempts). Uses Vertex AI SDK (`gemini-2.5-flash`).

### What is deployed vs uncommitted

There are **significant uncommitted changes** in the working directory that ARE deployed to Cloud Run (via `gcloud run deploy --source .`) but NOT yet pushed to GitHub:

```
Modified files (deployed but not committed):
 api/models/schemas.py            — AIChatMessage schema, history field
 api/routers/ai_assistant.py      — Chat with history, retry logic
 api/routers/query.py             — Boolean expressions, multi-select codes, sortable
 api/services/bigquery_service.py — Minor updates
 api/services/gemini_service.py   — Major rewrite: Vertex AI, conversational chat, comprehensive SQL rules
 frontend/css/styles.css          — Accordion styles, sortable table styles
 frontend/index.html              — SQL accordion, cache v=7
 frontend/js/ai_assistant.js      — Full rewrite for chat mode
 frontend/js/app.js               — buildInteractiveTable() with sortable columns, column picker
 frontend/js/query_builder.js     — Dynamic condition types, code dropdowns, boolean name fields
 requirements.txt                 — google-cloud-aiplatform (was google-generativeai)
```

**IMPORTANT**: These changes need to be committed and pushed to GitHub. The user was asked to transition to a stronger computer before this was done.

### Untracked files (not in git)

```
MaintFeeEventsFileDocumentation.doc  — USPTO maintenance fee format documentation
PADX-File-Description-v2_Hague.doc   — PADX XML format documentation
etl/download_and_parse_pasyr.sh      — PASYR download orchestrator
etl/load_file_wrapper.py             — DEPRECATED, do not use
etl/parse_assignments_xml.py         — PADX XML parser
etl/parse_maintenance_fees.py        — PTMNFEE2 parser
```

These ETL scripts and documentation files should be committed to GitHub.

---

## IMMEDIATE TASKS (in priority order)

### 1. Commit and push all changes to GitHub

All the modified and untracked files listed above need to be committed. This is the most urgent task.

### 2. Complete PTFWPRE download to GCS

**Status**: 1 of 3 files downloaded to `gs://uspto-bulk-staging/ptfwpre/`

| File | Size | Status |
|---|---|---|
| `2021-2026-patent-filewrapper-full-json-20260301.zip` | ~13 GB | Downloaded (10.98 GiB in GCS) |
| `2001-2010-patent-filewrapper-full-json-20260301.zip` | ~19.6 GB | NOT downloaded — failed on Chromebook |
| `2011-2020-patent-filewrapper-full-json-20260301.zip` | ~30 GB | NOT downloaded — failed on Chromebook |

The downloads failed because the Chromebook couldn't sustain long-running curl streams. On the stronger machine, you have two options:

**Option A: Download to local disk, then upload to GCS**
```bash
# Get redirect URL
REDIRECT_URL=$(curl -s -o /dev/null -w '%{redirect_url}' \
  -H "x-api-key: $USPTO_API_KEY" \
  "https://api.uspto.gov/api/v1/datasets/products/files/PTFWPRE/2001-2010-patent-filewrapper-full-json-20260301.zip")

# Download to local disk
curl -L -o ptfwpre-2001-2010.zip "$REDIRECT_URL"

# Upload to GCS
gcloud storage cp ptfwpre-2001-2010.zip gs://uspto-bulk-staging/ptfwpre/2001-2010-patent-filewrapper-full-json-20260301.zip
```

**Option B: Stream directly to GCS (requires stable connection)**
```bash
REDIRECT_URL=$(curl -s -o /dev/null -w '%{redirect_url}' \
  -H "x-api-key: $USPTO_API_KEY" \
  "https://api.uspto.gov/api/v1/datasets/products/files/PTFWPRE/2001-2010-patent-filewrapper-full-json-20260301.zip")

curl -sL "$REDIRECT_URL" | gcloud storage cp - gs://uspto-bulk-staging/ptfwpre/2001-2010-patent-filewrapper-full-json-20260301.zip
```

**Option C: Download to USB hard drive** — The user has an external USB hard drive available. Total PTFWPRE data is ~62 GB, so a 64+ GB drive works. Download all 3 files locally, then upload to GCS or process directly.

### 3. Build PTFWPRE parser and load into BigQuery

This is the main outstanding engineering task. The PTFWPRE files are ZIP archives containing JSON files (one per year, e.g., `2026.json`, `2025.json`, etc.).

#### PTFWPRE JSON Structure

Based on the user's description and the USPTO product documentation, the JSON follows this structure:

```json
{
  "patentFileWrapperDataBag": {
    "applicationMetaData": {
      "applicationNumberText": "16123456",
      "filingDate": "2021-05-15",
      "inventionTitle": "...",
      "patentNumber": "11234567",
      "grantDate": "2023-03-21",
      "applicantBag": [
        {
          "applicantNameText": "JOHN DOE",
          "applicantAddress": {
            "streetAddress": "123 Main St",
            "cityName": "San Jose",
            "geographicRegionName": "CA",
            "countryCode": "US"
          },
          "entityStatusCategory": "SMALL"
        }
      ]
    }
  }
}
```

**NOTE**: You MUST verify this structure against the actual data before building the parser. Extract a small sample from the downloaded 2021-2026 zip file first:

```bash
# On a machine with enough disk space:
unzip -l ptfwpre-2021-2026.zip  # List contents
unzip -p ptfwpre-2021-2026.zip 2026.json | head -c 10000  # Peek at structure
```

Or stream from GCS:
```bash
gcloud storage cat gs://uspto-bulk-staging/ptfwpre/2021-2026-patent-filewrapper-full-json-20260301.zip | \
  python3 -c "
import sys, zipfile, io, json
zf = zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read()))
print('Files in zip:', zf.namelist())
# Read first record from first file
with zf.open(zf.namelist()[0]) as f:
    data = json.loads(f.read(50000).decode())
    print(json.dumps(data[:1] if isinstance(data, list) else data, indent=2)[:5000])
"
```

**WARNING**: The 13 GB zip may contain a very large JSON file. The parser MUST use streaming JSON parsing (e.g., `ijson` library) to avoid loading the entire file into memory.

#### Target BigQuery Schema

Map PTFWPRE JSON to the existing `patent_file_wrapper` table schema:

```
patent_number      ← patentNumber (nullable for ungranted apps)
application_number ← applicationNumberText
invention_title    ← inventionTitle
grant_date         ← grantDate
applicants[]       ← applicantBag[]
  .name            ← applicantNameText
  .street_address  ← applicantAddress.streetAddress
  .city            ← applicantAddress.cityName
  .state           ← applicantAddress.geographicRegionName
  .country         ← applicantAddress.countryCode
  .entity_type     ← entityStatusCategory
```

#### Parser Requirements

1. **Streaming**: Process JSON files in streaming mode (use `ijson` or similar)
2. **Memory-safe**: Don't load entire files into memory (individual year files could be 10+ GB uncompressed)
3. **Disk-efficient**: Process files from GCS if local disk is limited
4. **Output**: JSONL format, gzipped, for `bq load`
5. **Year filtering**: Accept min_year parameter (default: all years)
6. **Batch loading**: Load year by year or in chunks to BigQuery

### 4. Regenerate entity_names table

After loading PTFWPRE data:
```bash
python etl/populate_entity_names.py
```

This will include applicant names from the newly loaded data.

---

## ARCHITECTURE DETAILS

### Backend (FastAPI)

**Entry point**: `api/main.py`
- Registers 3 routers: `/mdm`, `/query`, `/ai`
- Mounts `frontend/` as static files at `/static`
- Serves `index.html` at root `/`
- Health check at `/health`

**Configuration**: `api/config.py`
- `Settings` class reads from environment variables
- Computed properties return fully-qualified BigQuery table names
- Global `settings` singleton

**Services**:
- `bigquery_service.py` — Singleton `BigQueryService` with lazy client initialization. All BigQuery operations: parameterized queries, MDM search, name association, address lookup, name expansion.
- `gemini_service.py` — Singleton `GeminiService` using Vertex AI. `chat()` for conversational queries, `fix_sql()` for retrying failed SQL. Comprehensive schema context with SQL rules, CTE patterns, and conversation rules.

### Frontend (Vanilla JS SPA)

**No build toolchain**. 4 JS files loaded directly with `?v=7` cache busting.

- `app.js` — Tab navigation, API helpers (`apiPost`, `apiGet`, `apiDelete`), `buildInteractiveTable()` (sortable columns, column picker), `setLoading()`, `showStatus()`, `escHtml()`
- `mdm.js` — Full MDM workflow including two-level modal system
- `query_builder.js` — Dynamic condition rows, field-dependent input types
- `ai_assistant.js` — Chat with history, SQL accordion

### Deployment

**Cloud Run service**: `uspto-api` in `us-central1`

Two deployment methods:
1. **Manual**: `gcloud run deploy uspto-api --source . --region us-central1 --allow-unauthenticated`
2. **CI/CD**: Push to `main` → GitHub Actions → Docker build → Artifact Registry → Cloud Run

The manual method was used during development. CI/CD is configured but requires the GitHub secrets to be set up.

---

## GOOGLE CLOUD RESOURCES

| Resource | Details |
|---|---|
| **Project ID** | `uspto-data-app` |
| **Cloud Run** | Service: `uspto-api`, Region: `us-central1` |
| **BigQuery** | Dataset: `uspto_data`, 5 tables |
| **GCS Bucket** | `gs://uspto-bulk-staging/` — PTFWPRE staging area |
| **Artifact Registry** | `us-central1-docker.pkg.dev/uspto-data-app/uspto-repo/` |
| **Vertex AI** | Model: `gemini-2.5-flash`, Region: `us-central1` |
| **Enabled APIs** | BigQuery, Cloud Run, Artifact Registry, Vertex AI (`aiplatform.googleapis.com`) |

---

## BigQuery Table Status

| Table | Row Count | Size | Status |
|---|---|---|---|
| `patent_file_wrapper` | 12,733,017 | ~1.5 GB | Loaded from PTFWPRE (2001-2026 + no_filing_date) |
| `patent_assignments` | 17,022,261 | 2 GB | Loaded from PASYR (2006+) |
| `maintenance_fee_events` | 12,215,388 | 528 MB | Loaded from PTMNFEE2 (2016+) |
| `name_unification` | 64 | 0 | MDM mappings (user-created) |
| `entity_names` | 1,342,085 | ~50 MB | Pre-computed from applicants + assignees |

---

## USPTO API ACCESS

- **API Key**: Stored in `$USPTO_API_KEY` environment variable
- **Product Data API**: `GET https://api.uspto.gov/api/v1/datasets/products/{productIdentifier}`
- **File Download API**: `GET https://api.uspto.gov/api/v1/datasets/products/files/{productIdentifier}/{fileName}`
- **Auth header**: `x-api-key: $USPTO_API_KEY`

### PTFWPRE Product Files

```
Product: PTFWPRE (Patent File Wrapper - Bulk Datasets - Weekly)
Total size: 62.6 GB across 3 files
Last modified: 2026-03-06

Files:
  2001-2010-patent-filewrapper-full-json-20260301.zip  (19.6 GB)
  2011-2020-patent-filewrapper-full-json-20260301.zip  (30.0 GB)
  2021-2026-patent-filewrapper-full-json-20260301.zip  (13.0 GB)
```

---

## USER PREFERENCES & DECISIONS

These were explicitly stated by the user during development:

1. **Data sources**: ONLY official USPTO bulk products. No PatentsView, no Google public datasets.
2. **Assignment data**: Filtered to 2006+ (user preference: ~20 years of data)
3. **Maintenance fee data**: Filtered to 2016+
4. **PASYR processing**: File-by-file due to disk constraints on Chromebook (~9 GB disk total)
5. **MDM approach**: Strictly manual/deterministic, no AI in the data write path
6. **SQL visibility**: User doesn't understand SQL — hidden behind accordion
7. **AI interaction**: Conversational chat (discuss first, execute only on command)
8. **Deployment**: Cloud Run with `gcloud run deploy --source .` for quick iterations

---

## MEMORY FILE

The project has an auto-memory file at:
`~/.claude/projects/-home-uzi-SmallEntityPayments/memory/MEMORY.md`

This file is automatically loaded into Claude's context at conversation start. If you're using Claude Desktop, create this file structure on the new machine and copy the contents from the handoff. Key contents:

```
# Critical Rules
- NEVER source data from anywhere the user hasn't explicitly specified
- Only use official USPTO bulk data products
- patent_file_wrapper is contaminated, needs reload from PTFWPRE

# Project Structure, BigQuery Tables, Schema, Data Sources, Config, Deployment details
```

The full memory file contents are in the repository (see MEMORY.md section above or read it from the Chromebook before transitioning).

---

## KNOWN ISSUES

All critical issues from the original handoff have been resolved:
- ~~patent_file_wrapper is empty~~ — **RESOLVED**: 12,733,017 rows loaded from PTFWPRE
- ~~PTFWPRE download incomplete~~ — **RESOLVED**: All 3 ZIPs downloaded and archived in GCS
- ~~PTFWPRE parser doesn't exist~~ — **RESOLVED**: etl/parse_file_wrapper.py built and tested
- ~~entity_names only contains assignee names~~ — **RESOLVED**: 1,342,085 names (applicants + assignees)
- ~~load_file_wrapper.py must not be used~~ — **RESOLVED**: Deleted from repo
- ~~USPTO API key hardcoded~~ — **RESOLVED**: Now requires $USPTO_API_KEY env var
- ~~Uncommitted changes~~ — **RESOLVED**: All changes committed and pushed
- ~~CI/CD secrets not configured~~ — **RESOLVED**: All 5 GitHub Actions secrets set

## REMAINING OPPORTUNITIES

1. **CI/CD end-to-end test** — GitHub Actions workflow has secrets configured but hasn't been triggered yet. Next push to main will test it.
2. **Weekly PTFWPRE refresh** — The PTFWPRE bulk data updates weekly. Could automate periodic re-parsing.
3. **Entity type analytics** — With 12.7M patent file wrapper records, rich entity status analysis is now possible.
