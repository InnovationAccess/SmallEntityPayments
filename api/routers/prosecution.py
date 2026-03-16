"""Prosecution Payment Investigation API — 3-phase analysis of prosecution fees.

Phase 1: Entity discovery — find entities with N+ SMAL declarations (2016+)
Phase 2: Application drill-down — list applications for a selected entity
Phase 3: Document retrieval + fee code extraction from payment invoices
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from google.cloud import bigquery, storage
from pydantic import BaseModel

from api.config import settings
from api.services.bigquery_service import bq_service

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

USPTO_API_KEY = "inbsszqfwwnkjfebpibunnbllbygqz"
USPTO_DOC_API = "https://api.uspto.gov/api/v1/patent/applications/{}/documents"
GCS_BUCKET = "uspto-bulk-staging"
GCS_INVOICE_PREFIX = "prosecution-invoices"

# Keywords for identifying payment-related documents
PAYMENT_KEYWORDS = [
    "FEE WORKSHEET", "SB06", "PAYMENT RECEIPT", "FEE PAYMENT",
    "FEE RECORDATION", "FEE CALCULATION", "FEE SHEET",
]
# Document codes that are payment-related
PAYMENT_DOC_CODES = {"WFEE"}

router = APIRouter(prefix="/api/prosecution", tags=["Prosecution Payments"])


# ── Request / response models ────────────────────────────────────

class EntityDiscoveryRequest(BaseModel):
    """Phase 1: find entities with many SMAL declarations."""
    min_declarations: int = 1000
    limit: int = 200


class ApplicationDrilldownRequest(BaseModel):
    """Phase 2: list applications for a selected entity."""
    applicant_name: str
    date_from: str = "2016-01-01"
    date_to: str = "2026-12-31"
    limit: int = 5000


class DocumentListRequest(BaseModel):
    """Phase 3a: list payment-related documents for selected applications."""
    application_numbers: List[str]


class DocumentDownloadRequest(BaseModel):
    """Phase 3b: download specific documents to GCS."""
    documents: List[Dict[str, str]]  # [{app_number, download_url, filename}]


class DocumentExtractRequest(BaseModel):
    """Phase 3c: extract fee codes from a downloaded PDF."""
    gcs_path: str  # e.g. "prosecution-invoices/14414087/Fee_Worksheet.pdf"


# ── Phase 1: Entity discovery ────────────────────────────────────

@router.post("/entities")
def discover_entities(req: EntityDiscoveryRequest) -> Dict[str, Any]:
    """
    Find entities that have made at least `min_declarations` SMAL declarations
    in pfw_transactions from 2016 onwards.

    Joins pfw_transactions (SMAL events) with patent_file_wrapper_v2
    to get the applicant name, then groups by applicant.
    """
    if req.min_declarations < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_declarations must be >= 1",
        )

    sql = f"""
        WITH smal_events AS (
            SELECT
                t.application_number,
                t.event_date,
                t.event_code
            FROM `{settings.pfw_transactions_table}` t
            WHERE t.event_code = 'SMAL'
              AND t.event_date >= '2016-01-01'
        ),
        with_applicant AS (
            SELECT
                s.application_number,
                s.event_date,
                -- Normalize name variants via name_unification table (case-insensitive)
                COALESCE(
                    nu.representative_name,
                    p.first_applicant_name,
                    p.first_inventor_name,
                    'UNKNOWN'
                ) AS applicant_name
            FROM smal_events s
            LEFT JOIN `{settings.patent_table}` p
                ON s.application_number = p.application_number
            LEFT JOIN `{settings.unification_table}` nu
                ON UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name))
                    = UPPER(nu.associated_name)
        )
        SELECT
            applicant_name,
            COUNT(*) AS smal_count,
            COUNT(DISTINCT application_number) AS app_count,
            MIN(event_date) AS earliest_date,
            MAX(event_date) AS latest_date
        FROM with_applicant
        GROUP BY applicant_name
        HAVING applicant_name != 'UNKNOWN'
           AND COUNT(*) >= @min_decl
        ORDER BY smal_count DESC
        LIMIT @lim
    """

    params = [
        bigquery.ScalarQueryParameter("min_decl", "INT64", req.min_declarations),
        bigquery.ScalarQueryParameter("lim", "INT64", req.limit),
    ]

    rows = bq_service.run_query(sql, params)

    # Convert dates to strings for JSON serialisation
    results = []
    for r in rows:
        results.append({
            "applicant_name": r["applicant_name"],
            "smal_count": r["smal_count"],
            "app_count": r["app_count"],
            "earliest_date": str(r["earliest_date"]) if r["earliest_date"] else None,
            "latest_date": str(r["latest_date"]) if r["latest_date"] else None,
        })

    return {
        "total": len(results),
        "min_declarations": req.min_declarations,
        "results": results,
    }


# ── Phase 1b: Post-grant entity discovery (maintenance fees) ────

@router.post("/entities/post-grant")
def discover_post_grant_entities(req: EntityDiscoveryRequest) -> Dict[str, Any]:
    """
    Find entities with post-grant small entity activity in maintenance fees.

    Searches maintenance_fee_events_v2 for small + large entity fee payments
    and entity status declarations (SMAL, BIG., LTOS, STOL).

    Returns detailed breakdown columns:
      - small_1st/2nd/3rd: M2551/M2552/M2553 payment counts
      - large_1st/2nd/3rd: M1551/M1552/M1553 payment counts
      - small_decl_total: post-grant SMALL declarations (SMAL, LTOS events)
      - large_decl_total: post-grant LARGE declarations (BIG., STOL events)
    """
    if req.min_declarations < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_declarations must be >= 1",
        )

    sql = f"""
        WITH maint_events AS (
            SELECT
                m.patent_number,
                m.event_code,
                m.event_date
            FROM `{settings.maintenance_table}` m
            WHERE m.event_code LIKE 'M1%'
               OR m.event_code LIKE 'M2%'
               OR m.event_code LIKE 'F17%'
               OR m.event_code LIKE 'F27%'
               OR m.event_code IN ('SMAL', 'BIG.', 'LTOS', 'STOL')
        ),
        with_applicant AS (
            SELECT
                me.patent_number,
                me.event_code,
                me.event_date,
                COALESCE(
                    nu.representative_name,
                    p.first_applicant_name,
                    p.first_inventor_name,
                    'UNKNOWN'
                ) AS applicant_name
            FROM maint_events me
            LEFT JOIN `{settings.patent_table}` p
                ON me.patent_number = p.patent_number
            LEFT JOIN `{settings.unification_table}` nu
                ON UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name))
                    = UPPER(nu.associated_name)
        )
        SELECT
            applicant_name,
            -- Small entity maintenance fee payments
            COUNT(CASE WHEN event_code = 'M2551' THEN 1 END) AS small_1st,
            COUNT(CASE WHEN event_code = 'M2552' THEN 1 END) AS small_2nd,
            COUNT(CASE WHEN event_code = 'M2553' THEN 1 END) AS small_3rd,
            -- Large entity maintenance fee payments
            COUNT(CASE WHEN event_code = 'M1551' THEN 1 END) AS large_1st,
            COUNT(CASE WHEN event_code = 'M1552' THEN 1 END) AS large_2nd,
            COUNT(CASE WHEN event_code = 'M1553' THEN 1 END) AS large_3rd,
            -- Post-grant entity status declarations
            COUNT(CASE WHEN event_code IN ('SMAL', 'LTOS') THEN 1 END) AS small_decl_total,
            COUNT(CASE WHEN event_code IN ('BIG.', 'STOL') THEN 1 END) AS large_decl_total,
            -- Summary
            COUNT(DISTINCT patent_number) AS patent_count,
            MIN(event_date) AS earliest_date,
            MAX(event_date) AS latest_date
        FROM with_applicant
        GROUP BY applicant_name
        HAVING applicant_name != 'UNKNOWN'
           AND (
            COUNT(CASE WHEN event_code LIKE 'M2%' OR event_code LIKE 'F27%'
                         OR event_code IN ('SMAL', 'LTOS') THEN 1 END)
        ) >= @min_decl
        ORDER BY small_1st + small_2nd + small_3rd DESC
        LIMIT @lim
    """

    params = [
        bigquery.ScalarQueryParameter("min_decl", "INT64", req.min_declarations),
        bigquery.ScalarQueryParameter("lim", "INT64", req.limit),
    ]

    rows = bq_service.run_query(sql, params)

    results = []
    for r in rows:
        results.append({
            "applicant_name": r["applicant_name"],
            "small_1st": r["small_1st"],
            "small_2nd": r["small_2nd"],
            "small_3rd": r["small_3rd"],
            "large_1st": r["large_1st"],
            "large_2nd": r["large_2nd"],
            "large_3rd": r["large_3rd"],
            "small_decl_total": r["small_decl_total"],
            "large_decl_total": r["large_decl_total"],
            "patent_count": r["patent_count"],
            "earliest_date": str(r["earliest_date"]) if r["earliest_date"] else None,
            "latest_date": str(r["latest_date"]) if r["latest_date"] else None,
        })

    return {
        "total": len(results),
        "min_declarations": req.min_declarations,
        "mode": "post-grant",
        "results": results,
    }


# ── Phase 1c: Combined prosecution + post-grant discovery ───────

@router.post("/entities/combined")
def discover_combined_entities(req: EntityDiscoveryRequest) -> Dict[str, Any]:
    """
    Find entities with the highest total small entity activity across BOTH
    prosecution (SMAL declarations in file wrapper) and post-grant
    (maintenance fee payments + declarations).

    Uses FULL OUTER JOIN so entities appearing in only one source are included.
    """
    if req.min_declarations < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_declarations must be >= 1",
        )

    sql = f"""
        WITH -- Prosecution: SMAL declarations from file wrapper
        smal_events AS (
            SELECT
                t.application_number,
                t.event_date
            FROM `{settings.pfw_transactions_table}` t
            WHERE t.event_code = 'SMAL'
              AND t.event_date >= '2016-01-01'
        ),
        prosecution AS (
            SELECT
                COALESCE(nu.representative_name, p.first_applicant_name,
                         p.first_inventor_name, 'UNKNOWN') AS applicant_name,
                COUNT(*) AS smal_count,
                COUNT(DISTINCT s.application_number) AS app_count,
                MIN(s.event_date) AS p_earliest,
                MAX(s.event_date) AS p_latest
            FROM smal_events s
            LEFT JOIN `{settings.patent_table}` p
                ON s.application_number = p.application_number
            LEFT JOIN `{settings.unification_table}` nu
                ON UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name))
                    = UPPER(nu.associated_name)
            GROUP BY applicant_name
        ),
        -- Post-grant: maintenance fee events
        maint_events AS (
            SELECT m.patent_number, m.event_code, m.event_date
            FROM `{settings.maintenance_table}` m
            WHERE m.event_code LIKE 'M1%'
               OR m.event_code LIKE 'M2%'
               OR m.event_code LIKE 'F17%'
               OR m.event_code LIKE 'F27%'
               OR m.event_code IN ('SMAL', 'BIG.', 'LTOS', 'STOL')
        ),
        postgrant AS (
            SELECT
                COALESCE(nu.representative_name, p.first_applicant_name,
                         p.first_inventor_name, 'UNKNOWN') AS applicant_name,
                COUNT(CASE WHEN me.event_code = 'M2551' THEN 1 END) AS small_1st,
                COUNT(CASE WHEN me.event_code = 'M2552' THEN 1 END) AS small_2nd,
                COUNT(CASE WHEN me.event_code = 'M2553' THEN 1 END) AS small_3rd,
                COUNT(CASE WHEN me.event_code = 'M1551' THEN 1 END) AS large_1st,
                COUNT(CASE WHEN me.event_code = 'M1552' THEN 1 END) AS large_2nd,
                COUNT(CASE WHEN me.event_code = 'M1553' THEN 1 END) AS large_3rd,
                COUNT(CASE WHEN me.event_code IN ('SMAL', 'LTOS') THEN 1 END) AS small_decl_total,
                COUNT(CASE WHEN me.event_code IN ('BIG.', 'STOL') THEN 1 END) AS large_decl_total,
                COUNT(DISTINCT me.patent_number) AS patent_count,
                MIN(me.event_date) AS pg_earliest,
                MAX(me.event_date) AS pg_latest
            FROM maint_events me
            LEFT JOIN `{settings.patent_table}` p
                ON me.patent_number = p.patent_number
            LEFT JOIN `{settings.unification_table}` nu
                ON UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name))
                    = UPPER(nu.associated_name)
            GROUP BY applicant_name
        )
        SELECT
            COALESCE(pr.applicant_name, pg.applicant_name) AS applicant_name,
            COALESCE(pr.smal_count, 0) AS smal_count,
            COALESCE(pr.app_count, 0) AS app_count,
            COALESCE(pg.small_1st, 0) AS small_1st,
            COALESCE(pg.small_2nd, 0) AS small_2nd,
            COALESCE(pg.small_3rd, 0) AS small_3rd,
            COALESCE(pg.large_1st, 0) AS large_1st,
            COALESCE(pg.large_2nd, 0) AS large_2nd,
            COALESCE(pg.large_3rd, 0) AS large_3rd,
            COALESCE(pg.small_decl_total, 0) AS small_decl_total,
            COALESCE(pg.large_decl_total, 0) AS large_decl_total,
            COALESCE(pg.patent_count, 0) AS patent_count,
            LEAST(pr.p_earliest, pg.pg_earliest) AS earliest_date,
            GREATEST(pr.p_latest, pg.pg_latest) AS latest_date
        FROM prosecution pr
        FULL OUTER JOIN postgrant pg
            ON pr.applicant_name = pg.applicant_name
        WHERE COALESCE(pr.applicant_name, pg.applicant_name) != 'UNKNOWN'
          AND (
            COALESCE(pr.smal_count, 0)
            + COALESCE(pg.small_1st, 0)
            + COALESCE(pg.small_2nd, 0)
            + COALESCE(pg.small_3rd, 0)
            + COALESCE(pg.small_decl_total, 0)
        ) >= @min_decl
        ORDER BY (
            COALESCE(pr.smal_count, 0)
            + COALESCE(pg.small_1st, 0)
            + COALESCE(pg.small_2nd, 0)
            + COALESCE(pg.small_3rd, 0)
            + COALESCE(pg.small_decl_total, 0)
        ) DESC
        LIMIT @lim
    """

    params = [
        bigquery.ScalarQueryParameter("min_decl", "INT64", req.min_declarations),
        bigquery.ScalarQueryParameter("lim", "INT64", req.limit),
    ]

    rows = bq_service.run_query(sql, params)

    results = []
    for r in rows:
        results.append({
            "applicant_name": r["applicant_name"],
            "smal_count": r["smal_count"],
            "app_count": r["app_count"],
            "small_1st": r["small_1st"],
            "small_2nd": r["small_2nd"],
            "small_3rd": r["small_3rd"],
            "large_1st": r["large_1st"],
            "large_2nd": r["large_2nd"],
            "large_3rd": r["large_3rd"],
            "small_decl_total": r["small_decl_total"],
            "large_decl_total": r["large_decl_total"],
            "patent_count": r["patent_count"],
            "earliest_date": str(r["earliest_date"]) if r["earliest_date"] else None,
            "latest_date": str(r["latest_date"]) if r["latest_date"] else None,
        })

    return {
        "total": len(results),
        "min_declarations": req.min_declarations,
        "mode": "combined",
        "results": results,
    }


# ── Phase 1d: 3rd maintenance fee payments at small entity rate ──

@router.post("/entities/3rd-small")
def discover_3rd_small_entities(req: EntityDiscoveryRequest) -> Dict[str, Any]:
    """
    Find entities with the most 3rd maintenance fee payments (11.5yr) at small
    entity rates (M2553).  Strategic signal: the 3rd maintenance fee is so
    expensive that paying it indicates the patent generates revenue.
    """
    if req.min_declarations < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="min_declarations must be >= 1",
        )

    sql = f"""
        WITH m2553_events AS (
            SELECT
                m.patent_number,
                m.event_date
            FROM `{settings.maintenance_table}` m
            WHERE m.event_code = 'M2553'
        ),
        with_applicant AS (
            SELECT
                e.patent_number,
                e.event_date,
                COALESCE(
                    nu.representative_name,
                    p.first_applicant_name,
                    p.first_inventor_name,
                    'UNKNOWN'
                ) AS applicant_name
            FROM m2553_events e
            LEFT JOIN `{settings.patent_table}` p
                ON e.patent_number = p.patent_number
            LEFT JOIN `{settings.unification_table}` nu
                ON UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name))
                    = UPPER(nu.associated_name)
        )
        SELECT
            applicant_name,
            COUNT(*) AS m2553_count,
            COUNT(DISTINCT patent_number) AS patent_count,
            MIN(event_date) AS earliest_date,
            MAX(event_date) AS latest_date
        FROM with_applicant
        GROUP BY applicant_name
        HAVING applicant_name != 'UNKNOWN'
           AND COUNT(*) >= @min_decl
        ORDER BY m2553_count DESC
        LIMIT @lim
    """

    params = [
        bigquery.ScalarQueryParameter("min_decl", "INT64", req.min_declarations),
        bigquery.ScalarQueryParameter("lim", "INT64", req.limit),
    ]

    rows = bq_service.run_query(sql, params)

    results = []
    for r in rows:
        results.append({
            "applicant_name": r["applicant_name"],
            "m2553_count": r["m2553_count"],
            "patent_count": r["patent_count"],
            "earliest_date": str(r["earliest_date"]) if r["earliest_date"] else None,
            "latest_date": str(r["latest_date"]) if r["latest_date"] else None,
        })

    return {
        "total": len(results),
        "min_declarations": req.min_declarations,
        "mode": "3rd-small",
        "results": results,
    }


# ── Phase 2: Application drill-down ─────────────────────────────

@router.post("/applications")
def list_applications(req: ApplicationDrilldownRequest) -> Dict[str, Any]:
    """
    For a selected entity, list all applications that have SMAL declarations
    within the specified date range.

    Returns application details with declaration counts and dates.
    """
    if not req.applicant_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="applicant_name is required",
        )

    # Expand the name through name_unification (if normalized)
    expanded = bq_service.expand_name_for_query(req.applicant_name.strip())

    params = [
        bigquery.ScalarQueryParameter("date_from", "STRING", req.date_from),
        bigquery.ScalarQueryParameter("date_to", "STRING", req.date_to),
        bigquery.ScalarQueryParameter("lim", "INT64", req.limit),
    ]

    # Build the applicant filter: IN clause for expanded names, LIKE for single
    if len(expanded) > 1:
        for i, name in enumerate(expanded):
            params.append(bigquery.ScalarQueryParameter(f"name_{i}", "STRING", name))
        name_in = ", ".join(f"@name_{i}" for i in range(len(expanded)))
        applicant_filter = f"COALESCE(p.first_applicant_name, p.first_inventor_name) IN ({name_in})"
    else:
        params.append(bigquery.ScalarQueryParameter(
            "applicant", "STRING", req.applicant_name.strip().upper(),
        ))
        applicant_filter = "UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name, '')) = @applicant"

    sql = f"""
        WITH smal_events AS (
            SELECT
                t.application_number,
                COUNT(*) AS smal_count,
                MIN(t.event_date) AS first_smal_date,
                MAX(t.event_date) AS last_smal_date
            FROM `{settings.pfw_transactions_table}` t
            WHERE t.event_code = 'SMAL'
              AND t.event_date >= @date_from
              AND t.event_date <= @date_to
            GROUP BY t.application_number
        )
        SELECT
            s.application_number,
            p.patent_number,
            p.invention_title,
            p.filing_date,
            p.grant_date,
            COALESCE(p.first_applicant_name, p.first_inventor_name) AS applicant_name,
            p.application_status,
            s.smal_count,
            s.first_smal_date,
            s.last_smal_date
        FROM smal_events s
        JOIN `{settings.patent_table}` p
            ON s.application_number = p.application_number
        WHERE {applicant_filter}
        ORDER BY s.smal_count DESC, s.first_smal_date ASC
        LIMIT @lim
    """

    rows = bq_service.run_query(sql, params)

    results = []
    for r in rows:
        results.append({
            "application_number": r["application_number"],
            "patent_number": r.get("patent_number"),
            "invention_title": r.get("invention_title"),
            "filing_date": str(r["filing_date"]) if r.get("filing_date") else None,
            "grant_date": str(r["grant_date"]) if r.get("grant_date") else None,
            "applicant_name": r.get("applicant_name"),
            "application_status": r.get("application_status"),
            "smal_count": r["smal_count"],
            "first_smal_date": str(r["first_smal_date"]) if r.get("first_smal_date") else None,
            "last_smal_date": str(r["last_smal_date"]) if r.get("last_smal_date") else None,
        })

    return {
        "total": len(results),
        "applicant_name": req.applicant_name,
        "expanded_names": expanded,
        "date_from": req.date_from,
        "date_to": req.date_to,
        "results": results,
    }


# ── Phase 3: Document retrieval + extraction ───────────────────

GEMINI_PROJECT = "uspto-data-app"
GEMINI_LOCATION = "us-central1"
GEMINI_MODEL = "gemini-2.5-flash"

GEMINI_PROMPT = """You are analyzing a USPTO patent payment document (PDF image).

