"""SEC Leads API endpoints — serve patent importance analysis results."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from google.cloud import bigquery

from api.config import settings
from api.services.bigquery_service import bq_service

router = APIRouter(prefix="/api/sec-leads", tags=["SEC Leads"])


@router.get("/reports")
def list_reports(limit: int = 30) -> Dict[str, Any]:
    """List available report dates with summary counts."""
    if limit > 100:
        limit = 100

    sql = f"""
    SELECT
      FORMAT_DATE('%Y-%m-%d', analysis_date) AS analysis_date,
      COUNT(*) AS total_companies,
      COUNTIF(score >= 5) AS score_5_plus,
      COUNTIF(score >= 7) AS score_7_plus,
      MAX(created_at) AS report_generated
    FROM `{settings.sec_leads_table}`
    GROUP BY analysis_date
    ORDER BY analysis_date DESC
    LIMIT @limit
    """
    params = [bigquery.ScalarQueryParameter("limit", "INT64", limit)]
    rows = bq_service.run_query(sql, params)

    reports = []
    for row in rows:
        reports.append({
            "analysis_date": row.get("analysis_date"),
            "total_companies": row.get("total_companies", 0),
            "score_5_plus": row.get("score_5_plus", 0),
            "score_7_plus": row.get("score_7_plus", 0),
            "report_generated": str(row.get("report_generated", "")),
        })

    return {"reports": reports}


@router.get("/reports/latest")
def get_latest_report() -> Dict[str, Any]:
    """Get all results for the most recent analysis date."""
    sql = f"""
    SELECT *
    FROM `{settings.sec_leads_table}`
    WHERE analysis_date = (
      SELECT MAX(analysis_date) FROM `{settings.sec_leads_table}`
    )
    ORDER BY score DESC, company_name ASC
    """
    rows = bq_service.run_query(sql)
    if not rows:
        return {"results": [], "stats": {}}

    return _format_report_response(rows)


@router.get("/reports/{report_date}")
def get_report(report_date: str) -> Dict[str, Any]:
    """Get all results for a specific analysis date."""
    sql = f"""
    SELECT *
    FROM `{settings.sec_leads_table}`
    WHERE analysis_date = @report_date
    ORDER BY score DESC, company_name ASC
    """
    params = [bigquery.ScalarQueryParameter("report_date", "DATE", report_date)]
    rows = bq_service.run_query(sql, params)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No report found for {report_date}")

    return _format_report_response(rows)


@router.get("/reports/{report_date}/{ticker}/memo")
def get_memo(report_date: str, ticker: str) -> Dict[str, Any]:
    """Get the memo text for a specific company on a specific date."""
    sql = f"""
    SELECT memo_text, company_name
    FROM `{settings.sec_leads_table}`
    WHERE analysis_date = @report_date AND ticker = @ticker
    LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("report_date", "DATE", report_date),
        bigquery.ScalarQueryParameter("ticker", "STRING", ticker.upper()),
    ]
    rows = bq_service.run_query(sql, params)
    if not rows:
        raise HTTPException(status_code=404, detail="Memo not found")

    return {
        "company_name": rows[0].get("company_name", ""),
        "memo_text": rows[0].get("memo_text", ""),
    }


@router.get("/reports/{report_date}/{ticker}/letter")
def get_letter(report_date: str, ticker: str) -> Dict[str, Any]:
    """Get the letter text for a specific company on a specific date."""
    sql = f"""
    SELECT letter_text, company_name
    FROM `{settings.sec_leads_table}`
    WHERE analysis_date = @report_date AND ticker = @ticker
    LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("report_date", "DATE", report_date),
        bigquery.ScalarQueryParameter("ticker", "STRING", ticker.upper()),
    ]
    rows = bq_service.run_query(sql, params)
    if not rows:
        raise HTTPException(status_code=404, detail="Letter not found")

    return {
        "company_name": rows[0].get("company_name", ""),
        "letter_text": rows[0].get("letter_text", ""),
    }


def _format_report_response(rows: list) -> Dict[str, Any]:
    """Format BigQuery rows into the API response structure."""
    results = []
    for row in rows:
        results.append({
            "analysis_date": str(row.get("analysis_date", "")),
            "company_name": row.get("company_name", ""),
            "ticker": row.get("ticker", ""),
            "cik": row.get("cik", ""),
            "filing_date": str(row.get("filing_date", "")),
            "filing_url": row.get("filing_url", ""),
            "score": row.get("score", 0),
            "gist": row.get("gist", ""),
            "secretary_name": row.get("secretary_name"),
            "secretary_title": row.get("secretary_title"),
            "secretary_email": row.get("secretary_email"),
            "general_counsel_name": row.get("general_counsel_name"),
            "general_counsel_title": row.get("general_counsel_title"),
            "general_counsel_email": row.get("general_counsel_email"),
            "board_chair_name": row.get("board_chair_name"),
            "board_chair_title": row.get("board_chair_title"),
            "board_chair_email": row.get("board_chair_email"),
            "ceo_name": row.get("ceo_name"),
            "cfo_name": row.get("cfo_name"),
            "board_members_json": row.get("board_members_json", "[]"),
            "memo_text": row.get("memo_text"),
            "letter_text": row.get("letter_text"),
            "apollo_enriched": row.get("apollo_enriched", False),
        })

    # Compute stats
    total = len(results)
    score_5_plus = sum(1 for r in results if r["score"] >= 5)
    score_7_plus = sum(1 for r in results if r["score"] >= 7)
    analysis_date = results[0]["analysis_date"] if results else ""

    return {
        "stats": {
            "total_companies": total,
            "score_5_plus": score_5_plus,
            "score_7_plus": score_7_plus,
            "analysis_date": analysis_date,
        },
        "results": results,
    }
