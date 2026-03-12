"""Entity Status Analytics API — small-to-large conversion patterns.

Entity status is DERIVED from maintenance fee event codes, NOT from the
entity_status column (which the USPTO populates inconsistently).

Event code → entity status mapping:
  M1xxx / F17xx → LARGE
  M2xxx / F27xx → SMALL
  M3xxx         → MICRO

Payment code families:
  M*551 = 3.5-year   M*552 = 7.5-year   M*553 = 11.5-year
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from google.cloud import bigquery
from pydantic import BaseModel

from api.config import settings
from api.services.bigquery_service import bq_service

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utils.patent_number import normalize_patent_number

router = APIRouter(prefix="/api/entity-status", tags=["Entity Status"])

# ── SQL fragment: derive entity status from event_code ────────────
# Used in all queries instead of trusting the entity_status column.
DERIVE_STATUS_SQL = """
  CASE
    WHEN event_code LIKE 'M1%' OR event_code LIKE 'F17%' THEN 'LARGE'
    WHEN event_code LIKE 'M2%' OR event_code LIKE 'F27%' THEN 'SMALL'
    WHEN event_code LIKE 'M3%' THEN 'MICRO'
    ELSE NULL
  END
"""


# ── Request models ────────────────────────────────────────────────

class ConversionSearchRequest(BaseModel):
    from_status: str = "SMALL"
    to_status: str = "LARGE"
    grant_year_start: int = 2010
    grant_year_end: int = 2025
    applicant_name: Optional[str] = None
    limit: int = 200


class ApplicantRequest(BaseModel):
    applicant_name: str
    limit: int = 500


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/summary")
def get_summary() -> Dict[str, Any]:
    """Aggregate entity status statistics and conversion rates by year.

    Entity status is derived from event codes, not the entity_status column.
    """

    # 1. Distribution of entity status across all maintenance fee events
    #    (most recent status per patent, derived from event code)
    dist_sql = f"""
    WITH latest_status AS (
      SELECT
        patent_number,
        ARRAY_AGG(
          {DERIVE_STATUS_SQL}
          ORDER BY event_date DESC LIMIT 1
        )[OFFSET(0)] AS derived_status
      FROM `{settings.maintenance_table}`
      WHERE {DERIVE_STATUS_SQL} IS NOT NULL
      GROUP BY patent_number
    )
    SELECT derived_status, COUNT(*) AS cnt
    FROM latest_status
    WHERE derived_status IS NOT NULL
    GROUP BY derived_status
    ORDER BY cnt DESC
    """
    dist_rows = bq_service.run_query(dist_sql)
    distribution = {r["derived_status"]: r["cnt"] for r in dist_rows}

    # 2. Conversion rates by grant year (last 20 years)
    conv_sql = f"""
    WITH patent_statuses AS (
      SELECT
        m.patent_number,
        ARRAY_AGG({DERIVE_STATUS_SQL} ORDER BY m.event_date ASC LIMIT 1)[OFFSET(0)] AS first_status,
        ARRAY_AGG({DERIVE_STATUS_SQL} ORDER BY m.event_date DESC LIMIT 1)[OFFSET(0)] AS last_status
      FROM `{settings.maintenance_table}` m
      WHERE {DERIVE_STATUS_SQL} IS NOT NULL
      GROUP BY m.patent_number
    )
    SELECT
      EXTRACT(YEAR FROM pfw.grant_date) AS grant_year,
      COUNTIF(ps.first_status IN ('SMALL', 'MICRO') AND ps.last_status = 'LARGE') AS small_to_large,
      COUNTIF(ps.first_status IN ('SMALL', 'MICRO')) AS total_small,
      COUNT(*) AS total_patents
    FROM patent_statuses ps
    JOIN `{settings.patent_table}` pfw ON pfw.patent_number = ps.patent_number
    WHERE pfw.grant_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 20 YEAR)
      AND pfw.grant_date IS NOT NULL
      AND ps.first_status IS NOT NULL
      AND ps.last_status IS NOT NULL
    GROUP BY grant_year
    ORDER BY grant_year
    """
    conv_rows = bq_service.run_query(conv_sql)
    by_year = []
    total_conversions = 0
    total_small = 0
    for r in conv_rows:
        by_year.append({
            "year": r["grant_year"],
            "small_to_large": r["small_to_large"],
            "total_small": r["total_small"],
            "total_patents": r["total_patents"],
        })
        total_conversions += r["small_to_large"]
        total_small += r["total_small"]

    return {
        "distribution": distribution,
        "by_year": by_year,
        "total_conversions": total_conversions,
        "total_small_filed": total_small,
        "conversion_rate": round(total_conversions / total_small * 100, 1) if total_small else 0,
    }


@router.get("/{patent_number}")
def get_patent_status(patent_number: str) -> Dict[str, Any]:
    """Get entity status timeline for a single patent.

    Status is derived from event codes at each maintenance fee event.
    """
    normalized = normalize_patent_number(patent_number)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid patent number: {patent_number}",
        )

    params = [bigquery.ScalarQueryParameter("pn", "STRING", normalized)]

    # Patent info (basic metadata — NOT using entity_status from this table)
    info_sql = f"""
    SELECT patent_number, application_number, invention_title,
           filing_date, grant_date,
           first_applicant_name, first_inventor_name
    FROM `{settings.patent_table}`
    WHERE patent_number = @pn
    LIMIT 1
    """
    info_rows = bq_service.run_query(info_sql, params)
    if not info_rows:
        raise HTTPException(status_code=404, detail=f"Patent {normalized} not found")

    info = info_rows[0]

    # Maintenance fee events timeline — derive entity status from event codes
    timeline_sql = f"""
    SELECT
      event_date,
      event_code,
      {DERIVE_STATUS_SQL} AS derived_status
    FROM `{settings.maintenance_table}`
    WHERE patent_number = @pn
    ORDER BY event_date ASC
    """
    timeline_rows = bq_service.run_query(timeline_sql, params)

    timeline = []
    statuses_seen = []
    for r in timeline_rows:
        ed = r["event_date"]
        derived = r["derived_status"]
        timeline.append({
            "event_date": ed.isoformat() if hasattr(ed, "isoformat") else str(ed),
            "event_code": r["event_code"],
            "entity_status": derived,  # derived from event code
        })
        if derived:
            statuses_seen.append((derived, len(timeline) - 1))

    # Detect conversion from first to last derived status
    first_status = statuses_seen[0][0] if statuses_seen else None
    last_status = statuses_seen[-1][0] if statuses_seen else None
    status_changed = (
        first_status is not None
        and last_status is not None
        and first_status != last_status
    )

    # Find conversion date (first event where status differs from first)
    conversion_date = None
    if status_changed:
        for s, idx in statuses_seen:
            if s != first_status:
                conversion_date = timeline[idx]["event_date"]
                break

    return {
        "patent_number": normalized,
        "application_number": info.get("application_number"),
        "invention_title": info.get("invention_title"),
        "applicant_name": info.get("first_applicant_name"),
        "filing_date": _fmt_date(info.get("filing_date")),
        "grant_date": _fmt_date(info.get("grant_date")),
        "filing_entity_status": first_status,
        "current_entity_status": last_status or first_status,
        "status_changed": status_changed,
        "conversion_date": conversion_date,
        "timeline": timeline,
    }


@router.post("/conversions")
def search_conversions(req: ConversionSearchRequest) -> Dict[str, Any]:
    """Find patents that changed entity status between maintenance windows.

    Status derived from event codes (M1=LARGE, M2=SMALL, M3=MICRO).
    """
    params = [
        bigquery.ScalarQueryParameter("from_status", "STRING", req.from_status),
        bigquery.ScalarQueryParameter("to_status", "STRING", req.to_status),
        bigquery.ScalarQueryParameter("year_start", "INT64", req.grant_year_start),
        bigquery.ScalarQueryParameter("year_end", "INT64", req.grant_year_end),
        bigquery.ScalarQueryParameter("limit", "INT64", min(req.limit, 1000)),
    ]

    applicant_filter = ""
    if req.applicant_name:
        expanded = bq_service.expand_name_for_query(req.applicant_name)
        if len(expanded) > 1:
            for i, name in enumerate(expanded):
                params.append(bigquery.ScalarQueryParameter(f"name_{i}", "STRING", name))
            name_in = ", ".join(f"@name_{i}" for i in range(len(expanded)))
            applicant_filter = f"AND pfw.first_applicant_name IN ({name_in})"
        else:
            params.append(bigquery.ScalarQueryParameter("app_name", "STRING", req.applicant_name))
            applicant_filter = "AND LOWER(pfw.first_applicant_name) = LOWER(@app_name)"

    sql = f"""
    WITH patent_statuses AS (
      SELECT
        m.patent_number,
        ARRAY_AGG({DERIVE_STATUS_SQL} ORDER BY m.event_date ASC LIMIT 1)[OFFSET(0)] AS first_status,
        ARRAY_AGG({DERIVE_STATUS_SQL} ORDER BY m.event_date DESC LIMIT 1)[OFFSET(0)] AS last_status
      FROM `{settings.maintenance_table}` m
      WHERE {DERIVE_STATUS_SQL} IS NOT NULL
      GROUP BY m.patent_number
    )
    SELECT
      ps.patent_number,
      ps.first_status,
      ps.last_status,
      pfw.invention_title,
      pfw.first_applicant_name AS applicant_name,
      pfw.grant_date,
      pfw.application_number
    FROM patent_statuses ps
    JOIN `{settings.patent_table}` pfw ON pfw.patent_number = ps.patent_number
    WHERE ps.first_status = @from_status
      AND ps.last_status = @to_status
      AND ps.first_status != ps.last_status
      AND EXTRACT(YEAR FROM pfw.grant_date) BETWEEN @year_start AND @year_end
      {applicant_filter}
    ORDER BY pfw.grant_date DESC
    LIMIT @limit
    """
    rows = bq_service.run_query(sql, params)

    results = []
    for r in rows:
        results.append({
            "patent_number": r["patent_number"],
            "application_number": r.get("application_number"),
            "invention_title": r.get("invention_title"),
            "applicant_name": r.get("applicant_name"),
            "grant_date": _fmt_date(r.get("grant_date")),
            "first_status": r["first_status"],
            "last_status": r["last_status"],
        })

    return {"total": len(results), "results": results}


@router.post("/by-applicant")
def get_applicant_portfolio(req: ApplicantRequest) -> Dict[str, Any]:
    """Entity status breakdown for all patents of a given applicant.

    Status derived from maintenance fee event codes only.
    """
    expanded = bq_service.expand_name_for_query(req.applicant_name)

    params = []
    if len(expanded) > 1:
        for i, name in enumerate(expanded):
            params.append(bigquery.ScalarQueryParameter(f"name_{i}", "STRING", name))
        name_in = ", ".join(f"@name_{i}" for i in range(len(expanded)))
        name_filter = f"pfw.first_applicant_name IN ({name_in})"
    else:
        params.append(bigquery.ScalarQueryParameter("app_name", "STRING", req.applicant_name))
        name_filter = "LOWER(pfw.first_applicant_name) = LOWER(@app_name)"

    params.append(bigquery.ScalarQueryParameter("limit", "INT64", min(req.limit, 2000)))

    sql = f"""
    WITH applicant_patents AS (
      SELECT patent_number, application_number, filing_date, grant_date,
             invention_title, first_applicant_name
      FROM `{settings.patent_table}` pfw
      WHERE {name_filter}
        AND patent_number IS NOT NULL
    ),
    maint_statuses AS (
      SELECT m.patent_number,
        ARRAY_AGG({DERIVE_STATUS_SQL} ORDER BY m.event_date ASC LIMIT 1)[OFFSET(0)] AS first_maint_status,
        ARRAY_AGG({DERIVE_STATUS_SQL} ORDER BY m.event_date DESC LIMIT 1)[OFFSET(0)] AS latest_maint_status
      FROM `{settings.maintenance_table}` m
      WHERE m.patent_number IN (SELECT patent_number FROM applicant_patents)
        AND {DERIVE_STATUS_SQL} IS NOT NULL
      GROUP BY m.patent_number
    )
    SELECT
      ap.patent_number, ap.application_number, ap.invention_title,
      ap.filing_date, ap.grant_date,
      ms.first_maint_status, ms.latest_maint_status
    FROM applicant_patents ap
    LEFT JOIN maint_statuses ms ON ms.patent_number = ap.patent_number
    ORDER BY ap.grant_date DESC
    LIMIT @limit
    """
    rows = bq_service.run_query(sql, params)

    results = []
    total = 0
    small_count = 0
    converted_count = 0
    for r in rows:
        total += 1
        first = r.get("first_maint_status")
        current = r.get("latest_maint_status") or first
        changed = (
            first is not None
            and r.get("latest_maint_status") is not None
            and first != r["latest_maint_status"]
        )
        if first and first in ("SMALL", "MICRO"):
            small_count += 1
        if changed:
            converted_count += 1

        results.append({
            "patent_number": r["patent_number"],
            "application_number": r.get("application_number"),
            "invention_title": r.get("invention_title"),
            "filing_date": _fmt_date(r.get("filing_date")),
            "grant_date": _fmt_date(r.get("grant_date")),
            "filing_status": first,
            "current_status": current,
            "status_changed": changed,
        })

    return {
        "applicant_name": req.applicant_name,
        "expanded_names": expanded,
        "total_patents": total,
        "small_filed": small_count,
        "converted": converted_count,
        "results": results,
    }


# ── Helpers ───────────────────────────────────────────────────────

def _fmt_date(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)