Extract ALL of the following information as structured JSON:

1. **doc_type**: One of:
   - "FEE_WORKSHEET_SB06" — PTO/SB/06 or PTO-875 Fee Determination Record
   - "ELECTRONIC_FEE_TRANSMITTAL" — Electronic Patent Application Fee Transmittal
   - "ISSUE_FEE_PTO85B" — PTO-85B / Part B Fee(s) Transmittal / Issue Fee Payment
   - "ELECTRONIC_PAYMENT_RECEIPT" — Electronic Payment Receipt
   - "UNKNOWN" — none of the above

2. **application_number**: The patent application number (digits only, no slashes/commas)

3. **filing_date**: Filing date if shown

4. **entity_status**: Look for:
   - Checked checkboxes next to LARGE, SMALL, or MICRO
   - Text like "Filed as Small Entity" or "ENTITY STATUS: SMALL"
   - Column headers: if fees are in "SMALL ENTITY" column, entity_status = "SMALL"
   - Return: "SMALL", "LARGE", "MICRO", or null

5. **title**: Title of invention if shown

6. **fees**: Array of fee line items. For each fee:
   - **fee_code**: Numeric fee code (e.g. "2820", "1833") — null if not shown
   - **description**: Fee description
   - **amount**: Dollar amount per item
   - **quantity**: How many (default 1)
   - **item_total**: Line total if shown

   For SB06 forms with fee rates (not codes), extract the fee type + amount.

