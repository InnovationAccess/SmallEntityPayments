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

import json
import logging
import sys
from datetime import date as _date, datetime as _datetime
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
log = logging.getLogger(__name__)

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


class ProsecutionTimelinesRequest(BaseModel):
    application_numbers: List[str]


class EntityProsecutionRequest(BaseModel):
    applicant_name: str
    application_numbers: List[str]


class ExtractionDataRequest(BaseModel):
    application_numbers: List[str]


class ExtractionProgressRequest(BaseModel):
    representative_name: str
    application_numbers: List[str]


class InvoiceKpisRequest(BaseModel):
    applicant_name: str
    application_numbers: List[str]


class QueueExtractionRequest(BaseModel):
    application_numbers: List[str]
    representative_name: str = ""  # informational only (queued_by)


# ── Invoice fee-code → entity status helpers ──────────────────────

_AIA_DATE = _date(2013, 3, 19)
_UAIA_DATE = _date(2022, 12, 29)


def _entity_from_fee_code(fee_code: str | None) -> str:
    """Derive entity status from the first digit of a USPTO fee code.

    1xxx = LARGE, 2xxx = SMALL, 3xxx = MICRO, 4xxx = SMALL (electronic).
    Returns 'LARGE' if fee_code is None or unrecognizable.
    """
    if not fee_code or len(fee_code) < 1:
        return "LARGE"
    d = fee_code[0]
    if d == "1":
        return "LARGE"
    if d in ("2", "4"):
        return "SMALL"
    if d == "3":
        return "MICRO"
    return "LARGE"


def _large_rate_multiplier(entity_status: str, mail_date: _date | None) -> float:
    """Return the multiplier to convert a discounted amount to large-rate equivalent.

    Discount ratios (from utils/fee_schedule.py):
      Pre-AIA (before Mar 19, 2013): Small = 50% of Large (×2.0), Micro didn't exist (=Small)
      AIA to UAIA (Mar 19, 2013 – Dec 28, 2022): Small = 50% (×2.0), Micro = 25% (×4.0)
      Post-UAIA (Dec 29, 2022+): Small = 40% (×2.5), Micro = 20% (×5.0)
    """
    if entity_status == "LARGE":
        return 1.0
    if mail_date is None:
        return 2.0  # safe default
    if mail_date >= _UAIA_DATE:
        return 5.0 if entity_status == "MICRO" else 2.5
    if mail_date >= _AIA_DATE:
        return 4.0 if entity_status == "MICRO" else 2.0
    # Pre-AIA: micro didn't exist, treat as small
    return 2.0


# ── Prosecution Payment Analysis Constants ────────────────────────

# Status-change event codes → new entity status
_PROS_STATUS_CODES = {
    # → SMALL
    'SES': 'SMALL', 'SMAL': 'SMALL', 'P013': 'SMALL', 'MP013': 'SMALL',
    'MSML': 'SMALL', 'NOSE': 'SMALL', 'MRNSME': 'SMALL',
    # → MICRO
    'MICR': 'MICRO', 'MENC': 'MICRO', 'PMRIA': 'MICRO', 'MPMRIA': 'MICRO',
    # → LARGE
    'BIG.': 'LARGE', 'P014': 'LARGE', 'MP014': 'LARGE',
}

# All 102 prosecution payment event codes
_PROS_PAYMENT_CODES = [
    'A.I.', 'A.LA', 'A.NQ', 'A.NR', 'A.PE', 'A371', 'AABR', 'ABN/',
    'ABN6', 'ABN9', 'ABNF', 'ACKNAHA', 'ADDDWRG', 'ADDFLFEE', 'ADDSPEC',
    'AFNE', 'AP.B', 'AP.C', 'AP.C3', 'AP/A', 'APBD', 'APBI', 'APBR',
    'APCA', 'APCD', 'APCP', 'APCR', 'APE2', 'APEA', 'APFC', 'APHT',
    'APND', 'APNH', 'APNH.CA', 'APNH.CO', 'APNH.MI', 'APNH.TX',
    'APNH.VA', 'APOH', 'APRD', 'APRR', 'ARBP', 'BRCE', 'C610', 'C9DE',
    'C9GR', 'CPA-AMD', 'DIST', 'FEE.', 'FLFEE', 'FRCE', 'IDS.',
    'IDSPTA', 'IFEE', 'IFEEHA', 'IRCE', 'J521', 'JA94', 'JA95', 'JS13',
    'MABN6', 'MAPHT', 'MCPA-AMD', 'MODPD28', 'MODPD33', 'MP005',
    'MP020', 'MQRCE', 'MRAPD', 'MRAPS', 'MRXEAS', 'MRXG.', 'MRXTG',
    'MSML', 'N/AP', 'N/AP-NOA', 'N084', 'NOIFIBHA', 'ODPD28', 'ODPD33',
    'ODPET4', 'P003', 'P005', 'P007', 'P010', 'P012', 'P020', 'P131',
    'P138', 'PFP', 'PMFP', 'QRCE', 'RCEX', 'RETF', 'RVIFEEHA',
    'RXIDS.R', 'RXRQ/T', 'RXSAPB', 'RXXT/G', 'TDP', 'VFEE', 'XT/G',
]

# IDS trigger codes — needed to determine if IDS filing requires a fee
# (IDS is only a PAY event if filed after Final Office Action or NOA)
_IDS_TRIGGER_CODES = ['CTFR', 'MS95', 'NOA', 'MAILNOA', 'D.ISS']

# Cache version — bumped when fee calculation logic changes
_CACHE_VERSION = 2


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


