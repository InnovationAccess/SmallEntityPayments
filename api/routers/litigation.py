"""Patent Litigation lookup via Unified Patents public API.

Queries the Unified Patents Elasticsearch endpoint for litigation cases
involving specific patents.  Results are cached in BigQuery for 30 days
to avoid repeated API calls.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List

import requests as http_requests
from fastapi import APIRouter
from google.cloud import bigquery
from pydantic import BaseModel

from api.config import settings
from api.services.bigquery_service import bq_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/litigation", tags=["Litigation"])

UNIFIED_PATENTS_URL = (
    "https://api.unifiedpatents.com/cases/"
    "cafc-ln,cfc-ln,dc-ln,itc-ln,sc-ln/_search"
)
BATCH_SIZE = 200
BATCH_DELAY = 2.0  # seconds between API batches
CACHE_DAYS = 30
_SOURCE_FIELDS = [
    "case_id", "case_no", "filed_date", "closed_date", "court",
    "status", "cause_of_action", "plaintiff", "defendant",
    "judge", "flag", "patents", "product", "entity_type", "industry",
]


class LitigationRequest(BaseModel):
    patent_numbers: List[str]


# ── Endpoint ─────────────────────────────────────────────────────

@router.post("/lookup")
def litigation_lookup(req: LitigationRequest) -> Dict[str, Any]:
    """Look up litigation for a list of patent numbers.

    Checks BigQuery cache first; only queries Unified Patents API
    for patents not checked within the last 30 days.
    """
    if not req.patent_numbers:
        return _empty_response()

    patent_numbers = list(set(req.patent_numbers))

    # Step 1: Check cache — which patents were checked recently?
    cached_set, uncached = _check_cache(patent_numbers)

    # Step 2: Query Unified Patents API for uncached patents
    fresh_results: Dict[str, List[Dict]] = {}
    if uncached:
        fresh_results = _query_api_batched(uncached)
        try:
            _write_cache(fresh_results, uncached)
        except Exception:
            logger.exception("Failed to write litigation cache")

    # Step 3: Load cached litigation records (only for previously-cached patents)
    cached_results = _load_cached_litigation(list(cached_set)) if cached_set else {}

    # Step 4: Merge — cached patents use cached_results, fresh patents use fresh_results
    litigated: Dict[str, List[Dict]] = {}
    for pn in patent_numbers:
        cases = cached_results.get(pn, []) if pn in cached_set else fresh_results.get(pn, [])
        if cases:
            litigated[pn] = cases

    # Build case-centric list (deduplicated by case_id)
    case_map: Dict[str, Dict] = {}
    for pn, pn_cases in litigated.items():
        for c in pn_cases:
            cid = c.get("case_id", "")
            if cid not in case_map:
                case_map[cid] = {**c, "portfolio_patents": []}
            case_map[cid]["portfolio_patents"].append(pn)

    return {
        "litigated_patents": litigated,
        "cases": list(case_map.values()),
        "litigated_count": len(litigated),
        "total_cases": len(case_map),
        "total_checked": len(patent_numbers),
        "from_cache": len(cached_set),
        "freshly_queried": len(uncached),
    }


# ── Cache helpers ────────────────────────────────────────────────

def _check_cache(patent_numbers: List[str]) -> tuple:
    """Return (set of cached patent_numbers, list of uncached)."""
    cutoff = date.today() - timedelta(days=CACHE_DAYS)
    params = [
        bigquery.ArrayQueryParameter("pn_list", "STRING", patent_numbers),
        bigquery.ScalarQueryParameter("cutoff", "DATE", cutoff),
    ]
    sql = f"""
    SELECT DISTINCT patent_number
    FROM `{settings.patent_litigation_cache_table}`
    WHERE patent_number IN UNNEST(@pn_list)
      AND fetched_date >= @cutoff
    """
    rows = bq_service.run_query(sql, params)
    cached = {r["patent_number"] for r in rows}
    uncached = [pn for pn in patent_numbers if pn not in cached]
    return cached, uncached


def _load_cached_litigation(patent_numbers: List[str]) -> Dict[str, List[Dict]]:
    """Load litigation records from BigQuery cache."""
    cutoff = date.today() - timedelta(days=CACHE_DAYS)
    params = [
        bigquery.ArrayQueryParameter("pn_list", "STRING", patent_numbers),
        bigquery.ScalarQueryParameter("cutoff", "DATE", cutoff),
    ]
    sql = f"""
    SELECT patent_number, case_id, case_no, filed_date, closed_date,
           court, status, cause_of_action, plaintiff, defendant, judge, flag,
           entity_type, industry, product
    FROM `{settings.patent_litigation_table}`
    WHERE patent_number IN UNNEST(@pn_list)
      AND fetched_date >= @cutoff
    GROUP BY patent_number, case_id, case_no, filed_date, closed_date,
             court, status, cause_of_action, plaintiff, defendant, judge, flag,
             entity_type, industry, product
    """
    rows = bq_service.run_query(sql, params)
    result: Dict[str, List[Dict]] = {}
    for r in rows:
        pn = r["patent_number"]
        result.setdefault(pn, []).append({
            "case_id": r.get("case_id"),
            "case_no": r.get("case_no"),
            "filed_date": _fmt_date(r.get("filed_date")),
            "closed_date": _fmt_date(r.get("closed_date")),
            "court": r.get("court"),
            "status": r.get("status"),
            "cause_of_action": r.get("cause_of_action"),
            "plaintiff": r.get("plaintiff"),
            "defendant": r.get("defendant"),
            "judge": r.get("judge"),
            "flag": r.get("flag"),
            "entity_type": r.get("entity_type"),
            "industry": r.get("industry"),
            "product": r.get("product"),
        })
    return result


# ── Unified Patents API ──────────────────────────────────────────

def _query_api_batched(patent_numbers: List[str]) -> Dict[str, List[Dict]]:
    """Query Unified Patents API in batches with rate limiting."""
    all_results: Dict[str, List[Dict]] = {}
    for i in range(0, len(patent_numbers), BATCH_SIZE):
        batch = patent_numbers[i:i + BATCH_SIZE]
        try:
            batch_results = _query_unified_patents(batch)
            for pn, cases in batch_results.items():
                all_results.setdefault(pn, []).extend(cases)
        except Exception:
            logger.exception("Unified Patents API batch %d failed", i // BATCH_SIZE)
        # Rate limit: sleep between batches (not after the last one)
        if i + BATCH_SIZE < len(patent_numbers):
            time.sleep(BATCH_DELAY)
    return all_results


def _query_unified_patents(patent_numbers: List[str]) -> Dict[str, List[Dict]]:
    """Query Unified Patents ES API for a batch of patent numbers."""
    body = {
        "query": {"terms": {"patents": patent_numbers}},
        "size": 10000,
        "_source": _SOURCE_FIELDS,
    }
    resp = http_requests.post(
        UNIFIED_PATENTS_URL,
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results: Dict[str, List[Dict]] = {}
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        case_dict = {
            "case_id": src.get("case_id", ""),
            "case_no": src.get("case_no", ""),
            "filed_date": _parse_es_date(src.get("filed_date")),
            "closed_date": _parse_es_date(src.get("closed_date")),
            "court": src.get("court", ""),
            "status": src.get("status", ""),
            "cause_of_action": "; ".join(src.get("cause_of_action") or []),
            "plaintiff": "; ".join(src.get("plaintiff") or []),
            "defendant": "; ".join(src.get("defendant") or []),
            "judge": "; ".join(src.get("judge") or []),
            "flag": src.get("flag", ""),
            "product": _join_field(src.get("product")),
            "entity_type": _join_field(src.get("entity_type")),
            "industry": _join_field(src.get("industry")),
        }
        # Each case may involve multiple patents — link to each
        for pn in src.get("patents") or []:
            if pn in patent_numbers:
                results.setdefault(pn, []).append(case_dict)
    return results


# ── Cache write ──────────────────────────────────────────────────

def _write_cache(
    results: Dict[str, List[Dict]], all_queried: List[str]
) -> None:
    """Write litigation results and cache entries to BigQuery."""
    today_str = date.today().isoformat()
    client = bq_service.client

    # Write litigation records
    lit_rows = []
    for pn, cases in results.items():
        for c in cases:
            lit_rows.append({
                "patent_number": pn,
                "case_id": c["case_id"],
                "case_no": c["case_no"],
                "filed_date": c["filed_date"],
                "closed_date": c["closed_date"],
                "court": c["court"],
                "status": c["status"],
                "cause_of_action": c["cause_of_action"],
                "plaintiff": c["plaintiff"],
                "defendant": c["defendant"],
                "judge": c["judge"],
                "flag": c["flag"],
                "entity_type": c.get("entity_type", ""),
                "industry": c.get("industry", ""),
                "product": c.get("product", ""),
                "fetched_date": today_str,
            })
    if lit_rows:
        errors = client.insert_rows_json(
            settings.patent_litigation_table, lit_rows
        )
        if errors:
            logger.error("Litigation insert errors: %s", errors[:5])

    # Update cache tracker for ALL queried patents (including no-results)
    cache_rows = [
        {"patent_number": pn, "fetched_date": today_str}
        for pn in all_queried
    ]
    if cache_rows:
        # Insert in chunks to avoid payload limits
        for i in range(0, len(cache_rows), 5000):
            chunk = cache_rows[i:i + 5000]
            errors = client.insert_rows_json(
                settings.patent_litigation_cache_table, chunk
            )
            if errors:
                logger.error("Cache insert errors: %s", errors[:5])


# ── Utilities ────────────────────────────────────────────────────

def _parse_es_date(val) -> str | None:
    """Parse Elasticsearch date string to YYYY-MM-DD."""
    if not val:
        return None
    # ES dates look like "2021-09-28T00:00:00.000"
    return val[:10] if len(val) >= 10 else val


def _fmt_date(val) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val) if val else None


def _join_field(val) -> str:
    """Join a field that may be a list or a string."""
    if not val:
        return ""
    if isinstance(val, list):
        return "; ".join(str(v) for v in val)
    return str(val)


def _empty_response() -> Dict[str, Any]:
    return {
        "litigated_patents": {},
        "cases": [],
        "litigated_count": 0,
        "total_cases": 0,
        "total_checked": 0,
        "from_cache": 0,
        "freshly_queried": 0,
    }
