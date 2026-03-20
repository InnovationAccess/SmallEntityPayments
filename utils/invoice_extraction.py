"""Invoice extraction utility — download payment PDFs, extract structured data.

Strategy: Download only actual payment receipts (N417.PYMT, IFEE) from USPTO,
extract fee line items via Gemini Vision. Fee codes are self-describing:
first digit encodes entity size (1=LARGE, 2=SMALL, 3=MICRO, 4=SMALL electronic).

This module is used by:
  - scripts/orchestrate_invoice_pipeline.py (batch extraction)
  - api/routers/prosecution.py (on-demand viewing)
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

USPTO_API_KEY = "inbsszqfwwnkjfebpibunnbllbygqz"
USPTO_DOC_API = "https://api.uspto.gov/api/v1/patent/applications/{}/documents"
GCS_BUCKET = "uspto-bulk-staging"
GCS_INVOICE_PREFIX = "prosecution-invoices"

# Document codes for actual payment receipts only.
# WFEE (fee worksheet) is an assessment, not a payment — excluded to avoid double-counting.
# Fee codes in these receipts encode entity size via first digit (1=LARGE, 2=SMALL, 3=MICRO).
PAYMENT_DOC_CODES = {
    "N417.PYMT",    # Electronic Fee Payment receipt
    "IFEE",         # Issue Fee Payment (PTO-85B)
}

# Keywords matched against documentCodeDescriptionText (case-insensitive)
# Only match actual payment receipts and issue fee forms.
PAYMENT_KEYWORDS = [
    "PAYMENT RECEIPT", "FEE PAYMENT", "ELECTRONIC PAYMENT",
    "ISSUE FEE", "85B",
]

# Gemini Vision configuration
GEMINI_PROJECT = "uspto-data-app"
GEMINI_LOCATION = "us-central1"
GEMINI_MODEL = "gemini-2.5-flash-lite"  # cheapest vision model (~$0.10/1M input)

GEMINI_PROMPT = """Extract fee line items from this USPTO payment receipt as JSON.

Return:
{
  "fees": [
    {"fee_code": "2501", "description": "UTILITY APPL ISSUE FEE", "amount": 480.00, "quantity": 1}
  ],
  "total_amount": 480.00
}

Rules:
- fee_code: the 4-digit numeric fee code (e.g. "2501", "1311"). null if not shown.
- description: fee description text
- amount: dollar amount per unit (number, no $ or commas)
- quantity: how many (default 1)
- total_amount: total payment on the receipt

