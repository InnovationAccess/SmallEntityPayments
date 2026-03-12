"""Patent Assignment Chain API endpoint."""

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

router = APIRouter(prefix="/api/assignments", tags=["Assignments"])


@router.get("/{patent_number}/chain")
def get_assignment_chain(patent_number: str) -> Dict[str, Any]:
    """Get the chain of assignments for a patent, sorted by execution date.

    Resolves the input (patent_number or application_number) to
    application_number(s), then queries the normalized assignment tables.
    """
    normalized = normalize_patent_number(patent_number)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid patent number: {patent_number}",
        )

    from google.cloud import bigquery

    params = [
        bigquery.ScalarQueryParameter("id", "STRING", normalized),
    ]

    # Resolve input to application_number(s) via documents table,
    # then join through reel_frame to get assignment details.
    sql = f"""
    WITH matching_docs AS (
      SELECT DISTINCT reel_frame
      FROM `{settings.assign_documents_table}`
      WHERE application_number = @id
         OR patent_number = @id
    )
    SELECT
      ao.assignor_execution_date,
      ao.assignor_name,
      ar.conveyance_text,
      ae.assignee_name,
      ar.reel_frame,
      ar.recorded_date
    FROM matching_docs md
    JOIN `{settings.assign_records_table}` ar ON ar.reel_frame = md.reel_frame
    LEFT JOIN `{settings.assign_assignors_table}` ao ON ao.reel_frame = md.reel_frame
    LEFT JOIN `{settings.assign_assignees_table}` ae ON ae.reel_frame = md.reel_frame
    ORDER BY ao.assignor_execution_date ASC, ar.recorded_date ASC
    LIMIT 200
    """

    rows = bq_service.run_query(sql, params)

    chain = []
    for r in rows:
        exec_date = r.get("assignor_execution_date")
        chain.append({
            "execution_date": (
                exec_date.isoformat()
                if hasattr(exec_date, "isoformat")
                else str(exec_date) if exec_date else None
            ),
            "assignor": r.get("assignor_name") or "",
            "conveyance": r.get("conveyance_text") or "",
            "assignee": r.get("assignee_name") or "",
            "reel_frame": r.get("reel_frame") or "",
        })

    return {
        "patent_number": normalized,
        "assignments": chain,
    }
