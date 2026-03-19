"""Invoice extraction utility — download payment PDFs, extract structured data.

Two extraction methods:
  1. Primary: pdfplumber + PyMuPDF (free, fast)
  2. Fallback: Gemini Vision (for PDFs that fail primary extraction)

This module is used by:
  - scripts/orchestrate_invoice_pipeline.py (batch extraction)
  - api/routers/prosecution.py (on-demand viewing)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

USPTO_API_KEY = "inbsszqfwwnkjfebpibunnbllbygqz"
USPTO_DOC_API = "https://api.uspto.gov/api/v1/patent/applications/{}/documents"
GCS_BUCKET = "uspto-bulk-staging"
GCS_INVOICE_PREFIX = "prosecution-invoices"

# Document codes known to be payment-related (from IFW doc codes audit)
PAYMENT_DOC_CODES = {
    "WFEE",         # Fee Worksheet (SB06) — primary prosecution fee form
    "WFEE.APPEAL",  # Appeal Forwarding Fee transmittal
    "IFEE",         # Issue Fee Payment (PTO-85B)
    "N417.PYMT",    # Electronic Fee Payment
    "RXFEE",        # Reexam Fee Payment
}

# Keywords matched against documentCodeDescriptionText (case-insensitive)
PAYMENT_KEYWORDS = [
    # Proven in calibration (captured 4 doc types across 1,240 extractions)
    "FEE WORKSHEET", "SB06", "PAYMENT RECEIPT", "FEE PAYMENT",
    "FEE RECORDATION", "FEE CALCULATION", "FEE SHEET",
    # Added from IFW doc codes audit
    "FEE TRANSMITTAL", "ISSUE FEE", "85B", "SB/17",
    "RCE TRANSMITTAL", "SB/30", "PROCESSING FEE",
]

# Gemini Vision configuration
GEMINI_PROJECT = "uspto-data-app"
GEMINI_LOCATION = "us-central1"
GEMINI_MODEL = "gemini-2.5-flash-lite"  # cheapest vision model (~$0.10/1M input)

GEMINI_PROMPT = """You are analyzing a USPTO patent payment document (PDF image).

Extract ALL of the following information as structured JSON:

1. **doc_type**: One of:
   - "FEE_WORKSHEET_SB06" — PTO/SB/06 or PTO-875 Fee Determination Record
   - "ELECTRONIC_FEE_TRANSMITTAL" — Electronic Patent Application Fee Transmittal
   - "ISSUE_FEE_PTO85B" — PTO-85B / Part B Fee(s) Transmittal / Issue Fee Payment
   - "ELECTRONIC_PAYMENT_RECEIPT" — Electronic Payment Receipt
   - "UNKNOWN" — none of the above

2. **application_number**: The patent application number (digits only, no slashes/commas)

3. **filing_date**: Filing date if shown

4. **entity_status**: Look for:
   - Checked checkboxes next to LARGE, SMALL, or MICRO
   - Text like "Filed as Small Entity" or "ENTITY STATUS: SMALL"
   - Column headers: if fees are in "SMALL ENTITY" column, entity_status = "SMALL"
   - Return: "SMALL", "LARGE", "MICRO", or null

5. **title**: Title of invention if shown

6. **fees**: Array of fee line items. For each fee:
   - **fee_code**: Numeric fee code (e.g. "2820", "1833") — null if not shown
   - **description**: Fee description
   - **amount**: Dollar amount per item
   - **quantity**: How many (default 1)
   - **item_total**: Line total if shown

   For SB06 forms with fee rates (not codes), extract the fee type + amount.

7. **total_amount**: Total payment if shown

8. **assignee_name**: Name of assignee if shown

9. **issue_fee_due**: For PTO-85B forms, issue fee due amount

10. **entity_status_evidence**: Brief quote proving the entity status