Return ONLY valid JSON. No markdown, no code fences."""


# ── USPTO API functions ──────────────────────────────────────────

def _is_payment_doc(doc: dict) -> bool:
    """Check if a USPTO document is payment-related by code or description."""
    code = (doc.get("documentCode") or "").upper()
    desc = (doc.get("documentCodeDescriptionText") or "").upper()
    return code in PAYMENT_DOC_CODES or any(kw in desc for kw in PAYMENT_KEYWORDS)


def find_payment_docs(app_number: str, timeout: int = 30) -> List[dict]:
    """Call USPTO Documents API, filter to payment docs, return metadata list.

    Each item has: doc_code, description, mail_date, download_url, page_count, doc_id.
    Retries on 429 with exponential backoff.
    """
    url = USPTO_DOC_API.format(app_number)
    delays = [5, 10, 20, 40]

    for attempt in range(len(delays) + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, headers={
                    "X-API-KEY": USPTO_API_KEY,
                    "Accept": "application/json",
                })

            if resp.status_code == 429:
                if attempt < len(delays):
                    logger.warning("USPTO 429 for %s, retrying in %ds", app_number, delays[attempt])
                    time.sleep(delays[attempt])
                    continue
                else:
                    logger.error("USPTO 429 for %s after %d retries", app_number, len(delays))
                    return []

            if resp.status_code != 200:
                logger.warning("USPTO API %d for %s", resp.status_code, app_number)
                return []

            break
        except Exception as e:
            if attempt < len(delays):
                logger.warning("USPTO API error for %s: %s, retrying", app_number, e)
                time.sleep(delays[attempt])
                continue
            logger.error("USPTO API failed for %s after retries: %s", app_number, e)
            return []

    data = resp.json()
    docs = []
    for doc in data.get("documentBag", []):
        if not _is_payment_doc(doc):
            continue

        downloads = doc.get("downloadOptionBag", [])
        dl_url = downloads[0].get("downloadUrl") if downloads else None
        if not dl_url:
            continue

        docs.append({
            "doc_code": doc.get("documentCode"),
            "description": doc.get("documentCodeDescriptionText"),
            "mail_date": doc.get("officialDate"),
            "download_url": dl_url,
            "page_count": doc.get("pageCount"),
            "doc_id": doc.get("documentIdentifier"),
        })

    return docs


def download_pdf_bytes(download_url: str, timeout: int = 60) -> Optional[bytes]:
    """Download a PDF from USPTO. Returns bytes or None on failure."""
    delays = [5, 10, 20]
    for attempt in range(len(delays) + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                resp = client.get(download_url, headers={
                    "X-API-KEY": USPTO_API_KEY,
                    "Accept": "application/pdf",
                })

            if resp.status_code == 429:
                if attempt < len(delays):
                    time.sleep(delays[attempt])
                    continue
                return None

            if resp.status_code != 200:
                logger.warning("PDF download failed: HTTP %d", resp.status_code)
                return None

            return resp.content

        except Exception as e:
            if attempt < len(delays):
                time.sleep(delays[attempt])
                continue
            logger.error("PDF download error: %s", e)
            return None
    return None


# ── GCS functions ────────────────────────────────────────────────

def upload_pdf_to_gcs(gcs_client, app_number: str, doc_meta: dict, pdf_bytes: bytes) -> str:
    """Upload PDF to GCS and return the gcs_path.

    Path format: prosecution-invoices/{app_number}/{app}_{docCode}_{mailDate}.pdf
    Uses mail_date (not doc_id) for stable filenames across API calls.
    """
    doc_code = doc_meta.get("doc_code", "DOC")
    mail_date = (doc_meta.get("mail_date") or "unknown").replace("/", "-")
    filename = f"{app_number}_{doc_code}_{mail_date}.pdf"
    gcs_path = f"{GCS_INVOICE_PREFIX}/{app_number}/{filename}"

    bucket = gcs_client.bucket(GCS_BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(pdf_bytes, content_type="application/pdf")

    return gcs_path


# ── Extraction: Gemini Vision ────────────────────────────────────
# USPTO payment PDFs are scanned TIFF images — no text layer for
# pdfplumber/PyMuPDF.  Gemini Vision is the sole extraction method.

def _get_gcp_access_token() -> str:
    """Get access token using default credentials (works on Cloud Run + local gcloud)."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def extract_with_gemini(pdf_bytes: bytes) -> Optional[dict]:
    """Extract fee line items from a payment PDF using Gemini Vision.

    Primary extraction method — USPTO payment PDFs are scanned images
    with no text layer. Returns dict with fees + total_amount, or None.
    """
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        token = _get_gcp_access_token()
    except Exception as e:
        logger.error("GCP auth failed: %s", e)
        return None

    vertex_url = (
        f"https://{GEMINI_LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{GEMINI_PROJECT}/locations/{GEMINI_LOCATION}/"
        f"publishers/google/models/{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": GEMINI_PROMPT},
                {"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}},
            ],
        }],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            "mediaResolution": "MEDIA_RESOLUTION_MEDIUM",  # 258 tokens/page vs 1,806 default
        },
    }

    try:
        with httpx.Client(timeout=90) as client:
            resp = client.post(
                vertex_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            )
    except Exception as e:
        logger.error("Gemini API call failed: %s", e)
        return None

    if resp.status_code != 200:
        logger.error("Gemini API error %d: %s", resp.status_code, resp.text[:300])
        return None

    resp_json = resp.json()
    candidates = resp_json.get("candidates", [])
    if not candidates:
        return None

    text = candidates[0]["content"]["parts"][0]["text"].strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                result = json.loads(m.group())
            except json.JSONDecodeError:
                return None
        else:
            return None

    # Sanitize numeric fields — Gemini may return formatted strings like "3,200.00"
    result["total_amount"] = _to_float(result.get("total_amount"))
    for fee in result.get("fees", []):
        if isinstance(fee, dict):
            fee["amount"] = _to_float(fee.get("amount"))
            fee["quantity"] = _to_float(fee.get("quantity")) or 1

    result["raw_response"] = text
    result["extraction_method"] = "gemini_vision"
    result["extraction_model"] = GEMINI_MODEL
    return result


