#!/usr/bin/env python3
"""Orchestrate the full invoice extraction pipeline for an entity.

This is a Cloud Run Job entrypoint that manages the entire pipeline:
  Phase 1: Download payment receipt PDFs from USPTO to GCS (parallel threads)
  Phase 2: Extract fee line items from PDFs using Gemini Vision (rate-limited)

Only downloads N417.PYMT (electronic payment receipts) and IFEE (issue fee)
documents. Fee codes are self-describing: first digit encodes entity size
(1=LARGE, 2=SMALL, 3=MICRO, 4=SMALL electronic).

All state is tracked in BigQuery for resumability. If the job times out
or fails, re-running it picks up where it left off.

Environment variables:
  ENTITY_NAME — entity to process (required)
  PARALLEL_DOWNLOADS — number of parallel download threads (default 5)
  MAX_APPS — limit for testing (default: all)
  FILING_YEARS — number of years of filings to cover (default 10)
  GCP_PROJECT_ID — GCP project (default: uspto-data-app)
  BIGQUERY_DATASET — BQ dataset (default: uspto_data)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import bigquery, storage

from utils.invoice_extraction import (
    download_pdf_bytes,
    extract_with_gemini,
    find_payment_docs,
    get_downloaded_apps,
    save_extraction,
    update_pipeline_status,
    upload_pdf_to_gcs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

ENTITY_NAME = os.environ.get("ENTITY_NAME", "")
PARALLEL_DOWNLOADS = int(os.environ.get("PARALLEL_DOWNLOADS", "5"))
MAX_APPS = int(os.environ.get("MAX_APPS", "0"))  # 0 = unlimited
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "uspto-data-app")
BQ_DATASET = os.environ.get("BIGQUERY_DATASET", "uspto_data")


def get_entity_app_numbers(bq_client: bigquery.Client) -> list[str]:
    """Get all application numbers for an entity (same portfolio query as entity_status.py)."""
    # Default: last 10 years of filings. Set FILING_YEARS env var to override.
    filing_years = int(os.environ.get("FILING_YEARS", "10"))

    query = f"""
    WITH portfolio AS (
        -- Source 1: pfw_applicants
        SELECT DISTINCT application_number
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.pfw_applicants`
        WHERE UPPER(applicant_name) LIKE CONCAT('%', UPPER(@entity), '%')

        UNION DISTINCT

        -- Source 2: pfw_inventors (for individual inventors)
        SELECT DISTINCT application_number
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.pfw_inventors`
        WHERE UPPER(CONCAT(last_name, ', ', first_name)) LIKE CONCAT('%', UPPER(@entity), '%')

        UNION DISTINCT

        -- Source 3: pat_assign_assignees (acquired via assignment)
        SELECT DISTINCT d.application_number
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.pat_assign_assignees` a
        JOIN `{GCP_PROJECT_ID}.{BQ_DATASET}.pat_assign_documents` d
          ON a.reel_frame = d.reel_frame
        WHERE UPPER(a.assignee_name) LIKE CONCAT('%', UPPER(@entity), '%')
    )
    SELECT p.application_number, pfw.filing_date
    FROM portfolio p
    JOIN `{GCP_PROJECT_ID}.{BQ_DATASET}.patent_file_wrapper_v2` pfw
      ON p.application_number = pfw.application_number
    -- Utility apps only (numeric). PCT/design/reissue don't work with USPTO Documents API.
    WHERE REGEXP_CONTAINS(p.application_number, r'^\\d+$')
      AND pfw.filing_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {filing_years} YEAR)
    -- Most recently filed first (most actionable for monetization)
    ORDER BY pfw.filing_date DESC
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("entity", "STRING", ENTITY_NAME)
        ]
    )
    rows = list(bq_client.query(query, job_config=job_config).result())
    return [r.application_number for r in rows]


def download_app_docs(
    bq_client: bigquery.Client,
    gcs_client: storage.Client,
    app_number: str,
) -> dict:
    """Download all payment documents for one application.

    Returns: {app_number, docs_found, docs_downloaded, errors}
    """
    result = {"app_number": app_number, "docs_found": 0, "docs_downloaded": 0, "errors": []}

    try:
        # Find payment docs via USPTO API
        docs = find_payment_docs(app_number)
        result["docs_found"] = len(docs)

        if not docs:
            # No payment receipts found — save a marker row so this app
            # is marked as "checked" and won't be re-queried on resume.
            save_extraction(
                bq_client, app_number,
                doc_meta={"doc_code": "NONE", "description": "No payment receipts found"},
                extraction=None,
                gcs_path="",
                extraction_status="no_docs",
            )

        for doc_meta in docs:
            try:
                # Download PDF bytes
                pdf_bytes = download_pdf_bytes(doc_meta["download_url"])
                if pdf_bytes is None:
                    result["errors"].append(f"Download failed: {doc_meta.get('doc_code')}")
                    continue

                # Upload to GCS
                gcs_path = upload_pdf_to_gcs(gcs_client, app_number, doc_meta, pdf_bytes)

                # Save download record to BigQuery (extraction_status='downloaded')
                save_extraction(
                    bq_client, app_number, doc_meta,
                    extraction=None,
                    gcs_path=gcs_path,
                    extraction_status="downloaded",
                )

                result["docs_downloaded"] += 1

            except Exception as e:
                result["errors"].append(f"{doc_meta.get('doc_code')}: {str(e)[:100]}")

        # Rate limit: 1 second between apps
        time.sleep(1)

    except Exception as e:
        result["errors"].append(str(e)[:200])

    return result


def extract_single_doc(
    bq_client: bigquery.Client,
    gcs_client: storage.Client,
    row: dict,
) -> dict:
    """Extract fee data from a single downloaded PDF using Gemini Vision.

    USPTO payment PDFs are scanned TIFF images — Gemini Vision is the
    sole extraction method. Returns: {gcs_path, success, method}
    """
    gcs_path = row["gcs_path"]
    app_number = row["application_number"]

    try:
        # Download PDF from GCS
        bucket = gcs_client.bucket("uspto-bulk-staging")
        blob = bucket.blob(gcs_path)
        pdf_bytes = blob.download_as_bytes()

        # Extract with Gemini Vision
        extraction = extract_with_gemini(pdf_bytes)
        if extraction:
            _update_extraction_status(
                bq_client, app_number, gcs_path, extraction, "extracted", row
            )
            return {"gcs_path": gcs_path, "success": True, "method": "gemini"}

        # Gemini failed
        _update_extraction_status(
            bq_client, app_number, gcs_path, None, "failed", row
        )
        return {"gcs_path": gcs_path, "success": False, "method": "failed"}

    except Exception as e:
        logger.warning("Extraction error for %s: %s", gcs_path, e)
        return {"gcs_path": gcs_path, "success": False, "method": "error"}


def _update_extraction_status(
    bq_client: bigquery.Client,
    app_number: str,
    gcs_path: str,
    extraction: dict | None,
    status: str,
    row_meta: dict | None = None,
):
    """Update or insert extraction data for a document.

    Tries UPDATE first. If the row is in the BQ streaming buffer (blocks
    UPDATE for ~30 min after streaming insert), falls back to INSERT of a
    new row. The extraction-data endpoint picks the latest row per doc.
    """
    now = datetime.now(timezone.utc).isoformat()

    if extraction:
        fees = extraction.get("fees", [])
        fees_json = json.dumps(fees) if isinstance(fees, list) else "[]"

        update_query = """
        UPDATE `uspto-data-app.uspto_data.invoice_extractions`
        SET extraction_status = @status,
            entity_status = @entity_status,
            fees_json = @fees_json,
            total_amount = @total_amount,
            extraction_method = @method,
            extraction_model = @model,
            extracted_at = @now,
            raw_response = @raw_response
        WHERE application_number = @app AND gcs_path = @gcs_path
        """
        update_params = [
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("entity_status", "STRING", extraction.get("entity_status")),
            bigquery.ScalarQueryParameter("fees_json", "STRING", fees_json),
            bigquery.ScalarQueryParameter("total_amount", "FLOAT64", extraction.get("total_amount")),
            bigquery.ScalarQueryParameter("method", "STRING", extraction.get("extraction_method", "")),
            bigquery.ScalarQueryParameter("model", "STRING", extraction.get("extraction_model", "")),
            bigquery.ScalarQueryParameter("now", "STRING", now),
            bigquery.ScalarQueryParameter("raw_response", "STRING", extraction.get("raw_response", "")),
            bigquery.ScalarQueryParameter("app", "STRING", app_number),
            bigquery.ScalarQueryParameter("gcs_path", "STRING", gcs_path),
        ]
    else:
        update_query = """
        UPDATE `uspto-data-app.uspto_data.invoice_extractions`
        SET extraction_status = @status, extracted_at = @now
        WHERE application_number = @app AND gcs_path = @gcs_path
        """
        update_params = [
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("now", "STRING", now),
            bigquery.ScalarQueryParameter("app", "STRING", app_number),
            bigquery.ScalarQueryParameter("gcs_path", "STRING", gcs_path),
        ]

    try:
        job_config = bigquery.QueryJobConfig(query_parameters=update_params)
        bq_client.query(update_query, job_config=job_config).result()
    except Exception as e:
        if "streaming buffer" in str(e).lower():
            # Row is in streaming buffer — fall back to INSERT a new row
            logger.info("Streaming buffer for %s, inserting new row instead", gcs_path)
            _insert_extraction_row(bq_client, app_number, gcs_path, extraction, status, now, row_meta)
        else:
            raise


def _insert_extraction_row(
    bq_client: bigquery.Client,
    app_number: str,
    gcs_path: str,
    extraction: dict | None,
    status: str,
    now: str,
    row_meta: dict | None = None,
):
    """Insert a new extraction row (fallback when UPDATE fails on streaming buffer).

    Carries forward doc_code and mail_date from the original download row
    so the extraction record has complete metadata.
    """
    fees = []
    if extraction:
        fees = extraction.get("fees", [])
    fees_json = json.dumps(fees) if isinstance(fees, list) else "[]"

    doc_code = (row_meta or {}).get("doc_code", "") or ""
    mail_date = (row_meta or {}).get("mail_date") or None

    query = """
    INSERT INTO `uspto-data-app.uspto_data.invoice_extractions`
      (application_number, gcs_path, doc_code, mail_date, extraction_status,
       entity_status, fees_json, total_amount, extraction_method,
       extraction_model, extracted_at, raw_response)
    VALUES
      (@app, @gcs_path, @doc_code, @mail_date, @status,
       @entity_status, @fees_json, @total_amount, @method,
       @model, @now, @raw_response)
    """
    params = [
        bigquery.ScalarQueryParameter("app", "STRING", app_number),
        bigquery.ScalarQueryParameter("gcs_path", "STRING", gcs_path),
        bigquery.ScalarQueryParameter("doc_code", "STRING", doc_code),
        bigquery.ScalarQueryParameter("mail_date", "STRING", mail_date),
        bigquery.ScalarQueryParameter("status", "STRING", status),
        bigquery.ScalarQueryParameter("entity_status", "STRING",
                                      extraction.get("entity_status") if extraction else None),
        bigquery.ScalarQueryParameter("fees_json", "STRING", fees_json),
        bigquery.ScalarQueryParameter("total_amount", "FLOAT64",
                                      extraction.get("total_amount") if extraction else None),
        bigquery.ScalarQueryParameter("method", "STRING",
                                      extraction.get("extraction_method", "") if extraction else ""),
        bigquery.ScalarQueryParameter("model", "STRING",
                                      extraction.get("extraction_model", "") if extraction else ""),
        bigquery.ScalarQueryParameter("now", "STRING", now),
        bigquery.ScalarQueryParameter("raw_response", "STRING",
                                      extraction.get("raw_response", "") if extraction else ""),
    ]
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    bq_client.query(query, job_config=job_config).result()


# ── Main orchestration ───────────────────────────────────────────

def main():
    if not ENTITY_NAME:
        logger.error("ENTITY_NAME environment variable is required")
        sys.exit(1)

    filing_years = int(os.environ.get("FILING_YEARS", "10"))
    logger.info("=" * 60)
    logger.info("Invoice Pipeline Orchestrator")
    logger.info("Entity: %s", ENTITY_NAME)
    logger.info("Filing window: last %d years", filing_years)
    logger.info("Parallel downloads: %d", PARALLEL_DOWNLOADS)
    logger.info("Max apps: %s", MAX_APPS or "unlimited")
    logger.info("=" * 60)

    bq_client = bigquery.Client(project=GCP_PROJECT_ID, location="us-west1")
    gcs_client = storage.Client(project=GCP_PROJECT_ID)

    # ── Phase 1: DOWNLOAD ─────────────────────────────────────────

    logger.info("Phase 1: Getting entity portfolio...")
    all_apps = get_entity_app_numbers(bq_client)
    total_apps = len(all_apps)
    logger.info("Found %d applications for %s", total_apps, ENTITY_NAME)

    if MAX_APPS > 0:
        all_apps = all_apps[:MAX_APPS]
        logger.info("Limited to %d apps for testing", MAX_APPS)

    # Check which apps already have downloads
    already_downloaded = get_downloaded_apps(bq_client, all_apps)
    remaining = [a for a in all_apps if a not in already_downloaded]
    logger.info("Already downloaded: %d, Remaining: %d", len(already_downloaded), len(remaining))

    update_pipeline_status(
        bq_client, ENTITY_NAME, "downloading",
        total_apps=len(all_apps),
        downloaded_apps=len(already_downloaded),
    )

    if remaining:
        logger.info("Starting downloads with %d parallel workers...", PARALLEL_DOWNLOADS)
        total_docs_downloaded = 0
        download_errors = []

        with ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as executor:
            futures = {
                executor.submit(download_app_docs, bq_client, gcs_client, app): app
                for app in remaining
            }

            completed = 0
            for future in as_completed(futures):
                completed += 1
                result = future.result()
                total_docs_downloaded += result["docs_downloaded"]

                if result["errors"]:
                    download_errors.extend(result["errors"])

                if completed % 50 == 0 or completed == len(remaining):
                    logger.info(
                        "Download progress: %d/%d apps, %d docs downloaded, %d errors",
                        completed, len(remaining), total_docs_downloaded, len(download_errors),
                    )
                    update_pipeline_status(
                        bq_client, ENTITY_NAME, "downloading",
                        total_apps=len(all_apps),
                        downloaded_apps=len(already_downloaded) + completed,
                        downloaded_docs=total_docs_downloaded,
                    )

        logger.info("Download phase complete: %d docs, %d errors", total_docs_downloaded, len(download_errors))

    # ── Phase 2: EXTRACT (Gemini Vision) ────────────────────────────

    logger.info("Phase 2: Extracting fee data from downloaded PDFs via Gemini Vision...")
    update_pipeline_status(
        bq_client, ENTITY_NAME, "extracting",
        total_apps=len(all_apps),
        downloaded_apps=len(all_apps),
    )

    # Get all downloaded-but-not-extracted docs
    query = """
    SELECT application_number, gcs_path, doc_code, mail_date
    FROM `uspto-data-app.uspto_data.invoice_extractions`
    WHERE extraction_status = 'downloaded'
      AND application_number IN UNNEST(@apps)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("apps", "STRING", all_apps)]
    )
    unextracted = [dict(r) for r in bq_client.query(query, job_config=job_config).result()]
    logger.info("Found %d unextracted documents", len(unextracted))

    if unextracted:
        extracted_count = 0
        failed_count = 0

        # Gemini Flash-Lite via Vertex AI — paid usage, no hard daily cap.
        # 2-second delay between calls to be conservative with rate limits.
        GEMINI_DELAY_SECONDS = 2

        for i, row in enumerate(unextracted):
            result = extract_single_doc(bq_client, gcs_client, row)
            if result["success"]:
                extracted_count += 1
            else:
                failed_count += 1

            time.sleep(GEMINI_DELAY_SECONDS)  # rate limit Gemini calls

            total_done = extracted_count + failed_count
            if total_done % 50 == 0 or total_done == len(unextracted):
                logger.info(
                    "Extraction progress: %d/%d done (%d extracted, %d failed)",
                    total_done, len(unextracted), extracted_count, failed_count,
                )
                update_pipeline_status(
                    bq_client, ENTITY_NAME, "extracting",
                    total_apps=len(all_apps),
                    downloaded_apps=len(all_apps),
                    extracted_docs=extracted_count,
                    failed_docs=failed_count,
                )

        logger.info("Extraction complete: %d extracted, %d failed",
                    extracted_count, failed_count)

    # ── Final summary ─────────────────────────────────────────────

    # Get final counts from BQ
    summary_query = """
    SELECT
      COUNT(*) as total_docs,
      COUNTIF(extraction_status = 'extracted') as extracted,
      COUNTIF(extraction_status = 'failed') as failed,
      COUNTIF(extraction_status = 'downloaded') as still_downloaded,
      COUNTIF(extraction_status = 'no_docs') as no_docs,
      ROUND(SUM(CASE WHEN extraction_status = 'extracted' THEN total_amount ELSE 0 END), 2) as total_dollars
    FROM `uspto-data-app.uspto_data.invoice_extractions`
    WHERE application_number IN UNNEST(@apps)
    """
    summary = list(bq_client.query(summary_query, job_config=job_config).result())[0]

    update_pipeline_status(
        bq_client, ENTITY_NAME, "complete",
        total_apps=len(all_apps),
        downloaded_apps=len(all_apps),
        downloaded_docs=summary.total_docs,
        extracted_docs=summary.extracted,
        failed_docs=summary.failed,
        completed=True,
    )

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info("Entity: %s", ENTITY_NAME)
    logger.info("Applications: %d", len(all_apps))
    logger.info("Documents: %d total, %d extracted, %d failed, %d apps with no payment receipts",
                summary.total_docs, summary.extracted, summary.failed, summary.no_docs)
    logger.info("Total dollars extracted: $%s", f"{summary.total_dollars:,.2f}" if summary.total_dollars else "0")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