7. **total_amount**: Total payment if shown

8. **assignee_name**: Name of assignee if shown

9. **issue_fee_due**: For PTO-85B forms, issue fee due amount

10. **entity_status_evidence**: Brief quote proving the entity status

Return ONLY valid JSON. No markdown, no code fences, no explanation."""


def _get_gcp_access_token() -> str:
    """Get access token using default credentials (works on Cloud Run + local gcloud)."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _is_payment_doc(doc: dict) -> bool:
    """Check if a USPTO document is payment-related by code or description."""
    code = (doc.get("documentCode") or "").upper()
    desc = (doc.get("documentCodeDescriptionText") or "").upper()
    return code in PAYMENT_DOC_CODES or any(kw in desc for kw in PAYMENT_KEYWORDS)


@router.post("/documents")
def list_payment_documents(req: DocumentListRequest) -> Dict[str, Any]:
    """
    Phase 3a: For selected application numbers, query the USPTO ODP API
    to find payment-related documents (fee worksheets, payment receipts, etc.).

    Returns a flat list of matching documents with download URLs.
    """
    if not req.application_numbers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No application numbers provided",
        )
    if len(req.application_numbers) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 200 applications per request",
        )

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    with httpx.Client(timeout=30) as client:
        for app_num in req.application_numbers:
            try:
                url = USPTO_DOC_API.format(app_num)
                resp = client.get(url, headers={
                    "X-API-KEY": USPTO_API_KEY,
                    "Accept": "application/json",
                })

                if resp.status_code != 200:
                    errors.append({
                        "app": app_num,
                        "error": f"HTTP {resp.status_code}",
                    })
                    continue

                data = resp.json()
                doc_bag = data.get("documentBag", [])

                for doc in doc_bag:
                    if not _is_payment_doc(doc):
                        continue

                    downloads = doc.get("downloadOptionBag", [])
                    download_url = None
                    if downloads:
                        download_url = downloads[0].get("downloadUrl")

                    results.append({
                        "app_number": app_num,
                        "doc_id": doc.get("documentIdentifier"),
                        "doc_code": doc.get("documentCode"),
                        "description": doc.get("documentCodeDescriptionText"),
                        "mail_date": doc.get("officialDate"),
                        "download_url": download_url,
                        "page_count": doc.get("pageCount"),
                        "filename": (
                            f"{app_num}_{doc.get('documentCode', 'DOC')}"
                            f"_{doc.get('documentIdentifier', 'unknown')}.pdf"
                        ),
                    })

            except httpx.TimeoutException:
                errors.append({"app": app_num, "error": "Request timed out"})
            except Exception as e:
                errors.append({"app": app_num, "error": str(e)})
                logger.warning("Failed to query docs for %s: %s", app_num, e)

    return {
        "total": len(results),
        "apps_queried": len(req.application_numbers),
        "apps_with_errors": len(errors),
        "errors": errors,
        "results": results,
    }


