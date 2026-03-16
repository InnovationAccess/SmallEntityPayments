"""SEC Leads pipeline orchestrator.

Discovers 10-K filings from SEC EDGAR, scores patent importance,
extracts board members, generates sales documents, enriches emails.

Usage:
    python patent_analyzer/run_pipeline.py                     # yesterday
    python patent_analyzer/run_pipeline.py --date 2026-03-14   # specific date
    python patent_analyzer/run_pipeline.py --lookback 365      # backfill past year
"""

import argparse
import json
import logging
import sys
import os
from datetime import datetime, date, timedelta

from google.cloud import bigquery

from patent_analyzer.sec_edgar import (
    discover_10k_filers,
    fetch_filing_text,
    extract_sections,
)
from patent_analyzer.scoring import score_company
from patent_analyzer.board_extraction import extract_officers_and_board
from patent_analyzer.documents import generate_memo, generate_letter
from patent_analyzer.apollo_enrichment import enrich_contacts
from patent_analyzer.report_generator import generate_report, upload_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID", "uspto-data-app")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET", "uspto_data")
BQ_LOCATION = "us-west1"


def _previous_business_day(ref_date: date = None) -> date:
    """Return the previous business day (Mon-Fri)."""
    d = ref_date or date.today()
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


def analyze_company(ticker: str, company_info: dict, analysis_date: str) -> dict | None:
    """Process one company end-to-end. Returns result dict or None on error.

    Each step is wrapped in try/except so partial results are preserved.
    """
    company_name = company_info.get("company_name", ticker)
    cik = company_info.get("cik", "")
    filing_url = company_info.get("filing_url", "")
    filing_date = company_info.get("filing_date", "")
    form_type = company_info.get("form", "10-K")

    log.info("=" * 60)
    log.info("Analyzing %s (%s) — CIK %s", company_name, ticker, cik)

    result = {
        "analysis_date": analysis_date,
        "company_name": company_name,
        "ticker": ticker,
        "cik": cik,
        "filing_date": filing_date,
        "filing_url": filing_url,
        "score": 1,
        "rationale": "",
        "key_excerpts_json": "[]",
        "stats_json": "{}",
        "gist": "",
        "secretary_name": None,
        "secretary_title": None,
        "general_counsel_name": None,
        "general_counsel_title": None,
        "board_chair_name": None,
        "board_chair_title": None,
        "ceo_name": None,
        "cfo_name": None,
        "board_members_json": "[]",
        "memo_text": None,
        "letter_text": None,
        "apollo_enriched": False,
    }

    # Step 1: Download and parse filing text
    try:
        log.info("  Downloading filing: %s", filing_url[:80])
        filing_text = fetch_filing_text(filing_url)
        log.info("  Filing text: %d chars", len(filing_text))
    except Exception as e:
        log.error("  Failed to download filing for %s: %s", ticker, e)
        result["gist"] = f"Filing download failed: {e}"
        return result

    # Step 2: Extract sections
    try:
        sections = extract_sections(filing_text)
        section_names = [k for k in sections.keys()]
        log.info("  Extracted sections: %s", section_names)
    except Exception as e:
        log.error("  Section extraction failed for %s: %s", ticker, e)
        sections = {"full_text": filing_text[:90_000]}

    # Step 3: Score patent importance
    try:
        scoring_result = score_company(company_name, sections)
        result["score"] = scoring_result["score"]
        result["rationale"] = scoring_result["rationale"]
        result["gist"] = scoring_result["gist"]
        result["key_excerpts_json"] = scoring_result["key_excerpts_json"]
        result["stats_json"] = scoring_result["stats_json"]
        log.info("  Score: %d/10", result["score"])
    except Exception as e:
        log.error("  Scoring failed for %s: %s", ticker, e)
        result["gist"] = f"Scoring error: {e}"

    # Step 4: Extract board members (always — needed for the table)
    try:
        officers = extract_officers_and_board(filing_text, cik, company_name)
        if officers.get("secretary"):
            result["secretary_name"] = officers["secretary"]["name"]
            result["secretary_title"] = officers["secretary"].get("title", "")
        if officers.get("general_counsel"):
            result["general_counsel_name"] = officers["general_counsel"]["name"]
            result["general_counsel_title"] = officers["general_counsel"].get("title", "")
        if officers.get("board_chair"):
            result["board_chair_name"] = officers["board_chair"]["name"]
            result["board_chair_title"] = officers["board_chair"].get("title", "")
        if officers.get("ceo"):
            result["ceo_name"] = officers["ceo"]["name"]
        if officers.get("cfo"):
            result["cfo_name"] = officers["cfo"]["name"]
        result["board_members_json"] = json.dumps(officers.get("directors", []))
        log.info(
            "  Officers: sec=%s, gc=%s, chair=%s, directors=%d",
            result["secretary_name"],
            result["general_counsel_name"],
            result["board_chair_name"],
            len(officers.get("directors", [])),
        )
    except Exception as e:
        log.error("  Board extraction failed for %s: %s", ticker, e)
        officers = {"directors": []}

    # Steps 5-7 only for high-scoring companies
    if result["score"] >= 5:
        # Step 5: Generate memo
        try:
            key_excerpts = json.loads(result["key_excerpts_json"])
            stats = json.loads(result["stats_json"])
            result["memo_text"] = generate_memo(
                company_name=company_name,
                form_type=form_type,
                filing_date=filing_date,
                score=result["score"],
                rationale=result["rationale"],
                stats=stats,
                key_excerpts=key_excerpts,
            )
            log.info("  Memo generated: %d chars", len(result["memo_text"]))
        except Exception as e:
            log.error("  Memo generation failed for %s: %s", ticker, e)

        # Step 6: Generate letter
        try:
            key_excerpts = json.loads(result["key_excerpts_json"])
            result["letter_text"] = generate_letter(
                company_name=company_name,
                date=analysis_date,
                officers=officers,
                score=result["score"],
                rationale=result["rationale"],
                key_excerpts=key_excerpts,
            )
            log.info("  Letter generated: %d chars", len(result["letter_text"]))
        except Exception as e:
            log.error("  Letter generation failed for %s: %s", ticker, e)

        # Step 7: Apollo email enrichment
        try:
            officers = enrich_contacts(company_name, ticker, officers)
            result["apollo_enriched"] = True
            # Update board_members_json with enriched data
            result["board_members_json"] = json.dumps(officers.get("directors", []))
            log.info("  Apollo enrichment complete")
        except Exception as e:
            log.error("  Apollo enrichment failed for %s: %s", ticker, e)

    return result