@router.get("/queue-stats")
def get_queue_stats() -> Dict[str, Any]:
    """Return global extraction queue and processing stats."""
    client = bigquery.Client(location="us-west1")
    query = """
    SELECT
      (SELECT COUNT(DISTINCT application_number)
       FROM `uspto-data-app.uspto_data.invoice_extraction_queue`) as queue_count,
      (SELECT COUNT(*)
       FROM `uspto-data-app.uspto_data.invoice_extractions`
       WHERE extraction_status = 'downloaded') as pending_ocr,
      (SELECT COUNT(*)
       FROM `uspto-data-app.uspto_data.invoice_extractions`
       WHERE extraction_status = 'extracted') as extracted
    """
    rows = list(client.query(query).result())
    r = rows[0] if rows else None
    return {
        "queue_count": r.queue_count if r else 0,
        "pending_ocr": r.pending_ocr if r else 0,
        "extracted": r.extracted if r else 0,
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


@router.post("/prosecution-timelines")
def get_prosecution_timelines(req: ProsecutionTimelinesRequest) -> Dict[str, Any]:
    """Prosecution payment analysis: status segments + payment events.

    Uses a BigQuery cache table (prosecution_payment_cache) to avoid
    re-analyzing applications that have already been processed.  Only
    uncached applications are queried from pfw_transactions.

    Max 1000 applications per request.
    """
    _dollar_kpi_zeros = {
        "total_paid": 0, "total_large_rate": 0, "total_underpayment": 0,
        "reduced_paid": 0, "reduced_large_rate": 0, "reduced_underpayment": 0,
        "total_paid_10y": 0, "total_large_rate_10y": 0, "total_underpayment_10y": 0,
        "reduced_paid_10y": 0, "reduced_large_rate_10y": 0, "reduced_underpayment_10y": 0,
    }
    empty = {"timelines": {}, "date_range": None,
             "payments_detail": [], "summary": {},
             "kpis": {"small": 0, "micro": 0, "large": 0, "total": 0,
                      "apps_with_findings": 0,
                      "small_10y": 0, "micro_10y": 0, "large_10y": 0,
                      "total_10y": 0, "apps_with_findings_10y": 0,
                      **_dollar_kpi_zeros},
             "cache_stats": {"from_cache": 0, "freshly_analyzed": 0}}
    if not req.application_numbers:
        return empty

    an_list = req.application_numbers[:1000]

    # ── Step 1: Check cache ──────────────────────────────────────
    cache_params = [bigquery.ArrayQueryParameter("an_list", "STRING", an_list)]
    cache_sql = f"""
    SELECT application_number, segments, payments,
           small_count, micro_count, large_count, filing_date
    FROM `{settings.prosecution_payment_cache_table}`
    WHERE application_number IN UNNEST(@an_list)
      AND cache_version = {_CACHE_VERSION}
    """
    cache_rows = bq_service.run_query(cache_sql, cache_params)

    cached: Dict[str, dict] = {}
    for r in cache_rows:
        an = r["application_number"]
        cached[an] = {
            "segments": json.loads(r["segments"]) if r.get("segments") else [],
            "payments": json.loads(r["payments"]) if r.get("payments") else [],
            "small_count": r.get("small_count", 0),
            "micro_count": r.get("micro_count", 0),
            "large_count": r.get("large_count", 0),
            "filing_date": r.get("filing_date"),
        }

    uncached = [an for an in an_list if an not in cached]
    from_cache = len(cached)
    freshly_analyzed = 0

    # ── Step 2: Analyze uncached apps ────────────────────────────
    fresh: Dict[str, dict] = {}
    if uncached:
        fresh = _analyze_prosecution_apps(uncached)
        freshly_analyzed = len(fresh)

        # ── Step 3: Save fresh results to cache ──────────────────
        _save_prosecution_cache(fresh)

    # ── Step 4: Merge cached + fresh, build response ─────────────
    timelines: Dict[str, dict] = {}
    payments_detail = []
    summary: Dict[str, Dict[str, int]] = {}
    kpi_small = 0
    kpi_micro = 0
    kpi_large = 0
    kpi_small_10y = 0
    kpi_micro_10y = 0
    kpi_large_10y = 0
    apps_with_findings: set = set()
    apps_with_findings_10y: set = set()
    # Dollar accumulators
    kpi_total_paid = 0
    kpi_total_large = 0
    kpi_total_delta = 0
    kpi_reduced_paid = 0      # Small+Micro only
    kpi_reduced_large = 0
    kpi_reduced_delta = 0
    kpi_total_paid_10y = 0
    kpi_total_large_10y = 0
    kpi_total_delta_10y = 0
    kpi_reduced_paid_10y = 0
    kpi_reduced_large_10y = 0
    kpi_reduced_delta_10y = 0
    global_min = None
    global_max = None
    ten_yr_cutoff = _fmt_date(_date.today().replace(year=_date.today().year - 10))

    def _update_range_str(d_str):
        nonlocal global_min, global_max
        if not d_str:
            return
        if global_min is None or d_str < global_min:
            global_min = d_str
        if global_max is None or d_str > global_max:
            global_max = d_str

    # Process all apps (from cache or fresh analysis)
    for an in an_list:
        if an in fresh:
            tl = fresh[an]
        elif an in cached:
            tl = cached[an]
        else:
            continue  # app had no data in either source

        timelines[an] = {"segments": tl["segments"], "payments": tl["payments"]}

        # Update date range from segments
        for seg in tl["segments"]:
            _update_range_str(seg.get("start"))
            _update_range_str(seg.get("end"))

        # Count KPIs and build detail/summary from payments
        for pay in tl["payments"]:
            _update_range_str(pay.get("d"))
            pay_status = pay.get("status", "LARGE")

            pay_paid = pay.get("paid", 0)
            pay_large = pay.get("large", 0)
            pay_delta = pay.get("delta", 0)

            if pay_status == "SMALL":
                kpi_small += 1
                apps_with_findings.add(an)
            elif pay_status == "MICRO":
                kpi_micro += 1
                apps_with_findings.add(an)
            else:
                kpi_large += 1

            # Dollar accumulators (all events with fees)
            kpi_total_paid += pay_paid
            kpi_total_large += pay_large
            kpi_total_delta += pay_delta
            if pay_status in ("SMALL", "MICRO"):
                kpi_reduced_paid += pay_paid
                kpi_reduced_large += pay_large
                kpi_reduced_delta += pay_delta

            # 10-year window KPIs
            if pay.get("d") and pay["d"] >= ten_yr_cutoff:
                if pay_status == "SMALL":
                    kpi_small_10y += 1
                    apps_with_findings_10y.add(an)
                elif pay_status == "MICRO":
                    kpi_micro_10y += 1
                    apps_with_findings_10y.add(an)
                else:
                    kpi_large_10y += 1

                kpi_total_paid_10y += pay_paid
                kpi_total_large_10y += pay_large
                kpi_total_delta_10y += pay_delta
                if pay_status in ("SMALL", "MICRO"):
                    kpi_reduced_paid_10y += pay_paid
                    kpi_reduced_large_10y += pay_large
                    kpi_reduced_delta_10y += pay_delta

            # Summary pivot
            yr = pay["d"][:4] if pay.get("d") else "Unknown"
            if yr not in summary:
                summary[yr] = {}
            summary[yr][pay["c"]] = summary[yr].get(pay["c"], 0) + 1

            # Detail record for Small + Micro
            if pay_status in ("SMALL", "MICRO"):
                # Find origin from segment
                origin_code = None
                origin_date = None
                for seg in tl["segments"]:
                    seg_start = seg.get("start")
                    seg_end = seg.get("end")
                    if seg_start and pay["d"] >= seg_start:
                        if seg_end is None or pay["d"] < seg_end:
                            origin_code = seg.get("trigger")
                            origin_date = seg_start
                            break
                payments_detail.append({
                    "application_number": an,
                    "event_date": pay["d"],
                    "event_code": pay["c"],
                    "event_description": pay.get("desc", ""),
                    "claimed_status": pay_status,
                    "fee_category": pay.get("cat"),
                    "amount_paid": pay_paid,
                    "large_rate": pay_large,
                    "underpayment": pay_delta,
                    "origin_code": origin_code,
                    "origin_date": origin_date,
                })

    log.info("Prosecution timelines: %d from cache, %d freshly analyzed",
             from_cache, freshly_analyzed)

    return {
        "timelines": timelines,
        "date_range": {
            "min": global_min,
            "max": global_max,
        } if global_min and global_max else None,
        "payments_detail": payments_detail,
        "summary": summary,
        "kpis": {
            "small": kpi_small,
            "micro": kpi_micro,
            "large": kpi_large,
            "total": kpi_small + kpi_micro + kpi_large,
            "apps_with_findings": len(apps_with_findings),
            "small_10y": kpi_small_10y,
            "micro_10y": kpi_micro_10y,
            "large_10y": kpi_large_10y,
            "total_10y": kpi_small_10y + kpi_micro_10y + kpi_large_10y,
            "apps_with_findings_10y": len(apps_with_findings_10y),
            # Dollar KPIs
            "total_paid": kpi_total_paid,
            "total_large_rate": kpi_total_large,
            "total_underpayment": kpi_total_delta,
            "reduced_paid": kpi_reduced_paid,
            "reduced_large_rate": kpi_reduced_large,
            "reduced_underpayment": kpi_reduced_delta,
            "total_paid_10y": kpi_total_paid_10y,
            "total_large_rate_10y": kpi_total_large_10y,
            "total_underpayment_10y": kpi_total_delta_10y,
            "reduced_paid_10y": kpi_reduced_paid_10y,
            "reduced_large_rate_10y": kpi_reduced_large_10y,
            "reduced_underpayment_10y": kpi_reduced_delta_10y,
        },
        "cache_stats": {
            "from_cache": from_cache,
            "freshly_analyzed": freshly_analyzed,
        },
    }


def _analyze_prosecution_apps(an_list: List[str]) -> Dict[str, dict]:
    """Analyze prosecution payments for a list of application numbers.

    Returns dict: application_number → {segments, payments, small_count,
    micro_count, large_count, filing_date, total_paid, total_large,
    total_delta}.
    """
    from utils.fee_schedule import calculate_payment_fees, IDS_TRIGGER_CODES
    params = [bigquery.ArrayQueryParameter("an_list", "STRING", an_list)]

    # ── Query A: Status-change events (for building segments) ────
    status_codes = list(_PROS_STATUS_CODES.keys())
    status_in = ", ".join(f"'{c}'" for c in status_codes)
    seg_sql = f"""
    SELECT application_number, event_date, event_code
    FROM `{settings.pfw_transactions_table}`
    WHERE application_number IN UNNEST(@an_list)
      AND event_code IN ({status_in})
    ORDER BY application_number, event_date ASC
    """

    # ── Query B: Payment events + IDS trigger codes ─────────────
    all_query_codes = _PROS_PAYMENT_CODES + _IDS_TRIGGER_CODES
    pay_in = ", ".join(f"'{c}'" for c in all_query_codes)
    pay_sql = f"""
    SELECT application_number, event_date, event_code, event_description
    FROM `{settings.pfw_transactions_table}`
    WHERE application_number IN UNNEST(@an_list)
      AND event_code IN ({pay_in})
    ORDER BY application_number, event_date ASC
    """

    # ── Query C: Filing dates ─────────────────────────────────────
    filing_sql = f"""
    SELECT application_number, filing_date
    FROM `{settings.patent_table}`
    WHERE application_number IN UNNEST(@an_list)
    """

    seg_rows = bq_service.run_query(seg_sql, params)
    pay_rows = bq_service.run_query(pay_sql, params)
    filing_rows = bq_service.run_query(filing_sql, params)

    # Build filing date lookup
    filing_dates: Dict[str, _date] = {}
    for r in filing_rows:
        if r.get("filing_date"):
            filing_dates[r["application_number"]] = r["filing_date"]

    # Group status-change events by application
    seg_by_app: Dict[str, list] = {}
    for r in seg_rows:
        an = r["application_number"]
        seg_by_app.setdefault(an, []).append({
            "date": r["event_date"],
            "code": r["event_code"],
            "status": _PROS_STATUS_CODES.get(r["event_code"], "LARGE"),
        })

    # Build segments per application
    today = _date.today()
    results: Dict[str, dict] = {}

    for an in an_list:
        filing_d = filing_dates.get(an)
        changes = seg_by_app.get(an, [])
        segments = []

        if not changes:
            start = filing_d or today
            segments.append({
                "status": "LARGE",
                "start": _fmt_date(start),
                "end": None,
                "trigger": None,
            })
        else:
            first_change_date = changes[0]["date"]
            seg_start = filing_d or first_change_date
            if seg_start < first_change_date:
                segments.append({
                    "status": "LARGE",
                    "start": _fmt_date(seg_start),
                    "end": _fmt_date(first_change_date),
                    "trigger": None,
                })

            for i, ch in enumerate(changes):
                end_date = changes[i + 1]["date"] if i + 1 < len(changes) else None
                segments.append({
                    "status": ch["status"],
                    "start": _fmt_date(ch["date"]),
                    "end": _fmt_date(end_date),
                    "trigger": ch["code"],
                })

        results[an] = {
            "segments": segments,
            "payments": [],
            "small_count": 0,
            "micro_count": 0,
            "large_count": 0,
            "filing_date": filing_d,
        }

    # Group ALL events by application (including IDS trigger codes for context)
    all_events_by_app: Dict[str, list] = {}
    pay_by_app: Dict[str, list] = {}
    ids_trigger_set = set(IDS_TRIGGER_CODES)
    for r in pay_rows:
        an = r["application_number"]
        ev = {
            "date": r["event_date"],
            "code": r["event_code"],
            "desc": r.get("event_description", ""),
        }
        all_events_by_app.setdefault(an, []).append(ev)
        # Only include actual payment codes (not IDS trigger codes) in the
        # payment list — trigger codes are context-only for IDS conditional
        if r["event_code"] not in ids_trigger_set:
            pay_by_app.setdefault(an, []).append(ev)

    # Classify each payment by segment
    for an in an_list:
        if an not in results:
            continue
        tl = results[an]
        payments = pay_by_app.get(an, [])

        for pay in payments:
            pay_date = pay["date"]
            pay_status = "LARGE"
            for seg in tl["segments"]:
                seg_start = _date.fromisoformat(seg["start"]) if seg["start"] else None
                seg_end = _date.fromisoformat(seg["end"]) if seg["end"] else None
                if seg_start and pay_date >= seg_start:
                    if seg_end is None or pay_date < seg_end:
                        pay_status = seg["status"]
                        break

            tl["payments"].append({
                "d": _fmt_date(pay_date),
                "c": pay["code"],
                "desc": pay["desc"],
                "status": pay_status,
            })

            if pay_status == "SMALL":
                tl["small_count"] += 1
            elif pay_status == "MICRO":
                tl["micro_count"] += 1
            else:
                tl["large_count"] += 1

        # ── Fee calculation: enrich payments with dollar amounts ──
        all_events = all_events_by_app.get(an, [])
        tl["payments"] = calculate_payment_fees(tl["payments"], all_events)
        tl["total_paid"] = sum(p.get("paid", 0) for p in tl["payments"])
        tl["total_large"] = sum(p.get("large", 0) for p in tl["payments"])
        tl["total_delta"] = sum(p.get("delta", 0) for p in tl["payments"])

    return results


def _save_prosecution_cache(results: Dict[str, dict]) -> None:
    """Save analyzed prosecution payment results to BigQuery cache table."""
    if not results:
        return

    client = bq_service.client
    table_ref = settings.prosecution_payment_cache_table
    now = _datetime.utcnow().isoformat()

    rows_to_insert = []
    for an, tl in results.items():
        # Find latest event date across segments and payments
        latest = None
        for seg in tl["segments"]:
            if seg.get("start") and (latest is None or seg["start"] > latest):
                latest = seg["start"]
            if seg.get("end") and (latest is None or seg["end"] > latest):
                latest = seg["end"]
        for pay in tl["payments"]:
            if pay.get("d") and (latest is None or pay["d"] > latest):
                latest = pay["d"]

        rows_to_insert.append({
            "application_number": an,
            "analyzed_at": now,
            "filing_date": _fmt_date(tl.get("filing_date")),
            "segments": json.dumps(tl["segments"]),
            "payments": json.dumps(tl["payments"]),
            "small_count": tl.get("small_count", 0),
            "micro_count": tl.get("micro_count", 0),
            "large_count": tl.get("large_count", 0),
            "latest_event_date": latest,
            "cache_version": _CACHE_VERSION,
        })

    # Delete any existing rows for these apps, then insert fresh
    an_delete = list(results.keys())
    del_params = [bigquery.ArrayQueryParameter("an_list", "STRING", an_delete)]
    del_sql = f"""
    DELETE FROM `{table_ref}`
    WHERE application_number IN UNNEST(@an_list)
    """
    try:
        bq_service.run_query(del_sql, del_params)
    except Exception:
        pass  # table might be empty

    # Insert in batches of 500
    for i in range(0, len(rows_to_insert), 500):
        batch = rows_to_insert[i:i + 500]
        errors = client.insert_rows_json(table_ref, batch)
        if errors:
            log.error("Cache insert errors: %s", errors[:3])


@router.post("/entity-prosecution-kpis")
def get_entity_prosecution_kpis(req: EntityProsecutionRequest) -> Dict[str, Any]:
    """Entity-level prosecution KPIs with server-side batching.

    Checks entity_prosecution_cache first.  On cache miss, batches all
    applications through per-app cache / fresh analysis, aggregates KPIs,
    and saves the entity-level result for instant repeat loads.
    """
    _dollar_kpi_zeros = {
        "total_paid": 0, "total_large_rate": 0, "total_underpayment": 0,
        "reduced_paid": 0, "reduced_large_rate": 0, "reduced_underpayment": 0,
        "total_paid_10y": 0, "total_large_rate_10y": 0, "total_underpayment_10y": 0,
        "reduced_paid_10y": 0, "reduced_large_rate_10y": 0, "reduced_underpayment_10y": 0,
    }
    empty = {"timelines": {}, "date_range": None,
             "payments_detail": [], "summary": {},
             "kpis": {"small": 0, "micro": 0, "large": 0, "total": 0,
                      "apps_with_findings": 0,
                      "small_10y": 0, "micro_10y": 0, "large_10y": 0,
                      "total_10y": 0, "apps_with_findings_10y": 0,
                      **_dollar_kpi_zeros},
             "cache_stats": {"from_cache": 0, "freshly_analyzed": 0}}
    if not req.application_numbers:
        return empty

    entity_name = req.applicant_name.strip()
    an_list = req.application_numbers
    app_count = len(an_list)

    # ── Step 1: Check entity-level cache ──────────────────────────
    try:
        ecache_sql = f"""
        SELECT kpis_json, payments_detail_json, summary_json, timelines_json
        FROM `{settings.entity_prosecution_cache_table}`
        WHERE entity_name = @entity_name
          AND app_count = @app_count
          AND cache_version = {_CACHE_VERSION}
        LIMIT 1
        """
        ecache_params = [
            bigquery.ScalarQueryParameter("entity_name", "STRING", entity_name),
            bigquery.ScalarQueryParameter("app_count", "INT64", app_count),
        ]
        ecache_rows = bq_service.run_query(ecache_sql, ecache_params)
        if ecache_rows:
            row = ecache_rows[0]
            log.info("Entity prosecution cache HIT for %s (%d apps)",
                     entity_name, app_count)
            result = {
                "kpis": json.loads(row["kpis_json"]) if row.get("kpis_json") else empty["kpis"],
                "payments_detail": json.loads(row["payments_detail_json"]) if row.get("payments_detail_json") else [],
                "summary": json.loads(row["summary_json"]) if row.get("summary_json") else {},
                "timelines": json.loads(row["timelines_json"]) if row.get("timelines_json") else {},
                "date_range": None,
                "cache_stats": {"from_cache": app_count, "freshly_analyzed": 0},
            }
            # Rebuild date_range from timelines
            g_min, g_max = None, None
            for _an, tl in result["timelines"].items():
                for seg in tl.get("segments", []):
                    for k in ("start", "end"):
                        v = seg.get(k)
                        if v:
                            if g_min is None or v < g_min: g_min = v
                            if g_max is None or v > g_max: g_max = v
                for pay in tl.get("payments", []):
                    v = pay.get("d")
                    if v:
                        if g_min is None or v < g_min: g_min = v
                        if g_max is None or v > g_max: g_max = v
            if g_min and g_max:
                result["date_range"] = {"min": g_min, "max": g_max}
            return result
    except Exception as e:
        log.warning("Entity cache lookup failed: %s", e)

    # ── Step 2: Cache miss — batch through per-app analysis ───────
    log.info("Entity prosecution cache MISS for %s (%d apps) — analyzing",
             entity_name, app_count)

    BATCH = 1000
    all_timelines: Dict[str, dict] = {}
    total_from_cache = 0
    total_fresh = 0

    for i in range(0, len(an_list), BATCH):
        batch = an_list[i:i + BATCH]

        # Check per-app cache
        cache_params = [bigquery.ArrayQueryParameter("an_list", "STRING", batch)]
        cache_sql = f"""
        SELECT application_number, segments, payments,
               small_count, micro_count, large_count, filing_date
        FROM `{settings.prosecution_payment_cache_table}`
        WHERE application_number IN UNNEST(@an_list)
          AND cache_version = {_CACHE_VERSION}
        """
        cache_rows = bq_service.run_query(cache_sql, cache_params)

        cached: Dict[str, dict] = {}
        for r in cache_rows:
            an = r["application_number"]
            cached[an] = {
                "segments": json.loads(r["segments"]) if r.get("segments") else [],
                "payments": json.loads(r["payments"]) if r.get("payments") else [],
                "small_count": r.get("small_count", 0),
                "micro_count": r.get("micro_count", 0),
                "large_count": r.get("large_count", 0),
                "filing_date": r.get("filing_date"),
            }
        total_from_cache += len(cached)

        uncached = [an for an in batch if an not in cached]
        fresh: Dict[str, dict] = {}
        if uncached:
            fresh = _analyze_prosecution_apps(uncached)
            total_fresh += len(fresh)
            _save_prosecution_cache(fresh)

        # Merge into all_timelines
        for an in batch:
            if an in fresh:
                all_timelines[an] = fresh[an]
            elif an in cached:
                all_timelines[an] = cached[an]

    # ── Step 3: Aggregate KPIs from all timelines ─────────────────
    timelines_out: Dict[str, dict] = {}
    payments_detail = []
    summary: Dict[str, Dict[str, int]] = {}
    kpi_small = kpi_micro = kpi_large = 0
    kpi_small_10y = kpi_micro_10y = kpi_large_10y = 0
    apps_with_findings: set = set()
    apps_with_findings_10y: set = set()
    kpi_total_paid = kpi_total_large = kpi_total_delta = 0
    kpi_reduced_paid = kpi_reduced_large = kpi_reduced_delta = 0
    kpi_total_paid_10y = kpi_total_large_10y = kpi_total_delta_10y = 0
    kpi_reduced_paid_10y = kpi_reduced_large_10y = kpi_reduced_delta_10y = 0
    global_min = global_max = None
    ten_yr_cutoff = _fmt_date(_date.today().replace(year=_date.today().year - 10))

    def _upd(d_str):
        nonlocal global_min, global_max
        if not d_str: return
        if global_min is None or d_str < global_min: global_min = d_str
        if global_max is None or d_str > global_max: global_max = d_str

    for an, tl in all_timelines.items():
        timelines_out[an] = {"segments": tl["segments"], "payments": tl["payments"]}

        for seg in tl["segments"]:
            _upd(seg.get("start"))
            _upd(seg.get("end"))

        for pay in tl["payments"]:
            _upd(pay.get("d"))
            ps = pay.get("status", "LARGE")
            pp = pay.get("paid", 0)
            pl = pay.get("large", 0)
            pd = pay.get("delta", 0)

            if ps == "SMALL":
                kpi_small += 1; apps_with_findings.add(an)
            elif ps == "MICRO":
                kpi_micro += 1; apps_with_findings.add(an)
            else:
                kpi_large += 1

            kpi_total_paid += pp; kpi_total_large += pl; kpi_total_delta += pd
            if ps in ("SMALL", "MICRO"):
                kpi_reduced_paid += pp; kpi_reduced_large += pl; kpi_reduced_delta += pd

            if pay.get("d") and pay["d"] >= ten_yr_cutoff:
                if ps == "SMALL":
                    kpi_small_10y += 1; apps_with_findings_10y.add(an)
                elif ps == "MICRO":
                    kpi_micro_10y += 1; apps_with_findings_10y.add(an)
                else:
                    kpi_large_10y += 1
                kpi_total_paid_10y += pp; kpi_total_large_10y += pl; kpi_total_delta_10y += pd
                if ps in ("SMALL", "MICRO"):
                    kpi_reduced_paid_10y += pp; kpi_reduced_large_10y += pl; kpi_reduced_delta_10y += pd

            yr = pay["d"][:4] if pay.get("d") else "Unknown"
            if yr not in summary: summary[yr] = {}
            summary[yr][pay["c"]] = summary[yr].get(pay["c"], 0) + 1

            if ps in ("SMALL", "MICRO"):
                origin_code = origin_date = None
                for seg in tl["segments"]:
                    s_start = seg.get("start")
                    s_end = seg.get("end")
                    if s_start and pay["d"] >= s_start:
                        if s_end is None or pay["d"] < s_end:
                            origin_code = seg.get("trigger")
                            origin_date = s_start
                            break
                payments_detail.append({
                    "application_number": an,
                    "event_date": pay["d"],
                    "event_code": pay["c"],
                    "event_description": pay.get("desc", ""),
                    "claimed_status": ps,
                    "fee_category": pay.get("cat"),
                    "amount_paid": pp,
                    "large_rate": pl,
                    "underpayment": pd,
                    "origin_code": origin_code,
                    "origin_date": origin_date,
                })

    kpis = {
        "small": kpi_small, "micro": kpi_micro, "large": kpi_large,
        "total": kpi_small + kpi_micro + kpi_large,
        "apps_with_findings": len(apps_with_findings),
        "small_10y": kpi_small_10y, "micro_10y": kpi_micro_10y, "large_10y": kpi_large_10y,
        "total_10y": kpi_small_10y + kpi_micro_10y + kpi_large_10y,
        "apps_with_findings_10y": len(apps_with_findings_10y),
        "total_paid": kpi_total_paid, "total_large_rate": kpi_total_large,
        "total_underpayment": kpi_total_delta,
        "reduced_paid": kpi_reduced_paid, "reduced_large_rate": kpi_reduced_large,
        "reduced_underpayment": kpi_reduced_delta,
        "total_paid_10y": kpi_total_paid_10y, "total_large_rate_10y": kpi_total_large_10y,
        "total_underpayment_10y": kpi_total_delta_10y,
        "reduced_paid_10y": kpi_reduced_paid_10y, "reduced_large_rate_10y": kpi_reduced_large_10y,
        "reduced_underpayment_10y": kpi_reduced_delta_10y,
    }

    # ── Step 4: Save entity-level cache ───────────────────────────
    try:
        client = bq_service.client
        del_sql = f"""
        DELETE FROM `{settings.entity_prosecution_cache_table}`
        WHERE entity_name = @entity_name
        """
        del_params = [bigquery.ScalarQueryParameter("entity_name", "STRING", entity_name)]
        bq_service.run_query(del_sql, del_params)

        cache_row = {
            "entity_name": entity_name,
            "app_count": app_count,
            "cache_version": _CACHE_VERSION,
            "analyzed_at": _datetime.utcnow().isoformat(),
            "kpis_json": json.dumps(kpis),
            "payments_detail_json": json.dumps(payments_detail),
            "summary_json": json.dumps(summary),
            "timelines_json": json.dumps(timelines_out),
        }
        errors = client.insert_rows_json(
            settings.entity_prosecution_cache_table, [cache_row])
        if errors:
            log.error("Entity cache insert errors: %s", errors[:3])
        else:
            log.info("Entity prosecution cache saved for %s (%d apps)",
                     entity_name, app_count)
    except Exception as e:
        log.warning("Entity cache save failed: %s", e)

    return {
        "timelines": timelines_out,
        "date_range": {"min": global_min, "max": global_max} if global_min and global_max else None,
        "payments_detail": payments_detail,
        "summary": summary,
        "kpis": kpis,
        "cache_stats": {"from_cache": total_from_cache, "freshly_analyzed": total_fresh},
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
      COUNTIF(b.event_code = 'M1559' AND b.during_ownership) AS pay_m1559,
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
        ('M3551', 'pay_m3551'), ('M3552', 'pay_m3552'), ('M3553', 'pay_m3553'), ('M1559', 'pay_m1559'),
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
    pg_m3551 = 0; pg_m3552 = 0; pg_m3553 = 0; pg_m1559 = 0
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
        pg_m1559 += pg.get("pay_m1559", 0)
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
                "m3551": pg_m3551, "m3552": pg_m3552, "m3553": pg_m3553, "m1559": pg_m1559,
            },
            "decl_smal": pg_decl_smal,
            "decl_big": pg_decl_big,
            "decl_micr": pg_decl_micr,
        },
        "results": results,
    }