@router.post("/download")
def download_documents(req: DocumentDownloadRequest) -> Dict[str, Any]:
    """
    Phase 3b: Download selected PDF documents from USPTO and save to GCS.

    Each document dict must have: app_number, download_url, filename.
    PDFs are saved to gs://uspto-bulk-staging/prosecution-invoices/{app_number}/{filename}.
    """
    if not req.documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No documents to download",
        )
    if len(req.documents) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 documents per request",
        )

    gcs_client = storage.Client()
    bucket = gcs_client.bucket(GCS_BUCKET)
    downloaded: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for doc in req.documents:
            app_num = doc.get("app_number", "unknown")
            download_url = doc.get("download_url")
            filename = doc.get("filename", "document.pdf")

            if not download_url:
                errors.append({"filename": filename, "error": "No download URL"})
                continue

            try:
                resp = client.get(download_url, headers={
                    "X-API-KEY": USPTO_API_KEY,
                    "Accept": "application/pdf",
                })

                if resp.status_code != 200:
                    errors.append({
                        "filename": filename,
                        "error": f"HTTP {resp.status_code}",
                    })
                    continue

                # Upload to GCS
                gcs_path = f"{GCS_INVOICE_PREFIX}/{app_num}/{filename}"
                blob = bucket.blob(gcs_path)
                blob.upload_from_string(
                    resp.content,
                    content_type="application/pdf",
                )

                downloaded.append({
                    "app_number": app_num,
                    "filename": filename,
                    "gcs_path": gcs_path,
                    "size_bytes": len(resp.content),
                })

            except httpx.TimeoutException:
                errors.append({"filename": filename, "error": "Download timed out"})
            except Exception as e:
                errors.append({"filename": filename, "error": str(e)})
                logger.warning("Failed to download %s: %s", filename, e)

    return {
        "total_downloaded": len(downloaded),
        "total_errors": len(errors),
        "downloaded": downloaded,
        "errors": errors,
    }


