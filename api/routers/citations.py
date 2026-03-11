"""Forward Citation Lookup API endpoints."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, status

from api.config import settings
from api.services.bigquery_service import bq_service

# Import shared patent number normalizer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utils.patent_number import normalize_patent_number

router = APIRouter(prefix="/api/forward-citations", tags=["Forward Citations"])


@router.get("/{patent_number}")
def get_forward_citations(patent_number: str, limit: int = 500) -> Dict[str, Any]:
    """Get all patents that cite the given patent number.

    Returns the full list of citing patents sorted by grant date descending,
    with normalized patent numbers and citation categories.
    """
    normalized = normalize_patent_number(patent_number)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid patent number: {patent_number}",
        )

    # Cap limit
    if limit > 5000:
        limit = 5000

    sql = f"""
    SELECT
      fc.citing_patent_number,
      fc.citing_grant_date,
      fc.citing_application_number,
      fc.citing_filing_date,
      fc.citation_category,
      fc.citing_kind_code,
      pfw.invention_title AS citing_invention_title,
      COALESCE(nu.representative_name, pfw.first_applicant_name) AS citing_applicant_name,
      pfw.examiner_name AS citing_examiner_name
    FROM `{settings.forward_citations_table}` fc
    LEFT JOIN `{settings.patent_table}` pfw
      ON pfw.patent_number = fc.citing_patent_number
    LEFT JOIN `{settings.unification_table}` nu
      ON nu.associated_name = pfw.first_applicant_name
    WHERE fc.cited_patent_number = @patent_number
    ORDER BY fc.citing_grant_date DESC
    LIMIT @limit
    """
    from google.cloud import bigquery

    params = [
        bigquery.ScalarQueryParameter("patent_number", "STRING", normalized),
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]

    rows = bq_service.run_query(sql, params)
    citations = []
    for row in rows:
        citations.append({
            "citing_patent_number": row.get("citing_patent_number"),
            "citing_grant_date": (
                row["citing_grant_date"].isoformat()
                if hasattr(row.get("citing_grant_date"), "isoformat")
                else row.get("citing_grant_date")
            ),
            "citing_application_number": row.get("citing_application_number"),
            "citing_filing_date": (
                row["citing_filing_date"].isoformat()
                if hasattr(row.get("citing_filing_date"), "isoformat")
                else row.get("citing_filing_date")
            ),
            "citation_category": row.get("citation_category"),
            "citing_kind_code": row.get("citing_kind_code"),
            "citing_invention_title": row.get("citing_invention_title"),
            "citing_applicant_name": row.get("citing_applicant_name"),
            "citing_examiner_name": row.get("citing_examiner_name"),
        })

    return {
        "cited_patent_number": normalized,
        "total_citations": len(citations),
        "citations": citations,
    }


@router.get("/{patent_number}/summary")
def get_citation_summary(patent_number: str) -> Dict[str, Any]:
    """Get aggregated citation statistics for a patent.

    Returns counts by category, counts by year, total count, and date range.
    """
    normalized = normalize_patent_number(patent_number)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid patent number: {patent_number}",
        )

    from google.cloud import bigquery

    params = [
        bigquery.ScalarQueryParameter("patent_number", "STRING", normalized),
    ]

    # Total count and date range
    total_sql = f"""
    SELECT
      COUNT(*) AS total_citations,
      MIN(citing_grant_date) AS earliest_citing_date,
      MAX(citing_grant_date) AS latest_citing_date
    FROM `{settings.forward_citations_table}`
    WHERE cited_patent_number = @patent_number
    """
    total_result = bq_service.run_query(total_sql, params)

    if not total_result or total_result[0]["total_citations"] == 0:
        return {
            "cited_patent_number": normalized,
            "total_citations": 0,
            "by_category": {},
            "by_year": {},
            "earliest_citing_date": None,
            "latest_citing_date": None,
            "by_examiner": [],
            "by_applicant": [],
        }

    total_row = total_result[0]

    # Count by category
    cat_sql = f"""
    SELECT citation_category, COUNT(*) AS count
    FROM `{settings.forward_citations_table}`
    WHERE cited_patent_number = @patent_number
    GROUP BY citation_category
    ORDER BY count DESC
    """
    cat_rows = bq_service.run_query(cat_sql, params)
    by_category = {row["citation_category"]: row["count"] for row in cat_rows}

    # Count by year
    year_sql = f"""
    SELECT EXTRACT(YEAR FROM citing_grant_date) AS year, COUNT(*) AS count
    FROM `{settings.forward_citations_table}`
    WHERE cited_patent_number = @patent_number
    GROUP BY year
    ORDER BY year
    """
    year_rows = bq_service.run_query(year_sql, params)
    by_year = {int(row["year"]): row["count"] for row in year_rows}

    # Examiner breakdown: only examiner-category citations
    # Uses COALESCE so NULLs become 'Unknown' and totals match the KPI
    exam_sql = f"""
    SELECT
      COALESCE(pfw.examiner_name, 'Unknown') AS name,
      COUNT(*) AS count
    FROM `{settings.forward_citations_table}` fc
    LEFT JOIN `{settings.patent_table}` pfw
      ON pfw.patent_number = fc.citing_patent_number
    WHERE fc.cited_patent_number = @patent_number
      AND fc.citation_category = 'examiner'
    GROUP BY name
    ORDER BY count DESC
    """
    exam_rows = bq_service.run_query(exam_sql, params)
    by_examiner = [
        {"name": r["name"], "count": r["count"]} for r in exam_rows
    ]

    # Applicant breakdown: only applicant-category citations
    # Resolves names via name_unification; NULLs become 'Unknown'
    appl_sql = f"""
    SELECT
      COALESCE(nu.representative_name, pfw.first_applicant_name, 'Unknown') AS name,
      COUNT(*) AS count
    FROM `{settings.forward_citations_table}` fc
    LEFT JOIN `{settings.patent_table}` pfw
      ON pfw.patent_number = fc.citing_patent_number
    LEFT JOIN `{settings.unification_table}` nu
      ON nu.associated_name = pfw.first_applicant_name
    WHERE fc.cited_patent_number = @patent_number
      AND fc.citation_category = 'applicant'
    GROUP BY name
    ORDER BY count DESC
    """
    appl_rows = bq_service.run_query(appl_sql, params)
    by_applicant = [
        {"name": r["name"], "count": r["count"]} for r in appl_rows
    ]

    return {
        "cited_patent_number": normalized,
        "total_citations": total_row["total_citations"],
        "by_category": by_category,
        "by_year": by_year,
        "earliest_citing_date": (
            total_row["earliest_citing_date"].isoformat()
            if hasattr(total_row.get("earliest_citing_date"), "isoformat")
            else total_row.get("earliest_citing_date")
        ),
        "latest_citing_date": (
            total_row["latest_citing_date"].isoformat()
            if hasattr(total_row.get("latest_citing_date"), "isoformat")
            else total_row.get("latest_citing_date")
        ),
        "by_examiner": by_examiner,
        "by_applicant": by_applicant,
    }
