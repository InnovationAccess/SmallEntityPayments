"""Prosecution Payment Investigation API — 3-phase analysis of prosecution fees.

Phase 1: Entity discovery — find entities with N+ SMAL declarations (2016+)
Phase 2: Application drill-down — list applications for a selected entity
Phase 3: PDF extraction — extract fee codes from payment invoices (future)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from google.cloud import bigquery
from pydantic import BaseModel

from api.config import settings
from api.services.bigquery_service import bq_service

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
                COALESCE(p.first_applicant_name, p.first_inventor_name, 'UNKNOWN') AS applicant_name
            FROM smal_events s
            LEFT JOIN `{settings.patent_table}` p
                ON s.application_number = p.application_number
        )
        SELECT
            applicant_name,
            COUNT(*) AS smal_count,
            COUNT(DISTINCT application_number) AS app_count,
            MIN(event_date) AS earliest_date,
            MAX(event_date) AS latest_date
        FROM with_applicant
        GROUP BY applicant_name
        HAVING COUNT(*) >= @min_decl
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
        WHERE UPPER(COALESCE(p.first_applicant_name, p.first_inventor_name, ''))
              LIKE CONCAT('%', @applicant, '%')
        ORDER BY s.smal_count DESC, s.first_smal_date ASC
        LIMIT @lim
    """

    params = [
        bigquery.ScalarQueryParameter("applicant", "STRING", req.applicant_name.strip().upper()),
        bigquery.ScalarQueryParameter("date_from", "STRING", req.date_from),
        bigquery.ScalarQueryParameter("date_to", "STRING", req.date_to),
        bigquery.ScalarQueryParameter("lim", "INT64", req.limit),
    ]

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
        "date_from": req.date_from,
        "date_to": req.date_to,
        "results": results,
    }
