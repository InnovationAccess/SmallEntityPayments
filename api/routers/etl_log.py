"""ETL Log API endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from api.config import settings
from api.services.bigquery_service import bq_service

router = APIRouter(prefix="/api/etl-log", tags=["ETL Log"])


@router.get("")
def get_etl_log(limit: int = 100) -> Dict[str, Any]:
    """Get recent ETL pipeline run history.

    Returns log entries sorted by most recent first.
    """
    if limit > 500:
        limit = 500

    from google.cloud import bigquery

    sql = f"""
    SELECT
      run_id,
      source,
      status,
      FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S UTC', started_at) AS started_at,
      FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S UTC', completed_at) AS completed_at,
      files_processed,
      files_skipped,
      files_failed,
      rows_loaded,
      ROUND(duration_seconds, 1) AS duration_seconds,
      details,
      error_message
    FROM `{settings.GCP_PROJECT_ID}.{settings.BIGQUERY_DATASET}.etl_log`
    ORDER BY started_at DESC
    LIMIT @limit
    """
    params = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]

    rows = bq_service.run_query(sql, params)
    entries = []
    for row in rows:
        entries.append({
            "run_id": row.get("run_id"),
            "source": row.get("source"),
            "status": row.get("status"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "files_processed": row.get("files_processed"),
            "files_skipped": row.get("files_skipped"),
            "files_failed": row.get("files_failed"),
            "rows_loaded": row.get("rows_loaded"),
            "duration_seconds": row.get("duration_seconds"),
            "details": row.get("details"),
            "error_message": row.get("error_message"),
        })

    return {
        "total": len(entries),
        "entries": entries,
    }


@router.get("/summary")
def get_etl_summary() -> Dict[str, Any]:
    """Get summary of latest successful run per source."""
    sql = f"""
    WITH latest AS (
      SELECT *,
        ROW_NUMBER() OVER (PARTITION BY source ORDER BY started_at DESC) AS rn
      FROM `{settings.GCP_PROJECT_ID}.{settings.BIGQUERY_DATASET}.etl_log`
      WHERE status = 'success'
    )
    SELECT
      source,
      FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S UTC', started_at) AS last_success,
      files_processed,
      rows_loaded,
      ROUND(duration_seconds, 1) AS duration_seconds
    FROM latest
    WHERE rn = 1
    ORDER BY source
    """
    rows = bq_service.run_query(sql)
    summary = {}
    for row in rows:
        summary[row["source"]] = {
            "last_success": row.get("last_success"),
            "files_processed": row.get("files_processed"),
            "rows_loaded": row.get("rows_loaded"),
            "duration_seconds": row.get("duration_seconds"),
        }

    return {"sources": summary}
