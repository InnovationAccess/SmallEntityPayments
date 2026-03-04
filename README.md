# USPTO Data Platform — Patent Intelligence & MDM

A serverless, enterprise-grade **Patent Intelligence and Master Data Management (MDM)** platform built on Google Cloud. The platform ingests messy USPTO data (Patent File Wrappers, Assignments, Maintenance Fees), enables human data-stewards to clean and normalize entity names, and supports both AI-driven natural-language querying and strict Boolean querying of the resulting dataset.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Stack](#2-technology-stack)
3. [Core Business Logic & Functionality](#3-core-business-logic--functionality)
   - [A. Master Data Management (Entity Normalization)](#a-master-data-management-entity-normalization-workflow)
   - [B. AI Patent Intelligence (Natural Language Querying)](#b-ai-patent-intelligence-natural-language-querying)
   - [C. Advanced Boolean Builder](#c-advanced-boolean-builder)
4. [Repository Architecture](#4-repository-architecture)
5. [Setup and Deployment](#5-setup-and-deployment)

---

## 1. Project Overview

The USPTO Data Platform solves a core data-quality problem that affects patent analytics: raw USPTO records contain hundreds of inconsistent spellings and abbreviations for the same legal entity. Left uncorrected, these inconsistencies make ownership queries unreliable and break downstream analytics.

The platform provides three integrated capabilities:

| Capability | Description |
|---|---|
| **Master Data Management** | Human-supervised, deterministic workflow to normalize raw entity names to canonical master records. |
| **AI Patent Intelligence** | Natural-language query interface powered by Google Gemini that translates plain-English questions into validated BigQuery SQL. |
| **Advanced Boolean Builder** | Parameterized, injection-safe SQL query builder for analysts who require strict, repeatable Boolean logic without LLM involvement. |

All three capabilities share a common BigQuery data warehouse and are served through a single FastAPI backend deployed as a containerized Cloud Run service.

---

## 2. Technology Stack

| Layer | Technology |
|---|---|
| **Backend** | Python, FastAPI, Uvicorn |
| **Database & Data Warehouse** | Google Cloud BigQuery |
| **AI / LLM** | Google Gemini (`google-generativeai` SDK) |
| **Frontend** | Vanilla JavaScript, HTML5, CSS3 (Modular Architecture) |
| **Infrastructure & CI/CD** | Google Cloud Run, Docker, GitHub Actions |

---

## 3. Core Business Logic & Functionality

### A. Master Data Management (Entity Normalization Workflow)

The MDM workflow is a **strictly manual, deterministic process** that is completely decoupled from AI to eliminate any risk of hallucination introducing bad data into the canonical name registry.

#### How it works

1. A data-steward opens the **MDM tab** and runs a Boolean search against the BigQuery `assignees` and `applicants` arrays.
2. The search syntax supports:
   - `+term` — the result **must** contain this term.
   - `-term` — the result **must not** contain this term.
   - `*term` — wildcard/prefix match.
3. The results panel returns raw entity names alongside their current normalization status.

#### UI/UX State Logic (Color Coding)

| Color | Meaning |
|---|---|
| 🟠 **Orange** | An orphaned **Representative Name** (a master record that has no aliases linked to it yet). |
| 🔴 **Red** | The currently **active / copied Representative Name** held in global UI state — this is the target master record for the next link operation. |
| 🟢 **Green** | A raw entity name that has been **successfully normalized** (linked to a master representative name). |

#### Geographic Cross-Referencing

Users can open an **Address Modal** for any entity to view all unique postal addresses associated with that raw name. When a master representative name is selected, the modal aggregates addresses across all of its linked aliases into a single combined list.

Stewards use address similarity to discover hidden typos — for example, `"Acme Corp."` and `"Acme Corporation"` may share the same registered address, revealing that they are the same legal entity.

This geographic cross-referencing ensures that geographically co-located variants are found and merged into a single canonical record, even when textual similarity alone would miss them.

---

### B. AI Patent Intelligence (Natural Language Querying)

The AI tab allows analysts to ask complex patent-portfolio questions in plain English, such as:

> *"List assignees with a 3rd maintenance fee paid at a small entity rate in the past 10 years, grouped by technology class."*

#### Prompt Context

The FastAPI backend constructs a prompt for Google Gemini that includes:

- The **full BigQuery schema** (table names, column names, data types, and array structures) so the model can reference actual column names.
- The **current MDM name-mappings** so the model can resolve canonical entity names rather than matching raw strings.

#### SQL Generation Instructions

Gemini is explicitly instructed to:

- Use `UNNEST()` when querying array columns such as `assignees` and `applicants`.
- Use `GROUP BY` and `COUNT()` for aggregation queries.
- Return only a single, executable BigQuery SQL statement.

#### Security Guardrails

The backend **strictly intercepts the SQL returned by Gemini** before execution. Any statement containing the following keywords is blocked and never sent to BigQuery:

```
DROP  |  DELETE  |  UPDATE  |  ALTER  |  INSERT  |  TRUNCATE
```

This ensures that even a compromised or adversarially-manipulated LLM response cannot mutate or destroy data.

---

### C. Advanced Boolean Builder

The Boolean Builder tab is the **fallback / strict querying mechanism** for analysts who need fully reproducible, auditable queries without any LLM involvement.

- Users construct queries through UI dropdowns selecting **Fields**, **Operators**, and **Values**.
- Each user-supplied value is passed to BigQuery as a **parameterized input**, completely bypassing SQL injection vectors.
- The generated query is displayed to the user before execution for transparency and audit purposes.
- Results are rendered in a sortable, paginated table.

---

## 4. Repository Architecture

```
SmallEntityPayments/
├── database/
│   └── setup.sql               # BigQuery DDL — table schemas, partitioning, clustering
│
├── api/
│   ├── main.py                 # FastAPI application entry point; router registration
│   ├── routers/
│   │   ├── mdm.py              # MDM endpoints (search, link, unlink, address lookup)
│   │   ├── query.py            # Boolean Builder query endpoints
│   │   └── ai_assistant.py     # AI natural-language query endpoints
│   ├── services/
│   │   ├── bigquery_service.py # All BigQuery read/write operations
│   │   └── gemini_service.py   # Gemini prompt construction, SQL extraction & validation
│   └── models/
│       └── schemas.py          # Pydantic request/response models for strict validation
│
├── frontend/
│   ├── index.html              # Single-page application shell; tab navigation
│   ├── css/
│   │   └── styles.css          # Global styles; color-coded MDM state variables
│   └── js/
│       ├── app.js              # Global state management; shared utilities
│       ├── mdm.js              # MDM tab logic (search, link, address modal)
│       ├── query_builder.js    # Boolean Builder tab logic
│       └── ai_assistant.js     # AI tab logic (prompt submission, result rendering)
│
├── .env.example                # Template for required environment variables
├── Dockerfile                  # Multi-stage container build
├── .github/
│   └── workflows/
│       └── deploy.yml          # CI/CD pipeline: build → push → deploy to Cloud Run
└── README.md
```

### Modular Architecture Rationale

| Path | Purpose |
|---|---|
| `database/setup.sql` | Single source of truth for the BigQuery DDL schema. Run once during provisioning and again when schema migrations are needed. |
| `api/main.py` | Thin application entry point. Imports and registers routers; configures CORS and middleware. Contains no business logic. |
| `api/routers/` | HTTP boundary layer. Each file maps to one product area (MDM, Boolean Query, AI). Routers handle request parsing and response serialization only. |
| `api/services/` | Core business logic, fully decoupled from HTTP. `bigquery_service.py` owns all data-access; `gemini_service.py` owns prompt engineering and security filtering. |
| `api/models/schemas.py` | Pydantic models enforce strict input/output contracts, providing automatic request validation and OpenAPI documentation generation. |
| `frontend/` | Modular Vanilla JS SPA. Each feature tab has its own dedicated JS module (`mdm.js`, `query_builder.js`, `ai_assistant.js`), keeping concerns cleanly separated without a build toolchain dependency. |

---

## 5. Setup and Deployment

### Prerequisites

- Google Cloud project with BigQuery and Cloud Run APIs enabled.
- A Google Cloud service account with the `BigQuery Data Editor` and `BigQuery Job User` roles.
- Docker installed locally for container builds.

### Environment Variables

Copy the provided template and populate the values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `GCP_PROJECT_ID` | Your Google Cloud project ID. |
| `BIGQUERY_DATASET` | The BigQuery dataset containing the USPTO tables. |
| `GEMINI_API_KEY` | API key for the Google Gemini model. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to the service account JSON key file (local dev only). |

### Local Development

Build and run the container locally:

```bash
# Build the image
docker build -t uspto-platform .

# Run with environment variables
docker run --env-file .env -p 8080:8080 uspto-platform
```

The API will be available at `http://localhost:8080`. The frontend is served as static files from the same container.

### Automated CI/CD Deployment

The GitHub Actions workflow (`.github/workflows/deploy.yml`) automates the full deployment pipeline on every push to `main`:

1. **Build** — Builds the Docker image using the repository `Dockerfile`.
2. **Push** — Pushes the image to Google Artifact Registry.
3. **Deploy** — Updates the Cloud Run service to the new image revision with zero-downtime traffic migration.

Required GitHub Actions secrets: `GCP_PROJECT_ID`, `GCP_SA_KEY` (service account JSON), `GEMINI_API_KEY`.
