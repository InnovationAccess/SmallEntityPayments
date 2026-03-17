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
from datetime import date as _date
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
    limit: int = 50000


class BulkTimelineRequest(BaseModel):
    patent_numbers: List[str]


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
    expanded = []
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

    return {"total": len(results), "results": results, "expanded_names": expanded}


@router.post("/bulk-timelines")
def get_bulk_timelines(req: BulkTimelineRequest) -> Dict[str, Any]:
    """Fetch event timelines for multiple patents (max 200).

    Returns event_date + event_code per patent, grouped by patent_number.
    Merges prosecution declarations (from pfw_transactions) + grant date
    + post-grant events (from maintenance_fee_events_v2) into a single
    chronologically-sorted timeline per patent.
    """
    if not req.patent_numbers:
        return {"timelines": {}, "date_range": None}

    pn_list = req.patent_numbers[:200]
    params = [bigquery.ArrayQueryParameter("pn_list", "STRING", pn_list)]

    # ── 1. Post-grant maintenance events ─────────────────────────
    maint_sql = f"""
    SELECT patent_number, event_date, event_code
    FROM `{settings.maintenance_table}`
    WHERE patent_number IN UNNEST(@pn_list)
    ORDER BY patent_number, event_date ASC
    """
    maint_rows = bq_service.run_query(maint_sql, params)

    # ── 2. Patent lookup: application_number + grant_date ────────
    pfw_sql = f"""
    SELECT patent_number, application_number, grant_date
    FROM `{settings.patent_table}`
    WHERE patent_number IN UNNEST(@pn_list)
    """
    pfw_rows = bq_service.run_query(pfw_sql, params)

    # Map patent_number → application_number and grant_date
    pn_to_app: Dict[str, str] = {}
    pn_to_grant: Dict[str, Any] = {}
    for r in pfw_rows:
        pn = r["patent_number"]
        if r.get("application_number"):
            pn_to_app[pn] = r["application_number"]
        if r.get("grant_date"):
            pn_to_grant[pn] = r["grant_date"]

    # ── 3. Prosecution declarations (SMAL, BIG., MICR from pfw_transactions)
    app_nums = list(set(pn_to_app.values()))
    pros_events: Dict[str, list] = {}  # app_num → list of (date, code)
    if app_nums:
        pros_params = [
            bigquery.ArrayQueryParameter("an_list", "STRING", app_nums),
        ]
        pros_sql = f"""
        SELECT application_number, event_date, event_code
        FROM `{settings.pfw_transactions_table}`
        WHERE application_number IN UNNEST(@an_list)
          AND event_code IN ('SMAL', 'BIG.', 'MICR')
        ORDER BY application_number, event_date ASC
        """
        pros_rows = bq_service.run_query(pros_sql, pros_params)
        for r in pros_rows:
            an = r["application_number"]
            if an not in pros_events:
                pros_events[an] = []
            pros_events[an].append((r["event_date"], r["event_code"]))

    # ── 4. Build unified timelines ───────────────────────────────
    timelines: Dict[str, list] = {}
    global_min = None
    global_max = None

    def _update_range(ed):
        nonlocal global_min, global_max
        if ed:
            if global_min is None or ed < global_min:
                global_min = ed
            if global_max is None or ed > global_max:
                global_max = ed

    # 4a. Add prosecution declarations (keyed by app_num → patent_num)
    app_to_pn: Dict[str, list] = {}
    for pn, an in pn_to_app.items():
        app_to_pn.setdefault(an, []).append(pn)

    for an, evts in pros_events.items():
        for pn in app_to_pn.get(an, []):
            if pn not in timelines:
                timelines[pn] = []
            for (ed, ec) in evts:
                date_str = ed.isoformat() if hasattr(ed, "isoformat") else str(ed)
                timelines[pn].append({"d": date_str, "c": ec})
                _update_range(ed)

    # 4b. Add grant date as synthetic GRNT event
    for pn, gd in pn_to_grant.items():
        if pn not in timelines:
            timelines[pn] = []
        date_str = gd.isoformat() if hasattr(gd, "isoformat") else str(gd)
        timelines[pn].append({"d": date_str, "c": "GRNT"})
        _update_range(gd)

    # 4c. Add post-grant maintenance events
    for r in maint_rows:
        pn = r["patent_number"]
        ed = r["event_date"]
        ec = r["event_code"]
        if pn not in timelines:
            timelines[pn] = []
        date_str = ed.isoformat() if hasattr(ed, "isoformat") else str(ed)
        timelines[pn].append({"d": date_str, "c": ec})
        _update_range(ed)

    # 4d. Sort each timeline chronologically
    for pn in timelines:
        timelines[pn].sort(key=lambda e: e["d"])

    return {
        "timelines": timelines,
        "date_range": {
            "min": _fmt_date(global_min),
            "max": _fmt_date(global_max),
        } if global_min and global_max else None,
    }


