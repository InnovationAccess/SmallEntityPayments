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

    Deduplicates by (reel_frame, assignor_name, assignee_name) and returns
    the assignment history from earliest to latest.
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

    sql = f"""
    SELECT
      assignor_execution_date,
      assignor_name,
      conveyance_text,
      assignee_name,
      reel_frame,
      recorded_date
    FROM (
      SELECT
        assignor_execution_date,
        assignor_name,
        conveyance_text,
        assignee_name,
        reel_frame,
        recorded_date,
        ROW_NUMBER() OVER (
          PARTITION BY reel_frame, assignor_name, assignee_name
          ORDER BY recorded_date DESC
        ) AS rn
      FROM `{settings.assignments_table}`
      WHERE doc_number = @patent_number
    )
    WHERE rn = 1
    ORDER BY assignor_execution_date ASC, recorded_date ASC
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