def _to_float(val) -> Optional[float]:
    """Safely convert a value to float, handling commas, dollar signs, etc."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace(",", "").replace("$", "").replace(" ", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


# ── BigQuery functions ───────────────────────────────────────────

def save_extraction(
    bq_client,
    app_number: str,
    doc_meta: dict,
    extraction: Optional[dict],
    gcs_path: str,
    extraction_status: str = "extracted",
):
    """Save a download/extraction record to invoice_extractions via DML INSERT.

    Uses DML INSERT (not streaming insert_rows_json) so rows go directly
    to managed storage. This avoids the 30-minute streaming buffer that
    blocks subsequent UPDATE/DELETE operations.
    """
    fees = []
    if extraction:
        fees = extraction.get("fees", [])
    fees_json = json.dumps(fees) if isinstance(fees, list) else "[]"

    now = datetime.now(timezone.utc).isoformat()

    query = """
    INSERT INTO `uspto-data-app.uspto_data.invoice_extractions`
      (application_number, gcs_path, doc_code, doc_description, doc_description_text,
       mail_date, page_count, extraction_status, entity_status, fees_json,
       total_amount, extraction_method, extraction_model, extracted_at, raw_response)
    VALUES
      (@app, @gcs_path, @doc_code, @doc_desc, @doc_desc_text,
       @mail_date, @page_count, @status, @entity_status, @fees_json,
       @total_amount, @method, @model, @now, @raw_response)
    """
    from google.cloud import bigquery as bq
    params = [
        bq.ScalarQueryParameter("app", "STRING", app_number),
        bq.ScalarQueryParameter("gcs_path", "STRING", gcs_path),
        bq.ScalarQueryParameter("doc_code", "STRING", doc_meta.get("doc_code", "")),
        bq.ScalarQueryParameter("doc_desc", "STRING", doc_meta.get("description", "")),
        bq.ScalarQueryParameter("doc_desc_text", "STRING", doc_meta.get("description", "")),
        bq.ScalarQueryParameter("mail_date", "STRING", doc_meta.get("mail_date")),
        bq.ScalarQueryParameter("page_count", "INT64", doc_meta.get("page_count")),
        bq.ScalarQueryParameter("status", "STRING", extraction_status),
        bq.ScalarQueryParameter("entity_status", "STRING",
                                extraction.get("entity_status") if extraction else None),
        bq.ScalarQueryParameter("fees_json", "STRING", fees_json),
        bq.ScalarQueryParameter("total_amount", "FLOAT64",
                                extraction.get("total_amount") if extraction else None),
        bq.ScalarQueryParameter("method", "STRING",
                                extraction.get("extraction_method", "") if extraction else ""),
        bq.ScalarQueryParameter("model", "STRING",
                                extraction.get("extraction_model", "") if extraction else ""),
        bq.ScalarQueryParameter("now", "STRING", now),
        bq.ScalarQueryParameter("raw_response", "STRING",
                                extraction.get("raw_response", "") if extraction else ""),
    ]
    job_config = bq.QueryJobConfig(query_parameters=params)
    try:
        bq_client.query(query, job_config=job_config).result()
        return True
    except Exception as e:
        logger.warning("BQ DML insert error for %s/%s: %s", app_number, gcs_path, e)
        return False


def get_downloaded_apps(bq_client, app_numbers: List[str]) -> Set[str]:
    """Check which apps already have at least one download in invoice_extractions."""
    if not app_numbers:
        return set()

    # Use parameterized query for safety
    query = """
    SELECT DISTINCT application_number
    FROM `uspto-data-app.uspto_data.invoice_extractions`
    WHERE application_number IN UNNEST(@apps)
    """
    from google.cloud import bigquery as bq
    job_config = bq.QueryJobConfig(
        query_parameters=[bq.ArrayQueryParameter("apps", "STRING", app_numbers)]
    )
    rows = list(bq_client.query(query, job_config=job_config).result())
    return {r.application_number for r in rows}


def update_pipeline_status(
    bq_client,
    entity_name: str,
    phase: str,
    total_apps: int = 0,
    downloaded_apps: int = 0,
    downloaded_docs: int = 0,
    extracted_docs: int = 0,
    failed_docs: int = 0,
    gemini_recovered: int = 0,
    errors_json: str = "[]",
    completed: bool = False,
):
    """Append a pipeline status row for real-time monitoring.

    Append-only design: BigQuery streaming buffer blocks DELETE/UPDATE on
    recently-inserted rows. The status endpoint queries ORDER BY updated_at
    DESC LIMIT 1, so the latest row always wins.
    """
    now = datetime.now(timezone.utc).isoformat()

    row = {
        "entity_name": entity_name,
        "phase": phase,
        "total_apps": total_apps,
        "downloaded_apps": downloaded_apps,
        "downloaded_docs": downloaded_docs,
        "extracted_docs": extracted_docs,
        "failed_docs": failed_docs,
        "gemini_recovered": gemini_recovered,
        "errors_json": errors_json,
        "started_at": now if phase == "downloading" and downloaded_apps == 0 else None,
        "updated_at": now,
        "completed_at": now if completed else None,
    }

    table_ref = bq_client.dataset("uspto_data").table("invoice_pipeline_status")
    errs = bq_client.insert_rows_json(table_ref, [row])
    if errs:
        logger.warning("Pipeline status update error: %s", errs)