def _store_results(results: list[dict], analysis_date: str):
    """Store results to BigQuery sec_leads_results table."""
    client = bigquery.Client(project=GCP_PROJECT)
    table_id = f"{GCP_PROJECT}.{BQ_DATASET}.sec_leads_results"

    # Delete existing rows for this date (idempotent re-runs)
    delete_sql = f"""
    DELETE FROM `{table_id}`
    WHERE analysis_date = @analysis_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("analysis_date", "DATE", analysis_date)
        ]
    )
    try:
        client.query(delete_sql, job_config=job_config, location=BQ_LOCATION).result()
        log.info("Cleared existing results for %s", analysis_date)
    except Exception as e:
        log.warning("Delete failed (table may not exist yet): %s", e)

    # Insert new rows
    rows_to_insert = []
    for r in results:
        row = dict(r)
        # Convert None to appropriate defaults for BQ
        row.setdefault("apollo_enriched", False)
        rows_to_insert.append(row)

    if not rows_to_insert:
        log.info("No results to store")
        return

    errors = client.insert_rows_json(table_id, rows_to_insert)
    if errors:
        log.error("BigQuery insert errors: %s", errors[:5])
    else:
        log.info("Stored %d results to BigQuery", len(rows_to_insert))


def run_date_analysis(filing_date: str) -> dict:
    """Analyze all 10-K filers for a single date.

    Returns stats dict: {analyzed, scored_5plus, errors, gcs_path}
    """
    log.info("=" * 70)
    log.info("SEC LEADS ANALYSIS for filing date: %s", filing_date)
    log.info("=" * 70)

    # Step 1: Discover filers
    try:
        filers = discover_10k_filers(filing_date)
        log.info("Found %d 10-K filers for %s", len(filers), filing_date)
    except Exception as e:
        log.error("EDGAR discovery failed for %s: %s", filing_date, e)
        return {"analyzed": 0, "scored_5plus": 0, "errors": 1, "gcs_path": None}

    if not filers:
        log.info("No 10-K filings found for %s", filing_date)
        return {"analyzed": 0, "scored_5plus": 0, "errors": 0, "gcs_path": None}

    # Step 2: Process each company sequentially
    results = []
    errors = 0
    for i, filer in enumerate(filers, 1):
        ticker = filer.get("ticker", "???")
        log.info("\n--- Company %d/%d: %s ---", i, len(filers), ticker)

        try:
            result = analyze_company(ticker, filer, filing_date)
            if result:
                results.append(result)
        except Exception as e:
            log.error("UNHANDLED ERROR analyzing %s: %s", ticker, e, exc_info=True)
            errors += 1

    log.info("\nAll companies processed: %d results, %d errors", len(results), errors)

    # Step 3: Store to BigQuery
    try:
        _store_results(results, filing_date)
    except Exception as e:
        log.error("Failed to store results to BigQuery: %s", e)

    # Step 4: Generate and upload HTML report
    gcs_path = None
    try:
        html = generate_report(results, filing_date, len(filers))
        gcs_path = upload_report(html, filing_date)
        log.info("Report uploaded: %s", gcs_path)
    except Exception as e:
        log.error("Report generation/upload failed: %s", e)

    scored_5plus = sum(1 for r in results if r.get("score", 0) >= 5)

    stats = {
        "analyzed": len(results),
        "scored_5plus": scored_5plus,
        "errors": errors,
        "gcs_path": gcs_path,
    }
    log.info("Run complete: %s", stats)
    return stats


def main():
    parser = argparse.ArgumentParser(description="SEC Leads patent importance pipeline")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Analysis date YYYY-MM-DD (default: previous business day)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=0,
        help="Process N previous days (for backfill). Processes sequentially.",
    )
    args = parser.parse_args()

    if args.lookback > 0:
        # Backfill mode: process each day going back
        log.info("BACKFILL MODE: processing %d days", args.lookback)
        today = date.today()
        total_stats = {"analyzed": 0, "scored_5plus": 0, "errors": 0, "days": 0}

        for days_back in range(1, args.lookback + 1):
            target_date = today - timedelta(days=days_back)
            # Skip weekends (no SEC filings)
            if target_date.weekday() >= 5:
                continue

            date_str = target_date.strftime("%Y-%m-%d")
            log.info("\n" + "=" * 70)
            log.info("BACKFILL DAY %d: %s", days_back, date_str)
            log.info("=" * 70)

            try:
                stats = run_date_analysis(date_str)
                total_stats["analyzed"] += stats.get("analyzed", 0)
                total_stats["scored_5plus"] += stats.get("scored_5plus", 0)
                total_stats["errors"] += stats.get("errors", 0)
                total_stats["days"] += 1
            except Exception as e:
                log.error("BACKFILL FAILED for %s: %s", date_str, e, exc_info=True)
                total_stats["errors"] += 1

        log.info("\nBACKFILL COMPLETE: %s", total_stats)

    else:
        # Single day mode
        if args.date:
            filing_date = args.date
        else:
            filing_date = _previous_business_day().strftime("%Y-%m-%d")

        stats = run_date_analysis(filing_date)
        if stats["errors"] > 0:
            log.warning("Completed with %d errors", stats["errors"])


if __name__ == "__main__":
    main()