@router.post("/extract")
def extract_fee_codes(req: DocumentExtractRequest) -> Dict[str, Any]:
    """
    Phase 3c: Read a PDF from GCS and run Gemini Vision extraction
    to get entity status, fee codes, and payment amounts.

    Returns structured JSON with doc_type, entity_status, fees[], etc.
    """
    if not req.gcs_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="gcs_path is required",
        )

    # 1. Read PDF from GCS
    try:
        gcs_client = storage.Client()
        bucket = gcs_client.bucket(GCS_BUCKET)
        blob = bucket.blob(req.gcs_path)
        if not blob.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PDF not found in GCS: {req.gcs_path}",
            )
        pdf_bytes = blob.download_as_bytes()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read from GCS: {e}",
        )

    # 2. Base64-encode the PDF
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    # 3. Call Gemini Vision via Vertex AI REST API
    try:
        token = _get_gcp_access_token()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get GCP credentials: {e}",
        )

    vertex_url = (
        f"https://{GEMINI_LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{GEMINI_PROJECT}/locations/{GEMINI_LOCATION}/"
        f"publishers/google/models/{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": GEMINI_PROMPT},
                {
                    "inlineData": {
                        "mimeType": "application/pdf",
                        "data": pdf_b64,
                    }
                },
            ],
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
        },
    }

    try:
        with httpx.Client(timeout=90) as client:
            resp = client.post(
                vertex_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Gemini Vision request timed out",
        )

    if resp.status_code != 200:
        logger.error("Gemini API error %d: %s", resp.status_code, resp.text[:500])
        return {
            "gcs_path": req.gcs_path,
            "doc_type": "UNKNOWN",
            "error": f"Gemini API error {resp.status_code}",
            "entity_status": None,
            "fees": [],
        }

    # 4. Parse Gemini response
    resp_json = resp.json()
    candidates = resp_json.get("candidates", [])
    if not candidates:
        return {
            "gcs_path": req.gcs_path,
            "doc_type": "UNKNOWN",
            "error": "No candidates in Gemini response",
            "entity_status": None,
            "fees": [],
        }

    text = candidates[0]["content"]["parts"][0]["text"].strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except json.JSONDecodeError:
                result = {
                    "doc_type": "UNKNOWN",
                    "error": "Could not parse Gemini response",
                    "entity_status": None,
                    "fees": [],
                }
        else:
            result = {
                "doc_type": "UNKNOWN",
                "error": "No JSON in Gemini response",
                "entity_status": None,
                "fees": [],
            }

    result["gcs_path"] = req.gcs_path
    result["extraction_method"] = "gemini_vision"
    return result