Return ONLY valid JSON. No markdown, no code fences, no explanation."""


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


# ── Primary extraction: pdfplumber + PyMuPDF ─────────────────────

def extract_with_pdfplumber(pdf_bytes: bytes) -> Optional[dict]:
    """Extract structured data from a payment PDF using pdfplumber + PyMuPDF.

    Returns dict with: doc_type, entity_status, fees, total_amount, entity_status_evidence
    Returns None if extraction fails or finds no meaningful data.
    """
    try:
        import fitz  # PyMuPDF
        import pdfplumber
    except ImportError as e:
        logger.error("Missing PDF library: %s", e)
        return None

    result = {
        "doc_type": "UNKNOWN",
        "entity_status": None,
        "fees": [],
        "total_amount": None,
        "entity_status_evidence": None,
        "extraction_method": "pdfplumber",
    }

    try:
        # Step 1: Extract full text with PyMuPDF (fast)
        pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in pdf_doc:
            full_text += page.get_text() + "\n"
        pdf_doc.close()

        if not full_text.strip():
            # Scanned PDF with no text layer — fall back to Gemini
            return None

        text_upper = full_text.upper()

        # Step 2: Detect document type
        result["doc_type"] = _detect_doc_type(text_upper)

        # Step 3: Extract entity status
        entity_status, evidence = _extract_entity_status(full_text, text_upper)
        result["entity_status"] = entity_status
        result["entity_status_evidence"] = evidence

        # Step 4: Extract fee table using pdfplumber
        fees, total = _extract_fees_pdfplumber(pdf_bytes, text_upper)
        result["fees"] = fees
        result["total_amount"] = total

        # Validate: must have at least entity_status or fees to be useful
        if not entity_status and not fees:
            return None

        return result

    except Exception as e:
        logger.warning("pdfplumber extraction failed: %s", e)
        return None


def _detect_doc_type(text_upper: str) -> str:
    """Detect the type of USPTO payment document from its text content."""
    if "SB/06" in text_upper or "SB06" in text_upper or "PTO-875" in text_upper:
        return "FEE_WORKSHEET_SB06"
    if "PTO-85B" in text_upper or "PTOL-85B" in text_upper or "ISSUE FEE TRANSMITTAL" in text_upper:
        return "ISSUE_FEE_PTO85B"
    if "ELECTRONIC PATENT APPLICATION FEE TRANSMITTAL" in text_upper:
        return "ELECTRONIC_FEE_TRANSMITTAL"
    if "ELECTRONIC PAYMENT" in text_upper and "RECEIPT" in text_upper:
        return "ELECTRONIC_PAYMENT_RECEIPT"
    if "FEE TRANSMITTAL" in text_upper:
        return "ELECTRONIC_FEE_TRANSMITTAL"
    if "PAYMENT RECEIPT" in text_upper:
        return "ELECTRONIC_PAYMENT_RECEIPT"
    return "UNKNOWN"


def _extract_entity_status(text: str, text_upper: str) -> tuple:
    """Extract entity status (SMALL/LARGE/MICRO) from PDF text.

    Returns (status, evidence) tuple.
    """
    # Check for explicit status declarations
    patterns = [
        (r"(?:FILED|CLAIMED|CERTIFIED)\s+AS\s+(SMALL|MICRO|LARGE)\s+ENTITY", "declaration"),
        (r"ENTITY\s+STATUS\s*[:=]\s*(SMALL|LARGE|MICRO)", "status_field"),
        (r"(SMALL|MICRO)\s+ENTITY\s+(?:FEE|RATE|STATUS)", "entity_mention"),
        (r"APPLICANT\s+CLAIMS\s+(SMALL|MICRO)\s+ENTITY", "applicant_claim"),
        (r"\[\s*X?\s*\]\s*(SMALL|MICRO|LARGE)\s+ENTITY", "checkbox"),  # [X] SMALL ENTITY
        (r"ENTITY:\s*(SMALL|LARGE|MICRO)", "entity_label"),
    ]

    for pattern, source in patterns:
        m = re.search(pattern, text_upper)
        if m:
            status = m.group(1).upper()
            # Get context around the match for evidence
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 20)
            evidence = text[start:end].strip()
            return status, evidence

    # Check if "SMALL ENTITY" appears in fee column headers
    if "SMALL ENTITY" in text_upper and "LARGE ENTITY" not in text_upper:
        return "SMALL", "Column header: SMALL ENTITY fees shown"

    # Check for entity in fee rate context
    if "MICRO ENTITY" in text_upper:
        return "MICRO", "Text mentions MICRO ENTITY"
    if "SMALL ENTITY" in text_upper:
        return "SMALL", "Text mentions SMALL ENTITY"

    return None, None


def _extract_fees_pdfplumber(pdf_bytes: bytes, text_upper: str) -> tuple:
    """Extract fee line items and total from PDF tables using pdfplumber.

    Returns (fees_list, total_amount) tuple.
    """
    import pdfplumber

    fees = []
    total_amount = None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                # Try table extraction first
                tables = page.extract_tables()
                for table in tables:
                    if not table:
                        continue
                    for row in table:
                        if not row:
                            continue
                        fee = _parse_fee_row(row)
                        if fee:
                            fees.append(fee)

                # If no tables found, try text-based extraction
                if not fees:
                    page_text = page.extract_text() or ""
                    text_fees = _extract_fees_from_text(page_text)
                    fees.extend(text_fees)

    except Exception as e:
        logger.warning("pdfplumber table extraction error: %s", e)

    # Calculate total from fees if not explicitly found
    if fees:
        fee_total = sum(f.get("amount", 0) or 0 for f in fees)
        total_amount = fee_total

    # Try to find explicit total in text
    total_match = re.search(
        r"(?:TOTAL|AMOUNT\s+(?:DUE|PAID))\s*[:$=]?\s*\$?\s*([\d,]+\.?\d*)",
        text_upper
    )
    if total_match:
        try:
            explicit_total = float(total_match.group(1).replace(",", ""))
            if explicit_total > 0:
                total_amount = explicit_total
        except ValueError:
            pass

    return fees, total_amount


def _parse_fee_row(row: list) -> Optional[dict]:
    """Parse a table row into a fee dict if it looks like a fee line item."""
    if not row or len(row) < 2:
        return None

    # Clean cells
    cells = [str(c).strip() if c else "" for c in row]

    # Look for dollar amounts in any cell
    amount = None
    amount_idx = -1
    for i, cell in enumerate(cells):
        m = re.search(r'\$?\s*([\d,]+\.?\d{2})', cell)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if val > 0:
                    amount = val
                    amount_idx = i
                    break
            except ValueError:
                continue

    if amount is None:
        return None

    # Look for fee code (numeric, 4 digits)
    fee_code = None
    for cell in cells:
        m = re.match(r'^(\d{4})$', cell.strip())
        if m:
            fee_code = m.group(1)
            break

    # Description is the longest text cell that isn't the amount
    description = ""
    for i, cell in enumerate(cells):
        if i != amount_idx and len(cell) > len(description) and not re.match(r'^[\d.$,\s]+$', cell):
            description = cell

    if not description:
        return None

    # Skip header rows and non-fee rows
    skip_patterns = ["FEE CODE", "DESCRIPTION", "AMOUNT", "TOTAL", "---", "==="]
    if any(p in description.upper() for p in skip_patterns):
        return None

    return {
        "fee_code": fee_code,
        "description": description,
        "amount": amount,
        "quantity": 1,
    }


def _extract_fees_from_text(text: str) -> List[dict]:
    """Extract fee line items from unstructured text using regex patterns."""
    fees = []

    # Pattern: description followed by dollar amount
    # e.g., "UTILITY FILING FEE    $320.00"
    # e.g., "2501 - UTILITY APPL ISSUE FEE    $500.00"
    pattern = r'(?:(\d{4})\s*[-–]\s*)?([A-Z][A-Z\s/()]+?)\s+\$?\s*([\d,]+\.\d{2})'
    for m in re.finditer(pattern, text.upper()):
        fee_code = m.group(1)
        description = m.group(2).strip()
        try:
            amount = float(m.group(3).replace(",", ""))
        except ValueError:
            continue

        if amount <= 0:
            continue

        # Skip non-fee lines
        if any(kw in description for kw in ["TOTAL", "BALANCE", "PAGE"]):
            continue

        fees.append({
            "fee_code": fee_code,
            "description": description,
            "amount": amount,
            "quantity": 1,
        })

    return fees


# ── Fallback extraction: Gemini Vision ───────────────────────────

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
    """Extract structured data from a payment PDF using Gemini Vision.

    Used as fallback when pdfplumber fails. Returns dict or None.
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
    result["issue_fee_due"] = _to_float(result.get("issue_fee_due"))
    for fee in result.get("fees", []):
        if isinstance(fee, dict):
            fee["amount"] = _to_float(fee.get("amount"))
            fee["item_total"] = _to_float(fee.get("item_total"))
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