# ── Invoice-Based KPIs (from extracted PDF data) ─────────────────

@router.post("/invoice-kpis")
def get_invoice_kpis(req: InvoiceKpisRequest) -> Dict[str, Any]:
    """Compute prosecution KPIs from extracted invoice data (not event codes).

    Parses fees_json from invoice_extractions, determines entity status
    from fee_code first digit (1=LARGE, 2=SMALL, 3=MICRO, 4=SMALL),
    and uses ratio-based multipliers to compute large-rate equivalents
    and underpayment amounts.
    """
    if not req.application_numbers:
        return {"kpis": {}, "per_app": {}, "source": "invoice"}

    client = bigquery.Client(location="us-west1")

    # Fetch all extracted invoices — same dedup logic as extraction-data endpoint
    query = """
    SELECT
      application_number,
      mail_date,
      fees_json,
      total_amount,
      gcs_path
    FROM (
      SELECT *,
        ROW_NUMBER() OVER (PARTITION BY gcs_path ORDER BY extracted_at DESC) as rn
      FROM `uspto-data-app.uspto_data.invoice_extractions`
      WHERE application_number IN UNNEST(@apps)
        AND (extraction_status = 'extracted'
             OR (extraction_status IS NULL AND total_amount IS NOT NULL))
    )
    WHERE rn = 1
    ORDER BY application_number, mail_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("apps", "STRING", req.application_numbers)
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())

    # Accumulators
    kpi_s = kpi_m = kpi_l = 0
    kpi_s10 = kpi_m10 = kpi_l10 = 0
    apps_findings: set = set()
    apps_findings_10y: set = set()

    tot_paid = tot_large = tot_delta = 0.0
    red_paid = red_large = red_delta = 0.0
    tot_paid_10 = tot_large_10 = tot_delta_10 = 0.0
    red_paid_10 = red_large_10 = red_delta_10 = 0.0

    ten_yr_cutoff = _date.today().replace(year=_date.today().year - 10)

    per_app: Dict[str, dict] = {}

    for row in rows:
        app = row.application_number
        mail_date_raw = row.mail_date  # may be date, datetime, or str
        if isinstance(mail_date_raw, str):
            try:
                mail_date = _date.fromisoformat(mail_date_raw[:10])
            except ValueError:
                mail_date = None
        elif isinstance(mail_date_raw, _date):
            mail_date = mail_date_raw
        else:
            mail_date = None
        fees_raw = row.fees_json
        if not fees_raw:
            continue
        try:
            fees = json.loads(fees_raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(fees, list):
            continue

        for fee in fees:
            if not isinstance(fee, dict):
                continue
            amount = 0.0
            try:
                amount = float(fee.get("amount") or 0)
            except (ValueError, TypeError):
                continue
            if amount <= 0:
                continue

            fee_code = str(fee.get("fee_code") or "")
            entity = _entity_from_fee_code(fee_code)
            mult = _large_rate_multiplier(entity, mail_date)
            large_equiv = round(amount * mult, 2)
            delta = round(large_equiv - amount, 2)

            is_reduced = entity in ("SMALL", "MICRO")
            is_10y = mail_date is not None and mail_date >= ten_yr_cutoff

            # Count KPIs
            if entity == "SMALL":
                kpi_s += 1
                apps_findings.add(app)
                if is_10y:
                    kpi_s10 += 1
                    apps_findings_10y.add(app)
            elif entity == "MICRO":
                kpi_m += 1
                apps_findings.add(app)
                if is_10y:
                    kpi_m10 += 1
                    apps_findings_10y.add(app)
            else:
                kpi_l += 1
                if is_10y:
                    kpi_l10 += 1

            # Dollar totals
            tot_paid += amount
            tot_large += large_equiv
            tot_delta += delta
            if is_reduced:
                red_paid += amount
                red_large += large_equiv
                red_delta += delta
            if is_10y:
                tot_paid_10 += amount
                tot_large_10 += large_equiv
                tot_delta_10 += delta
                if is_reduced:
                    red_paid_10 += amount
                    red_large_10 += large_equiv
                    red_delta_10 += delta

            # Per-app accumulation
            if app not in per_app:
                per_app[app] = {
                    "paid": 0.0, "large": 0.0, "delta": 0.0,
                    "small": 0, "micro": 0, "large_count": 0, "fee_count": 0,
                }
            pa = per_app[app]
            pa["paid"] += amount
            pa["large"] += large_equiv
            pa["delta"] += delta
            pa["fee_count"] += 1
            if entity == "SMALL":
                pa["small"] += 1
            elif entity == "MICRO":
                pa["micro"] += 1
            else:
                pa["large_count"] += 1

    return {
        "kpis": {
            "small": kpi_s, "micro": kpi_m, "large": kpi_l,
            "total": kpi_s + kpi_m + kpi_l,
            "apps_with_findings": len(apps_findings),
            "small_10y": kpi_s10, "micro_10y": kpi_m10, "large_10y": kpi_l10,
            "total_10y": kpi_s10 + kpi_m10 + kpi_l10,
            "apps_with_findings_10y": len(apps_findings_10y),
            "total_paid": round(tot_paid, 2),
            "total_large_rate": round(tot_large, 2),
            "total_underpayment": round(tot_delta, 2),
            "reduced_paid": round(red_paid, 2),
            "reduced_large_rate": round(red_large, 2),
            "reduced_underpayment": round(red_delta, 2),
            "total_paid_10y": round(tot_paid_10, 2),
            "total_large_rate_10y": round(tot_large_10, 2),
            "total_underpayment_10y": round(tot_delta_10, 2),
            "reduced_paid_10y": round(red_paid_10, 2),
            "reduced_large_rate_10y": round(red_large_10, 2),
            "reduced_underpayment_10y": round(red_delta_10, 2),
        },
        "per_app": per_app,
        "source": "invoice",
    }


# ── Invoice Extraction Data (from PDF pipeline) ──────────────────

@router.post("/extraction-data")
def get_extraction_data(req: ExtractionDataRequest) -> Dict[str, Any]:
    """Return extracted invoice data keyed by application_number.

    Used by the frontend to show rich tooltips on payment icons
    with actual fee details from extracted PDFs.
    """
    if not req.application_numbers:
        return {"extractions": {}, "stats": {"apps_with_extractions": 0, "total_extractions": 0}}

    client = bigquery.Client(location="us-west1")

    # Fetch all extracted records for these applications.
    # Include rows with extraction_status='extracted' OR NULL (calibration data).
    # Deduplicate by gcs_path: pick the row with the latest extracted_at.
    query = """
    SELECT
      application_number,
      mail_date,
      doc_code,
      doc_description,
      entity_status,
      fees_json,
      total_amount,
      gcs_path,
      extraction_method,
      extraction_status
    FROM (
      SELECT *,
        ROW_NUMBER() OVER (PARTITION BY gcs_path ORDER BY extracted_at DESC) as rn
      FROM `uspto-data-app.uspto_data.invoice_extractions`
      WHERE application_number IN UNNEST(@apps)
        AND (extraction_status = 'extracted' OR (extraction_status IS NULL AND total_amount IS NOT NULL))
    )
    WHERE rn = 1
    ORDER BY application_number, mail_date DESC
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("apps", "STRING", req.application_numbers)
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())

    # Group by application_number
    extractions: Dict[str, list] = {}
    for r in rows:
        app = r.application_number
        if app not in extractions:
            extractions[app] = []

        # Parse fees_json safely
        fees = []
        if r.fees_json:
            try:
                fees = json.loads(r.fees_json)
            except (json.JSONDecodeError, TypeError):
                pass

        extractions[app].append({
            "mail_date": r.mail_date,
            "doc_code": r.doc_code or "",
            "description": r.doc_description or "",
            "entity_status": r.entity_status,
            "total_amount": float(r.total_amount) if r.total_amount else None,
            "fees": fees,
            "gcs_path": r.gcs_path or "",
            "extraction_method": r.extraction_method or "",
        })

    apps_with = len(extractions)
    total_docs = sum(len(v) for v in extractions.values())

    return {
        "extractions": extractions,
        "stats": {
            "apps_with_extractions": apps_with,
            "total_extractions": total_docs,
        },
    }


