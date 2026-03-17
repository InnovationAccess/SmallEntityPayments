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

    # Resolve input to application_number(s) first to avoid collisions
    # between patent numbers and application numbers.
    # E.g. patent 11172434 (ETRI) vs application 11/172,434 (Stryker).
    # Step 1: resolve patent_number → application_number via file wrapper
    # Step 2: find assignment reel_frames for that application_number
    # Step 3: fall back to direct patent_number match if no file wrapper record
    sql = f"""
    WITH resolved_app AS (
      SELECT DISTINCT application_number
      FROM `{settings.patent_table}`
      WHERE patent_number = @id
    ),
    matching_docs AS (
      -- Primary: match by resolved application_number from file wrapper
      SELECT DISTINCT reel_frame
      FROM `{settings.assign_documents_table}` d
      WHERE d.application_number IN (SELECT application_number FROM resolved_app)

      UNION DISTINCT

      -- Fallback: direct patent_number match in assignment documents
      -- (covers cases where file wrapper has no record for this patent)
      SELECT DISTINCT reel_frame
      FROM `{settings.assign_documents_table}` d
      WHERE d.patent_number = @id
    ),
    -- Aggregate assignors per reel_frame to avoid cross-product duplicates
    agg_assignors AS (
      SELECT
        reel_frame,
        STRING_AGG(DISTINCT assignor_name, '; ' ORDER BY assignor_name) AS assignors,
        MIN(assignor_execution_date) AS execution_date
      FROM `{settings.assign_assignors_table}`
      WHERE reel_frame IN (SELECT reel_frame FROM matching_docs)
      GROUP BY reel_frame
    ),
    -- Aggregate assignees per reel_frame
    agg_assignees AS (
      SELECT
        reel_frame,
        STRING_AGG(DISTINCT assignee_name, '; ' ORDER BY assignee_name) AS assignees
      FROM `{settings.assign_assignees_table}`
      WHERE reel_frame IN (SELECT reel_frame FROM matching_docs)
      GROUP BY reel_frame
    )
    SELECT
      ao.execution_date,
      ao.assignors,
      ar.conveyance_text,
      ae.assignees,
      ar.reel_frame,
      ar.recorded_date
    FROM matching_docs md
    JOIN `{settings.assign_records_table}` ar ON ar.reel_frame = md.reel_frame
    LEFT JOIN agg_assignors ao ON ao.reel_frame = md.reel_frame
    LEFT JOIN agg_assignees ae ON ae.reel_frame = md.reel_frame
    ORDER BY ao.execution_date ASC, ar.recorded_date ASC
    LIMIT 200
    """

    rows = bq_service.run_query(sql, params)

    chain = []
    for r in rows:
        exec_date = r.get("execution_date")
        chain.append({
            "execution_date": (
                exec_date.isoformat()
                if hasattr(exec_date, "isoformat")
                else str(exec_date) if exec_date else None
            ),
            "assignor": r.get("assignors") or "",
            "conveyance": r.get("conveyance_text") or "",
            "assignee": r.get("assignees") or "",
            "reel_frame": r.get("reel_frame") or "",
        })

    return {
        "patent_number": normalized,
        "assignments": chain,
    }