@router.post("/by-applicant")
def get_applicant_portfolio(req: ApplicantRequest) -> Dict[str, Any]:
    """Full portfolio analysis for an entity — prosecution AND post-grant.

    Searches ALL applicants, inventors, and assignees (not just first_applicant_name).
    Provides entity status from two phases:
      - Prosecution: SMAL/BIG./MICR codes from pfw_transactions
      - Post-grant: M-code payments, declaration events, and transition codes
        from maintenance_fee_events_v2
    """
    expanded = bq_service.expand_name_for_query(req.applicant_name)

    params: List = []
    if len(expanded) > 1:
        for i, name in enumerate(expanded):
            params.append(bigquery.ScalarQueryParameter(f"name_{i}", "STRING", name))
        name_in = ", ".join(f"@name_{i}" for i in range(len(expanded)))
    else:
        params.append(bigquery.ScalarQueryParameter("name_0", "STRING", req.applicant_name))
        name_in = "@name_0"

    # ── Query 1: Portfolio assembly ────────────────────────────────
    # Find ALL application_numbers where entity appears as applicant,
    # inventor, or assignee.  No LIMIT here — aggregate dashboard stats
    # must reflect the complete portfolio.  The detailed results list
    # is trimmed to req.limit at the end.
    portfolio_sql = f"""
    WITH portfolio_apps AS (
      SELECT DISTINCT application_number
      FROM (
        SELECT application_number FROM `{settings.pfw_applicants_table}`
        WHERE applicant_name IN ({name_in})
        UNION DISTINCT
        SELECT application_number FROM `{settings.pfw_inventors_table}`
        WHERE inventor_name IN ({name_in})
        UNION DISTINCT
        SELECT d.application_number
        FROM `{settings.assign_documents_table}` d
        JOIN `{settings.assign_assignees_table}` a ON a.reel_frame = d.reel_frame
        JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
        WHERE a.assignee_name IN ({name_in})
          AND d.application_number IS NOT NULL
          AND r.normalized_type IN ('divestiture', 'merger', 'court_order', 'name_change')
      )
    )
    SELECT
      p.application_number, p.patent_number, p.filing_date, p.grant_date,
      p.invention_title, p.first_applicant_name
    FROM `{settings.patent_table}` p
    WHERE p.application_number IN (SELECT application_number FROM portfolio_apps)
    ORDER BY p.grant_date DESC NULLS LAST
    """
    portfolio_rows = bq_service.run_query(portfolio_sql, params)

    if not portfolio_rows:
        return {
            "applicant_name": req.applicant_name,
            "expanded_names": expanded,
            "total_patents": 0,
            "total_applications": 0,
            "divested_count": 0,
            "acquired_count": 0,
            "sold_count": 0,
            "portfolio": {
                "granted": {"filed": 0, "acquired": 0, "divested": 0, "expired": 0, "owned": 0},
                "pending": {"filed": 0, "acquired": 0, "divested": 0, "owned": 0},
            },
            "prosecution": {"small": 0, "large": 0, "micro": 0, "total": 0,
                           "small_10y": 0, "large_10y": 0, "micro_10y": 0, "total_10y": 0},
            "post_grant": {
                "small": 0, "large": 0, "micro": 0, "total": 0,
                "stol": 0, "ltos": 0, "stom": 0, "mtos": 0,
                "declarations": 0, "converted": 0,
                "payments": {
                    "m1551": 0, "m1552": 0, "m1553": 0,
                    "m2551": 0, "m2552": 0, "m2553": 0,
                    "m3551": 0, "m3552": 0, "m3553": 0,
                },
                "decl_smal": 0, "decl_big": 0, "decl_micr": 0,
            },
            "results": [],
        }

    # Collect IDs for subsequent queries
    app_nums = []
    pat_nums = []
    for r in portfolio_rows:
        app_nums.append(r["application_number"])
        if r.get("patent_number"):
            pat_nums.append(r["patent_number"])

    # ── Query 2: Post-grant events (maintenance_fee_events_v2) ─────
    # Payment codes derive entity status; also count declarations and transitions.
    # Filtered by ownership window: only events during the entity's ownership
    # period are counted (after acquisition, before divestiture).
    pg_params = list(params)  # include name params for ownership CTE
    pg_params.append(bigquery.ArrayQueryParameter("pn_list", "STRING", pat_nums))
    pg_params.append(bigquery.ArrayQueryParameter("an_list", "STRING", app_nums))
    postgrant_sql = f"""
    WITH applicant_inventor AS (
      SELECT DISTINCT application_number
      FROM (
        SELECT application_number FROM `{settings.pfw_applicants_table}`
        WHERE applicant_name IN ({name_in})
        UNION DISTINCT
        SELECT application_number FROM `{settings.pfw_inventors_table}`
        WHERE inventor_name IN ({name_in})
      )
      WHERE application_number IN UNNEST(@an_list)
    ),
    acquired_via_assign AS (
      SELECT d.application_number, MIN(r.recorded_date) AS acquired_date
      FROM `{settings.assign_documents_table}` d
      JOIN `{settings.assign_assignees_table}` a ON a.reel_frame = d.reel_frame
      JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
      WHERE d.application_number IN UNNEST(@an_list)
        AND a.assignee_name IN ({name_in})
        AND r.normalized_type IN ('divestiture', 'merger', 'court_order', 'name_change')
        AND d.application_number NOT IN (SELECT application_number FROM applicant_inventor)
      GROUP BY d.application_number
    ),
    divested AS (
      SELECT d.application_number, MIN(r.recorded_date) AS divested_date
      FROM `{settings.assign_documents_table}` d
      JOIN `{settings.assign_assignors_table}` a ON a.reel_frame = d.reel_frame
      JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
      WHERE d.application_number IN UNNEST(@an_list)
        AND a.assignor_name IN ({name_in})
        AND r.normalized_type IN ('divestiture', 'merger', 'court_order')
      GROUP BY d.application_number
    ),
    ownership AS (
      SELECT pfw.patent_number, aa.acquired_date, dv.divested_date
      FROM `{settings.patent_table}` pfw
      LEFT JOIN acquired_via_assign aa ON aa.application_number = pfw.application_number
      LEFT JOIN divested dv ON dv.application_number = pfw.application_number
      WHERE pfw.patent_number IN UNNEST(@pn_list)
    ),
    base AS (
      SELECT
        m.patent_number,
        m.event_code,
        m.event_date,
        {DERIVE_STATUS_SQL} AS derived_status,
        CASE WHEN m.event_date >= COALESCE(ow.acquired_date, DATE '0001-01-01')
              AND m.event_date <  COALESCE(ow.divested_date, DATE '9999-12-31')
             THEN TRUE ELSE FALSE END AS during_ownership
      FROM `{settings.maintenance_table}` m
      LEFT JOIN ownership ow ON ow.patent_number = m.patent_number
      WHERE m.patent_number IN UNNEST(@pn_list)
        AND (
          {DERIVE_STATUS_SQL} IS NOT NULL
          OR m.event_code IN ('BIG.', 'SMAL', 'MICR', 'STOL', 'LTOS', 'STOM', 'MTOS')
        )
    ),
    first_status AS (
      SELECT
        patent_number,
        ARRAY_AGG(derived_status IGNORE NULLS ORDER BY event_date ASC LIMIT 1)[SAFE_OFFSET(0)] AS first_status
      FROM base
      WHERE during_ownership
      GROUP BY patent_number
    )
    SELECT
      b.patent_number,
      f.first_status AS first_maint_status,
      ARRAY_AGG(CASE WHEN b.during_ownership THEN b.derived_status END
        IGNORE NULLS ORDER BY b.event_date DESC LIMIT 1)[SAFE_OFFSET(0)] AS latest_maint_status,
      COUNTIF(b.event_code = 'BIG.'  AND b.during_ownership) AS decl_big,
      COUNTIF(b.event_code = 'SMAL'  AND b.during_ownership) AS decl_smal,
      COUNTIF(b.event_code = 'MICR'  AND b.during_ownership) AS decl_micr,
      COUNTIF(b.event_code = 'STOL'  AND b.during_ownership) AS trans_stol,
      COUNTIF(b.event_code = 'LTOS'  AND b.during_ownership) AS trans_ltos,
      COUNTIF(b.event_code = 'STOM'  AND b.during_ownership) AS trans_stom,
      COUNTIF(b.event_code = 'MTOS'  AND b.during_ownership) AS trans_mtos,
      COUNTIF(b.event_code = 'M1551' AND b.during_ownership) AS pay_m1551,
      COUNTIF(b.event_code = 'M1552' AND b.during_ownership) AS pay_m1552,
      COUNTIF(b.event_code = 'M1553' AND b.during_ownership) AS pay_m1553,
      COUNTIF(b.event_code = 'M2551' AND b.during_ownership) AS pay_m2551,
      COUNTIF(b.event_code = 'M2552' AND b.during_ownership) AS pay_m2552,
      COUNTIF(b.event_code = 'M2553' AND b.during_ownership) AS pay_m2553,
      COUNTIF(b.event_code = 'M3551' AND b.during_ownership) AS pay_m3551,
      COUNTIF(b.event_code = 'M3552' AND b.during_ownership) AS pay_m3552,
      COUNTIF(b.event_code = 'M3553' AND b.during_ownership) AS pay_m3553,
      MIN(CASE
        WHEN b.during_ownership AND b.derived_status IS NOT NULL
        AND b.derived_status != f.first_status
        THEN b.event_date
      END) AS change_date
    FROM base b
    LEFT JOIN first_status f ON f.patent_number = b.patent_number
    GROUP BY b.patent_number, f.first_status
    """ if pat_nums else None

    pg_by_patent = {}
    if postgrant_sql:
        pg_rows = bq_service.run_query(postgrant_sql, pg_params)
        for r in pg_rows:
            pg_by_patent[r["patent_number"]] = r

    # ── Query 3: Prosecution declarations (pfw_transactions) ───────
    # SMAL, BIG., MICR codes during prosecution phase.
    # Filtered by ownership window: only events during entity's ownership.
    pros_params = list(params)  # include name params for ownership CTE
    pros_params.append(bigquery.ArrayQueryParameter("an_list", "STRING", app_nums))
    prosecution_sql = f"""
    WITH applicant_inventor AS (
      SELECT DISTINCT application_number
      FROM (
        SELECT application_number FROM `{settings.pfw_applicants_table}`
        WHERE applicant_name IN ({name_in})
        UNION DISTINCT
        SELECT application_number FROM `{settings.pfw_inventors_table}`
        WHERE inventor_name IN ({name_in})
      )
      WHERE application_number IN UNNEST(@an_list)
    ),
    acquired_via_assign AS (
      SELECT d.application_number, MIN(r.recorded_date) AS acquired_date
      FROM `{settings.assign_documents_table}` d
      JOIN `{settings.assign_assignees_table}` a ON a.reel_frame = d.reel_frame
      JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
      WHERE d.application_number IN UNNEST(@an_list)
        AND a.assignee_name IN ({name_in})
        AND r.normalized_type IN ('divestiture', 'merger', 'court_order', 'name_change')
        AND d.application_number NOT IN (SELECT application_number FROM applicant_inventor)
      GROUP BY d.application_number
    ),
    divested AS (
      SELECT d.application_number, MIN(r.recorded_date) AS divested_date
      FROM `{settings.assign_documents_table}` d
      JOIN `{settings.assign_assignors_table}` a ON a.reel_frame = d.reel_frame
      JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
      WHERE d.application_number IN UNNEST(@an_list)
        AND a.assignor_name IN ({name_in})
        AND r.normalized_type IN ('divestiture', 'merger', 'court_order')
      GROUP BY d.application_number
    ),
    owned_events AS (
      SELECT
        t.application_number,
        t.event_code,
        t.event_date,
        CASE WHEN t.event_date >= COALESCE(aa.acquired_date, DATE '0001-01-01')
              AND t.event_date <  COALESCE(dv.divested_date, DATE '9999-12-31')
             THEN TRUE ELSE FALSE END AS during_ownership
      FROM `{settings.pfw_transactions_table}` t
      LEFT JOIN acquired_via_assign aa ON aa.application_number = t.application_number
      LEFT JOIN divested dv ON dv.application_number = t.application_number
      WHERE t.application_number IN UNNEST(@an_list)
        AND t.event_code IN ('SMAL', 'BIG.', 'MICR')
    )
    SELECT
      application_number,
      ARRAY_AGG(CASE WHEN during_ownership THEN event_code END
        IGNORE NULLS ORDER BY event_date ASC LIMIT 1)[SAFE_OFFSET(0)]
        AS first_pros_status,
      ARRAY_AGG(CASE WHEN during_ownership THEN event_code END
        IGNORE NULLS ORDER BY event_date DESC LIMIT 1)[SAFE_OFFSET(0)]
        AS latest_pros_status,
      COUNTIF(event_code = 'SMAL' AND during_ownership) AS pros_smal,
      COUNTIF(event_code = 'BIG.' AND during_ownership) AS pros_big,
      COUNTIF(event_code = 'MICR' AND during_ownership) AS pros_micr,
      ARRAY_AGG(
        CASE WHEN during_ownership
              AND event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 10 YEAR)
             THEN event_code END
        IGNORE NULLS ORDER BY event_date DESC LIMIT 1
      )[SAFE_OFFSET(0)] AS latest_pros_status_10y
    FROM owned_events
    GROUP BY application_number
    """
    pros_by_app = {}
    pros_rows = bq_service.run_query(prosecution_sql, pros_params)
    for r in pros_rows:
        pros_by_app[r["application_number"]] = r

    # ── Query 4: Ownership window per application ───────────────────
    # Determines when the entity acquired and/or divested each patent.
    # - acquired_date: non-NULL only for patents acquired via assignment
    #   (not originally filed by the entity). NULL = applicant/inventor.
    # - divested_date: non-NULL only for patents divested away via
    #   divestiture, merger, or court_order.
    ow_params = list(params)  # reuse name params
    ow_params.append(bigquery.ArrayQueryParameter("an_list", "STRING", app_nums))
    ownership_sql = f"""
    WITH applicant_inventor AS (
      SELECT DISTINCT application_number
      FROM (
        SELECT application_number FROM `{settings.pfw_applicants_table}`
        WHERE applicant_name IN ({name_in})
        UNION DISTINCT
        SELECT application_number FROM `{settings.pfw_inventors_table}`
        WHERE inventor_name IN ({name_in})
      )
      WHERE application_number IN UNNEST(@an_list)
    ),
    acquired_via_assign AS (
      SELECT d.application_number, MIN(r.recorded_date) AS acquired_date
      FROM `{settings.assign_documents_table}` d
      JOIN `{settings.assign_assignees_table}` a ON a.reel_frame = d.reel_frame
      JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
      WHERE d.application_number IN UNNEST(@an_list)
        AND a.assignee_name IN ({name_in})
        AND r.normalized_type IN ('divestiture', 'merger', 'court_order', 'name_change')
        AND d.application_number NOT IN (SELECT application_number FROM applicant_inventor)
      GROUP BY d.application_number
    ),
    divested AS (
      SELECT d.application_number, MIN(r.recorded_date) AS divested_date
      FROM `{settings.assign_documents_table}` d
      JOIN `{settings.assign_assignors_table}` a ON a.reel_frame = d.reel_frame
      JOIN `{settings.assign_records_table}` r ON r.reel_frame = d.reel_frame
      WHERE d.application_number IN UNNEST(@an_list)
        AND a.assignor_name IN ({name_in})
        AND r.normalized_type IN ('divestiture', 'merger', 'court_order')
      GROUP BY d.application_number
    )
    SELECT
      aa.application_number, aa.acquired_date, CAST(NULL AS DATE) AS divested_date
    FROM acquired_via_assign aa
    WHERE aa.application_number NOT IN (SELECT application_number FROM divested)
    UNION ALL
    SELECT
      dv.application_number, aa.acquired_date, dv.divested_date
    FROM divested dv
    LEFT JOIN acquired_via_assign aa ON aa.application_number = dv.application_number
    """
    ow_rows = bq_service.run_query(ownership_sql, ow_params)
    # ownership_map: app_num → (acquired_date_or_None, divested_date_or_None)
    ownership_map: Dict[str, tuple] = {}
    for r in ow_rows:
        ownership_map[r["application_number"]] = (
            r.get("acquired_date"),
            r.get("divested_date"),
        )
    divested_count = sum(1 for _, (_, dv) in ownership_map.items() if dv is not None)
    acquired_count = sum(1 for _, (ac, _) in ownership_map.items() if ac is not None)

    # Event code → pg_by_patent field name (for building per-patent mf_events)
    _MF_CODE_MAP = [
        ('M1551', 'pay_m1551'), ('M1552', 'pay_m1552'), ('M1553', 'pay_m1553'),
        ('M2551', 'pay_m2551'), ('M2552', 'pay_m2552'), ('M2553', 'pay_m2553'),
        ('M3551', 'pay_m3551'), ('M3552', 'pay_m3552'), ('M3553', 'pay_m3553'),
        ('BIG.', 'decl_big'), ('SMAL', 'decl_smal'), ('MICR', 'decl_micr'),
        ('STOL', 'trans_stol'), ('LTOS', 'trans_ltos'),
        ('STOM', 'trans_stom'), ('MTOS', 'trans_mtos'),
    ]

    # ── Merge results ──────────────────────────────────────────────
    PROS_CODE_MAP = {"SMAL": "SMALL", "BIG.": "LARGE", "MICR": "MICRO"}

    results = []
    total_patents = 0
    total_applications = len(portfolio_rows)
    display_limit = min(req.limit, 50000)
    # KPI split counters (granted vs pending)
    filed_granted = 0; acquired_granted = 0; divested_granted = 0; expired_granted = 0
    filed_pending = 0; acquired_pending = 0; divested_pending = 0
    # Dashboard accumulators — prosecution
    pros_small = 0
    pros_large = 0
    pros_micro = 0
    pros_total = 0
    # Prosecution — past 10 years only
    pros_small_10y = 0
    pros_large_10y = 0
    pros_micro_10y = 0
    pros_total_10y = 0
    # Dashboard accumulators — post-grant
    pg_small = 0
    pg_large = 0
    pg_micro = 0
    pg_total = 0
    pg_stol = 0
    pg_ltos = 0
    pg_stom = 0
    pg_mtos = 0
    pg_declarations = 0
    pg_converted = 0
    # Individual payment code accumulators
    pg_m1551 = 0; pg_m1552 = 0; pg_m1553 = 0
    pg_m2551 = 0; pg_m2552 = 0; pg_m2553 = 0
    pg_m3551 = 0; pg_m3552 = 0; pg_m3553 = 0
    # Declaration accumulators
    pg_decl_smal = 0; pg_decl_big = 0; pg_decl_micr = 0

    for r in portfolio_rows:
        app_num = r["application_number"]
        pat_num = r.get("patent_number")
        if pat_num:
            total_patents += 1

        # Prosecution data
        pros = pros_by_app.get(app_num, {})
        pros_status_raw = pros.get("latest_pros_status")
        pros_status = PROS_CODE_MAP.get(pros_status_raw)
        first_pros_raw = pros.get("first_pros_status")
        first_pros = PROS_CODE_MAP.get(first_pros_raw)
        if pros_status:
            pros_total += 1
            if pros_status == "SMALL":
                pros_small += 1
            elif pros_status == "LARGE":
                pros_large += 1
            elif pros_status == "MICRO":
                pros_micro += 1

        # Prosecution — past 10 years
        pros_status_10y_raw = pros.get("latest_pros_status_10y")
        pros_status_10y = PROS_CODE_MAP.get(pros_status_10y_raw)
        if pros_status_10y:
            pros_total_10y += 1
            if pros_status_10y == "SMALL":
                pros_small_10y += 1
            elif pros_status_10y == "LARGE":
                pros_large_10y += 1
            elif pros_status_10y == "MICRO":
                pros_micro_10y += 1

        # Post-grant data
        pg = pg_by_patent.get(pat_num, {}) if pat_num else {}
        pg_first = pg.get("first_maint_status")
        pg_latest = pg.get("latest_maint_status")
        pg_change = pg.get("change_date")
        if pg_first:
            pg_total += 1
            if pg_first == "SMALL":
                pg_small += 1
            elif pg_first == "LARGE":
                pg_large += 1
            elif pg_first == "MICRO":
                pg_micro += 1
        # Count PATENTS with transitions (not total events)
        pg_stol += 1 if pg.get("trans_stol", 0) > 0 else 0
        pg_ltos += 1 if pg.get("trans_ltos", 0) > 0 else 0
        pg_stom += 1 if pg.get("trans_stom", 0) > 0 else 0
        pg_mtos += 1 if pg.get("trans_mtos", 0) > 0 else 0
        pg_declarations += (
            pg.get("decl_big", 0) + pg.get("decl_smal", 0) + pg.get("decl_micr", 0)
        )
        # Individual payment codes
        pg_m1551 += pg.get("pay_m1551", 0)
        pg_m1552 += pg.get("pay_m1552", 0)
        pg_m1553 += pg.get("pay_m1553", 0)
        pg_m2551 += pg.get("pay_m2551", 0)
        pg_m2552 += pg.get("pay_m2552", 0)
        pg_m2553 += pg.get("pay_m2553", 0)
        pg_m3551 += pg.get("pay_m3551", 0)
        pg_m3552 += pg.get("pay_m3552", 0)
        pg_m3553 += pg.get("pay_m3553", 0)
        # Declaration codes
        pg_decl_smal += pg.get("decl_smal", 0)
        pg_decl_big += pg.get("decl_big", 0)
        pg_decl_micr += pg.get("decl_micr", 0)
        if pg_first and pg_latest and pg_first != pg_latest:
            pg_converted += 1

        # Determine overall change
        changed = False
        change_phase = None
        if pg_first and pg_latest and pg_first != pg_latest:
            changed = True
            change_phase = "post_grant"
        elif first_pros and pros_status and first_pros != pros_status:
            changed = True
            change_phase = "prosecution"

        # Build mf_events: space-separated event codes present for this patent
        mf_codes = [code for code, field in _MF_CODE_MAP if pg.get(field, 0) > 0]
        mf_events = ' '.join(mf_codes)

        # Ownership window info for this patent
        ow = ownership_map.get(app_num, (None, None))
        acq_date, div_date = ow

        # Determine expired status for granted patents
        # A patent expires if maintenance fees were missed or term ended (20 yrs)
        expired = False
        grant_dt = r.get("grant_date")
        if pat_num and grant_dt and isinstance(grant_dt, _date):
            age_days = (_date.today() - grant_dt).days
            mf_set = set(mf_codes)
            has_551 = any(c.endswith("551") for c in mf_set)
            has_552 = any(c.endswith("552") for c in mf_set)
            has_553 = any(c.endswith("553") for c in mf_set)
            if age_days >= 7305:         # 20 years — natural expiration
                expired = True
            elif age_days >= 4383 and not has_553:  # 12 years, no 11.5-yr fee
                expired = True
            elif age_days >= 2922 and not has_552:  # 8 years, no 7.5-yr fee
                expired = True
            elif age_days >= 1461 and not has_551:  # 4 years, no 3.5-yr fee
                expired = True

        # KPI split counts
        is_acquired = acq_date is not None
        is_divested = div_date is not None
        if pat_num:
            if is_acquired:
                acquired_granted += 1
            else:
                filed_granted += 1
            if is_divested:
                divested_granted += 1
            elif expired:
                expired_granted += 1
        else:
            if is_acquired:
                acquired_pending += 1
            else:
                filed_pending += 1
            if is_divested:
                divested_pending += 1

        # Only add to detailed results up to display_limit
        if len(results) < display_limit:
            results.append({
                "patent_number": pat_num,
                "application_number": app_num,
                "invention_title": r.get("invention_title"),
                "filing_date": _fmt_date(r.get("filing_date")),
                "grant_date": _fmt_date(r.get("grant_date")),
                "prosecution_status": pros_status,
                "prosecution_status_10y": pros_status_10y,
                "post_grant_first": pg_first,
                "post_grant_current": pg_latest or pg_first,
                "status_changed": changed,
                "change_date": _fmt_date(pg_change),
                "change_phase": change_phase,
                "mf_events": mf_events,
                "acquired_via_assignment": is_acquired,
                "acquired_date": _fmt_date(acq_date),
                "divested": is_divested,
                "divested_date": _fmt_date(div_date),
                "expired": expired,
            })

    # Computed KPIs
    owned_granted = filed_granted + acquired_granted - divested_granted - expired_granted
    owned_pending = filed_pending + acquired_pending - divested_pending

    return {
        "applicant_name": req.applicant_name,
        "expanded_names": expanded,
        "total_patents": total_patents,
        "total_applications": total_applications,
        "divested_count": divested_count,
        "acquired_count": acquired_count,
        "sold_count": divested_count,  # backward compatibility alias
        "portfolio": {
            "granted": {
                "filed": filed_granted,
                "acquired": acquired_granted,
                "divested": divested_granted,
                "expired": expired_granted,
                "owned": owned_granted,
            },
            "pending": {
                "filed": filed_pending,
                "acquired": acquired_pending,
                "divested": divested_pending,
                "owned": owned_pending,
            },
        },
        "prosecution": {
            "small": pros_small,
            "large": pros_large,
            "micro": pros_micro,
            "total": pros_total,
            "small_10y": pros_small_10y,
            "large_10y": pros_large_10y,
            "micro_10y": pros_micro_10y,
            "total_10y": pros_total_10y,
        },
        "post_grant": {
            "small": pg_small,
            "large": pg_large,
            "micro": pg_micro,
            "total": pg_total,
            "stol": pg_stol,
            "ltos": pg_ltos,
            "stom": pg_stom,
            "mtos": pg_mtos,
            "declarations": pg_declarations,
            "converted": pg_converted,
            "payments": {
                "m1551": pg_m1551, "m1552": pg_m1552, "m1553": pg_m1553,
                "m2551": pg_m2551, "m2552": pg_m2552, "m2553": pg_m2553,
                "m3551": pg_m3551, "m3552": pg_m3552, "m3553": pg_m3553,
            },
            "decl_smal": pg_decl_smal,
            "decl_big": pg_decl_big,
            "decl_micr": pg_decl_micr,
        },
        "results": results,
    }


# ── Helpers ───────────────────────────────────────────────────────

def _fmt_date(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)