# ── Extraction Progress (keyed by representative name) ───────────

@router.post("/extraction-progress")
def get_extraction_progress(req: ExtractionProgressRequest) -> Dict[str, Any]:
    """Return extraction pipeline progress for a representative name's portfolio.

    Queries invoice_extractions by application_numbers (pre-computed by the
    frontend from the MDM-resolved portfolio).  Returns counts for each
    extraction_status so the frontend can render two progress gauges:
      Gauge 1 — PDF Retrieval:  apps_checked / total_apps_in_portfolio
      Gauge 2 — Data Extraction: extracted_docs / total_docs_retrieved
    """
    if not req.application_numbers:
        return {
            "representative_name": req.representative_name,
            "total_apps_in_portfolio": 0,
            "apps_checked": 0,
            "total_docs_retrieved": 0,
            "extracted_docs": 0,
            "pending_extraction": 0,
            "failed_docs": 0,
            "no_docs_apps": 0,
            "retrieval_pct": 0,
            "extraction_pct": 0,
            "phase": "not_started",
        }

    client = bigquery.Client(location="us-west1")

    query = """
    SELECT
      COUNT(DISTINCT application_number) as apps_with_records,
      COUNTIF(extraction_status = 'extracted') as extracted_docs,
      COUNTIF(extraction_status = 'downloaded') as pending_docs,
      COUNTIF(extraction_status = 'failed') as failed_docs,
      COUNTIF(extraction_status = 'no_docs') as no_docs_apps
    FROM `uspto-data-app.uspto_data.invoice_extractions`
    WHERE application_number IN UNNEST(@apps)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("apps", "STRING", req.application_numbers)
        ]
    )
    rows = list(client.query(query, job_config=job_config).result())

    if not rows or rows[0].apps_with_records == 0:
        return {
            "representative_name": req.representative_name,
            "total_apps_in_portfolio": len(req.application_numbers),
            "apps_checked": 0,
            "total_docs_retrieved": 0,
            "extracted_docs": 0,
            "pending_extraction": 0,
            "failed_docs": 0,
            "no_docs_apps": 0,
            "retrieval_pct": 0,
            "extraction_pct": 0,
            "phase": "not_started",
        }

    r = rows[0]
    total_apps = len(req.application_numbers)
    apps_checked = r.apps_with_records or 0
    extracted = r.extracted_docs or 0
    pending = r.pending_docs or 0
    failed = r.failed_docs or 0
    no_docs = r.no_docs_apps or 0
    total_retrieved = extracted + pending + failed

    retrieval_pct = round(apps_checked / total_apps * 100, 1) if total_apps > 0 else 0
    extraction_pct = round(extracted / total_retrieved * 100, 1) if total_retrieved > 0 else 0

    # Phase logic
    if pending > 0:
        phase = "in_progress"
    else:
        phase = "complete"

    return {
        "representative_name": req.representative_name,
        "total_apps_in_portfolio": total_apps,
        "apps_checked": apps_checked,
        "total_docs_retrieved": total_retrieved,
        "extracted_docs": extracted,
        "pending_extraction": pending,
        "failed_docs": failed,
        "no_docs_apps": no_docs,
        "retrieval_pct": retrieval_pct,
        "extraction_pct": extraction_pct,
        "phase": phase,
    }


@router.post("/queue-extraction")
def queue_extraction(req: QueueExtractionRequest) -> Dict[str, Any]:
    """Queue application numbers for invoice extraction and trigger the worker.

    1. MERGEs app numbers into invoice_extraction_queue (upsert, no duplicates)
    2. Checks if the Cloud Run Job is already running
    3. Triggers the worker if not running
    """
    if not req.application_numbers:
        return {"status": "empty", "queued": 0, "message": "No application numbers provided."}

    client = bigquery.Client(location="us-west1")

    # Step 1: MERGE app numbers into queue (upsert)
    merge_sql = """
    MERGE `uspto-data-app.uspto_data.invoice_extraction_queue` T
    USING UNNEST(@apps) AS app_number
    ON T.application_number = app_number
    WHEN NOT MATCHED THEN
      INSERT (application_number, queued_at, queued_by)
      VALUES (app_number, CURRENT_TIMESTAMP(), @queued_by)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("apps", "STRING", req.application_numbers),
            bigquery.ScalarQueryParameter("queued_by", "STRING", req.representative_name or "unknown"),
        ]
    )
    merge_job = client.query(merge_sql, job_config=job_config)
    merge_job.result()  # wait for completion
    queued_count = len(req.application_numbers)

    # Step 2: Check if worker is already running
    try:
        from google.cloud import run_v2

        exec_client = run_v2.ExecutionsClient()
        job_name = "projects/uspto-data-app/locations/us-central1/jobs/uspto-extract-invoices"
        executions = exec_client.list_executions(parent=job_name)
        for ex in executions:
            if ex.running_count and ex.running_count > 0:
                return {
                    "status": "already_running",
                    "queued": queued_count,
                    "message": f"Queued {queued_count:,} applications. Worker already running.",
                }
            break  # only check most recent execution

        # Step 3: Trigger the worker — no ENTITY_NAME override
        jobs_client = run_v2.JobsClient()
        run_request = run_v2.RunJobRequest(name=job_name)
        operation = jobs_client.run_job(request=run_request)
        exec_name = operation.metadata.name if hasattr(operation, "metadata") else "started"

        return {
            "status": "started",
            "queued": queued_count,
            "execution_name": str(exec_name),
            "message": f"Queued {queued_count:,} applications. Worker started.",
        }

    except Exception as e:
        log.error("Failed to check/trigger Cloud Run Job: %s", e)
        return {
            "status": "queued_only",
            "queued": queued_count,
            "message": f"Queued {queued_count:,} applications. Could not trigger worker: {e}",
        }




# ── Helpers ───────────────────────────────────────────────────────

def _fmt_date(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)
